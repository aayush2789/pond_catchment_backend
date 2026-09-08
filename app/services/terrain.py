from dataclasses import dataclass
from typing import Optional, Tuple
import numpy as np
from pyproj import Transformer
from scipy.interpolate import griddata
from fastapi import HTTPException, status

from app.schemas.catchment import (
    GeographicExtent,
    NormalizedContourDataset,
    ProjectedBounds,
    SlopeMetadata,
    TerrainMetadata,
)


@dataclass
class TerrainModel:
    elevation_grid: np.ndarray
    crs: str
    grid_resolution_meters: float
    bounds: Tuple[float, float, float, float]
    geographic_extent: GeographicExtent
    min_elevation: float
    max_elevation: float
    slope_grid: Optional[np.ndarray] = None

    @property
    def rows(self) -> int:
        return int(self.elevation_grid.shape[0])

    @property
    def cols(self) -> int:
        return int(self.elevation_grid.shape[1])

    def to_metadata(self) -> TerrainMetadata:
        min_x, max_x, min_y, max_y = self.bounds
        slope_meta = None
        if self.slope_grid is not None:
            slope_meta = SlopeMetadata(
                min_slope_degrees=round(float(self.slope_grid.min()), 2),
                max_slope_degrees=round(float(self.slope_grid.max()), 2),
                mean_slope_degrees=round(float(self.slope_grid.mean()), 2),
            )

        return TerrainMetadata(
            crs=self.crs,
            grid_resolution_meters=self.grid_resolution_meters,
            rows=self.rows,
            cols=self.cols,
            min_elevation=float(self.min_elevation),
            max_elevation=float(self.max_elevation),
            projected_bounds=ProjectedBounds(
                min_x=min_x,
                max_x=max_x,
                min_y=min_y,
                max_y=max_y,
            ),
            geographic_extent=self.geographic_extent,
            slope=slope_meta,
        )


class TerrainService:
    @staticmethod
    def _compute_utm_epsg(lon: float, lat: float) -> int:
        zone = int((lon + 180) / 6) + 1
        return 32600 + zone if lat >= 0 else 32700 + zone

    @staticmethod
    def calculate_slope(elevation_grid: np.ndarray, resolution_meters: float) -> np.ndarray:
        dy, dx = np.gradient(elevation_grid, resolution_meters, resolution_meters)
        slope_rad = np.arctan(np.sqrt(dx**2 + dy**2))
        return np.degrees(slope_rad)

    @classmethod
    def reconstruct_terrain(
        cls,
        dataset: NormalizedContourDataset,
        resolution_meters: float = 10.0,
    ) -> TerrainModel:
        if not dataset.contours or len(dataset.contours) < 2:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Insufficient contour information: at least two contour lines are required.",
            )

        unique_elevations = {c.elevation for c in dataset.contours}
        if len(unique_elevations) < 2:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Insufficient contour information: terrain modeling requires at least two distinct contour elevations.",
            )

        pts_list = []
        vals_list = []
        for contour in dataset.contours:
            if not contour.coordinates or len(contour.coordinates) < 2:
                continue
            for lon, lat in contour.coordinates:
                pts_list.append((lon, lat))
                vals_list.append(contour.elevation)

        if len(pts_list) < 3:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Insufficient spatial sample points to reconstruct terrain surface.",
            )

        center_lon = (dataset.extent.min_longitude + dataset.extent.max_longitude) / 2.0
        center_lat = (dataset.extent.min_latitude + dataset.extent.max_latitude) / 2.0
        epsg_code = cls._compute_utm_epsg(center_lon, center_lat)
        crs_str = f"EPSG:{epsg_code}"

        transformer = Transformer.from_crs("EPSG:4326", crs_str, always_xy=True)
        pts_arr = np.array(pts_list, dtype=np.float64)
        vals_arr = np.array(vals_list, dtype=np.float64)

        xs, ys = transformer.transform(pts_arr[:, 0], pts_arr[:, 1])
        proj_pts = np.column_stack([xs, ys])

        min_x, max_x = float(xs.min()), float(xs.max())
        min_y, max_y = float(ys.min()), float(ys.max())

        if max_x <= min_x or max_y <= min_y:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Contour geometries do not span a valid 2D area.",
            )

        x_grid = np.arange(min_x, max_x + resolution_meters, resolution_meters)
        y_grid = np.arange(min_y, max_y + resolution_meters, resolution_meters)

        if len(x_grid) < 2 or len(y_grid) < 2:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Spatial extent is too small for the specified grid resolution.",
            )

        grid_x, grid_y = np.meshgrid(x_grid, y_grid)

        dem_linear = griddata(proj_pts, vals_arr, (grid_x, grid_y), method="linear")

        nan_mask = np.isnan(dem_linear)
        if np.any(nan_mask):
            dem_nearest = griddata(proj_pts, vals_arr, (grid_x, grid_y), method="nearest")
            dem_linear[nan_mask] = dem_nearest[nan_mask]

        if np.isnan(dem_linear).any() or np.isinf(dem_linear).any():
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Terrain surface interpolation resulted in invalid elevation values.",
            )

        slope_grid = cls.calculate_slope(dem_linear, resolution_meters)

        return TerrainModel(
            elevation_grid=dem_linear,
            crs=crs_str,
            grid_resolution_meters=resolution_meters,
            bounds=(min_x, max_x, min_y, max_y),
            geographic_extent=dataset.extent,
            min_elevation=float(dem_linear.min()),
            max_elevation=float(dem_linear.max()),
            slope_grid=slope_grid,
        )
