#!/bin/bash
# 部署本次代码 + nginx 改动到服务器(适配你的实际路径 /opt/fastapi/ 端口 8765)
# 用法: bash deploy/linux/server-actual/deploy.sh

set -euo pipefail

SERVER="${SERVER:-root@8.138.111.12}"
INSTALL_DIR="${INSTALL_DIR:-/opt/fastapi}"

cd "$(dirname "$0")/../../.."   # 切到项目根

echo "==> 1/4 推后端代码改动(main.py + text_extractor.py)"
rsync -avz \
    backend/main.py \
    backend/text_extractor.py \
    "${SERVER}:${INSTALL_DIR}/backend/"

echo "==> 2/4 推 nginx 配置"
scp deploy/linux/server-actual/zz-doc-review-limits.conf "${SERVER}:/etc/nginx/conf.d/zz-doc-review-limits.conf"
# 注意:如果你的站点 conf 文件名不是 doc-review.conf,改下面这行的目标名
# 先备份原文件再覆盖
ssh "${SERVER}" "
  set -e
  # 找出当前站点配置文件名(假设只有一个非默认 conf 在 conf.d/ 下)
  SITE_CONF=\$(ls /etc/nginx/conf.d/*.conf 2>/dev/null | grep -v 'zz-doc-review-limits' | head -1)
  if [ -z \"\$SITE_CONF\" ]; then
      echo '未找到现有站点 conf,新建 /etc/nginx/conf.d/doc-review.conf'
      SITE_CONF=/etc/nginx/conf.d/doc-review.conf
  else
      cp \"\$SITE_CONF\" \"\${SITE_CONF}.bak.\$(date +%Y%m%d_%H%M%S)\"
      echo '已备份原站点 conf 到 '\"\$SITE_CONF\".bak.\$(date +%Y%m%d_%H%M%S)
  fi
  echo \"目标:\$SITE_CONF\"
"
# 用 SCP 推站点 conf(目标名固定 doc-review.conf,如果服务器原文件名不同,上面会备份后我们用这个新名)
scp deploy/linux/server-actual/nginx-site.conf "${SERVER}:/etc/nginx/conf.d/doc-review.conf"

echo "==> 3/4 nginx 语法检查 + reload"
ssh "${SERVER}" 'nginx -t && systemctl reload nginx'

echo "==> 4/4 重启后端"
ssh "${SERVER}" "
  pkill -f 'uvicorn main:app' || true
  sleep 2
  cd ${INSTALL_DIR}/backend
  source venv/bin/activate
  nohup python -m uvicorn main:app --host 0.0.0.0 --port 8765 > app.log 2>&1 &
  sleep 5
  echo '--- healthz ---'
  curl -s http://127.0.0.1:8765/api/healthz || echo '健康检查失败,看 app.log'
  echo
"

echo ""
echo "完成。验证:"
echo "  curl http://8.138.111.12/healthz"
echo "  浏览器: http://8.138.111.12/"
