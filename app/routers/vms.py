from fastapi import APIRouter

from app.cache import cache
from app.models import VMStat

router = APIRouter()


@router.get("/api/vms", response_model=list[VMStat])
async def get_vms():
    return cache.vms
