#!/bin/bash
# CI 测试入口 - 测试白名单的唯一维护点
# 纯函数档:不依赖 DB / 外部服务,直接跑
# DB 档:依赖 PostgreSQL,每次运行前重建 doc_review_ci 库(绝不碰 doc_review 业务库)
#
# 环境变量:
#   CI_DB_PASSWORD  ci 角色密码;未设置时尝试读 /var/lib/jenkins/ci-db.env(Jenkins 机上)
# 用法: bash tests/run_ci.sh
set -euo pipefail
cd "$(dirname "$0")/.."

VENV=.ci-venv
if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV"
fi
PIP="$VENV/bin/pip"
# 阿里云 ECS 走镜像站,几秒装完;本地开发走默认 PyPI 也可
$PIP install -q --upgrade pip -i https://mirrors.aliyun.com/pypi/simple/ 2>/dev/null || $PIP install -q --upgrade pip
$PIP install -q -r backend/requirements.txt -i https://mirrors.aliyun.com/pypi/simple/ 2>/dev/null \
  || $PIP install -q -r backend/requirements.txt
# CI 额外依赖(ocrapi 契约测试用,不在 backend/requirements.txt)
$PIP install -q PyJWT bcrypt -i https://mirrors.aliyun.com/pypi/simple/ 2>/dev/null \
  || $PIP install -q PyJWT bcrypt

PY="$VENV/bin/python"
export PYTHONIOENCODING=utf-8 PYTHONUTF8=1
export LC_ALL=C.UTF-8 LANG=C.UTF-8   # 服务器默认 POSIX locale,alembic/测试打印中文会炸

# DB 密码:环境变量优先,其次 Jenkins 机上的 ci-db.env
if [ -z "${CI_DB_PASSWORD:-}" ] && [ -r /var/lib/jenkins/ci-db.env ]; then
  . /var/lib/jenkins/ci-db.env
fi

# 部分"纯函数"测试 import 链会读 config.json(db.engine 等),没有就生成一份 CI 专用
# (不进制品:package.sh 只打包 backend/migrations/alembic.ini/deploy/frontend/dist)
if [ ! -f config.json ]; then
  cat > config.json <<EOF
{"database": {"host": "127.0.0.1", "port": 5432, "user": "ci",
  "password": "${CI_DB_PASSWORD:-ci-dummy}", "dbname": "doc_review_ci"},
 "llm": {"api_key": "ci-dummy", "base_url": "http://127.0.0.1:9", "model": "ci-dummy"}}
EOF
  echo "[run_ci] 已生成 CI 专用 config.json"
fi

# ---- 测试白名单(新增测试按依赖归类追加;一次性脚本/需外部服务的不进 CI)----
# 不进 CI 的已知项及原因:
#   test_scan_anchors / test_anchor   依赖本机 fixture「POA 信息表.docx」(根目录一次性产物,不入库)
#   test_ocrapi_auth                  需要 ocrapi 源码,而 ocrapi 不在本仓库
#   test_text_extractor_sampling      硬编码 /tmp fixture,环境脆弱;非 CLAUDE.md 登记测试
#   test_local_ollama_detect          需要本地 ollama 服务
#   test_profile_api_import/gen_ceo_report  一次性参考脚本
PURE_TESTS="
test_split_service test_redactor
test_archive_detect_crud_clean test_ai_api_call_clean
test_doc_type_matcher test_profile_api_manifest test_extract_rules
test_review_scoring test_extract_multi test_image_preprocess test_field_validators
test_page_big_image test_text_extractor_docx_ocr
"
DB_TESTS="
test_worker_runner_claim test_event_service test_daily_report
test_doc_extract_mapping test_profile_crud test_profile_task_delete
test_person_dedup test_profile_cases_project test_profile_content_dedup
test_file_assign test_credibility test_person_merge test_name_en_expiry
test_profile_resume
"

fail=0
echo "===== 纯函数档 ====="
for t in $PURE_TESTS; do
  if $PY "tests/$t.py" >/tmp/ci_$t.log 2>&1; then
    echo "PASS $t"
  else
    echo "FAIL $t  (日志: /tmp/ci_$t.log)"; tail -5 "/tmp/ci_$t.log"; fail=1
  fi
done

echo "===== DB 档(重建 doc_review_ci) ====="
: "${CI_DB_PASSWORD:?需要 CI_DB_PASSWORD(ci 角色密码)}"
export PGPASSWORD="$CI_DB_PASSWORD"
export PGCLIENTENCODING=UTF8   # 集群 template1 是 SQL_ASCII,psycopg2 默认会用 ascii 编 SQL,中文迁移注释必炸
export DATABASE_URL="postgresql://ci:${CI_DB_PASSWORD}@127.0.0.1:5432/doc_review_ci"

psql -h 127.0.0.1 -U ci -d postgres -q <<'SQL'
SELECT pg_terminate_backend(pid) FROM pg_stat_activity
 WHERE datname='doc_review_ci' AND pid <> pg_backend_pid();
SQL
psql -h 127.0.0.1 -U ci -d postgres -q -c "DROP DATABASE IF EXISTS doc_review_ci"
# 必须显式 UTF8 + template0:本集群 template1 是 SQL_ASCII,默认建库会继承成 SQL_ASCII
psql -h 127.0.0.1 -U ci -d postgres -q -c "CREATE DATABASE doc_review_ci OWNER ci ENCODING 'UTF8' TEMPLATE template0"
$PY -m alembic upgrade head 2>&1 | tail -1

for t in $DB_TESTS; do
  if $PY "tests/$t.py" >/tmp/ci_$t.log 2>&1; then
    echo "PASS $t"
  else
    echo "FAIL $t  (日志: /tmp/ci_$t.log)"; tail -5 "/tmp/ci_$t.log"; fail=1
  fi
done
unset PGPASSWORD

if [ "$fail" -ne 0 ]; then
  echo "===== 有测试失败 ====="; exit 1
fi
echo "===== 全部通过 ====="
