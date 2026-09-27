#!/usr/bin/env bash
set -Eeuo pipefail

export PORT="${PORT:-8080}"
export BROWSER_PANEL_USER="${BROWSER_PANEL_USER:-browser}"
export BROWSER_PANEL_PASSWORD="${BROWSER_PANEL_PASSWORD:-${SE_VNC_PASSWORD:-}}"
export SE_VNC_PASSWORD="${SE_VNC_PASSWORD:-${BROWSER_PANEL_PASSWORD}}"

if [[ -z "${BROWSER_PANEL_PASSWORD}" || -z "${SE_VNC_PASSWORD}" ]]; then
  echo "[start] BROWSER_PANEL_PASSWORD (or SE_VNC_PASSWORD) is required" >&2
  exit 1
fi

mkdir -p /tmp/nginx_client_temp /tmp/nginx_proxy_temp /tmp/nginx_fastcgi_temp /tmp/nginx_uwsgi_temp /tmp/nginx_scgi_temp
printf '%s\n' "${BROWSER_PANEL_PASSWORD}" | htpasswd -i -B -c /tmp/nginx.htpasswd "${BROWSER_PANEL_USER}" >/dev/null
chmod 0600 /tmp/nginx.htpasswd

echo "[start] rendering nginx config on port ${PORT}"
# Use sed instead of envsubst: nginx itself needs variables such as $host and
# $proxy_add_x_forwarded_for, which must not be expanded by the shell helper.
sed "s/\${PORT}/${PORT}/g" /etc/nginx/nginx.conf.template > /tmp/nginx.conf

cleanup() {
  echo "[start] stopping child processes"
  kill "${KEEPALIVE_PID:-}" "${SELENIUM_PID:-}" "${NGINX_PID:-}" 2>/dev/null || true
  wait || true
}
trap cleanup EXIT INT TERM

echo "[start] starting nginx with HTTP basic-auth protection"
nginx -c /tmp/nginx.conf -g 'daemon off;' &
NGINX_PID=$!

echo "[start] starting Selenium/Chrome stack"
/opt/bin/entry_point.sh &
SELENIUM_PID=$!

for _ in $(seq 1 90); do
  if curl -fsS http://127.0.0.1:4444/status >/dev/null 2>&1; then break; fi
  sleep 1
done

if ! curl -fsS http://127.0.0.1:4444/status >/dev/null 2>&1; then
  echo "[start] Selenium did not become ready" >&2
  exit 1
fi

echo "[start] starting multi-tab keepalive"
python3 /opt/keepalive.py &
KEEPALIVE_PID=$!

wait -n "${SELENIUM_PID}" "${NGINX_PID}" "${KEEPALIVE_PID}"
exit 1
