# WeatherStream v0.2.2 — Roller Weather Network TTS Test Release

WeatherStream generates Roller Weather Network (RWN) local-weather IPTV channels for Jellyfin, VLC, and other HLS clients. **v0.2.2 is a focused text-to-speech test release** built on the multi-channel/on-demand architecture from the previous release.

The TTS scope is intentionally narrow:

- **Local on the 8s:** one concise spoken local forecast per scheduled Local on the 8s block.
- **Severe weather:** one spoken announcement per new qualifying severe-weather takeover alert.
- **No narration** during the ordinary rotation, RWN Radar, station IDs, normal forecast slides, or other screens.

TTS is optional and **disabled by default**. Existing installations retain their prior audio behavior until it is enabled in Admin.

## TTS engine

v0.2.2 uses **Piper 1.7.0** locally inside the WeatherStream container. Piper runs on CPU; no GPU is required. The default voice is:

```text
en_US-lessac-medium
```

Two additional presets are exposed in Admin:

```text
en_US-amy-medium
en_US-ryan-medium
```

Voice models are stored persistently under:

```text
/config/tts/voices
```

Synthesized announcement PCM is cached under:

```text
/config/tts/cache
```

The first voice download requires Internet access. After the voice is installed, synthesis is local.

## How Local on the 8s narration works

Local on the 8s narration is tied to the existing scheduled-local-update engine. The default clock marks are:

```text
:08
:18
:28
:38
:48
:58
```

The scheduled update feature must be enabled for Local on the 8s narration to trigger.

When a Local channel enters one of those blocks, WeatherStream builds one concise script from the current local observation and forecast, for example:

```text
This is your Local on the 8s forecast from Roller Weather Network.
In Ruston, it is 86 degrees with partly cloudy skies.
It feels like 90, humidity is 58 percent, and winds are southwest 7 miles per hour.
For today, a high near 92, with a low near 73, with a 30 percent chance of precipitation.
```

The announcement is synthesized once and cached. WeatherStream does not continuously run TTS while the block is on screen.

Each Local channel has its own block identity, so simultaneous ZIP channels can receive location-specific narration without sharing the wrong forecast.

## Severe-weather narration

When a new alert meets the configured Severe Weather Takeover threshold, WeatherStream:

1. Plays the existing RWN attention chime if enabled.
2. Builds a short deterministic alert script from the NWS alert event, affected area, headline, and first instruction sentence.
3. Synthesizes the announcement through Piper.
4. Speaks the alert once for that alert ID on the active channel.

The dedicated RWN Severe channel can therefore auto-start for a qualifying warning and speak the warning while ordinary Local channels remain on demand.

WeatherStream does **not** use an LLM to rewrite warning text. The spoken alert is constructed from the same NWS alert data already used by the severe-weather graphics.

## Music ducking

When enabled, the FFmpeg audio pipeline uses side-chain compression so background music drops while the announcement/chime bus is active and recovers afterward.

Conceptually:

```text
Music ----------------------┐
                            ├─ side-chain duck ─┐
Piper / alert chime --------┘                   ├─ AAC output
Piper / alert chime ----------------------------┘
```

The announcement bus remains silent when there is nothing to say, so normal music playback is unchanged.

## TTS does not block HLS generation

Piper synthesis runs in a background thread. The FFmpeg auxiliary PCM pipe continues receiving silence while a new announcement is being generated. Once the cached PCM is ready, the announcement is inserted into the live audio bus.

This is important because a slow first voice download or first synthesis should not stall video production.

## Admin controls

Open:

```text
http://SERVER:8787/admin/settings
```

The new **Local on the 8s + Severe TTS** card provides:

- Enable/disable Piper TTS
- Enable/disable Local on the 8s narration
- Enable/disable severe-alert narration
- Music ducking
- Automatic voice download
- Voice selection
- Narration volume
- Voice speed
- Announcement cache size
- Download/reinstall voice button
- Browser-playable TTS test button

### Recommended first test

1. Open **Admin → Settings**.
2. In **Scheduled Local Updates**, enable scheduled updates.
3. Leave the default marks `8,18,28,38,48,58`.
4. Enable **Piper TTS**.
5. Enable **Narrate Local on the 8s** and **Narrate qualifying severe alerts**.
6. Leave voice at `en_US-lessac-medium`.
7. Click **Save TTS Settings**.
8. Click **Download / Reinstall Voice**.
9. Once the status reads `READY`, click **Play TTS Test**.
10. Tune an RWN Local channel around the next configured minute mark.

The browser test works even before the live channel is tuned and is the quickest way to validate Piper and the selected voice.

## TTS API

### Status

```http
GET /api/tts/status
```

Example fields:

```json
{
  "enabled": true,
  "provider": "piper",
  "package_available": true,
  "voice": "en_US-lessac-medium",
  "voice_installed": true,
  "local_on_8s": true,
  "severe_alerts": true,
  "duck_music": true,
  "cache_items": 3,
  "last_error": null
}
```

### Download/reinstall a voice

```http
POST /api/tts/download
Content-Type: application/json

{"voice":"en_US-lessac-medium"}
```

If `voice` is omitted or null, WeatherStream uses the voice currently saved in TTS settings.

### Generate a browser/test WAV

```http
POST /api/tts/test
Content-Type: application/json

{"text":"Roller Weather Network. This is a Local on the 8s text to speech test."}
```

The response is `audio/wav`.

## Default TTS settings

```json
{
  "tts": {
    "enabled": false,
    "provider": "piper",
    "voice": "en_US-lessac-medium",
    "auto_download_voice": true,
    "local_on_8s": true,
    "severe_alerts": true,
    "volume": 0.92,
    "speed": 1.0,
    "duck_music": true,
    "cache_items": 64
  }
}
```

## Existing on-demand channels remain intact

The previous on-demand lifecycle is retained:

```text
Nobody watching
    ↓
Local / Radar / Severe encoders IDLE
    ↓
HLS playlist requested
    ↓
Pillow + FFmpeg start for only that channel
    ↓
Viewer stops
    ↓
Idle timeout
    ↓
Encoder returns to IDLE
```

Weather, NWS, radar, SPC, map, and history refresh services remain active even while every encoder is idle.

## Performance notes

Piper does not run continuously. CPU use appears as short synthesis bursts when:

- a Local on the 8s script changes;
- a new qualifying severe alert arrives; or
- the Admin TTS test is used.

Generated audio is cached by voice, speed, announcement type, and exact text. Reusing the same script avoids another synthesis pass.

The video-performance defaults remain:

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

## Docker / CasaOS

Build locally:

```bash
docker compose down
docker compose build --no-cache
docker compose up -d
```

Or when publishing through GHCR, use the included GitHub Actions workflow and point CasaOS at your image tag.

The normal persistent mappings remain:

```yaml
volumes:
  - ./config:/config
  - ./music:/music:ro
```

No additional TTS volume is required because `/config/tts` lives under the existing persistent `/config` mount.

## Upgrade / schema

v0.2.2 advances the settings schema:

```text
12 → 13
```

Migration behavior:

- Existing channel lifecycle settings are preserved.
- Existing ZIPs, themes, music, radar, RWN branding, history, channel lineup, and per-channel overrides are preserved.
- TTS is added with defaults but remains **disabled** until explicitly enabled.
- Existing alert chime behavior is preserved.

## Files intentionally excluded from Git

The `music/` directory retains its local-only `.gitignore` behavior. TTS voices are runtime data under `/config`, not source-tree assets, so they are not committed to GitHub either.

## Troubleshooting

### TTS says VOICE NEEDED

Use **Download / Reinstall Voice** in Admin. The container needs Internet access for that initial model download.

### TTS test fails with Piper unavailable

Rebuild the image without Docker layer cache so the new `piper-tts==1.7.0` dependency is installed:

```bash
docker compose build --no-cache
docker compose up -d
```

### TTS test works but Local on the 8s does not speak

Confirm both are enabled:

```text
Scheduled Local Updates → Enable scheduled updates
Local on the 8s + Severe TTS → Narrate Local on the 8s
```

Also verify that the tuned channel is an RWN **Local** channel. RWN Radar intentionally does not receive Local on the 8s narration.

### Severe narration does not play

The alert must meet the existing Severe Weather Takeover threshold. The normal alert ticker can contain lower-severity products without triggering spoken severe narration.

## v0.2.2 validation targets

The release is designed to validate these behaviors before expanding TTS further:

- Piper installs in the Docker image on x86-64 and ARM64-compatible Linux wheels.
- Voice download persists under `/config/tts/voices`.
- Browser test WAV works from Admin.
- TTS generation never blocks the FFmpeg PCM feed.
- Local on the 8s is narrated at most once per scheduled block.
- Ordinary rotation does not trigger narration.
- Radar does not trigger Local on the 8s narration.
- Severe alerts speak at most once per alert ID per active worker.
- Existing alert chime can precede severe narration.
- Music ducks while the announcement bus is active.
- On-demand encoder lifecycle remains functional.

This is intentionally a test-focused TTS release. If the voice, timing, and ducking feel right in real use, the next release can refine script wording, voice choices, timing relative to Local on the 8s slides, and optional per-channel voice overrides without broadening narration beyond the two approved use cases.
