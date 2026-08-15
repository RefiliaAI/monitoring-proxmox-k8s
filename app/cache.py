import asyncio
from dataclasses import dataclass, field

from app.models import ClientDevice, PodStat, ServerStats, StoragePool, Topology, VMStat


@dataclass
class SnapshotCache:
    server: ServerStats | None = None
    vms: list[VMStat] = field(default_factory=list)
    pods: list[PodStat] = field(default_factory=list)
    clients: list[ClientDevice] = field(default_factory=list)
    storage_pools: list[StoragePool] = field(default_factory=list)
    topology: Topology | None = None
    ready: bool = False
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def update(
        self,
        server: ServerStats | None,
        vms: list[VMStat] | None,
        pods: list[PodStat] | None,
        clients: list[ClientDevice] | None,
        storage_pools: list[StoragePool] | None,
        topology: Topology | None,
    ) -> None:
        async with self._lock:
            if server is not None:
                self.server = server
            if vms is not None:
                self.vms = vms
            if pods is not None:
                self.pods = pods
            if clients is not None:
                self.clients = clients
            if storage_pools is not None:
                self.storage_pools = storage_pools
            if topology is not None:
                self.topology = topology
            self.ready = True


cache = SnapshotCache()
