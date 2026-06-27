#!/bin/bash
# 本地 → 服务器:rsync 上传 backend/migrations/config/deploy + 已构建的 frontend/dist
# 用法: bash 05-upload.sh user@server-ip [/opt/doc-review]
#
# 默认假设服务器上已经跑过 01-server-setup.sh,docreview 用户和 /opt/doc-review 已经创建。
# 你的本机 ssh key 需要能登录到 root 或 docreview(脚本里用 root 上传后,再 chown)。

set -euo pipefail

SSH_TARGET="${1:?用法: bash 05-upload.sh <user@host> [install_dir]}"
INSTALL_DIR="${2:-/opt/doc-review}"

# 切到项目根
cd "$(dirname "$0")/../.."

echo "==> 1/3 本地构建前端"
(cd frontend && npm run build)

echo "==> 2/3 rsync 上传到 ${SSH_TARGET}:${INSTALL_DIR}"
rsync -avz --delete \
    --exclude='__pycache__' --exclude='*.pyc' \
    --exclude='.venv*' --exclude='node_modules' \
    --exclude='output/*' --exclude='temp/*' --exclude='logs/*' \
    --exclude='.git' \
    backend migrations config.json alembic.ini deploy \
    "${SSH_TARGET}:${INSTALL_DIR}/"

rsync -avz --delete frontend/dist/ "${SSH_TARGET}:${INSTALL_DIR}/frontend/dist/"

echo "==> 3/3 修正 owner(若 ssh 登录是 root)"
ssh "${SSH_TARGET}" "chown -R docreview:docreview ${INSTALL_DIR} 2>/dev/null || true"

cat <<EOF

上传完成。服务器上接下来:
  ssh ${SSH_TARGET}
  # 首次部署:
  sudo -u docreview bash ${INSTALL_DIR}/deploy/linux/02-install-app.sh
  sudo bash ${INSTALL_DIR}/deploy/linux/03-systemd-install.sh
  sudo bash ${INSTALL_DIR}/deploy/linux/04-nginx-install.sh
  # 后续更新代码:
  sudo systemctl restart doc-review
  sudo systemctl reload nginx   # 前端 dist 变化时
EOF
