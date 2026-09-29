# v0.3.10 — RWN Voice & Audio

- Added deterministic narration planning, speech formatting, Piper adapter, cache, per-channel audio director, mixer and captions.
- Added Studio audio health, preview, scoped script edits, regeneration and live voice/bed mute.
- Preserved Local 8 phase synchronization, severe priority and existing hardware encoder selection.
- Advanced configuration to schema 28 and active image/version references to v0.3.10.
- Added 32 tests; all 111 tests pass. See release notes for Windows validation limits.

# Changelog

## 0.3.9

### Added

- Studio Control Room 2.0 with LIVE / NEXT production monitors
- Actual playout state from the renderer timeline, including progress and remaining time
- Graphical rundown with LIVE, NEXT, and QUEUED states plus slide thumbnails
- Weather Story score, reason, evidence, NOW/NEXT/LATER/CONTEXT sections, and override state
- Normalized source-health telemetry with HEALTHY, CACHED, STALE, ERROR, WAITING, and DISABLED states
- Per-channel preview endpoint with generated fallback for idle on-demand channels
- Runtime-only operator takeover controls with 30-second to 15-minute durations
- Broadcast title/action-safe overlays in Studio
- Recent operations/activity feed in Studio
- Dedicated Control Room preference endpoint
- Schema 27 migration and v0.3.9 regression coverage

### Changed

- Manual operator takeovers no longer persist in settings; service restart always returns to automatic programming
- Official severe-weather takeover immediately clears and supersedes any operator takeover
- Local on the 8s and manual takeovers are represented as out-of-band playout blocks instead of being mislabeled as part of the normal rotation
- Studio editor remains separate from temporary TAKE LIVE controls
- Docker image tag, service/API metadata, XMLTV generator, backup/diagnostic names, user agents, and Admin UI identify v0.3.9

### Validation

- Full Python regression suite passes
- Dedicated schema migration, source-health, runtime-takeover, and Control Room surface tests pass
- Python compilation, Studio JavaScript syntax, Docker Compose parsing, route integrity, and browser visual QA pass

## 0.3.9

### Added

- Studio Control Room 2.0 with actual LIVE / NEXT production monitors
- Renderer playout telemetry exposed to Studio, including current slide, next slide, progress, remaining time, daypart, and rundown source
- Graphical rundown with thumbnails and LIVE/NEXT state
- Weather Story reasoning panel with score, evidence, and NOW/NEXT/LATER/CONTEXT sections
- Source-health normalization in `app.control_room` with freshness thresholds and cached/stale/error states
- Recent control-activity panel using WeatherStream observability events
- Broadcast safe-area guides
- Temporary manual Studio takeover with configurable duration, automatic expiry, Return to Auto, and channel restart control
- Per-channel worker playout status and manual-takeover telemetry
- Schema 27 migration and v0.3.9 regression coverage

### Safety / behavior

- Official severe-weather takeovers remain higher priority than operator Studio takeovers
- Local on the 8s is aborted when an operator takeover starts so picture and narration cannot diverge
- Manual takeover state is runtime-only and is not restored after a process/container restart

### Validation

- 79 automated tests plus 49 render subtests pass
- Python compilation, Studio JavaScript syntax, Docker Compose parsing, and browser-based Studio layout QA pass

## 0.3.8

- Added Broadcast Motion 2.0 and automatic desk-aware transition resolution
- Added RWN logo wipe and network panel-push transition
- Added Severe angular, Flood waterline, Winter ice-shard, Heat shimmer, Wildfire smoke-dissolve, and Tropical radar-sweep transitions
- Added subtle desk-aware screen-entry animation
- Severe-warning takeovers can hard-cut immediately, bypassing decorative transitions
- Added Admin motion controls and Schema 26 migration
- Preserved all v0.3.7 event-desk identities, Studio rundowns, Story Engine behavior, and channel configuration

## 0.3.7

### Added

- RWN Event Channel Identity system with six dedicated visual packages: Severe, Flood, Winter, Heat, Wildfire/Smoke, and Tropical
- Central `app.event_identity` resolver so specialty channels share renderer components while inheriting the correct desk package
- Purpose-built event palettes, secondary accents, desk strap lines, and background motifs
- Dedicated event header treatment and specialty-channel footer bug
- Event-aware lower-third data ribbons with event-relevant metrics
- Event Summary 2.0 with RWN hazard artwork, official alert presentation, and event-specific measured/forecast metrics
- Admin controls for event identity, individual desks, background motifs, and direct desk previews
- `tools/render_v037_previews.py` for reproducible 720p/1080p event-package QA
- Schema 25 migration and v0.3.7 regression coverage

### Changed

- Severe channel and Tornado event channels share the Severe Weather Center identity
- Flood, Winter, Heat, and Wildfire event channels now inherit dedicated desk styling across their existing rundowns
- Tropics Watch now uses the Tropical desk identity across NHC update, track, local-impact, and shared local screens
- Dedicated desk backgrounds suppress the normal condition-responsive background overlay so event identities remain visually coherent
- Ordinary local/ZIP channels retain their configured station theme even when the Weather Story Engine identifies a hazard
- Docker image tag, API/service metadata, XMLTV generator, backup/diagnostic names, user agents, and Admin UI identify v0.3.7

### Validation

- 69 automated tests plus 31 render subtests pass
- Python compilation, Admin JavaScript syntax, and Docker Compose validation pass
- 720p and 1080p visual QA covers all six dedicated desks plus Event Summary/Tropical Update presentations


## 0.3.6

### Added

- RWN Weather Story Engine with scored classification for quiet, rain, storms, severe, heat, cold, winter, flood, wind, air quality, tropical, and wildfire/smoke situations
- Structured NOW / NEXT / LATER / CONTEXT programming blocks for each story
- Supporting-story merge so secondary meaningful hazards remain in the adaptive rundown
- Ordinary-story hold window to reduce threshold chatter without holding cleared high-impact official stories
- **The Weather Story** broadcast slide with reason, evidence, and program-block preview
- Story-aware lower-third data ribbon
- `/api/story` status endpoint and story metadata in rundown/status APIs
- Weather Story Director panel in WeatherStream Studio, including Load Automatic Story
- Active Story panel on the Broadcast Dashboard
- Story Engine Admin controls and Story Brief duration control
- Schema 24 migration and v0.3.6 regression coverage

### Changed

- Smart programming now composes an adaptive local rundown when Story Engine is enabled instead of only promoting individual forecast slides
- Published Studio sequences remain an explicit override of automatic story composition
- Existing Forecast Graphics 2.0 secondary signals are preserved as supporting story products
- Configuration slide validation now includes all v0.3.2–v0.3.6 products in one complete registry
- Docker image tag, service/API metadata, XMLTV generator metadata, backup/diagnostic names, and admin UI identify v0.3.6

### Validation

- Full Python regression suite passes
- Dedicated Story Engine classification, migration, stabilization, manual-override, composition, and rendering tests pass
- 720p and 1080p visual QA covers quiet, rain, storms, severe, heat, wind, AQI, and story-aware Current Conditions ribbon states

## 0.3.5

### Added

- Nearby NWS station discovery and latest-observation ingestion for current conditions
- Field-by-field observed/current merge with Open-Meteo fallback
- Observation metadata in local SQLite history: source, station ID, dew point, and visibility
- Deduplication of repeated station timestamps
- **Today So Far** observational summary screen
- **Past 24 Hours** temperature/pressure history screen
- **Air Quality** screen using zero-configuration Open-Meteo/CAMS modeled U.S. AQI guidance
- **Local Rivers & Streams** screen using the modern USGS Water Data OGC API
- Configurable USGS radius, gauge count, and trend window
- **Climate Context** screen with optional NOAA NCEI 1991–2020 daily normals
- Explicit local 30-day context fallback when no official climate station is configured
- Local-data Admin controls, Studio slides, previews, durations, source refresh aliases, and event-channel integration
- Schema 23 migration and v0.3.5 regression coverage

### Changed

- Current Conditions can use real NWS observations as the preferred current-condition source
- Current Conditions can display observed dew point and visibility
- History retains actual observation timestamps instead of duplicating every refresh as a new observation
- Flood channels include local USGS river gauges; Heat and Wildfire channels gain relevant local-data products
- Air-quality and river screens explicitly distinguish modeled/provisional information from official warning or flood-stage determinations

### Validation

- 57 automated tests plus 18 render subtests pass
- Python compilation, Admin JavaScript syntax, and Docker Compose YAML validation pass
- New local-data graphics validated at 1280×720 and 1920×1080

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
