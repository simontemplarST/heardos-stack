"""Smart default: nearest region for the operator's locale, cheapest plan that meets the minimum. Always overridable."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Optional

from .base import Plan, Region

# timezone-prefix -> preferred country codes in order (coarse but good enough for a default the user can change)
_TZ_COUNTRIES = {
    "America/Chicago": ["US", "CA"], "America/New_York": ["US", "CA"], "America/Denver": ["US", "CA"], "America/Los_Angeles": ["US", "CA"],
    "America/Phoenix": ["US"], "America/Toronto": ["CA", "US"], "America/Vancouver": ["CA", "US"], "America/Sao_Paulo": ["BR", "US"],
    "America/Mexico_City": ["MX", "US"], "Europe/London": ["GB", "NL", "DE"], "Europe/Dublin": ["IE", "GB", "NL"],
    "Europe/Berlin": ["DE", "NL", "FR"], "Europe/Paris": ["FR", "DE", "NL"], "Europe/Amsterdam": ["NL", "DE"], "Europe/Madrid": ["ES", "FR"],
    "Europe/Rome": ["IT", "DE"], "Europe/Stockholm": ["SE", "DE"], "Europe/Warsaw": ["PL", "DE"], "Asia/Tokyo": ["JP", "SG"],
    "Asia/Singapore": ["SG", "JP"], "Asia/Kolkata": ["IN", "SG"], "Australia/Sydney": ["AU", "SG"], "Australia/Melbourne": ["AU", "SG"],
}
# region-name keywords -> country, for APIs that omit a country code
_NAME_HINTS = {"dallas": "US", "chicago": "US", "new york": "US", "newark": "US", "atlanta": "US", "los angeles": "US", "seattle": "US",
               "silicon valley": "US", "miami": "US", "fremont": "US", "toronto": "CA", "london": "GB", "frankfurt": "DE", "amsterdam": "NL",
               "paris": "FR", "stockholm": "SE", "warsaw": "PL", "madrid": "ES", "milan": "IT", "tokyo": "JP", "osaka": "JP",
               "singapore": "SG", "sydney": "AU", "melbourne": "AU", "mumbai": "IN", "chennai": "IN", "sao paulo": "BR", "mexico": "MX"}


def local_timezone() -> str:
    tz = os.environ.get("TZ")
    if tz:
        return tz
    try:
        from pathlib import Path
        p = Path("/etc/localtime")
        if p.is_symlink():
            s = str(p.resolve())
            if "zoneinfo/" in s:
                return s.split("zoneinfo/", 1)[1]
    except OSError:
        pass
    return time.tzname[0] if time.tzname else "UTC"


def _country(r: Region) -> str:
    if r.country:
        return r.country.upper()
    low = r.name.lower()
    for k, c in _NAME_HINTS.items():
        if k in low:
            return c
    return ""


@dataclass
class Choice:
    region: Region
    plan: Plan
    reason: str


def smart_default(regions: list[Region], plans_for, tz: Optional[str] = None, min_vcpu: int = 1, min_ram_mb: int = 1024) -> Optional[Choice]:
    """plans_for(region_id) -> list[Plan]. Picks the nearest available region, then its cheapest eligible plan."""
    tz = tz or local_timezone()
    prefs = next((v for k, v in _TZ_COUNTRIES.items() if tz.startswith(k)), ["US"])
    avail = [r for r in regions if r.available]
    if not avail:
        return None
    ranked = sorted(avail, key=lambda r: (prefs.index(_country(r)) if _country(r) in prefs else len(prefs), r.name))
    for region in ranked:
        cands = [p for p in plans_for(region.id) if p.vcpu >= min_vcpu and p.ram_mb >= min_ram_mb and p.monthly_usd > 0]
        if cands:
            plan = min(cands, key=lambda p: (p.monthly_usd, -p.ram_mb))
            why = f"nearest region for {tz}" if _country(region) in prefs else "first region with an eligible plan"
            return Choice(region, plan, f"{why}; cheapest plan with ≥{min_vcpu} vCPU / {min_ram_mb} MB")
    return None
