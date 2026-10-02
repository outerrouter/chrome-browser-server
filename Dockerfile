FROM selenium/standalone-chrome:latest

USER root

RUN apt-get update \
    && apt-get install -y --no-install-recommends nginx gettext-base python3 python3-pip curl apache2-utils \
    && python3 -m pip install --no-cache-dir --break-system-packages "mcp[cli]>=2,<3" "playwright>=1.55,<2" \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /tmp/nginx_client_temp /tmp/nginx_proxy_temp /tmp/nginx_fastcgi_temp /tmp/nginx_uwsgi_temp /tmp/nginx_scgi_temp \
    && chown -R seluser:seluser /etc/nginx /tmp/nginx_*

COPY nginx.conf.template /etc/nginx/nginx.conf.template
COPY start.sh /start.sh
COPY keepalive.py /opt/keepalive.py
COPY mcp_server.py /opt/mcp_server.py
COPY dashboard /opt/dashboard
COPY agent_runtime.py /opt/agent_runtime.py

RUN chmod 0755 /start.sh /opt/keepalive.py /opt/mcp_server.py /opt/agent_runtime.py

USER seluser

ENV PORT=8080 \
    BROWSER_PANEL_USER=browser \
    BROWSER_PANEL_PASSWORD= \
    SE_VNC_PASSWORD= \
    SE_SCREEN_WIDTH=1440 \
    SE_SCREEN_HEIGHT=900 \
    SE_RECORD_VIDEO=false \
    SE_VIDEO_RECORDING=false \
    SE_NODE_MAX_SESSIONS=1 \
    KEEPALIVE_URLS=https://example.com \
    KEEPALIVE_INTERVAL_SECONDS=60 \
    BROWSER_DURATION_MINUTES=0 \
    MCP_PORT=8090 \
    MCP_BROWSER_TIMEOUT_SECONDS=30 \
    CDP_ENDPOINT=http://127.0.0.1:9222 \
    AGENT_MAX_STEPS=100

EXPOSE 8080

ENTRYPOINT ["/start.sh"]
