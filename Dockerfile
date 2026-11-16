# AIC Factory Studio 单容器部署镜像
# 结构：nginx（公网端口）→ Streamlit 工作台 + RFTwin 三维服务（均只监听 127.0.0.1）
# 三维前端构建为 base=/twin/，由 nginx 同源代理，保持 WebSocket 与参数注入能力。

FROM node:20-bookworm-slim AS frontend
WORKDIR /web
COPY rftwin/frontend/package.json rftwin/frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY rftwin/frontend/ ./
RUN npm run build -- --base=/twin/

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        nginx apache2-utils supervisor gettext-base ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
COPY --from=frontend /web/dist ./rftwin/frontend/dist
COPY deploy/nginx.conf.template /etc/nginx/nginx.conf.template
COPY deploy/supervisord.conf /etc/supervisor/conf.d/aic.conf
COPY deploy/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh && mkdir -p /var/data

ENV AIC_DATA_DIR=/var/data \
    AIC_STORAGE=sqlite \
    TWIN_PUBLIC_BASE=/twin \
    TWIN_INTERNAL_PORT=8020 \
    STREAMLIT_SERVER_ENABLE_CORS=false \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

EXPOSE 10000
CMD ["/entrypoint.sh"]
