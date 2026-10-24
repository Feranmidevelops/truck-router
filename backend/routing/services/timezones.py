"""Time zone at a coordinate, and reading a typed local time in it. Pure functions, offline.

The driver types the departure as local time at the origin, whatever zone their own device is
in. tzfpy looks the zone up from bundled polygons (no network, no key).

DST change nights, same rule as JavaScript's Date:
- A time that happens twice (01:30 on the first Sunday of November in the US) is the first one.
- A time that doesn't exist (02:30 on the second Sunday of March) moves forward by the jump,
  so 02:30 becomes 03:30.
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import tzfpy

FALLBACK_ZONE = "UTC"


def zone_name(lat: float, lon: float) -> str:
    """IANA zone at a point, e.g. "America/Chicago". Points at sea get an "Etc/GMT+n" zone."""
    name = tzfpy.get_tz(lon, lat)  # longitude first
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        # A zone newer than the installed tz database, or no answer at all
        return FALLBACK_ZONE
    return name


def to_zone(when: datetime, zone: str) -> datetime:
    """The instant `when` with `zone`'s UTC offset at that instant, e.g. 2026-10-26T08:00:00-05:00.

    A naive `when` is read as local time in `zone`. The result carries a fixed offset, not the
    zone: adding a timedelta to a ZoneInfo datetime adds wall-clock time (an hour off across a
    DST change), and a time in a repeated hour never compares equal to one in another zone.
    """
    tz = ZoneInfo(zone)
    if when.utcoffset() is None:
        # fold=0 is the first of a repeated hour. In a skipped hour it takes the offset from
        # before the jump, so the round trip through UTC lands the same distance past the jump.
        when = when.replace(tzinfo=tz, fold=0)
    local = when.astimezone(timezone.utc).astimezone(tz)
    return local.replace(tzinfo=timezone(local.utcoffset()))
