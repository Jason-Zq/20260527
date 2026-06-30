#!/bin/bash
# 安装 systemd 单元并启动服务(方案二 2b: 主服务 + 3 个 Worker 进程)。
# 用法: sudo bash 03-systemd-install.sh

set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/doc-review}"
WORKER_COUNT="${WORKER_COUNT:-1}"     # Worker 进程数,小内存/单盘服务器默认 1,稳定性优先

# 1. 若 app.env 不存在,从 example 复制并提示
if [ ! -f "${INSTALL_DIR}/deploy/linux/app.env" ]; then
    cp "${INSTALL_DIR}/deploy/linux/app.env.example" "${INSTALL_DIR}/deploy/linux/app.env"
    echo "已创建 ${INSTALL_DIR}/deploy/linux/app.env,请编辑数据库密码后重新运行本脚本"
    exit 1
fi
chmod 600 "${INSTALL_DIR}/deploy/linux/app.env"
chown docreview:docreview "${INSTALL_DIR}/deploy/linux/app.env"

# 2. 安装主单元 + Worker 模板
cp "${INSTALL_DIR}/deploy/linux/doc-review.service" /etc/systemd/system/doc-review.service
cp "${INSTALL_DIR}/deploy/linux/doc-review-worker@.service" /etc/systemd/system/doc-review-worker@.service
systemctl daemon-reload

# 3. 启动主服务 + Worker 进程
systemctl enable --now doc-review
sleep 3

# 4. 启动 Worker 进程(每隔 5s 启动一个,避免 3 个进程同时加载 PaddleOCR 模型瞬时 OOM)
for i in $(seq 1 ${WORKER_COUNT}); do
    echo "==> 启动 worker-${i}"
    systemctl enable --now doc-review-worker@${i}
    sleep 5
done

# 5. 验证主服务(走 /api/healthz,真查 DB)
sleep 3
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

# 6. 验证 Worker
echo ""
echo "Worker 状态:"
for i in $(seq 1 ${WORKER_COUNT}); do
    if systemctl is-active --quiet doc-review-worker@${i}; then
        echo "  ✓ worker-${i} active"
    else
        echo "  ✗ worker-${i} not active(journalctl -u doc-review-worker@${i} -n 30)"
    fi
done

echo ""
echo "下一步:sudo bash ${INSTALL_DIR}/deploy/linux/04-nginx-install.sh"
