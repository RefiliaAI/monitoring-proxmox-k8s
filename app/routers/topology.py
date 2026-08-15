from fastapi import APIRouter, HTTPException

from app.cache import cache
from app.models import Topology

router = APIRouter()


@router.get("/api/topology", response_model=Topology)
async def get_topology():
    if cache.topology is None:
        raise HTTPException(status_code=503, detail="No topology data yet")
    return cache.topology
