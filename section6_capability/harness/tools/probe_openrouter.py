from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from constrained.provider import OpenRouterProvider
from evalspec import MODEL_PROVIDER, MODELS


async def probe(model_key: str) -> int:
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        print("OPENROUTER_API_KEY is missing")
        return 2
    records: list[dict] = []
    provider = OpenRouterProvider(key, MODELS[model_key], 1, records.append)
    provider.set_system_prompt(
        "Return exactly the structured response requested by the harness."
    )
    schema = {
        "type": "object",
        "properties": {
            "thinking": {"type": "string"},
            "choice": {"type": "string", "enum": ["A", "B"]},
        },
        "required": ["thinking", "choice"],
        "additionalProperties": False,
    }
    result = await provider.generate(
        "Menu:\nA. Continue\nB. Wait\n\nChoose A.", schema, "probe", 1
    )
    record = records[0]
    actual_provider = (record.get("provider_response") or {}).get("provider")
    expected_provider = {"deepinfra": "DeepInfra", "meta": "Meta"}[
        MODEL_PROVIDER[model_key]
    ]
    ok = (
        not record.get("error")
        and result.get("choice") == "A"
        and isinstance(result.get("thinking"), str)
        and actual_provider == expected_provider
    )
    print(json.dumps({
        "ok": ok,
        "model": MODELS[model_key],
        "expected_provider": expected_provider,
        "actual_provider": actual_provider,
        "http_status": record.get("http_status"),
        "error": record.get("error"),
        "request_controls": record.get("request_controls"),
        "native_reasoning_returned": record.get("native_reasoning_returned"),
        "tokens": record.get("tokens"),
    }, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", choices=sorted(MODELS), required=True)
    raise SystemExit(asyncio.run(probe(parser.parse_args().model)))
