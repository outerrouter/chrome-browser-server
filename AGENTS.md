# Base44 Dev Environment

## What this app is

A server-side Chromium/Selenium container with noVNC browser viewer, nginx basic-auth front door, and a Python keepalive script that maintains persistent Chrome tabs. Not a typical web app — there is no dev server or live reload.

## How it runs

- `docker-compose.base44.yml` builds from the repo's own `Dockerfile` (base image `selenium/standalone-chrome:latest`).
- Source files (`start.sh`, `keepalive.py`, `nginx.conf.template`) are bind-mounted read-only so edits take effect on container restart.
- Port 3000 (host) maps to 8080 (container nginx).
- `/healthz` is exempt from basic auth and returns 200 once nginx is up.

## Required secrets

- `BROWSER_PANEL_PASSWORD` — HTTP basic-auth + noVNC password (required at boot).
- `SE_VNC_PASSWORD` — VNC password (required at boot; defaults to BROWSER_PANEL_PASSWORD).

Both have generated development placeholders. Replace with real passwords for any shared/public use.

## Verification

```bash
docker compose -f docker-compose.base44.yml up -d --build
curl -fsS http://localhost:3000/healthz   # should return "ok"
curl -u browser:<password> -fsS http://localhost:3000/status  # Selenium status
```

The noVNC viewer is at `http://localhost:3000/` (basic-auth prompt, then VNC password).

## Notes

- `shm_size: 2gb` is required for Chrome to run without crashes.
- Selenium startup can take 30-60s; the healthcheck has a 90s start period.
- No live reload — call `reload_preview` after editing source files.
