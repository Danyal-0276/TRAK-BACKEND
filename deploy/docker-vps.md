# Docker + CI/CD on a VPS (TRAK API)

Web stays on **Vercel**. Mobile is separate. This guide is **Django API only** (same role as Render).

## Architecture

| Piece | Where |
|-------|--------|
| `trak-api` container | VPS — Daphne API 24/7 (`docker compose up -d`) |
| `pipeline` container | VPS — one-shot scrape + AI pipeline (cron, **not** always running) |
| `.env` + Firebase JSON | **On the server only** — never in git |
| MongoDB | Atlas (`MONGODB_URI` in server `.env`) |
| Web | Vercel (`vercel.json` → VPS `:8000`) |
| CI/CD | GitHub Actions → GHCR → SSH deploy |

**Your VPS app directory:** `/home/shahroz/trak` (`cd ~/trak`).

Workflow file: `.github/workflows/ci-cd-vps.yml` at the **repository root** (`TRAK_Backend/` — same folder as `Dockerfile` and `docker-compose.yml`). Commit and push to GitHub.

---

## 1. Get local Docker running again

From `Backend/TRAK_Backend` (with `.env` present):

```bash
docker compose up -d --build
curl http://127.0.0.1:8000/api/accounts/health/
```

Stop:

```bash
docker compose down
```

---

## 2. One-time VPS setup

App directory: **`~/trak`** (`/home/<user>/trak`) — no sudo required.

```bash
# On the VPS (Ubuntu) — user must be in the docker group (re-login after usermod)
docker ps

mkdir -p ~/trak
git clone https://github.com/Danyal-0276/TRAK-BACKEND.git ~/trak
cd ~/trak
git checkout main
```

From your PC (PowerShell), copy `.env`:

```powershell
scp C:\Users\donib\Documents\TRAK\Backend\TRAK_Backend\.env shahroz@167.86.110.151:~/trak/.env
```

### Server `.env` (keep secrets on the server only)

```bash
cd ~/trak
chmod 600 .env
nano .env   # or use scp from PC (above)
```

Production tweaks in `.env`:

```env
DJANGO_DEBUG=False
# Required until nginx/SSL on the VPS — otherwise HTTP 301 → https://ip:8000 and Vercel/mobile break.
DJANGO_SECURE_SSL_REDIRECT=False
# Keep heavy scrape/pipeline OUT of the API container (prevents OOM / random restarts).
PIPELINE_AUTO_ENABLED=false
SCRAPE_SCHEDULE_ENABLED=false
DJANGO_ALLOWED_HOSTS=your-domain.com,your-vps-ip,.your-domain.com
CORS_ALLOWED_ORIGINS=https://trak-flax.vercel.app
CSRF_TRUSTED_ORIGINS=https://your-domain.com,https://trak-flax.vercel.app
PASSWORD_RESET_FRONTEND_URL=https://trak-flax.vercel.app/reset-password
SOCIAL_AUTH_FRONTEND_URL=https://trak-flax.vercel.app/login
MONGODB_URI=mongodb+srv://...
# FIREBASE: paste JSON or mount firebase-service-account.json
```

**Docker Compose + `.env`:** do not use `$` in `DJANGO_SECRET_KEY` (Compose treats `$foo` as a variable). If you see `The "h7bxs9rmuf" variable is not set`, remove `$` from the secret or regenerate the key.

Atlas **Network Access**: allow the VPS IP (e.g. `167.86.110.151`).

```bash
cd ~/trak
docker compose up -d --build
curl http://127.0.0.1:8000/api/accounts/health/
```

Optional FCM mount in `docker-compose.yml`:

```yaml
- ./firebase-service-account.json:/app/firebase-service-account.json:ro
```

### nginx (TLS → container :8000)

Point `proxy_pass` to `http://127.0.0.1:8000` and enable WebSockets for `/ws/`.

---

## 3. GitHub secrets (Settings → Secrets and variables → Actions)

| Secret | Purpose |
|--------|---------|
| `VPS_HOST` | VPS IP or hostname |
| `VPS_USER` | SSH user (e.g. `ubuntu`) |
| `VPS_SSH_KEY` | Private key (full PEM) |
| `READ_GHCR_TOKEN` | PAT with `read:packages` so VPS can `docker pull` |

`GITHUB_TOKEN` is used automatically to **push** images to GHCR.

### GHCR package visibility

After first successful build, open **Packages** on GitHub → `trak-api` → **Package settings** → allow your repo to access, or make the package public so the VPS can pull without a token (less ideal).

---

## 4. What CI does (vs your friend’s tailoring repo)

| Friend’s repo | TRAK |
|---------------|------|
| `api/` + `web/` pnpm | `Backend/TRAK_Backend` + `TRAK/web` npm |
| Docker API + Docker Next web | **Docker API only** |
| Deploy both images | Deploy **trak-api** only |
| `.env` on server | `~/trak/.env` |

On **pull request**: runs `verify-backend` only (no deploy).

On **push to main**: builds `ghcr.io/<owner>/trak-api:<sha>`, SSHs to VPS, `cd ~/trak`, `git pull`, sets `API_IMAGE=...`, `docker compose up --no-build`.

---

## 5. Manual deploy (without waiting for CI)

```bash
ssh user@vps
cd ~/trak
export GHCR_OWNER=your-github-username-lowercase
export IMAGE_TAG=<commit-sha>
echo $READ_GHCR_TOKEN | docker login ghcr.io -u <github-user> --password-stdin
docker pull ghcr.io/$GHCR_OWNER/trak-api:$IMAGE_TAG
export API_IMAGE=ghcr.io/$GHCR_OWNER/trak-api:$IMAGE_TAG
docker compose up -d --no-build --force-recreate
```

---

## 6. Point Vercel at the VPS

In Vercel env:

```env
VITE_API_URL=https://api.yourdomain.com
```

(nginx HTTPS URL, not raw `:8000` unless you expose it.)

---

## 7. Daily scrape + pipeline (separate container + cron)

The API container must **not** run scrape/pipeline in the background (`PIPELINE_AUTO_ENABLED=false`, `SCRAPE_SCHEDULE_ENABLED=false` in `.env`).

Instead, use the **`pipeline` compose service** — same Docker image as `api`, but runs once and exits:

```bash
cd ~/trak
docker compose --profile pipeline run --rm pipeline
```

Or the helper script (after `git pull`):

```bash
chmod +x ~/trak/deploy/run-pipeline.sh
~/trak/deploy/run-pipeline.sh
```

Check last run:

```bash
docker compose exec api python manage.py check_scrape_status
```

### One-time: enable daily cron on the VPS

```bash
ssh shahroz@167.86.110.151
mkdir -p ~/trak/logs
chmod +x ~/trak/deploy/run-pipeline.sh
crontab -e
```

Add this line (runs every day at **02:00** server time):

```cron
0 2 * * * /home/shahroz/trak/deploy/run-pipeline.sh >> /home/shahroz/trak/logs/pipeline.log 2>&1
```

Verify cron is registered:

```bash
crontab -l
```

Tail logs after a run:

```bash
tail -f ~/trak/logs/pipeline.log
```

Alternative without cron: systemd timer + bare Python venv — see `vps-systemd.md` (non-Docker path).

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| App down after dockerizing | `docker compose up -d --build` and check `docker compose logs api` |
| `The "h7bxs9rmuf" variable is not set` | Remove `$` from `DJANGO_SECRET_KEY` in server `.env` (Compose interprets `$var`) |
| `curl: (56) Recv failure` right after `up` | Wait 1–3 min (migrate + collectstatic), then `docker compose logs api --tail=80` |
| Health returns 301 | Set `DJANGO_SECURE_SSL_REDIRECT=False` in server `.env` until nginx SSL |
| Migrate fails | Atlas → Network Access → allow VPS IP; verify `MONGODB_URI` in `.env` |
| Port 8000 in use on PC | Stop old Daphne: `netstat -ano \| findstr :8000` then kill PID |
| Deploy can’t pull image | Check `READ_GHCR_TOKEN` and package permissions |
| 502 from nginx | `curl http://127.0.0.1:8000/api/accounts/health/` on VPS |
| CORS errors from Vercel | Add Vercel URL to `CORS_ALLOWED_ORIGINS` in server `.env` |
