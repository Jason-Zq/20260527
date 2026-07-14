# 04 · OCR 与文本抽取服务

> **这是重构后保留在 Python 的核心。** 目标：把 OCR/文本抽取收敛为**纯 OCR 微服务**，对外暴露 HTTP API 供 .NET 调用。
>
> 涉及文件：`ocr_service.py`、`text_extractor.py`、`split_ocr_service.py`、`file_fetcher.py`、`redactor.py`。

## 1. OCR 引擎（`ocr_service.py`）

### 1.1 引擎：RapidOCR（onnxruntime，CPU）
- **不是 PaddleOCR**。模型权重随 `rapidocr_onnxruntime` 包内置，无需联网下载、无 libGL 依赖，Linux 部署友好。
- 全局单例懒加载 `_get_ocr_engine()`，用模块级 `_OCR_ENGINE_LOCK`（threading.Lock）串行化——RapidOCR 多线程推理不保证安全，业务审核 worker 与拆分流程在不同线程池，必须串行。

### 1.2 统一入口 `run_ocr(img_path, cls=True)`
所有模块都通过它调用，不直接拿引擎。
- **输入**：单张图片路径
- **输出**：兼容旧 PaddleOCR 格式 `[[ [bbox, (text, confidence)], ... ]]`，无文字时 `[None]`。这样下游 `ocr_result[0]` / `line[0]` / `line[1][0]` / `line[1][1]` 解析零改动。
- 内部：`result, _elapse = engine(img_path)` → 适配为 `[[box, (text, float(score))], ...]`。
- `cls` 参数保留兼容旧签名，RapidOCR 内部自带方向处理。

### 1.3 渲染参数（控制内存峰值）
- `OCR_RENDER_SCALE = 200/72`（200dpi，对扫描件足够，识别率相比 300dpi 下降 <2%，单页裸像素从 ~26MB 降到 ~11MB）
- `MAX_PIXELS = 16_000_000`（≈4000×4000），超过则 `_downscale_if_too_large` 等比缩放兜底，防超大扫描件打爆 numpy/OpenCV
- 置信度阈值：只保留 `confidence > 0.3` 的行

### 1.4 PDF 类型判断 `detect_pdf_type(pdf_path)`
用 pdfplumber 抽全文，去空格换行后有效长度 ≥ `TEXT_LENGTH_THRESHOLD(100)` → `"text"`（文字型），否则 `"image"`（图片型/扫描件）。异常默认 `"image"`。

### 1.5 核心函数
| 函数 | 用途 |
|------|------|
| `run_ocr(img_path, cls)` | 单图 OCR 统一入口（线程安全） |
| `detect_pdf_type(pdf_path)` | 文字型 vs 图片型 |
| `extract_text_pdf(pdf_path)` | 文字型 PDF 用 pdfplumber 抽全文（含表格 `--- 表格内容 ---`），返回 `[{page,text,image:None}]` |
| `extract_image_pdf(pdf_path, task_id, max_ocr_pages=0)` | 图片型 PDF：pypdfium2 逐页渲染成 PNG → run_ocr。`max_ocr_pages=0` 全部。返回 `[{page,text,image,ocr_details}]` |
| `extract_image_file(image_path, task_id)` | 单图 OCR，含 MAX_PIXELS 缩放兜底 |
| `process_file(file_path, task_id, max_ocr_pages=0)` | AI 材料解析统一入口，按扩展名分发 |

PNG 中间产物存 `output/{task_id}/images/page_N.png`，静态可访问；业务审核处理完由 `text_extractor._cleanup_ocr_dir` 清理。

## 2. 文本抽取分发（`text_extractor.py`）

### 2.1 统一入口 `async extract_text(file_path, mime_type=None)`
按扩展名分发到线程池（`asyncio.to_thread`），**每个分支出口都过 `_sanitize_extracted` 清洗 NUL/控制字符**（防御式，见 §2.4）：

| 扩展名 | 处理函数 | 引擎/库 | source |
|--------|---------|---------|--------|
| `.pdf` | `_extract_pdf` | pdfplumber / pypdfium2 + RapidOCR | pdf_text/pdf_ocr/pdf_ocr_sampled |
| `.docx` | `_extract_docx` | python-docx（段落+表格） | docx_text |
| `.xlsx` | `_extract_xlsx` | openpyxl（sheet/cell） | xlsx_text |
| `.xls` | `_extract_xls` | — | xls_text |
| `.pptx` | `_extract_pptx` | python-pptx（slide） | pptx_text |
| `.doc` | `_extract_doc` | olefile → soffice → antiword（三级回退） | doc_text |
| `.gif` | `_extract_gif` | 取首帧转 PNG → OCR | gif_first_frame_ocr |
| `.png/.jpg/.jpeg/.bmp/.tiff/.webp` | `_extract_image` | RapidOCR | image_ocr |

不支持的扩展名抛 `ValueError`。

### 2.2 统一返回格式
```python
{"text": "全文", "source": "pdf_text|pdf_ocr|...", "page_count": int, "char_count": int}
```

### 2.3 大文件 early-exit 采样（`_extract_pdf`）
- 文字型 PDF：pdfplumber 全文，秒级返回。
- 图片型 PDF ≤ `OCR_EARLY_EXIT_THRESHOLD(10)` 页：全页 OCR。
- 图片型 PDF > 阈值页：先 OCR 前 2 页 → LLM 初判 `detect_large_table_doc`：
  - 判为大表类（流水/社保/证券）→ 再 OCR 末页 = 3 页采样返回（`pdf_ocr_sampled`），记事件 `file.ocr_sampled`
  - 否则 → OCR 全文
  - LLM 抽风降级 → 也走采样（激进保速度）

> **微服务化注意**：`detect_large_table_doc` 是**对 LLM 的依赖**，纯 OCR 微服务应把它剥离——要么由 .NET 侧先判后传标志位（如 `sample_mode=head_tail`），要么把"是否采样 + 采样页码"作为请求参数交给调用方决定。见 [07-重构规划.md](07-重构规划.md)。

### 2.4 NUL/控制字符清洗（`_sanitize_text` / `_sanitize_extracted`）
- pdfplumber/python-docx/OCR 偶发混入 `0x00` 等 C0 控制符，PostgreSQL text 列不接受 NUL。
- `_CTRL_DELETE`：删除所有 C0 控制符（保留 `\t \n \r`）+ DEL(0x7F)。
- 在抽取出口统一剔除并同步 `char_count`，作为 CRUD 层清洗之外的**源头防御**（DB 层 `archive_detect_crud._clean_text` 是第二道闸门）。

### 2.5 `normalize_text(text, max_chars=None)`
给 LLM 前规整：合并多余空白行（`\n{3,}→\n\n`）、多空格合一、可选截断（头尾各留一半 + `...[省略 N 字]...`）。

### 2.6 PDF 拆分专用 OCR（`split_ocr_service.py`）
`split_extract_all_pages`：全页 OCR、200dpi、单线程，复用 ocr_service 全局引擎。不复用 `process_file`，因为拆分要每页文本判页边界。

## 3. 文件下载（`file_fetcher.py`）

### 3.1 下载
- `MAX_DOWNLOAD_BYTES = 50MB`，超限抛 `FileTooLargeError`。
- `async fetch_url_to_temp(url)` → `(local_path, filename, mime_type)`，落 `temp/fetched/<uuid>_xxx`。
- httpx 连接池 `max_connections=None` 不设上限；并发保护交给上层 `archive_detect_service._DOWNLOAD_SEMAPHORE`（默认 10，env `ARCHIVE_DETECT_DOWNLOAD_CONCURRENCY`）。
- `is_supported_extension` / `get_unsupported_hint` / `get_supported_extensions` 校验扩展名。

### 3.2 URL 刷新（业务方 OSS 签名地址过期）
- `refresh_download_url(file_id, type_)` 调业务方 `getFileDownloadUrl`（`config.json.file_url_service`）。
- **除 `file_id/type` 外必带 `usr_login/operation_user/url` 三个身份参数**，否则对方返回"没有登陆人不可查看"。默认值 `Jason邹启/Jason邹启/batch`。
- `fetch_url_to_temp_with_refresh(url, file_id)`：先直连，403/过期错误则用 file_id 刷新地址重试。
- 每次刷新调用记 `external_api_logs`（service=refresh_url），埋点 `_log_external_api`（async create_task）。

### 3.3 临时文件清理（Windows WinError 32）
Windows 上 pdfplumber/OCR 句柄释放有延迟，立即 `os.remove` 报"文件被占用"。策略：
1. `cleanup_temp_file`：立即删 → 失败 `sleep(0.5)` 重试一次 → 仍失败丢进模块级延迟队列 `_pending_cleanup`。
2. 启动挂 `periodic_cleanup_task()`：启动扫一次 `temp/fetched/` 删 1 小时前残留；之后每 60 秒处理延迟队列。
3. 这是业务无影响的收尾清理，HTTP 仍返回 200。

## 4. 脱敏（`redactor.py`）

> 脱敏在 worker 写 DB 前应用（`ocr_text`、`reason`、`key_points`）。**注意：`ai_api_calls` 表存脱敏前原文**（业务决策）。

### 4.1 规则（正则）
| 类型 | 正则（简） | 替换 |
|------|-----------|------|
| 金额 | `[¥￥$€£]\s*\d[\d,]*(?:\.\d+)?`、`\d+(万\|亿)` 等 | `[金额]` |
| 手机号 | `\b1[3-9]\d{9}\b` | `[手机号]` |
| 身份证 | 18 位（含 X） | `[身份证]` |
| 银行卡 | `\b(?:\d[ -]?){14,19}\d\b` | `[银行卡]` |
| 座机 | `(?:\(?0\d{2,3}\)?[-\s]?)?\b\d{7,8}\b` | `[座机]` |

### 4.2 API
- `redact(text)` — 单串脱敏
- `redact_list(items)` — 列表逐项
- `redact_dict(d)` — 按 key 脱敏：`_REDACT_STR_KEYS=(reason,summary,title,relevance_reason)`、`_REDACT_LIST_KEYS=(key_points,)`。返回新 dict，不改原对象。

> **微服务化注意**：脱敏是纯正则、无外部依赖，**建议保留在 OCR 微服务侧**（作为可选开关），或迁到 .NET（C# 正则等价）。两种都可行，取决于是否希望 OCR 微服务返回已脱敏文本。当前系统脱敏在 worker（业务侧），若纯 OCR 微服务只返回原文，则脱敏逻辑需在 .NET 重写。见 [07-重构规划.md](07-重构规划.md)。

## 5. 微服务化时 OCR 服务需暴露的能力

重构后 OCR 微服务对外应提供（HTTP）：

| 能力 | 对应现有函数 | 建议 API |
|------|-------------|---------|
| 文本抽取（统一入口，多格式） | `text_extractor.extract_text` | `POST /ocr/extract`（传文件或 URL，返回 `{text, source, page_count, char_count}`） |
| 单图 OCR（含坐标） | `ocr_service.run_ocr` / `extract_image_file` | `POST /ocr/image`（返回 ocr_details 含 bbox，供证件解析定位） |
| PDF 全页 OCR（拆分用） | `split_ocr_service.split_extract_all_pages` | `POST /ocr/pdf-pages`（返回每页文本） |
| PDF 类型判断 | `detect_pdf_type` | 可内联进 extract，或单独 `POST /ocr/pdf-type` |

**需从 OCR 微服务剥离的 LLM 依赖**：
- `text_extractor._extract_pdf` 里的 `detect_large_table_doc`（大文件采样初判）——改为 .NET 传采样策略参数。
- 脱敏（`redactor`）——迁到 .NET 或作为微服务可选开关。

**保留在 OCR 微服务的纯能力**：RapidOCR 引擎、pdfplumber/pypdfium2/python-docx/openpyxl/python-pptx/olefile 抽取、下载与 URL 刷新、临时文件清理、NUL 清洗。
