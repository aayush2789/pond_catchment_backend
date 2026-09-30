from typing import Any, Dict, List, Optional
from collections import OrderedDict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import httpx
import numpy as np
from fastapi import HTTPException, status

from app.core.config import settings
from app.schemas.rainfall import RainfallResult
from app.services.cache import CacheLayer

_MEMORY_CACHE_MAX_ENTRIES = 16

MONTH_DAYS = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
MONTH_NAMES = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]

OPEN_METEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
NASA_POWER_CLIMATOLOGY_URL = "https://power.larc.nasa.gov/api/temporal/climatology/point"

OPEN_METEO_ATTRIBUTION = (
    "Rainfall from the Open-Meteo Historical Weather API (ERA5 / ERA5-Land reanalysis, "
    "https://open-meteo.com). Spatial resolution ~9-11 km; suitable for planning-level estimates."
)
NASA_POWER_ATTRIBUTION = (
    "Rainfall from the NASA POWER Agroclimatology API (https://power.larc.nasa.gov). "
    "Spatial resolution ~0.5 degrees; suitable for planning-level estimates."
)


class RainfallService:
    """Historical rainfall statistics for a location, from public APIs.

    Provider evaluation (documented decision):

    - NASA POWER Agroclimatology — PRIMARY: free, no API key, global coverage,
      instant response (~300ms) with zero IP rate-limit restrictions across institutional/lab
      networks. Monthly climatology and annual rainfall for robust planning-level estimates.

    - Open-Meteo Historical Weather (archive) API — SECONDARY / FALLBACK: free, daily
      precipitation from ERA5/ERA5-Land reanalysis. Used if NASA POWER is unreachable.

    Identical requests are cached at three levels: per-process memory (L1), the
    SHARED Redis cache reachable by every API node (L2), and a per-node disk
    cache (L3, fallback when Redis is unavailable). The cache key rounds the
    coordinates to 2 decimals (~1.1 km) and is namespaced with the application
    version, so nearby points reuse the same result across all servers while
    stale results never survive a code change.
    """

    _memory_cache: "OrderedDict[str, RainfallResult]" = OrderedDict()

    # -- public API ---------------------------------------------------------------------

    @classmethod
    def get_rainfall(cls, latitude: float, longitude: float) -> RainfallResult:
        if not (-90.0 <= latitude <= 90.0) or not (-180.0 <= longitude <= 180.0):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Rainfall request coordinates are out of range.",
            )
        end_year = datetime.now(timezone.utc).year - 1  # last complete year
        start_year = end_year - settings.RAINFALL_YEARS_WINDOW + 1

        key = cls._cache_key(latitude, longitude, start_year, end_year)
        cached = cls._cache_get(key)
        if cached is not None:
            return cached

        errors: List[str] = []
        try:
            result = cls._from_nasa_power(latitude, longitude, start_year, end_year)
        except HTTPException as exc:
            errors.append(f"nasa-power: {exc.detail}")
            result = None
        if result is None:
            try:
                result = cls._from_open_meteo(latitude, longitude, start_year, end_year)
            except HTTPException as exc:
                errors.append(f"open-meteo: {exc.detail}")
                result = None
        if result is None:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=(
                    "All rainfall providers failed. "
                    + " | ".join(errors)
                ),
            )

        cls._cache_put(key, result)
        return result

    # -- providers ----------------------------------------------------------------------

    @staticmethod
    def _http_get_json(url: str, params: Dict[str, Any], connect_timeout: float = 3.5) -> Dict[str, Any]:
        last_exc = None
        for attempt in range(2):
            try:
                # Fast connect timeout (3.5s) so unreachable hosts fail over immediately
                # instead of freezing the UI for 2+ minutes.
                with httpx.Client(
                    timeout=httpx.Timeout(
                        connect=connect_timeout,
                        read=float(settings.RAINFALL_REQUEST_TIMEOUT_S),
                        write=10.0,
                        pool=10.0,
                    ),
                    transport=httpx.HTTPTransport(retries=1),
                ) as client:
                    response = client.get(url, params=params)
                    response.raise_for_status()
                    return response.json()
            except httpx.HTTPError as exc:
                last_exc = exc
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"Rainfall provider returned invalid JSON: {exc}",
                )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Rainfall request to {url} failed: {last_exc}",
        )

    @classmethod
    def _from_open_meteo(
        cls, latitude: float, longitude: float, start_year: int, end_year: int
    ) -> RainfallResult:
        data = cls._http_get_json(
            OPEN_METEO_ARCHIVE_URL,
            {
                "latitude": latitude,
                "longitude": longitude,
                "start_date": f"{start_year}-01-01",
                "end_date": f"{end_year}-12-31",
                "daily": "precipitation_sum",
                "timezone": "UTC",
            },
        )
        daily = data.get("daily") or {}
        times = daily.get("time") or []
        precip = daily.get("precipitation_sum") or []
        if not times or len(times) != len(precip):
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Open-Meteo response does not contain a usable daily precipitation series.",
            )

        annual_totals: Dict[int, float] = {y: 0.0 for y in range(start_year, end_year + 1)}
        monthly_sums = [[0.0, 0] for _ in range(12)]  # [total, valid-day count] per month
        for date_str, value in zip(times, precip):
            if value is None:
                continue
            year = int(str(date_str)[0:4])
            month = int(str(date_str)[5:7])
            if year in annual_totals:
                annual_totals[year] += float(value)
            monthly_sums[month - 1][0] += float(value)
            monthly_sums[month - 1][1] += 1

        valid_years = {y: total for y, total in annual_totals.items() if total > 0.0}
        if not valid_years:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Open-Meteo returned insufficient precipitation data for the requested period.",
            )
        annual_mean = float(np.mean(list(valid_years.values())))

        # Monthly climatology: mean monthly total across the years with data.
        years_with_data = max(1, len(valid_years))
        monthly_mm = [
            round(total / years_with_data, 1) if count > 0 else None
            for total, count in monthly_sums
        ]

        return RainfallResult(
            rainfall_mm=round(annual_mean, 1),
            units="mm/year",
            period=f"{start_year}-{end_year}",
            start_year=start_year,
            end_year=end_year,
            monthly_mm=monthly_mm,
            source="open-meteo",
            dataset="ERA5 / ERA5-Land reanalysis (historical daily precipitation)",
            attribution=OPEN_METEO_ATTRIBUTION,
            cache_hit=False,
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )

    @classmethod
    def _from_nasa_power(
        cls, latitude: float, longitude: float, start_year: int, end_year: int
    ) -> RainfallResult:
        data = cls._http_get_json(
            NASA_POWER_CLIMATOLOGY_URL,
            {
                "latitude": latitude,
                "longitude": longitude,
                "parameters": "PRECTOTCORR",
                "community": "AG",
                "format": "JSON",
            },
        )
        try:
            values = data["properties"]["parameter"]["PRECTOTCORR"]
            annual_daily_mean = float(values["ANN"])
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"NASA POWER response missing PRECTOTCORR data: {exc}",
            )
        if annual_daily_mean < 0:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="NASA POWER returned NoData for the requested location.",
            )

        annual_mean = annual_daily_mean * 365.25
        monthly_mm = []
        for idx, name in enumerate(MONTH_NAMES):
            daily = values.get(name)
            monthly_mm.append(
                round(float(daily) * MONTH_DAYS[idx], 1) if daily is not None and float(daily) >= 0 else None
            )

        return RainfallResult(
            rainfall_mm=round(annual_mean, 1),
            units="mm/year",
            period=f"climatology ({start_year}-{end_year} reference window)",
            start_year=start_year,
            end_year=end_year,
            monthly_mm=monthly_mm,
            source="nasa-power",
            dataset="POWER PRECTOTCORR agroclimatology (monthly mean daily precipitation)",
            attribution=NASA_POWER_ATTRIBUTION,
            cache_hit=False,
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )

    # -- cache --------------------------------------------------------------------------

    @staticmethod
    def _cache_key(latitude: float, longitude: float, start_year: int, end_year: int) -> str:
        raw = f"{round(latitude, 2):.2f}|{round(longitude, 2):.2f}|{start_year}|{end_year}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @classmethod
    def _memory_put(cls, key: str, result: RainfallResult) -> None:
        cls._memory_cache[key] = result
        if len(cls._memory_cache) > _MEMORY_CACHE_MAX_ENTRIES:
            cls._memory_cache.popitem(last=False)

    @classmethod
    def _load_from_disk(cls, key: str) -> Optional[RainfallResult]:
        path = Path(settings.RAINFALL_CACHE_DIR) / f"{key}.json"
        if not path.exists():
            return None
        try:
            return RainfallResult.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    @classmethod
    def _store_on_disk(cls, key: str, result: RainfallResult) -> None:
        cache_dir = Path(settings.RAINFALL_CACHE_DIR)
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            (cache_dir / f"{key}.json").write_text(result.model_dump_json(), encoding="utf-8")
        except OSError:
            pass  # disk caching is best-effort

    @classmethod
    def _cache_get(cls, key: str) -> Optional[RainfallResult]:
        # L1: per-process memory.
        cached = cls._memory_cache.get(key)
        if cached is not None:
            cls._memory_cache.move_to_end(key)
            return cached.model_copy(update={"cache_hit": True})
        # L2: shared Redis (all API nodes).
        shared = CacheLayer.get_json("rainfall", key)
        if shared is not None:
            try:
                result = RainfallResult.model_validate(shared)
                cls._memory_put(key, result)
                return result.model_copy(update={"cache_hit": True})
            except Exception:
                pass  # malformed entry: treat as a miss
        # L3: per-node disk cache.
        disk = cls._load_from_disk(key)
        if disk is not None:
            cls._memory_put(key, disk)
            return disk.model_copy(update={"cache_hit": True})
        return None

    @classmethod
    def _cache_put(cls, key: str, result: RainfallResult) -> None:
        cls._memory_put(key, result)
        CacheLayer.set_json(
            "rainfall", key, json.loads(result.model_dump_json()), settings.RAINFALL_CACHE_TTL_S
        )
        cls._store_on_disk(key, result)
