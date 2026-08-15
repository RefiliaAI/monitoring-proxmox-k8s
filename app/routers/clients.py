from fastapi import APIRouter

from app.cache import cache
from app.models import ClientDevice

router = APIRouter()


@router.get("/api/clients", response_model=list[ClientDevice])
async def get_clients():
    return cache.clients
