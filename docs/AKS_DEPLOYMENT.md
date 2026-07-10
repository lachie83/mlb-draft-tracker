# AKS Production Deployment

A cost-minimized, security-hardened Azure Kubernetes Service deployment of
this tracker for the 2026 MLB Draft weekend, with durable storage, a public
HTTPS endpoint via cert-manager/Let's Encrypt, and a fully automated
draft-day operational loop.

You run every command in this doc yourself against your own Azure
subscription, container registry, and domain — nothing here is executed on
your behalf.

## Contents
- [Architecture](#architecture)
- [Cost estimate](#cost-estimate)
- [Prerequisites](#prerequisites)
- [1. Provision AKS](#1-provision-aks)
- [2. Install ingress-nginx and cert-manager](#2-install-ingress-nginx-and-cert-manager)
- [3. Build and push the image](#3-build-and-push-the-image)
- [4. Deploy the app](#4-deploy-the-app)
- [5. DNS and certificate verification](#5-dns-and-certificate-verification)
- [6. Load pre-draft data](#6-load-pre-draft-data)
- [7. Draft-day plan](#7-draft-day-plan)
- [8. Security notes](#8-security-notes)
- [9. Cost monitoring and teardown](#9-cost-monitoring-and-teardown)
- [Troubleshooting](#troubleshooting)

## Architecture
```
Internet
  │  HTTPS (443)
  ▼
Azure Standard LB + Public IP  ──►  ingress-nginx  ──►  Service (ClusterIP)
                                         ▲                     │
                                    cert-manager               ▼
                                (Let's Encrypt HTTP-01)   Pod (1 replica)
                                                           ┌───────────────┐
                                                           │ dashboard      │  serves the UI
                                                           │ automation     │  pre_draft_sync.sh
                                                           │                │  + poll_draft_day.sh,
                                                           │                │  continuously
                                                           └───────┬────────┘
                                                                   │ RWO
                                                                   ▼
                                                        PersistentVolumeClaim
                                                        (Azure Disk, 5Gi)
```
One Deployment, one pod, two containers sharing one PVC (see
`k8s/05-deployment.yaml` for why: SQLite is single-writer and the PVC is
ReadWriteOnce, so everything that touches the database has to run on the
same node — trivially true when it's the same pod). The `dashboard`
container is stateless/read-mostly and is what the Ingress points at; the
`automation` container runs `scripts/k8s_automation.sh`, which is the
containerized equivalent of manually running `pre_draft_sync.sh` on a
schedule and `poll_draft_day.sh` continuously per `docs/OPERATIONS.md` §3 —
see [§7](#7-draft-day-plan).

## Cost estimate
Pay-as-you-go, no reserved capacity (appropriate for a few days of use).
Verify current prices for your region with the
[Azure Pricing Calculator](https://azure.microsoft.com/pricing/calculator/) —
these are ballpark figures.

| Resource | SKU | Est. $/month |
| --- | --- | --- |
| AKS control plane | Free tier | $0 |
| 1× node VM | Standard_B2s (2 vCPU, 4GB) | ~$30 |
| Node OS disk | 32GB Standard SSD | ~$3 |
| App data disk (PVC) | 5Gi managed-csi (Standard SSD) | ~$2 |
| Load Balancer | Standard | ~$18 |
| Public IP | Standard, static | ~$4 |
| Egress bandwidth | light traffic | ~$0–2 |
| **Total** | | **~$57–60/month** |

That leaves roughly $90/month of headroom under your $150 budget — e.g. to
size up to `Standard_B2ms` (~$60/mo node) if you want more RAM/CPU margin
and are still comfortably under budget. This deployment is designed to be
**torn down after the draft** ([§9](#9-cost-monitoring-and-teardown)); if
you only run it for a long weekend, actual spend will be a small fraction
of a full month's figures above.

Deliberately **not** using Spot/preemptible node pools: they're cheaper but
can be evicted at any time, which is the wrong tradeoff for something that
needs to be up during a live draft window.

## Prerequisites
- Azure CLI (`az`), logged in (`az login`) with a subscription that has
  quota for AKS + a B-series VM
- `kubectl`
- `helm`
- A container registry (you said you'll supply one). If it's Azure
  Container Registry, note its login server, e.g. `myregistry.azurecr.io`
- A domain name you control, so you can point a DNS record at the
  ingress's public IP
- Telegram bot token + chat id (see `docs/OPERATIONS.md` §7 if not done yet)

Throughout, replace the placeholders below with your own values:
`<RESOURCE_GROUP>`, `<LOCATION>` (e.g. `eastus`), `<AKS_CLUSTER_NAME>`,
`<ACR_NAME>`, `<ACR_LOGIN_SERVER>`, `<YOUR_DOMAIN>`, `<YOUR_EMAIL>`.

## 1. Provision AKS
```bash
az group create --name <RESOURCE_GROUP> --location <LOCATION>

az aks create \
  --resource-group <RESOURCE_GROUP> \
  --name <AKS_CLUSTER_NAME> \
  --tier free \
  --node-count 1 \
  --node-vm-size Standard_B2s \
  --node-osdisk-type Managed \
  --node-osdisk-size 32 \
  --enable-managed-identity \
  --generate-ssh-keys \
  --network-plugin azure

# Grant the cluster pull access to your registry (skip if already attached)
az aks update \
  --resource-group <RESOURCE_GROUP> \
  --name <AKS_CLUSTER_NAME> \
  --attach-acr <ACR_NAME>

az aks get-credentials --resource-group <RESOURCE_GROUP> --name <AKS_CLUSTER_NAME>
kubectl get nodes
```
`--attach-acr` grants the cluster's managed identity `AcrPull` on your
registry, so pods can pull images with **no `imagePullSecret` needed** — if
your registry isn't ACR, add an `imagePullSecret` to the `mlb-draft-tracker`
ServiceAccount in `k8s/04-serviceaccount.yaml` instead and reference it from
the Deployment.

## 2. Install ingress-nginx and cert-manager
```bash
helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx
helm repo add jetstack https://charts.jetstack.io
helm repo update

helm install ingress-nginx ingress-nginx/ingress-nginx \
  --namespace ingress-nginx --create-namespace \
  --set controller.service.externalTrafficPolicy=Local

helm install cert-manager jetstack/cert-manager \
  --namespace cert-manager --create-namespace \
  --set crds.enabled=true
```
`externalTrafficPolicy=Local` matters more than it looks: the chart's
default (`Cluster`) makes Azure's cloud-provider configure the Standard
Load Balancer's backend rule with `EnableFloatingIP: true` (a
Direct-Server-Return-style rule). On at least one real deployment, that
specific rule got stuck on Azure's data plane — the control-plane API
showed everything correctly configured (NSG, backend pool, health probes),
traffic worked fine from inside the VNet, and it still failed identically
after swapping to a completely fresh node — while `Local` mode uses a
plain NodePort + `/healthz` probe that Azure's LB picked up correctly
within seconds. It also has the side benefit of preserving real client
IPs in nginx's access logs. If you ever need to change this after install,
`helm upgrade ingress-nginx ingress-nginx/ingress-nginx --namespace
ingress-nginx --reuse-values --set controller.service.externalTrafficPolicy=Local`
applies it without losing other settings.
Wait for the ingress controller's public IP (needed for DNS in §5):
```bash
kubectl get svc -n ingress-nginx ingress-nginx-controller \
  -w -o jsonpath='{.status.loadBalancer.ingress[0].ip}'
```
(Ctrl-C once an IP appears.)

## 3. Build and push the image
This app's default `Dockerfile` includes a full R/baseballr toolchain that
the AKS deployment doesn't use — build `Dockerfile.k8s` instead, which
covers the same MLB Stats API + no-R paths in a much smaller, non-root
image.

**Important**: build for `linux/amd64` even if you're on Apple Silicon —
`Standard_B2s` nodes are x86_64. The simplest way to avoid any
cross-architecture mistakes is to let ACR build it remotely. Pass
`GIT_COMMIT` as a build-arg so the running dashboard's header shows which
commit is actually deployed (`.git` is excluded from the build context, so
it can't be recovered any other way once inside the image):
```bash
az acr build \
  --registry <ACR_NAME> \
  --image mlb-draft-tracker:<TAG> \
  --file Dockerfile.k8s \
  --build-arg GIT_COMMIT=$(git rev-parse --short HEAD) \
  .
```
(If your registry isn't ACR, build locally with an explicit platform and
push yourself: `docker buildx build --platform linux/amd64 -f Dockerfile.k8s -t <ACR_LOGIN_SERVER>/mlb-draft-tracker:<TAG> --build-arg GIT_COMMIT=$(git rev-parse --short HEAD) --push .`)

Then update the two `image:` placeholders in `k8s/05-deployment.yaml` to
`<ACR_LOGIN_SERVER>/mlb-draft-tracker:<TAG>`.

## 4. Deploy the app
Fill in the placeholders first:
- `k8s/05-deployment.yaml`: both `image:` fields
- `k8s/07-ingress.yaml`: both `<YOUR_DOMAIN>` occurrences
- `k8s/08-cluster-issuer.yaml`: `<YOUR_EMAIL>`

Apply everything except the secret template:
```bash
kubectl apply -k k8s/
```

Create the real Telegram secret **imperatively** — never write real
credentials into a file that could get committed:
```bash
kubectl create secret generic telegram-credentials \
  --namespace mlb-draft-tracker \
  --from-literal=TELEGRAM_BOT_TOKEN='<your-real-token>' \
  --from-literal=TELEGRAM_CHAT_ID='<your-real-chat-id>'
```

Watch the rollout:
```bash
kubectl -n mlb-draft-tracker rollout status deployment/mlb-draft-tracker
kubectl -n mlb-draft-tracker get pods
kubectl -n mlb-draft-tracker logs -f deploy/mlb-draft-tracker -c automation
```
The `automation` container's first `pre_draft_sync.sh` run needs network
access and can take a minute; the pod is ready once `dashboard`'s readiness
probe passes.

## 5. DNS and certificate verification
Point your domain at the ingress controller's public IP from §2 (an `A`
record for `<YOUR_DOMAIN>` → that IP). Then:
```bash
kubectl -n mlb-draft-tracker describe certificate mlb-draft-tracker-tls
kubectl -n mlb-draft-tracker get certificate -w
```
Wait for `READY=True`. If it's stuck, see [Troubleshooting](#troubleshooting).
Once ready:
```bash
curl -I https://<YOUR_DOMAIN>/
```
should return `200` with a valid Let's Encrypt certificate. Plain HTTP
requests are redirected to HTTPS by the ingress-nginx annotations already
in `k8s/07-ingress.yaml`.

## 6. Load pre-draft data
Two things to load, per your request: the complete (real, historical) 2025
draft, and everything 2026 needs ready to go.

**2026 — already done automatically.** The `automation` container ran the
equivalent of `pre_draft_sync.sh` on startup (§4's rollout): draft order via
the MLB Stats API, the prospect board, heuristic predictions, and mock-draft
consensus predictions. Verify:
```bash
kubectl -n mlb-draft-tracker exec deploy/mlb-draft-tracker -c dashboard -- \
  python3 -c "
import sqlite3
c = sqlite3.connect('/app/data/mlb_draft_2026.db')
print('2026 draft_slots:', c.execute('select count(*) from draft_slots where draft_year=2026').fetchone()[0])
print('2026 prospects:', c.execute('select count(*) from prospects where draft_year=2026').fetchone()[0])
"
```

**2025 — load once, manually, with Telegram explicitly disabled for this
command.** This is important: `live-monitor-api` sends a Telegram alert for
every *newly seen* pick, and 2025 is a fully completed draft with ~615
picks — running it with real credentials attached would fire ~615 alerts
at your chat. Unset the credentials just for this one command with `env -u`:
```bash
kubectl -n mlb-draft-tracker exec deploy/mlb-draft-tracker -c automation -- \
  env -u TELEGRAM_BOT_TOKEN -u TELEGRAM_CHAT_ID \
  python3 main.py --db /app/data/mlb_draft_2026.db sync-draft-order-api --year 2025

kubectl -n mlb-draft-tracker exec deploy/mlb-draft-tracker -c automation -- \
  env -u TELEGRAM_BOT_TOKEN -u TELEGRAM_CHAT_ID \
  python3 main.py --db /app/data/mlb_draft_2026.db live-monitor-api --year 2025
```
Verify: `https://<YOUR_DOMAIN>/?year=2025` should show the complete board.

Confirm Telegram itself still works (this one *should* send, to your real
chat):
```bash
kubectl -n mlb-draft-tracker exec deploy/mlb-draft-tracker -c automation -- \
  python3 main.py test-telegram
```

**Rehearse the whole thing** (optional but recommended) using the same
technique as local rehearsals — see `docs/OPERATIONS.md` §4a — but note
`rehearse-draft-day` also uses `reconcile_picks_from_api` and will send real
alerts by default; keep `--picks` small (its default, 10) the first time:
```bash
kubectl -n mlb-draft-tracker exec deploy/mlb-draft-tracker -c automation -- \
  python3 main.py --db /app/data/mlb_draft_2026.db rehearse-draft-day
```
This writes to `draft_year=9999` in the *same* production database — safe
by design (see `main.py`'s `rehearse-draft-day` docstring/warning), but if
you'd rather not touch the production db at all for a rehearsal, point
`--db` at a throwaway path under `/app/data/` instead, e.g.
`/app/data/rehearsal.db`.

## 7. Draft-day plan
**The short version: you don't need to manually trigger anything.** The
`automation` container is already running `poll_draft_day.sh` continuously
(60s interval) and re-running `pre_draft_sync.sh` every 6 hours
(`PRE_DRAFT_SYNC_INTERVAL_SECONDS` in `k8s/02-configmap.yaml`) — that's the
automated equivalent of `docs/OPERATIONS.md` §3's suggested schedule. Your
job on the day is to **monitor**, and step in only if something's actually
broken.

**Before the draft (any time in the days leading up):**
- `https://<YOUR_DOMAIN>/` — confirm the board looks right
- `kubectl -n mlb-draft-tracker logs deploy/mlb-draft-tracker -c automation --tail=50` — confirm periodic syncs are succeeding
- Optionally shrink the refresh interval as the draft gets closer:
  `kubectl -n mlb-draft-tracker set env deployment/mlb-draft-tracker -c automation PRE_DRAFT_SYNC_INTERVAL_SECONDS=3600` (hourly) — this restarts the pod (see [§8](#8-security-notes) note on `strategy: Recreate`, brief downtime is expected)

**During the live draft:**
- Watch `https://<YOUR_DOMAIN>/` (it auto-refreshes if you enable the toggle) and your Telegram chat for pick alerts
- If you want a quick "who's on the clock" check without opening the dashboard:
  ```bash
  kubectl -n mlb-draft-tracker exec deploy/mlb-draft-tracker -c automation -- \
    python3 main.py on-the-clock-api --year 2026
  ```
- If picks seem to be lagging, check the poller's log directly:
  ```bash
  kubectl -n mlb-draft-tracker exec deploy/mlb-draft-tracker -c automation -- \
    tail -50 /app/data/live-monitor-2026.log
  ```
- Force an immediate full reconciliation (bypassing the 60s wait) if you're impatient or suspicious something was missed:
  ```bash
  kubectl -n mlb-draft-tracker exec deploy/mlb-draft-tracker -c automation -- \
    python3 main.py --db /app/data/mlb_draft_2026.db live-monitor-api --year 2026
  ```

**If something breaks:**
- `kubectl -n mlb-draft-tracker get pods` — is it even running? Kubernetes
  restarts the pod automatically on crash (`restartPolicy: Always` is the
  Deployment default); `k8s_automation.sh` itself exits non-zero if the
  background poller dies, so a dead poller becomes a container restart
  automatically rather than a silent hang.
- `kubectl -n mlb-draft-tracker logs deploy/mlb-draft-tracker -c automation --previous` — logs from before the last restart
- Full manual restart if needed: `kubectl -n mlb-draft-tracker rollout restart deployment/mlb-draft-tracker`

This mapping covers `docs/OPERATIONS.md` §3's full suggested schedule
without you needing to remote-trigger the individual commands yourself —
they're already running on a loop inside the cluster.

## 8. Security notes
What's already built into the manifests in `k8s/`, and why:
- **Non-root, no privilege escalation, all capabilities dropped**, default
  seccomp profile — every container in the pod, enforced twice over: by
  each container's `securityContext` and by the namespace's
  `pod-security.kubernetes.io/enforce: restricted` label
  (`k8s/00-namespace.yaml`), which is Kubernetes' strongest built-in Pod
  Security Standard
- **Read-only root filesystem** on both containers; the only writable paths
  are the PVC (`/app/data`) and a size-capped `emptyDir` at `/tmp`
- **Secrets never touch a file**: the Telegram credentials are created with
  `kubectl create secret ... --from-literal`, not applied from YAML;
  `k8s/01-secret.example.yaml` is a template with obvious placeholder text,
  intentionally excluded from `kustomization.yaml` so `kubectl apply -k`
  can't apply it by accident. Only the `automation` container gets the
  secret mounted (as env vars) — `dashboard` has no use for it and doesn't
  get it
- **No Kubernetes API access**: a dedicated ServiceAccount with
  `automountServiceAccountToken: false` — the app never needs to talk to
  the K8s API, so its token is never mounted
- **NetworkPolicy default-deny**: ingress only from the ingress-nginx
  namespace on the app port; egress only DNS + HTTPS (statsapi.mlb.com and
  api.telegram.org don't publish fixed IP ranges, so this is as tight as
  practical without breaking functionality)
- **HTTPS-only, enforced redirect**: `nginx.ingress.kubernetes.io/ssl-redirect`
  and `force-ssl-redirect` annotations on the Ingress
- **`strategy: Recreate`** on the Deployment: with a ReadWriteOnce PVC and
  SQLite's single-writer model, a rolling update that briefly runs two pods
  could corrupt writes or fail to mount; `Recreate` guarantees the old pod
  fully terminates before a new one starts, at the cost of a few seconds of
  downtime during any rollout — acceptable for this use case

What's intentionally out of scope here (call them out if you want them
added): pod-to-pod mTLS/service mesh, WAF in front of the ingress, image
vulnerability scanning (enable Microsoft Defender for Containers on the ACR
if you want this), and audit logging beyond AKS's defaults.

## 9. Cost monitoring and teardown
Check current spend any time via the **Cost Management** view in the Azure
Portal, scoped to `<RESOURCE_GROUP>` — this is the most reliable way to see
actual spend regardless of subscription type. If your subscription supports
the consumption API, the CLI equivalent is:
```bash
az consumption usage list --start-date <YYYY-MM-DD> --end-date <YYYY-MM-DD> \
  --query "[?contains(instanceName, '<AKS_CLUSTER_NAME>')]"
```
(Not all subscription types — e.g. some free/sponsored ones — support this
API; the Portal view always works.)

**Tear down everything once the draft is over** — this deletes the AKS
cluster, its node(s), the load balancer, the public IP, and both disks,
stopping all billing for this deployment:
```bash
az group delete --name <RESOURCE_GROUP> --yes --no-wait
```
Your data isn't lost even if you do this: the sqlite db in the PVC will be
deleted along with the disk, so if you want to keep it, copy it out first:
```bash
kubectl -n mlb-draft-tracker cp mlb-draft-tracker-<pod-suffix>:/app/data/mlb_draft_2026.db \
  -c dashboard ./mlb_draft_2026_final.db
```

## Troubleshooting
- **Certificate stuck, not `READY`**: `kubectl -n mlb-draft-tracker describe certificate mlb-draft-tracker-tls` and `kubectl -n mlb-draft-tracker describe challenge` — almost always either DNS not yet pointed at the ingress IP, or the ingress isn't reachable on port 80 from the internet yet (cert-manager's HTTP-01 solver needs plain HTTP to work first, before HTTPS exists)
- **`ImagePullBackOff`**: confirm `--attach-acr` succeeded (`az aks check-acr --name <AKS_CLUSTER_NAME> --resource-group <RESOURCE_GROUP> --acr <ACR_NAME>`) or that your `imagePullSecret` is correct if not using ACR
- **Pod `CrashLoopBackOff` on the `automation` container**: check `kubectl -n mlb-draft-tracker logs deploy/mlb-draft-tracker -c automation --previous` — the most likely cause is the initial `pre_draft_sync.sh` failing outright (e.g. no network egress — check the NetworkPolicy is actually allowing 443 egress)
- **"database is locked" errors**: shouldn't happen (see the `PRAGMA busy_timeout` hardening in `mlb_tracker/db.py`), but if it does, check `kubectl -n mlb-draft-tracker get pods` for more than one pod ever having existed at once (would indicate the `Recreate` strategy wasn't respected, e.g. from a manual `kubectl apply` that changed the strategy)
- **Wrong architecture (`exec format error`)**: you built the image for arm64 instead of amd64 — see [§3](#3-build-and-push-the-image)
