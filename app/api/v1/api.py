from fastapi import APIRouter
from app.api.v1.endpoints import catchment, health, land, pond_site, terrain

api_router = APIRouter()
api_router.include_router(health.router, tags=["Health"])
api_router.include_router(catchment.router, tags=["Catchment Analysis"])
api_router.include_router(land.router, tags=["Land Selection"])
api_router.include_router(terrain.router, tags=["Terrain Acquisition"])
api_router.include_router(pond_site.router, tags=["Pond Planning"])
