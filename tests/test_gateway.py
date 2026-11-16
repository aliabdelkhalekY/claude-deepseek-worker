import io
import json
import socket
import unittest
import urllib.error

from deepseek_worker.gateway import Gateway, GatewayError, RateLimiter
from tests.helpers import make_config


def http_error(code, body="", headers=None):
    return urllib.error.HTTPError("https://example.test", code, "error", headers or {}, io.BytesIO(body.encode()))


class FakeOpener:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return io.BytesIO(json.dumps(outcome).encode())


class NoLimit:
    def acquire(self):
        pass


class GatewayTest(unittest.TestCase):
    def make(self, outcomes, **config):
        self.sleeps = []
        self.now = [0.0]
        self.opener = FakeOpener(outcomes)
        return Gateway(make_config(**config), limiter=NoLimit(), opener=self.opener,
                       sleep=self.sleeps.append, clock=lambda: self.now[0])

    def test_sends_the_request_the_gateway_expects(self):
        gateway = self.make([{"content": []}], DEEPSEEK_WORKER_SESSION_ID="someone",
                            DEEPSEEK_WORKER_SESSION_HEADER="x-session-id")
        self.assertEqual(gateway.create_message("sys", [{"role": "user", "content": "hi"}], []), {"content": []})
        request = self.opener.requests[0]
        self.assertEqual(request.full_url, "https://gateway.test/anthropic/v1/messages")
        self.assertEqual(request.get_header("Authorization"), "Bearer test-key")
        self.assertEqual(request.get_header("X-session-id"), "someone")
        body = json.loads(request.data)
        self.assertEqual(body["model"], "test-model")
        self.assertEqual(body["max_tokens"], 32768)
        self.assertEqual(body["system"], "sys")

    def test_leaves_out_session_header_when_not_set(self):
        for config in ({}, {"DEEPSEEK_WORKER_SESSION_ID": "someone"},
                       {"DEEPSEEK_WORKER_SESSION_HEADER": "x-session-id"}):
            gateway = self.make([{"content": []}], **config)
            gateway.create_message("sys", [], [])
            self.assertIsNone(self.opener.requests[0].get_header("X-session-id"))

    def test_retries_rate_limits_using_retry_after(self):
        gateway = self.make([http_error(429, headers={"Retry-After": "3"}), http_error(429), {"ok": True}])
        self.assertEqual(gateway.create_message("sys", [], []), {"ok": True})
        self.assertEqual(self.sleeps, [3.0, 4])

    def test_gives_up_after_too_many_rate_limits(self):
        gateway = self.make([http_error(429)] * 6, DEEPSEEK_WORKER_HELP="Ask in #help.")
        with self.assertRaisesRegex(GatewayError, "higher limit. Ask in #help."):
            gateway.create_message("sys", [], [])
        self.assertEqual(len(self.opener.requests), 6)

    def test_retries_an_outage_three_times_quickly(self):
        gateway = self.make([http_error(502), http_error(503), http_error(504), {"ok": True}])
        self.assertEqual(gateway.create_message("sys", [], []), {"ok": True})
        self.assertEqual(self.sleeps, [1, 2, 4])

    def test_after_three_retries_it_gives_up_and_skips_the_gateway_for_a_while(self):
        gateway = self.make([http_error(502)] * 4 + [{"ok": True}])
        with self.assertRaisesRegex(GatewayError, "not being served.*after 3 retries"):
            gateway.create_message("sys", [], [])
        self.assertEqual(len(self.opener.requests), 4)

        # The next task fails at once, without a request.
        self.now[0] = 60
        with self.assertRaisesRegex(GatewayError, "skipped for 60 more seconds"):
            gateway.create_message("sys", [], [])
        self.assertEqual(len(self.opener.requests), 4)

        # After the pause it tries again.
        self.now[0] = 121
        self.assertEqual(gateway.create_message("sys", [], []), {"ok": True})

    def test_a_timeout_is_not_retried(self):
        gateway = self.make([urllib.error.URLError(socket.timeout("timed out"))])
        with self.assertRaisesRegex(GatewayError, "did not answer within 300 seconds"):
            gateway.create_message("sys", [], [])
        self.assertEqual(len(self.opener.requests), 1)
        with self.assertRaisesRegex(GatewayError, "skipped"):
            gateway.create_message("sys", [], [])

        gateway = self.make([socket.timeout("timed out")])
        with self.assertRaisesRegex(GatewayError, "did not answer"):
            gateway.create_message("sys", [], [])

    def test_explains_key_problems_without_retrying(self):
        cases = [
            (http_error(401), "rejected the key"),
            (http_error(403, '{"error": "model_blocked"}'), "model in your profile"),
            (http_error(403, '{"error": "access_blocked"}'), "inactive or expired"),
        ]
        for error, message in cases:
            gateway = self.make([error])
            with self.assertRaisesRegex(GatewayError, message):
                gateway.create_message("sys", [], [])
            self.assertEqual(len(self.opener.requests), 1)

    def test_network_errors_retry_three_times(self):
        gateway = self.make([urllib.error.URLError("down")] * 4)
        with self.assertRaisesRegex(GatewayError, "Could not reach.*after 3 retries"):
            gateway.create_message("sys", [], [])
        self.assertEqual(len(self.opener.requests), 4)
        self.assertEqual(self.sleeps, [1, 2, 4])

    def test_missing_key_fails_before_any_request(self):
        gateway = self.make([], DEEPSEEK_WORKER_API_KEY="")
        with self.assertRaisesRegex(GatewayError, "no api key"):
            gateway.create_message("sys", [], [])

    def test_missing_gateway_settings_fail_before_any_request(self):
        gateway = self.make([], DEEPSEEK_WORKER_BASE_URL="", DEEPSEEK_WORKER_MODEL="")
        with self.assertRaisesRegex(GatewayError, "no base_url, model"):
            gateway.create_message("sys", [], [])


class RateLimiterTest(unittest.TestCase):
    def test_waits_when_the_minute_is_full(self):
        now = [0.0]
        waits = []

        def sleep(seconds):
            waits.append(seconds)
            now[0] += seconds

        limiter = RateLimiter(2, clock=lambda: now[0], sleep=sleep)
        limiter.acquire()
        now[0] = 10.0
        limiter.acquire()
        now[0] = 20.0
        limiter.acquire()
        self.assertEqual(waits, [40.0])


if __name__ == "__main__":
    unittest.main()
