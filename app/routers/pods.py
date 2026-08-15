from fastapi import APIRouter

from app.cache import cache
from app.models import PodStat

router = APIRouter()


@router.get("/api/pods", response_model=list[PodStat])
async def get_pods():
    return cache.pods
