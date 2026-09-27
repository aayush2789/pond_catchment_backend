from typing import Any, Dict, Iterator, List

from fastapi import HTTPException, status
from pyproj import Geod
from shapely.geometry import MultiPolygon as ShapelyMultiPolygon
from shapely.geometry import Polygon as ShapelyPolygon
from shapely.geometry import mapping
from shapely.ops import unary_union

from app.core.config import settings
from app.schemas.catchment import GeographicExtent
from app.schemas.land import (
    Centroid,
    LandGeometry,
    SelectedLand,
)

# Geodesic area engine on the WGS84 ellipsoid (the datum of EPSG:4326 input coordinates).
_GEOD = Geod(ellps="WGS84")

# A selection smaller than this is a degenerate sliver and cannot be meaningfully analyzed.
MIN_SELECTION_AREA_SQ_M = 1.0


class LandSelectionService:
    """Validates a user-selected land polygon (GeoJSON) and computes its planning metrics.

    Assumptions (documented):
    - Area is computed as the geodesic polygon area on the WGS84 ellipsoid using
      pyproj.Geod. This is projection-independent and accurate at village scale.
    - Holes (interior rings) are subtracted; overlapping MultiPolygon parts are
      merged via a union so shared area is never counted twice.
    - The centroid is the planar (shapely) centroid in WGS84 degrees, which is an
      adequate reference point for village-scale selections.
    - Polygons whose longitude span suggests they cross the antimeridian are
      rejected with a clear error instead of producing a silently wrong area.
    """

    @staticmethod
    def _iter_positions(geometry: LandGeometry) -> Iterator[List[float]]:
        if geometry.type == "Polygon":
            rings = [geometry.coordinates]
        else:
            rings = geometry.coordinates
        for polygon_rings in rings:
            for ring in polygon_rings:
                for pos in ring:
                    yield pos

    @classmethod
    def _validate_coordinate_ranges(cls, geometry: LandGeometry) -> None:
        for pos in cls._iter_positions(geometry):
            lon, lat = pos[0], pos[1]
            if not (-180.0 <= lon <= 180.0):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid longitude {lon} in selected land geometry. Longitudes must be within [-180, 180].",
                )
            if not (-90.0 <= lat <= 90.0):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid latitude {lat} in selected land geometry. Latitudes must be within [-90, 90].",
                )

    @staticmethod
    def _validate_ring(ring: List[List[float]], ring_label: str) -> None:
        if len(ring) < 4:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"{ring_label} must contain at least 4 positions "
                    "(three distinct points plus the closing point)."
                ),
            )
        if ring[0][0] != ring[-1][0] or ring[0][1] != ring[-1][1]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{ring_label} is not closed: the first and last positions must be identical.",
            )
        distinct = {(p[0], p[1]) for p in ring[:-1]}
        if len(distinct) < 3:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{ring_label} is degenerate: it must contain at least 3 distinct positions.",
            )

    @classmethod
    def _build_polygon(
        cls,
        polygon_rings: List[List[List[float]]],
        label: str,
    ) -> ShapelyPolygon:
        if not polygon_rings:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{label} does not contain any coordinate rings.",
            )
        for idx, ring in enumerate(polygon_rings):
            ring_label = f"{label} exterior ring" if idx == 0 else f"{label} interior ring #{idx}"
            cls._validate_ring(ring, ring_label)
        try:
            polygon = ShapelyPolygon(
                [(p[0], p[1]) for p in polygon_rings[0]],
                [[(p[0], p[1]) for p in hole] for hole in polygon_rings[1:]],
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{label} could not be constructed as a valid polygon: {exc}",
            )
        if polygon.is_empty or not polygon.is_valid:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{label} is invalid (empty or self-intersecting).",
            )
        return polygon

    @classmethod
    def _to_shapely(cls, geometry: LandGeometry):
        if geometry.type == "Polygon":
            return cls._build_polygon(geometry.coordinates, "Selected land polygon")
        if not geometry.coordinates:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="MultiPolygon must contain at least one polygon.",
            )
        polygons = [
            cls._build_polygon(polygon_rings, f"Selected land polygon #{i}")
            for i, polygon_rings in enumerate(geometry.coordinates, start=1)
        ]
        if len(polygons) == 1:
            return polygons[0]
        merged = unary_union(polygons)
        if merged.is_empty:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="MultiPolygon geometry is empty after merging overlapping parts.",
            )
        return merged

    @staticmethod
    def _shapely_ring_geodesic_area(ring) -> float:
        area, _ = _GEOD.geometry_area_perimeter(ring)
        return abs(area)

    @classmethod
    def _geodesic_area_sq_m(cls, geom) -> float:
        if geom.geom_type == "Polygon":
            exterior = cls._shapely_ring_geodesic_area(geom.exterior)
            holes = sum(cls._shapely_ring_geodesic_area(hole) for hole in geom.interiors)
            return max(0.0, exterior - holes)
        if isinstance(geom, ShapelyMultiPolygon) or geom.geom_type == "MultiPolygon":
            return sum(cls._geodesic_area_sq_m(part) for part in geom.geoms)
        # GeometryCollection (possible after union): sum polygon members only.
        return sum(cls._geodesic_area_sq_m(part) for part in geom.geoms if part.geom_type == "Polygon")

    @classmethod
    def validate_and_measure(cls, geometry: LandGeometry) -> SelectedLand:
        cls._validate_coordinate_ranges(geometry)
        shapely_geom = cls._to_shapely(geometry)

        min_lon, min_lat, max_lon, max_lat = shapely_geom.bounds
        if (max_lon - min_lon) > 180.0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Selected land polygon appears to cross the antimeridian (longitude span > 180 degrees). "
                    "Split the selection into two polygons on either side of the 180th meridian."
                ),
            )

        area_m2 = cls._geodesic_area_sq_m(shapely_geom)
        if area_m2 < MIN_SELECTION_AREA_SQ_M:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Selected land polygon has zero or negligible area "
                    f"({area_m2:.4f} m²); a measurable land area is required."
                ),
            )
        max_area_m2 = settings.MAX_LAND_AREA_SQ_KM * 1_000_000.0
        if area_m2 > max_area_m2:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Selected land area ({area_m2 / 1_000_000.0:.2f} km²) exceeds the maximum "
                    f"supported extent of {settings.MAX_LAND_AREA_SQ_KM} km². Select a smaller area."
                ),
            )

        return SelectedLand(
            geometry=mapping(shapely_geom),
            geometry_type=shapely_geom.geom_type,
            area_m2=round(area_m2, 2),
            area_hectares=round(area_m2 / 10_000.0, 4),
            bounding_box=GeographicExtent(
                min_latitude=round(min_lat, 7),
                max_latitude=round(max_lat, 7),
                min_longitude=round(min_lon, 7),
                max_longitude=round(max_lon, 7),
            ),
            centroid=Centroid(
                latitude=round(float(shapely_geom.centroid.y), 7),
                longitude=round(float(shapely_geom.centroid.x), 7),
            ),
        )
