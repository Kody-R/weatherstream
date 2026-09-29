# Changelog

## 0.3.4

### Added

- RWN Icon & Motion System with 207 bundled transparent PNG assets
- Hero, standard, and compact weather-condition artwork with day/night variants
- Four-frame motion sequences for selected cloud, precipitation, storm, snow, wind, lightning, and tropical states
- Central `app.iconography` weather-code resolver and cached asset loader
- RWN metric symbols for temperature, humidity, dew point, wind, gusts, pressure, cloud cover, rain, UV, visibility, sunrise, and sunset
- Dedicated alert icon family for tornado, severe thunderstorm, flash flood, winter storm, extreme heat, wildfire/smoke, and tropical hazards
- Admin controls for icon enablement, animation, motion speed, metric icons, and alert icons
- Reproducible icon generator under `tools/generate_rwn_icons.py`
- Schema 22 migration and v0.3.4 regression coverage

### Changed

- Current Conditions, Today, Hourly, 7-Day, and other weather-code screens now resolve condition artwork through the RWN icon system
- Current Conditions and Condition Focus metric cards use RWN symbols
- Severe Alert presentation automatically selects an event-specific RWN alert identity
- Legacy procedural weather icons remain available as a compatibility fallback

### Validation

- 51 automated tests plus 13 render subtests pass
- Core icon-enabled boards validated at 1280×720 and 1920×1080

## 0.3.3

### Added

- Map Engine 3.0 with cached official NOAA geographic overlays
- SPC Day 1 categorical geographic outlook slide
- SPC Day 1 tornado, hail, and damaging-wind probability triptych
- WPC Day 1 National Forecast Chart slide with fronts, highs/lows, precipitation, and significant-weather features
- WPC Day 1 24-hour quantitative precipitation forecast slide
- Severe Weather Hazard Map combining regional radar, warning polygons, warning legend, and local SPC context
- Map Engine 3.0 Admin layer controls and Studio slide support
- Schema 21 migration and v0.3.3 regression coverage

### Changed

- NWS warning polygons now use translucent fills, stronger colored outlines, and compact event labels on radar
- Radar, severe-weather, tornado, flood, and winter rundowns include relevant Map Engine 3.0 products
- NOAA map overlays refresh and cache outside the frame-render hot path with last-known-good fallback
- GOES imagery configuration now follows Map Engine 3.0 settings with Map Engine 2.0 fallback for upgraded installations

### Validation

- 46 automated tests plus 13 render subtests pass
- New Map Engine graphics validated at 1280×720 and 1920×1080

## 0.3.2

### Added

- Next 24 Hours forecast board with temperature, feels-like, rain chance, peak gust, and 24-hour summary metrics
- Humidity & Dew Point 12-hour trend graphic with local comfort classification
- Wind Outlook with sustained/gust trend lines and directional arrows
- Rainfall Accumulation graph with 6-, 12-, and 24-hour forecast totals
- Broadcast-style NWS Forecast Brief derived from official NWS forecast periods
- Forecast Graphics 2.0 Admin controls and Studio slide support
- Smart Story Ordering with wind, dew-point, rain accumulation, storm, and SPC-aware sequencing
- Schema 20 migration and v0.3.2 regression coverage

### Changed

- Open-Meteo hourly ingestion now retains wind direction and cloud cover
- NWS forecast ingestion retains precipitation probability, humidity, and dew-point fields when provided
- Local/daypart default sequences include the new forecast graphics, with quiet-weather products filtered by Smart Programming thresholds

## 0.3.1

### Added

- Visual System 2.0 logical 1280×720 design canvas with single-pass scaling to configured output resolution
- Reusable metric/source/freshness presentation helpers for broadcast graphics
- Redesigned Current Conditions, Hourly Forecast, Rain Timing, 7-Day Outlook, and Severe Alert screens
- RWN data-ribbon lower third with classic crawl compatibility mode
- Subtle condition-responsive weather backgrounds
- Admin toggles for Visual System 2.0, footer mode, source badges, freshness, and condition backgrounds

### Changed

- Radar lower information strip now shows loop timing and latest image time instead of encoder/operator presentation values
- Core forecast screens emphasize trends, timing, and weather context rather than repeated card layouts
- Settings schema advanced to 19 while preserving existing v0.3.0 configuration

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
