"""Automatic DEM acquisition service.

Acquires elevation data for an analysis extent (the selected land bbox expanded by a
documented buffer) from public DEM sources, without requiring the user to upload a
contour file.

Provider evaluation (documented decision):

- AWS Terrain Tiles (Mapzen/Tilezen "terrarium", s3://elevation-tiles-prod) — DEFAULT.
  Global coverage, no API key (public AWS Open Data bucket), slippy z/x/y PNG tiles
  (zoom 0-15), decode: elevation_m = R*256 + G + B/256 - 32768. Fast S3 delivery and
  stable long-term hosting make it suitable for zero-configuration deployments.
  Underlying sources include SRTM (courtesy USGS/NASA), GMTED2010, ETOPO1 and regional
  LiDAR composites (see attribution below).

- OpenTopography Global DEM API — OPTIONAL fallback (requires a free API key, rate
  limited to ~50 calls/24h for non-academic users). Used only when
  OPEN_TOPOGRAPHY_API_KEY is configured, requested as AAIGrid (plain-text ArcInfo
  ASCII Grid) so no GDAL/rasterio dependency is needed.

The provider is isolated behind the DEMProvider abstraction and can be replaced by
adding another subclass. Elevation data is cached (memory LRU + disk NPZ) with a
deterministic cache key derived from provider, dataset, geographic extent and
resolution, so identical areas are never re-downloaded.
"""

import hashlib
import io
import json
import math
from collections import OrderedDict
from pathlib import Path
from typing import List, Optional, Tuple

import httpx
import numpy as np
from PIL import Image
from pyproj import Transformer
from scipy.ndimage import distance_transform_edt
from fastapi import HTTPException, status

from app.core.config import settings
from app.schemas.catchment import GeographicExtent
from app.services.cache import CacheLayer
from app.services.terrain import DEMData, DEMSourceInfo, TerrainService

# --- Documented constants -------------------------------------------------------------

TERRARIUM_OFFSET = 32768.0  # terrarium PNG encoding offset (meters)
TERRARIUM_NODATA_MAX = -32000.0  # values at/below this are voids (ocean is 0.0)

WEB_MERCATOR_LAT_LIMIT_DEG = 85.0511  # Web Mercator latitude bounds
WEB_MERCATOR_HALF_WORLD_M = 20037508.342789244

# Ground resolution (m/pixel) of zoom-0 Web Mercator tiles at the equator.
ZOOM0_METERS_PER_PIXEL = 156543.03392

# Required attribution for tile sources used by the AWS Terrain Tiles dataset
# (https://github.com/tilezen/joerd/blob/master/docs/attribution.md).
AWS_TERRAIN_ATTRIBUTION = (
    "Terrain tiles: Mapzen/AWS Open Data. Global SRTM data courtesy of the "
    "U.S. Geological Survey; GMTED2010 courtesy of USGS; ETOPO1 courtesy of NOAA; "
    "regional sources per Mapzen attribution requirements."
)

OPEN_TOPOGRAPHY_ATTRIBUTION = (
    "DEM via the OpenTopography API (https://opentopography.org). "
    "SRTM data courtesy of the U.S. Geological Survey."
)

OPEN_TOPOGRAPHY_GLOBALDEM_URL = "https://portal.opentopography.org/API/globaldem"

# Approximate ground cell size (degrees) per OpenTopography dataset, used to pad
# request bboxes so bilinear sampling always has neighbouring cells.
OT_DATASET_CELL_DEG = {
    "SRTMGL1": 1.0 / 3600.0,
    "SRTMGL3": 3.0 / 3600.0,
    "AW3D30": 1.0 / 3600.0,
    "NASADEM": 1.0 / 3600.0,
    "Copernicus_GLO30": 1.0 / 3600.0,
    "Copernicus_GLO90": 3.0 / 3600.0,
}
OT_DEFAULT_CELL_DEG = 1.0 / 3600.0

# Elevation sanity bounds (meters) applied after acquisition.
MIN_PLAUSIBLE_ELEVATION_M = -500.0
MAX_PLAUSIBLE_ELEVATION_M = 9000.0

# Maximum fraction of void cells tolerated before the area is rejected.
MAX_NODATA_FRACTION = 0.5

_MEMORY_CACHE_MAX_ENTRIES = 4


# --- Shared bilinear sampler -----------------------------------------------------------


def bilinear_sample(grid: np.ndarray, fy: np.ndarray, fx: np.ndarray) -> np.ndarray:
    """Bilinearly sample `grid` at fractional (row, col) coordinates.

    `fy` and `fx` are 1-D arrays of N coordinates; the result is a 1-D array of N
    elevations. Out-of-range coordinates are clamped to the grid edge (nearest
    continuation).
    """
    rows, cols = grid.shape
    fy = np.clip(np.asarray(fy, dtype=np.float64), 0.0, max(0.0, rows - 1.0))
    fx = np.clip(np.asarray(fx, dtype=np.float64), 0.0, max(0.0, cols - 1.0))
    y0 = np.floor(fy).astype(np.int64)
    x0 = np.floor(fx).astype(np.int64)
    y1 = np.minimum(y0 + 1, rows - 1)
    x1 = np.minimum(x0 + 1, cols - 1)
    wy = (fy - y0)[:, None]
    wx = (fx - x0)[:, None]
    g00 = grid[y0, x0][:, None]
    g01 = grid[y0, x1][:, None]
    g10 = grid[y1, x0][:, None]
    g11 = grid[y1, x1][:, None]
    top = g00 * (1.0 - wx) + g01 * wx
    bottom = g10 * (1.0 - wx) + g11 * wx
    return (top * (1.0 - wy) + bottom * wy)[:, 0].astype(np.float32)


# --- Providers -------------------------------------------------------------------------


class DEMProvider:
    """Abstract DEM source. Implementations fetch their native raster and expose
    `sample(lon, lat)` for bilinear elevation queries in WGS84 degrees."""

    name: str = "abstract"
    dataset: str = "abstract"
    attribution: str = ""

    def sample(self, lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
        raise NotImplementedError


class AWSTerrainTilesProvider(DEMProvider):
    """Mapzen/Tilezen terrarium tiles from the public AWS Open Data bucket."""

    name = "aws_terrain_tiles"
    dataset = "terrarium"
    attribution = AWS_TERRAIN_ATTRIBUTION

    def __init__(self, timeout_s: int, max_tiles: int):
        self.timeout_s = timeout_s
        self.max_tiles = max_tiles
        self.zoom_used: Optional[int] = None

    def _select_zoom(self, target_resolution_m: float, lat_deg: float) -> int:
        mpp_at_lat = ZOOM0_METERS_PER_PIXEL * math.cos(math.radians(lat_deg))
        for zoom in range(0, 16):
            if mpp_at_lat / (2**zoom) <= target_resolution_m:
                return zoom
        return 15

    def _tile_range(
        self, extent: GeographicExtent, zoom: int
    ) -> Tuple[int, int, int, int]:
        n = 2**zoom
        x0 = int(math.floor((extent.min_longitude + 180.0) / 360.0 * n))
        x1 = int(math.floor((extent.max_longitude + 180.0) / 360.0 * n))
        lat_min = max(-WEB_MERCATOR_LAT_LIMIT_DEG, extent.min_latitude)
        lat_max = min(WEB_MERCATOR_LAT_LIMIT_DEG, extent.max_latitude)
        y0 = int(
            math.floor(
                (1.0 - math.asinh(math.tan(math.radians(lat_max))) / math.pi) / 2.0 * n
            )
        )
        y1 = int(
            math.floor(
                (1.0 - math.asinh(math.tan(math.radians(lat_min))) / math.pi) / 2.0 * n
            )
        )
        return max(0, x0), min(n - 1, x1), max(0, y0), min(n - 1, y1)

    def _fetch_mosaic(
        self, extent: GeographicExtent, zoom: int
    ) -> Tuple[np.ndarray, int, int]:
        tx0, tx1, ty0, ty1 = self._tile_range(extent, zoom)
        n_tiles_x = tx1 - tx0 + 1
        n_tiles_y = ty1 - ty0 + 1
        if n_tiles_x * n_tiles_y > self.max_tiles:
            raise ValueError("tile_limit")

        mosaic = np.full((n_tiles_y * 256, n_tiles_x * 256), TERRARIUM_NODATA_MAX, dtype=np.float32)
        base_url = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium"
        with httpx.Client(timeout=httpx.Timeout(connect=15.0, read=float(self.timeout_s), write=15.0, pool=15.0)) as client:
            for ty in range(ty0, ty1 + 1):
                for tx in range(tx0, tx1 + 1):
                    url = f"{base_url}/{zoom}/{tx}/{ty}.png"
                    response = None
                    last_exc = None
                    for attempt in range(3):
                        try:
                            response = client.get(url)
                            response.raise_for_status()
                            break
                        except httpx.HTTPError as exc:
                            last_exc = exc
                            import time
                            time.sleep(1.0)
                    if response is None:
                        raise HTTPException(
                            status_code=status.HTTP_502_BAD_GATEWAY,
                            detail=f"DEM tile request failed ({self.name}, {url}): {last_exc}",
                        )
                    png_bytes = response.content
                    if len(png_bytes) > 10 * 1024 * 1024:
                        raise HTTPException(
                            status_code=status.HTTP_502_BAD_GATEWAY,
                            detail=f"DEM tile exceeds the maximum accepted size ({url}).",
                        )
                    try:
                        img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
                    except Exception as exc:
                        raise HTTPException(
                            status_code=status.HTTP_502_BAD_GATEWAY,
                            detail=f"DEM tile is not a valid PNG ({url}): {exc}",
                        )
                    arr = np.asarray(img, dtype=np.float32)
                    elev = (
                        arr[:, :, 0] * 256.0
                        + arr[:, :, 1]
                        + arr[:, :, 2] / 256.0
                        - TERRARIUM_OFFSET
                    )
                    row = ty - ty0
                    col = tx - tx0
                    mosaic[row * 256 : (row + 1) * 256, col * 256 : (col + 1) * 256] = elev
        return mosaic, tx0, ty0

    def sample(self, extent: GeographicExtent, lon: np.ndarray, lat: np.ndarray, target_resolution_m: float) -> np.ndarray:
        if (
            lat.min() < -WEB_MERCATOR_LAT_LIMIT_DEG
            or lat.max() > WEB_MERCATOR_LAT_LIMIT_DEG
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Selected area lies outside Web Mercator coverage (polar regions are not supported by this DEM source).",
            )

        lat_center = (extent.min_latitude + extent.max_latitude) / 2.0
        zoom = self._select_zoom(target_resolution_m, lat_center)
        while zoom > 0:
            try:
                mosaic, tx0, ty0 = self._fetch_mosaic(extent, zoom)
                break
            except ValueError:
                zoom -= 1  # coarsen until the tile count fits the configured limit
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Analysis extent requires too many DEM tiles; select a smaller land area.",
            )
        self.zoom_used = zoom

        n = 256.0 * (2**zoom)
        lon = np.asarray(lon, dtype=np.float64)
        lat = np.asarray(lat, dtype=np.float64)
        px = (lon + 180.0) / 360.0 * n - tx0 * 256.0
        py = (
            1.0 - np.arcsinh(np.tan(np.radians(lat))) / math.pi
        ) / 2.0 * n - ty0 * 256.0
        return bilinear_sample(mosaic, py, px)


class OpenTopographyProvider(DEMProvider):
    """OpenTopography Global DEM API (optional; requires a free API key)."""

    name = "opentopography"
    dataset = "SRTMGL1"
    attribution = OPEN_TOPOGRAPHY_ATTRIBUTION

    def __init__(self, api_key: str, dataset: str, timeout_s: int, max_response_mb: int):
        if not api_key:
            raise HTTPException(
                status_code=status.HTTP_501_NOT_IMPLEMENTED,
                detail=(
                    "OpenTopography DEM provider is configured but no API key is set. "
                    "Set OPEN_TOPOGRAPHY_API_KEY or switch DEM_PROVIDER to aws_terrain_tiles."
                ),
            )
        self.api_key = api_key
        self.dataset = dataset
        self.timeout_s = timeout_s
        self.max_response_mb = max_response_mb
        self._grid: Optional[np.ndarray] = None
        self._header: dict = {}

    def _pad(self) -> float:
        return 3.0 * OT_DATASET_CELL_DEG.get(self.dataset, OT_DEFAULT_CELL_DEG)

    def _fetch(self, extent: GeographicExtent) -> None:
        pad = self._pad()
        params = {
            "demtype": self.dataset,
            "south": str(extent.min_latitude - pad),
            "north": str(extent.max_latitude + pad),
            "west": str(extent.min_longitude - pad),
            "east": str(extent.max_longitude + pad),
            "outputFormat": "AAIGrid",
            "API_Key": self.api_key,
        }
        try:
            with httpx.Client(timeout=httpx.Timeout(10.0, read=float(self.timeout_s))) as client:
                with client.stream("GET", OPEN_TOPOGRAPHY_GLOBALDEM_URL, params=params) as response:
                    if response.status_code != 200:
                        body = response.read().decode("utf-8", "replace")[:500]
                        raise HTTPException(
                            status_code=status.HTTP_502_BAD_GATEWAY,
                            detail=f"OpenTopography request failed (HTTP {response.status_code}): {body}",
                        )
                    max_bytes = self.max_response_mb * 1024 * 1024
                    buffer = io.BytesIO()
                    received = 0
                    for chunk in response.iter_bytes():
                        received += len(chunk)
                        if received > max_bytes:
                            raise HTTPException(
                                status_code=status.HTTP_502_BAD_GATEWAY,
                                detail="OpenTopography response exceeds the maximum accepted size.",
                            )
                        buffer.write(chunk)
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"OpenTopography request failed: {exc}",
            )
        self._parse(buffer.getvalue().decode("utf-8", "replace"))

    def _parse(self, text: str) -> None:
        header: dict = {}
        data_lines: List[str] = []
        known = {"ncols", "nrows", "xllcorner", "yllcorner", "cellsize", "nodata_value"}
        for line in text.splitlines():
            parts = line.split(None, 1)
            if len(parts) == 2 and parts[0].lower() in known:
                try:
                    header[parts[0].lower()] = float(parts[1])
                except ValueError:
                    break
            elif line.strip():
                data_lines.append(line)
        missing = known - set(header)
        if missing:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"OpenTopography AAIGrid response is missing header fields: {sorted(missing)}.",
            )
        try:
            grid = np.loadtxt(io.StringIO("\n".join(data_lines)), dtype=np.float32)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"OpenTopography AAIGrid response could not be parsed: {exc}",
            )
        if grid.ndim != 2 or grid.size == 0:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="OpenTopography AAIGrid response does not contain a 2D elevation grid.",
            )
        expected = (int(header["nrows"]), int(header["ncols"]))
        if grid.shape != expected:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"OpenTopography grid shape {grid.shape} does not match header {expected}.",
            )
        self._grid = grid
        self._header = header

    def sample(self, extent: GeographicExtent, lon: np.ndarray, lat: np.ndarray, target_resolution_m: float) -> np.ndarray:
        if self._grid is None:
            self._fetch(extent)
        grid = self._grid
        nodata = self._header.get("nodata_value", -9999.0)
        cellsize = float(self._header["cellsize"])
        xll = float(self._header["xllcorner"])
        yll = float(self._header["yllcorner"])
        rows = grid.shape[0]
        # AAIGrid row 0 is the northernmost row.
        fy = (yll + rows * cellsize - lat) / cellsize
        fx = (lon - xll) / cellsize
        return bilinear_sample(grid, fy, fx)


# --- Service ---------------------------------------------------------------------------


class DEMService:
    """Determines the analysis extent around the selected land and acquires the DEM."""

    _memory_cache: "OrderedDict[str, DEMData]" = OrderedDict()

    # -- analysis extent ----------------------------------------------------------------

    @staticmethod
    def compute_analysis_extent(
        land_bbox: GeographicExtent, buffer_meters: float
    ) -> GeographicExtent:
        """Expand the land bounding box by `buffer_meters` on every side.

        The selected land is the pond *construction* constraint, while the catchment
        feeding a candidate pond may extend outside it — therefore the DEM/analysis
        extent must include the surrounding terrain (documented strategy: land bbox +
        uniform metric buffer, converted to degrees at the land's center latitude).
        """
        lat_center = (land_bbox.min_latitude + land_bbox.max_latitude) / 2.0
        meters_per_deg_lat = 110_574.0
        meters_per_deg_lon = 111_320.0 * max(0.01, math.cos(math.radians(lat_center)))
        d_lat = buffer_meters / meters_per_deg_lat
        d_lon = buffer_meters / meters_per_deg_lon
        return GeographicExtent(
            min_latitude=land_bbox.min_latitude - d_lat,
            max_latitude=land_bbox.max_latitude + d_lat,
            min_longitude=land_bbox.min_longitude - d_lon,
            max_longitude=land_bbox.max_longitude + d_lon,
        )

    # -- cache --------------------------------------------------------------------------

    @staticmethod
    def _cache_key(provider_name: str, dataset: str, extent: GeographicExtent, resolution: float) -> str:
        raw = (
            f"{provider_name}|{dataset}|"
            f"{extent.min_longitude:.6f},{extent.min_latitude:.6f},"
            f"{extent.max_longitude:.6f},{extent.max_latitude:.6f}|"
            f"{resolution:.3f}"
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @classmethod
    def _load_from_disk(cls, key: str) -> Optional[DEMData]:
        cache_dir = Path(settings.DEM_CACHE_DIR)
        npz_path = cache_dir / f"{key}.npz"
        meta_path = cache_dir / f"{key}.json"
        if not npz_path.exists() or not meta_path.exists():
            return None
        try:
            with npz_path.open("rb") as f:
                data = np.load(f)
                grid = data["elevation"]
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            source = DEMSourceInfo(**meta["source"])
            extent = GeographicExtent(**meta["geographic_extent"])
            bounds = tuple(meta["bounds"])
            return DEMData(
                elevation_grid=grid,
                crs=meta["crs"],
                resolution_meters=meta["resolution_meters"],
                bounds=bounds,  # type: ignore[arg-type]
                geographic_extent=extent,
                source=source,
                nodata_cells_filled=int(meta["nodata_cells_filled"]),
                cache_hit=True,
            )
        except Exception:
            return None  # corrupted cache entries are simply re-acquired

    @classmethod
    def _store_on_disk(cls, key: str, dem: DEMData) -> None:
        cache_dir = Path(settings.DEM_CACHE_DIR)
        cache_dir.mkdir(parents=True, exist_ok=True)
        npz_path = cache_dir / f"{key}.npz"
        meta_path = cache_dir / f"{key}.json"
        try:
            with npz_path.open("wb") as f:
                np.savez_compressed(f, elevation=dem.elevation_grid.astype(np.float32))
            meta = {
                "crs": dem.crs,
                "resolution_meters": dem.resolution_meters,
                "bounds": list(dem.bounds),
                "geographic_extent": dem.geographic_extent.model_dump(),
                "source": {
                    "provider": dem.source.provider,
                    "dataset": dem.source.dataset,
                    "attribution": dem.source.attribution,
                    "zoom_level": dem.source.zoom_level,
                },
                "nodata_cells_filled": dem.nodata_cells_filled,
            }
            meta_path.write_text(json.dumps(meta), encoding="utf-8")
        except OSError:
            pass  # disk caching is best-effort; memory caching still applies

    @staticmethod
    def _serialize_dem(dem: DEMData) -> Tuple[bytes, str]:
        """Serialize a DEMData to (npz_bytes, meta_json) for the shared cache."""
        buffer = io.BytesIO()
        np.savez_compressed(buffer, elevation=dem.elevation_grid.astype(np.float32))
        meta = {
            "crs": dem.crs,
            "resolution_meters": dem.resolution_meters,
            "bounds": list(dem.bounds),
            "geographic_extent": dem.geographic_extent.model_dump(),
            "source": {
                "provider": dem.source.provider,
                "dataset": dem.source.dataset,
                "attribution": dem.source.attribution,
                "zoom_level": dem.source.zoom_level,
            },
            "nodata_cells_filled": dem.nodata_cells_filled,
        }
        return buffer.getvalue(), json.dumps(meta)

    @staticmethod
    def _deserialize_dem(grid_bytes: bytes, meta_json: str, cache_hit: bool) -> Optional[DEMData]:
        try:
            with np.load(io.BytesIO(grid_bytes)) as data:
                grid = data["elevation"]
            meta = json.loads(meta_json)
            source = DEMSourceInfo(**meta["source"])
            extent = GeographicExtent(**meta["geographic_extent"])
            return DEMData(
                elevation_grid=grid,
                crs=meta["crs"],
                resolution_meters=meta["resolution_meters"],
                bounds=tuple(meta["bounds"]),  # type: ignore[arg-type]
                geographic_extent=extent,
                source=source,
                nodata_cells_filled=int(meta["nodata_cells_filled"]),
                cache_hit=cache_hit,
            )
        except Exception:
            return None

    @classmethod
    def _cache_get(cls, key: str) -> Optional[DEMData]:
        cached = cls._memory_cache.get(key)
        if cached is not None:
            cls._memory_cache.move_to_end(key)
            return DEMData(**{**cached.__dict__, "cache_hit": True})
        # L2: shared Redis (binary NPZ grid + JSON metadata, TTL'd).
        grid_bytes = CacheLayer.get_bytes("dem", f"{key}:grid")
        meta_json = CacheLayer.get_json("dem", f"{key}:meta")
        if grid_bytes is not None and meta_json is not None:
            shared = cls._deserialize_dem(grid_bytes, json.dumps(meta_json), cache_hit=True)
            if shared is not None:
                cls._memory_cache[key] = shared
                if len(cls._memory_cache) > _MEMORY_CACHE_MAX_ENTRIES:
                    cls._memory_cache.popitem(last=False)
                return DEMData(**{**shared.__dict__, "cache_hit": True})
        disk = cls._load_from_disk(key)
        if disk is not None:
            cls._memory_cache[key] = disk
            if len(cls._memory_cache) > _MEMORY_CACHE_MAX_ENTRIES:
                cls._memory_cache.popitem(last=False)
            return DEMData(**{**disk.__dict__, "cache_hit": True})
        return None

    @classmethod
    def _cache_put(cls, key: str, dem: DEMData) -> None:
        cls._memory_cache[key] = dem
        if len(cls._memory_cache) > _MEMORY_CACHE_MAX_ENTRIES:
            cls._memory_cache.popitem(last=False)
        # Shared L2: any node can now serve this extent without a provider call.
        try:
            grid_bytes, meta_json = cls._serialize_dem(dem)
            CacheLayer.set_bytes("dem", f"{key}:grid", grid_bytes, settings.DEM_CACHE_TTL_S)
            CacheLayer.set_json(
                "dem", f"{key}:meta", json.loads(meta_json), settings.DEM_CACHE_TTL_S
            )
        except Exception:
            pass  # shared caching is best-effort
        cls._store_on_disk(key, dem)

    # -- providers ----------------------------------------------------------------------

    @staticmethod
    def _provider_chain() -> List[DEMProvider]:
        primary = settings.DEM_PROVIDER
        providers: List[DEMProvider] = []

        def _aws() -> Optional[DEMProvider]:
            return AWSTerrainTilesProvider(
                timeout_s=settings.DEM_REQUEST_TIMEOUT_S,
                max_tiles=settings.DEM_MAX_TILES,
            )

        def _ot() -> Optional[DEMProvider]:
            # OpenTopography requires a key; without one it is silently skipped so
            # the remaining provider can serve as fallback (documented behavior).
            if not settings.OPEN_TOPOGRAPHY_API_KEY:
                return None
            try:
                return OpenTopographyProvider(
                    api_key=settings.OPEN_TOPOGRAPHY_API_KEY,
                    dataset=settings.OPEN_TOPOGRAPHY_DATASET,
                    timeout_s=settings.DEM_REQUEST_TIMEOUT_S,
                    max_response_mb=settings.DEM_MAX_RESPONSE_MB,
                )
            except HTTPException:
                return None

        if primary == "aws_terrain_tiles":
            providers = [p for p in (_aws(), _ot()) if p is not None]
        elif primary == "opentopography":
            providers = [p for p in (_ot(), _aws()) if p is not None]
        else:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Unknown DEM_PROVIDER '{primary}'. Supported: aws_terrain_tiles, opentopography.",
            )
        if not providers:
            raise HTTPException(
                status_code=status.HTTP_501_NOT_IMPLEMENTED,
                detail=(
                    "No DEM provider is available. Configure OPEN_TOPOGRAPHY_API_KEY "
                    "or set DEM_PROVIDER=aws_terrain_tiles."
                ),
            )
        return providers

    # -- grid construction --------------------------------------------------------------

    @staticmethod
    def _build_utm_grid(
        extent: GeographicExtent, target_resolution_m: float
    ) -> Tuple[str, Tuple[float, float, float, float], np.ndarray, np.ndarray, float, int, int]:
        center_lon = (extent.min_longitude + extent.max_longitude) / 2.0
        center_lat = (extent.min_latitude + extent.max_latitude) / 2.0
        epsg = TerrainService._compute_utm_epsg(center_lon, center_lat)
        crs = f"EPSG:{epsg}"
        to_utm = Transformer.from_crs("EPSG:4326", crs, always_xy=True)

        corner_lons = [
            extent.min_longitude,
            extent.max_longitude,
            extent.min_longitude,
            extent.max_longitude,
            center_lon,
            center_lon,
            extent.min_longitude,
            extent.max_longitude,
        ]
        corner_lats = [
            extent.min_latitude,
            extent.min_latitude,
            extent.max_latitude,
            extent.max_latitude,
            extent.min_latitude,
            extent.max_latitude,
            center_lat,
            center_lat,
        ]
        xs, ys = to_utm.transform(corner_lons, corner_lats)
        min_x, max_x = float(min(xs)), float(max(xs))
        min_y, max_y = float(min(ys)), float(max(ys))
        if max_x <= min_x or max_y <= min_y:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Analysis extent does not span a valid 2D area.",
            )

        resolution = target_resolution_m
        max_dim_m = max(max_x - min_x, max_y - min_y)
        if max_dim_m / resolution > settings.DEM_MAX_GRID_DIM:
            resolution = round(max_dim_m / settings.DEM_MAX_GRID_DIM, 2)

        cols = int(math.ceil((max_x - min_x) / resolution))
        rows = int(math.ceil((max_y - min_y) / resolution))
        x_centers = min_x + (np.arange(cols, dtype=np.float64) + 0.5) * resolution
        y_centers = min_y + (np.arange(rows, dtype=np.float64) + 0.5) * resolution
        grid_x, grid_y = np.meshgrid(x_centers, y_centers)

        to_wgs = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
        lon_flat, lat_flat = to_wgs.transform(grid_x.ravel(), grid_y.ravel())

        bounds = (min_x, max_x, min_y, max_y)
        return crs, bounds, lon_flat, lat_flat, resolution, rows, cols

    # -- validation ---------------------------------------------------------------------

    @staticmethod
    def _validate_and_fill(grid: np.ndarray) -> Tuple[np.ndarray, int]:
        nodata_mask = ~np.isfinite(grid)
        filled = 0
        if nodata_mask.any():
            valid = ~nodata_mask
            valid_fraction = float(valid.mean())
            if valid_fraction < (1.0 - MAX_NODATA_FRACTION):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=(
                        "Insufficient elevation data for the selected area: more than "
                        f"{int(MAX_NODATA_FRACTION * 100)}% of the DEM cells are voids. "
                        "Choose a different land area or DEM provider."
                    ),
                )
            indices = distance_transform_edt(nodata_mask, return_distances=False, return_indices=True)
            grid = grid[tuple(indices)]
            filled = int(nodata_mask.sum())

        if float(grid.min()) < MIN_PLAUSIBLE_ELEVATION_M or float(grid.max()) > MAX_PLAUSIBLE_ELEVATION_M:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="DEM source returned implausible elevation values; the area may be unsupported.",
            )
        return grid.astype(np.float32), filled

    # -- public API ---------------------------------------------------------------------

    @classmethod
    def acquire_dem(
        cls,
        analysis_extent: GeographicExtent,
        target_resolution_m: Optional[float] = None,
    ) -> DEMData:
        resolution = round(target_resolution_m or settings.DEM_TARGET_RESOLUTION_M, 2)

        # Extent size guard (buffered extents can still be unreasonably large).
        lat_mid = (analysis_extent.min_latitude + analysis_extent.max_latitude) / 2.0
        extent_km_lat = (
            (analysis_extent.max_latitude - analysis_extent.min_latitude) * 111_320.0 / 1000.0
        )
        extent_km_lon = (
            (analysis_extent.max_longitude - analysis_extent.min_longitude)
            * 111_320.0
            * max(0.01, math.cos(math.radians(lat_mid)))
            / 1000.0
        )
        if max(extent_km_lat, extent_km_lon) > settings.DEM_MAX_EXTENT_KM:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Analysis extent ({max(extent_km_lat, extent_km_lon):.1f} km) exceeds the "
                    f"maximum supported extent of {settings.DEM_MAX_EXTENT_KM} km. Select a smaller land area."
                ),
            )

        providers = cls._provider_chain()
        key = cls._cache_key(providers[0].name, providers[0].dataset, analysis_extent, resolution)

        cached = cls._cache_get(key)
        if cached is not None:
            return cached

        crs, bounds, lon_flat, lat_flat, resolution, rows, cols = cls._build_utm_grid(
            analysis_extent, resolution
        )

        grid: Optional[np.ndarray] = None
        source: Optional[DEMSourceInfo] = None
        last_error: Optional[HTTPException] = None
        for provider in providers:
            try:
                elevations = provider.sample(analysis_extent, lon_flat, lat_flat, resolution)
                zoom = getattr(provider, "zoom_used", None)
                source = DEMSourceInfo(
                    provider=provider.name,
                    dataset=provider.dataset,
                    attribution=provider.attribution,
                    zoom_level=zoom,
                )
                grid, filled = cls._validate_and_fill(
                    np.asarray(elevations, dtype=np.float32).reshape(rows, cols)
                )
                break
            except HTTPException as exc:
                last_error = exc
                grid = None
                source = None
                continue
        if grid is None or source is None:
            raise last_error or HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="No DEM provider could supply elevation data for the selected area.",
            )

        dem = DEMData(
            elevation_grid=grid,
            crs=crs,
            resolution_meters=resolution,
            bounds=bounds,
            geographic_extent=analysis_extent,
            source=source,
            nodata_cells_filled=filled,
            cache_hit=False,
        )
        cls._cache_put(key, dem)
        return dem
