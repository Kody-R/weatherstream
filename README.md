# WeatherStream v0.1.7.1 — Roller Weather Network

WeatherStream is a self-hosted Docker application that generates a continuous retro cable-style local weather television channel. v0.1.7.1 turns the renderer into **Roller Weather Network (RWN)** and adds a broadcast-intelligence layer on top of the radar, severe-weather, mapping, and presentation systems built in earlier releases.


## v0.1.7.1 hourly night-icon patch

- Hourly forecast cards now use a crescent-moon icon for clear/mainly-clear conditions after local sunset and before local sunrise.
- Partly-cloudy nighttime cards combine the moon with the existing cloud layer.
- The day/night decision is calculated from **each forecast hour's date** and that date's Open-Meteo sunrise/sunset values, so hours after midnight are handled correctly.
- Daytime hourly cards continue to use the sun icon.
- No settings migration is required; the settings schema remains version 8.
- The v0.1.6.1 WMO weather-code-0 fix remains included.

> **Important v0.1.6.1 fix carried forward:** WMO weather code `0` is correctly interpreted as **Clear**. Earlier code used a falsey-value fallback that could turn a valid clear-sky code into `Weather Unavailable`. v0.1.7.1 preserves the corrected `None` check.

## Headline features in v0.1.7.1

### 1. Intelligent programming / Broadcast Director

Normal programming can now adapt to current conditions rather than blindly showing every slide.

Default behavior:

- Rain Chances is suppressed when the next-hours precipitation probability is below the configured threshold.
- Storm Potential is suppressed when model thunderstorm probability is below the configured threshold.
- SPC Outlook is inserted only when the local categorical risk meets the configured threshold.
- Weather History is omitted when history recording is disabled.
- Severe Weather Takeover remains the highest-priority mode.
- Scheduled Local Weather Updates remain higher priority than ordinary daypart/smart programming.

Admin controls:

- Enable/disable Smart Programming
- Rain threshold
- Storm threshold
- Heat-focus threshold
- Cold-focus threshold

### 2. Daypart programming

RWN can use different slide rotations during:

- Morning
- Daytime
- Evening
- Overnight

The start hour and exact sequence for every daypart are configurable from Admin.

Default examples:

**Morning:** Current → Condition Focus → Today → Temperature Trend → Hourly → Local Radar → Regional Map → 7-Day → History → Almanac

**Daytime:** Current → Condition Focus → Today → Temperature Trend → Hourly → Rain Chances → Storm Potential → SPC Outlook → Local Radar → NWS Forecast → Regional Map → 7-Day → History

**Evening/Overnight:** emphasizes tonight/tomorrow, radar, extended forecast, history, and almanac information.

### 3. 24-hour local weather history

WeatherStream now keeps a small SQLite history database at:

```text
/config/weatherstream.db
```

On each successful weather refresh for the primary ZIP, it records:

- temperature
- apparent temperature
- humidity
- surface pressure
- wind speed
- wind gust
- precipitation
- cloud cover
- WMO weather code

The new `weather_history` slide displays:

- 24-hour temperature graph
- local high / low
- maximum wind gust
- accumulated sampled precipitation
- pressure trend
- number of stored observations

History retention is configurable from Admin. Default: 90 days.

### 4. Smart condition-focus screen

The new `condition_focus` slide automatically emphasizes the most relevant local metric:

- Heat Index during hot/humid weather
- Wind Chill during cold/windy weather
- Wind Gusts during windy conditions
- Dew Point during very humid weather
- Apparent / Feels Like temperature otherwise

The slide also shows humidity, dew point, and wind context.

### 5. NOAA/NWS Storm Prediction Center outlooks

The new `spc_outlook` slide queries the official NOAA/NWS SPC outlook map service for the primary location and shows the highest categorical risk intersecting that point for:

- Day 1
- Day 2
- Day 3

Categories include:

```text
NONE
TSTM
MRGL
SLGT
ENH
MDT
HIGH
```

WeatherStream treats this as forecast guidance. It never replaces NWS watches/warnings, and actual qualifying NWS alerts still trigger Severe Weather Takeover.

SPC refresh and the minimum risk required for Smart Programming are configurable.

### 6. Roller Weather Network branding

Fresh installations now default to:

```text
Network:  Roller Weather Network
Bug:      RWN
Slogan:   Local Weather • Radar • Alerts • 24 Hours
```

A built-in transparent RWN logo is included in the image. If `/config/branding/logo.png` does not exist and **Use built-in RWN logo** is enabled, the bundled logo is used automatically.

You can still upload your own PNG/JPEG/WebP logo from Admin. Removing a custom logo returns the station to the built-in RWN logo.

The Docker/software project remains named **WeatherStream** while the on-air network identity is **Roller Weather Network**.

### 7. Jellyfin XMLTV guide

v0.1.7.1 adds:

```text
http://SERVER:8787/guide.xml
```

The M3U channel and XMLTV channel both use:

```text
rwn.local
```

Endpoints:

```text
M3U:    http://SERVER:8787/playlist.m3u
XMLTV:  http://SERVER:8787/guide.xml
HLS:    http://SERVER:8787/live/weather.m3u8
```

The XMLTV schedule reflects the RWN daypart identity and configured scheduled Local Weather Update windows. If Severe Weather Takeover is currently active when the guide is generated, the current affected guide block is labeled **RWN Severe Weather Coverage**.

For Jellyfin, add `/playlist.m3u` as the M3U tuner and `/guide.xml` as the XMLTV guide source.

### 8. Data-source diagnostics

Admin now has a Data Source Diagnostics section. `/api/status` exposes health information for the main upstream/local systems, including:

- Open-Meteo weather
- NWS forecast
- NWS alerts
- model storm guidance
- RainViewer radar
- GeoNames city database
- SPC outlooks
- local weather-history database

The status page reports recent success/error information so a failed upstream source can be distinguished from a renderer or Docker problem.

### 9. Admin preview/test console

You can preview individual graphics without changing the live IPTV stream:

- Station ID
- Current Conditions
- Condition Focus
- 24-Hour History
- SPC Outlook
- Local Radar

You can also render synthetic:

- TEST Warning
- TEST Warning Radar

Synthetic alert previews are visibly marked:

```text
TEST MODE • NOT A REAL WEATHER ALERT
```

They never modify the live NWS alert feed or trigger the live warning chime.

### 10. Smarter ticker priorities

The lower ticker still uses the clipped surface introduced in v0.1.4, so it cannot overlap the bottom-left station/time bug.

Normal ticker content can now prioritize useful context such as:

- current conditions
- meaningful rain chances
- SPC categorical risk
- today's high/low
- pressure trend from local history
- NWS alerts

Severe-alert ticker takeover remains highest priority.

### 11. Cache management

WeatherStream now includes a cache manager for `/config/cache`.

Default retention:

```text
48 hours
```

Automatic cleanup runs at startup and approximately every six hours. Admin also includes **Clean Cache Now**.

The SQLite weather-history database is not part of the transient cache and uses its own retention setting.

---

## Existing features retained

v0.1.7.1 preserves the prior WeatherStream feature set, including:

- multiple U.S. ZIP codes
- selectable primary ZIP
- Open-Meteo current/hourly/7-day weather
- NWS textual forecasts
- NWS active alerts
- Severe Weather Takeover
- NWS warning polygons on radar
- alert ticker takeover
- locally generated alert chime mixed into HLS audio
- Local / Regional / Wide radar
- RainViewer animated radar loops
- OpenStreetMap basemap
- U.S. Census TIGERweb county/state boundaries
- radar city labels and GeoNames nearby-city labels
- radar range rings
- optional classic radar sweep
- Regional Weather Map
- Temperature Trend
- Rain Chances
- Storm Potential model guidance
- 7-Day Forecast
- Regional Conditions
- Almanac
- five presentation themes
- analog/CRT effects
- animated transitions
- scheduled Local Weather Updates
- station branding/logo upload
- background music
- H.264/AAC HLS output
- Jellyfin-ready M3U
- CasaOS / Docker Compose deployment
- GHCR amd64 + arm64 GitHub Actions workflow

---

## Install / local Docker build

```bash
unzip weatherstream-v0.1.7.1.zip
cd weatherstream-v0.1.7.1

docker compose up -d --build
```

For a clean dependency/image rebuild:

```bash
docker compose down
docker compose build --no-cache
docker compose up -d
```

Open:

```text
http://SERVER-IP:8787/admin
```

Watch logs:

```bash
docker logs -f weatherstream
```

Status API:

```text
http://SERVER-IP:8787/api/status
```

---

## CasaOS

The included Compose file uses:

```yaml
ports:
  - "8787:8787"

volumes:
  - ./config:/config
  - ./music:/music:ro
```

For a CasaOS server you can map persistent host paths such as:

```text
/DATA/AppData/weatherstream  -> /config
/DATA/Media/WeatherMusic     -> /music
```

The `/config` volume contains settings, cached map/radar data, custom branding, and `weatherstream.db`.

---

## GHCR

The included workflow builds both:

```text
linux/amd64
linux/arm64
```

and publishes on `main` and version tags.

Example image after publishing:

```text
ghcr.io/YOUR_GITHUB_USERNAME/weatherstream:v0.1.7.1
```

Typical release flow:

```bash
git add .
git commit -m "WeatherStream v0.1.7.1 Roller Weather Network"
git push

git tag v0.1.7.1
git push origin v0.1.7.1
```

---

## Upgrade from v0.1.6.1

Your existing `/config` volume can be reused.

v0.1.7.1 migrates settings schema:

```text
7 -> 8
```

Migration behavior:

- existing ZIPs, radar, themes, music, severe-weather settings, and custom branding are preserved
- if the old untouched defaults still say `WeatherStream Local`, the on-air default is upgraded to `Roller Weather Network / RWN`
- a genuinely custom station name/callsign/slogan is preserved
- `condition_focus`, `spc_outlook`, and `weather_history` are inserted into an existing normal sequence when appropriate
- Smart Programming, dayparts, SPC, history, and cache settings receive safe defaults
- the v0.1.6.1 clear-sky/WMO-code-0 correction is preserved

---

## Important data-source notes

**NWS alerts are authoritative for warning/takeover behavior.** SPC categorical outlooks and model Storm Potential are forecast guidance and are labeled accordingly.

The build does not fabricate observed lightning strikes. Storm Potential continues to represent model guidance rather than live lightning observations.

Radar/map/data providers may occasionally be unavailable. WeatherStream's renderer is designed to use cached material where possible and expose source failures through `/api/status` rather than crashing the HLS stream.

---

## v0.1.7.1 validation performed

The release was validated with:

- Python compilation of all application modules
- Admin JavaScript syntax validation
- v0.1.6.1 schema-7 to v0.1.7.1 schema-8 migration
- preservation of custom station branding during migration
- WMO code `0` => `Clear`
- Broadcast Director / daypart sequence generation
- smart rain/storm/SPC filtering logic
- SQLite observation recording and history summaries
- Condition Focus rendering at 1280×720
- 24-Hour History rendering at 1280×720
- SPC Outlook rendering at 1280×720 using synthetic service data
- built-in transparent RWN logo rendering
- XMLTV generation with matching `rwn.local` channel ID
- FastAPI startup
- `/health`
- `/api/status`
- `/guide.xml`
- `/admin`
- built-in `/branding/logo.png`
- real FFmpeg H.264/AAC startup
- rolling HLS `.m3u8` and `.ts` segment generation

The build environment does not provide reliable outbound DNS, so live Open-Meteo/NWS/RainViewer/OSM/TIGERweb/GeoNames/SPC requests must receive their final end-to-end network validation on the deployed Docker host.
