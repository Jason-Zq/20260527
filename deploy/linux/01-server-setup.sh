#!/bin/bash
# 在 Aliyun Linux 3 / CentOS 8+ ECS 上一次性初始化:系统包 + Python + PostgreSQL + nginx + LibreOffice + 字体
# 用法: sudo bash 01-server-setup.sh
# 此脚本幂等,失败可重跑

set -euo pipefail

DEPLOY_USER="${DEPLOY_USER:-docreview}"
INSTALL_DIR="${INSTALL_DIR:-/opt/doc-review}"
PG_DB="${PG_DB:-doc_review}"
PG_USER="${PG_USER:-docreview}"
PG_PASSWORD="${PG_PASSWORD:-please_change_me}"

echo "==> 1/7 安装系统依赖(Python/编译工具/OpenCV deps/中文字体/antiword)"
dnf install -y --setopt=install_weak_deps=False \
    python3.12 python3.12-pip python3.12-devel \
    gcc gcc-c++ make \
    postgresql-server postgresql-contrib \
    nginx \
    libreoffice libreoffice-langpack-zh-Hans \
    mesa-libGL \
    wqy-zenhei-fonts wqy-microhei-fonts \
    antiword \
    git rsync tar

echo "==> 2/7 初始化 PostgreSQL(本机)"
if [ ! -d /var/lib/pgsql/data/base ]; then
    postgresql-setup --initdb
fi
# 允许本机密码登录(local trust → md5)
sed -i 's/^\(local\s\+all\s\+all\s\+\)peer/\1md5/' /var/lib/pgsql/data/pg_hba.conf || true
sed -i 's/^\(host\s\+all\s\+all\s\+127\.0\.0\.1\/32\s\+\)ident/\1md5/' /var/lib/pgsql/data/pg_hba.conf || true
systemctl enable --now postgresql

echo "==> 3/7 创建业务 DB + user"
sudo -u postgres psql <<SQL
DO \$\$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_user WHERE usename = '${PG_USER}') THEN
        CREATE USER ${PG_USER} WITH PASSWORD '${PG_PASSWORD}';
    END IF;
END
\$\$;
SELECT 'CREATE DATABASE ${PG_DB} OWNER ${PG_USER}'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '${PG_DB}')\gexec
GRANT ALL PRIVILEGES ON DATABASE ${PG_DB} TO ${PG_USER};
SQL

echo "==> 4/7 创建运行用户 ${DEPLOY_USER} 和安装目录"
id -u ${DEPLOY_USER} &>/dev/null || useradd -m -s /bin/bash ${DEPLOY_USER}
mkdir -p ${INSTALL_DIR} ${INSTALL_DIR}/output ${INSTALL_DIR}/temp ${INSTALL_DIR}/logs
chown -R ${DEPLOY_USER}:${DEPLOY_USER} ${INSTALL_DIR}

echo "==> 5/7 开机自启 + 防火墙放行(80/443)"
systemctl enable --now nginx
if systemctl is-active --quiet firewalld; then
    firewall-cmd --permanent --add-service=http
    firewall-cmd --permanent --add-service=https
    firewall-cmd --reload
fi

echo "==> 6/7 校验 soffice + 字体 + antiword"
which soffice
which antiword
fc-list :lang=zh | head -3

echo "==> 7/7 完成"
cat <<EOF

下一步:
1. 上传代码:在本地跑 deploy/linux/05-upload.sh <你的服务器 IP>
2. 安装 Python 依赖 + 跑 alembic:在服务器上跑 sudo -u ${DEPLOY_USER} bash ${INSTALL_DIR}/deploy/linux/02-install-app.sh
3. 安装 systemd 单元:sudo bash ${INSTALL_DIR}/deploy/linux/03-systemd-install.sh
4. 配置 nginx:sudo bash ${INSTALL_DIR}/deploy/linux/04-nginx-install.sh

数据库已就绪:
  DB:    ${PG_DB}
  User:  ${PG_USER}
  Pass:  (从环境变量 PG_PASSWORD 传入,本次=${PG_PASSWORD})
  连接串:postgresql://${PG_USER}:${PG_PASSWORD}@127.0.0.1:5432/${PG_DB}
EOF
