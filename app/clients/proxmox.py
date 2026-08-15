import ipaddress
import logging

import httpx

logger = logging.getLogger(__name__)

# Interface name prefixes that are never a VM's real LAN-facing IP.
_IGNORED_IFACE_PREFIXES = ("lo", "docker0", "cni0", "flannel", "veth", "virbr")


def _iface_is_ignored(name: str) -> bool:
    return name.lower().startswith(_IGNORED_IFACE_PREFIXES)


def pick_lan_ip(interfaces: list[dict], reference_host: str | None = None) -> list[str]:
    """Pick plausible LAN IPv4 addresses out of a QEMU guest-agent
    network-get-interfaces payload, dropping loopback/container/CNI
    interfaces that would otherwise be mistaken for the VM's real IP
    (this matters especially for the Debian VM, which also runs k3s
    and therefore has cni0/flannel.1/veth* interfaces of its own).
    """
    reference_net = None
    if reference_host:
        try:
            reference_net = ipaddress.ip_network(f"{reference_host}/24", strict=False)
        except ValueError:
            reference_net = None

    candidates: list[str] = []
    for iface in interfaces:
        name = iface.get("name", "")
        if _iface_is_ignored(name):
            continue
        for addr in iface.get("ip-addresses", []) or []:
            ip_str = addr.get("ip-address")
            if not ip_str or addr.get("ip-address-type") != "ipv4":
                continue
            try:
                ip_obj = ipaddress.ip_address(ip_str)
            except ValueError:
                continue
            if ip_obj.is_loopback or ip_obj.is_link_local:
                continue
            candidates.append(ip_str)

    if reference_net:
        in_subnet = [ip for ip in candidates if ipaddress.ip_address(ip) in reference_net]
        if in_subnet:
            return in_subnet + [ip for ip in candidates if ip not in in_subnet]

    return candidates


# Optical/removable media filesystems reported by guest-agent fsinfo that
# should never be picked as "the VM's disk" (mounted install ISOs, virtual
# CD-ROM drives, etc.).
_EXCLUDED_FS_TYPES = {"cdfs", "udf", "iso9660"}


def pick_primary_filesystem(fsinfo_result: list[dict]) -> tuple[int | None, int | None]:
    """Pick the filesystem that best represents "the VM's disk usage" out
    of a QEMU guest-agent get-fsinfo payload, which lists *every* mounted
    filesystem -- EFI/boot partitions, optical drives, "System Reserved"
    partitions with no size at all, and the real system/data drive all
    mixed together. Heuristic: exclude optical media and anything missing
    size fields, then take the one with the largest total-bytes (in
    practice this is always the real root/system drive, since boot/EFI/
    reserved partitions are always much smaller). Returns (used_bytes,
    total_bytes), or (None, None) if nothing usable was reported.
    """
    candidates = [
        fs
        for fs in fsinfo_result
        if fs.get("total-bytes") is not None
        and fs.get("used-bytes") is not None
        and (fs.get("type") or "").lower() not in _EXCLUDED_FS_TYPES
    ]
    if not candidates:
        return None, None
    primary = max(candidates, key=lambda fs: fs["total-bytes"])
    return primary["used-bytes"], primary["total-bytes"]


class ProxmoxClient:
    def __init__(self, host: str, node: str, token_id: str, token_secret: str, verify_ssl: bool = False):
        self.node = node
        self._reference_host = httpx.URL(host).host
        self.host = self._reference_host
        self._client = httpx.AsyncClient(
            base_url=f"{host.rstrip('/')}/api2/json",
            headers={"Authorization": f"PVEAPIToken={token_id}={token_secret}"},
            verify=verify_ssl,
            timeout=10.0,
        )

    async def aclose(self):
        await self._client.aclose()

    async def get_node_status(self) -> dict:
        resp = await self._client.get(f"/nodes/{self.node}/status")
        resp.raise_for_status()
        return resp.json()["data"]

    async def list_vms(self) -> list[dict]:
        resp = await self._client.get(f"/nodes/{self.node}/qemu")
        resp.raise_for_status()
        return resp.json()["data"]

    async def list_storage(self) -> list[dict]:
        resp = await self._client.get(f"/nodes/{self.node}/storage")
        resp.raise_for_status()
        return resp.json()["data"]

    async def get_vm_status(self, vmid: int) -> dict:
        resp = await self._client.get(f"/nodes/{self.node}/qemu/{vmid}/status/current")
        resp.raise_for_status()
        return resp.json()["data"]

    async def get_vm_ip_addresses(self, vmid: int) -> tuple[list[str], bool]:
        """Returns (ip_addresses, agent_reachable). Degrades gracefully
        when the guest agent isn't installed/running/responding yet
        rather than raising, since that's an expected transient state.
        """
        try:
            resp = await self._client.get(
                f"/nodes/{self.node}/qemu/{vmid}/agent/network-get-interfaces"
            )
            resp.raise_for_status()
            interfaces = resp.json()["data"]["result"]
        except (httpx.HTTPStatusError, httpx.RequestError, KeyError, TypeError):
            return [], False
        return pick_lan_ip(interfaces, self._reference_host), True

    async def get_vm_disk_usage(self, vmid: int) -> tuple[int | None, int | None]:
        """Returns (used_bytes, total_bytes) for the VM's primary
        filesystem via the guest agent, degrading to (None, None) the
        same way get_vm_ip_addresses does when the agent isn't reachable.
        """
        try:
            resp = await self._client.get(f"/nodes/{self.node}/qemu/{vmid}/agent/get-fsinfo")
            resp.raise_for_status()
            fsinfo = resp.json()["data"]["result"]
        except (httpx.HTTPStatusError, httpx.RequestError, KeyError, TypeError):
            return None, None
        return pick_primary_filesystem(fsinfo)

    async def fetch_all_vm_stats(self) -> list[dict]:
        # The list API doesn't guarantee stable ordering between calls;
        # sort so the UI doesn't reshuffle entities on every poll.
        vms = sorted(await self.list_vms(), key=lambda vm: vm["vmid"])
        results = []
        for vm in vms:
            vmid = vm["vmid"]
            status = vm
            if vm.get("status") == "running":
                try:
                    status = await self.get_vm_status(vmid)
                except (httpx.HTTPStatusError, httpx.RequestError):
                    status = vm
                ip_addresses, agent_reachable = await self.get_vm_ip_addresses(vmid)
                disk_used_bytes, disk_total_bytes = await self.get_vm_disk_usage(vmid)
            else:
                ip_addresses, agent_reachable = [], False
                disk_used_bytes, disk_total_bytes = None, None
            results.append(
                {
                    "vmid": vmid,
                    "name": vm.get("name", f"vm-{vmid}"),
                    "status": vm.get("status", "unknown"),
                    "cpu": status.get("cpu", 0.0),
                    "maxmem": status.get("maxmem", 0),
                    "mem": status.get("mem", 0),
                    "ip_addresses": ip_addresses,
                    "agent_reachable": agent_reachable,
                    "disk_used_bytes": disk_used_bytes,
                    "disk_total_bytes": disk_total_bytes,
                }
            )
        return results
