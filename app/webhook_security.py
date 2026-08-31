from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit


def validate_webhook_url(value: str, allow_private: bool = False) -> str:
    """Validate a webhook destination before each delivery.

    DNS is intentionally checked at delivery time instead of only when settings
    are saved, so a hostname that later changes to a local address is rejected.
    """
    url = str(value or "").strip()
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Webhook URL must use http or https and include a host.")
    if parsed.username or parsed.password:
        raise ValueError("Webhook credentials must not be embedded in the URL.")
    if len(url) > 1000:
        raise ValueError("Webhook URL is too long.")
    if not allow_private:
        try:
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            addresses = {item[4][0] for item in socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)}
        except (OSError, ValueError) as exc:
            raise ValueError(f"Webhook host could not be resolved: {exc}") from exc
        for address in addresses:
            ip = ipaddress.ip_address(address)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
                raise ValueError("Webhook resolves to a private or special-use address; enable private targets only for a trusted LAN service.")
    return url
