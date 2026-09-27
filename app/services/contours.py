"""Contour line generation from a DEM grid.

Implements vectorized marching squares over the elevation grid, chains the resulting
segments into LineStrings, and converts them to WGS84 GeoJSON suitable for direct
rendering on a frontend map (Leaflet/MapLibre).

Documented assumption: the contour interval must reflect what the underlying DEM can
support. A 30 m DEM resolves elevation trends of a few meters at best, so the default
interval is conservative (5 m) and configurable; sub-meter intervals are not offered
because they would imply precision the source data does not have.
"""

import math
from collections import defaultdict, deque
from typing import Any, Dict, List, Tuple

import numpy as np
from pyproj import Transformer
from fastapi import HTTPException, status

from app.services.terrain import TerrainModel

MAX_CONTOUR_LEVELS = 50
DEFAULT_CONTOUR_INTERVAL_M = 5.0
MIN_CONTOUR_INTERVAL_M = 1.0

# Marching squares case table.
# Corner bits: TL=8 (row r, col c), TR=4 (r, c+1), BR=2 (r+1, c+1), BL=1 (r+1, c).
# Edge ids: T=0, R=1, B=2, L=3. Each case lists the contour segments crossing that cell.
_CASE_SEGMENTS: Dict[int, List[Tuple[int, int]]] = {
    1: [(2, 3)],
    2: [(1, 2)],
    3: [(1, 3)],
    4: [(0, 1)],
    5: [(0, 3), (1, 2)],  # saddle
    6: [(0, 2)],
    7: [(0, 3)],
    8: [(0, 3)],
    9: [(0, 2)],
    10: [(0, 1), (2, 3)],  # saddle
    11: [(0, 1)],
    12: [(1, 3)],
    13: [(1, 2)],
    14: [(2, 3)],
}

_EDGE_T, _EDGE_R, _EDGE_B, _EDGE_L = 0, 1, 2, 3


class ContourGenerationService:
    @staticmethod
    def contour_levels(
        min_elevation: float,
        max_elevation: float,
        interval: float,
        max_levels: int = MAX_CONTOUR_LEVELS,
    ) -> List[float]:
        """Deterministic contour levels aligned to whole multiples of the interval."""
        if interval < MIN_CONTOUR_INTERVAL_M:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Contour interval must be at least {MIN_CONTOUR_INTERVAL_M} m: "
                    "the underlying DEM cannot support finer intervals."
                ),
            )
        span = max_elevation - min_elevation
        if span <= 0:
            return []
        effective = interval
        if span / effective > max_levels:
            effective = span / max_levels
        start = math.ceil(min_elevation / effective) * effective
        levels = np.arange(start, max_elevation + 1e-9, effective)
        if len(levels) > max_levels:
            levels = levels[:max_levels]
        return [round(float(v), 4) for v in levels]

    @staticmethod
    def _segments_for_level(
        grid: np.ndarray, level: float
    ) -> List[Tuple[Tuple[float, float], Tuple[float, float]]]:
        """Extract unconstrained contour segments (in cell coordinates) for one level."""
        above = grid >= level
        tl = above[:-1, :-1]
        tr = above[:-1, 1:]
        br = above[1:, 1:]
        bl = above[1:, :-1]
        case = (
            tl.astype(np.int16) * 8
            + tr.astype(np.int16) * 4
            + br.astype(np.int16) * 2
            + bl.astype(np.int16)
        )
        crossing = (case != 0) & (case != 15)
        rr, cc = np.nonzero(crossing)
        if rr.size == 0:
            return []

        g = grid
        tl_v = g[rr, cc].astype(np.float64)
        tr_v = g[rr, cc + 1].astype(np.float64)
        bl_v = g[rr + 1, cc].astype(np.float64)
        br_v = g[rr + 1, cc + 1].astype(np.float64)

        def frac(a: np.ndarray, b: np.ndarray) -> np.ndarray:
            denom = b - a
            safe = np.where(denom == 0.0, 1.0, denom)
            return np.clip((level - a) / safe, 0.0, 1.0)

        f_top = frac(tl_v, tr_v)
        f_right = frac(tr_v, br_v)
        f_bottom = frac(bl_v, br_v)
        f_left = frac(tl_v, bl_v)

        rr_f = rr.astype(np.float64)
        cc_f = cc.astype(np.float64)
        edge_points = [
            np.stack([cc_f + f_top, rr_f], axis=1),  # T
            np.stack([cc_f + 1.0, rr_f + f_right], axis=1),  # R
            np.stack([cc_f + f_bottom, rr_f + 1.0], axis=1),  # B
            np.stack([cc_f, rr_f + f_left], axis=1),  # L
        ]

        segments: List[Tuple[Tuple[float, float], Tuple[float, float]]] = []
        cases = case[rr, cc]
        for i in range(rr.size):
            for e1, e2 in _CASE_SEGMENTS[int(cases[i])]:
                p1 = edge_points[e1][i]
                p2 = edge_points[e2][i]
                if (p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2 > 1e-12:
                    segments.append(((float(p1[0]), float(p1[1])), (float(p2[0]), float(p2[1]))))
        return segments

    @staticmethod
    def _chain_segments(
        segments: List[Tuple[Tuple[float, float], Tuple[float, float]]]
    ) -> List[List[Tuple[float, float]]]:
        """Join marching-squares segments into polylines (closed rings stay closed)."""
        def key(p: Tuple[float, float]) -> Tuple[float, float]:
            return (round(p[0], 4), round(p[1], 4))

        adjacency: Dict[Tuple[float, float], List[Tuple[int, bool]]] = defaultdict(list)
        for idx, (a, b) in enumerate(segments):
            adjacency[key(a)].append((idx, False))
            adjacency[key(b)].append((idx, True))

        used = [False] * len(segments)
        lines: List[List[Tuple[float, float]]] = []
        for start in range(len(segments)):
            if used[start]:
                continue
            used[start] = True
            a, b = segments[start]
            pts: deque = deque([a, b])
            for extend_right in (True, False):
                while True:
                    tip = pts[-1] if extend_right else pts[0]
                    k = key(tip)
                    match = next(((j, end_is_b) for j, end_is_b in adjacency.get(k, []) if not used[j]), None)
                    if match is None:
                        break
                    j, end_is_b = match
                    used[j] = True
                    p, q = segments[j]
                    nxt = p if end_is_b else q
                    if extend_right:
                        pts.append(nxt)
                    else:
                        pts.appendleft(nxt)
            if len(pts) >= 2:
                lines.append(list(pts))
        return lines

    @classmethod
    def generate_contours(
        cls,
        terrain: TerrainModel,
        interval: float = DEFAULT_CONTOUR_INTERVAL_M,
    ) -> Dict[str, Any]:
        """Generate contour lines as a GeoJSON FeatureCollection in WGS84 lon/lat."""
        grid = np.asarray(terrain.elevation_grid, dtype=np.float64)
        levels = cls.contour_levels(float(grid.min()), float(grid.max()), interval)
        min_x, _, min_y, _ = terrain.bounds
        res = terrain.grid_resolution_meters
        to_wgs = Transformer.from_crs(terrain.crs, "EPSG:4326", always_xy=True)

        features: List[Dict[str, Any]] = []
        for level in levels:
            segments = cls._segments_for_level(grid, level)
            if not segments:
                continue
            for line in cls._chain_segments(segments):
                cell_x = np.fromiter((p[0] for p in line), dtype=np.float64, count=len(line))
                cell_y = np.fromiter((p[1] for p in line), dtype=np.float64, count=len(line))
                xs = min_x + cell_x * res
                ys = min_y + cell_y * res
                lons, lats = to_wgs.transform(xs, ys)
                coordinates = [
                    [round(float(lon), 7), round(float(lat), 7)]
                    for lon, lat in zip(lons, lats)
                ]
                features.append(
                    {
                        "type": "Feature",
                        "geometry": {"type": "LineString", "coordinates": coordinates},
                        "properties": {"elevation_m": level},
                    }
                )

        return {
            "type": "FeatureCollection",
            "features": features,
            "properties": {
                "interval_m": round(float(min(interval, (grid.max() - grid.min()) / max(1, len(levels))) if levels else interval), 3),
                "contour_count": len(features),
                "crs": "EPSG:4326",
            },
        }
