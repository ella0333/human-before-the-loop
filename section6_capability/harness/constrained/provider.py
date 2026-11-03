from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any, Callable

import aiohttp

from evalspec import MODEL_PROVIDER, MODELS

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MAX_TRANSIENT_ATTEMPTS = 3
TRANSIENT_HTTP_STATUSES = {408, 409, 425, 429, 500, 502, 503, 504}

def _validate_structured(value: Any, schema: dict) -> None:
    if not isinstance(value, dict):
        raise ValueError("structured response is not an object")
    properties = schema.get("properties", {})
    missing = [field for field in schema.get("required", []) if field not in value]
    if missing:
        raise ValueError(f"structured response missing required fields: {missing}")
    if schema.get("additionalProperties") is False:
        extra = sorted(set(value) - set(properties))
        if extra:
            raise ValueError(f"structured response has unexpected fields: {extra}")
    python_types = {"string": str, "boolean": bool}
    for field, rule in properties.items():
        if field not in value:
            continue
        expected = python_types.get(rule.get("type"))
        if expected and not isinstance(value[field], expected):
            raise ValueError(f"structured field {field!r} has wrong type")
        if "enum" in rule and value[field] not in rule["enum"]:
            raise ValueError(f"structured field {field!r} is outside its enum")


def _parse_structured(raw_text: str, schema: dict) -> tuple[dict[str, Any], list[str]]:
    normalizations: list[str] = []
    try:
        value = json.loads(raw_text)
    except json.JSONDecodeError:
        stripped = raw_text.lstrip()
        value, end = json.JSONDecoder().raw_decode(stripped)
        trailing = stripped[end:].strip()
        if not trailing:
            raise
        normalizations.append("ignored trailing text after first JSON value")
    _validate_structured(value, schema)
    return value, normalizations


class BudgetExceeded(Exception):
    pass


class OpenRouterProvider:
    def __init__(
        self,
        api_key: str,
        model: str,
        max_calls: int,
        log_cb: Callable[[dict], None],
        request_timeout: int = 120,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.max_calls = max_calls
        self.log_cb = log_cb
        self.request_timeout = request_timeout
        self.system_prompt = ""
        self.calls_made = 0
        self.tokens_in = 0
        self.tokens_out = 0
        self.reasoning_tokens = 0
        self.session_id = f"pokemon-tutorial-eval-{uuid.uuid4()}"

    def set_system_prompt(self, prompt: str) -> None:
        self.system_prompt = prompt

    async def generate(
        self,
        user_text: str,
        schema: dict,
        activity: str,
        turn_idx: int,
        history: list[dict] | None = None,
    ) -> dict[str, Any]:
        if self.calls_made >= self.max_calls:
            raise BudgetExceeded(f"model-call cap {self.max_calls} reached")

        messages: list[dict] = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user_text})

        wire_schema = schema
        reasoning_control = {
            "effort": "low",
            "exclude": True,
        }
        provider_by_model = {
            MODELS[model_key]: provider for model_key, provider in MODEL_PROVIDER.items()
        }
        if self.model not in provider_by_model:
            raise ValueError(f"no pinned OpenRouter provider for model {self.model!r}")
        provider_controls: dict[str, Any] = {
            "require_parameters": True,
            "only": [provider_by_model[self.model]],
            "allow_fallbacks": False,
        }
        payload = {
            "model": self.model,
            "session_id": self.session_id,
            "messages": messages,
            "max_tokens": 3000,
            "reasoning": reasoning_control,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "eval_response", "strict": True, "schema": wire_schema},
            },
            "provider": provider_controls,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://pokemon-tutorial-eval.local",
            "X-Title": "pokemon-tutorial-eval",
        }

        t0 = time.time()
        raw_text = ""
        response_body = ""
        provider_response: dict[str, Any] = {}
        parsed: dict[str, Any] = {}
        normalizations: list[str] = []
        error = None
        usage = {}
        http_status = None
        request_attempts: list[dict[str, Any]] = []
        native_reasoning_returned = False
        reasoning_tokens = 0
        try:
            timeout = aiohttp.ClientTimeout(total=self.request_timeout)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                for attempt in range(1, MAX_TRANSIENT_ATTEMPTS + 1):
                    attempt_started = time.time()
                    try:
                        async with session.post(OPENROUTER_URL, headers=headers, json=payload) as resp:
                            http_status = resp.status
                            response_body = await resp.text()
                        attempt_record = {
                            "attempt": attempt,
                            "http_status": http_status,
                            "response_body": response_body,
                            "error": None,
                            "latency_ms": int((time.time() - attempt_started) * 1000),
                        }
                        request_attempts.append(attempt_record)
                        empty_structured_response = False
                        transient_envelope_error = False
                        invalid_structured_response = False
                        if http_status == 200:
                            try:
                                response_probe = json.loads(response_body)
                                if "error" in response_probe:
                                    envelope = response_probe.get("error") or {}
                                    code = envelope.get("code") if isinstance(envelope, dict) else None
                                    transient_envelope_error = (
                                        isinstance(code, int) and (code == 429 or code >= 500))
                                    if transient_envelope_error:
                                        attempt_record["error"] = f"provider envelope error {code}"
                                else:
                                    probe_message = response_probe["choices"][0]["message"]
                                    probe_content = probe_message.get("content") or ""
                                    empty_structured_response = not bool(probe_content)
                                    if probe_content:
                                        try:
                                            _parse_structured(probe_content, wire_schema)
                                        except (ValueError, TypeError, json.JSONDecodeError) as exc:
                                            invalid_structured_response = True
                                            attempt_record["error"] = (
                                                f"structured response invalid: {type(exc).__name__}: {exc}")
                            except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
                                invalid_structured_response = True
                                attempt_record["error"] = (
                                    f"provider response invalid: {type(exc).__name__}: {exc}")
                        if empty_structured_response:
                            attempt_record["error"] = "empty assistant content"
                        if ((http_status not in TRANSIENT_HTTP_STATUSES
                             and not empty_structured_response
                             and not invalid_structured_response
                             and not transient_envelope_error) or
                                attempt == MAX_TRANSIENT_ATTEMPTS):
                            break
                    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                        http_status = None
                        response_body = ""
                        request_attempts.append({
                            "attempt": attempt,
                            "http_status": None,
                            "response_body": "",
                            "error": f"{type(exc).__name__}: {exc}",
                            "latency_ms": int((time.time() - attempt_started) * 1000),
                        })
                        if attempt == MAX_TRANSIENT_ATTEMPTS:
                            raise
                    await asyncio.sleep(2 ** (attempt - 1))
            provider_response = json.loads(response_body)
            if "error" in provider_response:
                error = str(provider_response["error"])
            else:
                message = provider_response["choices"][0]["message"]
                raw_text = message.get("content") or ""
                usage = provider_response.get("usage", {}) or {}
                native_reasoning_returned = bool(
                    message.get("reasoning") or message.get("reasoning_details")
                )
                token_details = (usage.get("completion_tokens_details") or
                                 usage.get("output_tokens_details") or {})
                reasoning_tokens = int(token_details.get("reasoning_tokens", 0) or 0)
                parsed, normalizations = _parse_structured(raw_text, wire_schema)
        except Exception as e:
            error = f"{type(e).__name__}: {e}"

        billed_in = billed_out = billed_reasoning = 0
        billed_cost_usd = 0.0
        for attempt_record in request_attempts:
            try:
                attempt_body = json.loads(attempt_record.get("response_body") or "{}")
                attempt_usage = attempt_body.get("usage") or {}
            except (TypeError, json.JSONDecodeError):
                continue
            billed_in += int(attempt_usage.get("prompt_tokens", 0) or 0)
            billed_out += int(attempt_usage.get("completion_tokens", 0) or 0)
            attempt_details = (attempt_usage.get("completion_tokens_details") or
                               attempt_usage.get("output_tokens_details") or {})
            billed_reasoning += int(attempt_details.get("reasoning_tokens", 0) or 0)
            billed_cost_usd += float(attempt_usage.get("cost", 0) or 0)

        self.calls_made += 1
        self.tokens_in += billed_in
        self.tokens_out += billed_out
        self.reasoning_tokens += billed_reasoning
        latency_ms = int((time.time() - t0) * 1000)

        self.log_cb(
            {
                "turn_idx": turn_idx,
                "activity": activity,
                "shown_text": user_text,
                "request_messages": messages,
                "request_schema": schema,
                "transport_schema": wire_schema,
                "request_controls": {
                    "max_tokens": payload["max_tokens"],
                    "response_format": "json_schema/strict",
                    "reasoning": reasoning_control,
                    "session_id": self.session_id,
                    "provider": provider_controls,
                },
                "request_attempts": request_attempts,
                "http_status": http_status,
                "provider_response_raw": response_body,
                "provider_response": provider_response,
                "raw_response": raw_text,
                "parsed": parsed,
                "normalizations": normalizations,
                "error": error,
                "tokens": {"in": billed_in, "out": billed_out,
                           "reasoning": billed_reasoning},
                "final_attempt_tokens": {
                    "in": int(usage.get("prompt_tokens", 0) or 0),
                    "out": int(usage.get("completion_tokens", 0) or 0),
                    "reasoning": reasoning_tokens,
                },
                "billed_attempt_cost_usd": billed_cost_usd,
                "native_reasoning_returned": native_reasoning_returned,
                "latency_ms": latency_ms,
            }
        )
        if error:
            return {"error": error}
        return parsed
