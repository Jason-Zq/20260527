#!/bin/bash
# 安装 systemd 单元并启动服务。
# 用法: sudo bash 03-systemd-install.sh

set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/doc-review}"

# 1. 若 app.env 不存在,从 example 复制并提示
if [ ! -f "${INSTALL_DIR}/deploy/linux/app.env" ]; then
    cp "${INSTALL_DIR}/deploy/linux/app.env.example" "${INSTALL_DIR}/deploy/linux/app.env"
    echo "已创建 ${INSTALL_DIR}/deploy/linux/app.env,请编辑数据库密码后重新运行本脚本"
    exit 1
fi
chmod 600 "${INSTALL_DIR}/deploy/linux/app.env"
chown docreview:docreview "${INSTALL_DIR}/deploy/linux/app.env"

# 2. 安装单元
cp "${INSTALL_DIR}/deploy/linux/doc-review.service" /etc/systemd/system/doc-review.service
systemctl daemon-reload

# 3. 启动 + 开机自启
systemctl enable --now doc-review
sleep 3

# 4. 验证(走 /api/healthz,会真查 DB)
if systemctl is-active --quiet doc-review; then
    echo "✓ doc-review systemd 已 active"
    health=$(curl -s -m 5 -w "\n%{http_code}" http://127.0.0.1:8000/api/healthz)
    code=$(echo "$health" | tail -1)
    body=$(echo "$health" | head -n -1)
    echo "  /api/healthz HTTP ${code}"
    echo "  ${body}"
    if [ "${code}" != "200" ]; then
        echo "✗ 健康检查失败,看日志:journalctl -u doc-review -n 50 --no-pager"
        exit 1
    fi
else
    echo "✗ doc-review 启动失败,查看日志:"
    echo "  journalctl -u doc-review -n 50 --no-pager"
    exit 1
fi

echo ""
echo "下一步:sudo bash ${INSTALL_DIR}/deploy/linux/04-nginx-install.sh"
