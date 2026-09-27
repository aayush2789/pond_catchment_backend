"""Unified pond-planning workflow (Phase 8).

Composes the existing single-responsibility services into the full pipeline:

    selected land -> analysis extent -> DEM acquisition (or KML fallback)
      -> terrain (DEM reconstruction, slope) -> contours
      -> hydrology (conditioning, D8 flow, accumulation - computed once)
      -> candidate pond siting (constrained to the selected land)
      -> catchment delineation (may extend beyond the selected land)
      -> rainfall -> runoff / expected collectible water -> indicative storage

The KML/KMZ contour path is retained as an expert/testing fallback: when a file is
supplied instead of a land geometry, terrain comes from contour reconstruction and
no land-constraint masking is applied.
"""

from typing import Optional

from fastapi import HTTPException, status
from shapely.geometry import shape as shape_from_geojson

from app.core.config import settings
from app.schemas.pond_site import (
    AnalysisParameters,
    PondSiteAnalysisRequest,
    PondSiteAnalysisResponse,
)
from app.schemas.terrain import DEMSourceInfo as DEMSourceInfoSchema
from app.services.candidate_selection import CandidateSelectionService
from app.services.contours import DEFAULT_CONTOUR_INTERVAL_M, ContourGenerationService
from app.services.dem import DEMService
from app.services.hydrology import HydrologyService
from app.services.land import LandSelectionService
from app.services.parser import ContourParserService
from app.services.pond import PondStorageService
from app.services.rainfall import RainfallService
from app.services.terrain import TerrainService
from app.services.water import WaterVolumeService

DEFAULT_SNAP_RADIUS_M = 100.0


class PondPlanningService:
    @classmethod
    def analyze_pond_site(
        cls,
        request: PondSiteAnalysisRequest,
        kml_bytes: Optional[bytes] = None,
        kml_filename: Optional[str] = None,
    ) -> PondSiteAnalysisResponse:
        params = request.analysis_parameters or AnalysisParameters()

        if (kml_bytes is None) == (request.geometry is None):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Provide exactly one terrain source: a land `geometry` (automatic DEM "
                    "acquisition) or an uploaded KML/KMZ contour `file`."
                ),
            )

        # 1. Land selection + terrain acquisition ---------------------------------------
        selected_land = None
        dem_source = None
        if kml_bytes is not None:
            kml_payload, _entry, _ext = ContourParserService.extract_kml_payload(
                kml_bytes, kml_filename or "upload.kml"
            )
            dataset = ContourParserService.parse_and_normalize_kml(kml_payload, kml_filename or "upload.kml")
            terrain = TerrainService.reconstruct_terrain(dataset)
            terrain_message = "Terrain reconstructed from the uploaded contour file."
        else:
            selected_land = LandSelectionService.validate_and_measure(request.geometry)
            buffer_meters = (
                params.buffer_meters
                if params.buffer_meters is not None
                else settings.ANALYSIS_BUFFER_METERS
            )
            analysis_extent = DEMService.compute_analysis_extent(
                selected_land.bounding_box, buffer_meters
            )
            dem = DEMService.acquire_dem(
                analysis_extent, target_resolution_m=params.dem_resolution_m
            )
            terrain = TerrainService.reconstruct_terrain_from_dem(dem)
            dem_source = DEMSourceInfoSchema(
                provider=dem.source.provider,
                dataset=dem.source.dataset,
                attribution=dem.source.attribution,
                zoom_level=dem.source.zoom_level,
            )
            terrain_message = (
                "Terrain acquired automatically for the buffered analysis extent "
                "around the selected land."
            )

        # 2. Contours for visualization ---------------------------------------------------
        contour_interval = (
            params.contour_interval_m
            if params.contour_interval_m is not None
            else DEFAULT_CONTOUR_INTERVAL_M
        )
        contours = ContourGenerationService.generate_contours(terrain, contour_interval)

        # 3. Hydrology grids computed once and shared ------------------------------------
        filled_dem = HydrologyService.condition_dem(terrain.elevation_grid)
        flow_dir = HydrologyService.calculate_flow_direction(
            filled_dem, terrain.grid_resolution_meters
        )
        flow_acc = HydrologyService.calculate_flow_accumulation(flow_dir, filled_dem)

        # 4. Candidate siting (land-constrained when a polygon is provided) ---------------
        candidate_mask = None
        if selected_land is not None:
            land_polygon = shape_from_geojson(selected_land.geometry)
            candidate_mask = TerrainService.mask_cells_within_polygon(terrain, land_polygon)

        candidates = CandidateSelectionService.identify_candidates(
            terrain,
            config=CandidateSelectionService.FLOW_WEIGHTED_CONFIG,
            flow_accumulation=flow_acc,
            candidate_mask=candidate_mask,
        )
        if not candidates:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "No suitable pond location could be identified within the selected land. "
                    "The selection may be too small relative to the DEM grid resolution "
                    f"({terrain.grid_resolution_meters} m) or lie on unsuitable terrain."
                ),
            )
        pond = candidates[0]

        # 5. Catchment delineation (not clipped to the land) ------------------------------
        catchment = HydrologyService.analyze_hydrology(
            terrain,
            pond,
            snap_radius_meters=(
                params.snap_radius_meters
                if params.snap_radius_meters is not None
                else DEFAULT_SNAP_RADIUS_M
            ),
            conditioned_dem=filled_dem,
            flow_direction=flow_dir,
            flow_accumulation=flow_acc,
        )

        # 6. Rainfall, runoff, indicative storage ------------------------------------------
        rainfall = RainfallService.get_rainfall(pond.latitude, pond.longitude)
        water = WaterVolumeService.estimate(
            catchment_area_m2=catchment.catchment_area_sq_meters,
            rainfall_mm=rainfall.rainfall_mm,
            rainfall_period=rainfall.period,
            runoff_coefficient=params.runoff_coefficient,
            collection_efficiency=params.collection_efficiency,
        )
        pond_storage = PondStorageService.suggest_pond_storage(
            water.expected_collectible_water_m3
        )

        return PondSiteAnalysisResponse(
            status="success",
            selected_land=selected_land,
            terrain=terrain.to_metadata(),
            contours=contours,
            pond=pond,
            catchment=catchment,
            rainfall=rainfall,
            water=water,
            pond_storage=pond_storage,
            dem_source=dem_source,
            message=(
                f"{terrain_message} Pond site, catchment, rainfall, and water-volume "
                "estimation completed."
            ),
        )
