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

    async def fetch_all_vm_stats(self) -> list[dict]:
        vms = await self.list_vms()
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
            else:
                ip_addresses, agent_reachable = [], False
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
                }
            )
        return results
