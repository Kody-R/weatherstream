from __future__ import annotations

import base64
import unittest
from unittest.mock import patch

from app.security import SlidingWindowLimiter, client_address, valid_basic_authorization


class SecurityTests(unittest.TestCase):
    def test_authentication_is_optional_for_compatible_upgrades(self) -> None:
        with patch("app.security.ADMIN_PASSWORD", ""):
            self.assertTrue(valid_basic_authorization(None))

    def test_basic_auth_uses_configured_credentials(self) -> None:
        token = base64.b64encode(b"operator:correct horse battery staple").decode("ascii")
        with patch("app.security.ADMIN_USER", "operator"), patch("app.security.ADMIN_PASSWORD", "correct horse battery staple"):
            self.assertTrue(valid_basic_authorization(f"Basic {token}"))
            self.assertFalse(valid_basic_authorization("Basic invalid"))

    def test_proxy_header_is_ignored_by_default(self) -> None:
        with patch("app.security.TRUST_PROXY_HEADERS", False):
            self.assertEqual(client_address("10.0.0.5", "203.0.113.10"), "10.0.0.5")
        with patch("app.security.TRUST_PROXY_HEADERS", True):
            self.assertEqual(client_address("10.0.0.5", "203.0.113.10, 10.0.0.1"), "203.0.113.10")

    def test_sliding_window_limiter_rejects_excess_calls(self) -> None:
        limiter = SlidingWindowLimiter()
        self.assertTrue(limiter.check("client", 2, 60).allowed)
        self.assertTrue(limiter.check("client", 2, 60).allowed)
        rejected = limiter.check("client", 2, 60)
        self.assertFalse(rejected.allowed)
        self.assertGreaterEqual(rejected.retry_after, 1)


if __name__ == "__main__":
    unittest.main()

