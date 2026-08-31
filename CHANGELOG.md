# Changelog

## 0.3.0

### Added

- Region-scoped Tornado, Flood, Winter Weather, Wildfire, and Extreme Heat event channels with NWS-alert activation and cooldown retention
- Map Engine 2.0 with configurable radar, warnings, cities, boundaries, tropical-track, GOES-19 GeoColor, and GLM lightning products
- WeatherStream Studio rundown editor, draggable sequence ordering, preview workflow, bumper builder, and region/channel/daypart schedules
- Multi-region location assignment, region identity, region-centered radar caches, and scoped IPTV identifiers
- Reusable branding profiles with station identity, theme, accent, profile logo, and music-subfolder support
- Per-source refresh API and Dashboard buttons
- Schema 18 migration and v0.3.0 regression coverage

### Changed

- Radar channel defaults now include Map Engine 2.0, satellite, and lightning slides
- Channel catalog includes disabled standby channels, region metadata, and resolved branding profiles
- IPTV playlists select profile logos when present
- Source status now includes NOAA satellite and lightning freshness

### Reliability

- NOAA imagery uses last-known-good in-memory caching and never performs network I/O in the render loop
- Region assignments are normalized so a location cannot activate two regions ambiguously
- Older single-region settings migrate without changing existing station identity or lineup overrides

## 0.2.6

### Added

- Seasonal Tropical Weather Update for normal RWN Local programming
- Stable RWN Tropics Watch IPTV/XMLTV channel
- Official NHC current-system, Atlantic outlook, and forecast-track ingestion
- Gulf-region, forecast-radius, development-probability, and local-alert activation logic
- Tropical overview, systems board, forecast-track, and local-impact slides
- Dynamic active-storm XMLTV descriptions
- Tropical Admin settings, previews, status, data-source diagnostics, and webhook events
- Intel render-node enumeration, metadata, real QSV/VAAPI encode probes, and Admin device selection from v0.2.5.1
- Global and per-channel `encoder_device` support
- Hardware-selection, generated-command, tropical-logic, and lineup regression tests
- Schema 17 migration covering tropical and hardware defaults

### Changed

- On-demand channel supervision can automatically start and retain Tropics Watch while an official trigger or cooldown is active
- Render-context revisions now include tropical-data state
- Normal RWN Local sequences receive a seasonal tropical update without requiring an encoder restart
- QSV uses the selected DRM render node as a Linux VAAPI child device; VAAPI explicitly initializes the selected node
- Failed hardware starts immediately retry with `libx264` without abandoning the original on-demand request

### Security and reliability

- NHC-discovered forecast files are restricted to official HTTPS hosts, with every redirect revalidated
- KMZ/KML reads are size bounded
- Tropical refresh failures retain last-known-good data and never block frame rendering
- Forecast graphics explicitly distinguish track/center guidance from the broader impact area
- Removed the hard-coded `/dev/dri/renderD128` assumption and validate render-node settings before use

## 0.2.5

### Added

- Guided first-run setup for ZIP, station identity, theme, and stream lifecycle mode
- Settings search and operational-impact labels
- Copy controls for M3U, XMLTV, and HLS channel endpoints
- Accelerated, non-disruptive full-rundown preview
- Structured severe-weather, source-health, stream, settings, refresh, and lifecycle events
- Optional bounded webhook notifications with cooldowns, delivery status, and test control
- Settings schema 15 notification migration and test coverage

### Changed

- Fresh installations enter setup before the channel home page
- Admin settings explain whether changes apply immediately, refresh data, or restart encoders
- Diagnostics redact configured webhook URLs

### Security

- Webhooks accept only HTTP(S), reject embedded credentials, do not follow redirects, and block private or special-use targets unless a trusted LAN target is explicitly enabled
- Notification tests use the existing administrator authentication and a three-per-five-minute rate limit

## 0.2.4

### Added

- Revision-aware settings, weather, and SPC snapshots
- Cached per-channel render contexts and dashboard cache statistics
- Persistent upstream HTTP connection pools
- Bounded concurrent ZIP refresh worker pool
- Shared target-size radar frame and basemap caches
- Cached transition masks and resized branding logos
- Batched SQLite observation insertion and cached history reads
- Performance-foundation unit tests

### Changed

- Rendering no longer deep-copies the complete settings and weather trees on every content frame
- Radar/map source images are treated as immutable published assets
- Weather, alert, storm-guidance, and SPC locations refresh concurrently within a configured bound
- SQLite uses WAL mode, explicitly closes short-lived connections, maintains row counts incrementally, and cleans retention once daily

### Fixed

- Closed SQLite connections explicitly, preventing lingering database handles and improving restore/cleanup behavior on Windows-hosted development environments

## 0.2.3

### Added

- Optional HTTP Basic authentication for administrator pages and sensitive APIs
- In-process rate limiting for expensive administrator operations
- Prometheus-compatible `/metrics` output
- Constant-time liveness and readiness endpoints
- Bounded recent-operations feed and Broadcast Dashboard panel
- Fixed-size Piper synthesis executor and bounded queue
- Initial `unittest` coverage for security, backup validation, and observability
- Compose init and graceful-stop configuration

### Changed

- Docker health checks now use `/health/live`
- Full status assembly is cached for one second
- Channel worker start/stop operations are serialized
- Background manager and channel threads are joined during shutdown
- Backup database restores run `PRAGMA integrity_check`
- Uploaded branding images are verified and limited to 20 megapixels

### Security

- Backup members are allow-listed and bounded by file count, expanded size, total size, and compression ratio
- Sensitive routes return an HTTP Basic challenge when `WEATHERSTREAM_ADMIN_PASSWORD` is configured
- Forwarded client addresses are ignored unless explicitly enabled for a trusted proxy
- Common browser hardening headers are attached to responses

## 0.2.2.1

- Converted Local on the 8s into a dedicated, phase-aware programming block
- Added screen-accurate per-phase narration and severe-weather preemption
