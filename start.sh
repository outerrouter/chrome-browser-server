#!/usr/bin/env bash
set -Eeuo pipefail

export PORT="${PORT:-8080}"
export MCP_PORT="${MCP_PORT:-8090}"
export BROWSER_PANEL_USER="${BROWSER_PANEL_USER:-browser}"
export BROWSER_PANEL_PASSWORD="${BROWSER_PANEL_PASSWORD:-${SE_VNC_PASSWORD:-}}"
export SE_VNC_PASSWORD="${SE_VNC_PASSWORD:-${BROWSER_PANEL_PASSWORD}}"

if [[ -z "${BROWSER_PANEL_PASSWORD}" || -z "${SE_VNC_PASSWORD}" ]]; then echo "[start] password is required" >&2; exit 1; fi

mkdir -p /tmp/nginx_client_temp /tmp/nginx_proxy_temp /tmp/nginx_fastcgi_temp /tmp/nginx_uwsgi_temp /tmp/nginx_scgi_temp
printf '%s\n' "${BROWSER_PANEL_PASSWORD}" | htpasswd -i -B -c /tmp/nginx.htpasswd "${BROWSER_PANEL_USER}" >/dev/null
chmod 0600 /tmp/nginx.htpasswd
sed "s/\${PORT}/${PORT}/g; s/\${MCP_PORT}/${MCP_PORT}/g" /etc/nginx/nginx.conf.template > /tmp/nginx.conf

cleanup() { kill "${KEEPALIVE_PID:-}" "${MCP_PID:-}" "${SELENIUM_PID:-}" "${NGINX_PID:-}" 2>/dev/null || true; wait || true; }
trap cleanup EXIT INT TERM

nginx -c /tmp/nginx.conf -g 'daemon off;' & NGINX_PID=$!
/opt/bin/entry_point.sh & SELENIUM_PID=$!

for _ in $(seq 1 90); do if curl -fsS http://127.0.0.1:4444/status >/dev/null 2>&1; then break; fi; sleep 1; done
curl -fsS http://127.0.0.1:4444/status >/dev/null 2>&1 || { echo "[start] Selenium did not become ready" >&2; exit 1; }

python3 /opt/keepalive.py & KEEPALIVE_PID=$!
python3 /opt/mcp_server.py & MCP_PID=$!

wait -n "${SELENIUM_PID}" "${NGINX_PID}" "${KEEPALIVE_PID}" "${MCP_PID}"
exit 1