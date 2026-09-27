from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    PROJECT_NAME: str = "Village Pond Planning System"
    API_V1_STR: str = "/api/v1"
    VERSION: str = "0.1.0"
    DEBUG: bool = False
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # Maximum accepted user-selected land area (km²). Guards /analyzeLand and
    # downstream terrain processing from unreasonably large selections.
    MAX_LAND_AREA_SQ_KM: float = 100.0

    # --- DEM acquisition (Phase 2A) ---
    # Primary provider: "aws_terrain_tiles" (no API key) or "opentopography" (free API key).
    DEM_PROVIDER: str = "aws_terrain_tiles"
    # OpenTopography dataset (demtype) when that provider is used, e.g. SRTMGL1 (30 m).
    OPEN_TOPOGRAPHY_DATASET: str = "SRTMGL1"
    OPEN_TOPOGRAPHY_API_KEY: str = ""
    # Buffer added around the selected land bbox to define the hydrological analysis
    # extent (the catchment may extend beyond the selected land).
    ANALYSIS_BUFFER_METERS: float = 500.0
    # Target DEM grid resolution in meters (AWS tiles are resampled to this).
    DEM_TARGET_RESOLUTION_M: float = 30.0
    DEM_REQUEST_TIMEOUT_S: int = 45
    DEM_CACHE_DIR: str = "data/cache/dem"
    DEM_MAX_TILES: int = 64
    DEM_MAX_GRID_DIM: int = 500
    DEM_MAX_EXTENT_KM: float = 15.0
    DEM_MAX_RESPONSE_MB: int = 64

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )


settings = Settings()
