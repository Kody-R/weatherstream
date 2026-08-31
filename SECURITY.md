# WeatherStream security

WeatherStream is designed for self-hosted operation. IPTV playback endpoints are public by design, but administrator pages and maintenance APIs should be protected whenever the service is reachable by untrusted devices.

## Enable administrator authentication

Set both values in Compose and recreate the container:

```yaml
environment:
  - WEATHERSTREAM_ADMIN_USER=admin
  - WEATHERSTREAM_ADMIN_PASSWORD=use-a-long-random-password
```

Authentication is disabled when `WEATHERSTREAM_ADMIN_PASSWORD` is empty so existing private-LAN installations continue to work after upgrading. The Broadcast Dashboard displays whether protection is active.

HTTP Basic credentials must be protected in transit. Use HTTPS at a trusted reverse proxy for any access beyond a private, trusted LAN.

## Reverse proxies

`WEATHERSTREAM_TRUST_PROXY_HEADERS` defaults to `false`. Enable it only when a trusted proxy strips and replaces incoming `X-Forwarded-For` headers. Incorrect use allows clients to spoof the address used for rate limiting.

## Network exposure

- Do not expose port 8787 directly to the public Internet.
- Prefer a firewall, VPN, or authenticated HTTPS reverse proxy.
- Keep `/config` backups private; they include settings, locations, branding, and the weather-history database.
- Review `/api/diagnostics` bundles before sharing them.

## Reporting a vulnerability

When publishing the project, add a private security contact or repository security-advisory link here. Avoid opening public issues that include working exploits, credentials, personal location data, or diagnostic bundles.

