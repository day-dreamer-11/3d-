#!/usr/bin/env bash
set -euo pipefail

PORT="${PORT:-10000}"
BASIC_AUTH_USER="${BASIC_AUTH_USER:-admin}"
AIC_DATA_DIR="${AIC_DATA_DIR:-/var/data}"
TWIN_PUBLIC_BASE="${TWIN_PUBLIC_BASE:-/twin}"
TWIN_INTERNAL_PORT="${TWIN_INTERNAL_PORT:-8020}"
export PORT AIC_DATA_DIR TWIN_PUBLIC_BASE TWIN_INTERNAL_PORT

if [ -z "${BASIC_AUTH_PASSWORD:-}" ]; then
    BASIC_AUTH_PASSWORD="$(python -c 'import secrets; print(secrets.token_urlsafe(14))')"
    echo "WARNING: BASIC_AUTH_PASSWORD 未设置，已生成临时密码：${BASIC_AUTH_PASSWORD}"
    echo "WARNING: 请在 Render 环境变量中设置固定密码，否则每次重启都会变化。"
fi

htpasswd -bc /etc/nginx/.htpasswd "${BASIC_AUTH_USER}" "${BASIC_AUTH_PASSWORD}"
chmod 644 /etc/nginx/.htpasswd

envsubst '${PORT}' < /etc/nginx/nginx.conf.template > /etc/nginx/nginx.conf
mkdir -p "${AIC_DATA_DIR}" /tmp/aic

echo "AIC Factory Studio"
echo "  PORT=${PORT}"
echo "  BASIC_AUTH_USER=${BASIC_AUTH_USER}"
echo "  AIC_DATA_DIR=${AIC_DATA_DIR}"
echo "  TWIN_PUBLIC_BASE=${TWIN_PUBLIC_BASE}"
echo "  TWIN_INTERNAL_PORT=${TWIN_INTERNAL_PORT}"

exec supervisord -c /etc/supervisor/conf.d/aic.conf
