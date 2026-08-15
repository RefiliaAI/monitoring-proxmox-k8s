import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.clients.fritzbox import FritzBoxClient
from app.clients.kubernetes import K8sClient
from app.clients.proxmox import ProxmoxClient
from app.config import settings
from app.routers import clients, health, meta, pods, server, topology, vms
from app.services.poller import poller_loop

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    px = ProxmoxClient(
        host=settings.proxmox_host,
        node=settings.proxmox_node,
        token_id=settings.proxmox_token_id,
        token_secret=settings.proxmox_token_secret,
        verify_ssl=settings.proxmox_verify_ssl,
    )
    k8s = K8sClient(in_cluster=settings.k8s_in_cluster)

    fritz: FritzBoxClient | None = None
    if settings.fritzbox_enabled:
        try:
            fritz = await asyncio.to_thread(
                FritzBoxClient,
                host=settings.fritzbox_host or settings.lan_gateway_ip,
                username=settings.fritzbox_username,
                password=settings.fritzbox_password,
            )
        except Exception:
            logger.exception("FRITZ!Box connection failed -- client discovery disabled")

    task = asyncio.create_task(poller_loop(px, k8s, fritz))
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await px.aclose()


app = FastAPI(title="Proxmox + K3s Monitoring Dashboard", lifespan=lifespan)

app.include_router(health.router)
app.include_router(server.router)
app.include_router(vms.router)
app.include_router(pods.router)
app.include_router(clients.router)
app.include_router(topology.router)
app.include_router(meta.router)

app.mount("/", StaticFiles(directory="static", html=True), name="static")
