# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> 另有 [AGENTS.md](AGENTS.md)：面向 AI 助手的结构化总览（项目结构/技术栈/命令/陷阱），与本文互补。本文侧重各流水线的设计决策与遗留注意事项；两份文档内容有重叠，改动任一时注意保持一致。

> **仓库可见性**：本仓库为 **Private**。`config.json` 含 API Key / DB 密码，已在 `.gitignore`，**切勿提交**。本地 git 历史此前已通过 `git checkout --orphan` 重置为干净 initial commit；本地若存在 `main-old-backup` 分支，仅保留在本地，**绝对不能 push**。

## 项目定位

智能文档审核工作台，面向移民/售后客户材料处理。当前主线是**文件留底检测/业务审核**，同时保留材料解析、Word 模板填写、PDF 拆分、URL 文件摘要等能力。

核心业务线：

1. **文件留底检测 / 业务审核**：业务方传入客户+项目+进展+文件列表（OSS URL）；后端 OCR/文本抽取 + LLM 按公司留底分类体系判定，持久化单文件结果、OCR 脱敏文本、批次总体报告，支持同 `(progress_id, file_id)` 的历史结果复用。**快速检测（upload/urls）已移除，业务审核是唯一入口**。
2. **AI 材料解析**：上传 PDF/图片 → OCR + LLM 提取结构化字段 → 人工复核 → 归档到客户档案。
3. **客户档案结构化生成**：从文件留底检测完成的 OCR 文件中，批量抽取客户/家庭成员/资产事实，自动写入客户档案结构化表；策略：**只补空字段，不覆盖已有非空人工数据**，避免误改。
4. **客户画像（Excel 导入）**：上传客户文件清单 Excel → 全量 OCR 入客户文件库（fresh 存原文）→ 关键词+LLM 筛出身份证/户口本/学位证/出生证明 4 类 → 按代码规则（`backend/extract_rules.py` 常量）提取 → 归因只补空写客户档案。方案见 [docs/09-客户画像-Excel导入方案.md](docs/09-客户画像-Excel导入方案.md)。
5. **Word 模板填写**：上传 docx 模板 → 扫描占位符/锚点 → 选择客户 → 从客户档案填值 → 输出 docx/PDF。
6. **PDF 拆分**：上传多证件合并 PDF → 全页 OCR + LLM 判断页边界 → 按证件类型拆为独立 PDF。
7. **URL 文件摘要**：输入文件 URL + 进展名 → 下载/OCR/抽文本 → LLM 摘要和相关性判断。

## 环境准备

新 clone 或首次运行时，参考 [AGENTS.md §5.1–5.3](AGENTS.md#51-环境初始化) 完成：

1. 创建并激活 Python 3.12 venv（`.venv312`）。
2. `pip install -r backend/requirements.txt`。
3. `cp config.json.example config.json` 并填入 `database`、`llm`、`file_url_service`。
4. `alembic upgrade head` 初始化数据库。

前端首次运行前在 `frontend/` 执行 `npm install`。

## 常用命令

```bash
# 后端：必须从 backend/ 目录启动，否则相对 import 会失败
cd e:/qoderproject/20260527/backend
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ../.venv312/Scripts/python.exe -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# OCR worker：独立进程,必须单独起,否则文件留底检测只入队不识别(一直 pending)
cd e:/qoderproject/20260527/backend
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ../.venv312/Scripts/python.exe -m worker_runner --worker-id worker-1

# Windows 本地一键起后端 + 1 个 worker(各自独立窗口)
start_backend.bat

# Windows 本地一键起前端 dev server
start_frontend.bat

# 前端
cd e:/qoderproject/20260527/frontend
npm run dev

# 前端生产构建
cd e:/qoderproject/20260527/frontend
npm run build

# 数据库迁移（alembic.ini 在项目根，DSN 优先 DATABASE_URL 环境变量,否则 config.json）
cd e:/qoderproject/20260527
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe -m alembic upgrade head
```

> **后端和 worker 是两个独立进程,本地开发要分别启动。** 改了 OCR/抽取/LLM 相关代码,uvicorn 的 `--reload` 不会重启 worker —— 必须手动重启 worker 才生效。

## 部署（Linux 生产环境）

**永久部署平台：Alibaba Cloud Linux 3 / CentOS 8+ ECS。** Windows 仅作开发环境。

> **当前实际服务器（2026-07-24 实录，与下文标准方案有出入，以此为准）**：`root@8.138.111.12`，SSH 用密钥 `C:\Users\zq\Downloads\doc.pem`（本机无 rsync，用 `tar czf - ... | ssh "tar xzf -"` 上传）。代码 `/opt/fastapi/`（backend + backend/venv，**后端 nohup 直跑 :8765 + worker nohup 直跑，无 systemd**）；前端 dist 在 **`/opt/vue3/dist`**（nginx default.conf,80 端口反代 `/api` → 8765）；DB 在本机 localhost:5432/doc_review。`config.json` 在 `/opt/fastapi/`（服务器自有配置，**上传时切勿覆盖**；auth 段 2026-07-24 已补齐，与本地一致）。迁移踩过的坑：服务器 alembic_version 曾停在 014 但 015/017 对象已手工建过——用 `alembic stamp` 对齐后再 upgrade（016 external_api_logs 是真缺的，正常执行）。重启注意：**远程命令行里 `pkill -f 'uvicorn main:app'` 会匹配到 bash -c 自身导致自杀**，用 `pkill -f '[u]vicorn main:app'` 或按 PID 杀。备份在 `/opt/backups/`。

完整方案在 [deploy/linux/](deploy/linux/)，**首次部署看 [deploy/linux/README.md](deploy/linux/README.md)**。日常流程：

```bash
# 本地：构建前端 + rsync 上传整个项目
cd e:/qoderproject/20260527
bash deploy/linux/05-upload.sh root@<服务器IP>

# 服务器：依赖/迁移变了才需要,普通代码改动跳过
sudo -u docreview bash /opt/doc-review/deploy/linux/02-install-app.sh

# 服务器：重启
sudo systemctl restart doc-review              # 后端代码改动
sudo systemctl restart doc-review-worker@1     # OCR/抽取/LLM/worker 代码改动(必须单独重启)
sudo systemctl reload nginx                    # 前端 dist 变化
```

关键约束：

- **业务审核已改为 DB 队列 + 多进程 worker 架构(方案二 2b)**：主进程(uvicorn)只接 HTTP、写 DB、跑 finalize 轮询 + watchdog;OCR/LLM 由独立的 `worker_runner.py` 进程通过 `SELECT FOR UPDATE SKIP LOCKED` 抢 `archive_detect_files` 的 pending 任务。状态全部落 DB,进程重启不丢任务。
- **uvicorn `--workers=1` 不能改**：主进程仍保留内存态 `_batch_status` 作为前端轮询 fast-path;多 uvicorn worker 会让该缓存分裂。worker 并发靠多起几个 `worker_runner` 进程,不是靠 uvicorn workers。
- **OCR worker 数 = 起几个 `worker_runner` 进程**。生产用 systemd 模板 [deploy/linux/doc-review-worker@.service](deploy/linux/doc-review-worker@.service)(`doc-review-worker@1/@2/...`);`doc-review.service` 用 `Wants=doc-review-worker@1.service` 在启动主服务时一并拉起 1 个 worker(但 `restart doc-review` 不会重启已在跑的 worker,改 OCR 代码要单独 `restart doc-review-worker@1`)。**4C/8G 小机器保持 1 个 worker**,OCR 串行,稳定优先。
- **OCR 引擎是 RapidOCR(onnxruntime)**,不是 PaddleOCR。所有 OCR 调用收口在 `ocr_service.run_ocr()`,内部把 RapidOCR 输出适配回旧 PaddleOCR 结构 `[[[bbox,(text,conf)],...]]`,下游零改动。模型权重随包内置,无需联网下载、无 libGL 依赖。
- **数据库连接优先用 `DATABASE_URL` 环境变量**，否则才回退到 `config.json`。生产环境 systemd unit 通过 [deploy/linux/app.env](deploy/linux/app.env)（不入库）注入。
- **`docx2pdf` 在 Linux 上不可用**：`backend/template_service.py:_convert_docx_to_pdf` 在 Windows 走 docx2pdf（Word COM），Linux 走 LibreOffice `soffice --headless`。两条路径都已落地，本地开发不受影响。
- **`.doc` 抽取优先纯 Python olefile**：`text_extractor._extract_doc` 优先用 `olefile` 解析 OLE2 复合文档(按 [MS-DOC] piece table 逐片解码,零系统依赖),失败再回退 `soffice`(LibreOffice 转 docx)→ `antiword`。生产 RHEL 系源里没有 antiword/libreoffice 也能靠 olefile 处理 .doc。三者全无才抛 ValueError。
- **健康检查**：`GET /api/healthz` 真查 DB 和 worker,nginx 反代到 `/healthz` 给外部监控用。`/api/archive-detect/admin/queue-stats` 只看 DB 队列深度。
- **Windows 专用部署脚本**（PowerShell 打包）已退役挪到 [deploy/windows/](deploy/windows/)，仅作历史保留。


测试为简单 `assert` 脚本风格，不依赖 pytest：

```bash
# 单个单元测试（assert 脚本风格，不依赖 pytest）
cd e:/qoderproject/20260527
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_split_service.py
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_redactor.py
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_scan_anchors.py
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_worker_runner_claim.py
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_event_service.py
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_archive_detect_crud_clean.py
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_ocrapi_auth.py   # ocrapi JWT/用户库/清洗(需 PyJWT/bcrypt,不依赖服务运行)
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_doc_type_matcher.py        # 证件关键词分类器(纯函数)
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_profile_excel_parse.py     # 客户清单 Excel 解析(读根目录真实例子文件)
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_extract_rules.py       # 提取规则常量(纯函数,不依赖 DB)
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_doc_extract_mapping.py     # 归因+只补空写库(依赖 DB)
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_review_scoring.py          # 质量评级打分(纯函数)
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_profile_crud.py             # 画像 v2 归因/字段写入语义(依赖 DB)

# 冒烟脚本（依赖运行中的服务）
cd e:/qoderproject/20260527
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/smoke/test_archive_detect_concurrent.py   # 业务审核并发(依赖后端+worker)
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/smoke/test_three_worker_throughput.py     # 3 worker 吞吐
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/smoke/test_ocrapi_ocr.py                  # ocrapi 冒烟(默认 http://localhost:8001)
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/smoke/e2e_v2_smoke.py                     # 模板 v2 流程(注意 BASE 硬编码 127.0.0.1:8765,按需改)
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/smoke/test_profile_import_e2e.py          # 客户画像端到端(真 LLM+真下载,数十分钟)
```

> 新增测试时保持同样风格：脚本自行把 `backend/` 插入 `sys.path`，用简单 `assert` 验证。

## 配置

`config.json` 是单文件配置，模板见 `config.json.example`：

```json
{
  "database": {"host": "localhost", "port": 5432, "user": "postgres", "password": "...", "dbname": "doc_review"},
  "llm": {"api_key": "...", "base_url": "https://...", "model": "...", "temperature": 0.1},
  "max_ocr_pages": 5,
  "document_types": ["身份证", "护照", "..."]
}
```

- LLM 走 OpenAI 兼容接口，模型 ID 完全由 `config.json` 驱动。
- `max_ocr_pages` 仅影响 AI 材料解析 `/api/upload`；文件留底检测当前走全页 OCR。
- `document_types` 仍用于证件解析和 PDF 拆分页分类；文件留底检测已改用 `llm_service.py` 中硬编码的公司售后留底分类体系。
- Windows 控制台运行后端时务必带 `PYTHONIOENCODING=utf-8 PYTHONUTF8=1`，否则中文 task_id / 文件名的 `print()` 可能触发 GBK 编码错误。

## 架构总览

```text
前端: Vue 3 + Element Plus + Vite + vue-router(hash)
  frontend/src/router.js                 路由: /, /clients, /parse, /template, /split, /summary, /archive-detect, /archive-admin, /file-info, /profile, /review-center, /events, /request-logs, /external-api-logs, /ai-api-calls, /child-age-leads
  frontend/src/api.js                    axios API 封装
  frontend/src/components/*.vue          各业务页面(含 ArchiveAdminPage / EventsPage / RequestLogsPage / ExternalApiLogsPage / ChildAgeLeadsPage)

后端: FastAPI + SQLAlchemy 2 async + Alembic
  backend/main.py                        FastAPI 入口 + 路由聚合 + startup/shutdown + 内存态轮询缓存
  backend/worker_runner.py               独立进程入口: SKIP LOCKED 抢 DB 任务 → OCR → LLM → 写终态(业务审核的实际执行者)
  backend/archive_detect_service.py      文件留底检测编排(提交入队、增量复用、watchdog 回收死 worker 任务、finalize 总报告)
  backend/llm_service.py                 LLM 调用封装与各业务 prompt(OpenAI 兼容,模型由 config.json 驱动)
  backend/ocr_service.py                 RapidOCR 引擎封装 + PDF/图片 OCR(run_ocr 统一入口)
  backend/text_extractor.py              PDF/图片/docx/doc/xls/pptx 统一文本抽取(文件留底检测复用)
  backend/file_fetcher.py                httpx 下载 URL/OSS 临时签名地址到临时文件 + 延迟清理
  backend/event_service.py + db/event_crud.py        业务事件流(批次/OCR/worker 崩溃等)写 system_events
  backend/middleware/request_log_middleware.py       纯 ASGI 中间件,只记 POST /business/batch 请求体到 api_request_logs
  backend/client_profile_service.py      客户档案结构化生成编排(后台任务,只补空不覆盖)
  backend/profile_import_service.py      客户画像编排(Excel 清单解析/run_import:取 OCR→分类→提取)
  backend/doc_type_matcher.py            证件类型关键词分类器(纯函数,4 类证件正负关键词评分)
  backend/extract_rules.py               证件字段提取规则(代码常量 + get_rule,原 doc_extract_rules 表迁来)
  backend/db/customer_file_crud.py       客户文件库(customer_files) + 导入任务 CRUD + OCR 复用查询
  backend/db/doc_extract_crud.py         提取结果 CRUD + 归因(find_person_match) + 只补空写库
  backend/template_service.py            Word 模板解析、锚点扫描、渲染
  backend/split_ocr_service.py           PDF 拆分专用全页 OCR(单线程,复用 ocr_service 全局引擎)
  backend/split_service.py               PDF 页范围规整与拆分
  backend/db/*.py                        ORM、engine、CRUD 模块

config.json                              DB + LLM + OCR/文档类型配置
output/                                  静态挂载为 /uploads/，保存 PNG/PDF/DOCX 等产物
temp/                                    上传/下载/模板解析临时文件
migrations/versions/                     Alembic 迁移(当前到 020_drop_extract_rules)
docs/                                    重构参考开发文档(01-系统概览 ~ 07-重构规划 + 客户数据库-PRD)
frontend2/DocReview.ArchiveDetect/       .NET 重构 PoC(目录名误导,是后端不是前端,见下节)
```

## 重构进行时：.NET 业务后端 + Python OCR 微服务

按 [docs/README.md](docs/README.md) 已确认的拆分决策：**OCR/文本抽取留在 Python、收敛为独立微服务；业务系统用 .NET 重写；PostgreSQL 保留；前端不动、只保 API 契约**。docs/ 下 01–07 是完整的现状文档与迁移规划，重构时以 [02-API接口清单.md](docs/02-API接口清单.md) + [03-数据库设计.md](docs/03-数据库设计.md) + [05-业务服务与队列.md](docs/05-业务服务与队列.md) 为 .NET 侧要复刻的契约。

- **`frontend2/DocReview.ArchiveDetect/`**：ASP.NET Core (**net10.0**) 重写 PoC，对标 FastAPI 的 `/api/archive-detect/*` 契约。EF Core/Npgsql 连**同一个 PG 库**；OCR 用 RapidOcrNet + 随仓库内置的 PP-OCRv4 ONNX 模型（`Models/v4/`）；`ArchiveWorker` 后台服务一并承担抢任务 + finalize + watchdog；JSON 序列化 snake_case 对齐前端契约。`dotnet run` 起在 `http://localhost:5001`，开发环境 Swagger 在 `/swagger`。
- **`ocrapi`（Python OCR 微服务）**：另一半拆分——独立 HTTP 服务（默认 `:8001`），JWT 鉴权（`/token` 登录，`/ocr` 需 token），bcrypt 用户库存 `ocrapi/users.json`，访问日志按天写 `ocrapi/logs/`（两者均已 gitignore）。**源码当前不在本仓库**，仓库内只有测试：`tests/test_ocrapi_auth.py`（单元）、`tests/smoke/test_ocrapi_ocr.py`（冒烟，`OCRAPI_BASE_URL/OCRAPI_USER/OCRAPI_PASSWORD/OCR_TEST_URL` 环境变量可配）。

## 数据库重点

主要表：

- `clients`：客户主表，`client_code` 是业务方稳定客户编码。
- `documents` / `client_info`：材料解析记录和 KV 兜底字段。
- `templates` / `template_fills`：Word 模板和填充历史。
- `split_tasks`：PDF 拆分任务，持久化状态和 ranges；7 天后清理磁盘文件但保留 DB 记录。
- `summaries`：URL 文件摘要历史。
- `archive_detect_batches`：文件留底检测批次；包含 `overall_verdict/overall_score/overall_reason` 当次总体判断、`stage(pre_submit/post_submit)`。
- `archive_detect_files`：单文件检测结果；含 `verdict/match_score/is_archival/confidence/reason/key_points/doc_category`、脱敏后的 `ocr_text`，业务模式下还有 `progress_id/file_id/version/deleted`；DB 队列字段 `status(pending/leased/fetching/ocr/llm/done/error)`、`worker_lease_until`、`retry_count`、`local_path`(upload 模式残留,现已停用)、`reuse_ocr_text`(重审入队预填的源 OCR 文本,非空则 worker 跳过下载+OCR)。`verdict` 除 match/partial/mismatch 外还有 **`no_text`**(OCR/抽取后无有效文字,标 done 不算失败、不参与总体判定)。
- `archive_detect_progress`：业务审核的进展包实体，`(client_id, progress_oid)` 唯一；存办理人、项目、项目详情、进展名称等。
- `archive_detect_folder_summaries`：进展包维度滚动总报告（多版本，后续阶段使用）。
- `client_profile_generation_tasks`：客户档案结构化生成任务；记录源文件、抽取结果、写入统计、状态。
- `system_events`：业务事件流(severity/category/message/context),前端 `/events` 页查看,GC 保留 30 天。
- `api_request_logs`：API 请求记录,中间件只记 `POST /api/archive-detect/business/batch` 的请求体,前端 `/request-logs` 页查看,GC 保留 30 天。
- `external_api_logs`：出站外部接口调用记录(`service=refresh_url`),记地址/请求参数/返回全文/耗时/成败,前端 `/external-api-logs` 页,GC 保留 30 天。URL 刷新在 `file_fetcher.refresh_download_url` 埋点(async create_task)。
- `ai_api_calls`：AI/LLM API 调用记录,记 operation/model/prompt/response/耗时/成败/业务上下文(batch_id/file_id/client_code/task_id),前端 `/ai-api-calls` 页,GC 保留 30 天。LLM 在 `llm_service._call_llm` 埋点--**注意用同步 `insert_ai_api_call_sync`**,因为它跑在 worker 线程(asyncio.to_thread)里,async 引擎连接池绑主 loop 不能跨线程用。**LLM 的 prompt/response 原文直存未脱敏**(业务决策,含脱敏前 OCR)。**入库前在 CRUD 层统一清洗**:去 NUL/控制字符(PG text 列不接受 ``,OCR 文本易混入,此前被 `except` 静默吞掉丢日志)、prompt/response 截断 50KB、error_msg 截断 2KB;列表接口只回 500 字预览,详情走单独的 `GET /api/admin/ai-api-calls/{row_id}` 拉全文。引擎层(asyncpg/psycopg2)已显式 `client_encoding=utf8`。
- `profile_import_tasks`：客户画像导入任务；一次 Excel 导入一行,记录进度与各类计数(4 类证件各筛出数/提取数/失败数/current_file/needs_review_count/household_id)。
- `customer_files`：客户文件库,`file_code` 全局唯一(重复导入只 re-link 不重复下载/OCR);存全量 OCR——**fresh=未脱敏原文**(与 ai_api_calls 存原文的既定决策一致)、reused=archive_detect 脱敏文本,`ocr_source` 区分;分类结果 `doc_type(id_card/hukou/degree_cert/birth_cert/other)` + `classify_by(keyword/llm/none)`;`local_path` 原件落盘 output/customer_files/(30 天 GC,DB/OCR 永留);`review_status/review_reason/quality_score` 复核与质量评级。
- ~~`doc_extract_rules`~~：**已废弃并 drop(migration 020)**。证件字段提取规则改为代码常量 `backend/extract_rules.py`(改规则=改该文件+重启 worker);`fields` 结构不变:带 `target:{entity:'person',column}`(column 解读为 profile 字段名),column=null 只抽不写,entity=asset/case 走资产/案件表。原 draft→activate→disable 生命周期已移除。
- `doc_extract_results`：一次提取一行;记用的规则版本、extracted(未脱敏原始抽取)、mapped(逐字段 written/updated/skipped_*)、write_stats;复核字段 `review_status(pending/confirmed/corrected/dismissed)/corrected/reviewed_by/at`。
- `profile_households` / `profile_persons` / `profile_person_fields` / `profile_assets` / `profile_cases`：**画像 v2 独立领域模型(migration 019),画像流水线只写这些表,不再写 clients/family_members**(老表留给 POA 模板等老流程,仅 `legacy_client_id` 软关联)。`profile_person_fields` 是字段级档案+证据链:`(person_id, field)` 唯一,带 `layer(verified官方证件/declared自报)` 与 `status(ai/confirmed/corrected)`;写入语义:人工字段不覆盖、declared 与 verified 同等对待(不区分来源层,layer 列仅作信息留存不参与覆盖决策)、同值跳过、复核修正永远覆盖。方案见 [docs/10-客户画像v2-复核与领域模型.md](docs/10-客户画像v2-复核与领域模型.md)。

## 文件留底检测 / 业务审核

入口路由在 `/api/archive-detect/*`，Swagger 已用 `tags=["文件留底检测"]` 分组，并给请求/响应字段加中文说明。

**业务审核是当前唯一入口**（快速检测已移除：后端 `/api/archive-detect/upload`、`/urls`、`/business/batch/upload` 端点及前端「快速检测」tab 均已删除；`source_kind=quick` 仅作为历史批次标签保留，`GET/DELETE /api/archive-detect/{batch_id}` 用于历史批次轮询/删除）。

- `POST /api/archive-detect/business/batch`：JSON + OSS URL。接口阶段只校验 + 写 DB(pending) + 秒回 `batch_id`,不下载、不 OCR;真正下载/OCR/LLM 由 worker 串行处理。URL 过期时 worker 用 `file_id` 调 `file_fetcher.fetch_url_to_temp_with_refresh` 刷新地址。前台本地上传入口已废弃（主进程瞬间写盘洪峰 4C/8G 扛不住），业务方先传 OSS 再提交 URL。
- `GET /api/archive-detect/business/batch/{batch_id}`：轮询完整结果，返回 client/progress/files/overall。
- 前端 `ArchiveDetectEntryPage.vue` 只接受 OSS URL;criteria 会根据客户/项目/进展/阶段自动预填(PSD 标准:服务启动即留底、软证据有效),用户手改后不再覆盖。**客户姓名/办理人由后端从 DB(`clients.name`/`archive_detect_progress.handler`)显式注入检测/总判 prompt**(含拼音/英文转写规则),不依赖前端 criteria 是否包含姓名。

关键逻辑：

- 同一进展包内 `(progress_id, file_id)` 命中历史 `done` 文件时严格复用旧结果，跳过 OCR/LLM；复用项 `elapsed_sec=0`，返回 `is_reused=true`。
- worker 处理新文件：`file_fetcher` 下载(必要时刷新 URL) -> `text_extractor.extract_text` -> `llm_service.detect_archival` -> `redactor` 脱敏 -> DB 终态写入 -> 删临时文件。业务方传的 `filename` 是权威可读名,优先保留;下载推断名仅在业务方没传时兜底(`filename = filename or fname`)。
- **URL 刷新接口**(`file_fetcher.refresh_download_url`)调业务方 `getFileDownloadUrl`,除 `file_id/type` 外还需带 `usr_login/operation_user/url` 三个身份参数(否则对方返回"没有登陆人不可查看"),值在 `config.json.file_url_service` 可配,默认 `Jason邹启/Jason邹启/batch`。
- **提交接口无限流**:业务审核提交只校验+写 DB(pending)+秒回,不再有队列水位/内存/磁盘的 429 准入(已移除);httpx 连接池 `max_connections=None` 不设上限。
- OCR 文本只以脱敏后的 `archive_detect_files.ocr_text` 入库；默认批次查询用 `defer` 不拉该大字段，单文件详情用 `get_file_detail`。
- 单文件 verdict 由 LLM 直接输出 `match/partial/mismatch`；`is_archival=(verdict=='match')`、`confidence=match_score` 用于向后兼容。OCR/抽取后无有效文字的文件不再标 error,而是 `verdict=no_text` + status=done(不重试),总体判定时从 done_items 排除;全批 no_text 则 overall=mismatch/0 + 说明。
- **重审/重跑/批量操作**(后台管理 `ArchiveAdminPage.vue`):
  - 单批「重审」-> `POST /api/archive-detect/rerun/{batch_id}?force_all=` -> `rerun_batch_inplace` 原地重跑(force_all 无视已有 AI 结果全跑,否则只补缺失)。
  - 「按新规则批量重判」-> `POST /admin/rejudge-overall` -> 只重跑 `judge_batch_overall`(每批 1 次 LLM),**不碰单文件、不 OCR/下载**;默认只刷 partial/mismatch,进度 `GET /admin/rejudge-overall/progress`。
  - 「批量重审」-> `POST /admin/rerun-files-batch` -> 对目标批次逐个 `rerun_batch_inplace(force_all)`,**重跑每个单文件**(复用 ocr_text,成本高、worker 串行),各批沿用自身 criteria;进度 `GET /admin/rerun-files-batch/progress`。生产别一次刷太多,会压垮 LLM。
- 批次总报告：所有文件完成后，优先调用 `llm_service.judge_batch_overall`（把全部文件明细 + criteria + stage 交给 LLM），让其综合判定 `overall_verdict/overall_score/overall_reason`。**判定核心口径遵循 PSD 业务标准(适用于所有进展)**:留底的本质是证明该进展对应的服务确已启动或发生,不是必须有官方回执类关键件。证据可以是官方文件(批复函/受理回执/递交确认),也可以是软证据组合(服务合同 + 聊天记录/邮件/确认截图,如客户确认转卖房产即视为该项服务已启动)。只要存在能证明服务已启动的证据(官方件或软证据组合任一即可)整批即 match;只有辅助性附件、无任何服务启动证据才 partial;完全无服务痕迹才 mismatch。该原则及衍生业务规则(转款凭证以金额币种为准不强制客户姓名、银行开户类项目账户核心材料属隐私有账户/开户/转款凭证任一即符合且银行通用材料属正常附带件、阶段错配最多 partial 不硬性否决、单份文件检测失败/加密只影响该文件不拖垮整批)已固化在单文件 prompt(`_build_archive_detect_prompt`)和总判 prompt(`_build_judge_batch_overall_prompt`)的判定指南中。LLM 调用失败时回退旧的**规则平均分**（avg≥80->match / ≥50->partial / <50->mismatch）+ `summarize_batch` 文本兜底。finalize 统一由主进程 `_batch_finalize_poll` 轮询触发 `_generate_batch_overall`（快速检测路径的 `_orchestrate` 已随快速检测一并删除）。
- 公司售后留底分类体系硬编码在 `llm_service.py` 的 `ARCHIVE_CATEGORIES_FULL/SIMPLE`，业务模式会传 `stage=pre_submit|post_submit` 让 LLM 感知递交前/后分类。

## 客户档案结构化生成

入口路由在 `/api/client-profile/*`，Swagger 已用 `tags=["客户档案生成"]` 分组：

流程：
1. 用户在前端选择客户，系统从该客户的 `archive_detect_files` 中列出所有 `done` 且有 `ocr_text` 的文件作为候选
2. 用户勾选候选文件 → 提交生成任务，后台异步处理
3. 对每个选中文献：`llm_service.extract_client_profile_facts` 抽取客户基本信息、家庭成员、资产等结构化事实
4. 按 **只补空，不覆盖** 策略写入数据库：已有非空值保持人工修改不变，仅当字段为空时写入 AI 抽取结果
5. 写入目标：`clients`（客户基本信息）→ `family_members`（家庭成员）→ `assets`（资产）→ `client_info`（Extra 兜底）

关键逻辑：
- 后台异步任务：`asyncio.create_task(_generate_background)`，前端可轮询任务状态
- 写入策略保证人工数据主权：AI 只做补充，不覆盖已有内容
- 任务完成后可在前端查看抽取结果和写入统计

## 其他流水线要点

### AI 材料解析

`POST /api/upload` → `asyncio.create_task(_process_file_background)`：
1. `ocr_service.process_file` OCR；图片型 PDF 受 `config.json.max_ocr_pages` 限制。
2. `llm_service.detect_and_extract` 一次完成类型检测 + 字段提取。
3. 内存 `_task_status` 供轮询，DB `documents` 存最终结果。
4. `PUT /api/result/{task_id}` 人工复核后归档到 `clients` / `family_members` / `assets` / `client_info`。

### Word 模板填写

- `template_service` 负责 mammoth HTML 预览、anchor 扫描、marker 注入和 docx 渲染。
- v2 分阶段接口：`POST /api/templates/parse`(解析+暂存 `temp_token`) → `POST /api/templates/quick-save` → `POST /api/templates/{id}/map-client` → `POST /api/templates/{id}/generate`；端到端冒烟 `tests/smoke/e2e_v2_smoke.py`。
- docx 预览走 `soffice → PDF → pypdfium2 PNG`；LibreOffice 缺失时 `pages=[]`，前端降级到 HTML。
- `docx2pdf` 强依赖 Windows + Word；线程内必须 COM 初始化。

### PDF 拆分

- `POST /api/split` 后台 `_process_split_background`。
- 不复用 `ocr_service.process_file`，而是用 `split_ocr_service.split_extract_all_pages` 全页 OCR、200dpi、单线程(复用 ocr_service 全局 RapidOCR 引擎)。
- DB `split_tasks` 是权威状态；内存 `_split_task_status` 只做轮询 fast-path。
- `/api/split/history` 必须声明在 `/api/split/{task_id}` 之前。

### 审核任务管理后台

- 路由 `/api/archive-detect/admin/*`，前端 `ArchiveAdminPage.vue`（`/archive-admin`）。
- 筛选:状态/来源/批次ID/客户/进展/日期范围 + **总体判断多选(match/partial/mismatch)** + **仅看有失败文件**(EXISTS 子查询)。
- 详情弹窗展示批次全量信息(批次/客户/进展/办理人/项目/判定标准/总体/识别完成时间等),文件表含「文件编码(file_id)」列;数据源 `pollBusinessBatch`(business)或 `pollArchiveDetect`(历史 quick 批次)兜底。
- 写操作:单批重审(`rerun/{batch_id}`)、按新规则批量重判(`admin/rejudge-overall`)、批量重审(`admin/rerun-files-batch`)—— 见上文「重审/重跑/批量操作」。

### 文件信息（文件维度查询）

- 前端 `FileInfoPage.vue`（`/file-info`）→ `GET /api/admin/file-infos`（Swagger tag「文件信息」）→ `archive_detect_crud.admin_list_files`。
- 以**单文件为维度**跨批次查询，每行带批次/客户/进展上下文；支持批次号、文件编码、文件名、状态、判定、客户编码/姓名、进展名、办理人模糊筛选 + limit/offset 分页。

### 可观测性：事件流 + 请求记录 + 外部接口记录

- **事件流**：`event_service.log_event(severity, category, message, context)` fire-and-forget 写 `system_events`,前端 `/events`(EventsPage.vue)。category 常量在 event_service.py(batch.*/file.*/worker.crash/llm.timeout 等)。
- **请求记录**：纯 ASGI 中间件 `request_log_middleware`(在 main.py 用 `app.add_middleware` 注册,不能用 `BaseHTTPMiddleware`——它读 body 会噎死下游 Pydantic)。只记 `POST /api/archive-detect/business/batch` 的 JSON 请求体,前端 `/request-logs`(RequestLogsPage.vue)。
- **外部接口记录**：URL 刷新等非 AI 出站调用记 `external_api_logs`,前端 `/external-api-logs`(ExternalApiLogsPage.vue)。
- **AI/LLM 调用记录**：所有 LLM 调用记 `ai_api_calls`,前端 `/ai-api-calls`(AiApiCallsPage.vue),支持按 operation/model/batch_id/file_id/client_code/task_id 筛选。见「数据库重点」里的埋点说明。
- 四张表(system_events / api_request_logs / external_api_logs / ai_api_calls)都在 `_split_cleanup_loop` 里 GC 30 天。

### 销售线索：子女年龄

- 路由 `/api/sales/child-age-leads`，逻辑在 `backend/db/sales_crud.py`。
- 从 `family_members` 表中 `relation in ('child','子女','子','女','儿子','女儿','son','daughter',...)` 的记录算年龄；带 `min_age/max_age` 筛选时在 Python 层过滤（避免复杂 SQL），`total` 可能略有偏差是当前的可接受 MVP。

## 客户画像（Excel 导入）

入口路由 `/api/profile/*`（tag「客户画像」）+ `/api/doc-extract/*`（tag「信息提取」），前端 `/profile`(ProfilePage.vue)。方案细节见 [docs/09-客户画像-Excel导入方案.md](docs/09-客户画像-Excel导入方案.md)（流水线）与 [docs/10-客户画像v2-复核与领域模型.md](docs/10-客户画像v2-复核与领域模型.md)（**v2 领域模型与复核,当前实现**）。

流程：`POST /api/profile/import` 上传清单 Excel（解析兼容原文件错别列名"文件啊名称"；主客户=客户姓名列众数，建/联 `profile_households` 家庭,老 clients 仅软关联）→ 落 `customer_files` → 主进程 `asyncio.create_task(run_import)` 串行处理每个文件：

1. **取 OCR**：先 `customer_file_crud.find_reusable_ocr(file_code)` 全局查 `archive_detect_files` 同 file_id 最新 done 且有 ocr_text 行（复用脱敏文本）；没有再 `file_fetcher.refresh_download_url(file_code)` 刷新地址 → 下载 → `text_extractor.extract_text`。fresh 存**未脱敏原文**;**原件移存 output/customer_files/ 留存 30 天**(GC 挂 `_split_cleanup_loop`,DB/OCR 永留;`GET /api/profile/files/{id}/raw` 在线查看,已清理则按需重下顺延)。
2. **分类**：`doc_type_matcher.classify`（纯函数：strong/positive/negative 关键词评分 + 文件夹/相对路径线索，≥60 且领先 ≥10 定类）→ 置信不足才 `llm_service.recognize_doc_type` 兜底（2000 字头,失败→other 不抛）。
3. **提取**（12 类可配规则：id_card/hukou/degree_cert/birth_cert/passport/kyc_form/marriage_cert/property_cert/no_crime/approval/submission/receipt）：**OCR 乱码先拒提**(`review_service.is_garbled`,防垃圾人名错误建人)→ 读该类型 active 规则 → `llm_service.extract_doc_fields` 按规则 fields JSONB 提取 → 清洗（脱敏占位词 `[身份证]` 等记 `skipped_masked` 不写库不参与归因）→ `profile_crud.find_person_match`（家庭内 证件号→姓名→**拼音名**(name_en 词序无关,英文证件用),查不到新建 person `relation_to_main='待确认'`;**有 entity=case 字段时不要求姓名归因**）→ `apply_extracted_fields_v2` 写 `profile_person_fields`（**人工字段不覆盖、declared 与 verified 同等(不区分来源层)、复核修正永远覆盖**;特殊字段 `_relation`=与户主关系,仅在'待确认'时落地到 person.relation_to_main）→ `doc_extract_results` 留痕。**规则字段 `target.entity='asset'` 时走 `apply_extracted_asset` 写 `profile_assets`**（房产证类;attrs JSONB 存 key:value,**去重靠 AI：家庭内同类型无候选直接新建；有候选时调 `llm_service.judge_asset_duplicate`(operation=asset_dedup) 判定,match_id + confidence≥60 才合并,LLM 异常降级新建**,仅 status='ai' 可更新;画像接口/弹窗带「家庭资产」区块）;**`target.entity='case'` 时走 `apply_case_milestones` 写 `profile_cases`**（递交/签收/批复里程碑,家庭单案件,里程碑按 name upsert,状态 签收>交付>获批>递交 派生;画像弹窗「案件时间线」tab）。
4. **质量评级**：`review_service.evaluate_file_quality` 纯规则打分（无文本/乱码/过短/提取异常/no_person/证件号脱敏/分类置信低）→ `customer_files.review_status/quality_score` + 任务 `needs_review_count`。

复核闭环：`GET /api/review/files` 队列（质量分升序，**不传 import_task_id 即跨任务全局队列**）→ 画像页红色横幅 或 全局复核中心页（`/review-center`，ReviewCenterPage，导航「复核中心」）→ 复核抽屉三栏（原件|OCR|字段表单+归属选择，共享组件 `ReviewDrawer.vue`，prop `importTaskId` 可选 + `@done`；标签函数在 `utils/labels.js`）→ `confirm/correct/dismiss`(correct 永远覆盖写 person_fields 并留 `corrected` 痕)。

完备度矩阵：`GET /api/profile/tasks/{id}/matrix`（`profile_crud.build_completeness_matrix`，纯查询无 LLM）——人×材料类型（身份证/护照/户口本/出生证明/结婚证/无犯罪/学位证），格值 ok/warn/missing/na。文件类型归并：`resolve_matrix_type`（doc_type 优先,无犯罪/结婚证等靠文件名+文件夹提示词）；人档关联：提取归因(person_id)+文件名含人名；户口本/结婚证为家庭-夫妻共用件不按人名过滤;**有任一可用文件即 ok,全部待复核才 warn**。前端画像弹窗「完备度矩阵」tab,黄格点击直达复核。

护照到期提醒：`profile_crud.passport_expiry_info`/`attach_passport_expiry` 在画像接口给每人挂 `passport_expiry={date,days_left,level}`（level: expired / expiring≤180 天 / ok，阈值常量 `PASSPORT_EXPIRY_WARNING_DAYS`，移民递签 6 个月惯例）；前端画像弹窗顶部横幅+「护照到期日期」字段内联标签，全部 ok 时不渲染。**只提醒护照**（无犯罪等其他证件明确不提醒）。

交叉验证提醒：`profile_crud._normalize_field_value`/`collect_field_conflicts`/`attach_field_conflicts` 在画像接口给每人挂 `field_conflicts`——同一人同一身份字段（白名单 8 个 verified 字段）从多份证件提取的值不一致时标黄（成员卡字段「多源」可点按钮带各来源 tooltip + 顶部横幅,sources 带 customer_file_id 供按文件拉原件）,**AI 只提示不改值,人工点「多源」在弹窗里编辑确定**。归一化要点：拼音名忽略大小写与空格/粘连（NICHENG==NI CHENG，词序仍比对）、日期多格式、性别中英映射、masked 剔除；数据来自 `doc_extract_results.mapped`(key→extracted 取值）+`write_stats.person_id` 归因。

关键逻辑：

- **幂等**：`file_code` 全局唯一；重复上传同一 Excel 新建 task 但已 done 文件只 re-link 不重新下载/OCR；提取按当前代码规则重跑写新结果行;person_fields 同值跳过(skipped_same)保证数据层幂等。
- **规则维护改代码常量**：规则在 `backend/extract_rules.py` 的 `EXTRACT_RULES` dict;改规则=改该文件+重启 worker(`RULES_VERSION` 手动 +1 便于溯源)。无 Swagger 端点、无前端页。
- **LLM JSON 容错**：`extract_doc_fields` 解析失败重试一次(模型偶发字符串内未转义双引号);prompt 已要求值内引用用中文「」。
- **复用脱敏文本的表现**：证件号抽成 `[身份证]` → `skipped_masked`,归因退化为按姓名并标 `masked_id` 待复核；姓名/性别/出生日期等不脱敏字段仍可写入。
- 加新证件类型：`extract_rules.py` 加一条规则 + matcher 加组关键词 + `llm_service.DOC_EXTRACT_TYPES`/recognize 白名单加一项(小改三处);若要写新目标表(如资产)再扩 `target.entity` 分派。
- **前端交互(画像弹窗)**：人员卡片字段按 4 大组分段(基础个人信息/护照信息/公司收入/其他证件)+分割线,组内字段横向 3 列(`utils/labels.js` 的 `groupPersonFields`);人员卡 **inline 编辑**(编辑→字段变 input→保存/取消,`POST /api/profile/persons/{id}/field` 走 `correct_person_field`);「查看文件」按钮开 `PersonEditDrawer`(左人员文件列表+右原件);字段「多源」按钮开**多源字段核对抽屉**(上字段编辑+确定,下左来源文件列表+右原件,保存同 `correct_person_field`)。
- **文件清单 tab**：上方文件表格(含「提取」列=`latest_extract_status`,`list_task_files` 用 DISTINCT ON 取最新提取状态)+点行/查看详情弹三栏弹窗(左原件 iframe/中 OCR/右提取结果详情,`profile.extractions` 按 customer_file_id 前端过滤);三栏详情已从 inline 改弹窗。
- **抽屉并排模式**：多源核对/查看文件抽屉 `:modal=false` + `modal-class=side-drawer-overlay`(CSS pointer-events none 穿透 + 抽屉 auto),客户画像 el-dialog 用 `profileShift` 动态让出右侧(`:width=profileDialogWidth` + marginRight),两者并排同时操作;抽屉左边缘可拖拽调宽;关闭客户画像 watch 联动关右侧抽屉。
- **任务列表**：状态+客户名查询 + 分页(默认 10 条,10/25/50/100);客户画像菜单页样式对齐请求记录页(白色顶栏+灰背景+filter-card 查询区+表格 card)。
- **文件预览**：画像内原件只用 `img`(图片)/`iframe`(PDF)预览,其他类型(xlsx/doc 等)提示"不支持在线预览",避免 iframe 触发浏览器下载。
- 单文件异常只标 error 不杀任务；任务级事件 `profile.import.done/error`、提取事件 `extract.done/error/skip` 进 `system_events`。

## 临时文件清理（Windows 重要）

`file_fetcher.cleanup_temp_file` 用于在文件留底检测处理完一个 URL 后删除 `temp/fetched/<uuid>_xxx.pdf`。Windows 上 pdfplumber/OCR 句柄释放有延迟，立即 `os.remove` 会报 `WinError 32 文件被占用`。

实现策略：

1. 立即尝试 → 失败 `time.sleep(0.5)` 重试一次 → 仍失败丢进模块级延迟队列 `_pending_cleanup`。
2. 启动事件挂 `asyncio.create_task(file_fetcher.periodic_cleanup_task())`：
   - 启动时扫一次 `temp/fetched/`，删 1 小时前的残留兜底。
   - 之后每 60 秒处理一次延迟队列，能删则删，删不动重新排队。

这是**业务无影响的收尾清理**，HTTP 仍返回 200。日志里看到「已加入延迟清理队列」是正常的，不需要告警。

## 已知遗留/注意事项

- **业务审核文件卡在 `pending` 不动 = worker 没起**。worker 是独立进程,本地/服务器都要单独启动;不是 uvicorn 的一部分。
- **重审/重跑已统一走 worker DB 队列**：`submit_recheck_batch`(新建 recheck 批次)、`rerun_batch_inplace`(原地重跑)都是写 `status='pending'` 行(有 ocr_text 的写进 `reuse_ocr_text` 让 worker 跳过下载+OCR、只重跑 LLM)→ worker 消化 → 主进程 `_batch_finalize_poll` 轮询生成 overall。老的主进程 fan-out 函数(`_orchestrate_recheck`/`_process_one_recheck`/`_finalize_overall_for_batch`/`_orchestrate_rerun`)已删除,不要再引用。
- `archive_detect/` 独立子项目已迁出到 `E:\qoderproject\archive_detect\`；仓库内如残留空目录不要依赖。
- `archive_detect_files.content_sha256` 列已建但当前不写值；增量复用依赖业务方传稳定 `file_id`。
- `archive_detect_folder_summaries` 表已建，进展包维度滚动总报告是后续阶段，不要误以为当前已写入。
- `pdf_ocr.py` 单文件 CLI 若存在，不属于 web 流程，改 web 流水线时不需要同步改它。
- 业务接口不加 API Key 鉴权，当前假定由网络层隔离。
- **客户档案生成的候选列表不携带 `ocr_text`**：`client_profile_crud.list_source_files_for_client` 只返回元数据，生成阶段 `client_profile_service._generate_background` 再按 `id` 重查 `ocr_text` 喂给 LLM，避免大文本反复传输。
- **`/api/client-profile/generate/{client_id}` (POST) 与 `/api/client-profile/generate/{task_id}` (GET) 共用同一前缀**：FastAPI 按 method 区分，但任何新增 GET 子路径必须放在 `/generate/list/{client_id}` 这类更具体的路由之前，否则会被 `{task_id}` 抢匹配。
- **`frontend2/` 目录名有误导性**：内容是 .NET 业务后端重写 PoC，不是前端（见「重构进行时」）。
- **`backend/backfill_done_files.py` 是一次性脚本**：回填历史批次 `done_files` 计数，非常驻流程。
- **根目录一次性产物勿当流程依赖**：`gen_ceo_report.py`、`o2.txt`、`*.docx` 汇报文档是临时汇报产物；`tests/test_archive_detect_queue.py.deprecated` 是退役测试留档。
