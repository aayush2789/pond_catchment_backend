from fastapi import APIRouter
from app.api.v1.endpoints import catchment, health

api_router = APIRouter()
api_router.include_router(health.router, tags=["Health"])
api_router.include_router(catchment.router, tags=["Catchment Analysis"])
