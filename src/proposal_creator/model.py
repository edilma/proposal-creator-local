"""Minimal OpenAI-compatible chat client over the standard library.

Sends `reasoning_effort` and `response_format: json_object` (supported by the
default model); set reasoning effort blank to omit it for other providers.
The API key travels only in the Authorization header — never in errors.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from .config import ModelConfig

Transport = Callable[[str, dict, dict], dict]


class ModelError(Exception):
    pass


@dataclass(frozen=True)
class ModelResult:
    content: str
    usage: dict


def make_urllib_transport(timeout: int) -> Transport:
    def sender(url: str, headers: dict, body: dict) -> dict:
        data = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            url, data=data, headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8", "replace")[:300]
            except Exception:
                detail = ""
            message = f"Model API error: HTTP {exc.code}. {detail}".strip()
            raise ModelError(message) from exc
        except urllib.error.URLError as exc:
            raise ModelError(f"Could not reach the model API: {exc.reason}.") from exc

    return sender


def complete(
    config: ModelConfig,
    messages: list,
    transport: Transport | None = None,
    timeout: int | None = None,
) -> ModelResult:
    body: dict[str, Any] = {
        "model": config.model,
        "messages": messages,
        "temperature": config.temperature,
        "max_tokens": config.max_tokens,
        "response_format": {"type": "json_object"},
    }
    if config.reasoning_effort:
        body["reasoning_effort"] = config.reasoning_effort
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {config.api_key}",
    }
    if transport is None:
        transport = make_urllib_transport(
            config.timeout if timeout is None else timeout
        )
    sender = transport
    try:
        payload = sender(f"{config.base_url}/chat/completions", headers, body)
    except ModelError:
        raise
    except Exception as exc:
        raise ModelError(f"Model call failed: {exc}.") from exc
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ModelError("Model response had no usable content.") from exc
    if not content or not str(content).strip():
        raise ModelError(
            "Model returned empty content — the reasoning budget likely spent"
            " the token allowance. Raise PROPOSAL_MODEL_MAX_TOKENS and retry."
        )
    usage = payload.get("usage") or {}
    return ModelResult(content=str(content), usage=dict(usage))
