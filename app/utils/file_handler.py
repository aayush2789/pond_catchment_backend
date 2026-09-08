from pathlib import Path
from fastapi import HTTPException, status

ALLOWED_CONTOUR_EXTENSIONS = {".kml", ".kmz"}


def validate_contour_extension(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_CONTOUR_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file format '{suffix}'. Allowed formats: {', '.join(ALLOWED_CONTOUR_EXTENSIONS)}",
        )
    return suffix
