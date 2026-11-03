"""Philadelphia weather for the campus sky.

Open-Meteo needs no key. One process-wide cache means every player shares a sky, and a failed fetch
keeps the last good reading instead of clearing the clouds.
"""

import json
import logging
import threading
import time
import urllib.parse
import urllib.request
from collections.abc import Callable

log = logging.getLogger("locusts")

_CACHE_SECONDS = 600
_FAIL_SECONDS = 60
# WMO weather interpretation codes. https://open-meteo.com/en/docs
_RAIN = {51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82, 95, 96, 99}
_SNOW = {71, 73, 75, 77, 85, 86}
_STORM = {95, 96, 99}
_FREEZING = {56, 57, 66, 67}
_DRIZZLE = {51, 53, 55}
# How hard it looks when the hour's gauge hasn't caught up with the code yet.
_CODE_INTENSITY = {
    51: 0.15, 53: 0.35, 55: 0.55, 56: 0.2, 57: 0.5,
    61: 0.3, 63: 0.6, 65: 0.9, 66: 0.35, 67: 0.85,
    71: 0.25, 73: 0.55, 75: 0.9, 77: 0.2,
    80: 0.35, 81: 0.65, 82: 1.0, 85: 0.4, 86: 0.85,
    95: 0.75, 96: 0.85, 99: 1.0,
}

UNAVAILABLE = {
    "available": False, "stale": True, "kind": "clear", "intensity": 0.0, "storm": False,
    "cloud_cover": None, "temperature_f": None, "label": None, "sunrise": None, "sunset": None,
    "wind_kmh": None, "observed_at": None,
}


def _num(value, default=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        return default
    return number


def _hhmm(value) -> str | None:
    """'2026-11-02T06:30' -> '06:30'. Anything else is ignored, and the sky keeps the game's night."""
    if not isinstance(value, str) or len(value) < 16:
        return None
    hhmm = value[11:16]
    hour, _, minute = hhmm.partition(":")
    if hour.isdigit() and minute.isdigit() and len(hour) == 2 and len(minute) == 2:
        return hhmm
    return None


def _sun(daily: dict, key: str, day: str | None) -> str | None:
    times = daily.get(key) or []
    days = daily.get("time") or []
    if not isinstance(times, list):
        return None
    if day and isinstance(days, list):
        for i, listed in enumerate(days):
            if listed == day and i < len(times):
                return _hhmm(times[i])
    return _hhmm(times[0]) if times else None


def _label(kind: str, intensity: float, storm: bool, code: int, cover: float | None) -> str:
    if storm:
        return "Thunderstorm"
    if code in _FREEZING:
        return "Freezing rain"
    if kind == "mix":
        return "Wintry mix"
    if kind == "snow":
        return "Heavy snow" if intensity >= 0.75 else "Snow" if intensity >= 0.35 else "Flurries"
    if kind == "rain":
        if code in _DRIZZLE and intensity < 0.75:
            return "Drizzle"
        return "Heavy rain" if intensity >= 0.75 else "Rain" if intensity >= 0.3 else "Drizzle"
    if kind == "fog":
        return "Fog"
    if cover is None:
        return "Clear"
    if cover >= 0.85:
        return "Overcast"
    if cover >= 0.55:
        return "Cloudy"
    if cover >= 0.2:
        return "Partly cloudy"
    return "Clear"


def parse_forecast(payload: dict) -> dict:
    """The sky's report from an Open-Meteo forecast body. Raises ValueError when the body isn't one."""
    if not isinstance(payload, dict) or not isinstance(payload.get("current"), dict):
        raise ValueError("weather response has no current conditions")
    current = payload["current"]
    code = int(_num(current.get("weather_code"), 0))
    rain_mm = (_num(current.get("rain"), 0)) + (_num(current.get("showers"), 0))
    snow_cm = _num(current.get("snowfall"), 0)
    total = _num(current.get("precipitation"), 0)
    if rain_mm < 0.05 and snow_cm < 0.05 and total >= 0.05:
        if code in _SNOW:
            snow_cm = total
        else:
            rain_mm = total
    snowing = code in _SNOW or snow_cm >= 0.05
    raining = code in _RAIN or rain_mm >= 0.05
    if code in (45, 48) and not snowing and not raining:
        kind = "fog"
    elif snowing and raining:
        kind = "mix"
    elif snowing:
        kind = "snow"
    elif raining:
        kind = "rain"
    else:
        kind = "clear"
    storm = code in _STORM
    if storm and kind == "clear":
        kind = "rain"
    measured = 0.0
    if rain_mm > 0:
        measured = min(1.0, rain_mm / 5)
    if snow_cm > 0:
        measured = max(measured, min(1.0, snow_cm / 1.5))
    intensity = max(_CODE_INTENSITY.get(code, 0.0), measured)
    if kind in ("clear", "fog"):
        intensity = 0.0
    elif intensity <= 0:
        intensity = 0.35
    intensity = min(1.0, intensity)
    cover = _num(current.get("cloud_cover"))
    cloud_cover = None if cover is None else min(1.0, max(0.0, cover / 100))
    if kind == "fog":
        cloud_cover = 0.85 if cloud_cover is None else max(cloud_cover, 0.85)
    temp_c = _num(current.get("temperature_2m"))
    observed = current.get("time")
    day = observed[:10] if isinstance(observed, str) and len(observed) >= 10 else None
    daily = payload.get("daily") if isinstance(payload.get("daily"), dict) else {}
    wind = _num(current.get("wind_speed_10m"))
    return {
        "available": True,
        "stale": False,
        "kind": kind,
        "intensity": round(intensity, 3),
        "storm": storm,
        "cloud_cover": None if cloud_cover is None else round(cloud_cover, 3),
        "temperature_f": None if temp_c is None else round(temp_c * 9 / 5 + 32),
        "label": _label(kind, intensity, storm, code, cloud_cover),
        "sunrise": _sun(daily, "sunrise", day),
        "sunset": _sun(daily, "sunset", day),
        "wind_kmh": None if wind is None else round(wind, 1),
        "observed_at": observed if isinstance(observed, str) else None,
    }


def _fetch_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "locusts/0.1 (penn campus sky)"})
    with urllib.request.urlopen(request, timeout=8) as response:
        body = json.load(response)
    if not isinstance(body, dict):
        raise ValueError("weather response was not an object")
    return body


class Weather:
    """A cached forecast. `fetch` takes the URL and returns the decoded JSON, so tests never leave the machine."""

    def __init__(
        self,
        fetch: Callable[[str], dict] | None = None,
        clock: Callable[[], float] | None = None,
        *,
        latitude: float = 39.9522,
        longitude: float = -75.1932,
        timezone: str = "America/New_York",
        cache_seconds: float = _CACHE_SECONDS,
    ):
        self.fetch = fetch or _fetch_json
        self.clock = clock or time.time
        self.latitude = latitude
        self.longitude = longitude
        self.timezone = timezone
        self.cache_seconds = cache_seconds
        self._lock = threading.Lock()
        self._cached: dict | None = None
        self._until = 0.0
        self._good: dict | None = None

    def forecast_url(self) -> str:
        query = urllib.parse.urlencode({
            "latitude": f"{self.latitude:.4f}",
            "longitude": f"{self.longitude:.4f}",
            "current": "temperature_2m,precipitation,rain,showers,snowfall,weather_code,cloud_cover,wind_speed_10m",
            "daily": "sunrise,sunset",
            "timezone": self.timezone,
            "forecast_days": "2",
            "wind_speed_unit": "kmh",
        })
        return f"https://api.open-meteo.com/v1/forecast?{query}"

    def report(self) -> dict:
        now = self.clock()
        with self._lock:
            if self._cached is not None and now < self._until:
                return self._cached
        try:
            fresh = parse_forecast(self.fetch(self.forecast_url()))
        except (OSError, ValueError) as exc:
            log.warning("campus weather unavailable: %s", exc)
            with self._lock:
                stale = {**self._good, "stale": True} if self._good is not None else UNAVAILABLE
                self._cached = stale
                self._until = now + _FAIL_SECONDS
                return stale
        with self._lock:
            self._good = fresh
            self._cached = fresh
            self._until = now + self.cache_seconds
            return fresh
