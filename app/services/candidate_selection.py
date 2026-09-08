from dataclasses import dataclass
from typing import Dict, List, Optional
import numpy as np
from pyproj import Transformer
from app.schemas.catchment import PondCandidateSite
from app.services.terrain import TerrainModel


@dataclass
class CandidateScoringConfig:
    slope_weight: float = 0.5
    elevation_weight: float = 0.5
    ideal_slope_deg: float = 3.0
    max_acceptable_slope_deg: float = 12.0
    min_distance_meters: float = 150.0
    top_k: int = 5
    boundary_buffer_cells: int = 5
    flow_weight: float = 0.0


class CandidateSelectionService:
    @classmethod
    def identify_candidates(
        cls,
        terrain: TerrainModel,
        config: Optional[CandidateScoringConfig] = None,
        flow_accumulation: Optional[np.ndarray] = None,
    ) -> List[PondCandidateSite]:
        cfg = config or CandidateScoringConfig()

        dem = terrain.elevation_grid
        slope = terrain.slope_grid
        if slope is None:
            dy, dx = np.gradient(dem, terrain.grid_resolution_meters, terrain.grid_resolution_meters)
            slope = np.degrees(np.arctan(np.sqrt(dx**2 + dy**2)))

        slope_score = np.clip(
            1.0 - (slope - cfg.ideal_slope_deg) / max(0.1, cfg.max_acceptable_slope_deg - cfg.ideal_slope_deg),
            0.0,
            1.0,
        )
        slope_score[slope <= cfg.ideal_slope_deg] = 1.0

        elev_range = terrain.max_elevation - terrain.min_elevation
        if elev_range > 0:
            elevation_score = 1.0 - (dem - terrain.min_elevation) / elev_range
        else:
            elevation_score = np.ones_like(dem)

        flow_score = None
        if flow_accumulation is not None and cfg.flow_weight > 0.0:
            max_flow = float(np.max(flow_accumulation))
            flow_score = flow_accumulation / max_flow if max_flow > 0 else np.zeros_like(dem)

        total_weight = cfg.slope_weight + cfg.elevation_weight + (cfg.flow_weight if flow_score is not None else 0.0)
        if total_weight <= 0:
            total_weight = 1.0

        numerator = cfg.slope_weight * slope_score + cfg.elevation_weight * elevation_score
        if flow_score is not None:
            numerator += cfg.flow_weight * flow_score
        composite = numerator / total_weight

        work_grid = composite.copy()
        buf = max(1, cfg.boundary_buffer_cells)
        if work_grid.shape[0] > 2 * buf and work_grid.shape[1] > 2 * buf:
            work_grid[:buf, :] = 0.0
            work_grid[-buf:, :] = 0.0
            work_grid[:, :buf] = 0.0
            work_grid[:, -buf:] = 0.0

        cell_radius = max(1, int(cfg.min_distance_meters / terrain.grid_resolution_meters))
        min_x, max_x, min_y, max_y = terrain.bounds
        res = terrain.grid_resolution_meters

        transformer = Transformer.from_crs(terrain.crs, "EPSG:4326", always_xy=True)
        candidates: List[PondCandidateSite] = []

        for rank in range(1, cfg.top_k + 1):
            max_idx = np.unravel_index(np.argmax(work_grid), work_grid.shape)
            best_score = float(work_grid[max_idx])
            if best_score <= 0.0:
                break

            r, c = int(max_idx[0]), int(max_idx[1])
            proj_x = min_x + c * res
            proj_y = min_y + r * res
            lon, lat = transformer.transform(proj_x, proj_y)

            factor_scores: Dict[str, float] = {
                "slope_score": round(float(slope_score[r, c]), 4),
                "elevation_score": round(float(elevation_score[r, c]), 4),
            }
            if flow_score is not None:
                factor_scores["flow_score"] = round(float(flow_score[r, c]), 4)

            candidates.append(
                PondCandidateSite(
                    id=f"pond_site_{rank}",
                    rank=rank,
                    latitude=round(float(lat), 7),
                    longitude=round(float(lon), 7),
                    elevation=round(float(dem[r, c]), 2),
                    slope_degrees=round(float(slope[r, c]), 2),
                    suitability_score=round(float(composite[r, c]), 4),
                    factor_scores=factor_scores,
                )
            )

            r_min = max(0, r - cell_radius)
            r_max = min(work_grid.shape[0], r + cell_radius + 1)
            c_min = max(0, c - cell_radius)
            c_max = min(work_grid.shape[1], c + cell_radius + 1)
            work_grid[r_min:r_max, c_min:c_max] = 0.0

        return candidates
