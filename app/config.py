from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Proxmox
    proxmox_host: str
    proxmox_node: str
    proxmox_token_id: str
    proxmox_token_secret: str
    proxmox_verify_ssl: bool = False

    # Kubernetes
    k8s_in_cluster: bool = True
    watched_namespaces: str = "default"

    # Topology seed node (not discoverable via any API)
    lan_gateway_ip: str
    lan_gateway_label: str = "Home Router"

    # FRITZ!Box (optional) -- lists every LAN client via the router's
    # TR-064 API. Client discovery is simply skipped if unset.
    fritzbox_host: str | None = None
    fritzbox_username: str | None = None
    fritzbox_password: str | None = None

    @property
    def fritzbox_enabled(self) -> bool:
        return bool(self.fritzbox_username and self.fritzbox_password)

    # Poller
    refresh_interval_seconds: int = 15

    # Server
    port: int = 8080

    @property
    def watched_namespaces_list(self) -> list[str]:
        return [ns.strip() for ns in self.watched_namespaces.split(",") if ns.strip()]


settings = Settings()
