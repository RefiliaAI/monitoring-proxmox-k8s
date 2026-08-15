from fastapi import APIRouter

from app.cache import cache
from app.config import settings
from app.models import ClientDevice
from app.services.client_activity import is_recently_active

router = APIRouter()


@router.get("/api/clients", response_model=list[ClientDevice])
async def get_clients(all: bool = False):
    """By default, only devices seen online within CLIENT_RECENT_HOURS
    (24h) are returned -- the router remembers every device it's ever
    leased, most of which are long gone. Pass ?all=true for the full
    historical list.
    """
    if all:
        return cache.clients
    return [
        c
        for c in cache.clients
        if is_recently_active(c.active, c.last_active_at, settings.client_recent_hours)
    ]
