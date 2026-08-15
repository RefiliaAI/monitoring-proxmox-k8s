from fastapi import APIRouter

from app.cache import cache
from app.models import StoragePool

router = APIRouter()


@router.get("/api/storage", response_model=list[StoragePool])
async def get_storage():
    return cache.storage_pools
