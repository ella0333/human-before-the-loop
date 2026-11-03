from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

from aiohttp import ClientSession, web

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from openclaw_eval import openrouter_gate as gate


async def bound(app: web.Application) -> tuple[web.AppRunner, str]:
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    sockets = site._server.sockets
    port = sockets[0].getsockname()[1]
    return runner, f"http://127.0.0.1:{port}"


async def run() -> None:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        upstream_requests = []

        async def completion(request: web.Request) -> web.Response:
            payload = await request.json()
            upstream_requests.append(payload)
            if payload.get("test_status") == 429:
                return web.json_response({"error": {"code": 429}}, status=429)
            if payload.get("test_status") == 500:
                return web.json_response({"error": {"code": 500}}, status=500)
            if len(upstream_requests) == 1:
                return web.json_response({"error": {"code": 402,
                    "message": "Insufficient credits"}}, status=402)
            return web.json_response({
                "id": f"test-{len(upstream_requests)}", "model": payload.get("model"),
                "provider": ("Phala" if "glimmer" in payload.get("model", "") else "Meta"),
                "choices": [{"message": {"role": "assistant", "content": "ok"},
                             "finish_reason": "stop", "index": 0}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2, "cost": 0.001},
            })

        upstream_app = web.Application()
        upstream_app.router.add_post("/api/v1/chat/completions", completion)
        upstream_runner, upstream_url = await bound(upstream_app)
        old_upstream, old_control = gate.UPSTREAM, gate.CONTROL
        gate.UPSTREAM = upstream_url + "/api/v1"
        gate.CONTROL = root / "control.json"
        ledger = root / "ledger.jsonl"
        latch = root / "complete.json"
        control = {
            "active": True, "generation": "pilot-a", "model_key": "spark",
            "call_cap": 5, "credit_poll_seconds": 0,
            "ledger": str(ledger), "completion_latch": str(latch),
            "expected_model": "meta/muse-spark-1.2",
            "expected_provider_policy": {
                "only": ["meta"], "allow_fallbacks": False},
        }
        gate.CONTROL.write_text(json.dumps(control), encoding="utf-8")
        gate_runner, gate_url = await bound(gate.create_app())
        payload = {
            "model": "meta/muse-spark-1.2",
            "provider": {"only": ["meta"], "allow_fallbacks": False},
            "tools": [{"type": "function"}],
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": "observe"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}},
            ]}],
        }
        try:
            async with ClientSession() as session:
                statuses = []
                for _ in range(6):
                    response = await session.post(gate_url + "/api/v1/chat/completions",
                                                  json=payload)
                    statuses.append(response.status)
                    await response.read()
                assert statuses == [200, 200, 200, 200, 200, 429], statuses
                assert len(upstream_requests) == 6, len(upstream_requests)
                rows = [json.loads(line) for line in ledger.read_text().splitlines()]
                success = [row for row in rows if row.get("kind") == "response"
                           and row.get("status") == 200]
                assert len(success) == 5, success
                assert sum(row.get("kind") == "budget_block" for row in rows) == 1
                assert sum(row.get("kind") == "credit_pause" for row in rows) == 1
                assert sum(row.get("kind") == "credit_resume" for row in rows) == 1
                assert success[0]["image_parts"] == 1
                assert success[0]["provider_policy"] == {
                    "only": ["meta"], "allow_fallbacks": False}
                assert success[0]["actual_provider"] == "Meta"
                assert all("messages" not in row and "authorization" not in row for row in rows)

                before_route_checks = len(upstream_requests)
                response = await session.post(gate_url + "/api/v1/chat/completions",
                    json={**payload, "model": "meta/muse-glimmer-30b"})
                assert response.status == 409, response.status
                await response.read()
                assert len(upstream_requests) == before_route_checks

                control["generation"] = "route-b"
                gate.CONTROL.write_text(json.dumps(control), encoding="utf-8")
                missing_provider = {key: value for key, value in payload.items()
                                    if key != "provider"}
                response = await session.post(gate_url + "/api/v1/chat/completions",
                                              json=missing_provider)
                assert response.status == 200, response.status
                await response.read()
                assert upstream_requests[-1]["provider"] == {
                    "only": ["meta"], "allow_fallbacks": False}

                control["generation"] = "failure-c"
                gate.CONTROL.write_text(json.dumps(control), encoding="utf-8")
                before_failures = len(upstream_requests)
                for test_status, expected in ((429, 429), (500, 500)):
                    failed_payload = {**payload, "test_status": test_status}
                    response = await session.post(gate_url + "/api/v1/chat/completions",
                                                  json=failed_payload)
                    assert response.status == expected, response.status
                    await response.read()
                assert len(upstream_requests) == before_failures + 2
                assert gate.successful_calls(control) == 0

                control["generation"] = "full-d"
                gate.CONTROL.write_text(json.dumps(control), encoding="utf-8")
                latch.write_text("{}", encoding="utf-8")
                response = await session.post(gate_url + "/api/v1/chat/completions",
                                              json=payload)
                assert response.status == 409, response.status
                await response.read()
                assert len(upstream_requests) == before_failures + 2

                latch.unlink()
                control.update({
                    "generation": "dynamic-e", "model_key": "glimmer",
                    "expected_model": "meta/muse-glimmer-30b",
                    "expected_provider_policy": {
                        "allow_fallbacks": True, "require_parameters": True},
                })
                gate.CONTROL.write_text(json.dumps(control), encoding="utf-8")
                dynamic_payload = {
                    **missing_provider, "model": "meta/muse-glimmer-30b"}
                response = await session.post(gate_url + "/api/v1/chat/completions",
                                              json=dynamic_payload)
                assert response.status == 200, response.status
                await response.read()
                assert upstream_requests[-1]["provider"] == {
                    "allow_fallbacks": True, "require_parameters": True}
                await asyncio.sleep(0.01)
                dynamic_rows = [row for row in (
                    json.loads(line) for line in ledger.read_text().splitlines())
                    if row.get("generation") == "dynamic-e"]
                dynamic_success = next(row for row in dynamic_rows
                                       if row.get("kind") == "response")
                assert dynamic_success["actual_provider"] == "Phala"
        finally:
            gate.UPSTREAM, gate.CONTROL = old_upstream, old_control
            await gate_runner.cleanup()
            await upstream_runner.cleanup()


def main() -> int:
    asyncio.run(run())
    print("openrouter gate integration test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
