FROM nginx:1.27-alpine

ENV BACKEND_HOST=backend \
    BACKEND_PORT=9130 \
    NGINX_ENVSUBST_FILTER=BACKEND_

COPY frontend/nginx.conf.template /etc/nginx/templates/default.conf.template
COPY src/hive/webpanel/resources/panel.css /usr/share/nginx/html/panel.css
COPY src/hive/webpanel/resources/panel.js /usr/share/nginx/html/panel.js
COPY src/hive/webpanel/resources/logo.png /usr/share/nginx/html/logo.png
COPY src/hive/webpanel/resources/logo.png /usr/share/nginx/html/favicon.png
COPY src/hive/webpanel/resources/panel.html /tmp/panel.html

RUN sed \
      -e 's/__SESSION_TOKEN__//g' \
      -e 's/__ASSET_VERSION__/docker/g' \
      -e 's|__LOGO__|<span class="brandmark"><img src="/logo.png" alt=""></span>|g' \
      /tmp/panel.html > /usr/share/nginx/html/index.html \
    && rm /tmp/panel.html

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1/ || exit 1
