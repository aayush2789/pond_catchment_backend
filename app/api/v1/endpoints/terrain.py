from fastapi import APIRouter
import shapely
from shapely.geometry import shape as shape_from_geojson

from app.core.config import settings
from app.schemas.catchment import ProjectedBounds
from app.schemas.terrain import (
    DEMPreviewInfo,
    PondSitingResult,
    TerrainAnalysisInfo,
    TerrainPreviewRequest,
    TerrainPreviewResponse,
)
from app.services.candidate_selection import CandidateSelectionService
from app.services.contours import (
    DEFAULT_CONTOUR_INTERVAL_M,
    ContourGenerationService,
)
from app.services.dem import DEMService
from app.services.hydrology import HydrologyService
from app.services.land import LandSelectionService
from app.services.terrain import TerrainService

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

    # Phase 2B: the acquired DEM is a first-class terrain input — derive terrain
    # metrics and (optionally) visualization contours directly from it.
    terrain_model = TerrainService.reconstruct_terrain_from_dem(dem)
    contours = None
    if request.include_contours:
        interval = (
            request.contour_interval_m
            if request.contour_interval_m is not None
            else DEFAULT_CONTOUR_INTERVAL_M
        )
        contours = ContourGenerationService.generate_contours(terrain_model, interval)

    # Phases 3-4: candidate siting constrained to the selected land, catchment free
    # to extend beyond it. Flow grids are computed once and shared.
    pond_siting = None
    if request.include_analysis:
        land_polygon = shape_from_geojson(selected_land.geometry)
        land_mask = TerrainService.mask_cells_within_polygon(terrain_model, land_polygon)
        filled_dem = HydrologyService.condition_dem(terrain_model.elevation_grid)
        flow_dir = HydrologyService.calculate_flow_direction(
            filled_dem, terrain_model.grid_resolution_meters
        )
        flow_acc = HydrologyService.calculate_flow_accumulation(flow_dir, filled_dem)

        config = CandidateSelectionService.FLOW_WEIGHTED_CONFIG
        candidates = CandidateSelectionService.identify_candidates(
            terrain_model,
            config=config,
            flow_accumulation=flow_acc,
            candidate_mask=land_mask,
        )
        selected_pond = candidates[0] if candidates else None
        catchment = None
        if selected_pond is not None:
            catchment = HydrologyService.analyze_hydrology(
                terrain_model,
                selected_pond,
                conditioned_dem=filled_dem,
                flow_direction=flow_dir,
                flow_accumulation=flow_acc,
            )
        pond_siting = PondSitingResult(
            candidate_sites=candidates,
            selected_pond=selected_pond,
            scoring_config={
                "slope_weight": config.slope_weight,
                "elevation_weight": config.elevation_weight,
                "flow_weight": config.flow_weight,
                "ideal_slope_deg": config.ideal_slope_deg,
                "max_acceptable_slope_deg": config.max_acceptable_slope_deg,
                "min_distance_meters": config.min_distance_meters,
            },
            land_masked_cell_count=int(land_mask.sum()),
            catchment=catchment,
        )

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
        terrain=TerrainAnalysisInfo(
            terrain=terrain_model.to_metadata(),
            contours=contours,
        ),
        pond_siting=pond_siting,
        message=(
            "DEM acquired for the buffered analysis extent; terrain metrics and contours "
            "derived from it. The KML/KMZ upload path remains available."
        ),
    )
