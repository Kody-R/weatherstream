from __future__ import annotations

import copy
import re
from typing import Any


def slug(value: Any, fallback: str = "region") -> str:
    clean = re.sub(r"[^a-z0-9_-]+", "-", str(value or "").lower()).strip("-_")
    return (clean or fallback)[:32]


def normalized_regions(settings: dict[str, Any]) -> list[dict[str, Any]]:
    """Return configured regions, or a compatibility region for older installs."""
    locations = list(settings.get("locations") or [])
    location_ids = {str(row.get("id")) for row in locations if row.get("id")}
    cfg = settings.get("regions") if isinstance(settings.get("regions"), dict) else {}
    rows = cfg.get("items") if isinstance(cfg.get("items"), list) else []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    claimed: set[str] = set()
    for index, raw in enumerate(rows[:12]):
        if not isinstance(raw, dict) or not raw.get("enabled", True):
            continue
        rid = slug(raw.get("id") or raw.get("name"), f"region-{index + 1}")
        if rid in seen:
            continue
        members = [str(x) for x in (raw.get("location_ids") or []) if str(x) in location_ids and str(x) not in claimed]
        primary = str(raw.get("primary_location_id") or "")
        if primary not in members:
            primary = members[0] if members else ""
        if not members and primary in location_ids and primary not in claimed:
            members = [primary]
        if not members:
            continue
        seen.add(rid); claimed.update(members)
        out.append({
            "id": rid,
            "name": str(raw.get("name") or f"Region {index + 1}")[:48],
            "enabled": True,
            "location_ids": members,
            "primary_location_id": primary,
            "station_name": str(raw.get("station_name") or settings.get("station_name") or "Roller Weather Network")[:40],
            "callsign": str(raw.get("callsign") or settings.get("station_callsign") or "RWN")[:12],
            "slogan": str(raw.get("slogan") or settings.get("station_slogan") or "")[:64],
            "service_area": str(raw.get("service_area") or settings.get("service_area") or "")[:64],
            "theme": str(raw.get("theme") or settings.get("theme") or "local-90s"),
            "branding_profile": slug(raw.get("branding_profile"), "default"),
        })
    unclaimed = [str(row.get("id")) for row in locations if row.get("id") and str(row.get("id")) not in claimed]
    if not out:
        primary = str(settings.get("primary_location_id") or "")
        members = [str(row.get("id")) for row in locations if row.get("id")]
        if primary not in members:
            primary = members[0] if members else ""
        if members:
            out.append({
                "id": "default", "name": str(settings.get("service_area") or "Primary Region")[:48], "enabled": True,
                "location_ids": members, "primary_location_id": primary,
                "station_name": str(settings.get("station_name") or "Roller Weather Network")[:40],
                "callsign": str(settings.get("station_callsign") or "RWN")[:12],
                "slogan": str(settings.get("station_slogan") or "")[:64],
                "service_area": str(settings.get("service_area") or "")[:64], "theme": str(settings.get("theme") or "local-90s"),
                "branding_profile": "default",
            })
    elif unclaimed:
        out[0]["location_ids"].extend(unclaimed)
    return out


def region_for_location(settings: dict[str, Any], location_id: str | None) -> dict[str, Any] | None:
    for region in normalized_regions(settings):
        if location_id in region.get("location_ids", []):
            return region
    regions = normalized_regions(settings)
    return regions[0] if regions else None


def branding_profile(settings: dict[str, Any], profile_id: str | None) -> dict[str, Any]:
    profiles = settings.get("branding_profiles") if isinstance(settings.get("branding_profiles"), dict) else {}
    profile = profiles.get(slug(profile_id, "default"))
    return copy.deepcopy(profile) if isinstance(profile, dict) else {}


def apply_region_identity(settings: dict[str, Any], region: dict[str, Any] | None, profile_id: str | None = None) -> None:
    if region:
        settings["station_name"] = region.get("station_name") or settings.get("station_name")
        settings["station_callsign"] = region.get("callsign") or settings.get("station_callsign")
        settings["station_slogan"] = region.get("slogan") or settings.get("station_slogan")
        settings["service_area"] = region.get("service_area") or settings.get("service_area")
        settings["theme"] = region.get("theme") or settings.get("theme")
        settings["_region"] = copy.deepcopy(region)
    chosen = profile_id or (region or {}).get("branding_profile") or "default"
    profile = branding_profile(settings, chosen)
    for source, target in (("station_name", "station_name"), ("callsign", "station_callsign"), ("slogan", "station_slogan"), ("theme", "theme")):
        if profile.get(source):
            settings[target] = profile[source]
    settings["_branding_profile_id"] = slug(chosen, "default")
    settings["_branding_profile"] = profile

