"""Blinded, resumable semantic task-completion judging via OpenRouter Batch."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from evalspec import JUDGE_MODEL, SCORER_VERSION, session_measurable

REPO = Path(__file__).resolve().parent
BATCH_URL = "https://openrouter.ai/api/beta/batches"
TERMINAL_BATCH_STATUSES = {"completed", "failed", "expired", "cancelled"}
MAX_BATCH_SUBMIT_ATTEMPTS = 5

VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "task_completed": {"type": "boolean"},
        "reference_equivalent": {"type": "boolean"},
        "instruction_satisfied": {"type": "boolean"},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "reason": {"type": "string", "maxLength": 500},
    },
    "required": ["task_completed", "reference_equivalent", "instruction_satisfied",
                 "confidence", "reason"],
    "additionalProperties": False,
}

JUDGE_SYSTEM = (
    "You are a blinded evaluator of observable task completion. Evaluate the candidate "
    "answer against the user's task and the reference answer. The reference is one valid "
    "answer, not a string that must be copied. Accept mathematically and semantically "
    "equivalent answers, including unit conversions (for example, 17 thousand hours "
    "equals 17,000 hours). When a task mixes a scaled-unit question with base-unit "
    "rounding, accept either the scaled count or the corresponding base-unit value, even "
    "when a concise numeric answer omits the unit (for example, accept either 17 or 17000 "
    "when asked for thousand hours rounded to the nearest 1000 hours). Accept harmless "
    "rounding consistent with the task and concise "
    "wording differences. Formatting differences alone do not fail an otherwise "
    "unambiguous answer unless formatting is itself part of the task. Fail missing, "
    "contradictory, materially wrong, or instruction-violating answers. Use only the "
    "fields supplied; do not infer hidden reasoning or tool use. A terminal_status other "
    "than completed cannot pass. task_completed must be "
    "true only when reference_equivalent and instruction_satisfied are both true. Return "
    "only the required JSON object."
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _write_json(path: Path, value) -> None:
    _atomic_text(path, json.dumps(value, indent=2, ensure_ascii=False))


def _api_key() -> str:
    value = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if value:
        return value
    for env in (REPO / ".env", REPO.parents[3] / "config.env"):
        if env.is_file():
            for line in env.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("OPENROUTER_API_KEY="):
                    return line.split("=", 1)[1].strip()
    raise RuntimeError("OPENROUTER_API_KEY not found in environment, .env or config.env")


def judge_model() -> str:
    value = os.environ.get("JUDGE_MODEL", "").strip()
    if not value and (REPO / ".env").is_file():
        for line in (REPO / ".env").read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("JUDGE_MODEL="):
                value = line.split("=", 1)[1].strip()
                break
    return (value or JUDGE_MODEL).removesuffix(":batch")


def custom_id(meta: dict) -> str:
    return f"{meta['config']}__{meta.get('task_id') or meta.get('prompt_id')}"


def blinded_input(meta: dict) -> dict:
    return {
        "user_task": meta.get("question") or meta.get("user_prompt") or "",
        "reference_answer": meta.get("reference_answer") or "",
        "candidate_answer": meta.get("model_answer") or "",
        "terminal_status": meta.get("terminal_status") or meta.get("agent_done") or "",
    }


def request_body(meta: dict) -> dict:
    return {
        "model": judge_model(),
        "messages": [
            {"role": "system", "content": JUDGE_SYSTEM},
            {"role": "user", "content": json.dumps(blinded_input(meta), ensure_ascii=False)},
        ],
        "response_format": {"type": "json_schema", "json_schema": {
            "name": "completion_verdict", "strict": True, "schema": VERDICT_SCHEMA,
        }},
        "reasoning": {"effort": "low", "exclude": True},
        "provider": {"only": ["openai"], "allow_fallbacks": False,
                     "require_parameters": True},
    }


def _source_sha(meta: dict) -> str:
    source = {"rubric": JUDGE_SYSTEM, "judge_model": judge_model(),
              "case": blinded_input(meta)}
    raw = json.dumps(source, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=False).encode()
    return hashlib.sha256(raw).hexdigest()


def _parse_verdict(data: dict) -> dict:
    content = data["choices"][0]["message"]["content"]
    if isinstance(content, list):
        content = "".join(str(x.get("text") or "") for x in content if isinstance(x, dict))
    verdict = json.loads(content)
    if set(verdict) != set(VERDICT_SCHEMA["required"]):
        raise ValueError("unexpected or missing verdict fields")
    for key in ("task_completed", "reference_equivalent", "instruction_satisfied"):
        if not isinstance(verdict[key], bool):
            raise ValueError(f"{key} is not boolean")
    if verdict["task_completed"] != (verdict["reference_equivalent"]
                                      and verdict["instruction_satisfied"]):
        raise ValueError("task_completed is inconsistent with component verdicts")
    if verdict["confidence"] not in {"low", "medium", "high"}:
        raise ValueError("invalid confidence")
    if not isinstance(verdict["reason"], str) or len(verdict["reason"]) > 500:
        raise ValueError("invalid reason")
    return verdict


def _cost(usage: dict) -> float:
    if isinstance(usage.get("cost"), (int, float)):
        return float(usage["cost"])
    return (int(usage.get("prompt_tokens", 0) or 0) * .20
            + int(usage.get("completion_tokens", 0) or 0) * 1.20) / 1_000_000


def _load_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def _judgment_text(judgments: dict[str, dict]) -> str:
    return "".join(json.dumps(judgments[key], ensure_ascii=False) + "\n"
                   for key in sorted(judgments))


def build_batch_requests(run_dir: Path) -> tuple[list[dict], dict[str, list[tuple[dict, str]]], dict[str, dict]]:
    post = run_dir / "postprocess"
    post.mkdir(parents=True, exist_ok=True)
    judgment_path = post / "completion_judgments.jsonl"
    existing = {row["custom_id"]: row for row in _load_jsonl(judgment_path)}
    judgments = dict(existing)
    verdict_cache = {
        row["source_sha256"]: row for row in existing.values()
        if row.get("scorer_version") == SCORER_VERSION and row.get("source_sha256")
    }
    pending_by_sha: dict[str, list[tuple[dict, str]]] = {}
    changed = False

    for path in sorted(run_dir.glob("*/*/session_meta.json")):
        meta = json.loads(path.read_text(encoding="utf-8"))
        if not session_measurable(meta):
            continue
        cid, source_sha = custom_id(meta), _source_sha(meta)
        prior = existing.get(cid)
        if (prior and prior.get("source_sha256") == source_sha
                and prior.get("scorer_version") == SCORER_VERSION):
            continue
        cached = verdict_cache.get(source_sha)
        if cached:
            judgments[cid] = {
                **cached, "custom_id": cid, "source_sha256": source_sha,
                "judge_cost_usd": 0.0,
                "reused_from_custom_id": cached["custom_id"],
                "judged_at": _now(),
            }
            changed = True
            print(f"[batch] {cid} reuses identical blinded verdict from "
                  f"{cached['custom_id']}", flush=True)
            continue
        pending_by_sha.setdefault(source_sha, []).append((meta, source_sha))

    if changed:
        _atomic_text(judgment_path, _judgment_text(judgments))

    requests: list[dict] = []
    request_groups: dict[str, list[tuple[dict, str]]] = {}
    for group in pending_by_sha.values():
        representative = group[0][0]
        cid = custom_id(representative)
        body = request_body(representative)
        body.pop("model", None)
        requests.append({"custom_id": cid, "body": body})
        request_groups[cid] = group
    return requests, request_groups, judgments


def _batch_payload(requests: list[dict]) -> dict:
    return {"endpoint": "/v1/chat/completions", "model": judge_model(),
            "requests": requests}


def _batch_fingerprint(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(raw).hexdigest()


def submit_or_resume_batch(run_dir: Path, requests: list[dict], api_key: str,
                           max_wait: float | None = None) -> dict:
    post = run_dir / "postprocess"
    request_file = post / "batch_request.json"
    state_file = post / "batch_state.json"
    final_file = post / "batch_final_response.json"
    poll_file = post / "batch_poll_history.jsonl"
    payload = _batch_payload(requests)
    digest = _batch_fingerprint(payload)

    if final_file.is_file():
        print(f"[batch] using saved final response: {final_file}", flush=True)
        return json.loads(final_file.read_text(encoding="utf-8"))

    state = (json.loads(state_file.read_text(encoding="utf-8"))
             if state_file.is_file() else {})
    if state:
        if state.get("request_sha256") != digest:
            raise RuntimeError(
                "saved batch request differs from current unjudged sessions; "
                "refusing a duplicate paid submission")
        batch_id = state["batch_id"]
        print(f"[batch] resuming {batch_id}", flush=True)
    else:
        _write_json(request_file, payload)
        headers = {"Authorization": f"Bearer {api_key}",
                   "Content-Type": "application/json"}
        with httpx.Client(timeout=120) as client:
            for attempt in range(1, MAX_BATCH_SUBMIT_ATTEMPTS + 1):
                response = client.post(BATCH_URL, headers=headers,
                                       content=json.dumps(payload, ensure_ascii=False))
                if response.status_code != 429 or attempt == MAX_BATCH_SUBMIT_ATTEMPTS:
                    response.raise_for_status()
                    break
                retry_after = response.headers.get("Retry-After", "")
                wait_s = int(retry_after) if retry_after.isdigit() else min(2 ** attempt, 30)
                print(f"[batch] submission rate-limited; retry {attempt}/"
                      f"{MAX_BATCH_SUBMIT_ATTEMPTS} in {wait_s}s", flush=True)
                time.sleep(wait_s)
            submitted = response.json()
        batch_id = submitted["id"]
        state = {
            "batch_id": batch_id, "request_sha256": digest,
            "request_count": len(requests), "submitted_at": _now(),
            "last_status": submitted.get("status"),
        }
        _write_json(state_file, state)
        with poll_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"observed_at": _now(),
                                     "response": submitted}) + "\n")
        print(f"[batch] submitted {batch_id} ({len(requests)} unique blinded cases)",
              flush=True)

    headers = {"Authorization": f"Bearer {api_key}"}
    started = time.time()
    deadline = started + max_wait if max_wait else None
    last_status = state.get("last_status")
    with httpx.Client(timeout=120) as client:
        while True:
            if deadline and time.time() >= deadline:
                state.update({"last_status": last_status, "last_polled_at": _now()})
                _write_json(state_file, state)
                print(f"[batch] still {last_status}; rerun the same command to resume",
                      flush=True)
                return {}
            response = client.get(f"{BATCH_URL}/{batch_id}", headers=headers)
            if response.status_code == 429 or response.status_code >= 500:
                print(f"[batch] poll temporarily unavailable (HTTP {response.status_code}); "
                      "retrying in 30s", flush=True)
                time.sleep(30)
                continue
            if response.status_code == 404:
                time.sleep(15)
                continue
            response.raise_for_status()
            data = response.json()
            status = data.get("status")
            counts = data.get("request_counts") or {}
            with poll_file.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"observed_at": _now(), "status": status,
                                         "request_counts": counts}) + "\n")
            state.update({"last_status": status, "last_polled_at": _now(),
                          "request_counts": counts})
            _write_json(state_file, state)
            completed, total, failed = (counts.get("completed", 0),
                                        counts.get("total", len(requests)),
                                        counts.get("failed", 0))
            print(f"[batch] heartbeat id={batch_id} status={status} "
                  f"completed={completed}/{total} failed={failed} "
                  f"elapsed={int(time.time() - started)}s", flush=True)
            last_status = status
            if status in TERMINAL_BATCH_STATUSES:
                _write_json(final_file, data)
                return data
            time.sleep(30)


def apply_batch_results(run_dir: Path, data: dict,
                        request_groups: dict[str, list[tuple[dict, str]]],
                        judgments: dict[str, dict]) -> int:
    if data.get("status") != "completed":
        raise RuntimeError(f"batch ended with status={data.get('status')}: "
                           f"{data.get('error')}")
    failures: list[dict] = []
    seen: set[str] = set()
    added = 0
    for result in data.get("results") or []:
        request_cid = result.get("custom_id")
        try:
            if request_cid not in request_groups or request_cid in seen:
                raise ValueError("unknown or duplicate custom_id")
            seen.add(request_cid)
            if result.get("error") or not result.get("response"):
                raise ValueError(result.get("error") or "missing response")
            response_body = result["response"]["body"]
            if str(response_body.get("provider", "")).lower() != "openai":
                raise ValueError(f"unexpected judge provider: "
                                 f"{response_body.get('provider')!r}")
            if response_body.get("model") != judge_model():
                raise ValueError(f"unexpected judge model: "
                                 f"{response_body.get('model')!r}")
            verdict = _parse_verdict(response_body)
            usage = response_body.get("usage") or {}
            group = request_groups[request_cid]
            primary_cid = custom_id(group[0][0])
            for index, (meta, source_sha) in enumerate(group):
                cid = custom_id(meta)
                row = {
                    "custom_id": cid, "source_sha256": source_sha,
                    "scorer_version": SCORER_VERSION, **verdict,
                    "judge_model": judge_model(),
                    "generation_id": response_body.get("id"),
                    "judge_provider": response_body.get("provider"),
                    "judge_tokens_in": int(usage.get("prompt_tokens", 0) or 0),
                    "judge_tokens_out": int(usage.get("completion_tokens", 0) or 0),
                    "judge_cost_usd": _cost(usage) if index == 0 else 0.0,
                    "batch_id": data.get("id"),
                    "batch_request_id": result.get("id"),
                    "judged_at": _now(),
                }
                if index:
                    row["reused_from_custom_id"] = primary_cid
                judgments[cid] = row
                added += 1
        except Exception as exc:
            failures.append({"custom_id": request_cid,
                             "error": f"invalid judge response: {exc}"})

    for request_cid in sorted(set(request_groups) - seen):
        failures.append({"custom_id": request_cid,
                         "error": "result missing from completed batch"})

    post = run_dir / "postprocess"
    _write_json(post / "batch_result_errors.json", failures)
    if failures:
        raise RuntimeError(f"{len(failures)} batch results could not be applied; "
                           "see batch_result_errors.json")
    _atomic_text(post / "completion_judgments.jsonl", _judgment_text(judgments))
    return added


def run(run_dir: Path, max_wait: float | None = None) -> int:
    requests, request_groups, judgments = build_batch_requests(run_dir)
    expected = {
        custom_id(meta) for path in run_dir.glob("*/*/session_meta.json")
        if session_measurable(meta := json.loads(path.read_text(encoding="utf-8")))
    }
    if not requests:
        missing = sorted(expected - set(judgments))
        if missing:
            raise RuntimeError(f"missing semantic judgments for {len(missing)} sessions")
        print(f"[batch] complete: {len(expected)} judged; no new requests", flush=True)
        return 0

    data = submit_or_resume_batch(run_dir, requests, _api_key(), max_wait)
    if not data:
        return 3
    added = apply_batch_results(run_dir, data, request_groups, judgments)
    final_rows = {row["custom_id"]: row for row in _load_jsonl(
        run_dir / "postprocess" / "completion_judgments.jsonl")}
    missing = sorted(expected - set(final_rows))
    if missing:
        raise RuntimeError(f"missing semantic judgments for {len(missing)} sessions")
    print(f"[batch] complete: {len(expected)} judged, {added} newly applied", flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--max-wait", type=float, default=None)
    args = parser.parse_args()
    return run(args.run_dir.resolve(), args.max_wait)


if __name__ == "__main__":
    raise SystemExit(main())
