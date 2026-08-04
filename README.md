# 智能文档审核工作台

移民/售后客户材料处理系统。当前主线是**文件留底检测/业务审核**（业务方传 OSS 文件列表 → OCR + LLM 按公司留底分类体系判定），同时保留客户画像、AI 材料解析、Word 模板填写、PDF 拆分、URL 文件摘要等能力。

> ## ⚠️ 仓库可见性提醒
>
> 当前仓库为 **Private**。本地 git 历史已通过 `git checkout --orphan` 重置为干净的 initial commit，**远程历史中不含 `config.json` / API Key / `.venv312`**。
>
> - **新成员加入**：clone 后复制 `config.json.example` 为 `config.json` 并填入实际配置；`config.json` 已在 `.gitignore` 内
> - **若未来要改为 Public**：当前历史是干净的，无需重写；但需检查仓库设置中是否还有 fork、collaborator 持有的旧引用
> - **本地分支 `main-old-backup`** 保留了重置前的老历史（含敏感数据），仅在本地，**绝对不能 push**

---

## 功能总览

| 功能 | 前端入口 | 说明 |
|---|---|---|
| **文件留底检测 / 业务审核** | `/archive-detect`、`/archive-admin`、`/archive-daily-report` | 业务方接口提交客户+项目+进展+文件列表（OSS URL）→ DB 队列 + 独立 worker 进程 OCR/LLM 判定 → 批次总体报告；支持历史复用、重审/批量重判、每日报告 |
| **客户画像（接口导入）** | `/profile`、`/file-assign`、`/expiry-reminders` | 从业务方接口拉客户文件清单 → 全量 OCR 入库 → 12 类证件分类提取 → 归因写入独立画像域（人员/字段/资产/案件），含复核闭环、完备度矩阵、证件到期提醒 |
| **AI 材料解析** | `/parse` | 上传 PDF/图片 → OCR + LLM 提取字段 → 人工复核 → 归档客户档案 |
| **AI 填写文件** | `/template` | 上传 Word 模板 → 扫描占位符/锚点 → 从客户档案填值 → 输出 docx/PDF |
| **处理超长 PDF** | `/split` | 多证件合并 PDF → 全页 OCR + LLM 判断页边界 → 按证件类型拆分 |
| **URL 文件摘要** | `/summary` | 输入文件 URL + 进展名 → 下载/OCR → LLM 摘要与相关性判断 |
| **日志监控** | `/events`、`/request-logs`、`/external-api-logs`、`/ai-api-calls` | 系统事件流 / 外部请求体 / 出站接口调用 / LLM 调用全量记录（均保留 30 天） |

---

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | FastAPI + SQLAlchemy 2 (async) + Alembic；业务审核为 DB 队列 + 多进程 worker（`SELECT FOR UPDATE SKIP LOCKED`） |
| 数据库 | PostgreSQL 14+（当前迁移 head：025） |
| OCR | RapidOCR 3.x（PP-OCRv6 small，onnxruntime CPU，模型随 wheel 内置）+ 自适应图像前处理 + pypdfium2/pdfplumber |
| LLM | OpenAI 兼容接口，模型 ID 由 `config.json` 驱动 |
| 前端 | Vue 3 + Element Plus + Vite + vue-router（hash）；左侧分组侧边栏 + 多页签（tags-view, keep-alive 保活） |
| 运行环境 | Python 3.12（venv 目录 `.venv312`） |

---

## 快速开始（Windows 开发环境）

```bash
# 1. 创建 venv 并安装依赖
python -m venv .venv312
.venv312\Scripts\activate
pip install -r backend/requirements.txt

# 2. 配置（DB + LLM + 文件地址刷新服务）
cp config.json.example config.json   # 然后编辑填值

# 3. 初始化数据库（alembic.ini 在项目根）
alembic upgrade head

# 4. 启动后端（必须从 backend/ 目录；本地固定 8002 端口）
cd backend
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ../.venv312/Scripts/python.exe -m uvicorn main:app --host 0.0.0.0 --port 8002 --reload

# 5. 启动 OCR worker（独立进程，另开一个终端；不起则业务审核一直 pending）
cd backend
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ../.venv312/Scripts/python.exe -m worker_runner --worker-id worker-1

# 6. 启动前端（必须带 VITE_API_TARGET 指向 8002）
cd frontend
npm install
VITE_API_TARGET=http://localhost:8002 npm run dev
```

打开 http://localhost:5173/ 。Windows 一键脚本：`start_backend.bat`（后端+worker 两窗口）、`start_frontend.bat`。

> **后端和 worker 是两个独立进程**：改了 OCR/抽取/LLM 代码，uvicorn `--reload` 不会重启 worker，必须手动重启 worker 才生效。

生产部署（Alibaba Cloud Linux ECS，nginx + nohup 直跑）见 [deploy/linux/README.md](deploy/linux/README.md)。

---

## 文档导航

- [CLAUDE.md](CLAUDE.md) — 各流水线设计决策、数据库重点、部署实录、已知遗留坑（最详尽）
- [AGENTS.md](AGENTS.md) — 面向 AI 助手的结构化总览（项目结构/技术栈/命令/陷阱）
- [docs/](docs/) — 重构参考开发文档（01 系统概览 ~ 07 重构规划，09 客户画像方案，10 画像 v2 领域模型）
- API 文档：启动后端后访问 http://localhost:8002/docs （Swagger，按业务分组带中文说明）

---

## 许可

内部使用。
