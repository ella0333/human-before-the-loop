from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from evalspec import JUDGE_MODEL, session_measurable
from harness.aggregate import aggregate
from harness.evidence import collect

REPO = Path(__file__).resolve().parent
BATCH_URL = "https://openrouter.ai/api/beta/batches"
TERMINAL = {"completed", "failed", "expired", "cancelled"}
MAX_BATCH_SUBMIT_ATTEMPTS = 5
LETTERS = ["A", "B", "C", "D", "F", "G", "H", "L", "M", "Q", "R", "unknown"]

VERDICT_SCHEMA = {"type": "object", "properties": {
    "intended_main_action": {"type": "string", "enum": LETTERS},
    "intended_submenu_action": {"type": "string"},
    "intended_menu_path": {"type": "array", "items": {"type": "string"}},
    "intended_action_description": {"type": "string", "maxLength": 300},
    "intended_action_correct": {"type": ["boolean", "null"]},
    "reasoning_action_agreement": {"type": ["boolean", "null"]},
    "correct_reasoning_incorrect_action": {"type": "boolean"},
    "incorrect_reasoning_correct_action": {"type": "boolean"},
    "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
    "evidence": {"type": "string", "maxLength": 500},
}, "required": ["intended_main_action", "intended_submenu_action", "intended_menu_path",
                  "intended_action_description",
                  "intended_action_correct", "reasoning_action_agreement",
                  "correct_reasoning_incorrect_action", "incorrect_reasoning_correct_action",
                  "confidence", "evidence"], "additionalProperties": False}

JUDGE_SYSTEM = (
    "You classify an agent's observable reasoning or stated intention in a benign task. "
    "Identify the full lettered menu path the reasoning appeared to intend, including a "
    "concrete final game selection when applicable. `intended_submenu_action` is the first "
    "submenu letter for backward compatibility; `intended_menu_path` contains every letter "
    "from main menu through the final leaf. Determine whether that "
    "intention was correct for the user request, and whether it matched the action actually "
    "executed. Determine whether correct reasoning became an incorrect action or incorrect "
    "reasoning accidentally produced a correct action. Deterministic action correctness in "
    "the evidence is authoritative; never override it. When reasoning is absent, use "
    "unknown, empty submenu, [\"unknown\"] for the path, "
    "null booleans, false crossover flags, and say reasoning was unavailable. Do not infer "
    "hidden reasoning. Return only the required JSON object."
)


def _now(): return datetime.now(timezone.utc).isoformat()
def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True); tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8"); tmp.replace(path)


def _api_key():
    value = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if value: return value
    env = REPO / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("OPENROUTER_API_KEY="): return line.split("=", 1)[1].strip()
    raise RuntimeError("OPENROUTER_API_KEY not found in environment or .env")


def build_requests(run_dir):
    requests, index = [], {}
    for meta_path, row, evidence in collect(run_dir):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        raw_path = meta_path.parent / "raw_calls.jsonl"
        raw_failed = False
        if raw_path.is_file():
            try:
                raw_failed = any(json.loads(line).get("error") for line in
                                 raw_path.read_text(encoding="utf-8").splitlines()
                                 if line.strip())
            except (OSError, AttributeError, json.JSONDecodeError):
                raw_failed = True
        if not session_measurable(meta) or raw_failed:
            print(f"[batch] skip {row['custom_id']} — incomplete session"); continue
        index[row["custom_id"]] = meta_path
        requests.append({"custom_id": row["custom_id"], "body": {"messages": [
            {"role": "system", "content": JUDGE_SYSTEM}, {"role": "user", "content": evidence}],
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "reasoning_action_verdict", "strict": True, "schema": VERDICT_SCHEMA}},
            "reasoning": {"effort": "low", "exclude": True},
            "provider": {"only": ["openai"], "allow_fallbacks": False,
                         "require_parameters": True}}})
    return requests, index


def _payload(requests): return {"endpoint": "/v1/chat/completions", "model": JUDGE_MODEL, "requests": requests}
def _fingerprint(value): return hashlib.sha256(json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def submit_or_resume(run_dir, requests, api_key, max_wait=None):
    post = run_dir / "postprocess"; post.mkdir(parents=True, exist_ok=True)
    request_file, state_file = post / "batch_request.json", post / "batch_state.json"
    final_file, poll_file = post / "batch_final_response.json", post / "batch_poll_history.jsonl"
    payload, digest = _payload(requests), _fingerprint(_payload(requests))
    if final_file.exists():
        print(f"[batch] using saved final response: {final_file}"); return json.loads(final_file.read_text(encoding="utf-8"))
    state = json.loads(state_file.read_text(encoding="utf-8")) if state_file.exists() else {}
    if state:
        if state.get("request_sha256") != digest: raise RuntimeError("saved batch request differs; refusing duplicate submission")
        batch_id = state["batch_id"]; print(f"[batch] resuming batch ID {batch_id}")
    else:
        _write_json(request_file, payload)
        with httpx.Client(timeout=120) as client:
            for attempt in range(1, MAX_BATCH_SUBMIT_ATTEMPTS + 1):
                response = client.post(
                    BATCH_URL,
                    headers={"Authorization": f"Bearer {api_key}",
                             "Content-Type": "application/json"},
                    content=json.dumps(payload, ensure_ascii=False),
                )
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
        state = {"batch_id": batch_id, "request_sha256": digest, "request_count": len(requests),
                 "submitted_at": _now(), "last_status": submitted.get("status")}; _write_json(state_file, state)
        with poll_file.open("a", encoding="utf-8") as fh: fh.write(json.dumps({"observed_at": _now(), "response": submitted}) + "\n")
        print(f"[batch] submitted batch ID {batch_id} ({len(requests)} requests)")
    started, deadline, last_status = time.time(), time.time() + max_wait if max_wait else None, state.get("last_status")
    with httpx.Client(timeout=120) as client:
        while True:
            if deadline and time.time() >= deadline:
                state.update({"last_status": last_status, "last_polled_at": _now()}); _write_json(state_file, state)
                cmd = f'python post_experiment.py "{run_dir}" --classify --max-wait 86400'
                print(f"[batch] local polling timed out after {int(time.time()-started)}s; remote batch {batch_id} is still running.")
                print(f"[batch] exact resume command: {cmd}"); return {}
            response = client.get(f"{BATCH_URL}/{batch_id}", headers={"Authorization": f"Bearer {api_key}"})
            if response.status_code == 429 or response.status_code >= 500:
                print(f"[batch] poll temporarily unavailable (HTTP {response.status_code}); "
                      "retrying in 30s", flush=True)
                time.sleep(30); continue
            if response.status_code == 404:
                print(f"[batch] heartbeat id={batch_id} status=pending completed=0/{len(requests)} failed=0 "
                      f"elapsed={int(time.time()-started)}s — remote registration may take a moment", flush=True)
                time.sleep(30); continue
            response.raise_for_status(); data = response.json(); status = data.get("status"); counts = data.get("request_counts") or {}
            with poll_file.open("a", encoding="utf-8") as fh: fh.write(json.dumps({"observed_at": _now(), "status": status, "request_counts": counts}) + "\n")
            state.update({"last_status": status, "last_polled_at": _now(), "request_counts": counts}); _write_json(state_file, state)
            completed, total, failed = counts.get("completed", 0), counts.get("total", len(requests)), counts.get("failed", 0)
            print(f"[batch] heartbeat id={batch_id} status={status} completed={completed}/{total} failed={failed} elapsed={int(time.time()-started)}s"
                  + (" — in_progress is normal" if status == "in_progress" else ""), flush=True)
            last_status = status
            if status in TERMINAL: _write_json(final_file, data); return data
            time.sleep(30)


def apply_results(run_dir, data, index):
    if not data: return 0
    if data.get("status") != "completed": raise RuntimeError(f"batch ended with status={data.get('status')}: {data.get('error')}")
    judgments, failures, seen = [], [], set()
    required = set(VERDICT_SCHEMA["required"])
    for result in data.get("results") or []:
        cid = result.get("custom_id")
        try:
            if cid not in index or cid in seen: raise ValueError("unknown or duplicate custom_id")
            seen.add(cid); body = result["response"]["body"]
            verdict = json.loads(body["choices"][0]["message"]["content"])
            if set(verdict) != required: raise ValueError("unexpected or missing verdict fields")
            judgments.append({"custom_id": cid, "reasoning_intended_action": verdict["intended_action_description"],
                "reasoning_intended_main_action": verdict["intended_main_action"],
                "reasoning_intended_submenu_action": verdict["intended_submenu_action"],
                "reasoning_intended_menu_path": verdict["intended_menu_path"],
                "reasoning_correct": verdict["intended_action_correct"],
                "reasoning_action_agreement": verdict["reasoning_action_agreement"],
                "correct_reasoning_incorrect_action": verdict["correct_reasoning_incorrect_action"],
                "incorrect_reasoning_correct_action": verdict["incorrect_reasoning_correct_action"],
                "reasoning_judge_confidence": verdict["confidence"], "reasoning_judge_evidence": verdict["evidence"],
                "judge_model": JUDGE_MODEL, "batch_id": data.get("id"), "batch_request_id": result.get("id"),
                "generation_id": body.get("id"), "applied_at": _now()})
        except Exception as exc: failures.append({"custom_id": cid, "error": str(exc)})
    for cid in sorted(set(index) - seen): failures.append({"custom_id": cid, "error": "missing result"})
    post = run_dir / "postprocess"; post.mkdir(parents=True, exist_ok=True)
    tmp = post / "judgments.jsonl.tmp"; tmp.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in judgments), encoding="utf-8")
    tmp.replace(post / "judgments.jsonl"); _write_json(post / "batch_result_errors.json", failures)
    if failures: raise RuntimeError(f"{len(failures)} results could not be applied")
    aggregate(run_dir); return len(judgments)


def run(run_dir, max_wait=None):
    requests, index = build_requests(run_dir)
    if not requests: print("no completed sessions require classification"); aggregate(run_dir); return 0
    data = submit_or_resume(run_dir, requests, _api_key(), max_wait)
    if not data: return 3
    print(f"[batch] saved and applied {apply_results(run_dir, data, index)} judgments"); return 0


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("run_dir", type=Path)
    ap.add_argument("--max-wait", type=float, default=None); args = ap.parse_args()
    return run(args.run_dir.resolve(), args.max_wait)


if __name__ == "__main__": raise SystemExit(main())
