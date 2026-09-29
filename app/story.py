from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any


STORY_PRIORITY = {
    "quiet": 0,
    "air_quality": 20,
    "rain": 30,
    "heat": 35,
    "cold": 35,
    "wind": 40,
    "winter": 55,
    "wildfire": 58,
    "flood": 70,
    "storms": 72,
    "tropical": 80,
    "severe": 100,
}

STORY_TITLES = {
    "quiet": "Quiet Weather",
    "rain": "Rain Approaching",
    "storms": "Storms Possible",
    "severe": "Severe Weather",
    "heat": "Heat & Humidity",
    "cold": "Cold Weather",
    "winter": "Winter Weather",
    "flood": "Flooding / Heavy Rain",
    "wind": "High Wind",
    "air_quality": "Poor Air Quality",
    "tropical": "Tropical Weather",
    "wildfire": "Fire Weather / Smoke",
}

# Ordered program blocks. The director filters unavailable/disabled products later.
STORY_SECTIONS: dict[str, dict[str, list[str]]] = {
    "quiet": {
        "now": ["current", "today_so_far"],
        "next": ["hourly", "day_ahead", "nws_forecast"],
        "later": ["seven_day"],
        "context": ["climate_context", "past_24_hours", "air_quality", "almanac"],
    },
    "rain": {
        "now": ["current", "precipitation", "radar_local"],
        "next": ["rain_accumulation", "hourly", "nws_forecast", "qpf_map"],
        "later": ["seven_day"],
        "context": ["today_so_far", "past_24_hours", "local_rivers"],
    },
    "storms": {
        "now": ["current", "spc_outlook", "storm_outlook", "radar_local"],
        "next": ["spc_map", "spc_hazards", "hazard_map", "precipitation", "wind_outlook"],
        "later": ["hourly", "nws_forecast", "seven_day"],
        "context": ["qpf_map", "today_so_far"],
    },
    "severe": {
        "now": ["alert", "hazard_map", "alert_radar", "current"],
        "next": ["spc_hazards", "spc_map", "storm_outlook", "wind_outlook", "precipitation"],
        "later": ["nws_forecast", "hourly"],
        "context": ["qpf_map", "today_so_far"],
    },
    "heat": {
        "now": ["current", "today_so_far"],
        "next": ["humidity_outlook", "temperature_trend", "hourly"],
        "later": ["seven_day", "nws_forecast"],
        "context": ["climate_context", "air_quality", "almanac"],
    },
    "cold": {
        "now": ["current", "temperature_trend"],
        "next": ["hourly", "nws_forecast"],
        "later": ["seven_day"],
        "context": ["climate_context", "today_so_far", "past_24_hours"],
    },
    "winter": {
        "now": ["alert", "current", "surface_map"],
        "next": ["temperature_trend", "nws_forecast", "radar_regional"],
        "later": ["seven_day", "hourly"],
        "context": ["today_so_far", "climate_context"],
    },
    "flood": {
        "now": ["alert", "qpf_map", "radar_local", "local_rivers"],
        "next": ["rain_accumulation", "precipitation", "today_so_far"],
        "later": ["nws_forecast", "seven_day"],
        "context": ["past_24_hours", "climate_context"],
    },
    "wind": {
        "now": ["current", "wind_outlook"],
        "next": ["hourly", "nws_forecast"],
        "later": ["seven_day"],
        "context": ["today_so_far", "past_24_hours"],
    },
    "air_quality": {
        "now": ["current", "air_quality"],
        "next": ["wind_outlook", "humidity_outlook"],
        "later": ["hourly", "seven_day"],
        "context": ["climate_context"],
    },
    "tropical": {
        "now": ["tropical_update", "tropical_track", "tropical_local"],
        "next": ["map_satellite", "radar_wide", "tropical_systems"],
        "later": ["nws_forecast", "seven_day"],
        "context": ["qpf_map", "current"],
    },
    "wildfire": {
        "now": ["alert", "current", "air_quality"],
        "next": ["wind_outlook", "humidity_outlook", "regional_map"],
        "later": ["nws_forecast", "seven_day"],
        "context": ["climate_context", "today_so_far"],
    },
}


@dataclass
class StoryCandidate:
    id: str
    score: float
    reason: str
    signals: list[str]

    def payload(self) -> dict[str, Any]:
        data = asdict(self)
        data["title"] = STORY_TITLES.get(self.id, self.id.replace("_", " ").title())
        data["priority"] = STORY_PRIORITY.get(self.id, 0)
        return data


def _floats(values: Any) -> list[float]:
    out: list[float] = []
    for value in values or []:
        try:
            if value is not None:
                out.append(float(value))
        except Exception:
            pass
    return out


def _max(values: Any, default: float = 0.0) -> float:
    vals = _floats(values)
    return max(vals) if vals else default


def _sum(values: Any) -> float:
    return sum(_floats(values))


def _first(values: Any) -> float | None:
    vals = _floats(values)
    return vals[0] if vals else None


def _alert_events(snapshot: dict[str, Any]) -> list[str]:
    return [str((row or {}).get("event") or "").lower() for row in (snapshot.get("alerts") or []) if isinstance(row, dict)]


def _has_alert(events: list[str], words: tuple[str, ...]) -> bool:
    return any(any(word in event for word in words) for event in events)


def classify_story(settings: dict[str, Any], snapshot: dict[str, Any], primary: dict[str, Any] | None) -> dict[str, Any]:
    """Classify the single strongest local weather story from already-fetched RWN data.

    This is intentionally deterministic and source-neutral. It never manufactures an
    alert or risk category; official-alert-dependent stories only activate from the
    alert feed, while model signals can activate lower-priority forecast stories.
    """
    p = primary or {}
    current = p.get("current") or {}
    hourly = p.get("hourly") or {}
    daily = p.get("daily") or {}
    aq = p.get("air_quality") or {}
    rivers = p.get("rivers") or {}
    smart = settings.get("smart_programming") or {}
    cfg = settings.get("story_engine") or {}
    events = _alert_events(snapshot)

    rain_threshold = float(smart.get("rain_threshold", 20))
    storm_threshold = float(smart.get("storm_threshold", 15))
    heat_threshold = float(smart.get("heat_threshold", 95))
    cold_threshold = float(smart.get("cold_threshold", 32))
    wind_threshold = float(smart.get("wind_threshold", 25))
    aqi_threshold = float(cfg.get("aqi_threshold", 101))

    rain_prob = _max((hourly.get("precipitation_probability") or [])[:12])
    rain_24 = _sum((hourly.get("precipitation") or [])[:24])
    gust = max(float(current.get("wind_gusts_10m") or 0), _max((hourly.get("wind_gusts_10m") or [])[:12]))
    apparent = max(float(current.get("apparent_temperature") or current.get("temperature_2m") or -999), _max((hourly.get("apparent_temperature") or [])[:12], -999))
    temp_now = float(current.get("temperature_2m") or 999)
    low_24 = min(_floats((hourly.get("temperature_2m") or [])[:24]) or [temp_now])
    daily_high = _first(daily.get("temperature_2m_max"))
    if daily_high is not None:
        apparent = max(apparent, daily_high)
    aqi = float(aq.get("aqi") or 0)

    storm_guidance = (snapshot.get("storm_guidance") or {}).get("hourly") or {}
    storm_prob = _max((storm_guidance.get("thunderstorm_probability") or [])[:12])
    spc = settings.get("_spc_outlook") or {}
    spc_rank = int(((spc.get("day1") or {}).get("rank")) or 0)
    tropical = settings.get("_tropical") or {}

    candidates: list[StoryCandidate] = [StoryCandidate("quiet", 10, "No dominant hazard signal", [])]

    severe_alert = _has_alert(events, ("tornado warning", "severe thunderstorm warning", "extreme wind warning"))
    flood_alert = _has_alert(events, ("flash flood", "flood warning", "flood watch"))
    winter_alert = _has_alert(events, ("winter storm", "ice storm", "blizzard", "snow squall", "winter weather", "freezing rain"))
    heat_alert = _has_alert(events, ("excessive heat", "heat warning", "heat advisory"))
    wind_alert = _has_alert(events, ("high wind", "wind advisory"))
    fire_alert = _has_alert(events, ("red flag", "fire weather", "wildfire", "smoke"))
    tropical_alert = _has_alert(events, ("hurricane", "tropical storm", "storm surge", "tropical cyclone"))

    if severe_alert:
        candidates.append(StoryCandidate("severe", 100, "Active severe warning", ["NWS warning active"]))
    elif spc_rank >= 4 or storm_prob >= max(35, storm_threshold * 2):
        sig = []
        if spc_rank >= 4: sig.append(f"SPC risk rank {spc_rank}")
        if storm_prob: sig.append(f"Storm probability {storm_prob:.0f}%")
        candidates.append(StoryCandidate("storms", 76 + min(12, spc_rank * 2), "Strong storm signal", sig))
    elif spc_rank >= 2 or storm_prob >= storm_threshold:
        candidates.append(StoryCandidate("storms", 60 + min(12, spc_rank * 2), "Thunderstorm risk is notable", [f"Storm probability {storm_prob:.0f}%", f"SPC risk rank {spc_rank}"]))

    if tropical_alert or tropical.get("segment_active") or tropical.get("channel_active"):
        candidates.append(StoryCandidate("tropical", 86 if tropical_alert else 78, "Tropical weather is affecting the region", ["Official tropical trigger active"]))
    if flood_alert:
        candidates.append(StoryCandidate("flood", 84, "Flood alert is active", ["NWS flood product active", f"24h model rain {rain_24:.2f} in"]))
    elif rain_24 >= float(cfg.get("heavy_rain_inches", 1.0)):
        candidates.append(StoryCandidate("rain", 55, "Heavy rainfall is forecast", [f"24h model rain {rain_24:.2f} in", f"Peak rain chance {rain_prob:.0f}%"]))
    elif rain_prob >= rain_threshold:
        candidates.append(StoryCandidate("rain", 45 + min(10, rain_prob / 10), "Rain chances are high enough to lead the forecast", [f"Peak rain chance {rain_prob:.0f}%", f"24h model rain {rain_24:.2f} in"]))

    if winter_alert:
        candidates.append(StoryCandidate("winter", 82, "Winter weather alert is active", ["NWS winter product active"]))
    elif low_24 <= cold_threshold and rain_prob >= rain_threshold:
        candidates.append(StoryCandidate("cold", 42, "Cold temperatures coincide with precipitation chances", [f"Low near {low_24:.0f}°", f"Rain/snow probability proxy {rain_prob:.0f}%"]))
    elif low_24 <= cold_threshold:
        candidates.append(StoryCandidate("cold", 38, "Cold temperatures are the main local signal", [f"Low near {low_24:.0f}°"]))

    if heat_alert:
        candidates.append(StoryCandidate("heat", 79, "Heat alert is active", [f"Peak heat signal {apparent:.0f}°"]))
    elif apparent >= heat_threshold:
        candidates.append(StoryCandidate("heat", 50 + min(20, (apparent - heat_threshold) * 1.5), "Heat is the strongest non-alert signal", [f"Peak apparent/forecast temperature {apparent:.0f}°"]))

    if wind_alert:
        candidates.append(StoryCandidate("wind", 74, "Wind alert is active", [f"Peak gust {gust:.0f} mph"]))
    elif gust >= max(wind_threshold, float(cfg.get("high_wind_gust", 35))):
        candidates.append(StoryCandidate("wind", 48 + min(18, (gust - wind_threshold) / 2), "Gusts are high enough to affect the forecast", [f"Peak gust {gust:.0f} mph"]))

    if fire_alert:
        candidates.append(StoryCandidate("wildfire", 77, "Fire-weather or smoke alert is active", ["Official fire/smoke product active", f"AQI {aqi:.0f}" if aqi else "AQI unavailable"]))
    if aqi >= aqi_threshold:
        candidates.append(StoryCandidate("air_quality", 46 + min(20, (aqi - aqi_threshold) / 5), "Air quality is degraded", [f"AQI {aqi:.0f}", str(aq.get("primary_pollutant") or "").upper()]))

    # A rising river alone does not imply flooding. It is only context unless an
    # official flood alert exists, but expose it as a signal on a rain/flood story.
    gauges = rivers.get("gauges") or []
    if flood_alert and gauges:
        flood_candidate = next((row for row in candidates if row.id == "flood"), None)
        if flood_candidate:
            flood_candidate.signals.append(f"{len(gauges)} nearby USGS gauge(s)")

    # Score wins first; fixed priority is the deterministic tie-breaker.
    winner = max(candidates, key=lambda x: (x.score, STORY_PRIORITY.get(x.id, 0)))
    payload = winner.payload()
    payload["candidates"] = sorted((x.payload() for x in candidates), key=lambda x: (-float(x["score"]), -int(x["priority"])))[:5]
    payload["metrics"] = {
        "rain_probability_12h": round(rain_prob, 1),
        "rain_24h_inches": round(rain_24, 2),
        "peak_gust_mph": round(gust, 1),
        "peak_heat_f": round(apparent, 1) if apparent > -900 else None,
        "low_24h_f": round(low_24, 1),
        "storm_probability": round(storm_prob, 1),
        "spc_rank": spc_rank,
        "aqi": round(aqi, 0) if aqi else None,
    }
    payload["sections"] = {k: list(v) for k, v in STORY_SECTIONS.get(winner.id, STORY_SECTIONS["quiet"]).items()}
    return payload


def compose_story_sequence(story: dict[str, Any], *, station_id: bool = True, story_brief: bool = True, include_context: bool = True, max_slides: int = 13) -> tuple[list[str], dict[str, list[str]]]:
    sections = {k: list(v) for k, v in (story.get("sections") or STORY_SECTIONS["quiet"]).items()}
    # Keep the dominant story in charge, but preserve useful secondary signals.
    # This avoids a windy+raining day becoming a one-dimensional wind-only show.
    support = {
        "rain": ["precipitation", "rain_accumulation"],
        "wind": ["wind_outlook"],
        "heat": ["humidity_outlook", "temperature_trend"],
        "cold": ["temperature_trend"],
        "air_quality": ["air_quality"],
        "storms": ["storm_outlook", "spc_outlook"],
        "flood": ["local_rivers", "qpf_map"],
    }
    primary_id = str(story.get("id") or "quiet")
    for candidate in story.get("candidates") or []:
        cid = str(candidate.get("id") or "")
        if cid == primary_id or float(candidate.get("score") or 0) < 40:
            continue
        for slide in support.get(cid, []):
            if slide not in sections.setdefault("next", []):
                sections["next"].append(slide)
    if not include_context:
        sections["context"] = []
    result: list[str] = []
    if station_id:
        result.append("station_id")
    if story_brief:
        result.append("story_brief")
    for key in ("now", "next", "later", "context"):
        for slide in sections.get(key) or []:
            if slide not in result:
                result.append(slide)
            if len(result) >= max(4, int(max_slides)):
                return result, sections
    return result, sections
