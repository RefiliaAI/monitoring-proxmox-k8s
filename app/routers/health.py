from fastapi import APIRouter, Response

from app.cache import cache

router = APIRouter()


@router.get("/healthz")
async def healthz():
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(response: Response):
    if not cache.ready:
        response.status_code = 503
        return {"status": "not-ready"}
    return {"status": "ready"}
