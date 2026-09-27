from fastapi import APIRouter

from app.core.config import settings
from app.schemas.catchment import ProjectedBounds
from app.schemas.terrain import (
    DEMPreviewInfo,
    TerrainPreviewRequest,
    TerrainPreviewResponse,
)
from app.services.dem import DEMService
from app.services.land import LandSelectionService

router = APIRouter()


@router.post(
    "/terrainPreview",
    response_model=TerrainPreviewResponse,
    summary="Acquire DEM for the area around a selected land polygon",
    description=(
        "Diagnostic/preview endpoint for the automatic terrain-acquisition path. Validates the "
        "selected land geometry, expands its bounding box by a documented buffer to define the "
        "hydrological analysis extent (the catchment may extend beyond the selected land), and "
        "acquires a DEM for that extent from the configured public DEM provider (AWS Terrain "
        "Tiles by default, OpenTopography when an API key is configured). Results are cached by "
        "geographic extent. The KML/KMZ upload path remains fully supported independently."
    ),
)
async def terrain_preview(request: TerrainPreviewRequest) -> TerrainPreviewResponse:
    selected_land = LandSelectionService.validate_and_measure(request.geometry)
    buffer_meters = (
        request.buffer_meters
        if request.buffer_meters is not None
        else settings.ANALYSIS_BUFFER_METERS
    )
    resolution = request.resolution_meters

    analysis_extent = DEMService.compute_analysis_extent(
        selected_land.bounding_box, buffer_meters
    )
    dem = DEMService.acquire_dem(analysis_extent, target_resolution_m=resolution)

    return TerrainPreviewResponse(
        status="success",
        analysis_extent=analysis_extent,
        buffer_meters=buffer_meters,
        target_resolution_meters=dem.resolution_meters,
        dem=DEMPreviewInfo(
            crs=dem.crs,
            resolution_meters=dem.resolution_meters,
            rows=dem.rows,
            cols=dem.cols,
            min_elevation_m=round(float(dem.elevation_grid.min()), 2),
            max_elevation_m=round(float(dem.elevation_grid.max()), 2),
            mean_elevation_m=round(float(dem.elevation_grid.mean()), 2),
            nodata_cells_filled=dem.nodata_cells_filled,
            projected_bounds=ProjectedBounds(
                min_x=dem.bounds[0],
                max_x=dem.bounds[1],
                min_y=dem.bounds[2],
                max_y=dem.bounds[3],
            ),
            geographic_extent=dem.geographic_extent,
            source={
                "provider": dem.source.provider,
                "dataset": dem.source.dataset,
                "attribution": dem.source.attribution,
                "zoom_level": dem.source.zoom_level,
            },
            cache_hit=dem.cache_hit,
        ),
        message=(
            "DEM acquired for the buffered analysis extent. The KML/KMZ upload path remains available."
        ),
    )
