#!/bin/bash
# 安装/更新应用层依赖。以 docreview 用户身份运行。
# 用法: sudo -u docreview bash /opt/doc-review/deploy/linux/02-install-app.sh

set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/doc-review}"
VENV_DIR="${INSTALL_DIR}/.venv"

cd "${INSTALL_DIR}"

echo "==> 1/4 创建/复用 venv"
if [ ! -d "${VENV_DIR}" ]; then
    python3.12 -m venv "${VENV_DIR}"
fi
source "${VENV_DIR}/bin/activate"

echo "==> 2/4 升级 pip + 安装 backend 依赖(用国内镜像加速)"
pip install --upgrade pip
pip install -r backend/requirements.txt -i https://mirrors.aliyun.com/pypi/simple/

echo "==> 3/4 执行数据库迁移"
cd "${INSTALL_DIR}"
"${VENV_DIR}/bin/python" -m alembic upgrade head

echo "==> 4/4 预热 PaddleOCR 模型(首次下载约 100MB,只下一次)"
"${VENV_DIR}/bin/python" -c "
import sys
sys.path.insert(0, 'backend')
import ocr_service
ocr_service._get_ocr_engine()
print('PaddleOCR 模型就绪')
"

echo "完成。下一步: sudo bash ${INSTALL_DIR}/deploy/linux/03-systemd-install.sh"
