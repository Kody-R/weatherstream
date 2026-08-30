# WeatherStream v0.1.8.1 — RWN Realtime Performance Patch

WeatherStream generates the **Roller Weather Network (RWN)** as local HLS/IPTV channels for Jellyfin, VLC, Tunarr, and other clients.

**v0.1.8.1 is a focused performance/reliability patch for the v0.1.8 multi-channel architecture.** It retains one Local channel per configured ZIP plus shared RWN Radar and RWN Severe channels, while substantially reducing the amount of Python/Pillow rendering and H.264 encoding work required to keep those channels running in realtime.

## Why v0.1.8.1 exists

A real-world v0.1.8 test with two ZIP channels plus Radar and Severe showed:

- 4 simultaneous HLS encoders
- roughly 327% total container CPU
- roughly 182% CPU in the Python/Uvicorn process
- only 3 two-second HLS segments produced during 20 seconds of wall-clock time
- effective production speed of about **0.30x realtime**

That caused VLC to repeatedly catch the live edge and buffer while waiting for the producer.

The main problem was not network bandwidth. v0.1.8 did too much work per channel: Python rendered full 1280x720 Pillow frames at 10 FPS, FFmpeg converted each channel to 30 FPS, previews were JPEG-encoded once per second, and the alert-audio thread repeatedly rebuilt settings/weather snapshots while feeding silence.

## New optimized defaults

v0.1.8.1 changes the default video profile to:

| Setting | v0.1.8 | v0.1.8.1 |
|---|---:|---:|
| Pillow/pipe FPS | 10 | **5** |
| Expensive content redraw FPS | 10 | **3** |
| HLS output FPS | 30 | **15** |
| x264 preset | veryfast | **superfast** |
| Video bitrate | 2500k | **2000k** |
| HLS segment length | 2 sec | **3 sec** |
| HLS playlist size | 6 | **10** |
| Approx. HLS window | 12 sec | **30 sec** |
| Preview JPEG write | 1 sec | **5 sec** |

These values are intended for a mostly-static 720p weather-information channel. Radar loops, tickers, fades, and wipes remain animated while avoiding unnecessary 30 FPS encoding load.

## Frame reuse / content throttling

The encoder pipe still receives frames at `render_fps`, but WeatherStream only performs a complete expensive Pillow render at `content_fps`.

Default behavior:

```text
Pillow render:      3 FPS
        ↓
Cached RGB frame bytes
        ↓
FFmpeg input pipe:  5 FPS
        ↓
FFmpeg output:     15 FPS
        ↓
HLS
```

Between content redraws, the already-rendered RGB bytes are reused. This avoids repeatedly rebuilding the same forecast cards, icons, graph layouts, maps, fonts, and branding simply to give FFmpeg another copy of a mostly-static frame.

## Additional CPU fixes

### Font cache

Pillow `ImageFont.truetype()` objects are now cached instead of repeatedly opening/rebuilding the same font sizes during every frame.

### Removed duplicate settings deepcopy

`ConfigStore.get()` already returns an isolated deep copy. The renderer previously deep-copied that result a second time for every frame. v0.1.8.1 removes that redundant copy.

### Alert-audio polling optimization

The live PCM alert pipe still stays continuously fed, but WeatherStream no longer performs configuration and severe-alert snapshot work roughly 20 times per second per channel.

Alert state is checked about once per second, while the audio pipe continues feeding silence/chime data independently. This preserves timely alert chimes without wasting CPU on repeated weather/config cloning.

### FFmpeg stderr draining

Each encoder now continuously drains FFmpeg stderr into a small bounded in-memory tail. This prevents a noisy FFmpeg process from eventually blocking because its stderr pipe filled.

## Realtime HLS telemetry

v0.1.8 reported `running=true` and `playlist_ready=true`, but that did not prove the stream was actually keeping up with wall-clock time.

v0.1.8.1 tracks each channel's HLS segment production rate and exposes:

- latest HLS segment
- media sequence
- playlist age
- realtime ratio
- realtime state
- configured content / pipe / output FPS
- frames rendered
- frames sent to FFmpeg
- late-frame count
- average Pillow render time

Possible states are:

```text
WARMING UP
REALTIME
DEGRADED
FALLING BEHIND
STALLED
```

The Admin page shows this next to every Local/Radar/Severe encoder.

Example:

```text
zip-71270
LOCAL • /live/zip-71270/index.m3u8
3 content FPS → 5 pipe FPS → 15 output FPS
playlist age 1.2s

ON AIR   HLS READY   REALTIME 100%
```

## Performance settings in Admin

`/admin` now includes **Realtime Video Performance** controls for:

- Pipe FPS
- Content FPS
- Output FPS
- x264 preset
- Video bitrate
- HLS segment duration
- HLS playlist size
- Preview write interval

Recommended starting profile:

```text
Pipe FPS:                 5
Content FPS:              3
Output FPS:              15
Encoder preset:   superfast
Bitrate:              2000k
HLS segment:              3 sec
HLS list:                10
Preview interval:         5 sec
```

Higher FPS/preset-quality values increase CPU use. If a very low-power server still falls behind, try `content_fps=2`, `render_fps=4`, or `encoder_preset=ultrafast` before reducing resolution.

## Multi-channel behavior retained

Every configured ZIP can still have its own complete Local channel:

```text
RWN Local - Ruston
RWN Local - Pineville
RWN Local - Monroe
...
```

plus:

```text
RWN Radar
RWN Severe Weather
```

Examples:

```text
/live/zip-71270/index.m3u8
/live/zip-71360/index.m3u8
/live/radar/index.m3u8
/live/severe/index.m3u8
```

All channels continue to be generated automatically in:

```text
/playlist.m3u
/guide.xml
```

## Previous fixes retained

v0.1.8.1 includes all v0.1.8/v0.1.7.1 fixes, including:

- WMO weather code `0` correctly displays as **Clear**
- hourly forecast uses nighttime moon/cloud icons after local sunset and before sunrise
- one Local channel per ZIP
- ZIP-local NWS forecasts, alerts, SPC outlooks, storm guidance, and weather history
- RWN Radar and RWN Severe channels
- NWS warning polygons
- Severe Weather Takeover
- alert chime audio
- Roller Weather Network branding
- smart programming and dayparts
- SQLite weather history
- Jellyfin XMLTV
- radar/map/city overlays
- clipped ticker that disappears behind the lower-left station/time bug

## Settings migration

v0.1.8.1 advances the settings schema from **9 to 10**.

Your ZIPs, themes, RWN branding, radar settings, channel lineup, weather history, music, and other configuration remain intact.

If your saved `video` settings still match the original v0.1.8 defaults, migration automatically switches them to the optimized v0.1.8.1 profile.

If you had explicitly customized a video value, WeatherStream preserves that value where possible while adding the new `content_fps`, `encoder_preset`, and `preview_interval_seconds` settings.

## Docker / CasaOS

Build locally:

```bash
docker compose down
docker compose build --no-cache
docker compose up -d
```

Watch startup:

```bash
docker logs -f weatherstream
```

Admin:

```text
http://SERVER-IP:8787/admin
```

Channel discovery:

```text
http://SERVER-IP:8787/api/channels
```

Status / realtime diagnostics:

```text
http://SERVER-IP:8787/api/status
```

Jellyfin:

```text
M3U:    http://SERVER-IP:8787/playlist.m3u
XMLTV:  http://SERVER-IP:8787/guide.xml
```

## Realtime verification test

v0.1.8.1 defaults use 3-second HLS segments. After the playlist has been running long enough to fill its 10-segment window, a 20-second test should advance the media sequence about **6–7 segments**.

Example PowerShell test:

```powershell
docker exec weatherstream sh -c "echo BEFORE; grep -E 'TARGETDURATION|MEDIA-SEQUENCE' /tmp/weatherstream/live/zip-71270/index.m3u8; sleep 20; echo AFTER; grep -E 'TARGETDURATION|MEDIA-SEQUENCE' /tmp/weatherstream/live/zip-71270/index.m3u8"
```

Healthy example:

```text
BEFORE
#EXT-X-TARGETDURATION:3
#EXT-X-MEDIA-SEQUENCE:100

AFTER
#EXT-X-TARGETDURATION:3
#EXT-X-MEDIA-SEQUENCE:106
```

Use `/api/status` for the easier ongoing view; each encoder should settle on `REALTIME` close to 100%.

## Validation performed

The release was checked for:

- Python syntax across the application
- Admin JavaScript syntax
- v0.1.8 → v0.1.8.1 schema migration
- preserved custom video settings
- FastAPI application import
- four simultaneous FFmpeg workers
- separate Local / Local / Radar / Severe HLS playlists
- 3-second HLS segment generation
- realtime telemetry
- actual 15-second four-channel production test using the real WeatherRenderer

In the four-channel smoke test, every channel advanced **5 three-second HLS segments during 15 seconds of wall-clock time**, i.e. approximately **1.00x realtime**.

This test uses synthetic weather data because the build environment does not provide reliable outbound weather API access. The HLS/FFmpeg/rendering path itself is real.
