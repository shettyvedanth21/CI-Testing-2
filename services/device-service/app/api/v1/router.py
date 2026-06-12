"""API version 1 router aggregation."""

from fastapi import APIRouter

from app.api.v1.devices import router as devices_router
from app.api.v1.settings import router as settings_router

api_router = APIRouter()

api_router.include_router(settings_router, prefix="/settings", tags=["settings"])
api_router.include_router(devices_router, prefix="/devices", tags=["devices"])
