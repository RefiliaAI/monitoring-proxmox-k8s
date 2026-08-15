from typing import Literal

from pydantic import BaseModel


class ServerStats(BaseModel):
    node_name: str
    cpu_cores: int
    cpu_used_percent: float
    mem_total_bytes: int
    mem_used_bytes: int
    mem_used_percent: float
    # Represents the largest configured storage pool, not a sum across
    # pools -- some backends (e.g. a ZFS pool plus a "dir" storage on
    # that same pool) share physical capacity, so summing would double
    # count. See StoragePool for the individual, unambiguous per-pool
    # figures.
    disk_pool_name: str | None = None
    disk_total_bytes: int | None = None
    disk_used_bytes: int | None = None
    disk_used_percent: float | None = None
    updated_at: str
    stale: bool = False


class StoragePool(BaseModel):
    name: str
    type: str
    content: str
    active: bool
    total_bytes: int
    used_bytes: int
    avail_bytes: int
    used_percent: float
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
    disk_used_bytes: int | None = None
    disk_total_bytes: int | None = None
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
    pvc_name: str | None = None
    pvc_capacity_bytes: int | None = None
    pvc_storage_class: str | None = None
    updated_at: str
    stale: bool = False


class ClientDevice(BaseModel):
    ip: str
    name: str
    mac: str
    active: bool
    interface_type: str
    # The router's API only reports current on/off state, not history --
    # this is tracked by us across poll cycles (see poller.py), so it's
    # only as complete as how long this pod has been running.
    last_active_at: str | None = None
    updated_at: str
    stale: bool = False


class TopologyNode(BaseModel):
    id: str
    type: Literal[
        "gateway", "proxmox_host", "vm", "k8s_node", "k8s_pod", "k8s_service", "client"
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
