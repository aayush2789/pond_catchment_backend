from typing import Annotated, Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field

from app.schemas.catchment import GeographicExtent

# A GeoJSON position is [longitude, latitude] or [longitude, latitude, elevation].
Position = Annotated[List[float], Field(min_length=2, max_length=3)]


class PolygonGeometry(BaseModel):
    type: Literal["Polygon"]
    coordinates: List[List[Position]]


class MultiPolygonGeometry(BaseModel):
    type: Literal["MultiPolygon"]
    coordinates: List[List[List[Position]]]


# Discriminated union: requests carrying any other GeoJSON type are rejected at validation time.
LandGeometry = Annotated[Union[PolygonGeometry, MultiPolygonGeometry], Field(discriminator="type")]


class LandSelectionRequest(BaseModel):
    geometry: LandGeometry
    properties: Optional[Dict[str, Any]] = None


class Centroid(BaseModel):
    latitude: float
    longitude: float


class SelectedLand(BaseModel):
    geometry: Dict[str, Any]
    geometry_type: str
    area_m2: float
    area_hectares: float
    bounding_box: GeographicExtent
    centroid: Centroid


class LandSelectionResponse(BaseModel):
    status: str = "success"
    selected_land: SelectedLand
    message: str
