# 02 · API 接口清单

> 所有接口集中定义在 `backend/main.py`（FastAPI `@app.*` 装饰器，**无 APIRouter/include_router**）。静态资源挂载 `/uploads`（映射 `output/` 目录），非 API。
>
> **接口总数：72 个**。重构到 .NET 时，这些是必须保持的对外契约（前端依赖）。

## 目录

| 业务线 | 接口数 | 前缀 |
|--------|-------|------|
| [一、文件留底检测](#一文件留底检测archive-detect) | 17 | `/api/archive-detect/*` |
| [二、AI 材料解析](#二ai-材料解析) | 8 | `/api/upload`、`/api/result/*`、`/api/history`… |
| [三、客户档案生成](#三客户档案生成) | 4 | `/api/client-profile/*` |
| [四、客户管理](#四客户管理) | 15 | `/api/clients/*`、`/api/family/*`、`/api/assets/*` |
| [五、Word 模板](#五word-模板) | 10 | `/api/templates/*` |
| [六、PDF 拆分](#六pdf-拆分) | 6 | `/api/split/*` |
| [七、URL 摘要](#七url-摘要) | 4 | `/api/file-summary`、`/api/summaries/*` |
| [八、销售线索](#八销售线索) | 1 | `/api/sales/*` |
| [九、可观测性/运维](#九可观测性--运维) | 7 | `/api/healthz`、`/api/admin/*` |

---

## 一、文件留底检测（archive-detect）

核心 service：`archive_detect_service`、`archive_detect_crud`。

### 1. `POST /api/archive-detect/business/batch` — 业务批量提交（主入口）
- 业务方批量提交进展包（JSON + OSS URL），增量复用 + 业务字段透传。接口阶段只校验 + 写 DB(pending) + 秒回，不下载/OCR。
- **Body**（`BusinessBatchPayload`）：
  - `criteria` string 必填 — 审核标准/判定提示词
  - `stage` string — `pre_submit`/`post_submit`，默认 `post_submit`
  - `client` object 必填 — `client_code`(upsert key)、`name`
  - `progress` object 必填 — `progress_oid`(必填)、`handler`、`project_name`、`project_code`、`project_detail_name`、`project_detail_code`、`progress_name`
  - `items` array 必填 — 每项 `file_id`(复用 key)、`filename`、`url`(http/https)
- **响应**：`batch_id`、`progress_id`、`total_files`、`reused_count`、`new_count`、`queue_depth`
- `submit_business_batch(...)`；URL 非 http/https 或 stage 非法 → 400

### 2. `GET /api/archive-detect/business/batch?batch_id=` — 轮询业务批次结果（Query 版）
### 3. `GET /api/archive-detect/business/batch/{batch_id}` — 同上（Path 版）
- **响应**（`ArchiveDetectBatchResponse`）：`batch_id`/`criteria`/`source_kind`/`total_files`/`done_files`/`status`/`overall_verdict`/`overall_score`/`overall_reason`/`client`/`progress`/`reused_count`/`new_count`/`files[]`
- `get_business_batch(batch_id)`；不存在 404

### 4. `POST /api/archive-detect/recheck/{batch_id}` — 重新审核（新建批次）
- 复用 OCR 文本重新跑 AI（无 OCR 文本则重下载/OCR），新建一个 recheck 批次。
- **Body**（`ArchiveDetectRecheckPayload`）：`criteria`(默认"")、`stage`、`regenerate_criteria`(bool，true 时按 client/progress/stage 重新生成规则并忽略传入 criteria)
- **响应**：`batch_id`(新)、`source_batch_id`、`total_files`、`ai_only_count`、`ocr_count`、`mode`
- `submit_recheck_batch(...)`

### 5. `POST /api/archive-detect/rerun/{batch_id}` — 原地重跑（不新建批次）
- 复用已有结果，只补跑缺失的。
- **Query**：`force_all` bool，默认 false（true=无视已有 AI 结果全跑）
- **Body**：`ArchiveDetectRecheckPayload`（同上，支持 `regenerate_criteria`）
- **响应**：`batch_id`、`total_files`、`ai_only_count`、`ocr_count`、`skipped_count`、`mode`
- `rerun_batch_inplace(...)`

### 6. `GET /api/archive-detect/batch?batch_id=` — 轮询批次+每文件状态（含中间态）
### 7. `GET /api/archive-detect/{batch_id}` — 同上（Path 通用版）
- `get_batch(batch_id)`；不存在 404

### 8. `DELETE /api/archive-detect/{batch_id}` — 删除历史批次
- 内存 + DB 级联清理 files。响应 `{deleted: bool}`；`delete_batch`

### 9. `GET /api/archive-detect/history?limit=200` — 历史批次列表（不含 files）
- `limit` 1-500 默认 200；响应 `items[]` + `total`

### 审核管理后台

### 10. `GET /api/archive-detect/admin/queue-stats` — 文件级队列实时统计
- 响应：`queue_depth`、`queue_max`、`workers`、`in_flight_batches`、`llm_semaphore_avail`、`free_memory_mb`

### 11. `GET /api/archive-detect/admin/batches` — 后台批次列表（多条件筛选）
- **Query**：`status`、`source_kind`、`batch_id`、`overall_verdict`(逗号分隔 match,partial,mismatch)、`has_error_file`(bool)、`client_code`、`client_name`、`progress_oid`、`progress_name`、`handler`、`date_from`/`date_to`(YYYY-MM-DD)、`limit`(默认100,1-500)、`offset`
- 响应 `items[]`(含 client/progress) + `total`；`admin_list_batches(...)`

### 12. `POST /api/archive-detect/admin/rejudge-overall` — 按新规则批量重判总体（异步）
- 只重跑总体判定，不 OCR/下载/碰单文件。**Body**：`verdicts`(默认 `["partial","mismatch"]`)；冲突 409

### 13. `GET /api/archive-detect/admin/rejudge-overall/progress` — 重判总体进度

### 14. `POST /api/archive-detect/admin/rerun-files-batch` — 批量重审（异步）
- 对目标批次逐个原地重跑单文件（复用 ocr_text 重判）。**Body**：`verdicts`(默认 partial/mismatch)、`batch_ids`(可选，提供时只处理其中 done 批次)、`regenerate_criteria`(bool)；冲突 409

### 15. `GET /api/archive-detect/admin/rerun-files-batch/progress` — 批量重审进度

### 16. `GET /api/archive-detect/admin/progress` — 后台进展包列表
- **Query**：`client_code`、`client_name`、`handler`、`project_name`、`progress_oid`、`limit`、`offset`

### 17. `GET /api/archive-detect/admin/file/{record_id}` — 单文件详情（含 ocr_text 大字段）
- `record_id` = archive_detect_files.id；`admin_get_file_detail`；不存在 404

> 注：历史文档提到的 `/api/archive-detect/upload`、`/api/archive-detect/urls`（匿名上传/URL 提交）**在当前代码中已不存在**，留底检测仅保留业务模式；`source_kind` 仍保留 `upload`/`url` 枚举供历史数据兼容。`business/batch/upload` 已停用返回 410。

---

## 二、AI 材料解析

核心 service：`ocr_service`、`llm_service`、`db.crud`。

### 18. `POST /api/upload` — 上传解析（异步）
- `file`(multipart 必填)、`client_id`(Form 可选，带则解析完自动归档、跳过复核)。响应 `{task_id, status:"processing"}`。上限 50MB（超限 413）。
- 后台 `_process_file_background` → `ocr_service.process_file` → `llm_service.detect_and_extract` → `match_bboxes_to_fields`

### 19. `GET /api/result/{task_id}` — 轮询解析进度/结果
- 处理中 `{status:ocr|llm, progress}`；完成 `{status:done, filename, items[], images[], ocr_texts[]}`；失败 `{status:error}`

### 20. `PUT /api/result/{task_id}` — 保存人工复核 + 可选归档
- **Body**：`items[]`(修正字段)、`archive`(可选 `{client_id, entity: clients/family/assets, target_id, sub_meta}`)

### 21. `GET /api/history` — 解析任务历史（最多 100，倒序）
### 22. `GET /api/search?keyword=` — 全文模糊搜索 documents（最多 50）
### 23. `GET /api/export/{task_id}` — 导出解析结果为 JSON 附件
### 24. `DELETE /api/history/{task_id}` — 删除解析历史（DB + output 目录 + 缓存）
### 25. `GET /api/field-router/doc-types` — 字段路由器已知 doc_type 列表（前端下拉）

---

## 三、客户档案生成

核心 service：`client_profile_service`。

### 26. `GET /api/client-profile/source-files/{client_id}` — 候选 OCR 文件列表
- 响应 `items[]`(id/filename/doc_category/progress_name/status/char_count/has_ocr_text/selectable) + `total`

### 27. `POST /api/client-profile/generate/{client_id}` — 创建生成任务
- **Body**：`source_file_ids` int[]（archive_detect_files.id 数组）。响应 `task_id`/`client_id`/`source_file_count`/`status`

### 28. `GET /api/client-profile/generate/{task_id}` — 查询任务状态
- 响应 `status`(running/done/error)/`extracted_summary`/`created_count`/`error` 等

### 29. `GET /api/client-profile/generate/list/{client_id}` — 某客户的生成记录列表

> ⚠️ 路由陷阱：`/generate/{task_id}`(GET) 与 `/generate/{client_id}`(POST) 共用前缀，靠 method 区分。任何新增 GET 子路径（如 `/generate/list/{client_id}`）必须放在 `{task_id}` 之前，否则被抢匹配。

---

## 四、客户管理

核心 service：`crud`、`family_crud`、`assets_crud`、`template_crud`。

### 客户主表
- **30.** `GET /api/clients` — 客户列表（含文档/家属/资产计数）。Query：`keyword`/`visa_type`/`expiring_soon_days`/`sort_by`。最多 200
- **31.** `POST /api/clients/match` — 客户智能匹配（证件号/护照号/姓名+生日）。评分 100/95/80/50，最高分>=90 给 `best_match_client_id`
- **32.** `POST /api/clients` — 新建客户（`name` 必填）
- **33.** `GET /api/clients/{client_id}` — 客户详情（主表 + family + assets + client_info + documents）
- **34.** `PUT /api/clients/{client_id}` — 编辑客户主表（部分字段）

### 家庭成员
- **35.** `GET /api/clients/{client_id}/family` — 家庭成员列表
- **36.** `POST /api/clients/{client_id}/family` — 新建（`relation`+`name` 必填）
- **37.** `PUT /api/family/{member_id}` — 更新
- **38.** `DELETE /api/family/{member_id}` — 删除

### 资产
- **39.** `GET /api/clients/{client_id}/assets` — 资产列表
- **40.** `POST /api/clients/{client_id}/assets` — 新建（`asset_type` 必填）
- **41.** `PUT /api/assets/{asset_id}` — 更新
- **42.** `DELETE /api/assets/{asset_id}` — 删除

### 其他
- **43.** `POST /api/clients/{client_id}/info` — KV upsert（模板填写"同步到档案"）。Body `key_values` dict
- **44.** `GET /api/clients/{client_id}/fills` — 该客户模板填充历史（最多 100）

---

## 五、Word 模板

核心 service：`template_service`、`template_crud`。

- **45.** `POST /api/templates/parse` — 上传 docx（不入库）→ HTML + anchor 扫描 + LLM enrich + PNG 渲染。响应 `{html, anchors[], pages[], temp_token, filename}`
- **46.** `POST /api/templates` — 保存模板（用户采纳/编辑 anchor 后）。Body `name`/`filename`/`anchors[]`/`temp_token`
- **47.** `POST /api/templates/quick-save` — 快速保存（直接读 parse 缓存 enrich.json，不再调 LLM）
- **48.** `GET /api/templates` — 模板列表（最多 200）
- **49.** `GET /api/templates/{template_id}` — 模板详情（含 placeholders）
- **50.** `DELETE /api/templates/{template_id}` — 删除（DB + 级联 fills + 目录）
- **51.** `GET /api/templates/{template_id}/preview-html` — 原始 HTML + placeholders
- **52.** `GET /api/templates/{template_id}/preview-pages` — Word 原貌 PNG 页 URL 列表
- **53.** `POST /api/templates/{template_id}/map-client` — anchor→客户字段匹配（规则+LLM+缓存）。Body `client_id`
- **54.** `POST /api/templates/{template_id}/generate` — 生成填充文件（PDF 优先，失败降级 DOCX）。Body `client_id`/`anchor_values`。返回 FileResponse（头 `X-Fallback-Docx`）

---

## 六、PDF 拆分

核心 service：`split_ocr_service`、`llm_service`、`split_service`、`split_crud`。

- **55.** `POST /api/split` — 上传多证件 PDF，异步拆分。响应 `{task_id, status}`
- **56.** `GET /api/split/history?limit=200` — 拆分历史列表
- **57.** `DELETE /api/split/history/{task_id}` — 删除拆分历史（DB + 目录）
- **58.** `GET /api/split/{task_id}` — 轮询进度/结果
- **59.** `GET /api/split/{task_id}/download/{idx}` — 下载第 idx 份 PDF（0-based）；已清理 410
- **60.** `GET /api/split/{task_id}/download-all` — 打包 zip 下载全部

> 路由顺序：`/api/split/history` 必须声明在 `/api/split/{task_id}` 之前。

---

## 七、URL 摘要

核心 service：`file_fetcher`、`text_extractor`、`llm_service`、`summary_crud`。

- **61.** `POST /api/file-summary` — 同步 URL→下载→抽取→LLM 摘要+相关性→入库→返回。Body `url`(必填)+`progress_name`(必填)。响应含 `title`/`summary`/`key_points`/`doc_category`/`relevance`(strong/weak/unrelated)/`relevance_score`/`relevance_reason`。超 50MB 413，下载失败 502
- **62.** `GET /api/summaries?limit=100&offset=0` — 摘要历史（不返 extracted_text）
- **63.** `GET /api/summaries/{summary_id}` — 摘要详情（含 extracted_text）
- **64.** `DELETE /api/summaries/{summary_id}` — 删除

---

## 八、销售线索

核心 service：`sales_crud`。

- **65.** `GET /api/sales/child-age-leads` — 子女年龄线索。Query：`keyword`/`min_age`/`max_age`/`limit`/`offset`。从 `family_members` 中 relation 为子女的记录算年龄（`min_age/max_age` 在 Python 层过滤，`total` 可能略偏）

---

## 九、可观测性 / 运维

核心 service：`event_service`、`event_crud`、`request_log_crud`、`external_api_log_crud`、`ai_api_call_crud`。

- **66.** `GET /api/healthz` — 健康检查（DB `SELECT 1` + 队列统计）。正常 `{status:"ok", queue}`，异常 503 `{status:"unhealthy", problems[]}`
- **67.** `GET /api/admin/events` — 业务事件流（默认最近 24h）。Query：`severity`(逗号分隔)/`category`/`batch_id`/`since`/`until`/`limit`/`offset`
- **68.** `GET /api/admin/events/categories` — 已出现的 category 列表
- **69.** `GET /api/admin/request-logs` — API 外部请求日志。Query：`source`/`method`/`path`/`since`/`until`/`limit`/`offset`
- **70.** `GET /api/admin/external-api-logs` — 调用外部接口（refresh_url/llm）。Query：`service`/`status`/`batch_id`/…
- **71.** `GET /api/admin/ai-api-calls` — AI/LLM 调用记录。Query：`operation`/`model`/`status`/`batch_id`/`file_id`/`client_code`/`task_id`/…
- **72.** `GET /api/admin/ai-api-calls/{row_id}` — AI 调用详情（prompt/response 全文）

---

## 通用约定

- **响应包**：多数写操作返回 `{message, ...}` 或资源字典；列表返回 `{items:[], total}` 或 `{xxx:[], total}`。
- **错误码**：400 参数错误、404 资源不存在、409 任务冲突（异步任务已在跑）、410 资源已清理、413 文件超限、500 内部错误、502 外部依赖失败、503 依赖不可用（DB/渲染）。
- **鉴权**：业务接口**不加 API Key 鉴权**，当前假定由网络层隔离。重构到 .NET 时可考虑补充。
- **静态资源**：`/uploads/*` 映射 `output/` 目录（PNG/PDF/DOCX 产物）。
