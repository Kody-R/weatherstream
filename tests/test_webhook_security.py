from __future__ import annotations

import socket
import unittest
from unittest.mock import patch

from app.webhook_security import validate_webhook_url


def address(ip: str):
    return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))]


class WebhookSecurityTests(unittest.TestCase):
    def test_public_https_target_is_accepted(self) -> None:
        with patch("app.webhook_security.socket.getaddrinfo", return_value=address("93.184.216.34")):
            self.assertEqual(validate_webhook_url("https://example.com/hook"), "https://example.com/hook")

    def test_private_target_is_rejected_by_default(self) -> None:
        with patch("app.webhook_security.socket.getaddrinfo", return_value=address("192.168.1.9")):
            with self.assertRaisesRegex(ValueError, "private or special-use"):
                validate_webhook_url("http://weather-relay.local/hook")

    def test_trusted_private_target_requires_explicit_opt_in(self) -> None:
        self.assertEqual(validate_webhook_url("http://127.0.0.1:8123/hook", allow_private=True), "http://127.0.0.1:8123/hook")

    def test_credentials_and_non_http_schemes_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            validate_webhook_url("file:///etc/passwd")
        with self.assertRaises(ValueError):
            validate_webhook_url("https://user:secret@example.com/hook")


if __name__ == "__main__":
    unittest.main()
