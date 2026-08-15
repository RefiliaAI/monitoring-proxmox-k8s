from fastapi import APIRouter

from app.config import settings

router = APIRouter()

APP_VERSION = "0.2.0"


@router.get("/api/meta")
async def get_meta():
    return {
        "refresh_interval_seconds": settings.refresh_interval_seconds,
        "version": APP_VERSION,
        "client_discovery_enabled": settings.fritzbox_enabled,
    }
