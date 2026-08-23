FROM nginx:1.27-alpine@sha256:65645c7bb6a0661892a8b03b89d0743208a18dd2f3f17a54ef4b76fb8e2f2a10

ENV BACKEND_HOST=backend \
    BACKEND_PORT=9130 \
    NGINX_ENVSUBST_FILTER=BACKEND_

COPY frontend/nginx.conf.template /etc/nginx/templates/default.conf.template
COPY src/hive/webpanel/resources/panel.css /usr/share/nginx/html/panel.css
COPY src/hive/webpanel/resources/panel.js /usr/share/nginx/html/panel.js
COPY src/hive/webpanel/resources/logo.png /usr/share/nginx/html/logo.png
COPY src/hive/webpanel/resources/logo.png /usr/share/nginx/html/favicon.png
COPY src/hive/webpanel/resources/panel.html /tmp/panel.html

RUN ASSET_VERSION="$(cat /usr/share/nginx/html/panel.css /usr/share/nginx/html/panel.js | sha256sum | cut -c1-16)" \
    && sed \
      -e 's/__SESSION_TOKEN__//g' \
      -e "s/__ASSET_VERSION__/${ASSET_VERSION}/g" \
      -e 's|__LOGO__|<span class="brandmark"><img src="/logo.png" alt=""></span>|g' \
      /tmp/panel.html > /usr/share/nginx/html/index.html \
    && rm /tmp/panel.html

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1/ || exit 1
