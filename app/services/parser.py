import re
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List, Optional, Tuple
from fastapi import HTTPException, status
from app.schemas.catchment import (
    ContourInspectionResponse,
    ContourLine,
    GeographicExtent,
    NormalizedContourDataset,
)
from app.services.candidate_selection import CandidateSelectionService
from app.services.hydrology import HydrologyService
from app.services.terrain import TerrainService
from app.utils.file_handler import validate_contour_extension




VALID_KML_ROOT_TAGS = {"kml", "document", "folder"}
ELEVATION_ATTR_NAMES = {"elevation", "elev", "contour", "z", "height", "level", "ele"}


class ContourParserService:
    @staticmethod
    def _local_tag(element: ET.Element) -> str:
        return element.tag.split("}")[-1].lower() if "}" in element.tag else element.tag.lower()

    @classmethod
    def _extract_elevation(cls, placemark: ET.Element, fallback_z: Optional[float] = None) -> Optional[float]:
        for elem in placemark.iter():
            tag = cls._local_tag(elem)
            if tag == "simpledata":
                name_attr = elem.attrib.get("name", "").lower()
                if name_attr in ELEVATION_ATTR_NAMES and elem.text:
                    try:
                        return float(elem.text.strip())
                    except ValueError:
                        pass
            elif tag == "data":
                name_attr = elem.attrib.get("name", "").lower()
                if name_attr in ELEVATION_ATTR_NAMES:
                    val_elem = elem.find("{*}value")
                    if val_elem is not None and val_elem.text:
                        try:
                            return float(val_elem.text.strip())
                        except ValueError:
                            pass

        name_text = placemark.findtext("{*}name")
        if name_text:
            match = re.search(r"[-+]?\d+(?:\.\d+)?", name_text.strip())
            if match:
                return float(match.group(0))

        desc_text = placemark.findtext("{*}description")
        if desc_text:
            match = re.search(r"(?:elevation|contour|elev|height|z)\s*[:=]?\s*([-+]?\d+(?:\.\d+)?)", desc_text, re.I)
            if match:
                return float(match.group(1))

        if fallback_z is not None:
            return fallback_z

        return None

    @classmethod
    def _parse_coordinates(cls, coord_text: str) -> List[Tuple[float, float, Optional[float]]]:
        coords: List[Tuple[float, float, Optional[float]]] = []
        for token in coord_text.strip().split():
            parts = [p.strip() for p in token.split(",") if p.strip()]
            if len(parts) >= 2:
                try:
                    lon = float(parts[0])
                    lat = float(parts[1])
                    elev = float(parts[2]) if len(parts) >= 3 else None
                    coords.append((lon, lat, elev))
                except ValueError:
                    continue
        return coords

    @classmethod
    def parse_and_normalize_kml(cls, kml_bytes: bytes, filename: str) -> NormalizedContourDataset:
        try:
            root = ET.fromstring(kml_bytes)
        except ET.ParseError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Malformed KML content: {exc}",
            )

        tag_name = cls._local_tag(root)
        is_kml_ns = "opengis.net/kml" in root.tag.lower() or "google.com/kml" in root.tag.lower()

        if tag_name not in VALID_KML_ROOT_TAGS and not is_kml_ns:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Uploaded file is not a valid KML document.",
            )

        placemarks = [elem for elem in root.iter() if cls._local_tag(elem) == "placemark"]
        contour_lines: List[ContourLine] = []
        missing_elevation_count = 0
        degenerate_lines_count = 0
        total_line_geometries = 0

        search_elements = placemarks if placemarks else [root]

        for idx, elem in enumerate(search_elements):
            linestring_elements = [e for e in elem.iter() if cls._local_tag(e) == "linestring"]
            if not linestring_elements:
                continue

            for ls_idx, ls_elem in enumerate(linestring_elements):
                total_line_geometries += 1
                coord_elem = next((c for c in ls_elem.iter() if cls._local_tag(c) == "coordinates"), None)
                if coord_elem is None or not coord_elem.text:
                    degenerate_lines_count += 1
                    continue

                raw_coords = cls._parse_coordinates(coord_elem.text)
                if len(raw_coords) < 2:
                    degenerate_lines_count += 1
                    continue

                fallback_z = raw_coords[0][2] if raw_coords[0][2] is not None else None
                elevation = cls._extract_elevation(elem, fallback_z=fallback_z)

                if elevation is None:
                    missing_elevation_count += 1
                    continue

                contour_id = f"contour_{idx}_{ls_idx}"
                name_val = elem.findtext("{*}name")
                contour_lines.append(
                    ContourLine(
                        id=contour_id,
                        elevation=elevation,
                        coordinates=[(c[0], c[1]) for c in raw_coords],
                        vertex_count=len(raw_coords),
                        name=name_val.strip() if name_val else None,
                    )
                )

        if total_line_geometries == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No supported contour line geometries (e.g., LineString) found in the uploaded file.",
            )

        if degenerate_lines_count > 0 and not contour_lines:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Contour lines contain invalid geometry (minimum 2 coordinate points required).",
            )

        if missing_elevation_count > 0 and not contour_lines:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Found contour geometries missing required elevation data ({missing_elevation_count} features).",
            )

        if len(contour_lines) < 2:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Insufficient contour information: at least two contour lines are required.",
            )

        elevations = [c.elevation for c in contour_lines]
        if len(set(elevations)) < 2:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Insufficient contour information: terrain modeling requires at least two distinct contour elevation levels.",
            )

        min_lat = min(pt[1] for c in contour_lines for pt in c.coordinates)
        max_lat = max(pt[1] for c in contour_lines for pt in c.coordinates)
        min_lon = min(pt[0] for c in contour_lines for pt in c.coordinates)
        max_lon = max(pt[0] for c in contour_lines for pt in c.coordinates)

        extent = GeographicExtent(
            min_latitude=min_lat,
            max_latitude=max_lat,
            min_longitude=min_lon,
            max_longitude=max_lon,
        )

        return NormalizedContourDataset(
            filename=filename,
            contour_count=len(contour_lines),
            min_elevation=min(elevations),
            max_elevation=max(elevations),
            extent=extent,
            contours=contour_lines,
        )

    @classmethod
    def extract_kml_payload(cls, file_content: bytes, filename: str) -> Tuple[bytes, Optional[str], str]:
        ext = validate_contour_extension(filename)
        if not file_content:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Uploaded file is empty.",
            )

        if ext == ".kmz":
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir) / filename
                temp_path.write_bytes(file_content)

                if not zipfile.is_zipfile(temp_path):
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Malformed or corrupted KMZ archive.",
                    )
                try:
                    with zipfile.ZipFile(temp_path, "r") as archive:
                        kml_entries = [
                            name for name in archive.namelist()
                            if name.lower().endswith(".kml") and not name.startswith("__MACOSX/")
                        ]
                        if not kml_entries:
                            raise HTTPException(
                                status_code=status.HTTP_400_BAD_REQUEST,
                                detail="KMZ archive does not contain a valid .kml file.",
                            )
                        kml_entry_name = kml_entries[0]
                        kml_bytes = archive.read(kml_entry_name)
                except zipfile.BadZipFile:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Malformed or corrupted KMZ archive.",
                    )
            return kml_bytes, kml_entry_name, ext

        return file_content, None, ext

    @classmethod
    def inspect_contour_file(cls, file_content: bytes, filename: str) -> ContourInspectionResponse:
        kml_bytes, kml_entry_name, ext = cls.extract_kml_payload(file_content, filename)
        dataset = cls.parse_and_normalize_kml(kml_bytes, filename)
        terrain_model = TerrainService.reconstruct_terrain(dataset)
        candidate_sites = CandidateSelectionService.identify_candidates(terrain_model)
        selected_pond = candidate_sites[0] if candidate_sites else None
        catchment_result = None
        if selected_pond is not None:
            catchment_result = HydrologyService.analyze_hydrology(terrain_model, selected_pond)

        return ContourInspectionResponse(
            filename=filename,
            file_type=ext.lstrip("."),
            file_size_bytes=len(file_content),
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



