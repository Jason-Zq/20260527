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
4. **客户画像（接口导入）**：从业务方接口 `getAfterCustomerAllFiles` 拉客户文件清单（预览勾选）→ 全量 OCR 入客户文件库（fresh 存原文）→ 关键词+LLM 分类 12 类证件 → 按代码规则（`backend/extract_rules.py` 常量）提取 → 归因写独立 profile_* 画像域（简体/繁体/间隔号/拼音去重不重复建人 + 同名自动合并）、按售后项目多案件。方案见 [docs/09-客户画像-Excel导入方案.md](docs/09-客户画像-Excel导入方案.md)。
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
# 后端：必须从 backend/ 目录启动，否则相对 import 会失败；本地固定 8002 端口
cd e:/qoderproject/20260527/backend
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ../.venv312/Scripts/python.exe -m uvicorn main:app --host 0.0.0.0 --port 8002 --reload

# OCR worker：独立进程,必须单独起,否则文件留底检测只入队不识别(一直 pending)
cd e:/qoderproject/20260527/backend
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ../.venv312/Scripts/python.exe -m worker_runner --worker-id worker-1

# Windows 本地一键起后端 + 1 个 worker(各自独立窗口)
start_backend.bat

# Windows 本地一键起前端 dev server
start_frontend.bat

# 前端（本地后端是 8002,必须用 VITE_API_TARGET 指向,否则代理默认打 8000）
cd e:/qoderproject/20260527/frontend
VITE_API_TARGET=http://localhost:8002 npm run dev

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

> **CI/CD(2026-08-04 上线，测试服先行)**：测试服 8.138.111.12 上跑 Jenkins(绑 127.0.0.1:8080,SSH 隧道访问,admin 密码在服务器 `/root/.jenkins-admin-credentials`)。**push main → Poll SCM 2 分钟内自动：测试(27 项白名单,`tests/run_ci.sh`)→ 前端构建 → 打制品 → `deploy/ci/release.sh` 部署测试服**(快照/pg_dump/迁移校验/健康检查/失败回滚)。部署唯一入口是入库的 `deploy/ci/release.sh`,**服务器上老的 `/opt/fastapi/restart.sh` 已废弃勿用**。job 名 `doc-review-ci` 与 sudoers 路径绑定勿改名。IOD 生产发布走 `Jenkinsfile.release`(tag + 人工批准,待配 `iod-ssh-key` 凭据)。方案与实施记录见 [docs/12-CI-CD发布流水线计划.md](docs/12-CI-CD发布流水线计划.md)。

> **当前实际服务器（2026-07-24 实录，与下文标准方案有出入，以此为准）**：`root@8.138.111.12`，SSH 用密钥 `C:\Users\zq\Downloads\doc.pem`（本机无 rsync，用 `tar czf - ... | ssh "tar xzf -"` 上传）。代码 `/opt/fastapi/`（backend + backend/venv，**后端 nohup 直跑 :8765 + worker nohup 直跑，无 systemd**）；前端 dist 在 **`/opt/vue3/dist`**（nginx default.conf,80 端口反代 `/api` → 8765）；DB 在本机 localhost:5432/doc_review。`config.json` 在 `/opt/fastapi/`（服务器自有配置，**上传时切勿覆盖**；auth 段 2026-07-24 已补齐，与本地一致）。迁移踩过的坑：服务器 alembic_version 曾停在 014 但 015/017 对象已手工建过——用 `alembic stamp` 对齐后再 upgrade（016 external_api_logs 是真缺的，正常执行）。重启注意：**远程命令行里 `pkill -f 'uvicorn main:app'` 会匹配到 bash -c 自身导致自杀**，用 `pkill -f '[u]vicorn main:app'` 或按 PID 杀。备份在 `/opt/backups/`。2026-07-28 补充：重启统一用 **`/opt/fastapi/restart.sh`**（pkill+拉起 uvicorn:8765/worker-1，日志接 `/opt/fastapi/logs/`，用 `setsid bash restart.sh </dev/null >/dev/null 2>&1 &` 脱离会话执行——ssh 远程命令中途掉线(exit 255)曾导致只杀掉没拉起）；前端 dist 用 `dist.new` + `mv` 交换（旧目录留 `dist.old.*` 回滚）；**在服务器上 `curl localhost` 验证前端会命中 nginx 默认欢迎页**（站点 server block `server_name 8.138.111.12` 只 listen IPv4 80，localhost 解析到 IPv6 落默认块），正确姿势 `curl -H 'Host: 8.138.111.12' http://127.0.0.1/`。**2026-08-03 补充（用户口中「测试服务器」= 这台）**：已发布当前工作区全量改动（RapidOCR 3.9.2 替换 rapidocr-onnxruntime 1.4.4、image_preprocess 前处理、field_validators、RULES_VERSION=4 英文建人/sponsor/到期提醒、asyncpg 段错误修复），迁移 022→023（回填零重复组）→024→025 head。注意 `/opt/fastapi/venv` 是游离旧 venv，live 的是 `backend/venv`（restart.sh 从 backend/ 起 `venv/bin/python`）；worker stdout 写日志文件是全缓冲，重启后 log 里暂时看不到新 worker 启动行属正常（进程活着=引擎初始化已过）。

> **IOD 服务器（2026-07-28 部署实录，第二台生产机）**：`root@120.26.67.160`，SSH 已配本机免密公钥 **`~/.ssh/iod_deploy`**（服务器原来只开密码登录，用 SSH_ASKPASS 技巧装的 key；两个老 pem 都不匹配）。结构与 8.138.111.12 几乎一致但有出入：代码 `/opt/fastapi/`（backend + backend/venv(py3.11) + **migrations + alembic.ini 也已上传放这里**，`config.json` 同位置、切勿覆盖）；**uvicorn :8765 + 2 个 worker（worker-1/2）nohup 直跑，无 systemd、无 restart.sh**，日志在 backend/ 下 app.log/worker-N.log，重启用 `setsid nohup ./venv/bin/uvicorn main:app --host 0.0.0.0 --port 8765 >> app.log 2>&1 </dev/null &`（worker 同理，`pkill -f '[w]orker_runner'` 括号技巧防自杀；worker 收 SIGTERM 是优雅退出，部署等不住就 kill -9）。**前端在 `/opt/front/dist`**，宝塔 nginx 站点配置 `/www/server/panel/vhost/nginx/120.26.67.160.conf`（/api → 8765）；本机 curl 验证同样要带 `-H 'Host: 120.26.67.160'`。DB 本机 localhost:5432/doc_review；**alembic 坑与另一台相同**（停 014、015/017 手工建过、016 真缺），已按 stamp 015 → upgrade 016 → stamp 4617b534a2d2(=017，注意该文件 revision id 是 hash 不是 "017") → upgrade head 对齐到 **022 head，以后直接 `cd /opt/fastapi && backend/venv/bin/python -m alembic upgrade head`**。2026-07-28 第二次发布：023/024 同名折叠键迁移一次过（144 人回填、零重复组，未跑 merge-duplicates-all 直接升 024）。第三次发布：025 customer_files.content_sha256（**教训：025 是本地工作区里未提交的新迁移，部署时只看了 git status 里已跟踪文件差点漏掉，任务全报 UndefinedColumn 后才补上；以后部署前 `ls migrations/versions/ | tail` 与服务器 `alembic current` 对齐**）。机器 14G 内存，曾残留两代 4 个 worker 进程（部署时已清理，重启前先 `ps aux | grep [w]orker_runner` 确认无旧代残留）。

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
- **OCR 引擎是 RapidOCR 3.x(`rapidocr` 包,PP-OCRv6 small,onnxruntime CPU)**,不是 PaddleOCR,也不再是旧的 `rapidocr-onnxruntime`(PP-OCRv4)。所有 OCR 调用收口在 `ocr_service.run_ocr()`,内部把 v3 输出(boxes/txts/scores)适配回旧 PaddleOCR 结构 `[[[bbox,(text,conf)],...]]`,下游零改动。模型权重随 wheel 内置,无需联网下载、无 libGL 依赖。**图像前处理(纠偏/小图放大/低对比度 CLAHE)在 `image_preprocess.py`,全自适应(好图近零成本),在引擎锁外执行**;`config.json` 键 `ocr_preprocess`(默认 true)开关、`ocr_max_side_len`(默认 2560,检测输入长边上限,调大更准更慢)。**改了 OCR/前处理代码,后端和 worker 都要重启**(两条进程都内嵌引擎)。
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
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_profile_api_manifest.py   # 接口清单适配(扁平/嵌套形态,纯函数)
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_extract_rules.py       # 提取规则常量(纯函数,不依赖 DB)
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_doc_extract_mapping.py     # 归因+只补空写库(依赖 DB)
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_review_scoring.py          # 质量评级打分(纯函数)
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_profile_crud.py             # 画像 v2 归因/字段写入语义(依赖 DB)
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_extract_multi.py            # 多人模式提取(parse_persons_payload/prompt/重试,纯函数+mock)
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_profile_task_delete.py      # 画像任务删除级联(依赖 DB,测后清理)
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_person_dedup.py             # 人员去重(繁简/拼音/证件号归一,纯函数+DB)
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_profile_cases_project.py    # 项目案件路由/默认案件/部分唯一约束(依赖 DB,测后清理)
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_profile_content_dedup.py   # 同家庭内容级去重/重复文件跳过提取/任务内并发冒烟(依赖 DB,测后清理)
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_file_assign.py            # 文件归属(person_id 列/并集查询/归属页列表,依赖 DB,测后清理)
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_text_extractor_docx_ocr.py # Office 嵌图 OCR(docx/xlsx 阈值触发/source 标记/上限截断,mock OCR)
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_credibility.py            # 字段可信度打分(纯函数 + attach 全链路 DB,测后清理)
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_person_merge.py           # 同名人员合并(字段仲裁/重挂/回写/撞名守卫,依赖 DB,测后清理)
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_image_preprocess.py        # OCR 图像前处理(纠偏/放大/对比度,合成图纯函数)
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_field_validators.py       # 字段校验(身份证校验位修复/派生/日期合理性,纯函数)
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_name_en_expiry.py         # name_en建人/sponsor关系推导/证件到期提醒(纯函数+DB,测后清理)

# 冒烟脚本（依赖运行中的服务）
cd e:/qoderproject/20260527
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/smoke/test_archive_detect_concurrent.py   # 业务审核并发(依赖后端+worker)
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/smoke/test_three_worker_throughput.py     # 3 worker 吞吐
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/smoke/test_ocrapi_ocr.py                  # ocrapi 冒烟(默认 http://localhost:8001)
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/smoke/e2e_v2_smoke.py                     # 模板 v2 流程(注意 BASE 硬编码 127.0.0.1:8765,按需改)
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
- OCR 相关键：`ocr_preprocess`(图像前处理开关,默认 true)、`ocr_max_side_len`(检测输入长边上限,默认 2560,调大更准更慢)、`profile_ocr_render_dpi`(画像管线 PDF 扫描页渲染 DPI,默认 300;业务审核 worker 保持 200)。
- `document_types` 仍用于证件解析和 PDF 拆分页分类；文件留底检测已改用 `llm_service.py` 中硬编码的公司售后留底分类体系。
- `auth` 段（`admin_user`/`admin_password`/`biz_api_key`）驱动统一 Bearer 鉴权，见下节；`config.json.example` 未含该段，不配 = 本地开发放行。
- Windows 控制台运行后端时务必带 `PYTHONIOENCODING=utf-8 PYTHONUTF8=1`，否则中文 task_id / 文件名的 `print()` 可能触发 GBK 编码错误。

## 鉴权

`backend/middleware/auth_middleware.py`（纯 ASGI，与 request_log 同模式，不能用 `BaseHTTPMiddleware`）。凭证只有一套：`config.json.auth.biz_api_key`。

- 员工前端：`POST /api/auth/login` 校验 `admin_user`/`admin_password`，通过返回 biz_api_key 当会话 token；前端存 localStorage，axios 自动带 `Authorization: Bearer`，401 清 token 跳 `/login`。业务方直接拿 biz_api_key 走 Bearer。
- 白名单（免鉴权）：`POST /api/auth/login`、`GET /api/healthz`、文档（`/docs /redoc /openapi* /swagger*`）、**业务方集成接口前缀 `/api/archive-detect/business/batch`**（提交+轮询，业务方不带 token，与历史行为一致）。除此之外的所有 `/api/*` 都要 Bearer。
- 未配置 `biz_api_key` 时全部放行（本地开发）；`main.py` startup 调 `reset_auth_cache()` 让配置改动即时生效。
- 中间件顺序：Auth 比 RequestLog 后注册 = 先执行（Starlette 后注册先执行），被 401 挡掉的请求不进外部请求日志。

## 架构总览

```text
前端: Vue 3 + Element Plus + Vite + vue-router(hash)
  frontend/src/router.js                 路由: /login, /, /clients, /parse, /template, /split, /summary, /archive-detect, /archive-admin, /archive-daily-report, /file-info, /profile, /file-assign, /events, /request-logs, /external-api-logs, /ai-api-calls, /child-age-leads;全局守卫:无 token 一律跳 /login
  frontend/src/api.js                    axios API 封装(token 存 localStorage,请求拦截器自动带 Bearer,401 清 token 跳登录)
  frontend/src/menu.js                   侧边栏菜单配置(与路由分离;App.vue 渲染左侧分组 el-sub-menu,点击展开/收缩,可折叠;新增页面=router.js 加路由+这里加项)
  frontend/src/tabs.js + components/TagsView.vue   多页签(tags-view):keep-alive 保活已访问页面,右键菜单 + 右侧固定下拉(同一 teleported 浮层,刷新/关闭其他/关闭全部);**新增页面还需:路由 meta 加 title/cache + 页面组件 defineOptions name**(异步组件拿不到 name,keep-alive include 走 meta.cache)
  frontend/src/components/*.vue          各业务页面(含 ArchiveAdminPage / EventsPage / RequestLogsPage / ExternalApiLogsPage / ChildAgeLeadsPage)

后端: FastAPI + SQLAlchemy 2 async + Alembic
  backend/main.py                        FastAPI 入口 + 路由聚合 + startup/shutdown + 内存态轮询缓存
  backend/worker_runner.py               独立进程入口: SKIP LOCKED 抢 DB 任务 → OCR → LLM → 写终态(业务审核的实际执行者)
  backend/archive_detect_service.py      文件留底检测编排(提交入队、增量复用、watchdog 回收死 worker 任务、finalize 总报告)
  backend/llm_service.py                 LLM 调用封装与各业务 prompt(OpenAI 兼容,模型由 config.json 驱动)
  backend/ocr_service.py                 RapidOCR 3.x(PP-OCRv6) 引擎封装 + PDF/图片 OCR(run_ocr 统一入口)
  backend/image_preprocess.py            OCR 图像前处理(纯函数:纠偏/小图放大/低对比度 CLAHE,全自适应)
  backend/field_validators.py            提取字段校验(纯函数:身份证校验位修复/字段派生/日期合理性)
  backend/text_extractor.py              PDF/图片/docx/doc/xls/pptx 统一文本抽取(文件留底检测复用;PDF 逐页混合:含大图页 OCR、数字页读文本层;**docx/xlsx 纯文本<80 字时触发嵌图 OCR**——证件扫描件贴进 Word/Excel 的场景,`_ocr_zip_media` 解 zip 内 word/media/、xl/media/ 光栅图,≥150px 短边、≤10 张,阈值/上限常量 OFFICE_IMG_OCR_*;.doc 短文本时优先走 soffice→docx 复用嵌图 OCR;.xls 嵌图不做;另提供 `office_to_pdf` 给 preview-pdf 端点)
  backend/file_fetcher.py                httpx 下载 URL/OSS 临时签名地址到临时文件 + 延迟清理
  backend/event_service.py + db/event_crud.py        业务事件流(批次/OCR/worker 崩溃等)写 system_events
  backend/middleware/auth_middleware.py          纯 ASGI Bearer 鉴权(config.json.auth 驱动,白名单见「鉴权」节)
  backend/middleware/request_log_middleware.py       纯 ASGI 中间件,只记 POST /business/batch 请求体到 api_request_logs
  backend/client_profile_service.py      客户档案结构化生成编排(后台任务,只补空不覆盖)
  backend/profile_import_service.py      客户画像编排(接口清单适配 parse_api_manifest/远程拉取/run_import:取 OCR→分类→提取)
  backend/doc_type_matcher.py            证件类型关键词分类器(纯函数,12 类证件正负关键词评分)
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
migrations/versions/                     Alembic 迁移(当前到 025_customer_files_sha256;023/024 同名折叠键,生产须 023→合并存量→024 分步执行)
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
- `api_request_logs`：API 外部请求日志,中间件只记 `POST /api/archive-detect/business/batch` 的请求体,前端 `/request-logs` 页查看,GC 保留 30 天。
- `external_api_logs`：出站调用外部接口(`service=refresh_url`),记地址/请求参数/返回全文/耗时/成败,前端 `/external-api-logs` 页,GC 保留 30 天。URL 刷新在 `file_fetcher.refresh_download_url` 埋点(async create_task)。
- `ai_api_calls`：AI/LLM API 调用记录,记 operation/model/prompt/response/耗时/成败/业务上下文(batch_id/file_id/client_code/task_id),前端 `/ai-api-calls` 页,GC 保留 30 天。LLM 在 `llm_service._call_llm` 埋点--**注意用同步 `insert_ai_api_call_sync`**,因为它跑在 worker 线程(asyncio.to_thread)里,async 引擎连接池绑主 loop 不能跨线程用。**LLM 的 prompt/response 原文直存未脱敏**(业务决策,含脱敏前 OCR)。**入库前在 CRUD 层统一清洗**:去 NUL/控制字符(PG text 列不接受 ``,OCR 文本易混入,此前被 `except` 静默吞掉丢日志)、prompt/response 截断 50KB、error_msg 截断 2KB;列表接口只回 500 字预览,详情走单独的 `GET /api/admin/ai-api-calls/{row_id}` 拉全文。引擎层(asyncpg/psycopg2)已显式 `client_encoding=utf8`。
- `profile_import_tasks`：客户画像导入任务；一次接口导入一户一行,记录进度与各类计数(4 类证件各筛出数/提取数/失败数/current_file/needs_review_count/household_id)。
- `customer_files`：客户文件库,`file_code` 全局唯一(重复导入只 re-link 不重复下载/OCR);存全量 OCR——**fresh=未脱敏原文**(与 ai_api_calls 存原文的既定决策一致)、reused=archive_detect 脱敏文本,`ocr_source` 区分;分类结果 `doc_type`(12 类:id_card/hukou/degree_cert/birth_cert/passport/kyc_form/marriage_cert/property_cert/no_crime/approval/submission/receipt + other) + `classify_by(keyword/llm/none)`;`local_path` 原件落盘 output/customer_files/(30 天 GC,DB/OCR 永留);`review_status/review_reason/quality_score` 复核与质量评级;`person_id` 手动归属人(migration 021,文件↔人关联权威载体,与 write_stats 归因并集使用);`affter_entryoid` 售后项目OID + `project_name` 项目显示名(migration 022,项目案件路由键/反范式展示列,来自接口嵌套 list[] 项目外壳);`content_sha256` 原件内容 hash(migration 025,同家庭跨项目同内容文件去重键,下载后计算,命中兄弟行复用 OCR/分类)。`profile_households` 另有 `customer_code/crm_oid`(接口属性,只补空)。
- ~~`doc_extract_rules`~~：**已废弃并 drop(migration 020)**。证件字段提取规则改为代码常量 `backend/extract_rules.py`(仅被 profile_import_service 引用,画像提取跑在主进程——改规则=改该文件+**重启后端 uvicorn**,与 worker 无关);`fields` 结构不变:带 `target:{entity:'person',column}`(column 解读为 profile 字段名),column=null 只抽不写,entity=asset/case 走资产/案件表。原 draft→activate→disable 生命周期已移除。
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

### 检测批次管理后台

- 路由 `/api/archive-detect/admin/*`，前端 `ArchiveAdminPage.vue`（`/archive-admin`）。
- 筛选:状态/来源/批次ID/客户/进展/日期范围 + **总体判断多选(match/partial/mismatch)** + **仅看有失败文件**(EXISTS 子查询)。
- 详情弹窗展示批次全量信息(批次/客户/进展/办理人/项目/判定标准/总体/识别完成时间等),文件表含「文件编码(file_id)」列;数据源 `pollBusinessBatch`(business)或 `pollArchiveDetect`(历史 quick 批次)兜底。**弹窗已抽为共享组件 `BatchDetailDialog.vue`**(2026-08-03,审核任务管理与每日报告页共用)。
- **每日留底检测报告**(2026-08-03):`GET /api/archive-detect/admin/daily-report?date=` → `archive_detect_crud.admin_daily_report`——按批次 created_at 取当日全部批次按客户分组统计:done 按 overall_verdict 分桶 match/partial/mismatch/other、status=error 记 error、其余记 in_progress,avg_score 仅统计 done 且有分;无客户批次(历史 quick)归入合成桶。前端 `ArchiveDailyReportPage.vue`(`/archive-daily-report`,菜单「文件留底」组),点客户可看当日批次明细并弹 BatchDetailDialog。
- 写操作:单批重审(`rerun/{batch_id}`)、按新规则批量重判(`admin/rejudge-overall`)、批量重审(`admin/rerun-files-batch`)—— 见上文「重审/重跑/批量操作」。

### 文件信息（文件维度查询）

- 前端 `FileInfoPage.vue`（`/file-info`）→ `GET /api/admin/file-infos`（Swagger tag「文件信息」）→ `archive_detect_crud.admin_list_files`。
- 以**单文件为维度**跨批次查询，每行带批次/客户/进展上下文；支持批次号、文件编码、文件名、状态、判定、客户编码/姓名、进展名、办理人模糊筛选 + limit/offset 分页。

### 可观测性：事件流 + 外部请求日志 + 调用外部接口

- **事件流**：`event_service.log_event(severity, category, message, context)` fire-and-forget 写 `system_events`,前端 `/events`(EventsPage.vue)。category 常量在 event_service.py(batch.*/file.*/worker.crash/llm.timeout 等)。
- **外部请求日志**：纯 ASGI 中间件 `request_log_middleware`(在 main.py 用 `app.add_middleware` 注册,不能用 `BaseHTTPMiddleware`——它读 body 会噎死下游 Pydantic)。只记 `POST /api/archive-detect/business/batch` 的 JSON 请求体,前端 `/request-logs`(RequestLogsPage.vue)。
- **调用外部接口**：URL 刷新等非 AI 出站调用记 `external_api_logs`,前端 `/external-api-logs`(ExternalApiLogsPage.vue)。
- **AI/LLM 调用记录**：所有 LLM 调用记 `ai_api_calls`,前端 `/ai-api-calls`(AiApiCallsPage.vue),支持按 operation/model/batch_id/file_id/client_code/task_id 筛选。见「数据库重点」里的埋点说明。
- 四张表(system_events / api_request_logs / external_api_logs / ai_api_calls)都在 `_split_cleanup_loop` 里 GC 30 天。

### 销售线索：子女年龄

- 路由 `/api/sales/child-age-leads`，逻辑在 `backend/db/sales_crud.py`。
- 从 `family_members` 表中 `relation in ('child','子女','子','女','儿子','女儿','son','daughter',...)` 的记录算年龄；带 `min_age/max_age` 筛选时在 Python 层过滤（避免复杂 SQL），`total` 可能略有偏差是当前的可接受 MVP。

## 客户画像（接口导入）

入口路由 `/api/profile/*`（tag「客户画像」）+ `/api/doc-extract/*`（tag「信息提取」），前端 `/profile`(ProfilePage.vue)。方案细节见 [docs/09-客户画像-Excel导入方案.md](docs/09-客户画像-Excel导入方案.md)（流水线,Excel 入口已退役）与 [docs/10-客户画像v2-复核与领域模型.md](docs/10-客户画像v2-复核与领域模型.md)（**v2 领域模型与复核,当前实现**）。

流程：前端「导入客户文件清单」弹窗（客户编号/操作人默认 Jason邹启/条数 100 仅展示——接口固定返回最近 100 条不支持条数参数）→ `POST /api/profile/import-remote/preview` 拉业务方 `getAfterCustomerAllFiles`（`profile_import_service.fetch_after_customer_files`，URL 取 `config.json.file_url_service.customer_files_url`、缺省从 base_url 推导；同名客户多条目按姓名 `group_api_customers` 合并，记 `external_api_logs` service=`customer_files` 只存小摘要）→ 勾选客户 → `POST /api/profile/import-remote` 每户 `parse_api_manifest` 适配（**兼容扁平 `files[]` 与按项目嵌套 `list[].files[]` 两种返回形态**；过滤 `._` 垃圾文件/无编号行/按编号去重）→ 建/联 `profile_households` 家庭（老 clients 仅软关联）→ 落 `customer_files` → 主进程 `asyncio.create_task(run_imports_sequential)` **多户串行**、**任务内文件级并发**(`_IMPORT_CONCURRENCY=3`,LLM 网络等待与后续文件下载+OCR 重叠;OCR 引擎有全局锁推理天然串行,CPU 不叠加;**归因+画像写库段用 `_ATTR_WRITE_LOCK` 串行化**,防并发文件共享家庭域的 person/case get-or-create 竞态与字段写冲突)处理每个文件：

1. **取 OCR**：先 `customer_file_crud.find_reusable_ocr(file_code)` 全局查 `archive_detect_files` 同 file_id 最新 done 且有 ocr_text 行（复用脱敏文本）；没有再 `file_fetcher.refresh_download_url(file_code)` 刷新地址 → 下载 → **算内容 sha256 查同家庭兄弟行 `find_household_dup_ocr`(内容级去重:同一文件跨售后项目 file_code 不同,编号去重拦不住;命中则跳过 OCR 复用文本+分类,ocr_source 沿用兄弟行保持脱敏性质可溯源)** → 未命中才 `text_extractor.extract_text`。fresh 存**未脱敏原文**;**原件移存 output/customer_files/ 留存 30 天**(GC 挂 `_split_cleanup_loop`,DB/OCR 永留;`GET /api/profile/files/{id}/raw` 在线查看,已清理则按需重下顺延)。
2. **分类**：`doc_type_matcher.classify`（纯函数：strong/positive/negative 关键词评分 + 文件夹/相对路径线索，≥60 且领先 ≥10 定类）→ 置信不足才 `llm_service.recognize_doc_type` 兜底（2000 字头,失败→other 不抛）。
3. **提取**（12 类可配规则：id_card/hukou/degree_cert/birth_cert/passport/kyc_form/marriage_cert/property_cert/no_crime/approval/submission/receipt）：**同内容重复文件(命中内容去重)且规则无 entity=case 字段时跳过 LLM 提取**(`_record_dup_extract_skip`:person/asset 类提取幂等,留痕 skipped/dup_content + write_stats 沿用兄弟行归因人,人员「查看文件」并集可查;case 类里程碑按 affter_entryoid 路由本项目案件,必须重跑)→ **OCR 乱码先拒提**(`review_service.is_garbled`,防垃圾人名错误建人)→ 读该类型规则 → `llm_service.extract_doc_fields` 按规则 fields JSONB 提取（**多人模式**：规则带 `multi=True`（户口本整本 RULES_VERSION=2 起;**结婚证 RULES_VERSION=3 起**,见下「结婚证配偶关系」）时改走 `extract_doc_fields_multi` 输出 `{"persons":[...]}`，`_extract_one_multi` 逐人清洗/归因/写库，仍写一行 `doc_extract_results`，`write_stats.persons` 存逐人明细、顶层 `person_id`=首个归属人供复核预填；乱码假名 `plausible_person_name` 拦截不参与归因/建人）→ 清洗（脱敏占位词 `[身份证]` 等记 `skipped_masked` 不写库不参与归因）→ **字段校验(`field_validators.validate_field_items`,单人/多人均在 `_clean_field_items` 内统一跑)：身份证号 GB11643 校验位不过时按"单字符形近替换+内嵌日期/地区码/birth_date/gender 交叉过滤"找候选,唯一候选自动改(记 `write_stats.repairs`,修复值参与归因与写库),多候选/无候选不改记 `write_stats.validation_flags`;合法证件号派生缺失的 birth_date/gender(含 15 位老证);出生日期在未来/签发晚于有效期/日期无法解析也记 flags;原始 LLM 输出仍在 `extracted` 审计不动** → `profile_crud.find_person_match`（家庭内归因,**去重口径：简体/繁体/间隔号/拼音同一人不重复建卡**——证件号归一化(去空格/大写)→姓名繁→简+去间隔号折叠(OpenCC `_fold_cjk`,阿不都·外力==阿不都外力)→name_en 词序无关→拼音互转(matched_by="pinyin",连写两序变体 `_pinyin_glued_variants`,中文名↔英文证件名互中;多音字取 pypinyin 默认音为已知限制),查不到新建 person `relation_to_main='待确认'`;人工建人 `create_person` 同口径查重命中返回 `deduped=True` 不新建;`get_or_create_household` 家庭名同按繁简折叠;**三个建卡点统一走 `_create_person_in_session` 按 `name_folded` 折叠键(migration 023 加列回填,024 建 (household_id,name_folded) 唯一索引)先查后建+SAVEPOINT 捕 IntegrityError 兜底并发双卡**;**有 entity=case 字段时不要求姓名归因**）→ `apply_extracted_fields_v2` 写 `profile_person_fields`（**人工字段不覆盖、declared 与 verified 同等(不区分来源层)、复核修正永远覆盖**;特殊字段 `_relation`=与户主关系,仅在'待确认'时落地到 person.relation_to_main;**非主申请人的 `_relation=户主` 不落**——户口本户主≠画像主申请人,避免多个"户主"）→ `doc_extract_results` 留痕。**规则字段 `target.entity='asset'` 时走 `apply_extracted_asset` 写 `profile_assets`**（房产证类;attrs JSONB 存 key:value,**去重靠 AI：家庭内同类型无候选直接新建；有候选时调 `llm_service.judge_asset_duplicate`(operation=asset_dedup) 判定,match_id + confidence≥60 才合并,LLM 异常降级新建**,仅 status='ai' 可更新;画像接口/弹窗带「家庭资产」区块）;**`target.entity='case'` 时走 `apply_case_milestones` 写 `profile_cases`**（递交/签收/批复里程碑,**按项目多案件(migration 022)：一个售后项目=一个案件,案件行承载项目字段(affter_entryoid/projectno/projectname/projectno_detailed/projectname_detailed/project_created_at),路由键=文件行 `customer_files.affter_entryoid`,NULL→默认案件(扁平形态/旧数据);导入时 `upsert_project_cases` 先建全部项目案件壳;里程碑按 name upsert,状态 签收>交付>获批>递交 派生;画像弹窗「案件时间线」tab**）。
4. **质量评级**：`review_service.evaluate_file_quality` 纯规则打分（无文本/乱码/过短/提取异常/no_person/证件号脱敏/**字段校验疑点 field_validation**/分类置信低）→ `customer_files.review_status/quality_score` + 任务 `needs_review_count`。

复核闭环：`GET /api/review/files` 队列（质量分升序，**不传 import_task_id 即跨任务全局队列**）→ 画像页红色横幅 → 复核抽屉三栏（原件|OCR|字段表单+归属选择，共享组件 `ReviewDrawer.vue`，prop `importTaskId` 可选 + `@done`；标签函数在 `utils/labels.js`）→ `confirm/correct/dismiss`(correct 永远覆盖写 person_fields 并留 `corrected` 痕,**定了归属人同时写 `customer_files.person_id`**)。

文件归属（原复核中心页改造）：`/file-assign`（FileAssignPage，导航「文件归属」）→ `GET /api/profile/files`（全局分页文件列表,客户/文件名称(filename 与 file_code 均模糊)/类型/归属状态筛选,归属人列优先+提取归因兜底）→ 行内「归属」弹窗（左原件预览+右家庭成员下拉/新建人）→ `POST /api/profile/files/{file_id}/assign`（写 `customer_files.person_id`,可清除;校验归属人属于文件所在家庭）。**`customer_files.person_id` 是文件↔人关联的权威载体(migration 021)**；`list_person_files`（人员「查看文件」）与完备度矩阵人档关联均按 **列 ∪ write_stats 顶层 person_id ∪ write_stats.persons[] 多人明细** 三路并集查询。

完备度矩阵：`GET /api/profile/tasks/{id}/matrix`（`profile_crud.build_completeness_matrix`，纯查询无 LLM）——人×材料类型（身份证/护照/户口本/出生证明/结婚证/无犯罪/学位证），格值 ok/warn/missing/na。文件类型归并：`resolve_matrix_type`（doc_type 优先,无犯罪/结婚证等靠文件名+文件夹提示词）；人档关联：提取归因(person_id)+文件名含人名；户口本/结婚证为家庭-夫妻共用件不按人名过滤;**有任一可用文件即 ok,全部待复核才 warn**。前端画像弹窗「完备度矩阵」tab,黄格点击直达复核。

护照到期提醒：`profile_crud.passport_expiry_info`/`attach_passport_expiry` 在画像接口给每人挂 `passport_expiry={date,days_left,level}`（level: expired / expiring≤180 天 / ok，阈值常量 `PASSPORT_EXPIRY_WARNING_DAYS`，移民递签 6 个月惯例）；前端画像弹窗顶部横幅+「护照到期日期」字段内联标签，全部 ok 时不渲染。**只提醒护照**（无犯罪等其他证件明确不提醒）。

交叉验证提醒+字段可信度：`profile_crud._collect_field_provenance`（读时共享采样器：家庭全部 done 提取结果→全字段来源样本,一次 DB 查询同时喂冲突与可信度）→ `collect_field_conflicts`（仅 8 个 verified 身份字段、≥2 种归一化值才算冲突）挂 `field_conflicts`;`credibility.compute_field_credibility`（纯函数,`backend/credibility.py`）+ `attach_field_credibility` 给**每个字段**挂 `credibility={score,level,reasons,corroboration,conflict_count,sources}`。打分(0-100 确定性)：人工 confirmed/corrected 短路 100/高;基底 verified 70/declared 50;≥2 个不同文件取值一致 +15(≥3 再 +5)、跨证件类型互证 +5;存在不一致取值 -25;≥80 高/50-79 中/<50 低。归一化要点：拼音名忽略大小写与空格/粘连（NICHENG==NI CHENG，词序仍比对）、日期多格式、性别中英映射、masked 剔除；数据来自 `doc_extract_results.mapped`(key→extracted 取值）+`write_stats` 归因。**AI 只提示不改值**;前端每字段渲染高/中/低徽标(绿/橙/红,tooltip 显示分数+理由),点击开「字段来源与可信度」抽屉(上编辑+确定走 `correct_person_field`、中可信度构成、下来源列表带 一致/不一致 标+文件预览)——已取代旧「多源」标;顶部横幅按 `conflict_count>0` 汇总。

关键逻辑：

- **幂等**：`file_code` 全局唯一；重复导入同一客户新建 task 但已 done 文件只 re-link 不重新下载/OCR；`content_sha256`(migration 025)承载同家庭跨项目同内容去重(复用 OCR/分类,非 case 类跳过提取)；提取按当前代码规则重跑写新结果行;person_fields 同值跳过(skipped_same)保证数据层幂等。
- **规则维护改代码常量**：规则在 `backend/extract_rules.py` 的 `EXTRACT_RULES` dict;改规则=改该文件+**重启后端主进程**(`RULES_VERSION` 手动 +1 便于溯源;画像提取在主进程 asyncio task,不是 worker)。无 Swagger 端点、无前端页。
- **LLM JSON 容错**：`extract_doc_fields` 解析失败重试一次(模型偶发字符串内未转义双引号);prompt 已要求值内引用用中文「」。
- **复用脱敏文本的表现**：证件号抽成 `[身份证]` → `skipped_masked`,归因退化为按姓名并标 `masked_id` 待复核；姓名/性别/出生日期等不脱敏字段仍可写入。
- 加新证件类型：`extract_rules.py` 加一条规则 + matcher 加组关键词 + `llm_service.DOC_EXTRACT_TYPES`/recognize 白名单加一项(小改三处);若要写新目标表(如资产)再扩 `target.entity` 分派。
- **前端交互(画像弹窗)**：人员卡片字段按 4 大组分段(基础个人信息/护照信息/公司收入/其他证件)+分割线,组内字段横向 3 列(`utils/labels.js` 的 `groupPersonFields`);人员卡 **inline 编辑**(编辑→字段变 input→保存/取消,`POST /api/profile/persons/{id}/field` 走 `correct_person_field`;**前端 diff 只提交真正改动的字段**——后端该端点语义是"永远覆盖并标 corrected",整包提交会把未改动字段误标「已修正」;全无改动不发请求直接退出编辑态,可信度抽屉保存同理;**修正「姓名」字段会同事务同步 `person.name`/`name_folded`,撞家庭内他人折叠键报 ValueError 提示走合并**);「查看文件」按钮开 `FileListDrawer`(通用文件清单抽屉,纯展示:文件名称/文件类型/操作三列表格,数据父组件注入;人员= `listPersonFiles`、家庭资产区块级「查看文件」= `listHouseholdAssetFiles` 取全部资产 source_file_id 去重),点行内「查看」弹 `FilePreviewDialog`(append-to-body 最外层,宽 70%、上下拉满,整窗只显示原件 img/iframe PDF,Office 文件走 preview-pdf 转 PDF 预览,其他类型提示不支持预览);字段**可信度徽标**(高/中/低)点击开**字段来源与可信度抽屉**(上字段编辑+确定,中可信度构成,下来源文件列表带一致/不一致标点「查看」弹同一 `FilePreviewDialog` 大预览,保存同 `correct_person_field`)。
- **文件清单 tab**：上方文件表格(含「提取」列=`latest_extract_status`,`list_task_files` 用 DISTINCT ON 取最新提取状态)+点行/查看详情弹三栏弹窗(左原件 iframe/中 OCR/右提取结果详情,`profile.extractions` 按 customer_file_id 前端过滤);三栏详情已从 inline 改弹窗。
- **抽屉并排模式**：字段来源与可信度/查看文件抽屉 `:modal=false` + `modal-class=side-drawer-overlay`(CSS pointer-events none 穿透 + 抽屉 auto),客户画像 el-dialog 用 `profileShift` 动态让出右侧(`:width=profileDialogWidth` + marginRight),两者并排同时操作;抽屉左边缘可拖拽调宽;关闭客户画像 watch 联动关右侧抽屉。
- **任务列表**：状态+客户名+**客户编码**查询（`list_import_tasks` 按家庭 customer_code 子查询筛选，行返回 `customer_code`）+ 分页(默认 10 条,10/25/50/100)；表格含「客户编码」列；画像弹窗头部客户编码与姓名同款大粗样式（`.profile-dialog-title`），旁挂 CRM OID；客户画像菜单页样式对齐外部请求日志页(白色顶栏+灰背景+filter-card 查询区+表格 card)。
- **文件预览**：画像内原件用 `img`(图片)/`iframe`(PDF)预览;**Office 原件(doc/docx/xls/xlsx/ppt/pptx)走 `GET /api/profile/files/{id}/preview-pdf`**(soffice 按需转 PDF,缓存 `output/customer_files/previews/{file_id}.pdf`,无 LibreOffice 返回 501 前端回落"不支持在线预览");其他类型提示"不支持在线预览",避免 iframe 触发浏览器下载。
- 单文件异常只标 error 不杀任务；任务级事件 `profile.import.done/error`、提取事件 `extract.done/error/skip` 进 `system_events`。
- **任务删除（删除画像，2026-07-28 语义反转）**：`DELETE /api/profile/tasks/{task_id}`（前端任务列表「删除」）→ `customer_file_crud.delete_import_task`：**任务有 household 时 = 删除画像**（委托 `delete_household_profile`）——只 DELETE household 行，DB CASCADE 删 persons/person_fields/assets/cases，`profile_import_tasks.household_id` 被 FK(SET NULL) 自动置空，**任务/文件/OCR/提取结果/磁盘原件全部保留**（删前清 `customer_files.person_id` 裸列防悬挂）；重新导入按 file_code re-link 即可复用 OCR 重建画像。无 household 时维持原行为：任务级删，`customer_files`/`doc_extract_results` CASCADE + 磁盘原件连带删。`run_import` 在每个文件边界查任务是否还在，被删则协作停止。事件 `profile.import.deleted`。
- **重新生成画像（2026-07-28 改为原地重跑，不新建任务）**：画像弹窗标题栏右侧「重新生成画像」按钮 → `POST /api/profile/households/{household_id}/regenerate?task_id=` → **复用已有任务作为宿主原地重跑**（宿主=前端传的 task_id 即当前打开画像所在任务，须属于本家庭；缺省取家庭最近任务 `get_latest_task_for_household`；家庭无任务时兜底新建，正常不会发生）——`reset_import_task` 把宿主任务重置回 running/计数器清零，`upsert_task_files` 把家庭名下跨任务全部 `customer_files`（`list_household_files`）re-link 到宿主任务后 `run_import`：done+有 ocr_text 复用 OCR 只重分类/提取，error/pending 自动重置重试，无本地原件按 file_code 刷地址重下载；有 running 任务 409（`has_running_task`）。画像域数据不清空，人工已确认/修正字段不覆盖；`doc_extract_results` 追加新行（同重复导入）。前端弹窗保持打开，3s 静默轮询 `reloadProfile(taskId, silent)` 刷新进度直到任务 done/error（按钮在 running 时禁用）。事件 `profile.import.regenerate`。
- **恢复中断任务（2026-07-28，进程重启卡 running 治理）**：导入任务是主进程 asyncio 协程，重启即死、DB 永远卡 `running`（stale）。`profile_import_service` 模块级 `_ACTIVE_TASKS` 内存登记（`mark_task_active/unmark_task_active`，run_import 入口登记、finally 释放）区分真在跑 vs stale；`POST /api/profile/tasks/resume-stale`（前端任务列表「恢复中断任务」按钮）把所有 stale 任务同步 mark 后 `run_imports_sequential(resume=True)` 串行**断点续跑**：计数器从 DB 基线继续（进度不归零）、只跑 `list_unfinished_files`(pending+error，**done 文件不重跑**)、error 行自然走 fetch/OCR 重试路径。幂等：恢复中重复调用返回 `resumed: []` + `skipped_running`。事件 `profile.import.resumed`。
- **同名人员合并（2026-07-28，同名双卡治理）**：`profile_crud.merge_persons(household_id, keep_id, drop_id)`——把 drop 人并入 keep 人（单事务）。字段仲裁：**人工(confirmed/corrected)永远胜 AI；双人工 keep 胜；双 AI 按 `updated_at` 晚者胜（=后续覆盖前面）；keep 无该 field 直接迁**；败方字段快照进返回 `fields_lost` 并随事件留痕（可恢复性凭据）。骨架交接：drop.is_main→keep 接 `is_main`+「户主」、`household.main_person_id` 改指、relation/avatar 只补空、`person.name` 缺省恒保持 keep 原值;手动合并弹窗可选保留哪个人名(`keep_name` 透传,在 drop 删除 flush 后改名+重算 name_folded,撞家庭内第三人折叠键报 ValueError;改名记录进返回 `name_changed`)；**先重挂再删人**：`customer_files.person_id`/`profile_assets.owner_person_id`(FK SET NULL)/`doc_extract_results` 的 write_stats(顶层+persons[]+mapped[],extracted/corrected 原文不动)。自动触发：`run_import` 收尾（关系推导之前）`run_merge_duplicate_persons` 按 `person_name_fold` 分组自动合并（keep 选择 is_main>人工字段多>id 小；**守卫：组内 ≥2 人各持不同合法 id_number → 跳过**防同名父子，gender 冲突不阻塞）。手动入口：画像弹窗人员卡「合并」按钮 → `POST /api/profile/persons/merge`；单家庭 `POST /api/profile/households/{id}/merge-duplicates?dry_run=`；全量存量清理 `POST /api/profile/admin/merge-duplicates-all?dry_run=`（跳过 running 家庭）。事件 `profile.person.merged`。配套防新增：`import-remote` 同家庭有 running 任务按户跳过（响应带 `skipped_running`）。
- **结婚证配偶关系（2026-07-28,RULES_VERSION=3）**：marriage_cert 规则改**多人模式**(rule v2,`multi: True`)——中国结婚证含双方姓名/性别/出生日期/身份证号,prompt 输出恰好 2 个 person 对象(第一=持证人,第二=配偶,`cert_role` 标记只抽不写库供推导定位);两人各自归因(双方身份证号→强归因),**配偶无卡自动建卡**(走 `_extract_one_multi` 现成路径+折叠键去重,重跑幂等);每人 `spouse_name` 字段填**对方**姓名(新 PROFILE_FIELDS 键,verified 层,互写天然形成可查询的配偶边,双方都非主申请人时靠它表达关系,骨架 relation_to_main 表达不了);`marital_status=已婚`+`marriage_date/authority/cert_no` 两卡都写(同值 skipped_same 幂等)。**关系推导双格式兼容**:`_infer_from_marriage_cert` 对 multi extracted 按 `cert_role` 定位持证人/配偶、证件号优先匹配(persons[0] 非持证人时按 cert_role 现查持证人兜底),旧单人格式(spouse_name 顶层)分支保留(历史数据);pairs 逻辑不变——一方为户主时另一方写「配偶」。前端 labels.js 加 spouse_name(基础信息组)。注意:存量家庭旧结婚证是单人提取,需「重新生成画像」重跑才会建配偶卡。
- **英文证件建人 + 家属准证关系边 + 证件到期提醒（2026-07-30,RULES_VERSION=4）**：
  - **name_en 建人**：此前建人只认中文 `name`，纯英文证件(新加坡 EP/DP 卡、英文批复)提取到人却落不了卡(LIU SONGHAO 案例)。现 `apply_extracted_fields_v2` 建人回退到 name_en，门槛 `plausible_latin_name`(字母/空格/.'-、2-4 词、每词≥2 字母，防 "SGWORKPASS" 类 OCR 噪声建人);`_extract_one` 的 no_person 跳过守卫同步改为 `can_create = 中文名 or 合法拉丁名`。拉丁卡名先以英文为显示名，后续中文证件经 find_person_match 拼音路归并同卡(刘松昊↔LIU SONGHAO 实测归并)。非家庭成员的英文证件(如本地董事)也会建卡(relation 待确认),人工在画像弹窗处置——这是有意放宽。
  - **sponsor_name 关系边**：approval 规则 v3 新增 `sponsor_name`(家属/受抚养人准证 DP/LTVP 卡面 MAIN PASS HOLDER 姓名;主签本人或证件无此栏输出 None);`_clean_field_items` 丢弃 sponsor==本人 的值(`_is_self_sponsor`：拉丁词序无关/繁简折叠/拼音连写三口径);`infer_family_relations` 新增 `_infer_from_sponsor` 分支(在出生证/结婚证之后、纯启发式之前):sponsor 命中户主 + 户主年长>15 岁 + 性别已知 → 写「子/女」(basis=sponsor:main_pass_holder,证据链挂 sponsor 字段的 source_result_id;DP 区分不了配偶与同龄亲属,年龄差不足不猜)。
  - **到期日入库 + 全库提醒**：`approval_expiry_date`(approval 规则 expiry_date 此前 column=null 只抽不写,EP 到期 2026-11-14 曾读到即丢)、`id_card_expiry_date`(id_card v2,LLM 直接解析有效期限止日期,「长期」→None)两个新 PROFILE_FIELDS 键(verified 层,均入 DATE_FIELDS 校验);`profile_crud.list_expiry_reminders` 扫全库 `EXPIRY_FIELD_TYPES` 字段(护照/准证批复/身份证)算 expired/expiring(≤days,默认180)/ok,按剩余天数升序、支持 keyword/include_ok 过滤(Python 层,同 sales_crud 模式);端点 `GET /api/profile/expiry-reminders`,前端 `/expiry-reminders` 页(ExpiryRemindersPage,导航「到期提醒」)——续签/换证商机入口。字段值冲突(AI 后写赢,如 IPA 期限覆盖准证有效期)交给可信度徽标+人工确认,不做写时仲裁。
  - **LLM 占位字符串清洗**：`_clean_field_items` 把精确匹配 `none/null/n/a/nil` 的值按空值丢弃(实测 LLM 把 sponsor_name 输出成字符串 "None" 落库;中文「无」不受影响,房产证他项权利等 legit 值保留)。存量脏行直接 SQL 删(status='ai' 可安全删)。
  - 存量家庭要生效需「重新生成画像」(规则重跑才提 sponsor/到期字段并建英文卡);测试 `tests/test_name_en_expiry.py`。
- **家庭关系交叉推导**：`profile_crud.infer_family_relations(household_id)`——出生证父/母一方命中户主本人→另一方（已建档）写「配偶」；结婚证持证人与配偶一方为户主→另一方写「配偶」；启发式（同姓 + 户主年长>15 岁 + 双方户籍地址不冲突）→ 按性别写「子/女」。只写 `relation_to_main='待确认'` 的人（走 `_relation` 通道），人工已确认永不回改；**只匹配已有 person，绝不因推导建人**；幂等（二次跑全被 skipped_filled 挡掉）。`run_import` 收尾自动跑（异常不杀任务）+ 手动 `POST /api/profile/households/{id}/infer-relations`（刷历史已导入家庭）；事件 `profile.relation.inferred`。

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
- **8.138.111.12（测试服）有 ~470 个 Office 文件(doc/docx/xls/xlsx)卡 `pending`**(2026-07-28 诊断):不是识别不了,是 stale 任务残留(当时部署早于 resume-stale 功能;**2026-08-03 已部署含 resume-stale + 嵌图 OCR 的新版**);点「恢复中断任务」或对家庭「重新生成画像」即可消化,嵌图 OCR 生效后原落 other 的扫描贴图文档会正常分类提取。该机 pip 依赖(xlrd/olefile/python-docx/openpyxl)与 soffice(/usr/local/bin/soffice)均已确认就位;IOD 机部署时同样要确认 **LibreOffice 已装**(Office 预览 preview-pdf 与 .doc 兜底依赖,原为可选现为准必需)。
- **StatReload 会"部分漏载"**：带 `--reload` 也可能只重载部分模块（实测：profile_import_service 重载了新签名、profile_crud 没重载,跑批 51 条 TypeError extract_error）。**跨模块改签名后不要信 --reload,整进程重启再跑批**；验证方法=curl 一个只有新代码才有的行为（openapi 新 summary / 响应新键）。重启后行为仍是旧代码时,检查是否有旧实例占着端口——Windows 允许同端口重复绑定,netstat 里的 PID 可能是已死的 reloader 父进程,真凶是它留下的 pythoncore `spawn_main` 孤儿子进程（列全部 python 进程按命令行找,杀掉即恢复）。
- **重审/重跑已统一走 worker DB 队列**：`submit_recheck_batch`(新建 recheck 批次)、`rerun_batch_inplace`(原地重跑)都是写 `status='pending'` 行(有 ocr_text 的写进 `reuse_ocr_text` 让 worker 跳过下载+OCR、只重跑 LLM)→ worker 消化 → 主进程 `_batch_finalize_poll` 轮询生成 overall。老的主进程 fan-out 函数(`_orchestrate_recheck`/`_process_one_recheck`/`_finalize_overall_for_batch`/`_orchestrate_rerun`)已删除,不要再引用。
- `archive_detect/` 独立子项目已迁出到 `E:\qoderproject\archive_detect\`；仓库内如残留空目录不要依赖。
- `archive_detect_files.content_sha256` 列已建但当前不写值；增量复用依赖业务方传稳定 `file_id`。
- `archive_detect_folder_summaries` 表已建，进展包维度滚动总报告是后续阶段，不要误以为当前已写入。
- `pdf_ocr.py` 单文件 CLI 若存在，不属于 web 流程，改 web 流水线时不需要同步改它。
- **鉴权只豁免业务方提交/轮询**：配置了 `auth.biz_api_key` 后，除 `/api/archive-detect/business/batch` 前缀外的所有 `/api/*` 都要 Bearer（见「鉴权」节）；老文案"业务接口不加鉴权"已过时。
- **客户档案生成的候选列表不携带 `ocr_text`**：`client_profile_crud.list_source_files_for_client` 只返回元数据，生成阶段 `client_profile_service._generate_background` 再按 `id` 重查 `ocr_text` 喂给 LLM，避免大文本反复传输。
- **`/api/client-profile/generate/{client_id}` (POST) 与 `/api/client-profile/generate/{task_id}` (GET) 共用同一前缀**：FastAPI 按 method 区分，但任何新增 GET 子路径必须放在 `/generate/list/{client_id}` 这类更具体的路由之前，否则会被 `{task_id}` 抢匹配。
- **`frontend2/` 目录名有误导性**：内容是 .NET 业务后端重写 PoC，不是前端（见「重构进行时」）。
- **`backend/backfill_done_files.py` 是一次性脚本**：回填历史批次 `done_files` 计数，非常驻流程。
- **独立脚本调 LLM 必须先 `llm_service.load_config()`**：`CONFIG` 是模块级 `{}`，由 main.py/worker_runner 启动时加载；脚本不调用则所有 LLM 调用报"未配置大模型 API Key"并**静默降级**（分类落 other、不提取），极易误判成模型故障。
- **`tests/test_profile_api_import.py` 是一次性参考脚本（非单元测试）**：业务方 `getAfterCustomerAllFiles` 接口 → 复用生产适配 `profile_import_service.parse_api_manifest`（过滤 `._` 开头 macOS 垃圾文件、按文件编号去重）→ 复用 `run_import` 逐户串行跑，是「业务方接口作为文件来源」的早期验证脚本（生产入口已上线为 `POST /api/profile/import-remote`，脚本保留白名单/心跳等批处理特性）。`--dry-run` 只拉清单不写库，`--only 姓名1,姓名2` 补跑指定客户。实测注意：接口全量约百个客户且动态变化，同一客户可能多条目（不同 affter_entryoid），条目可能后续返回 0 文件（affter_entryoid 变 null）。
- **线程上下文写库必须走 psycopg2 同步引擎，跨 loop 用 asyncpg 是致命的（2026-07-29 实锤修复）**：IOD 生产 uvicorn 两次无声死亡（各在画像任务恢复跑批 ~40 分钟后），内核日志显示均为 asyncpg `protocol.so` 同一指令偏移段错误——根因是 `event_service.log_event` 同步分支在 worker 线程里 `asyncio.run(async 写)`，临时 loop 跨用绑在主 loop 的 asyncpg 连接池。修复：同步分支改走 `event_crud.insert_event_sync`（psycopg2，与 `insert_ai_api_call_sync` 同模式）；同时 `ai_api_call_crud._build_row` 对所有短 varchar 字段按列宽硬截断（`_clean_short`，不加截断标记——`_clean_text` 的标记会超出列宽），`detect_large_table_doc` 的 task_id 从临时全路径改传短 id（此前 107 次 StringDataRightTruncation）。**教训：跨 loop 用 asyncpg 不是"丢日志"级别，是杀整个进程级别，且无任何 Python traceback。**
- **根目录一次性产物勿当流程依赖**：`gen_ceo_report.py`、`o2.txt`、`*.docx` 汇报文档是临时汇报产物；`tests/test_archive_detect_queue.py.deprecated` 是退役测试留档。
- **导航为左侧分组侧边栏(2026-08-03 由顶栏改造)**：菜单配置在 `frontend/src/menu.js`(与路由分离,加页面=router.js 加路由+menu.js 加项),App.vue 渲染可折叠 el-menu 分组侧边栏(业务审核/客户画像/日志监控/工具箱 4 组)。「客户档案」(/clients)、「子女年龄线索」(/child-age-leads) 不在菜单(路由仍在,可直接输 URL 访问)。原「复核中心」页已改造为「文件归属」页（`/file-assign`，见客户画像节）；复核入口从画像页红色横幅进。
