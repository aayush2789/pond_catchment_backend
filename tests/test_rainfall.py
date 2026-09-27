import pytest
from fastapi import HTTPException, status

from app.core.config import settings
from app.services.rainfall import RainfallService


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    RainfallService._memory_cache.clear()
    monkeypatch.setattr(settings, "RAINFALL_CACHE_DIR", str(tmp_path / "rain_cache"))
    yield
    RainfallService._memory_cache.clear()


OPEN_METEO_PAYLOAD = {
    "daily": {
        "time": ["2020-01-01", "2020-01-02", "2020-06-15", "2021-01-01", "2021-07-20"],
        "precipitation_sum": [10.0, 20.0, 100.0, 5.0, 200.0],
    }
}

NASA_POWER_PAYLOAD = {
    "properties": {
        "parameter": {
            "PRECTOTCORR": {
                "JAN": 1.0,
                "FEB": 1.0,
                "MAR": 1.0,
                "APR": 2.0,
                "MAY": 2.0,
                "JUN": 5.0,
                "JUL": 8.0,
                "AUG": 7.0,
                "SEP": 5.0,
                "OCT": 2.0,
                "NOV": 1.0,
                "DEC": 1.0,
                "ANN": 3.0,
            }
        }
    }
}


def test_open_meteo_parsing():
    calls = []

    def fake_get_json(url, params):
        calls.append(url)
        assert "archive-api.open-meteo.com" in url
        return OPEN_METEO_PAYLOAD

    RainfallService._http_get_json = staticmethod(fake_get_json)
    try:
        result = RainfallService.get_rainfall(21.25, 81.29)
    finally:
        del RainfallService._http_get_json
    assert result.source == "open-meteo"
    assert result.units == "mm/year"
    # Annual totals: 2020 = 130.0, 2021 = 205.0 -> mean 167.5
    assert result.rainfall_mm == pytest.approx(167.5)
    assert result.cache_hit is False
    assert result.fetched_at
    assert result.monthly_mm is not None and len(result.monthly_mm) == 12
    assert len(calls) == 1


def test_fallback_to_nasa_power_on_primary_failure():
    def fake_get_json(url, params):
        if "open-meteo" in url:
            raise HTTPException(status_code=502, detail="primary down")
        assert "power.larc.nasa.gov" in url
        return NASA_POWER_PAYLOAD

    RainfallService._http_get_json = staticmethod(fake_get_json)
    try:
        result = RainfallService.get_rainfall(21.25, 81.29)
    finally:
        del RainfallService._http_get_json
    assert result.source == "nasa-power"
    # ANN 3.0 mm/day * 365.25
    assert result.rainfall_mm == pytest.approx(3.0 * 365.25, rel=1e-3)
    assert result.monthly_mm[6] == pytest.approx(8.0 * 31, rel=1e-3)  # JUL


def test_all_providers_failing_raises_502():
    def fake_get_json(url, params):
        raise HTTPException(status_code=502, detail="down")

    RainfallService._http_get_json = staticmethod(fake_get_json)
    try:
        with pytest.raises(HTTPException) as exc:
            RainfallService.get_rainfall(21.25, 81.29)
    finally:
        del RainfallService._http_get_json
    assert exc.value.status_code == status.HTTP_502_BAD_GATEWAY
    assert "open-meteo" in exc.value.detail and "nasa-power" in exc.value.detail


def test_identical_requests_are_cached():
    calls = []

    def fake_get_json(url, params):
        calls.append(url)
        return OPEN_METEO_PAYLOAD

    RainfallService._http_get_json = staticmethod(fake_get_json)
    try:
        first = RainfallService.get_rainfall(21.25, 81.29)
        second = RainfallService.get_rainfall(21.25, 81.29)
    finally:
        del RainfallService._http_get_json
    assert first.cache_hit is False
    assert second.cache_hit is True
    assert second.rainfall_mm == first.rainfall_mm
    assert len(calls) == 1


def test_nearby_coordinates_share_cache_entry():
    calls = []

    def fake_get_json(url, params):
        calls.append(url)
        return OPEN_METEO_PAYLOAD

    RainfallService._http_get_json = staticmethod(fake_get_json)
    try:
        RainfallService.get_rainfall(21.251, 81.291)
        RainfallService.get_rainfall(21.2549, 81.2949)
    finally:
        del RainfallService._http_get_json
    # Both round to (21.25, 81.29) -> one provider call.
    assert len(calls) == 1


def test_rejects_out_of_range_coordinates():
    with pytest.raises(HTTPException) as exc:
        RainfallService.get_rainfall(120.0, 81.29)
    assert exc.value.status_code == status.HTTP_400_BAD_REQUEST


def test_open_meteo_insufficient_data_falls_back():
    def fake_get_json(url, params):
        if "open-meteo" in url:
            return {"daily": {"time": [], "precipitation_sum": []}}
        return NASA_POWER_PAYLOAD

    RainfallService._http_get_json = staticmethod(fake_get_json)
    try:
        result = RainfallService.get_rainfall(21.25, 81.29)
    finally:
        del RainfallService._http_get_json
    assert result.source == "nasa-power"


def test_open_meteo_invalid_structure_raises_and_falls_back():
    def fake_get_json(url, params):
        if "open-meteo" in url:
            return {"unexpected": True}
        return NASA_POWER_PAYLOAD

    RainfallService._http_get_json = staticmethod(fake_get_json)
    try:
        result = RainfallService.get_rainfall(21.25, 81.29)
    finally:
        del RainfallService._http_get_json
    assert result.source == "nasa-power"
