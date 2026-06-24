# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目定位

智能文档审核工作台，面向移民/售后客户材料处理。当前主线是**文件留底检测/业务审核**，同时保留材料解析、Word 模板填写、PDF 拆分、URL 文件摘要等能力。

核心业务线：

1. **文件留底检测 / 业务审核**：快速检测上传/URL 文件，或由业务方传入客户+项目+进展+文件列表；后端 OCR/文本抽取 + LLM 按公司留底分类体系判定，持久化单文件结果、OCR 脱敏文本、批次总体报告，支持同 `(progress_id, file_id)` 的历史结果复用。
2. **AI 材料解析**：上传 PDF/图片 → OCR + LLM 提取结构化字段 → 人工复核 → 归档到客户档案。
3. **Word 模板填写**：上传 docx 模板 → 扫描占位符/锚点 → 选择客户 → 从客户档案填值 → 输出 docx/PDF。
4. **PDF 拆分**：上传多证件合并 PDF → 全页 OCR + LLM 判断页边界 → 按证件类型拆为独立 PDF。
5. **URL 文件摘要**：输入文件 URL + 进展名 → 下载/OCR/抽文本 → LLM 摘要和相关性判断。

## 常用命令

```bash
# 后端：必须从 backend/ 目录启动，否则相对 import 会失败
cd e:/qoderproject/20260527/backend
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ../.venv312/Scripts/python.exe -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# 前端
cd e:/qoderproject/20260527/frontend
npm run dev

# 前端生产构建
cd e:/qoderproject/20260527/frontend
npm run build

# 数据库迁移（alembic.ini 在项目根，DSN 从 config.json 读取）
cd e:/qoderproject/20260527
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe -m alembic upgrade head
```

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
  frontend/src/router.js                 路由: /, /parse, /template, /split, /summary, /archive-detect
  frontend/src/api.js                    axios API 封装
  frontend/src/components/*.vue          各业务页面

后端: FastAPI + SQLAlchemy 2 async + Alembic
  backend/main.py                        FastAPI 入口 + 路由聚合
  backend/llm_service.py                 LLM 调用封装与各业务 prompt
  backend/ocr_service.py                 PDF/图片 OCR 通用入口
  backend/text_extractor.py              PDF/图片/docx 统一文本抽取（文件留底检测复用）
  backend/file_fetcher.py                httpx 下载 URL/OSS 临时签名地址到临时文件
  backend/archive_detect_service.py      文件留底检测编排（批次、增量复用、总体报告）
  backend/template_service.py            Word 模板解析、锚点扫描、渲染
  backend/split_ocr_service.py           PDF 拆分专用全页 OCR（双线程、多 OCR 实例）
  backend/split_service.py               PDF 页范围规整与拆分
  backend/db/*.py                        ORM、engine、CRUD 模块

config.json                              DB + LLM + OCR/文档类型配置
output/                                  静态挂载为 /uploads/，保存 PNG/PDF/DOCX 等产物
temp/                                    上传/下载/模板解析临时文件
migrations/versions/                     Alembic 迁移（当前包含 archive-detect 009/010）
```

## 数据库重点

主要表：

- `clients`：客户主表，`client_code` 是业务方稳定客户编码。
- `documents` / `client_info`：材料解析记录和 KV 兜底字段。
- `templates` / `template_fills`：Word 模板和填充历史。
- `split_tasks`：PDF 拆分任务，持久化状态和 ranges；7 天后清理磁盘文件但保留 DB 记录。
- `summaries`：URL 文件摘要历史。
- `archive_detect_batches`：文件留底检测批次；包含 `overall_verdict/overall_score/overall_reason` 当次总体判断。
- `archive_detect_files`：单文件检测结果；含 `verdict/match_score/is_archival/confidence/reason/key_points/doc_category`、脱敏后的 `ocr_text`，业务模式下还有 `progress_id/file_id/version/deleted`。
- `archive_detect_progress`：业务审核的进展包实体，`(client_id, progress_oid)` 唯一；存办理人、项目、项目详情、进展名称等。
- `archive_detect_folder_summaries`：进展包维度滚动总报告（多版本，后续阶段使用）。

## 文件留底检测 / 业务审核

入口路由在 `/api/archive-detect/*`，Swagger 已用 `tags=["文件留底检测"]` 分组，并给请求/响应字段加中文说明。

模式：

1. **快速检测**
   - `POST /api/archive-detect/upload`
   - `POST /api/archive-detect/urls`
   - 前端 `ArchiveDetectEntryPage.vue` 的「快速检测」tab，保留自由判定提示词。

2. **业务审核**
   - `POST /api/archive-detect/business/batch`：JSON + OSS URL。
   - `POST /api/archive-detect/business/batch/upload`：multipart 上传，`client_payload/progress_payload/items_payload` 为 JSON 字符串。
   - `GET /api/archive-detect/business/batch/{batch_id}`：轮询完整结果，返回 client/progress/files/overall。
   - 前端 `ArchiveDetectEntryPage.vue` 的「业务审核」tab 默认打开；criteria 会根据客户姓名、项目名、项目详情、进展名、阶段自动预填，用户手改后不再覆盖。

关键逻辑：

- 同一进展包内 `(progress_id, file_id)` 命中历史 `done` 文件时严格复用旧结果，跳过 OCR/LLM；复用项 `elapsed_sec=0`，返回 `is_reused=true`。
- 新文件走 `text_extractor.extract_text` → `llm_service.detect_archival` → `redactor` 脱敏 → DB 终态写入。
- OCR 文本只以脱敏后的 `archive_detect_files.ocr_text` 入库；默认批次查询用 `defer` 不拉该大字段，单文件详情用 `get_file_detail`。
- 单文件 verdict 由 LLM 直接输出 `match/partial/mismatch`；`is_archival=(verdict=='match')`、`confidence=match_score` 用于向后兼容。
- 批次总报告：所有文件完成后，规则计算 `overall_verdict/overall_score`，再调用 `llm_service.summarize_batch` 生成 `overall_reason`；失败时用规则文本兜底。
- 公司售后留底分类体系硬编码在 `llm_service.py` 的 `ARCHIVE_CATEGORIES_FULL/SIMPLE`，业务模式会传 `stage=pre_submit|post_submit` 让 LLM 感知递交前/后分类。

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
- 不复用 `ocr_service.process_file`，而是用 `split_ocr_service.split_extract_all_pages` 全页 OCR、200dpi、2 线程、多 OCR 实例。
- DB `split_tasks` 是权威状态；内存 `_split_task_status` 只做轮询 fast-path。
- `/api/split/history` 必须声明在 `/api/split/{task_id}` 之前。

## 已知遗留/注意事项

- `archive_detect/` 独立子项目已迁出到 `E:\qoderproject\archive_detect\`；仓库内如残留空目录不要依赖。
- 快速检测 tab 的“文件不入库、处理完即删”文案与当前 DB 双写持久化已不完全一致；改文案时要谨慎区分快速检测和业务审核。
- `archive_detect_files.content_sha256` 列已建但当前不写值；增量复用依赖业务方传稳定 `file_id`。
- `archive_detect_folder_summaries` 表已建，进展包维度滚动总报告是后续阶段，不要误以为当前已写入。
- `pdf_ocr.py` 单文件 CLI 若存在，不属于 web 流程，改 web 流水线时不需要同步改它。
- 业务接口不加 API Key 鉴权，当前假定由网络层隔离。
