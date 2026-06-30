# Aliyun Linux 部署手册

## 适用环境

- 阿里云 ECS,镜像:**Alibaba Cloud Linux 3** 或 CentOS 8+
- 配置建议:**4 核 8GB 起步**,40GB 系统盘 + 100GB 数据盘(挂 `/opt/doc-review/output`)
- 公网带宽:看业务方文件大小,5Mbps 起
- 出口:能访问 PyPI(阿里云镜像 mirrors.aliyun.com 默认通)+ LLM API(看 `config.json.llm.base_url`)

---

## 一次性部署(全新机器)

### 1. SSH 上服务器,跑系统初始化

```bash
# 上传 deploy 目录(或先随便 scp 一份过去)
scp -r deploy/linux root@<服务器IP>:/tmp/
ssh root@<服务器IP>

# 设置数据库密码 + 跑初始化
export PG_PASSWORD='你的强密码'
bash /tmp/linux/01-server-setup.sh
```

这一步装:Python 3.12 / PostgreSQL / nginx / LibreOffice + 中文字体 / OpenCV 系统依赖 / antiword(.doc 文本抽取)。
创建 `docreview` 用户 + `/opt/doc-review` 安装目录。

> .doc 支持依赖 antiword(已在 01-server-setup.sh 安装)。如手动安装:`dnf install -y antiword`。
> 中文 .doc 验证:`antiword -m UTF-8.txt 测试.doc`,若乱码检查 `/usr/share/antiword/` mapping 文件。

### 2. 回本地,跑上传脚本

```bash
cd e:/qoderproject/20260527
bash deploy/linux/05-upload.sh root@<服务器IP>
```

脚本会:本地 `npm run build` → rsync 上传 backend/dist/config/deploy → chown。

### 3. 服务器上跑应用安装 + 启动

```bash
ssh root@<服务器IP>
cd /opt/doc-review

# 装 Python 依赖 + 跑 alembic 迁移 + 预热 PaddleOCR
sudo -u docreview bash deploy/linux/02-install-app.sh

# 改 app.env 把数据库密码填进去
sudo cp deploy/linux/app.env.example deploy/linux/app.env
sudo vim deploy/linux/app.env   # 改 DATABASE_URL 里的密码

# 装 systemd 单元 + 启动 API
sudo bash deploy/linux/03-systemd-install.sh

# 装 nginx 站点
sudo bash deploy/linux/04-nginx-install.sh
```

### 4. 验证

```bash
# 健康检查
curl http://<服务器IP>/healthz

# 浏览器打开
http://<服务器IP>/
```

---

## 日常更新代码

```bash
# 本地
cd e:/qoderproject/20260527
bash deploy/linux/05-upload.sh root@<服务器IP>

# 服务器
ssh root@<服务器IP>
cd /opt/doc-review
# 如果改了 requirements.txt 或 alembic 迁移
sudo -u docreview bash deploy/linux/02-install-app.sh
# 重启 API(必须)
sudo systemctl restart doc-review
# 前端 dist 变了需要让浏览器拿到新文件
sudo systemctl reload nginx
```

---

## 运维命令

```bash
# 服务状态
sudo systemctl status doc-review
sudo systemctl status nginx

# 日志
tail -f /opt/doc-review/logs/api.stderr.log
journalctl -u doc-review -f

# 队列监控
curl http://127.0.0.1:8000/api/archive-detect/admin/queue-stats

# 数据库
sudo -u postgres psql -d doc_review

# 重启(主服务;worker 通过 Wants= 一并就位,已 enable 的会自动拉起)
sudo systemctl restart doc-review

# 只看/重启 OCR worker
sudo systemctl status 'doc-review-worker@*'
sudo systemctl restart doc-review-worker@1
```

> 说明:`doc-review.service` 用 `Wants=doc-review-worker@1.service`,启动主服务会一并拉起 1 个 worker。
> worker 仍是独立进程(OCR 崩溃不连累 API)。需要更多 worker:`sudo systemctl enable --now doc-review-worker@2`。
> `systemctl restart doc-review` 不会重启已在跑的 worker,要单独 `restart doc-review-worker@1`。

---

## 调优开关

`/opt/doc-review/deploy/linux/app.env`:

| 变量 | 默认 | 说明 |
|---|---|---|
| `DATABASE_URL` | (必填) | PostgreSQL DSN |
| `ARCHIVE_DETECT_WORKERS` | 1 | OCR worker 数。**小内存机器保持 1**;8GB+ 可 2 |
| `ARCHIVE_DETECT_QUEUE_MAX` | 200 | 队列水位上限,超过返回 429 |
| `OMP_NUM_THREADS` | 2 | Paddle 内部线程数,**不要乱调** |
| `CORS_ALLOW_ORIGINS` | `*` | 同源部署不用改;跨域填具体 origin |

改完 `app.env` 后 `sudo systemctl restart doc-review`。

---

## 常见问题

### 1. PaddleOCR 首次启动慢

首次会从国内 paddleocr.bj.bcebos.com 下载 ~100MB 模型到 `/home/docreview/.paddlex/`。
`02-install-app.sh` 已经做了预热;若失败重试一次即可。

### 2. soffice 偶发挂掉,导致 docx → PDF 卡住

LibreOffice 单进程并发不安全。`03-systemd-install.sh` 配的 `PrivateTmp=true` 给每个进程独立 `/tmp`,
避免互锁。如果模板填充功能频繁失败,改用 docx 下载、客户端自己看(`render_to_pdf_v2` 已有 fallback)。

### 3. nginx 403 访问 /uploads/

`/opt/doc-review/output` 必须 nginx 用户能读。`04-nginx-install.sh` 已 `chmod 755`,
但如果是 SELinux enforcing,还要:
```bash
sudo setsebool -P httpd_read_user_content 1
sudo chcon -Rt httpd_sys_content_t /opt/doc-review/output
```

### 4. 业务方反馈"上传卡住"

看 nginx 的 `client_max_body_size`(已设 3GB)和 `proxy_read_timeout`(已设 600s)。
真有更大文件就改 `nginx-doc-review.conf` 重新 reload。

### 5. 内存不足报错(`could not create a primitive` / `numpy MemoryError`)

`free -m` 看可用内存。如果常驻 RSS > 70%,降:
- `ARCHIVE_DETECT_WORKERS=1`
- systemd 单元里 `MemoryMax=` 改大(或换更大机器)
- 终极方案:把 PostgreSQL 拆到独立 RDS

---

## 目录布局

```
/opt/doc-review/
├── backend/              # FastAPI 代码
├── migrations/           # alembic
├── config.json           # 老式配置(LLM key 仍走这,DB 走 app.env)
├── alembic.ini
├── .venv/                # Python 虚拟环境
├── frontend/dist/        # 前端构建产物(nginx serve)
├── output/               # OCR 渲染图/拆分PDF/模板填充输出(挂数据盘)
├── temp/                 # 上传/下载临时文件(自动清理)
├── logs/                 # uvicorn stdout/stderr
└── deploy/linux/         # 部署脚本本身
```

`docreview` 用户拥有所有文件;nginx 用户只读 `output/` 和 `frontend/dist/`。
