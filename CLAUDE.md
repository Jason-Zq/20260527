# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目定位

智能文档审核工作台，三条核心业务线：

1. **证件解析**：上传 PDF/图片 → OCR + LLM 提取结构化字段 → 人工复核 → 归档为客户档案
2. **Word 模板填写**：上传 docx 模板 → 扫描占位符 → 选客户后 LLM 语义匹配 → 渲染填值后的 docx/PDF
3. **PDF 拆分**：上传多证件合并 PDF → LLM 判断页码范围 → 按证件类型拆分为独立 PDF

## 运行命令

```bash
# 后端（必须从 backend/ 目录启动，否则 import 失败）
cd e:/qoderproject/20260527/backend
../.venv312/Scripts/python.exe -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# 前端
cd e:/qoderproject/20260527/frontend && npm run dev
```

数据库初始化：`alembic upgrade head`（配置在 `alembic.ini`，DSN 从 `config.json` 读）。

## 测试

```bash
# 运行单个测试文件（不依赖 pytest，直接 python 执行）
cd e:/qoderproject/20260527
PYTHONIOENCODING=utf-8 ./.venv312/Scripts/python.exe tests/test_split_service.py

# 测试模板扫描（不调 LLM）
PYTHONIOENCODING=utf-8 ./.venv312/Scripts/python.exe -c "
import sys; sys.path.insert(0, 'backend')
import template_service
print(template_service.scan_template_candidates('output/test_template_complex.docx'))"
```

测试文件位于 `tests/`：根目录下 `test_*.py` 是单元测试（不依赖外部服务），`tests/smoke/` 是冒烟脚本（依赖运行中的后端），`tests/fixtures/` 存放测试用 PDF。简单 assert 模式，无需 pytest。

## 配置

`config.json` 是单文件配置，包含：

```json
{
  "database": {"host", "port", "user", "password", "dbname"},
  "llm": {"api_key", "base_url", "model", "temperature"},
  "max_ocr_pages": 5,
  "document_types": ["身份证", "护照", ...]
}
```

- LLM 通过 OpenAI 兼容接口调用，模型 ID 完全由 `config.json` 驱动（当前部署：火山引擎 ark endpoint）
- 每次 parse/generate 都会调 LLM，**单次 30~200s**，前端必须给 loading 态
- 实际提示词在 `llm_service.py` 硬编码，`document_types` 同时用于分类 prompt 注入和 `normalize_doc_type` 子串归一（参见 `_normalize_doc_type` 与 `classify_one_page`）

## 架构总览

```
前端 (Vue 3 + Element Plus + Vite)     后端 (FastAPI + SQLAlchemy)
src/App.vue (viewMode 状态机)          backend/main.py (入口 + 路由)
src/api.js (axios 封装)                backend/llm_service.py (LLM 调用)
src/components/*.vue                   backend/ocr_service.py (PaddleOCR)
                                       backend/template_service.py (模板处理)
                                       backend/split_service.py (PDF 拆分)
                                       backend/split_ocr_service.py (拆分+OCR)
                                       backend/db/{engine,models,crud,template_crud,split_crud}.py
config.json ←── 单文件配置
output/     ←── 静态挂载 /uploads/，docx/PDF/PNG 写这里
```

**前端路由**：`App.vue` 是手写的 `viewMode` 状态机（`home | parse | template | split`），不是 vue-router。子页用 `@back` 事件回到 home。

**数据库表**：
- `clients` — 客户主表
- `documents` — 文档/解析记录（JSONB 存 extracted_fields）
- `client_info` — 归纳后的客户字段（供查询）
- `templates` — Word 模板（JSONB 存 placeholders）
- `template_fills` — 模板填充历史
- `split_tasks` — PDF 拆分任务（JSONB 存 ranges，含 `files_cleaned` 标志位）

## 三条核心流水线

### 流水线 A：证件解析（异步 + 轮询）
1. `POST /api/upload` → 写入 `temp/` + DB 记录，返回 `task_id`
2. `asyncio.create_task` 后台执行：先 pdfplumber 试提取文字型 PDF；若文字稀疏则降级 pypdfium2 渲染 300dpi + PaddleOCR
3. LLM 合并调用 `detect_and_extract`（类型检测 + 字段提取一次出），支持多证件 `items`
4. 状态写内存 `_task_status`，前端每秒轮询
5. 完成后写 `output/{task_id}/`：原图、OCR 文本、解析 JSON
6. `PUT /api/result/{id}` 人工复核后归档到 `clients + client_info`

### 流水线 B：Word 模板填写
1. `POST /api/templates/parse`：mammoth 转 HTML + LLM 建议占位符 + 扫描空 cell/下划线
2. 前端在 candidates 列表里选择位置（id 由前端分配为 `str1/str2/...`）
3. `POST /api/templates`：保存时对 `type=='table_cell'` 注入 `<<strN>>` marker
4. `POST /api/templates/{id}/generate`：字符串替换 + docx2pdf 转 PDF（仅 Windows + Word）

### 流水线 C：PDF 拆分（持久化 + 7 天 TTL）
1. `POST /api/split` → 上传多证件 PDF，原 PDF 落盘到 `output/{task_id}/_original.pdf`，DB 写 `split_tasks` 记录
2. 后台 `_process_split_background`：全页 OCR（200dpi 双线程）→ `detect_page_ranges` 逐页 4 并发分类 + "未知夹心"修正 → `normalize_ranges` 规整 → 切分 PDF
3. 每个流水线阶段同步更新 DB `status`，进程重启后 `GET /api/split/{task_id}` 仍能从 DB 恢复
4. `GET /api/split/history` 列出所有任务（前端"记录"抽屉），`DELETE /api/split/history/{task_id}` 彻底删除
5. `_split_cleanup_loop` 每 24h 跑一次：删除 >7 天的 `output/{task_id}/` 整目录，DB 记录保留并置 `files_cleaned=true`

## 关键设计决策

- **占位符 `original_text` 在 HTML 里被 mammoth 转义**为 `&lt;&lt;str1&gt;&gt;`，前端预览替换时要先 `escapeHtml(original_text)`
- **docx 预览走 soffice → PDF → pypdfium2 PNG 管线**（`render_docx_pages`，dpi=150）。LibreOffice 未装时 `pages=[]`，前端自动降级到 HTML 视图，这是预期路径
- **`docx2pdf` 强依赖 Windows + Word**。`asyncio.to_thread` 内必须 `pythoncom.CoInitialize()`
- **静态文件**：所有 PNG/PDF 写 `output/` 下，前端通过 `/uploads/相对路径` 访问
- **临时文件**：`temp/templates/` 存 parse 阶段 docx，`output/templates/{id}/template.docx` 存最终模板，`output/templates/{id}/fills/{YYMMDDHHMMSS}.docx` 存每次生成结果
- **启动时清理**：`_cleanup_stale_template_temp(60min)` + `_cleanup_expired_output(30天)` 各扫一次；拆分流水线另起 `_split_cleanup_loop` 每 24h 跑一次（7 天 TTL）
- **路由顺序坑**：FastAPI 按声明顺序匹配，`/api/split/history` 必须在 `/api/split/{task_id}` 之前注册，否则会被路径参数吞掉
- **task_id 含中文 + Windows 控制台**：后端 `print()` 会因 GBK 编码爆 UnicodeEncodeError；启动时务必用 `PYTHONIOENCODING=utf-8 PYTHONUTF8=1`

## 已知遗留（不要重新实现）

- **下划线占位符 (`type=='underscore'`)**：后端能扫出和注入 marker，但前端"添加"按钮主要面向 `table_cell`
- **`_parse/{temp_token}/` 目录**：保存时清理，但用户关掉 dialog 不保存的会泄露（启动时有清理函数）
- **`pdf_ocr.py` 单文件 CLI** 与 web 流水线并行存在，不在 web 流程里，改 web 时不需要同步改它
