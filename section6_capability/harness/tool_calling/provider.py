"""OpenRouter native tool calling with the constrained provider's logging and retries."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any, Callable

import aiohttp

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MAX_TRANSIENT_ATTEMPTS = 3
TRANSIENT_HTTP_STATUSES = {408, 409, 425, 429, 500, 502, 503, 504}


class BudgetExceeded(Exception):
    """Raised when the per-session model-call cap is hit."""


def parse_tool_call(message: dict, tools: list[dict]) -> dict[str, Any]:
    calls = message.get("tool_calls") or []
    if not calls:
        raise ValueError("tool call invalid: no tool call returned")
    function = calls[0].get("function") or {}
    name = function.get("name")
    specs = {tool["function"]["name"]: tool["function"]["parameters"] for tool in tools}
    if name not in specs:
        raise ValueError(f"tool call invalid: unknown tool {name!r}")
    raw = function.get("arguments") or "{}"
    arguments = json.loads(raw) if isinstance(raw, str) else dict(raw)
    if not isinstance(arguments, dict):
        raise ValueError("tool call invalid: arguments are not an object")
    schema = specs[name]
    missing = [field for field in schema.get("required", []) if field not in arguments]
    if missing:
        raise ValueError(f"tool call invalid: missing arguments {missing}")
    for field, rule in schema.get("properties", {}).items():
        if field not in arguments:
            continue
        value = arguments[field]
        if "enum" in rule and value not in rule["enum"]:
            raise ValueError(f"tool call invalid: {field}={value!r} is not a listed option")
        items = rule.get("items") or {}
        if rule.get("type") == "array":
            if not isinstance(value, list) or not value:
                raise ValueError(f"tool call invalid: {field} must be a non-empty list")
            if "enum" in items and any(item not in items["enum"] for item in value):
                raise ValueError(f"tool call invalid: {field} has an unlisted value")
    return {"name": name, "arguments": arguments, "content": message.get("content") or "",
            "id": calls[0].get("id")}


class ToolCallingProvider:
    def __init__(self, api_key: str, model: str, provider_policy: dict, max_calls: int,
                 log_cb: Callable[[dict], None], request_timeout: int = 240) -> None:
        self.api_key = api_key
        self.model = model
        self.provider_policy = provider_policy
        self.max_calls = max_calls
        self.log_cb = log_cb
        self.request_timeout = request_timeout
        self.system_prompt = ""
        self.calls_made = 0
        self.tokens_in = 0
        self.tokens_out = 0
        self.reasoning_tokens = 0
        self.cost_usd = 0.0
        self.image_calls = 0
        self.providers: set[str] = set()
        self.session_id = f"pokemon-tutorial-eval-tools-{uuid.uuid4()}"

    def set_system_prompt(self, prompt: str) -> None:
        self.system_prompt = prompt

    async def generate(self, user_text: str, image_uri: str | None, tools: list[dict],
                       activity: str, turn_idx: int,
                       history: list[dict] | None = None) -> dict[str, Any]:
        if self.calls_made >= self.max_calls:
            raise BudgetExceeded(f"model-call cap {self.max_calls} reached")
        messages: list[dict] = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        if history:
            messages.extend(history)
        content: Any = user_text
        if image_uri:
            content = [{"type": "text", "text": user_text},
                       {"type": "image_url", "image_url": {"url": image_uri}}]
        messages.append({"role": "user", "content": content})
        reasoning_control = {"effort": "low", "exclude": True}
        payload = {
            "model": self.model,
            "session_id": self.session_id,
            "messages": messages,
            "max_tokens": 3000,
            "reasoning": reasoning_control,
            "tools": tools,
            "tool_choice": "auto",
            "provider": self.provider_policy,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://pokemon-tutorial-eval.local",
            "X-Title": "pokemon-tutorial-eval",
        }
        t0 = time.time()
        response_body = ""
        provider_response: dict[str, Any] = {}
        parsed: dict[str, Any] = {}
        error = None
        http_status = None
        request_attempts: list[dict[str, Any]] = []
        try:
            timeout = aiohttp.ClientTimeout(total=self.request_timeout)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                for attempt in range(1, MAX_TRANSIENT_ATTEMPTS + 1):
                    attempt_started = time.time()
                    try:
                        async with session.post(OPENROUTER_URL, headers=headers,
                                                json=payload) as resp:
                            http_status = resp.status
                            response_body = await resp.text()
                        record = {"attempt": attempt, "http_status": http_status,
                                  "response_body": response_body, "error": None,
                                  "latency_ms": int((time.time() - attempt_started) * 1000)}
                        request_attempts.append(record)
                        retry = http_status in TRANSIENT_HTTP_STATUSES
                        if http_status == 200:
                            try:
                                probe = json.loads(response_body)
                                if "error" in probe:
                                    envelope = probe.get("error") or {}
                                    code = envelope.get("code") if isinstance(envelope, dict) else None
                                    if isinstance(code, int) and (code == 429 or code >= 500):
                                        retry = True
                                        record["error"] = f"provider envelope error {code}"
                                else:
                                    parse_tool_call(probe["choices"][0]["message"], tools)
                            except (KeyError, IndexError, TypeError, ValueError) as exc:
                                retry = True
                                record["error"] = f"{type(exc).__name__}: {exc}"
                        if not retry or attempt == MAX_TRANSIENT_ATTEMPTS:
                            break
                    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                        http_status, response_body = None, ""
                        request_attempts.append({
                            "attempt": attempt, "http_status": None, "response_body": "",
                            "error": f"{type(exc).__name__}: {exc}",
                            "latency_ms": int((time.time() - attempt_started) * 1000)})
                        if attempt == MAX_TRANSIENT_ATTEMPTS:
                            raise
                    await asyncio.sleep(2 ** (attempt - 1))
            provider_response = json.loads(response_body)
            if "error" in provider_response:
                error = str(provider_response["error"])
            else:
                if provider_response.get("provider"):
                    self.providers.add(str(provider_response["provider"]))
                parsed = parse_tool_call(provider_response["choices"][0]["message"], tools)
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"

        billed_in = billed_out = billed_reasoning = 0
        billed_cost = 0.0
        for record in request_attempts:
            try:
                usage = (json.loads(record.get("response_body") or "{}").get("usage") or {})
            except (TypeError, json.JSONDecodeError):
                continue
            billed_in += int(usage.get("prompt_tokens", 0) or 0)
            billed_out += int(usage.get("completion_tokens", 0) or 0)
            details = usage.get("completion_tokens_details") or {}
            billed_reasoning += int(details.get("reasoning_tokens", 0) or 0)
            billed_cost += float(usage.get("cost", 0) or 0)
        self.calls_made += 1
        self.tokens_in += billed_in
        self.tokens_out += billed_out
        self.reasoning_tokens += billed_reasoning
        self.cost_usd += billed_cost
        self.image_calls += bool(image_uri)
        logged_messages = messages[:-1] + [{"role": "user", "content": user_text,
                                            "image_attached": bool(image_uri)}]
        self.log_cb({
            "turn_idx": turn_idx, "activity": activity, "shown_text": user_text,
            "request_messages": logged_messages, "tools": tools,
            "request_controls": {"max_tokens": 3000, "tool_choice": "auto",
                                 "reasoning": reasoning_control,
                                 "session_id": self.session_id,
                                 "provider": self.provider_policy},
            "request_attempts": request_attempts, "http_status": http_status,
            "provider_response": provider_response, "parsed": parsed, "error": error,
            "tokens": {"in": billed_in, "out": billed_out, "reasoning": billed_reasoning},
            "billed_attempt_cost_usd": billed_cost,
            "latency_ms": int((time.time() - t0) * 1000),
        })
        if error:
            return {"error": error}
        return parsed
