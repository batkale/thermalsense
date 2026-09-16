# ThermalSense — Deployment

FastAPI serves the API, the WebSocket, and the built React frontend from a
single origin — no CORS setup, no mixed-content risk, one URL. `docker compose`
adds Caddy in front of it for automatic TLS.

## Why not serverless

The backend cannot run on Vercel/Netlify/Lambda. Three hard blockers:

- `start_ogn_stream()` holds a **persistent TCP socket** to `aprs.glidernet.org:10152`
  in a daemon thread. Serverless freezes between invocations and kills it.
- `/ws/live` pushes a frame every 2 s — it needs a long-lived connection.
- APScheduler writes `ogn_history.db`, `thermal_xgb.json` and `training_buffer.npz`.
  Without a persistent disk you lose the trained model on every redeploy.

It is also **single-process**. `_live_gliders` and the beacon buffers are
module-level state shared with the APRS thread, so a second uvicorn worker would
open a duplicate upstream connection and serve divergent data. The Dockerfile
pins `--workers 1`; do not raise it.

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `THERMALSENSE_DATA_DIR` | backend dir | Root for model, buffer and beacon DB. **Point at a mounted volume.** |
| `STATIC_DIR` | `../frontend/dist` | Built frontend. Set by the Dockerfile. |
| `CORS_ORIGINS` | `*` | Comma-separated. Leave as `*` for single-container; set explicitly if you split. |
| `ADMIN_TOKEN` | *(unset)* | When set, `POST /train` and `POST /seed` require `X-Admin-Token`. **Set this in production.** |
| `BEACON_RETENTION_DAYS` | `2` | **Hot** beacon window in SQLite. ~1.4 GB/day measured, so 2 days ≈ 2.8 GB. Lowered from 7 after the 17 Aug 2026 outage — the binding constraint is RAM (page cache), not disk. Nothing is lost at the edge of this window any more: completed days go to `ARCHIVE_DIR` first. |
| `THERMALSENSE_ARCHIVE_DIR` | `$DATA_DIR/archive/beacons` | Cold beacon archive, date-partitioned Parquet, kept forever. The default already lands inside the mounted `/data` volume, so the compose deploy needs no change — but it **grows without bound**, ~28 GB/year on the worldwide feed (10 B/row against 244 in SQLite). Watch it alongside the DB. |
| `MIN_FREE_DISK_GB` | `3` | Below this the purge job shortens retention rather than let the disk fill. |
| `MIN_RETENTION_DAYS` | `1` | Floor the emergency purge will not go under. |
| `PREDICT_CONCURRENCY` | `1` | Simultaneous `/predict` runs. Each saturates its cores and holds ~30 MB; raise only with cores to match. |
| `XGB_FIT_THREADS` | `1` | Threads for the retrain fit. Kept low so a background job cannot starve serving. |
| `XGB_PREDICT_THREADS` | *(all cores)* | Threads for inference. |

Frontend build-time vars are in `frontend/.env.example`. For the single-container
deploy you need none of them — `API_BASE` resolves to `''` and the bundle calls
its own origin, upgrading `https`→`wss` automatically.

## Build and run locally

```bash
docker build -t thermalsense .
docker volume create thermalsense-data
docker run -d --name thermalsense -p 8000:8000 \
  -v thermalsense-data:/data \
  -e ADMIN_TOKEN="$(openssl rand -hex 24)" \
  thermalsense
```

Open http://localhost:8000. Check `GET /healthz` for liveness.

For anything public use `docker compose` instead — it adds Caddy in front for
automatic TLS, and does not publish the app port on the host, so there is no
plaintext route that bypasses it.

## Host options

DigitalOcean's Student Pack credit **ended 31 July 2026** and existing balances
were wiped, so it is no longer an option.

### Azure for Students — $100 credit, 12 months, no credit card  ← chosen

Sign up at `azure.microsoft.com/free/students` and verify with your `.ac.uk`
address. No card required.

**1. Create the VM** (Portal → Virtual machines → Create):

| Field | Value |
|---|---|
| Image | **Ubuntu Server 24.04 LTS** — *not* Ubuntu Pro (see below) |
| Size | **B2ts_v2** (2 vCPU / 1 GiB, ~$8.76/mo) — see the size note below |
| Authentication | SSH public key |
| Inbound ports | SSH (22), HTTP (80), HTTPS (443) |
| Region | **Must be on your subscription's allowed list** — see the region note |
| Networking | Accept the default new vnet/subnet. Public IP: **Standard**, static. |

Port 80 must be open even though the app runs on HTTPS — Let's Encrypt validates
over HTTP before issuing, so closing it is the usual reason Caddy never gets a
certificate. Also tick **"delete public IP and NIC when VM is deleted"**;
orphaned public IPs keep billing after the VM is gone.

Azure warns that opening these ports exposes the VM to all IPs. For 80/443 that
is required and expected. For 22 it is optional hardening — SSH key auth means
the image ships with `PasswordAuthentication no`, so brute force cannot succeed;
restrict the source only if your own IP is stable:

```bash
az network nsg rule update -g thermalsense-rg --nsg-name thermalsenseNSG \
  -n SSH --source-address-prefixes "$(curl -s ifconfig.me)/32"
```

Editing the NSG from the portal always works, so a wrong rule cannot lock you
out permanently.

> **Do not accept the default image.** The portal preselects *Ubuntu Pro 24.04
> LTS*, which carries a Canonical licence surcharge billed on your Azure invoice
> — the free-services allowance covers compute, not the licence. Canonical's docs
> put it plainly: "Non-Pro LTS offers are always FREE". Choose "See all images"
> → **Ubuntu Server 24.04 LTS - x64 Gen2**. The SKUs differ only by name:
> `…:server:latest` (free) vs `…:ubuntu-pro:latest` (paid). Pro's benefits are
> host-OS package coverage, which is irrelevant here — everything runs in Docker.

> **Region note.** Student subscriptions carry an Azure Policy limiting you to
> about five regions, and the set differs per subscription. Deploying elsewhere
> fails every resource with `RequestDisallowedByAzure`. Find yours in Portal →
> Policy → Authoring → Assignments → "Allowed resource deployment regions" →
> Parameters → Allowed locations, or:
>
> ```bash
> az policy assignment list \
>   --query "[].{Policy:displayName, Allowed:parameters.listOfAllowedLocations.value}" -o json
> ```
>
> The resource group must be in an allowed region too. If the list is all-US,
> deploy there — it does not affect the data. The APRS filter string
> (`r/53.5/15.0/2500`) is evaluated by the OGN server, so glider coverage is
> identical from any region; Open-Meteo and OpenTopoData are public APIs called
> server-side. Only first-paint latency for Turkish users changes (~150 ms from
> US East vs ~40 ms from Europe), and map tiles come from Esri/CARTO CDNs.
> Prefer a European region if offered, else `eastus`/`eastus2`.
>
> **Size note.** The portal defaults to something like *Standard_D2s_v3*
> ($87.60/mo), which burns the whole $100 credit in five weeks — and student
> subscriptions usually reject it outright with `NotAvailableForSubscription`.
> The free-services allowance is specifically **B1s**, but many student
> subscriptions only offer the **B-v2** series, which starts at 2 vCPUs and has
> no B1s. In that case **B2ts_v2** is the pick: 2 vCPU / 1 GiB at $8.76/mo. It
> is strictly better than B1s for this workload — same RAM, double the vCPUs,
> which is what `predict()` is bound on.
>
> Budget the whole stack, not just compute: B2ts_v2 $8.76 + 30 GB Standard SSD
> ~$2.40 + Standard static public IPv4 ~$3.60 = **~$14.76/mo**, so the $100
> credit lasts **~7 months**.
>
> | Size | vCPU | RAM | Compute $/mo | Credit lasts (all-in) |
> |---|---|---|---|---|
> | **B2ts_v2** | 2 | 1 GiB | $8.76 | ~7 months |
> | B2ls_v2 | 2 | 4 GiB | $35.04 | ~2.5 months |
> | B2s_v2 | 2 | 8 GiB | $70.08 | ~1.3 months |

Or skip the portal entirely:

```bash
az group create -n thermalsense-rg -l polandcentral
az vm create -g thermalsense-rg -n thermalsense \
  --image Canonical:ubuntu-24_04-lts:server:latest \
  --size Standard_B2ts_v2 --generate-ssh-keys \
  --public-ip-sku Standard \
  --public-ip-address-dns-name thermalsense-tr
az vm open-port -g thermalsense-rg -n thermalsense --port 80,443 --priority 1001
```

If a size is rejected, list what your subscription actually allows —
rows with an empty `Blocked` column are available:

```bash
az vm list-skus -l polandcentral --resource-type virtualMachines \
  --query "[?starts_with(name,'Standard_B')].{Size:name, Blocked:restrictions[0].reasonCode}" -o table
```

The CLI path sets the DNS name label inline. If you used the portal, set it
afterwards on the VM's public IP (the IP resource → Configuration → DNS name
label). Either way you get `<label>.polandcentral.cloudapp.azure.com` for free, and
Let's Encrypt will issue a certificate for it — no domain purchase needed. Put
that hostname in `SITE_ADDRESS` in step 3.

**2. Add swap before building — required, not optional.** 1 GiB cannot run
`pip install` for xgboost + scipy + scikit-learn alongside the Vite build.
Skipping this is the most likely way for the deploy to fail:

```bash
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab   # persist across reboots
```

If the build still gets OOM-killed: stop the VM, resize to **B2ls_v2** (4 GiB),
build once, then resize back to B2ts_v2. Azure resizes stopped VMs freely and a
couple of hours at the higher rate costs cents. Docker caches the layers, so
later `--build` runs are incremental and fit comfortably in 1 GiB.

**3. Install Docker and deploy:**

```bash
sudo apt update && sudo apt install -y docker.io docker-compose-v2 git
sudo usermod -aG docker $USER && newgrp docker

git clone https://github.com/batkale/thermalsense && cd thermalsense
cp .env.example .env
nano .env        # set SITE_ADDRESS to your cloudapp hostname, ADMIN_TOKEN to a random hex

docker compose up -d --build
```

First build takes 5–10 minutes on a B1s. Watch it with `docker compose logs -f`.
Caddy fetches the certificate on the first HTTPS request, which adds a few
seconds. Then open `https://<your-label>.polandcentral.cloudapp.azure.com`.

**Sizing notes.** Memory is fine at runtime — the cost is almost entirely the
imported libraries (~400–600 MB); the training buffer is 1000×21 floats and the
heatmap grid ~6 MB. CPU is the thing to watch: `predict()` runs
`_MC_SAMPLES = 50` passes over a 200×200 grid, so one `/predict` is ~2 M rows
through XGBoost. B1s is *burstable* (10% baseline, banked credits), which suits
occasional predictions but will feel slow under sustained use. If it drags,
lower `_MC_SAMPLES` in `backend/models/thermal_model.py` or resize to a B2s
(~$30/mo against the $100 credit — about 3 months).

### Oracle Cloud Always Free — no expiry

Better long-term home: 2 OCPU / 12 GB ARM (Ampere A1), free forever rather than
for 12 months. Two caveats — it is **ARM**, so build on the instance itself
(`xgboost`, `scipy` and `scikit-learn` all publish `aarch64` wheels, so it works,
but an x86 image will not run); and popular regions return "out of host capacity"
for A1. Frankfurt provisions quickly and is a good latency choice for OGN.

Same Docker commands as above.

## Training node

A second role, alongside the public site rather than instead of it. The public
box serves pilots; the training node collects the feed, archives it and
retrains, and **serves nobody**. It runs from `docker-compose.training.yml`,
which leaves `docker-compose.yml` untouched.

| | Public site | Training node |
|---|---|---|
| Inbound | 22, 80, 443 | **22 only, restricted to the operator's IP** |
| Caddy / TLS | yes | **no** — reach the app with `ssh -L 8000:localhost:8000` |
| App port | `expose` behind Caddy | `127.0.0.1:8000` on the host |
| `--proxy-headers` | yes (Caddy sets the header) | **dropped** — no proxy, invariant 6 |
| Data | named volume on the OS disk | **host path on a separate data disk** (`deleteOption=Detach`) |
| `XGB_FIT_THREADS` | 1 (serving has priority) | 2 (nothing to serve) |
| Feed box | worldwide | Türkiye + Europe by default, env-tunable |

Why each one:

- **The data is the product.** A named volume lives under `/var/lib/docker` on
  the OS disk and dies with the VM. The data disk survives deleting the VM and
  can be attached to a replacement, so a broken box costs a rebuild, not months
  of collection.
- **No visitors, so no public surface.** Closing 80/443 and dropping Caddy
  removes everything but SSH from the internet.
- **Heavy analysis belongs on a laptop, not here.** The node collects, archives
  and runs the hourly retrain. Cross-validation and feature experiments run
  locally against a copy of the Parquet archive, which is megabytes a day —
  this box has 1 GiB of RAM and is not asked to fit anything larger than the
  live buffer.
- **The feed box is seasonal.** Northern-hemisphere thermals fade from October,
  so a Türkiye + Europe node produces few positives through the winter. Move
  `OGN_LAT_*` / `OGN_LON_*` south in `.env` and `docker compose -f
  docker-compose.training.yml up -d`; no rebuild.

Provisioning (`belgiumcentral` was the only allowed region offering B-series on
the student subscription it was built on — check yours as in the region note):

```bash
az group create -n thermalsense-rg -l belgiumcentral
az vm create -g thermalsense-rg -n thermalsense-dev \
  --image Canonical:ubuntu-24_04-lts:server:latest \
  --size Standard_B2ats_v2 --admin-username <you> \
  --ssh-key-values ~/.ssh/thermalsense_dev_ed25519.pub \
  --public-ip-sku Standard --public-ip-address-dns-name <label> \
  --storage-sku StandardSSD_LRS --os-disk-size-gb 32 \
  --data-disk-sizes-gb 32 --data-disk-delete-option Detach \
  --os-disk-delete-option Delete --nic-delete-option Delete
# SSH only, from your address
az network nsg rule update -g thermalsense-rg --nsg-name thermalsense-devNSG \
  -n default-allow-ssh --source-address-prefixes "$(curl -s ifconfig.me)/32"
```

~$16.33/month (B2ats_v2 $7.88, two 32 GB Standard SSDs $4.80, static IPv4
$3.65) — about six months of a $100 credit. On the VM: format and mount the
data disk at `/data`, add the 2 GB swap from step 2 above, install Docker, then
`mkdir -p /data/thermalsense` and bring the stack up with the training compose
file. Rotating your home IP locks SSH out; fix the NSG rule from the portal.

## TLS and domains

`docker compose` already runs Caddy in front of the app, so TLS is automatic for
whatever hostname is in `SITE_ADDRESS`. Caddy proxies WebSockets without extra
configuration, and once HTTPS is live the frontend switches to `wss://` by
itself — that is what the `http`→`ws` rewrite in `frontend/src/config.js` is for.

The free `<label>.cloudapp.azure.com` hostname is enough. If you prefer a custom
domain, the Student Pack includes a free Namecheap `.me` for a year: point an A
record at the VM's public IP, change `SITE_ADDRESS`, and
`docker compose up -d` to reissue.

Keep the `caddy-data` volume — it stores the issued certificates. Destroying it
repeatedly can trip Let's Encrypt rate limits (5 duplicate certs per week).

## Operating notes

**Triggering a retrain.** Keep `VITE_ADMIN_TOKEN` unset in production: any
`VITE_` var is compiled into the public JS bundle and is readable by anyone, so
it is not a secret. The button hides itself when unset; trigger runs from your
machine instead:

```bash
curl -X POST https://your-domain/train -H "X-Admin-Token: $ADMIN_TOKEN"
```

The 300 s APScheduler job retrains automatically regardless.

**Upstream API limits.** These are the real ceiling on public traffic:

- **OpenTopoData** (`api.opentopodata.org`): 1000 calls/day, 1 call/s.
  `fetch_elevation_grid` is 1 call per prediction and caches at 0.1° (~10 km),
  so roughly 1000 uncached predictions/day. The cache is in-memory and resets on
  restart. Self-host the OpenTopoData container if you outgrow this.
- **Open-Meteo**: 10,000 calls/day, non-commercial use only.

**Burstable CPU credits.** `Standard_B2ts_v2` is a burstable size: it earns
credits while idle and spends them above a baseline of **20% of 2 vCPU**.
Sustained work over that baseline — a multi-hour `/seed`, several viewers at
once — drains the balance, after which Azure throttles the VM to roughly 0.4
vCPU. Everything then gets slow *at once and for no visible reason*, which reads
exactly like an application bug and is the single most misleading failure mode
this host has. Check the balance before you debug anything else:

```bash
az monitor metrics list --resource $VM_ID --metric CPUCreditsRemaining \
  --interval PT5M --output table
```

An alert is worth the five minutes. `--condition` fires on the remaining
balance, so pick a number that leaves time to react (the size caps at 576):

```bash
az monitor metrics alert create -g thermalsense-rg -n low-cpu-credits \
  --scopes $VM_ID --description "B2ts_v2 credit balance is running down" \
  --condition "avg CPUCreditsRemaining < 100" \
  --window-size 15m --evaluation-frequency 5m \
  --action $ACTION_GROUP_ID
```

`$ACTION_GROUP_ID` needs an action group with your email
(`az monitor action-group create -g thermalsense-rg -n ops --action email me you@example.com`).

**Disk.** The beacon DB is the thing that grows: ~1.4 GB/day, bounded by
`BEACON_RETENTION_DAYS` at roughly 2.8 GB steady state on a 29 GB disk (it was
sized for ~10 GB at the old 7-day window). Note the real ceiling is RAM, not
disk — see the retention comment in `config.py`. Three non-obvious points:

- **The cold archive grows forever, on purpose.** `_archive_job` writes every
  completed day to `ARCHIVE_DIR` before the purge deletes it, at ~10 bytes/row
  against 244 in SQLite — ~80 MB/day, ~28 GB/year on the worldwide feed. That is
  the price of having a climatology and a benchmark that does not move, and it
  is the only copy of anything past the retention window. `purge_old_beacons`
  refuses to delete a day the archive does not hold, **except** under
  `_disk_guard`, which passes `emergency=True` and deletes regardless: a full
  disk is exactly when the export fails, and letting a failed export block the
  purge would rebuild the 17 Aug outage with extra steps. So if the disk fills,
  expect to lose unarchived beacons — move the archive off this volume before it
  gets close rather than relying on the guard.
- **Docker is usually the bigger consumer.** Build cache accumulates a layer set
  per `docker compose build` and is never reclaimed automatically. Check with
  `docker system df` and reclaim with `docker builder prune`.
- **A DB created before `auto_vacuum=INCREMENTAL` never shrinks.** SQLite moves
  deleted pages to a freelist and reuses them, so retention caps growth but
  returns nothing to the filesystem — a DB that once peaked at 10 GB occupies
  10 GB forever. New deployments get the pragma at creation. To convert the
  existing one, stop the app first (`VACUUM` locks out the APRS writer for
  minutes and needs ~2× the file size in scratch space):

  ```bash
  docker compose stop app
  docker compose run --rm -T app python -c \
    "import sqlite3; sqlite3.connect('/data/data/ogn_history.db').execute('VACUUM')"
  docker compose up -d app && docker ps
  ```

  Note the `-T`: without it, `compose run` over a piped ssh heredoc eats the
  remaining lines, and the `up -d app` never runs. That has caused a real outage.

**Backups.** `docker run --rm -v thermalsense-data:/data -v $PWD:/out alpine \
tar czf /out/thermalsense-backup.tgz /data`

**Logs.** `docker compose logs -f app` — the APRS thread prints connect and
reconnect lines, which is the fastest way to confirm the OGN feed is alive.
`docker compose logs -f caddy` shows certificate issuance.

**Updating.** `git pull && docker compose up -d --build`. The named volumes are
untouched, so the trained model, training buffer and beacon history survive.

**A Caddyfile change needs `--force-recreate`, not a reload.** The Caddyfile is
bind-mounted as a *single file*, so the container holds that file's inode. `git
pull` does not edit files in place — it writes a replacement and renames over
the old one, which leaves the container still reading the original inode. The
host file changes, the container's copy does not, and `caddy reload` cheerfully
re-reads the stale copy and reports success. This cost a deploy: `encode zstd`
looked applied and every response kept coming back gzip. Recreate instead, and
verify inside the container rather than on the host:

```bash
docker compose up -d --force-recreate caddy
docker exec thermalsense-caddy-1 grep encode /etc/caddy/Caddyfile
```

Certificates live in the `caddy-data` volume, so recreating is safe — Caddy does
not re-issue.
