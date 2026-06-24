# 智能文档审核工作台

移民公司客户档案 + 文档智能处理系统。围绕"客户档案"这一核心实体，提供 OCR 识别归档、模板自动填充、PDF 拆分、文件 AI 摘要四大功能。

> ## ⚠️ 仓库可见性提醒
>
> 当前仓库为 **Private**。本地 git 历史已通过 `git checkout --orphan` 重置为干净的 initial commit，**远程历史中不含 `config.json` / API Key / `.venv312`**。
>
> - **新成员加入**：clone 后请复制 `config.json.example` 为 `config.json`，填入实际配置；`config.json` 已在 `.gitignore` 内
> - **若未来要改为 Public**：本仓库当前历史是干净的，无需重写；但需检查仓库设置中是否还有 fork、collaborator 持有的旧引用
> - **本地分支 `main-old-backup`** 保留了重置前的老历史（含敏感数据），仅在本地，**绝对不能 push**

---

## 四大功能

| 功能 | 入口 | 说明 |
|---|---|---|
| **AI 材料解析** | `/parse` | 拖拽多文件批量上传 → OCR + LLM 提取字段 → 智能匹配现有客户 → 路由归档到 clients / family_members / assets / KV 兜底 |
| **AI 填写文件** | `/template` | 上传 Word 模板 → AI 识别占位符 → 选客户后从档案抽取数据填值 → 输出 PDF（失败降级 docx）|
| **处理超长 PDF** | `/split` | 多证件合并 PDF → 全页 OCR + LLM 判断证件边界 → 按类型拆分为独立子 PDF → 一键打包下载（7 天 TTL 自动清理） |
| **文件解析** | `/summary` | 输入文件 URL + 进展名称 → 后台下载并 OCR/抽取文字 → LLM 输出摘要 + 关键要点 + 与该进展的相关性判断（强/弱/不相关） |

---

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | FastAPI + SQLAlchemy 2 (asyncpg) + Alembic |
| 数据库 | PostgreSQL 14+（业务表用 JSONB 存灵活字段） |
| OCR | PaddleOCR PP-OCRv4（中文场景） + pypdfium2（PDF 渲染）+ pdfplumber（文字型 PDF 直抽） |
| LLM | 火山引擎 ark endpoint（OpenAI SDK 兼容）；可换任何 OpenAI-compatible 端点 |
| Word 处理 | python-docx + mammoth（HTML 预览） + docx2pdf（Word 自动化转 PDF）|
| 前端 | Vue 3 + Element Plus + Vite（无 vue-router，用手写 viewMode 状态机）|
| 运行环境 | Python 3.9–3.13（推荐 3.12，PaddlePaddle 限制） |

---

## 数据库结构

5 张业务核心表 + 3 张系统表：

| 表 | 用途 |
|---|---|
| `clients` | 客户主档（身份/联系/护照/教育/工作/婚姻 ~33 字段）|
| `family_members` | 家庭成员（配偶/子女/父母/紧急联系人共用，按 relation 区分）|
| `assets` | 资产（房产/存款/银行流水/股票/车辆/其他，按 asset_type 区分）|
| `client_info` | KV 兜底（联系方式、雅思成绩、旅行史等未纳入强 schema 的字段）|
| `documents` | OCR 解析任务记录 |
| `templates` / `template_fills` | Word 模板 + 填充历史 |
| `split_tasks` | PDF 拆分任务（持久化状态，进程重启不丢）|
| `summaries` | 文件解析摘要历史（含 progress_name / 相关性字段）|

字段路由表 [`backend/db/field_router.py`](backend/db/field_router.py) 把 OCR 提取的字段名（91 个 clients / 42 个 family / 38 个 assets 别名）精准映射到对应表的具体列；未命中的进 `client_info` KV 兜底。

---

## 安装部署

### 1. 创建虚拟环境

```bash
# 推荐 Python 3.12
python -m venv .venv312

# Windows
.venv312\Scripts\activate
# Linux/Mac
source .venv312/bin/activate
```

### 2. 安装后端依赖

```bash
pip install -r backend/requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### 3. 配置数据库与 LLM

```bash
cp config.json.example config.json
```

编辑 `config.json`，填入实际的：
- PostgreSQL 连接信息（`database` 段）
- LLM API Key + base_url + model（`llm` 段，OpenAI 兼容格式）

### 4. 初始化数据库

```bash
# alembic 配置已在项目根 alembic.ini，DSN 自动从 config.json 读取
alembic upgrade head
```

### 5. 启动后端

```bash
# 必须从 backend/ 目录启动，否则相对 import 失败
cd backend
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ../.venv312/Scripts/python.exe -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

任务 ID 含中文 + Windows 控制台时，**必须**带 `PYTHONIOENCODING=utf-8 PYTHONUTF8=1` 否则 print 会因 GBK 报 UnicodeEncodeError。

### 6. 启动前端

```bash
cd frontend
npm install
npm run dev
```

打开 http://localhost:5173/

---

## API 文档

启动后端后访问 http://localhost:8000/docs 查看 Swagger 自动生成的 43 个路由接口文档（全部带中文 docstring）。

主要路由分组：

```
/api/upload, /api/result/{id}                       — A 流水线（证件解析）
/api/clients, /api/clients/{id}, .../family, .../assets, .../info, .../fills
/api/clients/match                                  — 智能客户匹配
/api/family/{id}, /api/assets/{id}                  — 子表 RESTful
/api/templates, /api/templates/{id}/...             — B 流水线（Word 模板填充）
/api/split, /api/split/{id}, /api/split/history     — C 流水线（PDF 拆分）
/api/file-summary, /api/summaries                   — D 流水线（URL 文件解析）
/api/field-router/doc-types                         — 字段路由元数据
```

---

## 项目结构

```
20260527/
├── backend/
│   ├── main.py                      FastAPI 入口 + 43 个路由
│   ├── llm_service.py               LLM 调用封装（_call_llm + 各业务 prompt）
│   ├── ocr_service.py               PDF/图片 OCR 统一入口
│   ├── template_service.py          Word 模板解析、anchor 扫描、渲染
│   ├── split_service.py             PDF 按页拆分
│   ├── split_ocr_service.py         拆分专用全页 OCR
│   ├── file_fetcher.py              URL 下载（通用）
│   ├── text_extractor.py            统一文字抽取（PDF/图片/docx 三路分发）
│   └── db/
│       ├── models.py                ORM 模型
│       ├── engine.py                async engine + session maker
│       ├── crud.py                  clients + documents + client_info CRUD
│       ├── family_crud.py / assets_crud.py / template_crud.py / split_crud.py / summary_crud.py
│       └── field_router.py          OCR 字段名 → 表/列 路由表
├── frontend/
│   ├── src/
│   │   ├── App.vue                  viewMode 状态机（home/parse/template/split/summary）
│   │   ├── api.js                   axios 封装
│   │   └── components/
│   │       ├── HomePage.vue                     首页 4 张卡
│   │       ├── ParseEntryPage.vue + ArchiveReviewPanel.vue + ...   AI 识别归档
│   │       ├── ClientListPage.vue + ClientDetailPage.vue + 4 个 Tab 子组件
│   │       ├── FillEntryPage.vue + TemplateListPage.vue + TemplateFillPage.vue + ...
│   │       ├── SplitEntryPage.vue
│   │       └── SummaryEntryPage.vue
│   └── package.json
├── migrations/versions/             alembic 迁移（001~010）
├── tests/                           单元测试 + 冒烟脚本
├── config.json.example              配置模板（克隆后复制为 config.json 并填值）
├── alembic.ini
├── backend/requirements.txt         后端 Python 依赖清单
├── CLAUDE.md                        给 Claude Code 的项目说明
└── README.md
```

---

## 关键设计决策

- **客户档案是地基**：A/B/C/D 四条流水线都围绕 clients 表运作。AI 识别归档时通过 `field_router` 精准路由到主表/子表，不再让 LLM 现场猜
- **混合 schema**：高频筛选字段强 schema（clients 主表 ~30 列），低频/可变字段走 `client_info` KV 兜底
- **B 流水线运行时 0 LLM 调用**：模板填充时直接从客户档案查值，LLM 只在保存模板那一刻识别 anchor 字段含义
- **B1 字段锁定**：身份证号/护照号等核心字段在模板填写界面 readonly，必须回客户档案统一编辑（防误改）
- **C 流水线持久化**：拆分任务状态写 DB，进程重启后历史可恢复；7 天 TTL 自动清理磁盘文件，DB 记录保留并置 `files_cleaned=true`
- **D 流水线相关性判断**：用户输入"进展名称" + 文件 URL，LLM 同时输出摘要 + 该文件是否属于该进展（强/弱/不相关三档 + 0-100 评分 + 理由），合并 prompt 节省 LLM 成本

---

## 常见问题

**Q: 后端启动报 `import` 错误**
必须从 `backend/` 目录启动 uvicorn，相对 import 才能找到模块。

**Q: 中文 task_id 在 print 时崩溃**
Windows 控制台默认 GBK，启动后端时**必须**加 `PYTHONIOENCODING=utf-8 PYTHONUTF8=1`。

**Q: docx2pdf 转 PDF 失败**
`docx2pdf` 强依赖 Windows + 已安装 Word。无 Word 环境时会自动降级返回 docx 文件（响应头 `X-Fallback-Docx: 1`）。

**Q: LibreOffice 未安装时 Word 原貌预览空白**
模板预览页的 PNG 渲染走 `soffice → PDF → pypdfium2 PNG`。LibreOffice 缺失时 `pages=[]`，前端自动降级到 mammoth HTML 视图，这是预期行为。

**Q: PaddleOCR 模型首次加载慢**
PP-OCRv4 模型文件 ~150 MB，首次启动会从 ModelScope 下载到 `~/.paddlex/`。冷启动等 30-60 秒是正常的。

---

## 许可

内部使用。
