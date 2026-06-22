# 文件留底检测 (archive-detect)

独立的"文件留底检测"工具：上传 / 输入文件地址 → AI 按用户描述的标准判定文件是否符合留底要求。

## 功能

- **多行提示词**：用户描述判定标准（默认模板：`帮我检测文件是否是 XXX 客户 XXX 项目的 XXX 进展（留底）文件`），AI 严格按此判定
- **两种文件来源**：本地上传（拖拽多文件）或粘贴文件 URL（一行一个，最多 20 个）
- **一文件一卡**结果展示：是否符合留底 + 置信度 + 判断依据 + 关键要点
- **敏感信息自动脱敏**：金额 / 电话 / 身份证号 / 银行卡号在前端展示前已被替换为 `[金额]` / `[手机号]` 等占位符（提示词约束 + 服务层正则双层保险）
- **多文件并发**：单次 ≤20 文件，OCR 串行 + LLM 并发 3
- **无数据库依赖**：状态全在内存，重启服务后所有进行中的任务失效（用户重新提交即可）

## 技术栈

- 后端：FastAPI + asyncio + PaddleOCR + AI LLM（OpenAI 兼容协议）
- 前端：Vue 3 + Element Plus + Vite

## 目录结构

```
archive_detect/
├── README.md
├── config.json              ← 你需要从 config.example.json 复制并填入 LLM API Key
├── config.example.json
├── backend/
│   ├── main.py              FastAPI 入口（4 路由）
│   ├── archive_detect_service.py
│   ├── llm_service.py       极简版（仅 _call_llm + detect_archival）
│   ├── redactor.py          敏感信息正则脱敏
│   ├── file_fetcher.py      URL → temp 文件下载
│   ├── ocr_service.py       PaddleOCR + pdfplumber + pypdfium2
│   ├── text_extractor.py    pdf/docx/image 统一抽取
│   └── requirements.txt
└── frontend/
    ├── package.json
    ├── vite.config.js
    ├── index.html
    └── src/
        ├── main.js
        ├── App.vue
        ├── api.js
        └── components/
            └── ArchiveDetectEntryPage.vue
```

## 准备工作

### 1. 配置 LLM API Key

```bash
cd archive_detect
cp config.example.json config.json
```

编辑 `config.json`，填入 `llm.api_key` / `llm.model`。

### 2. 后端依赖

**离线方式（推荐，秒级安装，无需联网）**：

如果你拿到的交付包里带有 `offline_wheels/` 目录（约 300MB），`deploy.ps1` 会自动检测并使用本地安装，无需任何配置。

或者手动安装：
```bash
cd archive_detect
python -m venv .venv
.venv\Scripts\activate
.venv\Scripts\pip install --no-index --find-links offline_wheels -r backend/requirements.txt
```

**在线方式（需联网）**：

```bash
cd archive_detect
python -m venv .venv
source .venv/Scripts/activate     # Windows Git Bash
# 或 .venv\Scripts\activate        # Windows cmd
pip install -r backend/requirements.txt
```

> 提示：`paddleocr` + `paddlepaddle` 体积较大（>1GB），首次启动还会下载 OCR 模型权重，需联网。如果你确定只处理文字型 PDF / DOCX，可以删掉 requirements.txt 里的这两行（OCR 路径会失效，但文字型 PDF / DOCX / 图片型 PDF 的"无 OCR 文字"分支不会崩）。

**给业务同事打离线包的步骤（开发者一次性操作）**：

```bash
# 在能联网的开发机上：
cd archive_detect
.venv\Scripts\pip download -r backend/requirements.txt \
    -d offline_wheels \
    --platform win_amd64 --python-version 3.12 --only-binary=:all: \
    -i https://pypi.tuna.tsinghua.edu.cn/simple
# 把 offline_wheels/ (约 300MB) 跟随项目一起拷给同事，deploy.ps1 自动识别
```

### 3. 前端依赖

```bash
cd archive_detect/frontend
npm install
```

## 运行

### 开发模式

终端 1（后端）：
```bash
cd archive_detect/backend
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

终端 2（前端）：
```bash
cd archive_detect/frontend
npm run dev
```

浏览器打开 `http://localhost:5173`。

### 生产部署（同事使用）

**后端**：
```bash
cd archive_detect/backend
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

可用 `nssm` (Windows) 或 `systemd` (Linux) 注册成开机自启服务。

**前端**：
```bash
cd archive_detect/frontend
npm run build
# dist/ 是静态文件，扔到任意 web 服务器（nginx / caddy / 内网共享目录）
```

nginx 反代示例：
```nginx
server {
    listen 80;
    server_name archive-detect.example.com;

    location / {
        root /path/to/archive_detect/frontend/dist;
        try_files $uri $uri/ /index.html;
    }

    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        client_max_body_size 60M;     # 与 file_fetcher 50MB 对齐
        proxy_read_timeout 300s;       # OCR + LLM 单文件最长 200s
    }
}
```

## 配置项说明（config.json）

| 字段 | 说明 |
|---|---|
| `llm.api_key` | 火山引擎 ark API Key |
| `llm.base_url` | LLM endpoint，默认 `https://ark.cn-beijing.volces.com/api/v3` |
| `llm.model` | ark 模型 ID（形如 `ep-xxxx`） |
| `llm.temperature` | 推荐 0.1-0.2，太高判定不稳定 |

## 路由

- `POST /api/archive-detect/upload` — multipart 上传
- `POST /api/archive-detect/urls` — JSON `{user_prompt, urls[]}`
- `GET  /api/archive-detect/{batch_id}` — 轮询
- `GET  /api/health` — 健康检查

## 常见问题

**Q：重启后前端报 404**
A：内存态丢了，让用户重新提交即可。当前刻意去掉 DB 简化部署。

**Q：OCR 极慢**
A：CPU 模式 PaddleOCR 是慢，单页 2-5s 正常。建议 `max_ocr_pages` 设 5；或换 GPU 版 paddlepaddle。

**Q：内存占用涨**
A：内存里只保留 6 小时内的批次（`archive_detect_service.RESULT_TTL_HOURS`），后台 30 分钟跑一次 GC。

