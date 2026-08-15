import logging

from fritzconnection import FritzConnection
from fritzconnection.core.exceptions import FritzConnectionException
from fritzconnection.lib.fritzhosts import FritzHosts

logger = logging.getLogger(__name__)


class FritzBoxClient:
    """Wraps the router's TR-064 API to list every known LAN client
    (phones, laptops, IoT devices, ...), not just the infrastructure this
    dashboard already knows about from Proxmox/Kubernetes.
    """

    def __init__(self, host: str, username: str, password: str):
        self._connection = FritzConnection(address=host, user=username, password=password)
        self._hosts = FritzHosts(fc=self._connection)

    def get_client_devices(self) -> list[dict]:
        """Returns every host the router has ever leased/seen, each with
        an `active` flag for whether it's currently connected. This is a
        synchronous, blocking network call -- callers on the asyncio
        event loop should run it via asyncio.to_thread.
        """
        try:
            hosts = self._hosts.get_hosts_info()
        except FritzConnectionException:
            logger.exception("FRITZ!Box host list query failed")
            return []
        return [
            {
                "ip": h["ip"],
                "name": h["name"],
                "mac": h["mac"],
                "active": bool(h["status"]),
                "interface_type": h["interface_type"],
            }
            for h in hosts
            if h.get("ip")
        ]
