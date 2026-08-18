from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Proxmox
    proxmox_host: str
    proxmox_node: str
    proxmox_token_id: str
    proxmox_token_secret: str
    proxmox_verify_ssl: bool = False

    # Kubernetes -- every namespace is watched by default (so new ones
    # show up automatically with no config change) except these, which
    # are Kubernetes/k3s's own system namespaces rather than anything a
    # user deploys.
    k8s_in_cluster: bool = True
    excluded_namespaces: str = "kube-system,kube-public,kube-node-lease"

    # Topology seed node (not discoverable via any API)
    lan_gateway_ip: str
    lan_gateway_label: str = "Home Router"

    # FRITZ!Box (optional) -- lists every LAN client via the router's
    # TR-064 API. Client discovery is simply skipped if unset.
    fritzbox_host: str | None = None
    fritzbox_username: str | None = None
    fritzbox_password: str | None = None
    # Devices not seen online within this window are hidden from the
    # default client list/topology (still available via ?all=true).
    client_recent_hours: int = 24

    @property
    def fritzbox_enabled(self) -> bool:
        return bool(self.fritzbox_username and self.fritzbox_password)

    # Poller
    refresh_interval_seconds: int = 15

    # Server
    port: int = 8080

    @property
    def excluded_namespaces_list(self) -> list[str]:
        return [ns.strip() for ns in self.excluded_namespaces.split(",") if ns.strip()]


settings = Settings()
