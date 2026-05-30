# TRAK backend on a VPS (API + scheduled pipeline)

Use this when moving from Render to a friend’s VPS or a paid VM. MongoDB can stay on **Atlas**; only Django runs on the VPS.

## Architecture

| Process | Role |
|---------|------|
| `trak-api.service` | Daphne — REST API + WebSockets (`/ws/notifications/`) |
| `trak-pipeline.timer` | systemd timer — scrape + AI pipeline 1–3×/day |
| nginx | TLS reverse proxy to Daphne |

Do **not** run `run_news_cycle` or large `run_ai_pipeline --all` inside the API service.

## Suggested VPS size (HF Spaces, no local GPU)

| Setup | vCPU | RAM |
|-------|------|-----|
| API only | 1–2 | 2 GB |
| API + daily pipeline `--workers 3` | 2–4 | 4 GB |

## Install

```bash
sudo apt update && sudo apt install -y python3.10-venv python3-pip nginx certbot python3-certbot-nginx
sudo useradd -r -m -d /opt/trak trak || true
sudo -u trak git clone <your-repo> /opt/trak
cd /opt/trak/Backend/TRAK_Backend
sudo -u trak python3 -m venv /opt/trak/venv
sudo -u trak /opt/trak/venv/bin/pip install -r requirements.txt
sudo -u trak /opt/trak/venv/bin/python -m spacy download en_core_web_sm
sudo -u trak cp .env.example .env   # edit: MONGODB_URI, secrets, CORS, ALLOWED_HOSTS
sudo -u trak /opt/trak/venv/bin/python manage.py migrate --noinput
sudo -u trak /opt/trak/venv/bin/python manage.py collectstatic --noinput
```

## Environment (`.env`)

Copy from production Render. Required for pipeline:

```env
PIPELINE_WORKERS=3
PIPELINE_STALE_MINUTES=30
FAKE_DETECTION_SPACE_API_NAME=/detect
SUMMARIZER_SPACE_API_NAME=/summarize
FACT_CHECKER_PARALLEL=true
```

## systemd: API service

`/etc/systemd/system/trak-api.service`:

```ini
[Unit]
Description=TRAK Django ASGI (Daphne)
After=network.target

[Service]
User=trak
Group=trak
WorkingDirectory=/opt/trak/Backend/TRAK_Backend
EnvironmentFile=/opt/trak/Backend/TRAK_Backend/.env
ExecStart=/opt/trak/venv/bin/daphne -b 127.0.0.1 -p 8000 TRAK_Backend.asgi:application
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now trak-api
```

## systemd: pipeline timer (2×/day example)

`/etc/systemd/system/trak-pipeline.service`:

```ini
[Unit]
Description=TRAK scrape + AI pipeline
After=network.target

[Service]
Type=oneshot
User=trak
Group=trak
WorkingDirectory=/opt/trak/Backend/TRAK_Backend
EnvironmentFile=/opt/trak/Backend/TRAK_Backend/.env
ExecStart=/opt/trak/venv/bin/python manage.py run_news_cycle \
  --sources dawn dunya rss generic_sites \
  --scrape-limit 35 \
  --pipeline-all \
  --pipeline-batch-size 50 \
  --workers 3 \
  --requeue-stale
```

`/etc/systemd/system/trak-pipeline.timer`:

```ini
[Unit]
Description=Run TRAK news cycle twice daily

[Timer]
OnCalendar=*-*-* 06,18:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now trak-pipeline.timer
systemctl list-timers trak-pipeline.timer
```

### Schedule options

| Frequency | `OnCalendar` example |
|-----------|----------------------|
| 1×/day | `*-*-* 02:00:00` |
| 2×/day | `*-*-* 06,18:00:00` |
| 3×/day | `*-*-* 06,14,22:00:00` |

## nginx (snippet)

```nginx
server {
    listen 443 ssl;
    server_name api.yourdomain.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 120s;
    }
}
```

Point mobile/web `API_BASE` / `VITE_API_URL` at `https://api.yourdomain.com`.

## Manual pipeline (same as timer)

```bash
cd /opt/trak/Backend/TRAK_Backend
/opt/trak/venv/bin/python manage.py run_ai_pipeline --all --workers 4 --requeue-stale
```

## Stuck articles

```bash
/opt/trak/venv/bin/python manage.py run_ai_pipeline --requeue-stale
# or
/opt/trak/venv/bin/python scripts/requeue_failed_pipeline.py
```

## Migration from Render

1. Export env vars from Render dashboard into VPS `.env`.
2. Deploy code; start `trak-api`; verify `GET /api/user/feed/`.
3. Enable `trak-pipeline.timer`.
4. Update DNS; test apps against VPS URL.
5. Disable Render web service when stable (keep Atlas unchanged).
