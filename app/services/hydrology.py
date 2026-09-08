from collections import deque
import heapq
from typing import Dict, List, Optional, Tuple
import numpy as np
from pyproj import Transformer
from shapely.geometry import box, mapping
from shapely.ops import transform, unary_union
from fastapi import HTTPException, status

from app.schemas.catchment import (
    CatchmentBoundary,
    CatchmentResult,
    Coordinates,
    HydrologyMetadata,
    PondCandidateSite,
)
from app.services.terrain import TerrainModel

NEIGHBORS: List[Tuple[int, int]] = [
    (-1, 0),
    (1, 0),
    (0, -1),
    (0, 1),
    (-1, -1),
    (-1, 1),
    (1, -1),
    (1, 1),
]

DIST_FACTORS: List[float] = [
    1.0,
    1.0,
    1.0,
    1.0,
    float(np.sqrt(2)),
    float(np.sqrt(2)),
    float(np.sqrt(2)),
    float(np.sqrt(2)),
]


class HydrologyService:
    @staticmethod
    def condition_dem(dem: np.ndarray) -> np.ndarray:
        rows, cols = dem.shape
        filled = np.copy(dem)
        visited = np.zeros((rows, cols), dtype=bool)
        heap: List[Tuple[float, int, int]] = []

        for r in range(rows):
            for c in (0, cols - 1):
                heapq.heappush(heap, (float(dem[r, c]), r, c))
                visited[r, c] = True
        for c in range(cols):
            for r in (0, rows - 1):
                if not visited[r, c]:
                    heapq.heappush(heap, (float(dem[r, c]), r, c))
                    visited[r, c] = True

        while heap:
            elev, r, c = heapq.heappop(heap)
            for dr, dc in NEIGHBORS:
                nr, nc = r + dr, c + dc
                if 0 <= nr < rows and 0 <= nc < cols and not visited[nr, nc]:
                    visited[nr, nc] = True
                    if filled[nr, nc] < elev:
                        filled[nr, nc] = elev
                    heapq.heappush(heap, (float(filled[nr, nc]), nr, nc))

        return filled

    @staticmethod
    def calculate_flow_direction(filled_dem: np.ndarray, resolution_meters: float) -> np.ndarray:
        rows, cols = filled_dem.shape
        flow_dir = np.full((rows, cols), -1, dtype=np.int8)

        for r in range(1, rows - 1):
            for c in range(1, cols - 1):
                center_elev = filled_dem[r, c]
                max_slope = 0.0
                best_dir = -1
                for idx, ((dr, dc), dist_factor) in enumerate(zip(NEIGHBORS, DIST_FACTORS)):
                    drop = (center_elev - filled_dem[r + dr, c + dc]) / (dist_factor * resolution_meters)
                    if drop > max_slope:
                        max_slope = drop
                        best_dir = idx
                flow_dir[r, c] = best_dir

        return flow_dir

    @staticmethod
    def calculate_flow_accumulation(flow_direction: np.ndarray, filled_dem: np.ndarray) -> np.ndarray:
        rows, cols = flow_direction.shape
        acc = np.ones((rows, cols), dtype=np.float64)
        order = np.argsort(-filled_dem.ravel())

        for idx in order:
            r = int(idx // cols)
            c = int(idx % cols)
            d = flow_direction[r, c]
            if d >= 0:
                dr, dc = NEIGHBORS[d]
                nr, nc = r + dr, c + dc
                if 0 <= nr < rows and 0 <= nc < cols:
                    acc[nr, nc] += acc[r, c]

        return acc

    @staticmethod
    def snap_to_drainage_cell(
        cand_x: float,
        cand_y: float,
        bounds: Tuple[float, float, float, float],
        resolution: float,
        accumulation: np.ndarray,
        snap_radius_meters: float = 100.0,
    ) -> Tuple[int, int]:
        min_x, max_x, min_y, max_y = bounds
        rows, cols = accumulation.shape

        if not (min_x <= cand_x <= max_x and min_y <= cand_y <= max_y):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Candidate location falls outside the projected terrain boundary.",
            )

        cr = int(round((cand_y - min_y) / resolution))
        cc = int(round((cand_x - min_x) / resolution))
        cr = min(max(0, cr), rows - 1)
        cc = min(max(0, cc), cols - 1)

        radius_cells = max(1, int(round(snap_radius_meters / resolution)))
        r_min = max(0, cr - radius_cells)
        r_max = min(rows, cr + radius_cells + 1)
        c_min = max(0, cc - radius_cells)
        c_max = min(cols, cc + radius_cells + 1)

        best_r, best_c = cr, cc
        max_acc = -1.0
        min_dist_sq = float("inf")

        for r in range(r_min, r_max):
            for c in range(c_min, c_max):
                val = float(accumulation[r, c])
                dist_sq = (r - cr) ** 2 + (c - cc) ** 2
                if val > max_acc:
                    max_acc = val
                    best_r, best_c = r, c
                    min_dist_sq = dist_sq
                elif val == max_acc and dist_sq < min_dist_sq:
                    best_r, best_c = r, c
                    min_dist_sq = dist_sq

        return best_r, best_c

    @staticmethod
    def delineate_catchment(flow_direction: np.ndarray, outlet_r: int, outlet_c: int) -> np.ndarray:
        rows, cols = flow_direction.shape
        mask = np.zeros((rows, cols), dtype=bool)
        if not (0 <= outlet_r < rows and 0 <= outlet_c < cols):
            return mask

        mask[outlet_r, outlet_c] = True
        queue = deque([(outlet_r, outlet_c)])

        while queue:
            curr_r, curr_c = queue.popleft()
            for idx, (dr, dc) in enumerate(NEIGHBORS):
                nr, nc = curr_r + dr, curr_c + dc
                if 0 <= nr < rows and 0 <= nc < cols and not mask[nr, nc]:
                    d = flow_direction[nr, nc]
                    if d >= 0:
                        target_r = nr + NEIGHBORS[d][0]
                        target_c = nc + NEIGHBORS[d][1]
                        if target_r == curr_r and target_c == curr_c:
                            mask[nr, nc] = True
                            queue.append((nr, nc))

        return mask

    @classmethod
    def polygonize_catchment(
        cls,
        mask: np.ndarray,
        bounds: Tuple[float, float, float, float],
        resolution: float,
        src_crs: str,
    ) -> CatchmentBoundary:
        mask_rows, mask_cols = np.where(mask)
        if len(mask_rows) == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Unable to delineate catchment: no contributing cells found for outlet.",
            )

        min_x, _, min_y, _ = bounds
        boxes = [
            box(
                min_x + c * resolution,
                min_y + r * resolution,
                min_x + (c + 1) * resolution,
                min_y + (r + 1) * resolution,
            )
            for r, c in zip(mask_rows, mask_cols)
        ]

        poly_proj = unary_union(boxes)
        if poly_proj.is_empty or poly_proj.area <= 0.0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Unable to delineate catchment: resulting catchment boundary geometry is empty or invalid.",
            )

        if poly_proj.geom_type == "MultiPolygon":
            poly_proj = max(poly_proj.geoms, key=lambda g: g.area)

        transformer_to_wgs = Transformer.from_crs(src_crs, "EPSG:4326", always_xy=True)
        poly_wgs = transform(transformer_to_wgs.transform, poly_proj)

        return CatchmentBoundary(
            type="Feature",
            geometry=mapping(poly_wgs),
            properties={
                "contributing_cells": int(len(mask_rows)),
                "crs": src_crs,
            },
        )

    @classmethod
    def analyze_hydrology(
        cls,
        terrain: TerrainModel,
        candidate: PondCandidateSite,
        snap_radius_meters: float = 100.0,
    ) -> CatchmentResult:
        if (
            candidate.latitude < terrain.geographic_extent.min_latitude
            or candidate.latitude > terrain.geographic_extent.max_latitude
            or candidate.longitude < terrain.geographic_extent.min_longitude
            or candidate.longitude > terrain.geographic_extent.max_longitude
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Candidate location falls outside the analyzed terrain boundary.",
            )

        filled_dem = cls.condition_dem(terrain.elevation_grid)
        flow_dir = cls.calculate_flow_direction(filled_dem, terrain.grid_resolution_meters)
        accumulation = cls.calculate_flow_accumulation(flow_dir, filled_dem)

        to_proj = Transformer.from_crs("EPSG:4326", terrain.crs, always_xy=True)
        cand_x, cand_y = to_proj.transform(candidate.longitude, candidate.latitude)

        outlet_r, outlet_c = cls.snap_to_drainage_cell(
            cand_x,
            cand_y,
            terrain.bounds,
            terrain.grid_resolution_meters,
            accumulation,
            snap_radius_meters=snap_radius_meters,
        )

        to_wgs = Transformer.from_crs(terrain.crs, "EPSG:4326", always_xy=True)
        min_x, _, min_y, _ = terrain.bounds
        res = terrain.grid_resolution_meters
        snapped_x = min_x + outlet_c * res
        snapped_y = min_y + outlet_r * res
        snapped_lon, snapped_lat = to_wgs.transform(snapped_x, snapped_y)

        catchment_mask = cls.delineate_catchment(flow_dir, outlet_r, outlet_c)
        contributing_cells = int(np.sum(catchment_mask))
        if contributing_cells < 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Delineation yielded an empty catchment area.",
            )

        area_sq_meters = round(float(contributing_cells * (res**2)), 2)
        area_hectares = round(float(area_sq_meters / 10000.0), 4)

        boundary = cls.polygonize_catchment(catchment_mask, terrain.bounds, res, terrain.crs)

        outlet_elev = round(float(terrain.elevation_grid[outlet_r, outlet_c]), 2)
        outlet_slope = (
            round(float(terrain.slope_grid[outlet_r, outlet_c]), 2)
            if terrain.slope_grid is not None
            else candidate.slope_degrees
        )

        return CatchmentResult(
            outlet_location=Coordinates(
                latitude=candidate.latitude,
                longitude=candidate.longitude,
                elevation=candidate.elevation,
            ),
            snapped_outlet=Coordinates(
                latitude=round(float(snapped_lat), 7),
                longitude=round(float(snapped_lon), 7),
                elevation=outlet_elev,
            ),
            elevation_meters=outlet_elev,
            slope_degrees=outlet_slope,
            catchment_area_sq_meters=area_sq_meters,
            catchment_area_hectares=area_hectares,
            contributing_cells_count=contributing_cells,
            hydrology=HydrologyMetadata(
                max_flow_accumulation_cells=round(float(accumulation.max()), 1),
                outlet_flow_accumulation_cells=round(float(accumulation[outlet_r, outlet_c]), 1),
                conditioned_sinks_filled=True,
            ),
            boundary=boundary,
        )
