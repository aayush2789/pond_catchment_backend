import tempfile
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional
from fastapi import HTTPException, status
from app.schemas.catchment import ContourInspectionResponse
from app.utils.file_handler import validate_contour_extension


class ContourParserService:
    @staticmethod
    def inspect_contour_file(file_content: bytes, filename: str) -> ContourInspectionResponse:
        ext = validate_contour_extension(filename)
        if not file_content:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Uploaded file is empty.",
            )

        kml_bytes: bytes
        kml_entry_name: Optional[str] = None

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir) / filename
            temp_path.write_bytes(file_content)

            if ext == ".kmz":
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
            else:
                kml_bytes = file_content

            try:
                root = ET.fromstring(kml_bytes)
            except ET.ParseError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Malformed KML content: {exc}",
                )

            tag_name = root.tag.split("}")[-1].lower() if "}" in root.tag else root.tag.lower()
            if tag_name != "kml":
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Uploaded file is not a valid KML document.",
                )

            placemark_count = sum(
                1 for elem in root.iter()
                if (elem.tag.split("}")[-1].lower() if "}" in elem.tag else elem.tag.lower()) == "placemark"
            )

        return ContourInspectionResponse(
            filename=filename,
            file_type=ext.lstrip("."),
            file_size_bytes=len(file_content),
            is_valid=True,
            can_parse=True,
            kml_entry_name=kml_entry_name,
            features_count=placemark_count,
            message="Contour file successfully validated and ready for terrain analysis.",
        )
