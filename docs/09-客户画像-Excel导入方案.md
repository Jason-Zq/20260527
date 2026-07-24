# 客户画像 · Excel 导入 → 全量 OCR → 精准筛 4 类证件 → 规则提取 → 客户档案 实施方案

> 2026-07-23 定稿。**状态：已实现并端到端验证通过**（migration 018；真实 Excel 62 文件导入，重复导入幂等验证 0 重复写入）。
> 取代 [08-客户基本信息提取方案.md](08-客户基本信息提取方案.md) 中的**触发方式**（worker hook / 回填端点）；08 的**规则子系统**（doc_extract_rules、AI 起草→人工审核→激活、提取/归因/只补空写库）原样保留并并入本方案。

## 1. 背景与目标

用户描述的权威流程：

1. **获取客户名下所有文件**：现阶段上传 Excel 清单（格式 = 根目录 `客户文件信息例子.xlsx`）；后期业务方提供查询接口（文件来源抽象一层，Excel 是第一个实现）。
2. 后端识别**主客户姓名**（客户姓名列众数），提取全部**文件编码**。
3. 按文件编码先查 `archive_detect_files` 是否已有 OCR（status='done' 且 ocr_text 非空，取最新）→ 有则直接复用（注意：那是**脱敏**文本）。
4. 没有则调已有接口下载：`file_fetcher.refresh_download_url(file_id)` 取临时地址（已核实：只需 file_id，身份参数走 `config.json.file_url_service`，自动记 external_api_logs）→ `fetch_url_to_temp` 下载 → `text_extractor.extract_text` 抽取/OCR。
5. **每个文件的 OCR 都存进"客户文件库"**（用户决策：fresh 存**未脱敏原文**，与 ai_api_calls 存原文的既定业务决策一致；reused 存脱敏文本，`ocr_source` 列区分）——后续提取别的数据不用重新下载/OCR。
6. 从几十个文件里**精准筛出 4 类**（身份证/户口本/学位证/出生证明）：三层分类（文件夹/文件名线索 + OCR 关键词评分纯函数 → 置信不足才 LLM 兜底），绝大多数文件零 LLM 成本。
7. 4 类按 DB 里的提取规则（AI 起草、人工审核激活）LLM 提取 → 归因（证件号/姓名匹配）→ **只补空不覆盖**写入 `clients`/`family_members` → 结果表留痕。
8. 每步记日志：LLM 走 `_call_llm` 自动记 ai_api_calls；阶段事件记 system_events；每文件状态落库。

**交付物**：前端上传 `客户文件信息例子.xlsx` → 后台自动跑 → 页面上看到**客户画像**（客户卡片 + 家庭成员 + 4 类证件提取明细 + 文件清单状态）。

## 2. 关键已核实事实

- Excel 实读：Sheet1，1 表头 + 62 数据行；列序 `售后文件夹名称|文件编码|客户姓名|文件啊名称|文件路径|相对路径`；**"文件啊名称" 是原文件错别列名，parser 必须兼容**；folder 多为"护照"/人名，filename 多为 `IMG_xxx.jpg` → 文件名不可靠，OCR 关键词是分类主力，folder/rel_path（如"身份证2026/倪朝晖"）作线索加分。
- `archive_detect_crud.find_latest_done_file` 按 progress_id scope，**不能用**；新写全局按 file_id 的复用查询（必须带 `.options(undefer(ArchiveDetectFile.ocr_text))`）。
- `text_extractor.extract_text` 本身是 async（内部 to_thread），service 直接 await。
- `redactor` 把 18 位身份证 → 字面量 `[身份证]`（另 `[手机号]/[银行卡]/[金额]/[座机]`）：复用文本提取证件号会得到占位词 → masked 判定（值含 `*` 或任一 `[…]` 占位词）→ 跳过写入记 `skipped_masked`，且该 id_number 不参与归因；姓名/性别/出生日期/民族/住址不脱敏可正常写。
- `family_members.relation` NOT NULL → 新建成员用哨兵 `'待确认'`；`clients` 无身份证签发机关/有效期列 → 规则里这两字段 `target.column=null` 只抽不写。
- `find_or_create_client(name, id_number)`（db/crud.py:168）：先证件号再姓名，查不到新建（client_code 留空）。
- `llm_service.ARCHIVE_DETECT_INPUT_LIMIT_CHARS = 30000`，提取 wrapper 复用。
- 后台任务先例：PDF 拆分 `_process_split_background` 在主进程 `asyncio.create_task` 串行跑全页 OCR —— 本方案同模式（本地/demo OK；生产 4C/8G 后续再搬 worker/.NET）。

## 3. 数据表（migration 018，`down_revision='4617b534a2d2'`，4 表同迁）

### 3.1 `profile_import_tasks`
| 列 | 类型 | 说明 |
|---|---|---|
| id | Integer PK | |
| filename | String(500) NN | 上传的 Excel 文件名 |
| client_name | String(100) NN | 主客户姓名（众数） |
| client_id | Integer FK→clients.id SET NULL | |
| status | String(20) NN default 'running' | running/done/error |
| total_files / processed_files | Integer NN default 0 | |
| reused_count / relinked_count / fresh_ocr_count / failed_count / extracted_count | Integer NN default 0 | reused=复用 archive_detect；relinked=命中本库已有 done 行 |
| id_card_count / hukou_count / degree_cert_count / birth_cert_count | Integer NN default 0 | 4 类各筛出数 |
| current_file | String(500) | 正在处理的文件名（进度展示） |
| error | Text | |
| created_at / updated_at | DateTime NN | |

索引：status、client_id、created_at DESC。

### 3.2 `customer_files`（客户文件库）
| 列 | 类型 | 说明 |
|---|---|---|
| id | BigInteger PK | |
| file_code | String(200) NN **unique** | 业务文件编码 |
| import_task_id | Integer FK→profile_import_tasks.id CASCADE NN | 最近一次导入它的任务 |
| client_name / client_id | | client_id FK→clients.id SET NULL |
| filename / folder_name / rel_path | String | Excel 行数据 |
| status | String(20) NN default 'pending' | pending/fetching/ocr/done/error |
| ocr_source | String(10) NN default 'none' | fresh(原文)/reused(脱敏)/none |
| ocr_text | Text | **fresh=未脱敏原文（与 ai_api_calls 存原文的既定决策一致，comment 声明）**；reused=脱敏文本 |
| mime_type / page_count / char_count | | |
| doc_type | String(32) | id_card/hukou/degree_cert/birth_cert/other |
| classify_by | String(10) NN default 'none' | keyword/llm/none |
| classify_score | Integer | matcher 分数或 LLM confidence |
| error_msg | Text | |
| created_at / updated_at | DateTime NN | |

索引：`ux_customer_files_file_code`(unique)、import_task_id、doc_type、client_id、status。

### 3.3 `doc_extract_rules`（与 08 §3.1 一致，无种子）
doc_type/version/status(draft|active|disabled)/fields JSONB `[{key,label,description,required,target:{entity:'person',column|null},example}]`/prompt_extra/drafted_by/reviewed_by/reviewed_at/timestamps。
索引：`ix_...(doc_type,status)` + **部分唯一** `ux_doc_extract_rules_active ON (doc_type) WHERE status='active'`。

### 3.4 `doc_extract_results`
customer_file_id FK→customer_files.id CASCADE NN / import_task_id FK CASCADE NN / file_id（业务编码） / client_id / doc_type / rule_id / rule_version / status(done|error|skipped) / skip_reason(no_active_rule|no_client|no_person) / extracted JSONB（未脱敏原始抽取） / mapped JSONB（逐字段 `[{key,column,entity,entity_id,action}]`，action ∈ written/skipped_filled/skipped_masked/skipped_invalid/unmapped） / write_stats JSONB / error_msg / elapsed_ms / created_at。
索引：import_task_id、customer_file_id、file_id、doc_type。

## 4. 后端改动（文件级）

### 4.1 `backend/db/models.py`
追加 `ProfileImportTask` / `CustomerFile` / `DocExtractRule` / `DocExtractResult`（放 AiApiCall 后；不加 relationship，跨表在 CRUD 层 join，与现有一致）。

### 4.2 `backend/db/customer_file_crud.py`（新，仿 ai_api_call_crud.py；`_clean_text` 从 ai_api_call_crud import 复用）
- `find_reusable_ocr(file_code)`：全局查 archive_detect_files 同 file_id 最新 done 且 ocr_text 非空行（带 `undefer(ocr_text)`、`deleted` 过滤、version/created_at 倒序）→ `{archive_file_id, ocr_text(脱敏), page_count, char_count, mime_type, filename}` | None
- 任务：`create_import_task` / `get_import_task` / `list_import_tasks(status, client_name, limit, offset)` / `update_task_progress(task_id, **fields)` / `finish_import_task(task_id, status, error)`
- 文件：`upsert_task_files(task_id, client_id, files)`（按 file_code 幂等：已有行 re-link + 非 done 重置 pending，done 保留计 relinked；新行 pending）→ `{new, relinked}` / `list_task_files`（不含 ocr_text） / `get_customer_file` / `update_file_ocr` / `update_file_classify` / `mark_file_error`

### 4.3 `backend/db/doc_extract_crud.py`（新）
- 规则：`create_rule`(version=max+1) / `get_rule` / `get_active_rule` / `list_rules` / `update_rule_draft`(非 draft 抛 ValueError→409) / `activate_rule`(单事务：同类型其他→disabled) / `disable_rule`
- 结果：`insert_result` / `list_results(import_task_id, customer_file_id, file_id, doc_type, status, limit, offset)` / `get_result`
- 纯函数：`MASKED_TOKENS=("[身份证]","[手机号]","[银行卡]","[金额]","[座机]")`、`is_masked(v)`、`valid_id_number(v)`（17 位数字+数字/X）
- 归因+写库（`from db.client_profile_crud import _parse_date, _clean_str`）：
  - `find_person_match(client_id, id_number, name)`：本客户 id_number → 成员 id_number → 本客户 name → 成员 name；**clients 查询限定 id==client_id**（只建"这个客户"的档案，不写别的客户行）→ `{entity:client|member|None, row_id, matched_by}`
  - `apply_extracted_fields(client_id, match, field_items)`：match None 且有 name → 新建 FamilyMember(relation='待确认')；column=None 或目标表无此列（member 无 hukou_address/ethnicity）→ unmapped；只补空；Date 列 `_parse_date` 失败→skipped_invalid → `{mapped, write_stats}`

### 4.4 `backend/doc_type_matcher.py`（新，纯函数零 LLM）
```python
DOC_TYPES = ("id_card","hukou","degree_cert","birth_cert")
ACCEPT_THRESHOLD = 60; TIE_MARGIN = 10; CLUE_BONUS = 20; OCR_HEAD_CHARS = 3000
_RULES = {
 "id_card":     {"strong":["中华人民共和国居民身份证","公民身份号码"],
                 "positive":["身份证","居民身份证","签发机关","有效期限"],
                 "negative":["户口簿","常住人口登记卡","学位证书","毕业证书","出生医学证明","结婚证","护照"]},
 "hukou":       {"strong":["居民户口簿","常住人口登记卡"],
                 "positive":["户口簿","户口","户主","户号","户籍","家庭户","集体户","户口登记机关"],
                 "negative":["居民身份证","公民身份号码","出生医学证明","结婚证","学位证书"]},
 "degree_cert": {"strong":["学位证书","学士学位","硕士学位","博士学位"],
                 "positive":["学位","授予","学位评定委员会","证书编号"],
                 "negative":["毕业证书","毕业证","准予毕业","结业","修完","成绩单"]},   # 毕业证≠学位证
 "birth_cert":  {"strong":["出生医学证明"],
                 "positive":["新生儿姓名","出生孕周","出生体重","出生身长","助产机构","母亲姓名","父亲姓名"],
                 "negative":["结婚证","婚姻登记","持证人","户口簿","死亡"]},              # 结婚证也含"出生日期"
}
def classify(folder_name, filename, ocr_head, rel_path=None) -> dict:
    # score(t) = clamp(40*strong_hits + 15*positive_hits + clue(命中该类型词+20,一次) - 25*negative_hits, 0, 100)
    # clue 文本 = folder_name+rel_path+filename 拼接; 最高分>=60 且领先第二名>=10 → {doc_type, score, by:'keyword', scores}
    # 否则 doc_type=None → LLM 兜底; ocr_head 空 → by:'none'
```
打分 sanity：身份证背面 70✓、户口本页 70✓、学位证 100/75✓、毕业证→0→None✓、结婚证→0→None✓、护照→5→None✓、IMG_xxx 无命中→None✓。

### 4.5 `backend/llm_service.py`（末尾追加，均走 `_call_llm` 自动记 ai_api_calls）
- `draft_extract_rule(doc_type, target_columns, **context)`：给中文类型名 + person 可写列清单（clients/family_members 列名+comment），产出 `{fields, prompt_extra}`；operation="draft_extract_rule"；失败抛（端点 502）
- `recognize_doc_type(text_head, **context)`：截 2000 字，白名单 4 类+other；`{doc_type, confidence, _fallback}`；max_retries=2；任何失败→other 不抛（宁漏不误，仿 detect_large_table_doc）
- `extract_doc_fields(text, rule, **context)`：30000 字 head+tail；prompt 由 rule.fields+prompt_extra 拼；`{fields:{key:value}}`；失败抛（service 按文件 catch）

### 4.6 `backend/profile_import_service.py`（新，主编排）
**文件来源抽象（后期业务方接口替换点）**：
```python
class ManifestFile(TypedDict): file_code: str; filename: str; folder_name: str|None; rel_path: str|None; client_name: str|None
class FileManifest(TypedDict): client_name: str; files: list[ManifestFile]
class FileSourceProvider(Protocol):
    async def fetch_manifest(self, source: dict) -> FileManifest: ...   # Excel 是第一个实现
def parse_excel_manifest(path: str) -> FileManifest  # 同步纯函数,可单测
```
**Excel parser 规格**：openpyxl `read_only=True, data_only=True` 第一个 sheet 首行表头；列别名（folder_name:[售后文件夹名称,文件夹名称]、file_code:[文件编码]、client_name:[客户姓名]、**filename:[文件名称,文件啊名称]**、rel_path:[相对路径]）；必需列 file_code/client_name/filename 缺→ValueError(具体缺列)→400；单元格规整（float integral→str(int)）；file_code 与 filename 皆空→跳过计 skipped_rows；file_code 重复→保留首次计 duplicates；主客户=Counter 众数（全空→ValueError）；零有效行→ValueError；返回 `{client_name, files, skipped_rows, duplicates}`。

**`async run_import(task_id)`**（主进程 create_task 串行；单文件异常不杀任务）：
1. `update_task_progress(current_file=...)`；行已 done 且有 ocr_text → relinked++ 直接进分类
2. **OCR**：status='fetching' → `find_reusable_ocr` 命中 → `update_file_ocr(ocr_source='reused', ocr_text=脱敏文本)`；未命中 → `refresh_download_url(file_code)` → `fetch_url_to_temp` → `try: await text_extractor.extract_text(path, mime) finally: cleanup_temp_file` → `update_file_ocr(ocr_source='fresh', ocr_text=原文)`
3. **分类**：`m = doc_type_matcher.classify(folder, filename, ocr[:3000], rel_path)`；`m.doc_type` → `update_file_classify('keyword', score)`；None → `to_thread(recognize_doc_type, ocr[:2000])` → `update_file_classify('llm', confidence)`；空文本 → 'none'。4 类计数++
4. **提取**（仅 4 类且文本非空）：无 active 规则 → 写 skipped(no_active_rule) 结果行 + extract.skip 事件；有 → `to_thread(extract_doc_fields)` → 清洗（masked→skipped_masked 且不参与归因、id_number 非法→skipped_invalid）→ `find_person_match`（None 且无 name → skipped(no_person)）→ `apply_extracted_fields` → `insert_result(done)` + extract.done 事件，extracted_count++；提取异常 → `insert_result(error)` + extract.error，**不抛**
5. 文件级其他异常 → `mark_file_error`，failed_count++，继续
6. 完成 → `finish_import_task('done')` + profile.import.done 事件（context 带各计数）；任务级异常 → error + profile.import.error 事件

**幂等**：重复上传同一 Excel → 新建 task（历史可溯），file_code unique 命中 re-link；已 done 不重下载/OCR；提取按当前 active 规则重跑写新结果行（按 import_task_id 区分），"只补空"保证数据层幂等。

### 4.7 `backend/event_service.py`
新增 `CATEGORY_PROFILE_IMPORT_DONE/ERROR = "profile.import.done"/"profile.import.error"`、`CATEGORY_EXTRACT_DONE/ERROR/SKIP = "extract.done"/"extract.error"/"extract.skip"`。

### 4.8 `backend/main.py` 端点
tags `["客户画像"]` + `["信息提取"]`：
- `POST /api/profile/import`（multipart，仅 .xlsx 否则 400；存 temp 临时文件 → parse（ValueError→400 带具体原因）→ 删临时 → `find_or_create_client(主客户)` → `create_import_task` + `upsert_task_files` → `asyncio.create_task(run_import)`）→ `{task_id, client_name, client_id, total_files, new_files, relinked_files}`
- `GET /api/profile/tasks`（status/client_name/limit/offset）、`GET /api/profile/tasks/{task_id}`（404）
- `GET /api/profile/tasks/{task_id}/files`（不含 ocr_text）
- `GET /api/profile/tasks/{task_id}/profile`：动态拼画像 `{task, client(复用 db/crud 客户查询), family_members(复用 family_crud 按 client 查), extractions(该 task 的 done 结果摘要: file_code/filename/doc_type/extracted/write_stats), type_counts}`
- 规则（08 原样）：`POST /api/doc-extract/rules/draft`、`GET /rules`、`GET /rules/{id}`、`PUT /rules/{id}`(仅 draft,409)、`POST /rules/{id}/activate`、`POST /rules/{id}/disable`
- 结果：`GET /api/doc-extract/results`（import_task_id/file_id/doc_type/status 筛选+分页）、`GET /api/doc-extract/results/{id}`（extracted/mapped 全文）

## 5. 前端最小页 `/profile`（客户画像）

- **ProfilePage.vue**（仿 ArchiveAdminPage/AiApiCallsPage 风格，`<script setup>` + Element Plus）：
  - 上传区：el-upload（drag，accept=".xlsx"）→ `POST /profile/import` → 跳到该任务
  - 任务列表：el-table（id/客户/文件/状态 tag/进度 processed/total/4 类计数/失败数/时间），running 任务 3s 轮询
  - 任务详情（抽屉或展开）：① 客户卡（姓名/性别/出生日期/证件号/国籍/户籍地址…）② 家庭成员表（relation/name/gender/birth_date/id_number，'待确认' 高亮）③ 提取明细表（file_code/filename/doc_type/写入统计，点击看 extracted/mapped JSON）④ 文件清单表（file_code/filename/folder/状态/ocr_source/doc_type/classify_by/score/error）
- **api.js**：`importProfileExcel(formData)` / `listProfileTasks` / `getProfileTask` / `listProfileTaskFiles` / `getProfileTaskProfile` / `listDocExtractResults` / `getDocExtractResult`
- **router.js**：`{ path: '/profile', component: () => import('./components/ProfilePage.vue') }`
- **App.vue**：顶层导航加"客户画像"按钮

## 6. 测试（assert 脚本风格，sys.path 插 backend/）

- `tests/test_doc_type_matcher.py`（纯函数）：4 类正例打分过阈值；结婚证≠出生证；毕业证≠学位证；护照→None；IMG_xxx+无文本→None；rel_path "身份证2026/倪朝晖" 线索加分
- `tests/test_profile_excel_parse.py`（真实 `客户文件信息例子.xlsx`，仓库根）：62 数据行、client_name=倪朝晖、文件编码全提取（数字编码不被读成 float）、错名列"文件啊名称"兼容、缺列/空客户姓名报错路径（临时构造坏表）
- `tests/test_doc_extract_rules.py`（DB）：version 自增、activate 单 active、draft 才可改、disable；测后清理
- `tests/test_doc_extract_mapping.py`（DB）：id_number 命中 client 只补空 / name 命中 member / 无命中新建'待确认' / `[身份证]`→skipped_masked / 非空 skipped_filled / birth_date 转 Date；附 is_masked/valid_id_number 纯函数
- （可选）`tests/smoke/test_profile_import_e2e.py`：真 LLM+真下载，手动跑

## 7. Slice 顺序与验收

| Slice | 内容 | 验收 |
|---|---|---|
| (a) | migration 018 + models + matcher + excel parser + 纯函数测试 | `alembic upgrade head` 过；两个纯函数测试绿 |
| (b) | doc_extract_crud + 规则端点 + draft wrapper | Swagger 走通 draft→审→PUT→activate；test_doc_extract_rules 绿 |
| (c) | customer_file_crud + import service（下载+OCR+分类）+ 任务端点 | 上传 xlsx 跑出全量 OCR+分类（暂不提取），files 接口可见 doc_type/classify_by |
| (d) | 提取+归因+写库+结果端点 | 身份证提取写入 clients/members；test_doc_extract_mapping 绿；结果接口可查 |
| (e) | 前端 /profile 页 | 浏览器上传 xlsx → 看进度 → 画像完整呈现 |
| (f) | runbook 演练 + 日志核对 | 见下节全绿 |

## 8. 验证 runbook（端到端）

1. `alembic upgrade head` → 4 表就位
2. Swagger：`POST /api/doc-extract/rules/draft {doc_type:"id_card"}` → 人工审（必要时 PUT 改 fields）→ `activate` → `GET /rules?doc_type=id_card&status=active` 唯一
3. 前端上传 `客户文件信息例子.xlsx` → 任务进度可见（62 文件，reused/fresh 计数增长）
4. 画像页：倪朝晖客户卡有证件号等补空字段；刘小娟/倪成/倪想出现在家庭成员（按证件号/姓名归属）；提取明细可见每条用的 rule_version；文件清单可见每文件 doc_type 与分类依据
5. DB 核对：`customer_files` 全量 OCR（fresh=原文）；`doc_extract_results.extracted/mapped` 留痕（含 skipped_masked）；`ai_api_calls` 有 recognize/extract/draft 三条 operation；`system_events` 有 profile.import.done
6. 重复上传同一 Excel → relinked 计数生效，不重复下载/OCR，写入仍幂等（只补空）
7. 测试：4 个单测脚本全过

## 9. 风险与注意

- **主进程串行 OCR**：62 文件全量 OCR 需数十分钟（to_thread 不阻塞 API；本地/demo OK）。生产长期使用再议搬到 worker/.NET，本期不动。
- **姓名精确匹配撞名**：只在"本客户"范围内匹配，撞名宁新建成员（matched_by 留痕可审计）。
- **file_url_service 未启用/刷新失败**：该文件标 error 继续，不拖垮任务；external_api_logs 可查。
- **LLM 兜底量**：扫描质量差的关键词识别会集中走 recognize_doc_type（2000 字小调用，max_retries=2）。
- **复用脱敏文本**：证件号写不进（skipped_masked），其余字段可写；要补齐证件号需重新下载 OCR（fresh）。
- **Excel 无 client_code**：客户按姓名 find_or_create，client_code 留空；后续业务方接口接入时以 client_code 为准关联。

## 10. 明确不做（YAGNI）

不接 archive-detect worker 队列；不做规则管理前端页（Swagger 操作）；不动 archive_detect 现有表结构；不做 4 类以外的提取（后续加规则即跑，零改码）；不做画像快照表（profile 端点动态拼）；不处理户口本多成员拆分（MVP 按单人提取，上线后迭代）。

## 11. 后续扩展路径

- **业务方接口**：实现 `FileSourceProvider.fetch_manifest`（按 client_code/姓名远程拉清单），run_import 及下游零改动。
- **加证件类型**：rules 表 draft→activate 新类型 + matcher 加一组关键词 + recognize 白名单加一项（三处小改）。
- **提取别的数据**（护照/无犯罪/房产…）：OCR 已在 customer_files 落库，只需新增规则重跑提取，无需重新下载/OCR。
