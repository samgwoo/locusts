"""Philadelphia weather for the sky: parsed from Open-Meteo, cached, and served without a live call."""

from fastapi.testclient import TestClient

from locusts.app import create_app
from locusts.weather import Weather, parse_forecast


class FakeClock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


def forecast(**current):
    base = {
        "time": "2026-11-02T18:00", "temperature_2m": 8.4, "precipitation": 0.0, "rain": 0.0,
        "showers": 0.0, "snowfall": 0.0, "weather_code": 0, "cloud_cover": 10, "wind_speed_10m": 12.0,
    }
    base.update(current)
    return {
        "current": base,
        "daily": {
            "time": ["2026-11-02", "2026-11-03"],
            "sunrise": ["2026-11-02T06:30", "2026-11-03T06:32"],
            "sunset": ["2026-11-02T16:57", "2026-11-03T16:56"],
        },
    }


# The shape Open-Meteo actually returned for College Hall on the evening of 2026-11-02.
LIVE = {
    "current": {
        "time": "2026-11-02T20:00", "interval": 900, "temperature_2m": 12.2, "precipitation": 0.0,
        "rain": 0.0, "showers": 0.0, "snowfall": 0.0, "weather_code": 0, "cloud_cover": 0,
        "wind_speed_10m": 5.8,
    },
    "daily": {
        "time": ["2026-11-02", "2026-11-03"],
        "sunrise": ["2026-11-02T06:30", "2026-11-03T06:32"],
        "sunset": ["2026-11-02T16:57", "2026-11-03T16:56"],
    },
}


def test_a_live_clear_evening_parses():
    report = parse_forecast(LIVE)
    assert report["available"] is True and report["stale"] is False
    assert report["kind"] == "clear" and report["intensity"] == 0 and report["storm"] is False
    assert report["label"] == "Clear"
    assert report["temperature_f"] == 54
    assert report["cloud_cover"] == 0
    assert (report["sunrise"], report["sunset"]) == ("06:30", "16:57")
    assert report["wind_kmh"] == 5.8
    assert report["observed_at"] == "2026-11-02T20:00"


def test_codes_become_rain_snow_fog_and_storms():
    rain = parse_forecast(forecast(weather_code=61, rain=0.4, cloud_cover=86))
    assert rain["kind"] == "rain" and rain["label"] == "Rain" and rain["temperature_f"] == 47
    assert 0.3 <= rain["intensity"] < 0.75 and rain["cloud_cover"] == 0.86

    heavy = parse_forecast(forecast(weather_code=65, rain=8, cloud_cover=100))
    assert heavy["kind"] == "rain" and heavy["label"] == "Heavy rain" and heavy["intensity"] == 1

    drizzle = parse_forecast(forecast(weather_code=51, rain=0.1, cloud_cover=70))
    assert drizzle["label"] == "Drizzle"

    snow = parse_forecast(forecast(weather_code=75, snowfall=2.5, cloud_cover=100, temperature_2m=-2))
    assert snow["kind"] == "snow" and snow["label"] == "Heavy snow" and snow["intensity"] == 1
    assert snow["temperature_f"] == 28

    flurries = parse_forecast(forecast(weather_code=71, snowfall=0.1))
    assert flurries["kind"] == "snow" and flurries["label"] == "Flurries"

    mix = parse_forecast(forecast(weather_code=71, snowfall=0.4, rain=1.2))
    assert mix["kind"] == "mix" and mix["label"] == "Wintry mix"

    freezing = parse_forecast(forecast(weather_code=66, rain=1.0))
    assert freezing["kind"] == "rain" and freezing["label"] == "Freezing rain"

    storm = parse_forecast(forecast(weather_code=95, rain=6, cloud_cover=100))
    assert storm["kind"] == "rain" and storm["storm"] is True and storm["label"] == "Thunderstorm"

    fog = parse_forecast(forecast(weather_code=45, cloud_cover=30))
    assert fog["kind"] == "fog" and fog["label"] == "Fog" and fog["intensity"] == 0
    assert fog["cloud_cover"] >= 0.85

    cloudy = parse_forecast(forecast(weather_code=3, cloud_cover=60))
    assert cloudy["kind"] == "clear" and cloudy["label"] == "Cloudy"


def test_showers_and_a_snow_code_with_only_a_total_still_precipitate():
    showers = parse_forecast(forecast(weather_code=2, showers=3.0, cloud_cover=90))
    assert showers["kind"] == "rain" and showers["intensity"] == 0.6

    snow = parse_forecast(forecast(weather_code=73, precipitation=2.0, cloud_cover=100))
    assert snow["kind"] == "snow" and snow["intensity"] > 0


def test_a_bad_payload_is_refused():
    try:
        parse_forecast({"hourly": {}})
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_the_forecast_is_cached_and_a_failure_keeps_the_last_good_one():
    clock = FakeClock(1_000.0)
    calls = {"n": 0, "down": False}

    def fetch(url):
        calls["n"] += 1
        assert "latitude=39.9522" in url and "longitude=-75.1932" in url
        assert "timezone=America%2FNew_York" in url
        if calls["down"]:
            raise TimeoutError("timed out")
        return forecast(weather_code=63, rain=2.5, cloud_cover=95)

    weather = Weather(fetch, clock, cache_seconds=600)
    first = weather.report()
    assert first["kind"] == "rain" and first["stale"] is False
    clock.now += 500
    assert weather.report() is first or weather.report()["observed_at"] == first["observed_at"]
    assert calls["n"] == 1

    calls["down"] = True
    clock.now += 200
    stale = weather.report()
    assert stale["stale"] is True and stale["available"] is True and stale["kind"] == "rain"
    clock.now += 30
    weather.report()
    assert calls["n"] == 2  # the failure itself; the next half minute is served from the cache


def test_a_failure_with_nothing_cached_says_the_sky_is_unavailable():
    clock = FakeClock(0)

    def fetch(url):
        raise OSError("down")

    report = Weather(fetch, clock).report()
    assert report["available"] is False and report["kind"] == "clear" and report["sunrise"] is None


def test_the_weather_route_is_public(tmp_path):
    clock = FakeClock(0)

    def fetch(url):
        return LIVE

    app = create_app(str(tmp_path / "weather.db"), clock=clock, weather=Weather(fetch, clock))
    body = TestClient(app).get("/api/weather").json()
    assert body["label"] == "Clear" and body["sunset"] == "16:57" and body["temperature_f"] == 54
