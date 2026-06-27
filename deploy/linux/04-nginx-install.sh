#!/bin/bash
# 安装 nginx 站点配置 + 限流 zone。
# 用法: sudo bash 04-nginx-install.sh

set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/doc-review}"

# 1) 限流 zone 定义(单独文件,容易升级覆盖,不动主 nginx.conf)
cp "${INSTALL_DIR}/deploy/linux/nginx-doc-review-limits.conf" /etc/nginx/conf.d/zz-doc-review-limits.conf

# 2) 站点配置
cp "${INSTALL_DIR}/deploy/linux/nginx-doc-review.conf" /etc/nginx/conf.d/doc-review.conf

# Aliyun Linux 3 默认 /etc/nginx/nginx.conf 可能有冲突的 default server,禁掉
if grep -q 'server_name\s*localhost' /etc/nginx/nginx.conf 2>/dev/null; then
    sed -i '/server\s*{/,/}/{/server_name\s*localhost/,/}/d}' /etc/nginx/nginx.conf || true
fi

# nginx 默认 worker 用 nginx 用户,需要让它能读 /opt/doc-review/output/
chmod 755 /opt/doc-review
chmod -R 755 /opt/doc-review/output
chmod -R 755 /opt/doc-review/frontend/dist 2>/dev/null || true

nginx -t
systemctl reload nginx

echo "✓ nginx 配置已加载(含限流 zone)"
echo "  访问:http://<服务器公网IP>/"
echo "  API:http://<服务器公网IP>/api/archive-detect/admin/queue-stats"
