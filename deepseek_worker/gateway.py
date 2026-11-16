"""Client for the gateway's Anthropic Messages endpoint, with rate limiting and retries."""

import json
import socket
import threading
import time
import urllib.error
import urllib.request
from collections import deque

from . import config as config_module

MAX_RATE_LIMIT_RETRIES = 5
MAX_BACKOFF_SECONDS = 30
# When the gateway looks down: three quick retries, then give up.
OUTAGE_RETRY_DELAYS = (1, 2, 4)
OUTAGE_STATUS_CODES = (502, 503, 504)


class GatewayError(Exception):
    """A request failed in a way that retrying will not fix."""


class RateLimiter:
    """Keeps requests under a per-minute limit. Shared by every task in this process."""

    def __init__(self, per_minute, clock=time.monotonic, sleep=time.sleep):
        self.per_minute = per_minute
        self._clock = clock
        self._sleep = sleep
        self._sent = deque()
        self._lock = threading.Lock()

    def acquire(self):
        while True:
            with self._lock:
                now = self._clock()
                while self._sent and now - self._sent[0] >= 60:
                    self._sent.popleft()
                if len(self._sent) < self.per_minute:
                    self._sent.append(now)
                    return
                wait = 60 - (now - self._sent[0])
            self._sleep(wait)


class Gateway:
    def __init__(self, config, limiter=None, opener=urllib.request.urlopen, sleep=time.sleep,
                 clock=time.monotonic):
        self._config = config
        self._limiter = limiter or RateLimiter(config.requests_per_minute)
        self._opener = opener
        self._sleep = sleep
        self._clock = clock
        self._down_until = 0.0

    def create_message(self, system, messages, tools):
        config = self._config
        missing = config_module.missing_settings(config)
        if missing:
            raise GatewayError(f"Not set up: no {', '.join(missing)}. Run scripts/install.sh "
                               "with a provider profile. See the plugin README.")
        wait = self._down_until - self._clock()
        if wait > 0:
            raise GatewayError(f"The gateway was down a moment ago, so it is skipped for "
                               f"{round(wait)} more seconds.")

        body = json.dumps({
            "model": config.model,
            "max_tokens": config.max_output_tokens,
            "system": system,
            "tools": tools,
            "messages": messages,
        }).encode()
        headers = {
            "Authorization": f"Bearer {config.api_key}",
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        if config.session_header and config.session_id:
            # Some gateways use this to keep one person's requests on one engine, so the
            # prompt cache stays warm.
            headers[config.session_header] = config.session_id

        rate_retries = 0
        outage_retries = 0
        while True:
            self._limiter.acquire()
            request = urllib.request.Request(
                f"{config.base_url}/v1/messages", data=body, method="POST", headers=headers)
            try:
                with self._opener(request, timeout=config.request_timeout_seconds) as response:
                    return json.load(response)
            except urllib.error.HTTPError as error:
                detail = _read_body(error)
                if error.code == 429 and rate_retries < MAX_RATE_LIMIT_RETRIES:
                    rate_retries += 1
                    self._sleep(_retry_after(error, rate_retries))
                    continue
                if error.code in OUTAGE_STATUS_CODES:
                    if outage_retries < len(OUTAGE_RETRY_DELAYS):
                        self._sleep(OUTAGE_RETRY_DELAYS[outage_retries])
                        outage_retries += 1
                        continue
                    self._mark_down()
                raise GatewayError(_explain(error.code, detail, config.help)) from None
            except OSError as error:
                if _is_timeout(error):
                    # Retrying a timeout would mean another long wait, so stop here.
                    self._mark_down()
                    raise GatewayError(f"The gateway did not answer within "
                                       f"{config.request_timeout_seconds} seconds.") from None
                if outage_retries < len(OUTAGE_RETRY_DELAYS):
                    self._sleep(OUTAGE_RETRY_DELAYS[outage_retries])
                    outage_retries += 1
                    continue
                self._mark_down()
                raise GatewayError(f"Could not reach the gateway at {config.base_url} "
                                   f"after {outage_retries} retries: {error}") from None
            except ValueError:
                raise GatewayError("The gateway returned a response that is not JSON.") from None

    def _mark_down(self):
        self._down_until = self._clock() + self._config.outage_pause_seconds


def _is_timeout(error):
    # urllib wraps a connect timeout in URLError. A read timeout comes through as is.
    return isinstance(error, socket.timeout) or isinstance(getattr(error, "reason", None), socket.timeout)


def _read_body(error):
    try:
        return error.read().decode(errors="replace")
    except OSError:
        return ""


def _retry_after(error, attempt):
    value = error.headers.get("Retry-After") if error.headers else None
    try:
        return min(float(value), MAX_BACKOFF_SECONDS)
    except (TypeError, ValueError):
        return min(2 ** attempt, MAX_BACKOFF_SECONDS)


def _explain(code, detail, help_text=""):
    message = _describe(code, detail)
    if help_text and code in (401, 403, 429):
        message += " " + help_text
    return message


def _describe(code, detail):
    if code == 401:
        return "The gateway rejected the key (401). Check the key, or rotate it."
    if code == 403 and "model_blocked" in detail:
        return "This model is not allowed for your key (403 model_blocked). Check the model in your profile."
    if code == 403 and "access_blocked" in detail:
        return "Your key is inactive or expired (403 access_blocked)."
    if code == 403:
        return f"The gateway refused the request (403): {detail[:300]}"
    if code == 429:
        return "Still rate limited after retries (429). If this keeps happening, ask for a higher limit."
    if code == 502:
        return ("The model is not being served right now (502), "
                f"after {len(OUTAGE_RETRY_DELAYS)} retries.")
    if code in OUTAGE_STATUS_CODES:
        return f"The gateway is down right now (HTTP {code}), after {len(OUTAGE_RETRY_DELAYS)} retries."
    return f"The gateway returned HTTP {code}: {detail[:300]}"
