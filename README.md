# Monitoring Proxmox K8s

A cute pastel pink-and-white monitoring dashboard for a Proxmox VE host and its k3s
cluster. Runs as its own microservice inside k3s, reachable only on the LAN.

Shows:
- Overall physical server CPU/RAM totals vs. current utilization.
- Per-VM CPU/RAM (with LAN IPs, via the QEMU guest agent).
- Per-microservice (k3s pod) CPU/RAM.
- Every other device on the LAN (phones, laptops, IoT, ...), if a FRITZ!Box router is
  configured -- name, IP, MAC, online/offline.
- An auto-discovered network plan: gateway → Proxmox host → VMs → k3s node →
  pods/services, plus any LAN devices above.

## Architecture

- **Backend**: Python (FastAPI). Polls the Proxmox VE REST API and the Kubernetes API
  (+ metrics-server) on an interval, caches the latest snapshot, and serves it as JSON.
- **Frontend**: Static HTML/CSS/vanilla JS served by the same app. No build step.
- **Deploy**: Single container image, deployed into a dedicated `monitoring` namespace
  in the existing k3s cluster, exposed via a `NodePort` service.

See `app/` for the backend, `static/` for the frontend, `deploy/k8s/` for manifests.

## Setup checklist

Before deploying, you'll need:

1. **Proxmox API token** — Datacenter → Permissions → API Tokens → create one with the
   `PVEAuditor` role (read-only). Note the token ID (e.g. `monitoring@pve!dashboard`)
   and secret.
2. **QEMU Guest Agent** enabled on both VMs (VM → Options → QEMU Guest Agent, plus
   `qemu-guest-agent` installed inside Debian, and the VirtIO guest tools on Windows 11).
   Confirms with `qm agent <vmid> ping` from the Proxmox host. This is what lets the
   dashboard discover each VM's real LAN IP.
3. **LAN gateway IP** — your router's IP, used as the one manually-seeded node in the
   network plan (everything else is auto-discovered).
4. Fill in `deploy/k8s/configmap.yaml` (`PROXMOX_HOST`, `PROXMOX_NODE`,
   `WATCHED_NAMESPACES`, `LAN_GATEWAY_IP`, `LAN_GATEWAY_LABEL`).
5. Copy `deploy/k8s/secret.example.yaml` to `deploy/k8s/secret.yaml`, fill in the real
   Proxmox token, and **do not commit it** (already gitignored).
6. *(Optional)* LAN device discovery, if your router is a FRITZ!Box: create a dedicated
   FRITZ!Box user (Home Network → Network → Network Settings → FRITZ!Box Users, or
   System → FRITZ!Box Users) rather than reusing your admin login, and set
   `FRITZBOX_USERNAME` / `FRITZBOX_PASSWORD` in `secret.yaml`. Uses `LAN_GATEWAY_IP` as
   the router address unless `FRITZBOX_HOST` is set separately. Leave both blank to skip
   this entirely -- the dashboard works the same without it.

## Build & deploy (on/for the Debian k3s VM)

```bash
# Build the image
docker build -t monitoring-dashboard:latest .

# Get it into k3s's containerd without a registry
docker save monitoring-dashboard:latest | ctr -n k8s.io images import -

# Apply manifests (namespace first, secret last is fine, order below is just tidy)
kubectl apply -f deploy/k8s/namespace.yaml
kubectl apply -f deploy/k8s/serviceaccount.yaml
kubectl apply -f deploy/k8s/clusterrole.yaml
kubectl apply -f deploy/k8s/clusterrolebinding.yaml
kubectl apply -f deploy/k8s/configmap.yaml
kubectl apply -f deploy/k8s/secret.yaml
kubectl apply -f deploy/k8s/deployment.yaml
kubectl apply -f deploy/k8s/service.yaml

kubectl -n monitoring get pods -w
```

Once the pod is `Running` and ready, browse to `http://<debian-vm-lan-ip>:30080` from
any device on your LAN.

## Local development

Run the API directly against your real Proxmox host, with `K8S_IN_CLUSTER=false` to
fall back to your local `~/.kube/config` (works if `kubectl` already talks to the
cluster from wherever you're running this):

```bash
python -m venv .venv && .venv/Scripts/activate  # or source .venv/bin/activate
pip install -r requirements.txt

export PROXMOX_HOST=https://192.168.1.10:8006
export PROXMOX_NODE=pve
export PROXMOX_TOKEN_ID=monitoring@pve!dashboard
export PROXMOX_TOKEN_SECRET=...
export LAN_GATEWAY_IP=192.168.1.1
export K8S_IN_CLUSTER=false

uvicorn app.main:app --reload --port 8080
```

Then browse `http://localhost:8080`.

### Tests

```bash
pip install pytest
pytest tests/
```
