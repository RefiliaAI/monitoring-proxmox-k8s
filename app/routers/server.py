from fastapi import APIRouter, HTTPException

from app.cache import cache
from app.models import ServerStats

router = APIRouter()


@router.get("/api/server", response_model=ServerStats)
async def get_server():
    if cache.server is None:
        raise HTTPException(status_code=503, detail="No server data yet")
    return cache.server
