#!/bin/bash
# 服务器端发布脚本 - 部署逻辑的唯一载体(入库版本化,取代手工 restart.sh)
#
# 用法(在目标服务器上执行,Jenkins 通过 sudo 调用):
#   release.sh --target test --package /path/release-xxx.tar.gz   发布
#   release.sh --target iod  --package /path/release-xxx.tar.gz   发布到 IOD
#   release.sh --target test --rollback 20260804_180000           回滚代码到指定快照
#
# 设计要点(对应历史事故):
#   - pkill 用 [u]vicorn 括号技巧,绝不匹配自身(SSH 自杀事故)
#   - 后台进程 env -i + setsid 启动:不带 Jenkins 构建 cookie,脱离进程组,
#     不会被 Jenkins ProcessTreeKiller 或 SSH 断连杀掉
#   - 迁移失败/健康检查失败:不重启或回滚代码,绝不带病放行
#   - config.json / venv / output / temp / logs 原地保留,rsync 不触碰
set -euo pipefail

TARGET="" PACKAGE="" ROLLBACK=""
while [ $# -gt 0 ]; do
  case "$1" in
    --target)   TARGET="$2";   shift 2;;
    --package)  PACKAGE="$2";  shift 2;;
    --rollback) ROLLBACK="$2"; shift 2;;
    *) echo "未知参数: $1" >&2; exit 2;;
  esac
done

case "$TARGET" in
  test) APP_DIR=/opt/fastapi; FRONT_DIR=/opt/vue3/dist;  WORKERS=1;;
  iod)  APP_DIR=/opt/fastapi; FRONT_DIR=/opt/front/dist; WORKERS=2;;
  *) echo "用法: release.sh --target test|iod [--package 制品 | --rollback 时间戳]" >&2; exit 2;;
esac

BACKUP_DIR=/opt/backups
LOG_DIR=$APP_DIR/logs
TS=$(date +%Y%m%d_%H%M%S)
PY=$APP_DIR/backend/venv/bin/python
PIP_INDEX="https://mirrors.aliyun.com/pypi/simple/"

# 防并发发布
exec 9>"$APP_DIR/.release.lock"
flock -n 9 || { echo "已有发布在进行,退出"; exit 1; }

log(){ echo "[release $(date +%H:%M:%S)] $*"; }

stop_services(){
  log "停止 uvicorn / worker(括号技巧防自杀)"
  pkill -f '[u]vicorn main:app' || true
  pkill -f '[w]orker_runner' || true
  sleep 2
  pkill -9 -f '[u]vicorn main:app' 2>/dev/null || true
  pkill -9 -f '[w]orker_runner' 2>/dev/null || true
}

start_services(){
  log "拉起 uvicorn:8765 + ${WORKERS} 个 worker(env -i + setsid 脱离构建会话)"
  mkdir -p "$LOG_DIR"
  cd "$APP_DIR/backend"
  local ENV="PATH=/usr/local/bin:/usr/bin HOME=/root PYTHONIOENCODING=utf-8 PYTHONUTF8=1"
  env -i $ENV setsid nohup venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port 8765 \
    >> "$LOG_DIR/uvicorn.log" 2>&1 </dev/null &
  for i in $(seq 1 "$WORKERS"); do
    env -i $ENV setsid nohup venv/bin/python -m worker_runner --worker-id "worker-$i" \
      >> "$LOG_DIR/worker-$i.log" 2>&1 </dev/null &
  done
}

health_check(){
  curl -s --max-time 5 --retry 15 --retry-delay 3 --retry-all-errors \
    http://127.0.0.1:8765/api/healthz 2>/dev/null || true
}

sync_code(){  # $1=解包后的制品目录
  local S="$1"
  rsync -a --chown=root:root --delete --exclude='venv/' --exclude='__pycache__/' --exclude='*.log' \
    "$S/backend/" "$APP_DIR/backend/"
  rsync -a --chown=root:root --delete "$S/migrations/" "$APP_DIR/migrations/"
  rsync -a --chown=root:root "$S/alembic.ini" "$APP_DIR/alembic.ini"
  rsync -a --chown=root:root --delete "$S/deploy/" "$APP_DIR/deploy/"
  rsync -a --chown=root:root --delete "$S/frontend/dist/" "${FRONT_DIR}.new/"
  [ -d "$FRONT_DIR" ] && mv "$FRONT_DIR" "${FRONT_DIR}.old.$TS" || true
  mv "${FRONT_DIR}.new" "$FRONT_DIR"
}

snapshot(){
  log "代码快照 -> $BACKUP_DIR/code-$TS.tar.gz / front-$TS.tar.gz"
  mkdir -p "$BACKUP_DIR"
  local members=()
  for m in backend migrations alembic.ini deploy; do
    [ -e "$APP_DIR/$m" ] && members+=("$m")   # 老服务器上可能没有 deploy/
  done
  tar czf "$BACKUP_DIR/code-$TS.tar.gz" -C "$APP_DIR" \
    --exclude='backend/venv' --exclude='*/__pycache__' --exclude='*.log' \
    "${members[@]}"
  [ -d "$FRONT_DIR" ] && tar czf "$BACKUP_DIR/front-$TS.tar.gz" \
    -C "$(dirname "$FRONT_DIR")" "$(basename "$FRONT_DIR")" || true
}

restore_snapshot(){  # $1=快照时间戳
  local S="$1" TMP
  TMP=$(mktemp -d)
  tar xzf "$BACKUP_DIR/code-$S.tar.gz" -C "$TMP"
  rsync -a --chown=root:root --delete --exclude='venv/' --exclude='__pycache__/' --exclude='*.log' \
    "$TMP/backend/" "$APP_DIR/backend/"
  rsync -a --chown=root:root --delete "$TMP/migrations/" "$APP_DIR/migrations/"
  rsync -a --chown=root:root "$TMP/alembic.ini" "$APP_DIR/alembic.ini"
  [ -d "$TMP/deploy" ] && rsync -a --chown=root:root --delete "$TMP/deploy/" "$APP_DIR/deploy/" || true
  rm -rf "$TMP"
  if [ -f "$BACKUP_DIR/front-$S.tar.gz" ]; then
    rm -rf "$FRONT_DIR" && mkdir -p "$FRONT_DIR"
    tar xzf "$BACKUP_DIR/front-$S.tar.gz" -C "$(dirname "$FRONT_DIR")"
  fi
}

prune_backups(){
  ls -1t "$BACKUP_DIR"/code-*.tar.gz 2>/dev/null | tail -n +6 | xargs -r rm -f
  ls -1t "$BACKUP_DIR"/front-*.tar.gz 2>/dev/null | tail -n +6 | xargs -r rm -f
  ls -1t "$BACKUP_DIR"/pre-deploy-*.dump 2>/dev/null | tail -n +11 | xargs -r rm -f
}

# ---------- 回滚模式 ----------
if [ -n "$ROLLBACK" ]; then
  log "回滚到快照 $ROLLBACK(DB 不回滚,注意代码/迁移兼容性)"
  [ -f "$BACKUP_DIR/code-$ROLLBACK.tar.gz" ] || { echo "快照不存在: $BACKUP_DIR/code-$ROLLBACK.tar.gz" >&2; exit 1; }
  stop_services
  restore_snapshot "$ROLLBACK"
  start_services
  health_check | grep -q . && log "回滚完成,健康检查通过" || { log "回滚后健康检查失败,人工介入!"; exit 1; }
  exit 0
fi

# ---------- 发布模式 ----------
[ -n "$PACKAGE" ] && [ -f "$PACKAGE" ] || { echo "制品不存在: $PACKAGE" >&2; exit 2; }

log "进程盘点(发布前):"
ps aux | grep -E '[u]vicorn main:app|[w]orker_runner' | awk '{print "  ", $2, $9, substr($0, index($0,$11), 80)}' || echo "  (无运行中进程)"

snapshot

log "pg_dump 备份 -> $BACKUP_DIR/pre-deploy-$TS.dump"
sudo -u postgres pg_dump -Fc doc_review > "$BACKUP_DIR/pre-deploy-$TS.dump"

TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
tar xzf "$PACKAGE" -C "$TMP"

log "同步代码到 $APP_DIR(config.json/venv/output/temp/logs 不动)"
sync_code "$TMP"

if ! cmp -s "$APP_DIR/backend/requirements.txt" "$APP_DIR/.requirements.installed" 2>/dev/null; then
  log "requirements.txt 有变化,安装依赖"
  "$APP_DIR/backend/venv/bin/pip" install -q -i "$PIP_INDEX" -r "$APP_DIR/backend/requirements.txt"
  cp "$APP_DIR/backend/requirements.txt" "$APP_DIR/.requirements.installed"
else
  log "依赖无变化,跳过 pip install"
fi

log "alembic 迁移(前: $($PY -m alembic current 2>/dev/null | tail -1 | cut -c1-60))"
cd "$APP_DIR"
$PY -m alembic upgrade head 2>&1 | tail -2
CUR=$($PY -m alembic current 2>/dev/null | tail -1)
echo "$CUR" | grep -q '(head)' || { log "迁移校验失败(current=$CUR),未重启服务,人工介入!"; exit 1; }
log "迁移校验通过: $CUR"

stop_services
start_services

log "健康检查(直连 127.0.0.1:8765,绕开 nginx localhost 坑)"
HEALTH=$(health_check)
if echo "$HEALTH" | grep -q '"status"'; then
  log "发布成功: $HEALTH"
  prune_backups
else
  log "健康检查失败!回滚代码快照 $TS(DB 不回滚)"
  stop_services
  restore_snapshot "$TS"
  start_services
  health_check | grep -q '"status"' && log "已回滚到发布前版本" || log "回滚也失败,人工介入!"
  exit 1
fi
