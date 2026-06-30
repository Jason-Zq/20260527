# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目定位

智能文档审核工作台，面向移民/售后客户材料处理。当前主线是**文件留底检测/业务审核**，同时保留材料解析、Word 模板填写、PDF 拆分、URL 文件摘要等能力。

核心业务线：

1. **文件留底检测 / 业务审核**：快速检测上传/URL 文件，或由业务方传入客户+项目+进展+文件列表；后端 OCR/文本抽取 + LLM 按公司留底分类体系判定，持久化单文件结果、OCR 脱敏文本、批次总体报告，支持同 `(progress_id, file_id)` 的历史结果复用。
2. **AI 材料解析**：上传 PDF/图片 → OCR + LLM 提取结构化字段 → 人工复核 → 归档到客户档案。
3. **客户档案结构化生成**：从文件留底检测完成的 OCR 文件中，批量抽取客户/家庭成员/资产事实，自动写入客户档案结构化表；策略：**只补空字段，不覆盖已有非空人工数据**，避免误改。
4. **Word 模板填写**：上传 docx 模板 → 扫描占位符/锚点 → 选择客户 → 从客户档案填值 → 输出 docx/PDF。
5. **PDF 拆分**：上传多证件合并 PDF → 全页 OCR + LLM 判断页边界 → 按证件类型拆为独立 PDF。
6. **URL 文件摘要**：输入文件 URL + 进展名 → 下载/OCR/抽文本 → LLM 摘要和相关性判断。

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
- **`.doc` 抽取靠系统 antiword**：`text_extractor._extract_doc` subprocess 调 `antiword -m UTF-8.txt`。服务器需 `dnf/apt install antiword`(已写进 01-server-setup.sh);中文 .doc 上线前必须用真实文件验证 mapping。本地 Windows 没 antiword 会返回明确 ValueError。
- **健康检查**：`GET /api/healthz` 真查 DB 和 worker,nginx 反代到 `/healthz` 给外部监控用。`/api/archive-detect/admin/queue-stats` 只看 DB 队列深度。
- **Windows 专用部署脚本**（PowerShell 打包）已退役挪到 [deploy/windows/](deploy/windows/)，仅作历史保留。


测试为简单 `assert` 脚本风格，不依赖 pytest：

```bash
# 单个单元测试
cd e:/qoderproject/20260527
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_split_service.py
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_redactor.py

# 文件留底检测冒烟脚本（依赖运行中的后端）
cd e:/qoderproject/20260527
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/smoke/test_archive_detect.py

# 模板锚点扫描测试（不调 LLM）
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_scan_anchors.py
```

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
  frontend/src/router.js                 路由: /, /clients, /parse, /template, /split, /summary, /archive-detect, /archive-admin, /events, /request-logs, /child-age-leads
  frontend/src/api.js                    axios API 封装
  frontend/src/components/*.vue          各业务页面(含 ArchiveAdminPage / EventsPage / RequestLogsPage / ChildAgeLeadsPage)

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
  backend/template_service.py            Word 模板解析、锚点扫描、渲染
  backend/split_ocr_service.py           PDF 拆分专用全页 OCR(单线程,复用 ocr_service 全局引擎)
  backend/split_service.py               PDF 页范围规整与拆分
  backend/db/*.py                        ORM、engine、CRUD 模块

config.json                              DB + LLM + OCR/文档类型配置
output/                                  静态挂载为 /uploads/，保存 PNG/PDF/DOCX 等产物
temp/                                    上传/下载/模板解析临时文件
migrations/versions/                     Alembic 迁移(当前到 014_api_request_logs)
```

## 数据库重点

主要表：

- `clients`：客户主表，`client_code` 是业务方稳定客户编码。
- `documents` / `client_info`：材料解析记录和 KV 兜底字段。
- `templates` / `template_fills`：Word 模板和填充历史。
- `split_tasks`：PDF 拆分任务，持久化状态和 ranges；7 天后清理磁盘文件但保留 DB 记录。
- `summaries`：URL 文件摘要历史。
- `archive_detect_batches`：文件留底检测批次；包含 `overall_verdict/overall_score/overall_reason` 当次总体判断。
- `archive_detect_files`：单文件检测结果；含 `verdict/match_score/is_archival/confidence/reason/key_points/doc_category`、脱敏后的 `ocr_text`，业务模式下还有 `progress_id/file_id/version/deleted`；DB 队列字段 `status(pending/leased/fetching/ocr/llm/done/error)`、`worker_lease_until`、`retry_count`、`local_path`(upload 模式残留,现已停用)。
- `archive_detect_progress`：业务审核的进展包实体，`(client_id, progress_oid)` 唯一；存办理人、项目、项目详情、进展名称等。
- `archive_detect_folder_summaries`：进展包维度滚动总报告（多版本，后续阶段使用）。
- `client_profile_generation_tasks`：客户档案结构化生成任务；记录源文件、抽取结果、写入统计、状态。
- `system_events`：业务事件流(severity/category/message/context),前端 `/events` 页查看,GC 保留 30 天。
- `api_request_logs`：API 请求记录,中间件只记 `POST /api/archive-detect/business/batch` 的请求体,前端 `/request-logs` 页查看,GC 保留 30 天。

## 文件留底检测 / 业务审核

入口路由在 `/api/archive-detect/*`，Swagger 已用 `tags=["文件留底检测"]` 分组，并给请求/响应字段加中文说明。

模式：

1. **快速检测**
   - `POST /api/archive-detect/upload`
   - `POST /api/archive-detect/urls`
   - 前端 `ArchiveDetectEntryPage.vue` 的「快速检测」tab，保留自由判定提示词。

2. **业务审核**（当前主线,URL/OSS 模式）
   - `POST /api/archive-detect/business/batch`：JSON + OSS URL。接口阶段只校验 + 写 DB(pending) + 秒回 `batch_id`,不下载、不 OCR;真正下载/OCR/LLM 由 worker 串行处理。URL 过期时 worker 用 `file_id` 调 `file_fetcher.fetch_url_to_temp_with_refresh` 刷新地址。
   - `POST /api/archive-detect/business/batch/upload`：**已停用,返回 410**。前台本地上传会让主进程瞬间写盘洪峰(4C/8G 扛不住),改为业务方先传 OSS 再提交 URL。前端业务审核 tab 已移除上传入口。
   - `GET /api/archive-detect/business/batch/{batch_id}`：轮询完整结果，返回 client/progress/files/overall。
   - 前端 `ArchiveDetectEntryPage.vue` 的「业务审核」tab 默认打开,只接受 OSS URL;criteria 会根据客户/项目/进展/阶段自动预填，用户手改后不再覆盖。

关键逻辑：

- 同一进展包内 `(progress_id, file_id)` 命中历史 `done` 文件时严格复用旧结果，跳过 OCR/LLM；复用项 `elapsed_sec=0`，返回 `is_reused=true`。
- worker 处理新文件：`file_fetcher` 下载(必要时刷新 URL) → `text_extractor.extract_text` → `llm_service.detect_archival` → `redactor` 脱敏 → DB 终态写入 → 删临时文件。
- OCR 文本只以脱敏后的 `archive_detect_files.ocr_text` 入库；默认批次查询用 `defer` 不拉该大字段，单文件详情用 `get_file_detail`。
- 单文件 verdict 由 LLM 直接输出 `match/partial/mismatch`；`is_archival=(verdict=='match')`、`confidence=match_score` 用于向后兼容。
- 批次总报告：所有文件完成后，规则计算 `overall_verdict/overall_score`，再调用 `llm_service.summarize_batch` 生成 `overall_reason`；失败时用规则文本兜底。
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
- docx 预览走 `soffice → PDF → pypdfium2 PNG`；LibreOffice 缺失时 `pages=[]`，前端降级到 HTML。
- `docx2pdf` 强依赖 Windows + Word；线程内必须 COM 初始化。

### PDF 拆分

- `POST /api/split` 后台 `_process_split_background`。
- 不复用 `ocr_service.process_file`，而是用 `split_ocr_service.split_extract_all_pages` 全页 OCR、200dpi、单线程(复用 ocr_service 全局 RapidOCR 引擎)。
- DB `split_tasks` 是权威状态；内存 `_split_task_status` 只做轮询 fast-path。
- `/api/split/history` 必须声明在 `/api/split/{task_id}` 之前。

### 审核任务管理后台（只读）

- 路由 `/api/archive-detect/admin/*`，前端 `ArchiveAdminPage.vue`（`/archive-admin`）。
- 支持按状态/来源/客户/进展/日期范围筛选历史批次，详情弹窗复用 `pollBusinessBatch`（business 批次）或 `pollArchiveDetect`（快速检测批次）兜底。

### 可观测性：事件流 + 请求记录

- **事件流**：`event_service.log_event(severity, category, message, context)` fire-and-forget 写 `system_events`,前端 `/events`(EventsPage.vue)。category 常量在 event_service.py(batch.*/file.*/worker.crash/llm.timeout 等)。
- **请求记录**：纯 ASGI 中间件 `request_log_middleware`(在 main.py 用 `app.add_middleware` 注册,不能用 `BaseHTTPMiddleware`——它读 body 会噎死下游 Pydantic)。只记 `POST /api/archive-detect/business/batch` 的 JSON 请求体,前端 `/request-logs`(RequestLogsPage.vue)。两张表都在 `_split_cleanup_loop` 里 GC 30 天。

### 销售线索：子女年龄

- 路由 `/api/sales/child-age-leads`，逻辑在 `backend/db/sales_crud.py`。
- 从 `family_members` 表中 `relation in ('child','子女','子','女','儿子','女儿','son','daughter',...)` 的记录算年龄；带 `min_age/max_age` 筛选时在 Python 层过滤（避免复杂 SQL），`total` 可能略有偏差是当前的可接受 MVP。

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
- **`archive_detect_service.py` 里残留的 `_process_one_recheck` 等老 fan-out 函数**(重新审核路径)还没完全迁到新 worker 架构,改 recheck 时注意。
- `archive_detect/` 独立子项目已迁出到 `E:\qoderproject\archive_detect\`；仓库内如残留空目录不要依赖。
- 快速检测 tab 的"文件不入库、处理完即删"文案与当前 DB 双写持久化已不完全一致；改文案时要谨慎区分快速检测和业务审核。
- `archive_detect_files.content_sha256` 列已建但当前不写值；增量复用依赖业务方传稳定 `file_id`。
- `archive_detect_folder_summaries` 表已建，进展包维度滚动总报告是后续阶段，不要误以为当前已写入。
- `pdf_ocr.py` 单文件 CLI 若存在，不属于 web 流程，改 web 流水线时不需要同步改它。
- 业务接口不加 API Key 鉴权，当前假定由网络层隔离。
- **客户档案生成的候选列表不携带 `ocr_text`**：`client_profile_crud.list_source_files_for_client` 只返回元数据，生成阶段 `client_profile_service._generate_background` 再按 `id` 重查 `ocr_text` 喂给 LLM，避免大文本反复传输。
- **`/api/client-profile/generate/{client_id}` (POST) 与 `/api/client-profile/generate/{task_id}` (GET) 共用同一前缀**：FastAPI 按 method 区分，但任何新增 GET 子路径必须放在 `/generate/list/{client_id}` 这类更具体的路由之前，否则会被 `{task_id}` 抢匹配。
