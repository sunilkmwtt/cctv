# home-cctv

Lightweight, self-hosted CCTV dashboard for a home Ubuntu server. It works alongside an existing FFmpeg recorder (`cctv.service`) and never changes its output:

- **Live View**: the camera's RTSP stream is relayed by MediaMTX as HLS. Every playlist and segment request is authorised by Django.
- **Recordings**: calendar → hourly slots → playback with seeking, plus download of the original MKV.
- **Cameras** and **Storage** pages, Django admin, and a versioned REST API (`/api/v1/`).

Stack: Django 5.2 LTS · DRF · PostgreSQL 17 · Gunicorn · nginx · MediaMTX 1.21 · FFmpeg 8 · htmx + hls.js (vendored, no CDN) · Docker Compose.

```
 Browser ──HTTP──▶ proxy (nginx :8080) ──▶ web (Django/Gunicorn) ──▶ db (PostgreSQL)
                      │  ▲ auth_request          │
                      │  └──────────────────────┘ X-Accel-Redirect (files served by nginx)
                      └─▶ mediamtx (HLS) ◀──RTSP── Android camera
 indexer (sync_recordings loop) ──▶ db         host: cctv.service ──▶ /home/sunil/cctv/recordings
                                                  (unchanged)          (mounted READ-ONLY)
```

## Design guarantees

| Concern | How it is handled |
|---|---|
| Recorder independence | The recorder stays on the host as `cctv.service`. Nothing in this stack starts, stops or writes to it. If the dashboard, database or MediaMTX is down, recording continues. |
| Video safety | Recordings are bind-mounted `:ro` into web, indexer and proxy. The app has no delete or move code paths. Remuxed MP4s go to a separate Docker volume. |
| In-progress files | `.parts/` content is never shown as a finished recording. `recording.mkv` only appears through the recorder's atomic rename, and it is also ignored until it is 30 s old. |
| Stream auth | nginx `auth_request` calls Django for **every** HLS playlist and segment. MediaMTX ports are not published. Its API (which can reveal source URLs) is reachable only inside Docker. |
| Credentials | RTSP URLs live in the untracked `secrets/mediamtx.env` (or encrypted in the DB via the admin). They are never in HTML, JS or API responses. |
| Large files | nginx serves files via `X-Accel-Redirect` with native Range and seeking support. Django never buffers video. |
| CPU | Stream copy (`-c copy`) only. At most one remux job at a time across all workers, at `nice 10`. Live pulls are on demand. |
| Paths | Clients never send paths. Stored relative paths are validated: no `..`, no hidden components, extension allow-list, and symlink-escape checks. |

## Recording layout understood

```
recordings/
├── 2026-10-26/13-14/recording.mkv        finalized hour (current recorder)
├── 2026-10-26/17-18/.parts/*.mkv         hour in progress / awaiting finalization
├── 2026-10-26/2026-10-26_13-09-17.mkv    legacy (older recorder), one file per session
└── 2026-10-25_13-02-21.mkv               legacy, flat in the root
```

- Slot `23-00` belongs to its starting date and ends at midnight.
- Hours are often shorter than 60 minutes. The real start time is taken from the recorder's part filenames when the indexer saw them; otherwise it is estimated.
- Empty or unreadable files are marked **corrupt**. They can still be downloaded but not played.
- Files that disappear are marked **missing**. If the whole mount is absent, nothing is marked missing.
- Files that match none of these layouts are ignored, for example `test.mkv`.

## Requirements (server)

- Ubuntu 22.04+ with Docker Engine 24+ and the Docker Compose v2 plugin
- About 400 MB RAM for the stack (measured idle: ~240 MB) and ~1.5 GB disk for images
- Disk space for the playback cache (default limit 20 GiB, configurable)

---

## First deployment on the server

### 1. Install Docker (once)

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker "$USER"    # then log out and back in
```

**Network overlap warning.** If your LAN uses a `172.16–31.x.x` range (an iPhone hotspot uses `172.20.10.0/28`), stop Docker from ever picking overlapping ranges. Otherwise you can lose SSH access to the server:

```bash
sudo tee /etc/docker/daemon.json > /dev/null <<'JSON'
{ "default-address-pools": [ { "base": "10.88.0.0/16", "size": 24 } ],
  "log-driver": "json-file", "log-opts": { "max-size": "10m", "max-file": "3" } }
JSON
sudo systemctl restart docker
```

The compose networks are pinned to `10.89.1.0/24` and `10.89.2.0/24` (configurable in `.env`).

### 2. Get the code

```bash
cd ~
git clone git@github.com:sunilkmwtt/cctv.git home-cctv
cd home-cctv
scripts/inspect-server.sh        # read-only sanity check of the host
```

### 3. Configure

```bash
cp .env.example .env && chmod 600 .env
scripts/generate-secrets.sh      # copy the three values into .env
nano .env
```

Set at least:

| Variable | Value |
|---|---|
| `DJANGO_SECRET_KEY`, `POSTGRES_PASSWORD`, `CCTV_FIELD_ENCRYPTION_KEY` | from `generate-secrets.sh` |
| `DJANGO_ALLOWED_HOSTS` | the server IP/hostname, e.g. `172.20.10.2,localhost` |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | e.g. `http://172.20.10.2:8080` (must include the port) |
| `CCTV_HOST_RECORDINGS_DIR` | `/home/sunil/cctv/recordings` |
| `APP_UID` / `APP_GID` | output of `id -u` / `id -g` for the recorder user (`1000`) |
| `CCTV_HTTP_PORT` | `8080` (or `80` if nothing else uses it) |

Camera source for MediaMTX. Use the same URL as `~/cctv/camera.env`, but with the variable renamed:

```bash
cp secrets/mediamtx.env.example secrets/mediamtx.env && chmod 600 secrets/mediamtx.env
sed -n 's/^CCTV_RTSP_URL=/MTX_PATHS_ENTRANCE_SOURCE=/p' ~/cctv/camera.env > secrets/mediamtx.env
```

### 4. Start

```bash
docker compose up -d --build     # builds, runs migrations once, then starts everything
docker compose ps
docker compose exec web python manage.py createsuperuser
scripts/smoke-test.sh
```

Open `http://<server-ip>:8080/` from any device on the LAN.

---

## Operations

| Task | Command |
|---|---|
| Start / update containers | `docker compose up -d --build` |
| Stop (keeps all data) | `docker compose down` |
| Status | `docker compose ps` |
| Logs | `docker compose logs -f --tail=100 web` (also `indexer`, `mediamtx`, `proxy`, `db`) |
| Run migrations explicitly | `docker compose run --rm migrate` |
| Create admin user | `docker compose exec web python manage.py createsuperuser` |
| Index recordings now | `docker compose exec indexer python manage.py sync_recordings` |
| Resource usage | `docker stats --no-stream` |
| Disk usage | `df -h /home && du -sh ~/cctv/recordings && docker system df` |
| Playback cache size | `docker compose exec web du -sh /app/cache/playback` |
| Clear playback cache (safe, regenerated on demand) | `docker compose exec web sh -c 'rm -f /app/cache/playback/*.mp4'` |
| Run tests | `docker compose -f compose.test.yaml run --rm tests && docker compose -f compose.test.yaml down` |
| Smoke test | `scripts/smoke-test.sh` |
| Reload nginx / MediaMTX config | `docker compose restart proxy` / `docker compose restart mediamtx` |

> **Never** run `docker compose down -v` unless you intend to delete the database volume `home-cctv-pgdata`.

### Safe update

```bash
cd ~/home-cctv
scripts/backup-db.sh                     # 1. backup
git fetch && git log --oneline HEAD..origin/main   # 2. review what changes
git pull --ff-only                       # 3. update code
docker compose up -d --build             # 4. rebuild; the migrate service runs once under an advisory lock
scripts/smoke-test.sh                    # 5. verify
```

Migrations run in the one-shot `migrate` service (`manage.py migrate_safe`), which holds a PostgreSQL advisory lock. `web` and `indexer` start only after it succeeds. There is a single `web` service, so there is no concurrent migration.

### Rollback

```bash
git log --oneline -5
git checkout <previous-good-commit>
docker compose up -d --build
# Only if the failed release ran a migration that the old code cannot use:
scripts/restore-db.sh backups/cctv-<timestamp>.dump
```

The database only holds metadata. If it is lost, `sync_recordings` rebuilds the recording index from disk. Only users and camera settings need the backup.

### Backup & restore

```bash
scripts/backup-db.sh                 # writes backups/cctv-YYYYmmdd-HHMMSS.dump (pg_dump custom format, keeps 14)
scripts/restore-db.sh backups/cctv-20261026-120000.dump   # asks for confirmation
```

Daily backup via cron (`crontab -e`):

```
30 2 * * * cd /home/sunil/home-cctv && scripts/backup-db.sh >> backups/backup.log 2>&1
```

Copy `backups/` off the machine from time to time. Video files are not part of these backups.

---

## Adding a camera later

1. In **Admin → Cameras**, add the camera with:
   - a `slug` (e.g. `parking`)
   - a `stream_path` (lowercase letters and digits only, e.g. `parking`)
   - a `recording_root` folder (e.g. `parking`), relative to the recordings mount
2. Add `MTX_PATHS_PARKING_SOURCE=rtsp://…` to `secrets/mediamtx.env`.
3. Add a `parking:` path (with a placeholder source) to `docker/mediamtx/mediamtx.yml`.
4. Run `docker compose restart mediamtx`.
5. Point that camera's recorder at `~/cctv/recordings/parking/YYYY-MM-DD/HH-HH/…`.

Alternatively, store the RTSP URL encrypted in **Admin → Camera credentials** (superuser only, write-only field), then export it:
`docker compose exec -T web python manage.py export_mediamtx_env > secrets/mediamtx.env`

## Switching the recorder to MediaMTX (optional, later)

Today, both the recorder and MediaMTX (only while someone watches live) connect to the phone. To have the phone serve a single client:

1. Set `sourceOnDemand: false` for the path in `docker/mediamtx/mediamtx.yml`, then `docker compose restart mediamtx`.
2. Test: `ffprobe -rtsp_transport tcp rtsp://127.0.0.1:8554/entrance`
3. Only after testing and approval, change `CCTV_RTSP_URL` in `~/cctv/camera.env` to `rtsp://127.0.0.1:8554/entrance` and restart `cctv.service`.

   Trade-off: recording then depends on the MediaMTX container being up.

## REST API

Session-authenticated. Errors look like `{"error": {"status", "code", "detail"}}`. The OpenAPI schema is at `/api/v1/schema/`.

| Method & path | Notes |
|---|---|
| `GET /api/v1/cameras/` · `/{id}/` | metadata, never credentials |
| `GET /api/v1/cameras/{id}/status/` | `online`, recorder activity, MediaMTX state |
| `GET /api/v1/recordings/?camera_id=&date=&status=` | paginated (`page`, `page_size` ≤ 200) |
| `GET /api/v1/recordings/{id}/` | |
| `GET /api/v1/recordings/{id}/play/` | MP4 with Range; `202` + `Retry-After` while remuxing; `409` in progress, `410` missing, `415` unsupported codec, `422` corrupt, `507` no space |
| `GET /api/v1/recordings/{id}/download/` | original file, attachment |
| `GET /api/v1/recordings/calendar/?camera_id=&month=YYYY-MM` | per-day counts |
| `GET /api/v1/storage/` | disk capacity/used/free, recording size and count, dates |

## Development

```bash
# Tests (throwaway Postgres; never touches real recordings)
docker compose -f compose.test.yaml run --rm tests
docker compose -f compose.test.yaml down

# Full stack with a fake RTSP camera (test pattern), using sample files in .dev/recordings
docker compose -f compose.yaml -f compose.dev.yaml up -d --build

# Re-lock dependencies after editing requirements/*.in
scripts/lock-deps.sh
```

Layout: `src/config` (settings/urls) · `src/core` (paths, ffprobe, file serving, middleware) · `src/cameras` · `src/recordings` (scanner → indexer → playback, API) · `src/dashboard` (HTML views) · `docker/` (nginx, MediaMTX) · `tests/`.

## Security notes

- LAN only. Do not port-forward this to the internet. For remote access, use Tailscale. If you serve HTTPS (e.g. `tailscale serve`), set `DJANGO_SECURE_COOKIES=1` and add the HTTPS origin to `DJANGO_CSRF_TRUSTED_ORIGINS`.
- Containers are read-only, run as non-root, drop all capabilities and set `no-new-privileges`. PostgreSQL sits on an internal network with no exposed port.
- Security headers: CSP (`script-src 'self'`, no inline scripts), X-Frame-Options DENY, nosniff, Referrer-Policy, Permissions-Policy.
- Request logs contain the method, path, status, duration and username. They never include query strings, cookies or credentials.
