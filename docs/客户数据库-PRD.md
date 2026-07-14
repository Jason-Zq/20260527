# 客户数据库 · 产品需求文档（PRD）

> **文档性质**：产品需求文档（PRD）/ 战略规划
> **状态**：草案 v0.2（已纳入三项业务决策，见 §12.3）
> **日期**：2026-07-14
> **关联文档**：[01-系统概览.md](01-系统概览.md)、[03-数据库设计.md](03-数据库设计.md)、[05-业务服务与队列.md](05-业务服务与队列.md)、仓库根 [CLAUDE.md](../CLAUDE.md)
> **适用范围**：在现有「智能文档审核工作台」（FastAPI + Postgres + RapidOCR + LLM + DB 队列 worker）基础上，把无序、杂乱的客户文件数据沉淀为可信赖的客户数据库，为后续数字化、智能化打底。

---

## 0. 一句话目标

把分散在云盘、留底检测、材料解析、URL 摘要四处的客户文件，**统一登记、去重分类、关联到唯一客户、结构化抽取为带溯源的事实**，并在冲突处引入人工复核，最终形成一份「可信、可查、可演进」的客户 360 数据库——作为后续所有数字化/智能化应用的地基。

---

## 1. 背景与问题陈述

### 1.1 现状（我们手里有什么）

我们是一家移民公司，云盘里存放着大量客户文件（PDF / Word / 图片）。当前系统已经具备：

- **OCR + 文本抽取能力**：[text_extractor.py](backend/text_extractor.py) + [ocr_service.py](backend/ocr_service.py)（RapidOCR），文件留底检测已大规模跑通。
- **留底检测/业务审核**：[archive_detect_service.py](backend/archive_detect_service.py) + [worker_runner.py](backend/worker_runner.py)，DB 队列 + 多进程 worker，`archive_detect_files` 表已沉淀大量**脱敏后的 `ocr_text`** + `doc_category` + `progress_id` + `file_id`。
- **材料解析**：`documents` 表存 `ocr_text` + `extracted_fields`（JSONB）。
- **URL 文件摘要**：`summaries` 表存 `extracted_text` + `doc_category`。
- **客户档案强 schema**：`clients`（~33 字段）/ `family_members` / `assets` / `client_info`（KV 兜底）。
- **客户档案结构化生成**：[client_profile_service.py](backend/client_profile_service.py)，从 `archive_detect_files.ocr_text` 抽取事实写入强 schema，策略「只补空不覆盖」。

### 1.2 问题（为什么还不算「客户数据库」）

| # | 问题 | 表现 | 根因 |
|---|------|------|------|
| 1 | **文件无统一台账** | 同一份文件可能同时出现在 `archive_detect_files` / `documents` / `summaries` / 云盘未处理区，谁也说不清「我们到底有多少文件、覆盖哪些客户」 | 缺少跨来源的统一文件登记表 |
| 2 | **不去重** | `archive_detect_files.content_sha256` 列已建但**当前不写值**；同一份身份证上传 N 次就有 N 条记录、N 次 OCR、N 次 LLM | 内容哈希未落地，无版本/去重簇 |
| 3 | **客户身份不收敛** | `client_code` 手填且可空；同一个人可能以「张三 / Zhang San / 张三（曾用名李四）」散落多处；`documents.client_id` 可空，孤儿文件无主 | 缺主数据管理（MDM）与实体解析 |
| 4 | **抽取是手动的、单客户的、一次性的** | 必须人去挑客户、勾文件、点生成；新文件进来不会自动入库 | 无批量/持续增量管线 |
| 5 | **事实无溯源、冲突被静默吞掉** | [apply_profile_facts](backend/db/client_profile_crud.py#L181)「先写先得」：两份文件给不同出生日期时，**保留先到的、丢掉后到的，且不留痕** | 强 schema 表无事实级 provenance，只有 `client_info` 有 `source_doc_id` |
| 6 | **无质量/复核层** | 抽取置信度不落库、冲突不上报、低置信数据无人工复核入口 | 缺数据质量治理 |
| 7 | **无客户 360 视图** | 档案/家庭/资产/进展/文件清单分散在多表，没有一份「这个客户的全貌」 | 缺统一视图层 |

> **核心论断**：我们不是没有数据，而是数据**散、重、脏、盲**。底层不清理干净，上层的「智能搜索 / 完整性校验 / 自动填表 / 风险预警」都是空中楼阁。本 PRD 的使命就是把这层地基打实。

---

## 2. 目标与非目标

### 2.1 目标（Success Criteria）

1. **看得清**：建立统一文件台账，能回答「我们有多少文件、什么类型、关联到哪些客户、还有多少孤儿」。
2. **不重复**：以内容哈希去重，同一文件全系统只保留一份文本与一次抽取。
3. **认得人**：每个客户有稳定主键（`client_code` 收敛），文件能可靠归户，孤儿文件可聚类认领。
4. **结构化**：所有 OCR 过的文件批量抽取为**带溯源的事实**（事实层），强 schema 表成为「当前最优值」物化视图。
5. **治得对**：冲突/低置信数据进入人工复核队列，**已人工确认的数据永不被 AI 覆盖**。
6. **活得起**：新文件入库即自动走完「登记→分类→抽取→入库」；抽取逻辑升级后可批量重算并版本化。
7. **用得上**：输出客户 360 视图，支撑完整性校验、到期提醒、智能搜索、自动填表等下游应用。

### 2.2 非目标（Out of Scope）

- **不重写现有架构**：复用 Postgres + DB 队列 worker + RapidOCR + LLM 调用封装。本 PRD 是**增量演进**，不是推倒重来。
- **不存原始未脱敏文本**：沿用现行脱敏口径（[redactor.py](backend/redactor.py)），事实层与台账均存脱敏后文本，PII 处理见 §10。
- **不做新前端框架**：前端复用 Vue 3 + Element Plus，仅新增管理页面。
- **不替代业务方系统**：客户主数据的权威来源仍是业务方系统（云盘/CRM），我们是**只读消费 + 本地结构化沉淀**，不回写业务方。
- **本 PRD 不含最终 SQL DDL / 代码**：只定义数据模型方向与字段语义，落地时另出迁移脚本。

---

## 3. 设计原则（贯穿全方案）

| 原则 | 含义 | 落地点 |
|------|------|--------|
| **溯源优先（Provenance-first）** | 每个事实必须能回答「来自哪个文件、何时抽取、置信度多少、原文片段是什么」 | 事实层 `client_facts` 表 |
| **人工数据主权** | 已人工确认的字段永不被 AI 覆盖（升级现有「只补空」为「不覆盖已确认」） | 事实状态机 + 强 schema 刷新逻辑 |
| **幂等与增量** | 同一文件重跑不产生重复事实；新文件自动入流；逻辑升级可版本化重算 | `content_sha256` + 抽取任务幂等键 |
| **冲突显式化** | 两份证据打架时**上报**而非静默丢弃 | 冲突检测 + 复核队列 |
| **复用现有基建** | OCR/脱敏/worker 队列/LLM 封装/留底检测的 `(progress_id,file_id)` 复用模式全部沿用 | 见 §8 |
| **最小留存** | 脱敏后文本才入库；原始文件不长期落本地盘（沿用 `file_fetcher` 临时文件清理） | 台账与事实层 |
| **可演进** | 抽取 schema、分类体系、匹配规则都可迭代，迭代有版本、可回滚 | 事实 `extraction_version` + 分类体系版本 |

---

## 4. 现状盘点与数据资产梳理（Phase 0 输入）

> 落地第一步是先跑一次审计脚本，把下表填实。这里给出盘点维度与预期来源。

| 资产 | 来源表 | 文本字段 | 客户关联 | 体量（待盘） | 备注 |
|------|--------|----------|----------|--------------|------|
| 留底检测文件 | `archive_detect_files` | `ocr_text`（脱敏） | 经 `progress_id → archive_detect_progress.client_id` | ? | 最丰富的文本源；`content_sha256` 未写 |
| 材料解析记录 | `documents` | `ocr_text` + `extracted_fields` | `client_id`（可空！） | ? | 老流水线，`reviewed` 标记人工复核 |
| URL 文件摘要 | `summaries` | `extracted_text` | **无客户关联** | ? | 独立于档案体系 |
| 云盘未处理文件 | 业务方文件服务 | 无 | 无 | ? | 完全在系统外，需主动拉取登记 |
| 客户主档 | `clients` | — | 自身 | ? | `client_code` 空值率待盘 |
| 家庭成员 | `family_members` | — | `client_id` | ? | |
| 资产 | `assets` | — | `client_id` | ? | |
| KV 兜底 | `client_info` | — | `client_id` + `source_doc_id` | ? | 唯一有溯源雏形的表 |

**Phase 0 交付物**：一份「数据资产盘点报告」，包含各表行数、`client_code`/`id_number`/`passport_no` 的空值率与重复率、`ocr_text` 覆盖率、跨表文件重复估算（按文件名+大小粗估）、孤儿文件估算。**这是后续所有决策的依据，跳过这一步等于盲打。**

---

## 5. 总体方案（七大支柱）

```text
┌─────────────────────────────────────────────────────────────────────┐
│                  客户 360 视图（支柱七，下游应用消费）                  │
└──────────────────────────────────▲──────────────────────────────────┘
                                   │ 物化/聚合
┌──────────────────────────────────┴──────────────────────────────────┐
│   强 schema 当前值：clients / family_members / assets（支柱五产出）    │
└──────────────────────────────────▲──────────────────────────────────┘
                                   │ 刷新（取最高置信/已确认）
┌──────────────────────────────────┴──────────────────────────────────┐
│            事实层 client_facts（支柱五，带溯源、冲突、状态机）          │
└──────────────────────────────────▲──────────────────────────────────┘
                                   │ 批量/增量抽取（LLM）
┌──────────────────────────────────┴──────────────────────────────────┐
│        统一文件台账 file_registry（支柱一，去重/分类/归户）             │
└───────▲───────────────────────────────────────▲─────────────────────┘
        │ 回填                                   │ 新文件入流
┌───────┴──────────┐  ┌──────────────┐  ┌───────┴──────────┐
│ archive_detect_   │  │ documents    │  │ 云盘文件（未处理） │
│ files（留底检测）  │  │ summaries    │  │ → 登记 → OCR      │
└───────────────────┘  └──────────────┘  └──────────────────┘
        │
        │ 实体解析（支柱二：client_code 收敛 + 模糊匹配归户 + 孤儿聚类）
        ▼
                    clients（客户主数据 MDM）
```

七大支柱：

1. **统一文件台账**（file_registry）—— 跨来源的单一文件清单 + 去重簇 + 统一分类。
2. **客户主数据与实体解析**（MDM）—— 稳定客户主键 + 模糊归户 + 孤儿认领 + 客户合并。
3. **文件分类与去重** —— 统一证件类型分类法 + `content_sha256` 去重 + 版本簇。
4. **批量结构化抽取** —— 把手动单客户抽取升级为全量/增量批量管线。
5. **事实层与冲突治理** —— 带溯源的事实表 + 冲突检测 + 人工复核队列 + 强 schema 刷新。
6. **持续增量与重算** —— 新文件自动入流；逻辑升级版本化重算。
7. **客户 360 与智能化应用** —— 统一视图 + 完整性校验 + 到期提醒 + 智能搜索 + 自动填表。

---

## 6. 核心数据模型演进

> 原则：**不破坏现有表**，新增表为叠加层；现有 `archive_detect_files` / `documents` / `summaries` 作为「来源系统」回填进台账。

### 6.1 新增：统一文件台账 `file_registry`

跨来源的单一文件真相源。**不复制 `ocr_text`**，用 `source_ref` 指回原表，避免双份大文本。

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | int PK | |
| `content_sha256` | string(64) | **入流时强制计算写入**（补 `archive_detect_files` 当前的空缺）；去重主键 |
| `source_system` | string | `cloud` / `archive_detect` / `document` / `summary` |
| `source_ref` | string | 来源表内的稳定标识（如 `archive_detect_files.id` / `documents.id` / 云盘 file_id） |
| `filename` | string | 权威可读名（沿用留底检测「业务方传名优先」口径） |
| `mime_type` / `page_count` / `char_count` | | 元数据 |
| `doc_type` | string | **统一分类法**（见 §6.4），归一现有 `doc_category`/`doc_type` |
| `client_id` | FK→clients, nullable | **解析后归户**；孤儿为空 |
| `client_match_status` | string | `matched` / `orphan` / `ambiguous` / `manual` |
| `dedup_cluster_id` | string | 按 `content_sha256` 分簇；同簇取 `is_latest_version=true` |
| `is_latest_version` | bool | 簇内最新版（旧版保留可追溯） |
| `ocr_status` | string | `pending_ocr` / `ocr_done` / `no_text` / `error` |
| `extraction_status` | string | `pending` / `extracted` / `skipped` / `error` |
| `extraction_version` | int | 抽取逻辑版本号 |
| `created_at` / `updated_at` | | |

索引：`content_sha256`（去重）、`(client_id, doc_type)`、`client_match_status`、`dedup_cluster_id`。

### 6.2 演进：客户主数据 `clients`（MDM）

> **业务决策**：`client_code` 由业务方系统权威提供（稳定编码），作为客户唯一身份主键与合并依据。云盘文件可经清单 API 拉取，业务方提交时即带 `client_code`。

- `client_code` 从「手填可空」收敛为**业务方权威主键**：来源为业务方系统编码，补唯一非空约束。仅对极少数无业务方编码的历史脏数据保留本地兜底生成（标记 `code_source=local_fallback` 以便后续清洗）。
- **合并规则=同编号即合并**：两条 client 行若 `client_code` 相同，自动合并为一条（无需人工裁决）。合并前做一致性校验：若 `id_number`/姓名明显冲突，判定为业务方源头数据质量问题，**挂起不合并并上报**，转人工查源头。
- 新增 `match_signals` JSONB：归户用过的匹配信号快照（`client_code` / `id_number` / `passport_no` / `name_variants`），供审计。
- 新增 `merge_status` / `merged_into_client_id`：记录合并历史，被合并方逻辑指向存续方。
- `id_number` / `passport_no` 仍为强唯一约束，作为无 `client_code` 记录归户的辅助硬锚点。

### 6.3 新增：事实层 `client_facts`（本 PRD 的心脏）

append-only 的事实表，每条 = 「某文件说某客户的某字段是某值」。强 schema 表退化为「当前最优值的物化视图」，由事实层刷新。

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | bigint PK | |
| `client_id` | FK→clients | |
| `entity_type` | string | `client` / `family_member` / `asset` |
| `entity_ref` | string | 目标实体标识（client 字段名 / family_member 业务键 / asset 业务键） |
| `field_name` | string | 字段名（与强 schema 对齐，如 `birth_date` / `passport_no`） |
| `value` | jsonb | 值（支持字符串/数字/日期，统一 jsonb 便于异构） |
| `value_type` | string | `str` / `date` / `decimal` / `bool` |
| `source_file_id` | FK→file_registry | **溯源锚点** |
| `source_doc_type` | string | 来源文件类型 |
| `raw_snippet` | text | 支持该事实的原文片段（脱敏后），便于复核 |
| `confidence` | int | 0-100，LLM 自评 + 规则修正 |
| `extraction_task_id` | FK | 抽取任务 |
| `extraction_version` | int | 抽取逻辑版本 |
| `extracted_at` | datetime | |
| `status` | string | `candidate` / `confirmed` / `rejected` / `superseded` |
| `confirmed_by` / `confirmed_at` | | 人工确认留痕 |
| `conflict_group_id` | string nullable | 冲突分组：同 `(client,entity,field)` 不同值归一组 |

**关键规则**：
- 同一 `(source_file_id, entity_type, entity_ref, field_name, extraction_version)` 幂等：重跑不产生重复事实。
- 强 schema 刷新：对每个 `(client, entity, field)`，取 `status=confirmed` 优先，否则取最高 `confidence` 的 `candidate`；`rejected`/`superseded` 不参与。
- **人工确认永不被覆盖**：一旦 `status=confirmed`，后续抽取只能新增 `candidate` 并触发冲突复核，不能直接改写。
- `client_info`（KV 兜底）的 `source_doc_id` + `confirmed` 模型是事实层的雏形；事实层是其在强 schema 上的泛化。`client_info` 可保留作「未纳入强 schema 的临时 KV」，逐步迁入事实层。

### 6.4 统一文件分类法（doc_type）

现有 `doc_category`（留底体系）与 `doc_type`（材料解析）口径不一。定义**统一证件/文件类型枚举**（示例，需业务确认）：

```
身份证 | 护照 | 户口本 | 结婚证 | 离婚证 | 出生医学证明 | 亲属关系公证 |
学历证书 | 学位证书 | 成绩单 | 工作证明 | 在职证明 | 营业执照 |
房产证 | 购房合同 | 银行存款证明 | 银行流水 | 理财证明 | 股票对账单 |
车辆登记证 | 社保记录 | 个税记录 | 无犯罪证明 | 体检报告 |
服务合同 | 委托书 POA | 递交确认 | 受理回执 | 批复函 | 其他
```

- 留底检测的 `ARCHIVE_CATEGORIES_FULL/SIMPLE`（服务启动证据视角）与本文档的 `doc_type`（证件类型视角）是**正交**的两个维度：一个文件既有 `doc_type=银行流水`，又有留底 `verdict=match`。两者都保留，互不替代。
- 分类由 LLM 在登记/抽取阶段输出，落 `file_registry.doc_type`。

---

## 7. 分阶段实施路线图

> 每阶段都有**可验证的退出标准**，不达不上下一阶段。生产环境 4C/8G 单 worker，节奏求稳。

### Phase 0 · 现状盘点与数据审计（1-2 周）
- 接入**云盘清单 API**，拉取全量文件清单，与本地三表（`archive_detect_files`/`documents`/`summaries`）对账，得出「云盘有、本地未处理」的文件体量。
- 跑审计脚本，填实 §4 盘点表：行数、空值率、重复率、OCR 覆盖率、`client_code` 覆盖率、孤儿估算。
- 产出《数据资产盘点报告》。
- **退出标准**：能回答「云盘共多少文件、本地已 OCR 多少、覆盖多少客户、多少孤儿、`client_code` 覆盖率多少」。

### Phase 1 · 统一文件台账 + 客户主数据骨架（2-3 周）
- 建 `file_registry` 表 + 迁移。
- 回填：把 `archive_detect_files` / `documents` / `summaries` 的历史文件登记进台账，**计算并写入 `content_sha256`**（这一步直接补掉当前的空缺）。
- `client_code` 收敛：从业务方系统拉取/对齐编码，补唯一非空约束；仅极少数历史脏数据本地兜底生成。
- **退出标准**：台账行数 = 三表去重后文件数；`content_sha256` 非空率 100%；`client_code` 非空唯一率 ≥ 99%（剩余为无业务方编码的孤儿）。

### Phase 2 · 文件分类与去重（2 周）
- LLM 批量给台账文件打 `doc_type`（复用已 OCR 的文本，不重跑 OCR）。
- 按 `content_sha256` 建 `dedup_cluster_id` + `is_latest_version`。
- **退出标准**：去重簇报告产出；`doc_type` 覆盖率 ≥ 95%（剩余 `no_text` 除外）。

### Phase 3 · 客户实体解析与归户（1-2 周，因业务方提供 client_code 而简化）
- **主路径**：业务方提交带 `client_code`，直接归户，零匹配误差。
- **无 `client_code` 记录的归户**（URL 摘要、老 documents 等）：用 `id_number`/`passport_no`/`phone` 硬匹配 + 姓名（拼音/英文/曾用名）模糊匹配（**复用留底检测 criteria 的拼音/英文转写匹配逻辑**）提议归属；模糊匹配只产出**提案**，歧义者转人工。
- **合并**：同 `client_code` 即自动合并（§6.2），合并前一致性校验，冲突挂起上报。
- **退出标准**：`client_match_status=orphan` 占比下降到目标阈值；合并冲突上报数可度量；无人工合并积压。

### Phase 4 · 事实层 + 批量结构化抽取（3-4 周，核心）
- 建 `client_facts` 表 + 迁移。
- 把 [extract_client_profile_facts](backend/llm_service.py#L1124) 的输出从「直接写强 schema」改为「写事实层」；扩展抽取 schema（现有 9 个 `client_basic` 字段 + family + assets 是起点，按业务补充教育/工作/时间线）。
- 批量抽取管线：复用 DB 队列 + worker 架构（见 §8），全量跑一遍已 OCR 文件。
- 强 schema 刷新逻辑：从事实层物化 `clients`/`family_members`/`assets`。
- **退出标准**：事实层覆盖所有已 OCR 文件；强 schema 字段填充率较 Phase 0 显著提升；每条非空字段可溯源到文件。

### Phase 5 · 冲突治理与人工复核（2-3 周）
- 冲突检测：同 `(client,entity,field)` 多值 → 建 `conflict_group_id`。
- 复核队列前端页面（Vue 3）：展示冲突双方、原文片段、置信度，人工裁决。
- 低置信 `candidate` 自动进队列。
- **退出标准**：冲突队列可清理性度量；已确认事实占比上升；`confirmed` 字段不被任何自动流程覆盖（回归测试守卫）。

### Phase 6 · 持续增量与重算（2-3 周）
- **云盘增量同步**：定时经清单 API 拉取云盘文件清单，与台账对账，新文件自动入流：登记 -> OCR -> 分类 -> 抽取 -> 入事实 -> 刷新强 schema（全链路自动，复用 worker）。
- 留底检测产出的文件同样自动回流台账（复用 `(progress_id,file_id)` 增量模式）。
- 重算：抽取逻辑升级（`extraction_version` +1）-> 批量重抽 -> 旧事实 `superseded`，新事实 `candidate`，触发冲突复核。
- **退出标准**：新文件从云盘入库到入事实的端到端自动化；重算不丢已确认事实。

### Phase 7 · 客户 360 与智能化应用（持续）
- 客户 360 视图页面：档案 + 家庭 + 资产 + 进展时间线 + 文件清单（去重后）+ 事实溯源。
- 下游应用（见 §9）按需排期。

---

## 8. 与现有系统的关系（复用 vs 新增）

| 现有能力 | 本方案如何用 | 改动 |
|----------|--------------|------|
| [worker_runner.py](backend/worker_runner.py) DB 队列（SKIP LOCKED） | **直接复用**为抽取/分类的执行引擎，新增 `operation` 类型 | 扩展任务类型，不动队列核心 |
| [text_extractor.py](backend/text_extractor.py) + [ocr_service.py](backend/ocr_service.py) | 云盘新文件入流时复用 | 无 |
| **云盘清单 API**（业务方文件服务，与 `getFileDownloadUrl` 同族） | Phase 0 全量盘点 + Phase 6 定时增量拉取文件清单 | **新增接入**（复用 `file_fetcher` 身份参数 `usr_login/operation_user/url`） |
| [redactor.py](backend/redactor.py) 脱敏 | 事实层 `raw_snippet` 与台账文本均走脱敏 | 无 |
| [llm_service.py](backend/llm_service.py) `_call_llm` + `ai_api_calls` 埋点 | 抽取/分类 LLM 调用全部走封装，自动留痕 | 新增 prompt |
| `archive_detect_files.ocr_text` | 台账 `source_ref` 指回，**不复制大文本** | 无 |
| `archive_detect_files.content_sha256` | 台盘写入哈希，反向回填该列（消除已知空缺） | 写值 |
| `(progress_id, file_id)` 增量复用模式 | 抽取幂等键的范本 | 借鉴 |
| [client_profile_service.py](backend/client_profile_service.py) | 升级为事实层写入；手动单客户入口保留作「即时补抽」 | 改写入目标 |
| 留底检测 criteria 拼音/英文姓名匹配 | **复用**到实体解析归户 | 抽取为共享工具 |
| `client_info`（KV + `source_doc_id` + `confirmed`） | 事实层的雏形；逐步纳入事实层 | 渐进迁移 |
| systemd worker 模板 `doc-review-worker@.service` | 多 worker 扩并发用 | 无 |

> **架构延续性**：本方案完全在「DB 队列 + 多进程 worker + uvicorn 单进程」的现有约束内运行，不引入新中间件。即便后续后端按 [07-重构规划.md](07-重构规划.md) 迁到 .NET，表结构与事实层模型可平移（EF Core / Npgsql），逻辑不绑死 Python。

---

## 9. 智能化应用（Phase 7+，地基打好后的收益示例）

| 应用 | 依赖 | 价值 |
|------|------|------|
| **材料完整性校验** | 台账 `doc_type` + 客户 `visa_type` | 按业务类型自动列出「该客户还缺哪些证件」，驱动补件 |
| **证件到期提醒** | 事实层 `passport_expiry_date` 等 + `valid_until` | 自动预警护照/身份证将到期客户（现有 `client_info.valid_until` 已埋点） |
| **智能搜索** | 台账 + 事实层全文 | 「名下有 2 套上海房产且子女未成年的客户」这类条件检索 |
| **自动填表** | 强 schema + [template_service.py](backend/template_service.py) | Word 模板填值从「手动选客户」升级为「数据齐全自动填 + 缺项提示」 |
| **销售线索** | 事实层 family/asset | 扩展现有子女年龄线索（[sales_crud.py](backend/db/sales_crud.py)）到资产/家庭事件线索 |
| **风险与合规** | 事实层冲突历史 + 留底 verdict | 同一客户多份证据打架的历史可追溯，辅助合规审查 |
| **进展时间线** | `archive_detect_progress` + 事实层 | 客户办理进展与文件留底的可视化时间线 |

---

## 10. 风险与约束

| 风险 | 等级 | 缓解 |
|------|------|------|
| **LLM 成本与吞吐**：全量批量抽取 + 重算会放大 LLM 调用 | 高 | 复用 `content_sha256` 去重（同文件只抽一次）；增量优先；重算按 `extraction_version` 分批；生产单 worker 串行求稳 |
| **PII / 隐私**：事实层集中了客户敏感事实 | 高 | 全链路脱敏（redactor）；事实层 `raw_snippet` 仅存脱敏片段；访问鉴权；与 `ai_api_calls` 一样按需审计 |
| **误归户**：把 A 的文件归到 B | 中 | 业务方 `client_code` 为硬锚点（权威主键），主路径零匹配误差；仅无编码记录走模糊匹配提案，歧义转人工；保留 `client_match_status` 可回溯 |
| **业务方源头 `client_code` 质量问题**（同号不同人 / 编码变更） | 中 | 合并前一致性校验（`id_number`/姓名冲突即挂起上报），不盲合并；编码变更走 `merge_status` 留痕 |
| **人工确认被覆盖** | 高 | 事实状态机 + 回归测试守卫；`confirmed` 永不被自动流程改写（§6.3） |
| **数据迁移破坏现有** | 中 | 新增表为叠加层，不改现有表结构（仅补 `content_sha256` 写值、`client_code` 约束）；每阶段可回滚 |
| **.NET 重写协同** | 中 | 模型与逻辑不绑 Python；表结构平移友好（§8） |
| **孤儿文件长期无主** | 中 | 孤儿聚类 + 定期认领流程；不强行归户 |
| **抽取 schema 漂移** | 中 | `extraction_version` 版本化；schema 变更走版本号而非原地改 |

---

## 11. 成功指标（KPI）

| 维度 | 指标 | 目标（示例，待业务校准） |
|------|------|--------------------------|
| 看得清 | 台账覆盖率（已 OCR 文件 / 全部文件） | ≥ 95% |
| 看得清 | 孤儿文件占比 | < 5% |
| 不重复 | 去重后文件数 / 去重前 | 度量重复率下降 |
| 认得人 | `client_code` 非空唯一率 | 100% |
| 认得人 | 误归户率（抽样人工校验） | < 1% |
| 结构化 | 强 schema 字段平均填充率 | 较 Phase 0 提升 ≥ 30pt |
| 溯源 | 非空字段可溯源率（有 `source_file_id`） | 100% |
| 治得对 | 冲突队列周清率 | ≥ 80% |
| 治得对 | 已确认事实被覆盖事件数 | 0（硬指标） |
| 活得起 | 新文件入库到入事实的 P50 时延 | < 1 小时（worker 串行下） |

---

## 12. 附录

### 12.1 抽取事实 schema（起点，Phase 4 细化）

沿用 [extract_client_profile_facts](backend/llm_service.py#L1124) 现有结构并扩展：

```json
{
  "client_basic": { "name_en": "", "gender": "", "birth_date": "", "birth_place": "",
                    "nationality": "", "id_number": "", "passport_no": "",
                    "passport_expiry_date": "", "marital_status": "" },
  "family_members": [ { "relation": "", "name": "", "birth_date": "", "id_number": "",
                        "passport_no": "", "birth_cert_no": "" } ],
  "assets": [ { "asset_type": "", "asset_name": "", "value_amount": null, "currency": "",
                "bank_name": "", "account_no": "", "location_address": "", "certificate_no": "" } ],
  "extra_info": [ { "key": "", "value": "" } ],
  "confidence_notes": [ "..." ]
}
```

Phase 4 扩展方向：教育（学校/专业/学位/毕业日期）、工作（公司/职位/入职/月薪）、时间线事件（递交/受理/批复日期）、每事实的 `raw_snippet` 与 `confidence`。

### 12.2 术语表

- **留底**：证明某进展对应服务确已启动/发生的留档文件（官方件或软证据组合）。
- **实体解析（Entity Resolution）**：判定「散落多处的记录是否指向同一个真实客户」的过程。
- **MDM（Master Data Management）**：客户主数据管理，保证一个客户在全系统有唯一、稳定、权威的主键与档案。
- **Provenance（溯源）**：一条数据「来自哪个文件、何时被抽取、置信度多少」的完整链路。
- **事实层（Facts Layer）**：append-only 的事实表，与强 schema「当前值」分离，支持冲突与重算。
- **孤儿文件**：无法关联到任何已知客户的文件。

### 12.3 决策记录（已确认）

1. **`client_code` 权威来源**：业务方系统提供稳定编码，作为客户唯一身份主键与合并依据。仅极少数无编码历史脏数据本地兜底生成。
2. **云盘未处理文件**：有清单 API 可拉取。Phase 0 用于全量盘点，Phase 6 用于定时增量同步（与 `file_fetcher.refresh_download_url` 同业务方系统，复用身份参数）。
3. **客户合并裁决**：同 `client_code` 即自动合并，无需人工裁决；合并前一致性校验，冲突挂起上报查源头。

### 12.4 剩余开放问题

1. 统一文件分类法（§6.4）的最终枚举需业务方拍板。
2. 事实保留策略：`rejected`/`superseded` 事实保留多久（合规审计 vs 存储）？
3. 云盘清单 API 的调用频率/配额/鉴权方式（与 `getFileDownloadUrl` 是否一致）需对接确认。

---

> **下一步**：三项业务决策已纳入；确认 §12.4 剩余开放问题 -> 启动 Phase 0 盘点（含云盘清单 API 全量对账）。
