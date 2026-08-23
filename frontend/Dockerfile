FROM node:22-alpine@sha256:c610fcdfb1d5b4740dd70c284ed3cb16bb857e0f7166196e36a5501df7a3aa32 AS build

WORKDIR /build

COPY frontend/package.json frontend/package-lock.json ./frontend/
RUN npm ci --prefix frontend

COPY frontend ./frontend
ENV HIVE_FRONTEND_OUT_DIR=/build/dist
RUN npm run build --prefix frontend

FROM nginx:1.27-alpine@sha256:65645c7bb6a0661892a8b03b89d0743208a18dd2f3f17a54ef4b76fb8e2f2a10

ENV BACKEND_HOST=backend \
    BACKEND_PORT=9130 \
    NGINX_ENVSUBST_FILTER=BACKEND_

COPY frontend/nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /build/dist/panel.css /usr/share/nginx/html/panel.css
COPY --from=build /build/dist/panel.js /usr/share/nginx/html/panel.js
COPY --from=build /build/dist/panel.html /tmp/panel.html
COPY src/hive/webpanel/resources/logo.png /usr/share/nginx/html/logo.png
COPY src/hive/webpanel/resources/logo.png /usr/share/nginx/html/favicon.png

RUN ASSET_VERSION="$(cat /usr/share/nginx/html/panel.css /usr/share/nginx/html/panel.js | sha256sum | cut -c1-16)" \
    && sed \
      -e 's/__SESSION_TOKEN__//g' \
      -e "s/__ASSET_VERSION__/${ASSET_VERSION}/g" \
      /tmp/panel.html > /usr/share/nginx/html/index.html \
    && rm /tmp/panel.html

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1/ || exit 1
