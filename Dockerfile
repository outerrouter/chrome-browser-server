FROM selenium/standalone-chrome:latest

USER root

RUN apt-get update \
    && apt-get install -y --no-install-recommends nginx gettext-base python3 curl apache2-utils \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /tmp/nginx_client_temp /tmp/nginx_proxy_temp /tmp/nginx_fastcgi_temp /tmp/nginx_uwsgi_temp /tmp/nginx_scgi_temp \
    && chown -R seluser:seluser /etc/nginx /tmp/nginx_*

COPY nginx.conf.template /etc/nginx/nginx.conf.template
COPY start.sh /start.sh
COPY keepalive.py /opt/keepalive.py

RUN chmod 0755 /start.sh /opt/keepalive.py

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
    BROWSER_DURATION_MINUTES=0

EXPOSE 8080

ENTRYPOINT ["/start.sh"]
