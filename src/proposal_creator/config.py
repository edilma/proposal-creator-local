"""Model configuration: provider, base URL, key, and model name.

Values come from the environment, with a `.env` file as fallback — never
hardcoded (see decision 004). The API key is required for real providers and
is never printed, logged, or included in error messages.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

DEFAULT_ENV_FILE = ".env"
ENV_FILE_VAR = "PROPOSAL_ENV_FILE"


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class ModelConfig:
    provider: str
    base_url: str
    api_key: str
    model: str
    reasoning_effort: str
    max_tokens: int
    temperature: float
    timeout: int
    company_profile: str

    def describe(self) -> str:
        """Human-readable summary that never includes the key."""
        return (
            f"provider={self.provider} model={self.model}"
            f" base_url={self.base_url} reasoning_effort={self.reasoning_effort}"
            f" max_tokens={self.max_tokens} temperature={self.temperature}"
        )


def _read_env_file(path: str) -> dict:
    values: dict = {}
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return values
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, raw = line.partition("=")
        raw = raw.strip()
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ("'", '"'):
            raw = raw[1:-1]
        values[key.strip()] = raw
    return values


def load(
    env_file: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> ModelConfig:
    import os

    environ = os.environ if environ is None else environ
    if env_file is None:
        env_file = environ.get(ENV_FILE_VAR, DEFAULT_ENV_FILE)
    file_values = _read_env_file(env_file)

    def get(name: str, default: str) -> str:
        if name in environ:
            return environ[name]
        return file_values.get(name, default)

    provider = get("PROPOSAL_MODEL_PROVIDER", "meta")
    api_key = get("PROPOSAL_MODEL_API_KEY", "")
    if provider != "stub" and not api_key:
        raise ConfigError(
            "PROPOSAL_MODEL_API_KEY is not set. Add it to .env"
            " (see .env.example) or export it; it is never printed or logged."
        )
    try:
        max_tokens = int(get("PROPOSAL_MODEL_MAX_TOKENS", "4000"))
        temperature = float(get("PROPOSAL_MODEL_TEMPERATURE", "0.0"))
        timeout = int(get("PROPOSAL_MODEL_TIMEOUT", "120"))
    except ValueError as exc:
        raise ConfigError(f"Invalid numeric model setting: {exc}.") from exc
    return ModelConfig(
        provider=provider,
        base_url=get("PROPOSAL_MODEL_BASE_URL", "https://api.meta.ai/v1"),
        api_key=api_key,
        model=get("PROPOSAL_MODEL_NAME", "muse-spark-1.3"),
        reasoning_effort=get("PROPOSAL_MODEL_REASONING_EFFORT", "minimal"),
        max_tokens=max_tokens,
        temperature=temperature,
        timeout=timeout,
        company_profile=get("PROPOSAL_COMPANY_PROFILE", "data/company_profile.md"),
    )
