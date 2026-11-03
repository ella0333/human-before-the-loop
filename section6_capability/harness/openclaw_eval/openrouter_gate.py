from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from aiohttp import ClientSession, ClientTimeout, web

CONTROL = Path("/run/pokemon-eval/gate-control.json")
UPSTREAM = "https://openrouter.ai/api/v1"
HOP_HEADERS = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "content-length",
}
LOCK = asyncio.Lock()


def now() -> float:
    return time.time()


def control() -> dict[str, Any]:
    try:
        value = json.loads(CONTROL.read_text(encoding="utf-8"))
    except Exception as exc:
        raise web.HTTPServiceUnavailable(text=f"evaluation gate inactive: {type(exc).__name__}")
    if not value.get("active"):
        raise web.HTTPServiceUnavailable(text="evaluation gate inactive")
    return value


def append(path: str, row: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"ts": now(), **row}, ensure_ascii=False) + "\n")


def rows(path: str) -> list[dict[str, Any]]:
    target = Path(path)
    if not target.is_file():
        return []
    result = []
    for line in target.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            result.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return result


def successful_calls(cfg: dict[str, Any]) -> int:
    generation = cfg["generation"]
    return sum(row.get("kind") == "response" and row.get("generation") == generation
               and 200 <= int(row.get("status", 0)) < 300
               for row in rows(cfg["ledger"]))


def credit_error(status: int, body: bytes) -> bool:
    text = body.decode("utf-8", "replace").lower()
    return status == 402 or (
        status in {401, 403} and any(marker in text for marker in (
            "credit", "payment_required", "insufficient", "spending limit",
        ))
    )


def request_evidence(body: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(body)
    except Exception:
        payload = {}
    provider = payload.get("provider") if isinstance(payload, dict) else None
    messages = payload.get("messages") if isinstance(payload, dict) else None
    image_parts = 0
    if isinstance(messages, list):
        for message in messages:
            content = message.get("content") if isinstance(message, dict) else None
            if isinstance(content, list):
                image_parts += sum(isinstance(part, dict) and part.get("type") in {
                    "image", "image_url", "input_image",
                } for part in content)
    return {
        "model": payload.get("model") if isinstance(payload, dict) else None,
        "provider_policy": provider,
        "tools_count": len(payload.get("tools") or []) if isinstance(payload, dict) else 0,
        "image_parts": image_parts,
        "request_bytes": len(body),
        "request_sha256": hashlib.sha256(body).hexdigest(),
    }


def usage_from_stream(data: bytes) -> dict[str, Any] | None:
    usage = None
    for line in data.decode("utf-8", "replace").splitlines():
        if line.startswith("data:"):
            value = line[5:].strip()
            if not value or value == "[DONE]":
                continue
            try:
                payload = json.loads(value)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict) and isinstance(payload.get("usage"), dict):
                usage = payload["usage"]
    if usage is not None:
        return usage
    try:
        payload = json.loads(data)
    except Exception:
        return None
    return payload.get("usage") if isinstance(payload, dict) and isinstance(payload.get("usage"), dict) else None


def response_route(data: bytes) -> dict[str, Any]:
    payloads: list[dict[str, Any]] = []
    text = data.decode("utf-8", "replace")
    for line in text.splitlines():
        if not line.startswith("data:"):
            continue
        value = line[5:].strip()
        if not value or value == "[DONE]":
            continue
        try:
            payload = json.loads(value)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            payloads.append(payload)
    if not payloads:
        try:
            payload = json.loads(text)
        except Exception:
            payload = None
        if isinstance(payload, dict):
            payloads.append(payload)
    provider = next((item.get("provider") for item in payloads
                     if item.get("provider")), None)
    model = next((item.get("model") for item in payloads if item.get("model")), None)
    return {"actual_provider": provider, "response_model": model}


async def handler(request: web.Request) -> web.StreamResponse:
    cfg = control()
    tail = request.match_info.get("tail", "")
    body = await request.read()
    is_model_call = request.method == "POST" and tail.rstrip("/") in {
        "chat/completions", "responses",
    }
    if not is_model_call:
        return await forward_once(request, tail, body, cfg, count=False)

    async with LOCK:
        cfg = control()
        evidence = request_evidence(body)
        expected_model = cfg.get("expected_model")
        expected_policy = cfg.get("expected_provider_policy")
        if evidence["model"] != expected_model or evidence["provider_policy"] not in (None, expected_policy):
            append(cfg["ledger"], {
                "kind": "routing_block", "generation": cfg["generation"],
                "model": evidence["model"], "provider_policy": evidence["provider_policy"],
                "expected_model": expected_model, "expected_provider_policy": expected_policy,
                "request_bytes": evidence["request_bytes"],
                "request_sha256": evidence["request_sha256"],
            })
            return web.json_response({"error": {"code": "eval_routing_mismatch",
                "message": "evaluation model/provider route mismatch"}}, status=409)
        if evidence["provider_policy"] is None:
            try:
                payload = json.loads(body)
                payload["provider"] = expected_policy
                body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
            except Exception:
                return web.json_response({"error": {"code": "eval_routing_mismatch",
                    "message": "evaluation request was not valid JSON"}}, status=409)
            evidence = request_evidence(body)
            append(cfg["ledger"], {
                "kind": "provider_policy_added", "generation": cfg["generation"],
                "model": evidence["model"], "provider_policy": evidence["provider_policy"],
                "request_bytes": evidence["request_bytes"],
                "request_sha256": evidence["request_sha256"],
            })
        if Path(cfg["completion_latch"]).is_file():
            append(cfg["ledger"], {"kind": "completion_block", "generation": cfg["generation"]})
            raise web.HTTPConflict(text="evaluation objective already complete")
        count = successful_calls(cfg)
        if count >= int(cfg["call_cap"]):
            append(cfg["ledger"], {"kind": "budget_block", "generation": cfg["generation"],
                                    "successful_calls": count, "call_cap": cfg["call_cap"]})
            return web.json_response({"error": {"code": "eval_call_cap",
                "message": "evaluation model-call cap reached"}}, status=429)
        return await forward_with_credit_pause(request, tail, body, cfg, count + 1)


async def upstream_request(request: web.Request, tail: str, body: bytes):
    headers = {key: value for key, value in request.headers.items()
               if key.lower() not in HOP_HEADERS and key.lower() != "host"}
    headers["Accept-Encoding"] = "identity"
    timeout = ClientTimeout(total=None, connect=60, sock_read=1800)
    session = ClientSession(timeout=timeout)
    response = await session.request(request.method, f"{UPSTREAM}/{tail}",
                                     params=request.query, headers=headers, data=body)
    return session, response


async def forward_once(request: web.Request, tail: str, body: bytes,
                       cfg: dict[str, Any], count: bool) -> web.StreamResponse:
    session, upstream = await upstream_request(request, tail, body)
    data = await upstream.read()
    headers = {key: value for key, value in upstream.headers.items()
               if key.lower() not in HOP_HEADERS}
    status = upstream.status
    upstream.release()
    await session.close()
    if count:
        append(cfg["ledger"], {"kind": "response", "generation": cfg["generation"],
                               "status": status, **request_evidence(body),
                               "response_bytes": len(data),
                               "response_sha256": hashlib.sha256(data).hexdigest(),
                               "usage": usage_from_stream(data)})
    return web.Response(status=status, headers=headers, body=data)


async def forward_with_credit_pause(request: web.Request, tail: str, body: bytes,
                                    cfg: dict[str, Any], call_index: int) -> web.StreamResponse:
    evidence = request_evidence(body)
    paused_at = None
    attempt = 0
    while True:
        attempt += 1
        append(cfg["ledger"], {"kind": "upstream_attempt", "generation": cfg["generation"],
                               "call_index": call_index, "attempt": attempt, **evidence})
        session, upstream = await upstream_request(request, tail, body)
        if upstream.status >= 400:
            data = await upstream.read()
            status = upstream.status
            headers = {key: value for key, value in upstream.headers.items()
                       if key.lower() not in HOP_HEADERS}
            upstream.release()
            await session.close()
            if credit_error(status, data):
                if paused_at is None:
                    paused_at = now()
                    append(cfg["ledger"], {"kind": "credit_pause", "generation": cfg["generation"],
                                           "call_index": call_index, "status": status})
                    print(f"CREDIT PAUSE: container/{cfg['model_key']} OpenRouter credits exhausted; "
                          "Pokemon remains alive and the same request will retry", flush=True)
                await asyncio.sleep(int(cfg.get("credit_poll_seconds", 60)))
                cfg = control()
                continue
            append(cfg["ledger"], {"kind": "response", "generation": cfg["generation"],
                                   "call_index": call_index, "attempt": attempt,
                                   "status": status, **evidence,
                                   **response_route(data),
                                   "response_bytes": len(data),
                                   "response_sha256": hashlib.sha256(data).hexdigest(),
                                   "usage": usage_from_stream(data)})
            return web.Response(status=status, headers=headers, body=data)

        if paused_at is not None:
            duration = now() - paused_at
            append(cfg["ledger"], {"kind": "credit_resume", "generation": cfg["generation"],
                                   "call_index": call_index, "paused_seconds": duration})
            print(f"CREDIT RESUME: container/{cfg['model_key']} after {duration:.0f}s", flush=True)

        headers = {key: value for key, value in upstream.headers.items()
                   if key.lower() not in HOP_HEADERS}
        downstream = web.StreamResponse(status=upstream.status, headers=headers)
        await downstream.prepare(request)
        captured = bytearray()
        downstream_error = None
        try:
            async for chunk in upstream.content.iter_chunked(65536):
                captured.extend(chunk)
                await downstream.write(chunk)
            await downstream.write_eof()
        except (ConnectionError, asyncio.CancelledError) as exc:
            downstream_error = type(exc).__name__
        finally:
            status = upstream.status
            upstream.release()
            await session.close()
            append(cfg["ledger"], {"kind": "response", "generation": cfg["generation"],
                                   "call_index": call_index, "attempt": attempt,
                                   "status": status, **evidence,
                                   **response_route(captured),
                                   "response_bytes": len(captured),
                                   "response_sha256": hashlib.sha256(captured).hexdigest(),
                                   "usage": usage_from_stream(captured),
                                   "downstream_error": downstream_error})
        return downstream


async def health(_request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "control": CONTROL.is_file()})


def create_app() -> web.Application:
    app = web.Application(client_max_size=64 * 1024 * 1024)
    app.router.add_get("/health", health)
    app.router.add_route("*", "/api/v1/{tail:.*}", handler)
    return app


def main() -> None:
    app = create_app()
    web.run_app(app, host="127.0.0.1", port=int(os.environ.get("POKEMON_GATE_PORT", "18999")),
                print=None)


if __name__ == "__main__":
    main()
