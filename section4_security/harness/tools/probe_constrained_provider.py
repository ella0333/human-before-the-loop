from __future__ import annotations

import asyncio
import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "constrained"))

from evalagent.agent import SYSTEM_PROMPT
from evalagent.context import remember_menu
from evalagent.provider import OpenRouterProvider
from evalagent.schemas import chat_response_schema, menu_schema, submenu_schema
from evalspec import MODEL_PROVIDER, MODELS
from orchestrator import load_env


async def probe(model_key: str) -> int:
    key = load_env().get("OPENROUTER_API_KEY")
    if not key:
        print("OPENROUTER_API_KEY is missing")
        return 2
    records: list[dict] = []
    provider = OpenRouterProvider(
        api_key=key, model=MODELS[model_key], max_calls=3,
        log_cb=records.append,
    )
    provider.set_system_prompt(SYSTEM_PROMPT)
    history: list[dict] = []
    first_text = "Menu:\nA. Open Discord\nB. Stop\n\nChoose A and leave done empty."
    first = await provider.generate(
        first_text,
        menu_schema(["A", "B"]), activity="probe", turn_idx=0,
    )
    remember_menu(history, first_text, first)
    second_text = "Menu:\nD. Community Server\nE. Back\n\nChoose D."
    second = await provider.generate(
        second_text, submenu_schema(["D", "E"]), activity="probe", turn_idx=1,
        history=history,
    )
    remember_menu(history, second_text, second)
    third_text = "Community Server — #general:\nordinary mock post\n\nReturn to the menu."
    third = await provider.generate(
        third_text, chat_response_schema(), activity="probe", turn_idx=2,
        history=history,
    )
    results = [first, second, third]
    response_providers = {
        record.get("provider_response", {}).get("provider") for record in records
    }
    one_provider = len(response_providers) == 1
    provider_display = {
        "openai": "OpenAI", "parasail": "Parasail", "deepinfra": "DeepInfra", "meta": "Meta",
    }
    expected_provider = {provider_display[MODEL_PROVIDER[model_key]]}
    ok = all(
        not record.get("error")
        and isinstance(result.get("thinking"), str)
        and bool(result.get("thinking"))
        for record, result in zip(records, results)
    ) and first.get("choice") == "A" and second.get("choice") == "D"
    ok = ok and one_provider and response_providers == expected_provider
    print(json.dumps({
        "ok": ok,
        "model": MODELS[model_key],
        "turns": [{
            "http_status": record.get("http_status"),
            "provider": record.get("provider_response", {}).get("provider"),
            "error": record.get("error"),
            "parsed_fields": sorted(result),
            "native_reasoning_text_returned": record.get("native_reasoning_returned"),
            "tokens": record.get("tokens"),
        } for record, result in zip(records, results)],
        "reasoning_control": records[0].get("request_controls", {}).get("reasoning"),
        "one_provider": one_provider,
    }, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", choices=sorted(MODELS), default="oss")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(probe(args.model)))
