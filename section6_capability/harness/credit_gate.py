from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

KEY_STATUS_URL = "https://openrouter.ai/api/v1/key"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def credit_error(value: object) -> bool:
    text = value.lower() if isinstance(value, str) else json.dumps(
        value, ensure_ascii=False).lower()
    return any(marker in text for marker in (
        '"code": 402', '"code":402', "'code': 402", "payment_required",
        "payment required",
        "insufficient credits", "out of credits", "credit limit",
    ))


def remaining(api_key: str, timeout: int = 20) -> float | None:
    request = urllib.request.Request(
        KEY_STATUS_URL, headers={"Authorization": f"Bearer {api_key}"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))["data"]
    value = data.get("limit_remaining")
    return None if value is None else float(value)


def wait_if_exhausted(api_key: str, label: str, force: bool = False,
                      poll_seconds: int = 30) -> float:
    started = time.monotonic()
    paused = force
    next_notice = 0.0
    while True:
        try:
            available = remaining(api_key)
            if available is None or available > 0:
                if paused:
                    print(f"[{now()}] [{label}] CREDIT RESUMED: limit_remaining={available}",
                          flush=True)
                return time.monotonic() - started if paused else 0.0
            paused = True
            status = f"limit_remaining={available}"
        except (OSError, KeyError, TypeError, ValueError, urllib.error.URLError) as exc:
            if not paused:
                return 0.0
            status = f"status check failed: {type(exc).__name__}"

        if time.monotonic() >= next_notice:
            print(f"[{now()}] [{label}] CREDIT PAUSE: {status}. No model requests are being "
                  "sent. Raise/reset the OpenRouter key limit to resume automatically. "
                  "container CPU remains billable while paused.", flush=True)
            next_notice = time.monotonic() + 60
        time.sleep(poll_seconds)
