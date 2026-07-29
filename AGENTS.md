# AGENTS.md — 智能文档审核工作台

> 本文件面向 AI 编程助手。阅读者被假定为**完全不了解本项目**。文中信息均来自仓库实际文件，请勿凭假设修改。

## 1. 项目概述

**智能文档审核工作台**是一个面向移民/售后客户材料处理的 Web 应用。核心实体是"客户档案"，所有功能都围绕 `clients` 表展开。

当前主功能线：

1. **文件留底检测 / 业务审核**（主线）：接收业务方传入的客户+项目+进展+文件 URL，后台 OCR/文本抽取 + LLM 按公司留底分类体系判定，持久化单文件结果、OCR 脱敏文本、批次总体报告。
2. **AI 材料解析**：上传 PDF/图片 → OCR + LLM 提取结构化字段 → 人工复核 → 归档到客户档案。
3. **客户档案结构化生成**：从业务审核完成的 OCR 文件中批量抽取客户/家庭成员/资产事实，自动写入结构化表；**只补空字段，不覆盖已有非空人工数据**。
4. **客户画像（接口导入）**：画像页弹窗从业务方接口 `getAfterCustomerAllFiles` 拉客户文件清单（预览勾选，客户编号可空=最近 100 条）→ 全量 OCR 入客户文件库（fresh 存原文，原件落盘留存 30 天可在线查看）→ 关键词+LLM 分类（12 类：身份证/户口本/学位证/出生证明/护照/KYC表/结婚证/房产证/无犯罪/批复/递交包/签收回执）→ 按代码规则（`backend/extract_rules.py` 常量）提取 → 归因写独立 profile_* 领域表（不写 clients/family_members；**人员去重：简体/繁体/拼音同一人不重复建卡**——find_person_match 证件号归一化→姓名繁简折叠(OpenCC)→name_en 词序无关→拼音互转(pypinyin 连写两序)；entity=asset 写 profile_assets、entity=case 写 profile_cases 案件时间线（migration 022 起按项目多案件：一个售后项目=一个案件，按文件行 `affter_entryoid` 路由，NULL→默认案件，导入时先建全部项目案件壳；接口项目字段 projectno/projectname/明细项目同步落库）；**同家庭同内容文件去重**（`content_sha256`,migration 025：跨售后项目 file_code 不同的同一文件，命中兄弟行复用 OCR/分类、非 case 类跳过 LLM 提取）+ **任务内文件级并发 3**（LLM 等待与 OCR/下载重叠，归因写库段 asyncio 锁串行）；纯规则质量评级驱动复核闭环（待复核队列+人工修正永远覆盖）+ 完备度矩阵（人×材料）+ 文件归属页（`/file-assign`，手动指定文件归属人，写 `customer_files.person_id`，人员「查看文件」/矩阵按 列∪write_stats 并集）；画像弹窗标题栏「重新生成画像」→ `POST /api/profile/households/{id}/regenerate`（家庭名下跨任务重跑：有 OCR 复用、缺 OCR 重识别、缺文件按编码重下载，人工字段不覆盖）；**删除画像=只删画像数据**（household/persons/fields/assets/cases），任务/文件/OCR/提取结果/磁盘原件全保留，重新导入按 file_code re-link 复用 OCR 重建；**结婚证多人模式**（RULES_VERSION=3：抽全配偶字段、配偶无卡自动建卡、互写 spouse_name 字段）；**字段可信度徽标**（`credibility.compute_field_credibility` 纯函数：人工确认短路 100/基底 verified 70·declared 50/多文件互证 +15~20/跨类型 +5/冲突 -25，画像接口 `attach_field_credibility` 读时挂载，前端高/中/低徽标+来源抽屉）；**Office 嵌图 OCR**（docx/xlsx 纯文本过短时解 zip 内 media 图 OCR，扫描贴图文档可分类提取）+ `GET /api/profile/files/{id}/preview-pdf`（soffice 转 PDF 预览 Office 原件）。详见 docs/09 + docs/10。
5. **AI 填写文件（Word 模板）**：上传 docx 模板 → 扫描占位符/锚点 → 选择客户 → 从客户档案填值 → 输出 docx/PDF。
6. **处理超长 PDF**：上传多证件合并 PDF → 全页 OCR + LLM 判断证件边界 → 按类型拆为独立子 PDF。
7. **URL 文件摘要**：输入文件 URL + 进展名 → 下载/OCR/抽文本 → LLM 摘要和相关性判断。

仓库可见性：**Private**。`config.json` 含 API Key / DB 密码，已在 `.gitignore` 中，**切勿提交**。

## 2. 技术栈

| 层 | 技术 |
|---|---|
| 后端框架 | FastAPI + Pydantic |
| 数据库 ORM / 迁移 | SQLAlchemy 2 (asyncpg) + Alembic |
| 数据库 | PostgreSQL 14+，业务表用 JSONB 存灵活字段 |
| OCR | RapidOCR (`rapidocr-onnxruntime`)，CPU 推理，模型内置 |
| PDF 渲染/处理 | pypdfium2、pdfplumber、pypdf |
| LLM | OpenAI 兼容接口；模型 ID 完全由 `config.json` 驱动 |
| Word 处理 | python-docx、docxtpl、mammoth；Windows 用 `docx2pdf`，Linux 用 LibreOffice |
| 其他文件抽取 | openpyxl、python-pptx、xlrd、olefile（.doc 纯 Python 解析） |
| 前端 | Vue 3 + Element Plus + vue-router 4 + Vite |
| 运行环境 | Python 3.9–3.13（推荐 3.12） |

## 3. 项目结构

```text
20260527/
├── backend/                         # FastAPI 后端代码
│   ├── main.py                      # FastAPI 入口、路由聚合、startup/shutdown、内存态轮询缓存
│   ├── worker_runner.py             # 独立 OCR/LLM worker 进程入口
│   ├── archive_detect_service.py    # 文件留底检测编排（提交入队、增量复用、finalize 总报告）
│   ├── client_profile_service.py    # 客户档案结构化生成编排
│   ├── llm_service.py               # LLM 调用封装与各业务 prompt
│   ├── ocr_service.py               # RapidOCR 封装 + PDF/图片 OCR 统一入口
│   ├── text_extractor.py            # PDF/图片/docx/doc/xls/pptx 统一文本抽取(Office 嵌图 OCR + office_to_pdf)
│   ├── credibility.py               # 画像字段可信度打分(纯函数:来源层/确认状态/多文件互证/冲突)
│   ├── split_service.py             # PDF 按页拆分
│   ├── split_ocr_service.py         # 拆分专用全页 OCR
│   ├── template_service.py          # Word 模板解析、锚点扫描、渲染
│   ├── file_fetcher.py              # URL/OSS 下载 + URL 刷新 + 临时文件清理
│   ├── redactor.py                  # 身份证号/手机号/银行卡/金额等脱敏
│   ├── event_service.py             # 业务事件流写入 system_events
│   ├── field_dictionary.py          # 字段字典（clients/family/assets 字段定义）
│   ├── anchor.py                    # Word 模板 anchor 扫描辅助
│   ├── backfill_done_files.py       # 一次性数据回填脚本
│   ├── db/                          # ORM、engine、CRUD 模块
│   │   ├── models.py                # SQLAlchemy ORM 模型
│   │   ├── engine.py                # 异步/同步引擎 + session 工厂
│   │   ├── crud.py                  # clients/documents/client_info CRUD
│   │   ├── field_router.py          # OCR 字段名 → 表/列 路由表
│   │   └── *_crud.py                # 其他业务 CRUD
│   ├── middleware/
│   │   └── request_log_middleware.py # 纯 ASGI 请求记录中间件
│   └── requirements.txt             # Python 依赖清单
├── frontend/                        # Vue 3 前端
│   ├── package.json                 # npm 脚本与依赖
│   ├── vite.config.js               # Vite 配置（含 /api /uploads 代理）
│   ├── src/router.js                # vue-router hash 模式路由表
│   ├── src/api.js                   # axios 封装
│   └── src/components/*.vue         # 各业务页面组件
├── migrations/                      # Alembic 迁移
│   ├── env.py
│   └── versions/001_initial.py … 022_project_cases.py
├── docs/                            # 重构参考开发文档(01-系统概览 ~ 07-重构规划 + 客户数据库-PRD)
├── frontend2/DocReview.ArchiveDetect/ # .NET(net10.0) 业务后端重写 PoC(目录名误导,不是前端);
│                                      #   对标 /api/archive-detect/* 契约,EF Core 连同一 PG,RapidOcrNet 内置 OCR
├── tests/                           # 单元测试 + 冒烟脚本
├── deploy/                          # 部署脚本
│   ├── linux/                       # 生产部署（Alibaba Cloud Linux 3 / CentOS 8+）
│   └── windows/                     # 历史 PowerShell 打包脚本（已退役）
├── config.json.example              # 配置模板（复制为 config.json 并填值）
├── config.json                      # 真实配置（已在 .gitignore，勿提交）
├── alembic.ini                      # Alembic 配置（DSN 优先从 config.json / DATABASE_URL 读取）
├── output/                          # 静态挂载为 /uploads/，保存产物
├── temp/                            # 上传/下载/模板解析临时文件
├── start_backend.bat                # Windows 一键启动后端 + 1 个 worker
└── start_frontend.bat               # Windows 启动前端 dev server
```

## 4. 关键运行约束

### 4.1 后端与 Worker 是两个独立进程

- **uvicorn 主进程**只接 HTTP、写 DB、跑 finalize 轮询 + watchdog。
- **OCR/LLM 实际执行**在独立的 `worker_runner.py` 进程里。
- 本地开发要**分别启动**；改了 OCR/抽取/LLM 代码后，uvicorn 的 `--reload` **不会**重启 worker，必须手动重启 worker 才生效。
- 生产用 systemd 模板 `doc-review-worker@.service`；小内存机器（4C/8G）保持 **1 个 worker**，OCR 串行，稳定优先。
- `uvicorn --workers=1` **不能改**：主进程保留内存态 `_batch_status` 作为前端轮询 fast-path，多 uvicorn worker 会让该缓存分裂。

### 4.2 启动目录至关重要

后端必须从 `backend/` 目录启动，否则相对 import 会失败（**本机开发端口固定 8002**，前端配套 `VITE_API_TARGET=http://localhost:8002 npm run dev`，vite 代理默认 8000）：

```bash
cd e:/qoderproject/20260527/backend
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ../.venv312/Scripts/python.exe -m uvicorn main:app --host 0.0.0.0 --port 8002 --reload
```

### 4.3 Windows 控制台编码

中文 task_id / 文件名在 `print()` 时可能触发 GBK 编码错误，运行后端和测试时**务必**带：

```bash
PYTHONIOENCODING=utf-8 PYTHONUTF8=1
```

## 5. 本地开发与运行命令

### 5.1 环境初始化

```bash
# 推荐 Python 3.12
python -m venv .venv312

# Windows
.venv312\Scripts\activate
# Linux/Mac
source .venv312/bin/activate

pip install -r backend/requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### 5.2 配置

```bash
cp config.json.example config.json
```

编辑 `config.json`，填入：

- PostgreSQL 连接信息（`database` 段）
- LLM API Key + base_url + model（`llm` 段，OpenAI 兼容格式）
- `file_url_service` 段（业务审核 URL 刷新接口参数）

数据库 DSN 优先级：**环境变量 `DATABASE_URL` > `config.json.database`**。

### 5.3 数据库迁移

```bash
cd e:/qoderproject/20260527
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe -m alembic upgrade head
```

### 5.4 启动后端 + Worker

```bash
# 手动分别启动(本机固定 8002)
cd e:/qoderproject/20260527/backend
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ../.venv312/Scripts/python.exe -m uvicorn main:app --host 0.0.0.0 --port 8002 --reload

# 另一个终端
cd e:/qoderproject/20260527/backend
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ../.venv312/Scripts/python.exe -m worker_runner --worker-id worker-1

# 或 Windows 一键启动
start_backend.bat
```

### 5.5 启动前端

```bash
cd e:/qoderproject/20260527/frontend
npm install
npm run dev
```

开发环境打开 http://localhost:5173/，Vite 会把 `/api` 和 `/uploads` 代理到 `VITE_API_TARGET`（默认 `http://localhost:8000`，本机用 8002 需带环境变量启动，见 §4.2）。

### 5.6 前端生产构建

```bash
cd e:/qoderproject/20260527/frontend
npm run build
```

产物在 `frontend/dist/`，生产由 nginx 直接 serve。

## 6. 测试策略

测试为**简单 `assert` 脚本风格**，不依赖 pytest，直接 `python` 执行。

```bash
# 单元测试示例
cd e:/qoderproject/20260527
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_split_service.py
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_redactor.py
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_scan_anchors.py
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_worker_runner_claim.py

# 冒烟脚本（依赖运行中的后端）
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/smoke/test_archive_detect.py
```

测试文件通过把 `backend/` 插入 `sys.path` 来导入模块。新增测试时请保持这一风格。

## 7. 代码组织约定

### 7.1 后端

- `main.py` 是路由聚合入口，业务逻辑尽量放到对应 `*_service.py`。
- 所有数据库访问走 `backend/db/*_crud.py`，不要直接在 `main.py` 里写 SQL。
- OCR 调用统一走 `ocr_service.run_ocr()`，禁止直接拿全局 `_ocr_engine`。
- 文件下载统一走 `file_fetcher`，业务审核 URL 过期时由 `file_fetcher.refresh_download_url` 刷新。
- LLM 调用统一走 `llm_service._call_llm()` 及各业务 wrapper（`detect_archival`、`detect_and_extract` 等）。
- 敏感信息（身份证号、手机号、银行卡、金额）在入库前必须经过 `redactor` 脱敏。

### 7.2 数据库模型

主要表：

- `clients`：客户主表，`client_code` 是业务方稳定客户编码。
- `family_members` / `assets`：家庭成员、资产子表，按 `relation` / `asset_type` 区分。
- `client_info`：KV 兜底，存未纳入强 schema 的字段。
- `documents` / `templates` / `template_fills`：材料解析记录、Word 模板和填充历史。
- `split_tasks`：PDF 拆分任务，持久化状态和 ranges；7 天后清理磁盘文件但保留 DB 记录。
- `summaries`：URL 文件摘要历史。
- `archive_detect_batches` / `archive_detect_files` / `archive_detect_progress`：文件留底检测核心表。
- `client_profile_generation_tasks`：客户档案结构化生成任务。
- `system_events` / `api_request_logs` / `external_api_logs` / `ai_api_calls`：可观测性四张表，保留 30 天。

字段路由：`backend/db/field_router.py` 把 OCR 提取的字段名映射到 `clients`/`family_members`/`assets` 的具体列；未命中进 `client_info`。

### 7.3 前端

- 路由在 `frontend/src/router.js`，hash 模式，部署时不需要 nginx fallback。
- API 调用在 `frontend/src/api.js` 统一封装。
- 业务页面在 `frontend/src/components/*.vue`。

## 8. 文件留底检测 / 业务审核要点

- 入口路由在 `/api/archive-detect/*`。
- **业务审核**是唯一入口（快速检测/匿名模式已移除）：
  - `POST /api/archive-detect/business/batch` 只校验 + 写 DB（`pending`）+ 秒回 `batch_id`，不下载、不 OCR。
  - 真正的下载/OCR/LLM 由 worker 串行处理。
  - 客户姓名与办理人由后端从 DB（`clients.name` / `archive_detect_progress.handler`）显式注入检测/总判/总结 prompt，不再依赖前端 criteria 字符串。
- 同一进展包内 `(progress_id, file_id)` 命中历史 `done` 文件时严格复用旧结果。
- 单文件 verdict：`match/partial/mismatch/no_text`；`no_text` 不算失败，不参与总体判定。
- 批次总报告优先由 LLM 综合判定（理解"关键件 vs 附带件"），LLM 失败时回退规则平均分。
- 重审/重跑统一走 worker DB 队列；老的主进程 fan-out 函数已删除，不要再引用。
- 队列监控：`GET /api/archive-detect/admin/queue-stats`。
- 健康检查：`GET /api/healthz` 真查 DB 和 worker。

## 9. 部署

### 9.1 生产环境

- **永久部署平台：Alibaba Cloud Linux 3 / CentOS 8+ ECS**；Windows 仅作开发环境。
- 完整方案在 `deploy/linux/`，首次部署看 `deploy/linux/README.md`。
- 数据库密码/密钥放在 `deploy/linux/app.env`（`chmod 600`），通过 systemd `EnvironmentFile` 注入。

### 9.2 日常更新流程

```bash
# 本地：构建前端 + rsync 上传整个项目
cd e:/qoderproject/20260527
bash deploy/linux/05-upload.sh root@<服务器IP>

# 服务器：依赖/迁移变了才需要
sudo -u docreview bash /opt/doc-review/deploy/linux/02-install-app.sh

# 服务器：重启
sudo systemctl restart doc-review              # 后端代码改动
sudo systemctl restart doc-review-worker@1     # OCR/抽取/LLM/worker 代码改动（必须单独重启）
sudo systemctl reload nginx                    # 前端 dist 变化
```

### 9.3 关键生产约束

- `doc-review.service` 用 `Wants=doc-review-worker@1.service` 启动主服务时一并拉起 1 个 worker。
- `systemctl restart doc-review` **不会**重启已在跑的 worker；改 OCR 代码要单独 `restart doc-review-worker@1`。
- 4C/8G 小机器保持 1 个 worker，OCR 串行。
- `docx2pdf` 在 Linux 上不可用；模板转 PDF 走 LibreOffice `soffice --headless`。
- `.doc` 抽取优先纯 Python `olefile`，失败再回退 `soffice` / `antiword`。

## 10. 安全与敏感信息

- `config.json` 含 API Key、DB 密码，**绝对不能提交到 git**（已在 `.gitignore`）。
- 统一 Bearer 鉴权：`backend/middleware/auth_middleware.py`（纯 ASGI），凭证为 `config.json.auth.biz_api_key`；员工前端走 `POST /api/auth/login`（校验 `admin_user`/`admin_password`）拿 token。白名单：登录接口、`GET /api/healthz`、文档路径、**业务方集成前缀 `/api/archive-detect/business/batch`**（提交+轮询不带 token）；其余 `/api/*` 在配置 biz_api_key 后都要 Bearer，未配置则本地开发放行。
- OCR 原文不持久化；入库的是脱敏后的 `archive_detect_files.ocr_text`。
- `ai_api_calls` 中 LLM 的 prompt/response 原文直存未脱敏（业务决策），专门用于 AI 调用审计。
- 生产环境 `app.env` 应 `chmod 600 + chown docreview:docreview`。

## 11. 常见陷阱

- **业务审核文件卡在 `pending` 不动 = worker 没起**。worker 是独立进程，本地/服务器都要单独启动。
- **后端启动报 import 错误**：检查是否从 `backend/` 目录启动。
- **中文 task_id 在 print 时崩溃**：启动命令缺少 `PYTHONIOENCODING=utf-8 PYTHONUTF8=1`。
- **docx2pdf 转 PDF 失败**：Windows 需安装 Word；无 Word 时自动降级返回 docx（响应头 `X-Fallback-Docx: 1`）。
- **LibreOffice 未安装时 Word 原貌预览空白**：前端会自动降级到 mammoth HTML 视图，这是预期行为。
- **OCR 引擎首次加载慢**：RapidOCR 模型内置，但初始化仍需几秒到几十秒，属正常。
- **Windows 临时文件删不掉**：`file_fetcher.cleanup_temp_file` 会延迟重试并加入队列，日志里看到「已加入延迟清理队列」是正常的，不需要告警。
- **重审/重跑不要再引用旧主进程函数**：`_orchestrate_recheck`、`_process_one_recheck`、`_finalize_overall_for_batch`、`_orchestrate_rerun` 等已删除。

## 12. 参考资料

- `README.md`：面向人类用户的快速入门与四大功能说明。
- `CLAUDE.md`：更详细的 Claude Code 专用说明，含各流水线设计决策与遗留注意事项。
- `docs/`：系统重构参考文档（01-系统概览 ~ 07-重构规划）；重构决策：OCR 留 Python 收敛为独立微服务（ocrapi，:8001，JWT 鉴权，源码暂不在本仓库，仅 tests/test_ocrapi_auth.py + tests/smoke/test_ocrapi_ocr.py），业务系统用 .NET 重写（frontend2/DocReview.ArchiveDetect PoC，`dotnet run` 起在 :5001），PostgreSQL 保留，前端不动。
- `deploy/linux/README.md`：生产部署完整手册。
