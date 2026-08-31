# WeatherStream v0.3.0 — RWN Network Studio

WeatherStream is a self-hosted Docker broadcast engine that turns official weather data, radar, forecasts, alerts, satellite imagery, station branding, and background audio into continuously discoverable HLS/IPTV channels for Jellyfin, VLC, and other players. **v0.3.0 expands one Roller Weather Network installation into a multi-region weather network with automatic event channels, Map Engine 2.0, WeatherStream Studio, per-source refresh controls, and reusable channel branding profiles.**

## What's new in v0.3.0

- **Automatic Weather Event Channels:** standing Tornado, Flood, Winter Weather, Wildfire, and Extreme Heat channels are published per region. Matching official NWS alerts wake only the relevant on-demand encoder; configurable cooldowns prevent start/stop thrashing.
- **Broadcast Map Engine 2.0:** combines region-centered RainViewer radar, OSM/Census geography, NWS warning polygons, automatic city labels, tropical tracks, and cached official NOAA GOES-19 GeoColor and GLM lightning products. Each layer can be enabled independently.
- **WeatherStream Studio:** build and drag-reorder broadcast rundowns, preview slides, create code-native station bumpers, and publish channel/daypart/region schedules without restarting the container.
- **Multi-Region Network Mode:** assign locations to up to 12 service regions, each with its own primary location, callsign, identity, theme, branding profile, radar loop, and complete local/specialty/event lineup.
- **Per-source refresh controls:** refresh weather/alerts, radar, GeoNames, SPC, NHC tropical, satellite, or lightning independently from the Broadcast Dashboard.
- **Channel Branding Profiles:** reusable station name, callsign, slogan, theme, accent color, logo, and music-subfolder identity, selectable at region or individual-channel level.
- Settings schema 18 migration preserves a v0.2.6 installation as a compatible default region and inserts the new radar-map products into the Radar rundown.
- 34 automated regression tests cover earlier features plus schema migration, multi-region lineup generation, event matching, and Studio schedules.

Official imagery references:

- [NOAA/NESDIS GOES-19 GeoColor](https://www.star.nesdis.noaa.gov/GOES/fulldisk_band.php?band=GEOCOLOR&dim=1&length=36&sat=G19)
- [NOAA/NESDIS GOES-19 GLM lightning imagery](https://www.star.nesdis.noaa.gov/GOES/conus_band.php?band=EXTENT3&dim=1&length=72&refresh=true&sat=G19)

## Previous release: v0.2.6

## What's new in v0.2.6

- Seasonal Tropical Weather Update inserted into RWN Local programming from June 1 through November 30
- Permanent `RWN Tropics Watch` M3U/XMLTV channel that remains discoverable while its on-demand encoder is idle
- Automatic Tropics Watch activation for:
  - an active or forecast storm point entering the Gulf operational region
  - a storm center/forecast point entering the configured radius around the primary ZIP
  - an NHC Gulf disturbance reaching the configured development threshold
  - a local NWS tropical storm, hurricane, storm-surge, hurricane-local-statement, or extreme-wind alert
- Six-hour default cooldown after the final trigger clears, avoiding channel start/stop thrashing
- Official NHC current-storm JSON, Atlantic Tropical Weather Outlook RSS, and forecast-track KMZ ingestion
- Four new slides: `tropical_update`, `tropical_systems`, `tropical_track`, and `tropical_local`
- Revision-aware tropical snapshots and last-known-good fallback outside the video-frame hot path
- Dynamic Tropics Watch XMLTV titles containing active storm names
- Tropical lifecycle events available to the v0.2.5 webhook system
- Admin controls for the segment, channel, auto-start, refresh interval, radius, development threshold, cooldown, previews, and lineup sequence
- Settings schema 17 migration

### v0.2.5.1 reliability patch carried forward

The v0.2.5.1 Intel hardware-encoding fixes are fully integrated into v0.2.6:

- The Docker image installs `intel-media-va-driver` and `vainfo`.
- Every mapped `/dev/dri/renderD*` node is enumerated instead of assuming `renderD128`.
- QSV and VAAPI devices are marked READY only after a real one-frame H.264 encode succeeds.
- Linux QSV uses the selected DRM node as `child_device` with VAAPI as its child-device type.
- Global and per-channel render-node selection is available in the Dashboard and Channel Lineup.
- Failed hardware startup immediately retries the same channel with `libx264` while preserving the original on-demand tune request.
- Channel telemetry reports requested and active encoder/device details, fallback reason, and fallback count.

### Official data and refresh behavior

WeatherStream reads only official NHC/NOAA hosts. Forecast-track URLs discovered in NHC JSON are restricted to an HTTPS host allow-list, redirects are revalidated, and KMZ/KML extraction is size bounded. The default refresh interval is ten minutes; failed refreshes retain the last successful tropical snapshot and surface the error in Data Source Diagnostics.

Official feed references:

- [NHC and CPHC RSS feeds](https://www.nhc.noaa.gov/mobile/rss.html)
- [NHC GIS data via RSS](https://www.nhc.noaa.gov/gis/rss.php)
- [NHC tropical-cyclone status JSON reference](https://www.nhc.noaa.gov/productexamples/NHC_Tropical_Cyclone_Status_JSON_File_Reference.pdf)

### Channel lifecycle

Tropics Watch stays in the IPTV lineup year-round so Jellyfin does not repeatedly lose and rediscover it. With on-demand encoding, it normally consumes no FFmpeg encoder. An official Gulf/local trigger starts the worker and refreshes its activity lease until the trigger and configured cooldown both clear. Manual playback can still start the channel at any time.

The forecast track describes possible center positions, not the full extent of hazardous weather. WeatherStream displays official data and local-alert language, but never generates evacuation or protective-action instructions.

## v0.2.5 guided setup and operator QoL

### What's new in v0.2.5

- First-run setup wizard for the initial ZIP, station identity, theme, and streaming mode
- Automatic redirect to setup until the first location exists
- Searchable settings with `HOT`, `REFRESH`, and `RESTART` impact labels
- Copy buttons for M3U, XMLTV, and individual HLS channel URLs
- Accelerated full-rundown preview using the real configured slide sequence without changing live channels
- Structured events for severe alerts, upstream-source failures/recovery, stream lifecycle, settings, refreshes, and container lifecycle
- Optional asynchronous webhook delivery with a bounded queue and per-event cooldown
- SSRF-resistant webhook validation, private/LAN target opt-in, redirect blocking, and redacted diagnostics
- Notification status and rate-limited test-delivery controls in Admin
- Settings schema 15 migration for notification defaults

### First run

Open WeatherStream in a browser after starting the container. With no configured ZIP codes, `/` redirects to `/setup`. The wizard resolves the ZIP, creates the primary local channel, and starts background refreshes. Existing installations skip the wizard.

### Webhook notifications

Webhook delivery is disabled by default. Configure it under **Admin → Settings → Webhook Notifications**. Public HTTP(S) targets are allowed after DNS validation; private, loopback, link-local, multicast, reserved, and unspecified addresses are rejected unless the trusted-LAN option is explicitly enabled. WeatherStream does not follow webhook redirects.

Events use this envelope:

```json
{
  "product": "WeatherStream",
  "version": "0.3.0",
  "event": {
    "time": 1788144000.0,
    "kind": "source",
    "message": "nws_alerts source recovered",
    "source": "nws_alerts",
    "state": "recovered"
  }
}
```

## v0.2.4 revision-aware performance

### What's new in v0.2.4

- Revision numbers for settings, weather, and SPC state
- Per-channel render contexts cached by configuration/weather/SPC revision
- One weather snapshot copy per revision instead of one full copy per content frame
- Read-only radar/map image sharing between refresh and render pipelines
- Target-size radar frame and basemap caches shared by active channels
- Quantized deterministic transition-mask cache
- Resized station-logo cache
- Persistent pooled HTTP clients for weather, alerts, guidance, SPC, radar, maps, and GeoNames
- Bounded concurrent per-ZIP refreshes through `WEATHERSTREAM_REFRESH_WORKERS`
- One SQLite transaction for all observations in a weather refresh
- WAL mode, explicit SQLite connection closing, cached history queries/summaries, incremental row counts, and daily cleanup
- Render-context cache and refresh-worker visibility on the Broadcast Dashboard
- Performance-foundation tests for revision invalidation, batch inserts, query caches, and daily cleanup

### Refresh concurrency

The default pool processes up to four ZIP locations concurrently while preserving per-source timeouts and stale-data fallbacks:

```yaml
environment:
  - WEATHERSTREAM_REFRESH_WORKERS=4
```

Valid values are 1–8. Use 1–2 on very small systems or constrained networks. Four is recommended for most installations; increasing beyond that is mainly useful with a larger ZIP lineup.

### Render-context behavior

WeatherStream now copies and prepares channel state only when an input revision changes:

```text
settings/weather/SPC revision unchanged
        ↓
reuse prepared per-channel context
        ↓
draw the current frame

revision changes
        ↓
copy new published state once
        ↓
rebuild affected channel contexts
```

Runtime-only changes such as adaptive degradation and Local on the 8s phase overrides use small targeted dictionary copies, leaving the cached context read-only.

### SQLite behavior

Observations for all ZIPs are written with `executemany` in one transaction. History graphs and ticker summaries are cached until the observation revision changes. Retention cleanup runs at most once per UTC day rather than after every weather refresh.

## v0.2.3 secure operations foundation

## What's new in v0.2.3

- Optional HTTP Basic protection for Admin and sensitive API routes
- Per-client rate limits for TTS, voice download, backup restore, database vacuum, and mass encoder restart
- ZIP member allow-listing plus expanded-size and compression-ratio limits before backup restore
- Pillow decompression protection and SQLite integrity checking during restore
- Constant-time `/health`, `/health/live`, and `/health/ready` endpoints
- Prometheus-compatible request/process metrics at `/metrics`
- Bounded recent-operations feed at `/api/events` and on the Broadcast Dashboard
- One serialized lifecycle lock per channel worker to coalesce concurrent on-demand startup
- Fixed Piper worker pool and bounded narration queue
- Bounded thread joins and Compose init/signal-forwarding support during shutdown
- Initial standard-library test suite for authentication, rate limits, backup safety, and observability
- One-second caching for the expensive full `/api/status` assembly

### Enable Admin authentication

Authentication is opt-in for upgrade compatibility. Set a strong password before exposing port 8787 outside a trusted machine or LAN:

```yaml
environment:
  - WEATHERSTREAM_ADMIN_USER=admin
  - WEATHERSTREAM_ADMIN_PASSWORD=replace-with-a-long-random-password
```

When a password is configured, browsers present an HTTP Basic login prompt for `/admin`. Sensitive API routes use the same credentials. HLS, M3U, XMLTV, branding, previews, lightweight health checks, and public channel status remain available to IPTV clients without credentials.

Do not enable `WEATHERSTREAM_TRUST_PROXY_HEADERS` unless WeatherStream is behind a trusted reverse proxy that overwrites `X-Forwarded-For`.

### Health and metrics

```text
GET /health         compatibility liveness response
GET /health/live    constant-time process liveness
GET /health/ready   startup/shutdown readiness
GET /metrics        Prometheus/OpenMetrics text format
GET /api/events     authenticated recent operations feed
```

Docker and Compose use `/health/live`, avoiding filesystem scans, SQLite queries, channel reconciliation, and TTS-cache scans every 30 seconds.

### Piper worker controls

```yaml
environment:
  - WEATHERSTREAM_TTS_WORKERS=1
  - WEATHERSTREAM_TTS_QUEUE_SIZE=24
```

One worker is recommended on typical home servers. The application accepts at most two workers and drops stale narration requests when the bounded queue is full instead of starting unbounded Piper/FFmpeg processes.

## v0.2.2.1 Local on the 8s programming model

The central change is that **Local on the 8s is now its own programming block**. It interrupts the ordinary RWN rotation at the configured clock marks, runs a fixed sequence of local-weather phases, narrates each phase independently, and then returns to normal programming.

TTS remains intentionally limited to:

- **Local on the 8s phases** on RWN Local channels.
- **Qualifying severe-weather takeover alerts**.

There is still **no TTS during the ordinary rotation, RWN Radar channel, station IDs, or unrelated weather screens**.

## Local on the 8s programming model

Default trigger marks remain:

```text
:08
:18
:28
:38
:48
:58
```

At a configured mark, an active RWN Local channel changes from normal programming to the dedicated block:

```text
NORMAL RWN PROGRAMMING
          │
          │ :08 / :18 / :28 / :38 / :48 / :58
          ▼
┌───────────────────────────────┐
│       LOCAL ON THE 8s         │
└───────────────────────────────┘
          │
          ├─ Intro
          │
          ├─ Current Conditions
          │
          ├─ Today's Forecast
          │
          ├─ Hourly Forecast
          │
          ├─ Local Radar
          │
          └─ 7-Day Forecast
          │
          ▼
NORMAL RWN PROGRAMMING
```

The default configurable weather-phase sequence is:

```text
current,today,hourly,radar_local,seven_day
```

The Local on the 8s intro is added automatically and is not part of the editable phase list.

When the final phase completes, WeatherStream exits the block and the Broadcast Director returns to the normal RWN rotation.

## Screen-accurate phase narration

v0.2.2 generated one broad narration script for the entire Local on the 8s period. v0.2.2.1 replaces that behavior with **one script per visible phase**.

The renderer and narration generator use the same weather snapshot. The narration layer is deliberately limited to the values shown by that phase.

### Intro

A Local on the 8s intro contains only the station/location presentation and a short start message. TTS reads that presentation rather than giving an early forecast.

### Current Conditions

If the screen shows:

```text
LOCAL CONDITIONS
RUSTON, LOUISIANA

84°F
PARTLY CLOUDY

FEELS LIKE 87°
HUMIDITY 61%
WIND S 7 MPH
GUSTS 12 MPH
PRESSURE 29.90"
```

TTS can say:

```text
Current conditions for Ruston, Louisiana. 84 degrees. Partly cloudy.
Feels like 87 degrees. Humidity 61 percent. Wind south 7 miles per hour.
Gusts 12 miles per hour. Pressure 29.90 inches of mercury.
```

It does **not** add tonight's low, tomorrow's forecast, radar interpretation, or other information that is absent from this screen.

### Today's Forecast

Narration is limited to the forecast fields displayed by the Today phase, such as condition, high, low, and precipitation chance.

### Hourly Forecast

The Hourly phase narrates the same six forecast cards displayed on screen. It uses the displayed time, temperature, precipitation chance, and wind value for those cards. It does not narrate hidden hourly fields.

### Local Radar

The radar phase does not invent meteorological analysis from the image. If the phase only displays the Local Radar title and location, TTS is limited to that information while the music/radar presentation continues.

### 7-Day Forecast

Narration follows the seven visible forecast cards: displayed day, high, low, and precipitation chance. It does not read a separate long-form forecast that is not on screen.

### Not narrated

Local on the 8s narration intentionally ignores:

```text
Scrolling ticker
Clock
RWN logo
Decorative text
Map labels
Radar city labels
Other off-screen forecast fields
```

## Narration-driven phase timing

Each phase has a normal minimum display time. When phase TTS is enabled, WeatherStream coordinates the screen with the announcement:

```text
Phase becomes visible
        ↓
Short visual lead-in
        ↓
Piper narration begins
        ↓
Screen remains on the same phase
        ↓
Narration completes
        ↓
Short tail/pause
        ↓
Next Local on the 8s phase
```

Default timing controls include:

```text
Visual lead before TTS:       0.8 sec
Tail after narration:         1.0 sec
TTS preparation wait:        15 sec
Maximum phase safety limit:  75 sec
```

The phase stays visible for at least its normal slide duration. A slow or unavailable TTS engine cannot hold the channel indefinitely: the safety timing allows the block to continue without freezing HLS video.

Piper synthesis remains asynchronous and cached, so synthesis work does not run in the video-rendering critical path.

## Programming priority and severe-weather preemption

The priority order is now explicit:

```text
1. SEVERE WEATHER TAKEOVER
2. LOCAL ON THE 8s
3. NORMAL PROGRAMMING
```

If a qualifying severe alert arrives while Local on the 8s is running:

```text
LOCAL ON THE 8s
Hourly Forecast
        ↓
NEW QUALIFYING SEVERE ALERT
        ↓
Local on the 8s is aborted
        ↓
RWN attention chime / severe TTS
        ↓
SEVERE WEATHER TAKEOVER
```

The interrupted Local on the 8s block is marked handled and **does not restart after the severe takeover clears**. Normal programming resumes, and the Local channel waits for the next configured Local on the 8s mark.

## TTS scope remains narrow

Piper is still used only for:

```text
RWN Local channel
  └─ Local on the 8s phases

Qualifying severe-weather alert
  └─ Severe alert announcement
```

Examples of things that remain silent other than normal music/audio:

```text
Ordinary Current Conditions slide
Ordinary Hourly slide
Ordinary 7-Day slide
RWN Radar channel
Station IDs
SPC rotation
Weather history
Almanac screens
```

## Piper TTS

WeatherStream uses:

```text
piper-tts==1.7.0
```

Default voice:

```text
en_US-lessac-medium
```

Additional Admin presets:

```text
en_US-amy-medium
en_US-ryan-medium
```

Piper runs locally on CPU and does not require a GPU. Voice files persist under:

```text
/config/tts/voices
```

Generated announcements are cached under:

```text
/config/tts/cache
```

The initial voice download requires Internet access. Once installed, normal synthesis is local.

## Music ducking

The existing announcement bus remains in place. When narration or a severe alert chime is active, FFmpeg ducks the background music and restores it after the announcement.

```text
Normal phase
Music      ███████████████

Narration
Music      ███
TTS        ███████████████

Narration ends
Music      ███████████████
```

No announcement means the auxiliary announcement bus is silent and ordinary music playback is unchanged.

## Local on the 8s controls

Open:

```text
http://SERVER:8787/admin/settings
```

The **Local on the 8s Programming Block** section controls:

- Enable/disable the programming block
- Trigger minute marks
- Trigger grace window
- Local weather-phase sequence
- Intro enablement
- Visual lead before narration
- Tail after narration
- TTS preparation wait
- Maximum phase safety duration

Supported configurable phases are:

```text
current
today
hourly
radar_local
seven_day
```

The dedicated intro is inserted automatically when enabled.

## Manual Local on the 8s test

A complete block can be started without waiting for the next `:08/:18/...` mark.

In **Admin → Settings → Local on the 8s Programming Block**, click:

```text
Run Local on the 8s Test
```

WeatherStream selects the primary RWN Local channel (or the first available Local channel), activates it if it is using on-demand encoding, and starts the complete programming block.

The corresponding API is:

```http
POST /api/channels/{channel-key}/local8-test
```

Example channel key:

```text
zip-71270
```

The test is rejected while a Severe Weather Takeover is active because severe programming has higher priority.

## Channel status / dashboard

Each channel now exposes Local on the 8s state through `/api/channels` and the Broadcast Dashboard.

Example status structure:

```json
{
  "local_on_8s": {
    "active": true,
    "block_id": "20260830-2248",
    "phase": "hourly",
    "phase_index": 4,
    "phase_count": 6,
    "phase_elapsed_seconds": 7.4,
    "narration_queued": true,
    "last_completed_or_handled_block": null,
    "last_abort_reason": null
  }
}
```

While active, the Dashboard can display a channel as approximately:

```text
LOCAL 8s • HOURLY
phase 4 / 6 • narrating
```

## On-demand channel interaction

The on-demand encoder architecture is retained. An active Local on the 8s block counts as active programming, so the idle supervisor will **not shut down a Local channel in the middle of the block**.

```text
Viewer tunes RWN Local
        ↓
On-demand encoder starts
        ↓
Local on the 8s begins
        ↓
Block remains protected from idle shutdown
        ↓
Block completes
        ↓
Normal on-demand idle behavior resumes
```

Weather, NWS, SPC, radar/cache, maps, and history refresh services continue independently of the video encoder lifecycle.

## TTS controls

The existing **Local on the 8s + Severe TTS** settings remain available:

- Enable/disable Piper TTS
- Narrate Local on the 8s
- Narrate qualifying severe alerts
- Music ducking
- Automatic voice download
- Voice selection
- Narration volume
- Voice speed
- Announcement cache size
- Download/reinstall voice
- Browser TTS test

If TTS is disabled, Local on the 8s still runs as a dedicated visual programming block using the configured minimum slide durations.

## Recommended test procedure

1. Rebuild and start WeatherStream.
2. Open **Admin → Settings**.
3. Enable **Local on the 8s Programming Block**.
4. Leave the default marks `8,18,28,38,48,58`.
5. Enable Piper TTS and **Narrate Local on the 8s**.
6. Download/install the selected voice and confirm TTS status shows `READY`.
7. Use **Play TTS Test** to confirm Piper/audio output.
8. Tune an RWN Local channel.
9. Click **Run Local on the 8s Test** rather than waiting for a clock mark.
10. Verify that each screen remains in place while its own narration plays and that the channel returns to normal programming after the 7-Day phase.

Also verify that the scrolling ticker is never read aloud.

## TTS API

Status:

```http
GET /api/tts/status
```

Download/reinstall a voice:

```http
POST /api/tts/download
Content-Type: application/json

{"voice":"en_US-lessac-medium"}
```

Generate a browser/test WAV:

```http
POST /api/tts/test
Content-Type: application/json

{"text":"Roller Weather Network text to speech test."}
```

The response is `audio/wav`.

## Upgrade / settings schema

v0.3.0 advances the settings schema to 18 while retaining all earlier migrations:

```text
13 → 14 → 15 → 16 → 17
```

Migration behavior:

- Existing ZIP locations and channel lineup are preserved.
- Existing on-demand/always-on lifecycle settings are preserved.
- Themes, music, radar settings, RWN branding, weather history, and per-channel overrides are preserved.
- Existing TTS settings and downloaded voices under `/config` are preserved.
- TTS remains optional.
- Webhook notifications are added disabled, with no destination configured.
- Tropical monitoring and its stable channel entry are added enabled, using on-demand encoding unless the channel override selects Always On.
- Existing encoder choices are preserved and receive `encoder_device: auto`, removing the old hard-coded render-node assumption.
- An untouched v0.2.2 Local on the 8s sequence is migrated to the new phase list:

```text
current,today,hourly,radar_local,seven_day
```

The intro is handled separately by the new block state machine.

## Docker / CasaOS

Build locally:

```bash
docker compose down
docker compose build --no-cache
docker compose up -d
```

Persistent mappings remain:

```yaml
volumes:
  - ./config:/config
  - ./music:/music:ro
```

No separate TTS volume is needed because voices and cached announcements live under the persistent `/config` tree.

For Intel QSV/VAAPI, map the host DRM directory into the container:

```yaml
devices:
  - /dev/dri:/dev/dri
```

Then use **Admin → Broadcast Dashboard → Performance Manager → Re-probe GPUs**. A READY result means an actual test encode succeeded, not merely that FFmpeg lists the encoder.

## Performance profile

The v0.2.x optimized video defaults remain available:

```text
1280×720
3 content FPS
5 raw-video pipe FPS
15 output FPS
H.264 ~2000 kbps
3-second HLS segments
10-segment HLS list
CRT effects OFF by default
```

Phase-specific Piper synthesis is asynchronous and cached. Local on the 8s does not create a continuously running speech workload.

## Validation performed for v0.2.2.1

The release was checked for:

- Python compilation across the application
- Admin Settings JavaScript syntax
- Broadcast Dashboard JavaScript syntax
- Settings schema `13 → 14 → 15 → 16` migrations
- Dedicated Local on the 8s phase progression
- No immediate replay of a completed clock block
- Severe Weather Takeover preemption
- No restart of the same Local on the 8s block after severe preemption
- TTS-disabled visual-only block progression
- TTS-enabled phase waiting based on announcement duration
- Screen-specific narration generation for Current, Today, Hourly, Local Radar, and 7-Day
- Forced Local on the 8s slide rendering at 1280×720
- Actual FFmpeg/HLS smoke test while the programming state machine transitioned through phases
- On-demand lifecycle protection while Local on the 8s is active
- FastAPI application import and v0.2.2.1 health/version reporting

The build environment does not provide Docker itself, so the final Docker image build must be exercised on the target Docker/CasaOS host. Real Piper voice synthesis inside the completed image also depends on the runtime package/voice download; the integration, asynchronous announcement path, caching, and FFmpeg audio path were validated independently.

## Files intentionally excluded from Git

The `music/` directory retains its local-only `.gitignore` behavior. Piper voices are runtime files under `/config/tts`, not source assets, and are not intended to be committed to GitHub.

## Troubleshooting

### Local on the 8s does not start

Confirm **Local on the 8s Programming Block** is enabled and the tuned channel is an RWN **Local** channel. For immediate testing, use **Run Local on the 8s Test**.

### The block runs but does not speak

Confirm:

```text
Piper TTS                         Enabled
Narrate Local on the 8s          Enabled
Voice status                      READY
```

Use **Play TTS Test** first to isolate the TTS engine from the live programming state machine.

### A radar phase says very little

That is intentional. v0.2.2.1 does not infer storm movement or intensity from radar pixels. TTS is restricted to information deliberately shown by the radar phase.

### Severe weather interrupted Local on the 8s

That is expected. Severe Weather Takeover has higher priority. The interrupted block will not resume; the next scheduled Local on the 8s block starts at the next configured clock mark.
