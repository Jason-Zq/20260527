#!/bin/bash
# 打发布制品:release-<sha>.tar.gz
# 内容 = backend/ + migrations/ + alembic.ini + deploy/ + frontend/dist/
# 永不包含:config.json / venv / output / temp / logs / .git
# 用法: bash deploy/ci/package.sh [源码目录=.  [输出目录=.]]
set -euo pipefail

SRC="${1:-.}"
OUT="${2:-.}"
mkdir -p "$OUT"

# 前端 dist 不存在则现场构建(Jenkins 里 Build 阶段已构建,此分支主要给手工演练用)
if [ ! -d "$SRC/frontend/dist" ]; then
  echo "[package] frontend/dist 不存在,现场构建" >&2
  (cd "$SRC/frontend" \
    && npm ci --registry=https://registry.npmmirror.com >&2 \
    && NODE_OPTIONS=--max-old-space-size=1024 npm run build >&2)
fi

SHA=$(cd "$SRC" && git rev-parse --short HEAD 2>/dev/null || date +%Y%m%d%H%M%S)
PKG="release-${SHA}.tar.gz"

tar czf "$OUT/$PKG" -C "$SRC" \
  --exclude='*/__pycache__' --exclude='*.pyc' \
  --exclude='backend/venv' --exclude='backend/*.log' \
  backend migrations alembic.ini deploy frontend/dist

echo "[package] 制品: $OUT/$PKG ($(du -h "$OUT/$PKG" | cut -f1))" >&2
echo "$PKG"   # stdout 只输出制品文件名,供调用方捕获
