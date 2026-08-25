import asyncio
import ipaddress
import json
import logging
import re
import time

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


_MEMINFO_LINE_RE = re.compile(r"^(\w+):\s+(\d+) kB", re.MULTILINE)


def parse_linux_meminfo_used_bytes(meminfo_text: str) -> int | None:
    """Computes real used memory from a Linux `/proc/meminfo` dump, the
    same way `free`'s "used" column does: MemTotal - MemAvailable, not
    MemTotal - MemFree. MemFree alone is misleadingly low because Linux
    opportunistically fills spare RAM with reclaimable disk cache/buffers
    that isn't real memory pressure -- MemAvailable already accounts for
    that reclaim, so the resulting "used" tracks what's actually pinned
    by applications. This is also what Proxmox's own balloon-based `mem`
    stat gets wrong: the balloon driver only exposes total/free, not the
    cache/buffers split, so it counts all of that reclaimable cache as
    "used" too.
    """
    values = {m.group(1): int(m.group(2)) for m in _MEMINFO_LINE_RE.finditer(meminfo_text)}
    total_kb = values.get("MemTotal")
    available_kb = values.get("MemAvailable")
    if total_kb is None or available_kb is None:
        return None
    return (total_kb - available_kb) * 1024


def parse_windows_meminfo_used_bytes(wmi_json_text: str) -> int | None:
    """Computes used memory from a Win32_OperatingSystem WMI query's JSON
    output (TotalVisibleMemorySize/FreePhysicalMemory, both in KB).
    """
    try:
        data = json.loads(wmi_json_text)
        total_kb = data["TotalVisibleMemorySize"]
        free_kb = data["FreePhysicalMemory"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return None
    return (total_kb - free_kb) * 1024


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
        # Guest OS rarely/never changes for a running VM's lifetime, so
        # cache it instead of paying a guest-agent round trip every poll.
        self._os_id_cache: dict[int, str] = {}

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
        result = resp.json()["data"]
        # The list API doesn't guarantee stable ordering between calls;
        # sort so the UI doesn't reshuffle entities on every poll.
        result.sort(key=lambda item: item["storage"])
        return result

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

    async def get_guest_os_id(self, vmid: int) -> str | None:
        """Returns the guest-agent-reported OS id (e.g. "debian",
        "mswindows"), cached per VM since it doesn't change at runtime.
        """
        cached = self._os_id_cache.get(vmid)
        if cached:
            return cached
        try:
            resp = await self._client.get(f"/nodes/{self.node}/qemu/{vmid}/agent/get-osinfo")
            resp.raise_for_status()
            os_id = resp.json()["data"]["result"]["id"]
        except (httpx.HTTPStatusError, httpx.RequestError, KeyError, TypeError):
            return None
        self._os_id_cache[vmid] = os_id
        return os_id

    async def exec_guest_command(
        self, vmid: int, command: list[str], timeout: float = 5.0
    ) -> str | None:
        """Runs `command` inside the VM via the guest agent and returns
        its stdout, or None if the agent's unreachable, the command
        fails, or it doesn't finish within `timeout` seconds. QEMU
        guest-exec is async (start, then poll for completion), unlike
        every other guest-agent call this client makes.
        """
        try:
            resp = await self._client.post(
                f"/nodes/{self.node}/qemu/{vmid}/agent/exec",
                json={"command": command},
            )
            resp.raise_for_status()
            pid = resp.json()["data"]["pid"]
        except (httpx.HTTPStatusError, httpx.RequestError, KeyError, TypeError):
            return None

        deadline = time.monotonic() + timeout
        while True:
            try:
                resp = await self._client.get(
                    f"/nodes/{self.node}/qemu/{vmid}/agent/exec-status",
                    params={"pid": pid},
                )
                resp.raise_for_status()
                status = resp.json()["data"]
            except (httpx.HTTPStatusError, httpx.RequestError, KeyError, TypeError):
                return None
            if status.get("exited"):
                return status.get("out-data") if status.get("exitcode") == 0 else None
            if time.monotonic() >= deadline:
                return None
            await asyncio.sleep(0.2)

    async def get_vm_real_memory_used(self, vmid: int) -> int | None:
        """Real "in use" memory from inside the guest (excludes
        reclaimable disk cache/buffers, unlike Proxmox's own balloon-
        reported `mem` stat -- see parse_linux_meminfo_used_bytes).
        Returns None on any failure so the caller can fall back to the
        Proxmox-reported figure.
        """
        os_id = await self.get_guest_os_id(vmid)
        if os_id is None:
            return None
        if os_id == "mswindows":
            output = await self.exec_guest_command(
                vmid,
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-Command",
                    "Get-CimInstance Win32_OperatingSystem | "
                    "Select-Object TotalVisibleMemorySize,FreePhysicalMemory | ConvertTo-Json",
                ],
            )
            return parse_windows_meminfo_used_bytes(output) if output else None
        output = await self.exec_guest_command(vmid, ["cat", "/proc/meminfo"])
        return parse_linux_meminfo_used_bytes(output) if output else None

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
                # Prefer the guest's own real-usage figure (excludes
                # reclaimable cache/buffers); fall back to Proxmox's
                # balloon-reported value if the guest-exec path fails.
                real_mem_used = await self.get_vm_real_memory_used(vmid)
            else:
                ip_addresses, agent_reachable = [], False
                disk_used_bytes, disk_total_bytes = None, None
                real_mem_used = None
            results.append(
                {
                    "vmid": vmid,
                    "name": vm.get("name", f"vm-{vmid}"),
                    "status": vm.get("status", "unknown"),
                    "cpu": status.get("cpu", 0.0),
                    "maxmem": status.get("maxmem", 0),
                    "mem": real_mem_used if real_mem_used is not None else status.get("mem", 0),
                    "ip_addresses": ip_addresses,
                    "agent_reachable": agent_reachable,
                    "disk_used_bytes": disk_used_bytes,
                    "disk_total_bytes": disk_total_bytes,
                }
            )
        return results
