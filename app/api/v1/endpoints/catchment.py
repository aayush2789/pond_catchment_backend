from fastapi import APIRouter, File, UploadFile
from app.schemas.catchment import ContourInspectionResponse
from app.services.candidate_selection import CandidateSelectionService
from app.services.hydrology import HydrologyService
from app.services.parser import ContourParserService
from app.services.terrain import TerrainService

router = APIRouter()


@router.post(
    "/findCatchment",
    response_model=ContourInspectionResponse,
    summary="Analyze contour map, identify pond sites, and delineate catchment",
    description="Accepts a KML or KMZ contour map file via multipart form-data, reconstructs the continuous DEM surface, computes slope, evaluates candidate pond sites using explainable criteria, and delineates the upstream contributing catchment area and GeoJSON boundary polygon.",
)
@router.post(
    "/analyzeContour",
    response_model=ContourInspectionResponse,
    summary="Analyze contour map, identify pond sites, and delineate catchment (alias)",
    description="Alias endpoint for /findCatchment.",
)
async def find_catchment(
    file: UploadFile = File(..., description="Contour map file (.kml or .kmz)"),
) -> ContourInspectionResponse:
    content = await file.read()
    filename = file.filename or "upload.kml"

    # 1. KML extraction & archive unpacking
    kml_bytes, kml_entry_name, ext = ContourParserService.extract_kml_payload(content, filename)

    # 2. Contour extraction and normalization
    dataset = ContourParserService.parse_and_normalize_kml(kml_bytes, filename)

    # 3. Dynamic terrain interpolation & slope modeling
    terrain_model = TerrainService.reconstruct_terrain(dataset)

    # 4. Multi-factor candidate pond siting
    candidate_sites = CandidateSelectionService.identify_candidates(terrain_model)
    selected_pond = candidate_sites[0] if candidate_sites else None

    # 5. Hydrological modeling & catchment delineation
    catchment_result = None
    if selected_pond is not None:
        catchment_result = HydrologyService.analyze_hydrology(terrain_model, selected_pond)

    # 6. Assemble standardized response
    return ContourInspectionResponse(
        filename=filename,
        file_type=ext.lstrip("."),
        file_size_bytes=len(content),
        is_valid=True,
        can_parse=True,
        contours_processed=dataset.contour_count,
        min_elevation=dataset.min_elevation,
        max_elevation=dataset.max_elevation,
        extent=dataset.extent,
        kml_entry_name=kml_entry_name,
        terrain=terrain_model.to_metadata(),
        candidate_sites=candidate_sites,
        selected_pond=selected_pond,
        catchment=catchment_result,
        message="Contour file successfully validated, normalized, reconstructed, and analyzed for pond catchment.",
    )
