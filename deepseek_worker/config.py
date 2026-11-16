"""Settings for the worker.

The plugin passes them in the environment. scripts/install.sh fills them in from a provider
profile, so nothing here is tied to one gateway.
"""

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CONTEXT_TOKENS = 128_000
# Enough room that a thinking model does not run out before it answers.
DEFAULT_MAX_OUTPUT_TOKENS = 32_768
DEFAULT_REQUESTS_PER_MINUTE = 50

DEFAULT_MAX_TURNS = 40
DEFAULT_REQUEST_TIMEOUT_SECONDS = 300
# After the gateway is found down, skip it for this long so later tasks fail at once.
DEFAULT_OUTAGE_PAUSE_SECONDS = 120
DEFAULT_LOG_PATH = Path.home() / ".local" / "state" / "deepseek-worker" / "delegations.jsonl"


@dataclass(frozen=True)
class Config:
    api_key: str
    base_url: str
    model: str
    session_id: str
    session_header: str
    help: str
    context_tokens: int
    max_output_tokens: int
    requests_per_minute: int
    max_turns: int
    request_timeout_seconds: int
    outage_pause_seconds: int
    log_path: Path


def _positive_int(env, name, default):
    value = env.get(name)
    if not value:
        return default
    try:
        number = int(value)
    except ValueError:
        raise ValueError(f"{name} must be a whole number, got {value!r}.") from None
    if number <= 0:
        raise ValueError(f"{name} must be above zero, got {number}.")
    return number


def load(env=None):
    env = os.environ if env is None else env
    return Config(
        api_key=env.get("DEEPSEEK_WORKER_API_KEY", ""),
        base_url=env.get("DEEPSEEK_WORKER_BASE_URL", "").rstrip("/"),
        model=env.get("DEEPSEEK_WORKER_MODEL", ""),
        session_id=env.get("DEEPSEEK_WORKER_SESSION_ID", ""),
        session_header=env.get("DEEPSEEK_WORKER_SESSION_HEADER", ""),
        help=env.get("DEEPSEEK_WORKER_HELP", ""),
        context_tokens=_positive_int(env, "DEEPSEEK_WORKER_CONTEXT_TOKENS", DEFAULT_CONTEXT_TOKENS),
        max_output_tokens=_positive_int(env, "DEEPSEEK_WORKER_MAX_OUTPUT_TOKENS", DEFAULT_MAX_OUTPUT_TOKENS),
        requests_per_minute=_positive_int(env, "DEEPSEEK_WORKER_REQUESTS_PER_MINUTE", DEFAULT_REQUESTS_PER_MINUTE),
        max_turns=_positive_int(env, "DEEPSEEK_WORKER_MAX_TURNS", DEFAULT_MAX_TURNS),
        request_timeout_seconds=_positive_int(
            env, "DEEPSEEK_WORKER_REQUEST_TIMEOUT_SECONDS", DEFAULT_REQUEST_TIMEOUT_SECONDS),
        outage_pause_seconds=_positive_int(
            env, "DEEPSEEK_WORKER_OUTAGE_PAUSE_SECONDS", DEFAULT_OUTAGE_PAUSE_SECONDS),
        log_path=Path(env.get("DEEPSEEK_WORKER_LOG") or DEFAULT_LOG_PATH).expanduser(),
    )


def missing_settings(config):
    """Names of the settings the worker cannot run without."""
    required = {"api key": config.api_key, "base_url": config.base_url, "model": config.model}
    return [name for name, value in required.items() if not value]
