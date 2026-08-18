import logging
import re

from kubernetes import client, config
from kubernetes.client.rest import ApiException

logger = logging.getLogger(__name__)

_TRAILING_HASH_SUFFIX = re.compile(r"-[a-z0-9]+$")


def _derive_display_name(pod_name: str, owner_references) -> str:
    """Strip the ReplicaSet/pod-template hash noise from a pod's generated
    name (e.g. "e2e-extended-74d6c86cbb-f6q68" -> "e2e-extended") so the UI
    shows the workload name instead of an opaque hash. Uses the pod's owner
    reference rather than guessing from the pod name alone, since that's
    the only reliable signal without an extra Deployment/apps API call:

    - ReplicaSet owner (Deployment-managed pod): the ReplicaSet's own name
      is "<deployment>-<hash>" -- strip its one trailing hash segment.
    - DaemonSet/StatefulSet owner: the owner reference name is already the
      clean workload name (StatefulSet pod names like "myapp-0" are kept
      as-is since the ordinal is meaningful, not noise).
    - No owner, or the strip doesn't change anything: fall back to the
      pod's own name.
    """
    if not owner_references:
        return pod_name
    owner = owner_references[0]
    if owner.kind == "ReplicaSet":
        stripped = _TRAILING_HASH_SUFFIX.sub("", owner.name)
        return stripped or pod_name
    if owner.kind in ("DaemonSet", "Job"):
        return owner.name
    return pod_name


def _parse_cpu_to_millicores(cpu_str: str) -> int:
    cpu_str = cpu_str.strip()
    if cpu_str.endswith("n"):
        return max(1, int(cpu_str[:-1]) // 1_000_000)
    if cpu_str.endswith("u"):
        return max(1, int(cpu_str[:-1]) // 1_000)
    if cpu_str.endswith("m"):
        return int(cpu_str[:-1])
    return int(float(cpu_str) * 1000)


def _parse_mem_to_bytes(mem_str: str) -> int:
    mem_str = mem_str.strip()
    units = {
        "Ki": 1024,
        "Mi": 1024**2,
        "Gi": 1024**3,
        "Ti": 1024**4,
        "K": 1000,
        "M": 1000**2,
        "G": 1000**3,
        "T": 1000**4,
    }
    for suffix, multiplier in units.items():
        if mem_str.endswith(suffix):
            return int(float(mem_str[: -len(suffix)]) * multiplier)
    return int(mem_str)


class K8sClient:
    def __init__(self, in_cluster: bool = True):
        try:
            if in_cluster:
                config.load_incluster_config()
            else:
                config.load_kube_config()
        except config.ConfigException:
            config.load_kube_config()
        self.core = client.CoreV1Api()
        self.custom = client.CustomObjectsApi()

    def list_nodes(self) -> list[dict]:
        nodes = self.core.list_node()
        result = []
        for n in nodes.items:
            internal_ip = next(
                (a.address for a in n.status.addresses if a.type == "InternalIP"), None
            )
            result.append(
                {
                    "name": n.metadata.name,
                    "internal_ip": internal_ip,
                    "capacity": n.status.capacity,
                    "allocatable": n.status.allocatable,
                }
            )
        # The list API doesn't guarantee stable ordering between calls;
        # sort so the UI doesn't reshuffle entities on every poll.
        result.sort(key=lambda item: item["name"])
        return result

    def list_pods(self, excluded_namespaces: list[str]) -> list[dict]:
        pods = self.core.list_pod_for_all_namespaces()
        result = []
        for p in pods.items:
            if p.metadata.namespace in excluded_namespaces:
                continue
            limits = {}
            requests = {}
            for c in p.spec.containers or []:
                if c.resources:
                    if c.resources.limits:
                        limits.update(c.resources.limits)
                    if c.resources.requests:
                        requests.update(c.resources.requests)
            pvc_claim_name = None
            for v in p.spec.volumes or []:
                if v.persistent_volume_claim:
                    pvc_claim_name = v.persistent_volume_claim.claim_name
                    break
            result.append(
                {
                    "namespace": p.metadata.namespace,
                    "name": p.metadata.name,
                    "display_name": _derive_display_name(
                        p.metadata.name, p.metadata.owner_references
                    ),
                    "labels": p.metadata.labels or {},
                    "node": p.spec.node_name,
                    "pod_ip": p.status.pod_ip,
                    "status": p.status.phase,
                    "cpu_limit_millicores": (
                        _parse_cpu_to_millicores(limits["cpu"]) if limits.get("cpu") else None
                    ),
                    "mem_limit_bytes": (
                        _parse_mem_to_bytes(limits["memory"]) if limits.get("memory") else None
                    ),
                    "pvc_claim_name": pvc_claim_name,
                }
            )
        # The list API doesn't guarantee stable ordering between calls;
        # sort so the UI doesn't reshuffle entities on every poll.
        result.sort(key=lambda item: (item["namespace"], item["display_name"], item["name"]))
        return result

    def list_services(self) -> list[dict]:
        services = self.core.list_service_for_all_namespaces()
        result = []
        for s in services.items:
            result.append(
                {
                    "namespace": s.metadata.namespace,
                    "name": s.metadata.name,
                    "cluster_ip": s.spec.cluster_ip,
                    "selector": s.spec.selector or {},
                }
            )
        # The list API doesn't guarantee stable ordering between calls;
        # sort so the UI doesn't reshuffle entities on every poll.
        result.sort(key=lambda item: (item["namespace"], item["name"]))
        return result

    def list_pvcs(self) -> dict[tuple[str, str], dict]:
        pvcs = self.core.list_persistent_volume_claim_for_all_namespaces()
        result = {}
        for p in pvcs.items:
            capacity = (p.status.capacity or {}).get("storage")
            result[(p.metadata.namespace, p.metadata.name)] = {
                "capacity_bytes": _parse_mem_to_bytes(capacity) if capacity else None,
                "storage_class": p.spec.storage_class_name,
            }
        return result

    def get_node_metrics(self) -> dict[str, dict]:
        try:
            data = self.custom.list_cluster_custom_object("metrics.k8s.io", "v1beta1", "nodes")
        except ApiException as exc:
            logger.warning("metrics-server node metrics unavailable: %s", exc)
            return {}
        result = {}
        for item in data.get("items", []):
            name = item["metadata"]["name"]
            usage = item.get("usage", {})
            result[name] = {
                "cpu_millicores": _parse_cpu_to_millicores(usage.get("cpu", "0")),
                "mem_bytes": _parse_mem_to_bytes(usage.get("memory", "0")),
            }
        return result

    def get_pod_metrics(self) -> dict[tuple[str, str], dict]:
        """Cluster-wide, like get_node_metrics -- metrics.k8s.io exposes
        pod metrics across every namespace in one call (the same thing
        `kubectl top pods -A` uses), so there's no need to loop per
        namespace or know the namespace list in advance.
        """
        result: dict[tuple[str, str], dict] = {}
        try:
            data = self.custom.list_cluster_custom_object("metrics.k8s.io", "v1beta1", "pods")
        except ApiException as exc:
            logger.warning("metrics-server pod metrics unavailable: %s", exc)
            return result
        for item in data.get("items", []):
            namespace = item["metadata"]["namespace"]
            name = item["metadata"]["name"]
            cpu_total = 0
            mem_total = 0
            for c in item.get("containers", []):
                usage = c.get("usage", {})
                cpu_total += _parse_cpu_to_millicores(usage.get("cpu", "0"))
                mem_total += _parse_mem_to_bytes(usage.get("memory", "0"))
            result[(namespace, name)] = {"cpu_millicores": cpu_total, "mem_bytes": mem_total}
        return result
