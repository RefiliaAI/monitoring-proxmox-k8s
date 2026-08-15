import asyncio
from dataclasses import dataclass, field

from app.models import PodStat, ServerStats, Topology, VMStat


@dataclass
class SnapshotCache:
    server: ServerStats | None = None
    vms: list[VMStat] = field(default_factory=list)
    pods: list[PodStat] = field(default_factory=list)
    topology: Topology | None = None
    ready: bool = False
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def update(
        self,
        server: ServerStats | None,
        vms: list[VMStat] | None,
        pods: list[PodStat] | None,
        topology: Topology | None,
    ) -> None:
        async with self._lock:
            if server is not None:
                self.server = server
            if vms is not None:
                self.vms = vms
            if pods is not None:
                self.pods = pods
            if topology is not None:
                self.topology = topology
            self.ready = True


cache = SnapshotCache()
