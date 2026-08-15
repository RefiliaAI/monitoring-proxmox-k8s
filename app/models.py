from typing import Literal

from pydantic import BaseModel


class ServerStats(BaseModel):
    node_name: str
    cpu_cores: int
    cpu_used_percent: float
    mem_total_bytes: int
    mem_used_bytes: int
    mem_used_percent: float
    updated_at: str
    stale: bool = False


class VMStat(BaseModel):
    vmid: int
    name: str
    status: str
    cpu_percent: float
    mem_used_bytes: int
    mem_total_bytes: int
    ip_addresses: list[str] = []
    agent_reachable: bool = False
    updated_at: str
    stale: bool = False


class PodStat(BaseModel):
    namespace: str
    name: str
    display_name: str
    node: str | None
    cpu_millicores: int | None
    mem_bytes: int | None
    cpu_limit_millicores: int | None = None
    mem_limit_bytes: int | None = None
    pod_ip: str | None
    status: str
    updated_at: str
    stale: bool = False


class TopologyNode(BaseModel):
    id: str
    type: Literal[
        "gateway", "proxmox_host", "vm", "k8s_node", "k8s_pod", "k8s_service"
    ]
    label: str
    ip: str | None = None
    status: Literal["ok", "warn", "critical", "unknown"] = "unknown"
    meta: dict = {}


class TopologyEdge(BaseModel):
    source: str
    target: str
    type: Literal["network", "hosts", "runs_on", "exposes"]


class Topology(BaseModel):
    nodes: list[TopologyNode]
    edges: list[TopologyEdge]
    updated_at: str
