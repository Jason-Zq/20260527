# 12 - CI/CD 发布流水线计划（Jenkins)

> 状态：**已上线试运行**(2026-08-04 同日实施完毕，build #6 全链路绿：push main → Poll SCM 2 分钟内自动测试+部署测试服）。实施记录见文末 §10。
> 相关文档：[06-前端与部署.md](06-前端与部署.md)、[deploy/linux/README.md](../deploy/linux/README.md)

## 1. 背景与目标

当前发布全靠本机 Windows 手工 `tar|ssh` 上传 + 人工 SSH 重启，历史上由此引发的事故（CLAUDE.md 有案可查）：

| 事故 | 根因 | CI/CD 对策 |
|---|---|---|
| 025 迁移漏传，全任务 UndefinedColumn | 未跟踪文件靠人肉挑文件上传 | 制品从干净 git checkout 构建，不存在"漏挑" |
| `pkill` 匹配到 bash 自身，只杀没拉起 | 手工命令行操作 | 部署脚本入库版本化，固化 `[u]vicorn` 括号技巧 + setsid |
| 残留两代 4 个 worker 进程 | 重启前无进程盘点 | 部署脚本先 `ps` 盘点再杀再起，按台参数化 worker 数 |
| alembic 版本漂移（停 014，015/017 手工建过） | 迁移与代码发布不同步 | 部署自动 `upgrade head` + 校验 `current==heads` |
| SSH 中途掉线导致部署半成品 | 本地长连接直驱 | Jenkins 在服务器侧执行，构建与会话无关 |

**目标**：推 main 自动发测试服；打 tag + 一键批准发 IOD 生产；每次发布自动备份 DB、自动迁移、健康检查、失败可回滚。

## 2. 已确认决策

1. **触发策略**：推 main → 自动发测试服；打 tag `v*` → 手动批准后发 IOD。
2. **执行器**：**Jenkins LTS 装在测试服 8.138.111.12**(4C/8G)，先试行；资源与安全约束见 §4。
3. **分支模型**：维持单人直推 main + tag 发布，不引入 PR 流程。
4. **数据库迁移**：部署时自动 `pg_dump` 备份 + 自动 `alembic upgrade head`，校验不通过则部署失败。
5. **不改应用形态**：不上 Docker/k8s、不动 nohup 直跑模式（systemd 化作为可选增强，见 §8)。

## 3. 总体架构

```
本机 push main ──► GitHub ──(Jenkins Poll SCM 每分钟轮询)──► Jenkins(8.138.111.12)
                                                                  │
                                        ┌─────────────────────────┤
                                        ▼                         ▼
                              Job: doc-review-ci        Job: doc-review-release
                              (main 自动触发)            (手动, 参数=tag)
                                        │                         │
                              test → build → package      checkout tag → 同左
                                        │                         │
                              deploy 测试服(本机执行)      input 人工批准
                                        │                         ▼
                                        │                  SSH 部署 IOD(120.26.67.160)
                                        ▼
                            /opt/fastapi + /opt/vue3/dist
```

- **制品**：`release-<sha>.tar.gz` = `backend/ + migrations/ + alembic.ini + deploy/ + frontend/dist/`。**永不含** `config.json`、`venv`、`output/`、`temp/`、`logs/`。从干净 checkout 构建，根治"漏挑文件"。
- **服务器端发布脚本** `deploy/ci/release.sh` 入库，是部署的唯一逻辑载体：快照 → 备份 DB → 同步代码 → 装依赖（仅 requirements 变化时）→ 迁移+校验 → 重启 → 健康检查 → 失败回滚。测试服本机执行；IOD 通过 SSH 远程执行同一脚本（脚本随制品送达，天然同版本）。

## 4. Jenkins 安装与加固（测试服 4C/8G 生存策略）

**安装**(Alibaba Cloud Linux 3):

```bash
sudo dnf install -y java-17-openjdk-devel git
sudo wget -O /etc/yum.repos.d/jenkins.repo https://pkg.jenkins.io/redhat-stable/jenkins.repo
sudo rpm --import https://pkg.jenkins.io/redhat-stable/jenkins.io-2023.key
sudo dnf install -y jenkins
sudo systemctl enable --now jenkins   # 官方包自带 systemd unit,白捡进程托管
```

另需：`dnf install nodejs20`(或用 nvm 装 Node 20，前端构建）、`dnf install rsync`。

**资源限制（8G 内存机器上必须做）**:

- systemd override 限 JVM:`JAVA_OPTS="-Xmx512m"`，并 `systemd edit jenkins` 加 `MemoryMax=1.5G` 兜底。
- Jenkins 执行器数 = **1**（管理页 Configure → executors)，杜绝并行构建。
- 前端构建：`NODE_OPTIONS=--max-old-space-size=1024`(vite 默认可能吃到 2G+)。
- 检查 swap，没有则加 2G swapfile 作 OOM 保险。
- 构建与 OCR worker 撞内存的风险：单人项目构建频次低，可接受；若实测 OOM 再把 Jenkins 挪到 IOD(14G)。

**安全加固**:

- **不暴露公网**：安全组不开 8080；访问 UI 走 SSH 隧道 `ssh -L 8080:localhost:8080 root@8.138.111.12`。
- 触发方式用 **Poll SCM**(`H/2 * * * *` 每 2 分钟轮询 GitHub)，不需要 GitHub webhook → Jenkins 完全无入站暴露。代价：触发延迟 ≤2 分钟，单人项目无感。
- 插件最小化：Install suggested + **SSH Agent**(SSH 部署 IOD 用）+ Timestamper。不装多余插件，季度跟 LTS 升级。
- GitHub 拉取：用只读 PAT 或 deploy key 存 Jenkins 凭据；Aliyun 拉 GitHub 若慢，git 插件开 shallow clone(depth 1；release job 需要 tag，用 `refspec + 具体 tag 抓取`)。

**Jenkins 自身备份**:`/var/lib/jenkins` 每日 cron tar 到 `/opt/backups/jenkins/`(job 配置即全部资产，丢了能重建）。

## 5. 流水线定义（入库，Pipeline-as-Code)

### 5.1 测试策略

仓库根新增 `tests/run_ci.sh`（测试白名单的唯一维护点），分两档：

- **纯函数档**：test_split_service / test_redactor / test_scan_anchors / test_doc_type_matcher / test_profile_api_manifest / test_extract_rules / test_review_scoring / test_extract_multi / test_image_preprocess / test_field_validators / test_ocrapi_auth 等。
- **DB 档**(Jenkins 在测试服、本机就有 PG，直接可用）：建独立库 `doc_review_ci`(**绝不碰 doc_review 业务库**),`DATABASE_URL` 指过去 + `alembic upgrade head` 建 schema → 跑 test_doc_extract_mapping / test_profile_crud / test_person_dedup / test_person_merge / test_credibility / test_file_assign / test_profile_cases_project / test_profile_content_dedup / test_worker_runner_claim 等。每个 DB 测试沿用现有"测后清理"惯例。
- 调 LLM 的测试不进 CI（成本 + key 不进 Jenkins)。

### 5.2 `Jenkinsfile`(job: doc-review-ci,Poll SCM 触发）

```groovy
pipeline {
  agent any
  options { timestamps(); disableConcurrentBuilds() }
  stages {
    stage('Test') {        // python3.12 venv + pip install -r backend/requirements.txt
      steps { sh 'bash tests/run_ci.sh' }   // 内部: export DATABASE_URL=...doc_review_ci
    }
    stage('Build') {       // frontend: npm ci && NODE_OPTIONS=... npm run build
      steps { sh 'cd frontend && npm ci && npm run build' }
    }
    stage('Package') {     // tar 制品,排除 config.json/venv/output/temp/logs
      steps {
        sh 'bash deploy/ci/package.sh'
        archiveArtifacts artifacts: 'release-*.tar.gz', fingerprint: true
      }
    }
    stage('Deploy Test') { // 本机执行服务器端发布脚本
      steps { sh 'bash deploy/ci/release.sh --target test --artifact release-${GIT_COMMIT}.tar.gz' }
    }
  }
  post { failure { echo 'TODO: 通知(邮件/企业微信),见 §8' } }
}
```

### 5.3 `Jenkinsfile.release`(job: doc-review-release，手动触发）

- 参数：`TAG`(如 `v2026.08.05`)。checkout 该 tag → 跑与 CI 相同的 Test/Build/Package（同 SHA 同 lockfile，与"build once"等价，且免去跨 job 传制品的复杂度）。
- `input message: "确认发布 ${TAG} 到 IOD 生产?"` ← **人工批准卡点**。
- SSH 部署：`scp` 制品到 IOD `/tmp/` → `ssh` 解包并执行制品内的 `deploy/ci/release.sh --target iod`。SSH key(`~/.ssh/iod_deploy`）存 Jenkins 凭据，用 SSH Agent 插件注入。

## 6. 服务器端发布脚本 `deploy/ci/release.sh`（核心，入库）

两台服务器布局差异全部参数化（`--target test|iod` 选择）:

| 参数 | test(8.138.111.12) | iod(120.26.67.160) |
|---|---|---|
| 代码目录 | `/opt/fastapi` | `/opt/fastapi` |
| 前端 dist | `/opt/vue3/dist` | `/opt/front/dist` |
| worker 数 | 1 | 2 |
| venv | `/opt/fastapi/backend/venv` | 同左 |

执行步骤（每步失败即中止并标记构建红）:

1. **进程盘点**:`ps aux | grep -E '[u]vicorn|[w]orker_runner'` 记录当前代数（治"残留两代 worker")。
2. **代码快照**:`tar czf /opt/backups/code-<ts>.tar.gz backend/ + 前端 dist`，回滚凭据；保留最近 5 份。
3. **DB 备份**:`pg_dump -Fc doc_review > /opt/backups/pre-deploy-<ts>.dump`。
4. **同步代码**:rsync 制品内容到 `/opt/fastapi/`(`--delete --exclude config.json --exclude venv --exclude output --exclude temp --exclude logs`)；前端沿用现有 `dist.new + mv` 交换模式（旧目录留 `dist.old.*`)。
5. **依赖**:requirements.txt 与上一版 diff 变化才 `venv/bin/pip install -r`（服务器走 PyPI 镜像）。
6. **迁移**:`alembic current` 记录 → `alembic upgrade head` → **校验 `current==heads`，不等则中止**（代码已同步但服务未重启，回滚快照后报错退出；**不回滚 DB**)。
7. **重启**:`pkill -f '[u]vicorn main:app'` / `pkill -f '[w]orker_runner'`（括号技巧防自杀，固化进脚本）→ `setsid` 脱离会话拉起 uvicorn:8765 + N 个 worker。**必须 setsid**:Jenkins 的 ProcessTreeKiller 会杀构建衍生的后台进程。
8. **健康检查**:`curl --retry 10 --retry-delay 3 --retry-all-errors http://127.0.0.1:8765/api/healthz`（直连 uvicorn，绕开"localhost 命中 nginx 默认块"的坑）；失败 → 恢复快照 → 再重启 → 构建标红。
9. 输出 `queue-stats` 摘要到构建日志备查。

**回滚操作**（人工，分钟级）：服务器上执行 `release.sh --rollback <快照时间戳>`（恢复代码快照 + 重启；DB 不回滚，迁移纪律见 §7)。

## 7. 迁移纪律（自动化的前提）

- 新迁移文件**必须随代码提交进 git** —— CI 从干净 checkout 打制品，漏提交=CI 里测试就红，到不了服务器。
- 生产迁移坚持**向后兼容**（只加列/加表，改语义走 expand-contract 两步），这样"代码回滚、DB 不回滚"才成立。
- 破坏性迁移（删列/改类型）不自动发，单独走人工窗口。

## 8. 后续可选增强（不在首期范围）

- **通知**：构建失败发邮件（QQ 邮箱 SMTP）或企业微信 webhook。
- **应用 systemd 化**：仓库 `deploy/linux/` 已有现成 unit 文件，适配路径后替代 nohup，治"SSH 掉线只杀没拉起"更彻底；release.sh 的启动段届时换成 `systemctl restart`。
- **releases + symlink 目录结构**(Capistrano 式不可变发布）：首期用快照回滚已够用（venv 内 shebang 写死路径不可移动、`output/` 存画像原件，搬迁成本高），列为演进方向。
- **依赖扫描**:Dependabot(GitHub 原生）或 pip-audit/npm audit 挂 CI。
- **secret 扫描**:gitleaks 挂 CI(config.json 历史教训）。

## 9. 落地步骤与验收

| # | 步骤 | 验收 |
|---|---|---|
| 1 | 入库 `deploy/ci/release.sh` + `deploy/ci/package.sh` + `tests/run_ci.sh`，先在测试服手工跑通 release.sh 全流程 | 手工执行一次发布成功，healthz 绿 |
| 2 | 测试服装 Jenkins（含 §4 资源限制与安全组确认），建 `doc_review_ci` 库 | SSH 隧道能开 UI,Poll SCM 能拉到仓库 |
| 3 | 配 doc-review-ci job，用一个 docs-only commit 推 main 观察全链路 | 2 分钟内自动触发，测试绿→自动发测试服→healthz 绿 |
| 4 | 故意推一个 failing test 验证红构建不部署 | 构建红、测试服未动 |
| 5 | 配 doc-review-release job，打 tag `v2026.08.x` 演练发 IOD（含批准卡点、回滚演练各一次） | IOD 发布成功；`--rollback` 能回到上一版 |
| 6 | 更新 CLAUDE.md/AGENTS.md 部署章节，标注"手工 tar 上传已退役" | 文档一致 |

**首期不做**：PR 流程、Docker 化、多 runner、制品仓库（Nexus 等）、灰度发布。

## 10. 实施记录（2026-08-04，已上线）

当天完成：Jenkins 2.568.1 LTS 装于测试服（`127.0.0.1:8080`,SSH 隧道访问；admin 密码在服务器 `/root/.jenkins-admin-credentials`);`doc-review-ci` job(Poll SCM `H/2`）创建；build #6 全链路绿（push main → 自动测试 → 前端构建 → 打制品 → 部署测试服 → 健康检查通过）。测试白名单 27 项（13 纯函数 + 14 DB）在服务器实测全过。

实施中踩掉的坑（都已固化进脚本/配置，勿回退）:

| 坑 | 对策 |
|---|---|
| Jenkins 2.568 要求 Java 21;yum GPG 密钥 2023 版已过期 | 用 `jenkins.io-2026.key`;`java-21-alibaba-dragonwell-headless`（与 17 有文件冲突，17 已卸） |
| 公网 `mirrors.aliyun.com` 对 pip 23.x 全挂；崩溃后 jenkins 用户 pip 缓存被污染，后续安装必炸 | 镜像固定为内网 `mirrors.cloud.aliyuncs.com` + TUNA 兜底（`run_ci.sh`/`release.sh`)；不做 pip 自升级；出事 `pip cache purge` |
| PG 集群 template1 是 SQL_ASCII，默认建库继承后 psycopg2 编中文 SQL 必炸 | `doc_review_ci` 显式 `ENCODING 'UTF8' TEMPLATE template0` + `PGCLIENTENCODING=UTF8`（业务库 doc_review 本来就是 UTF8) |
| **flock fd 被守护进程继承**:release.sh 的发布锁经 fork 传给 uvicorn/worker,open file description 不释放 → 后续发布永远"已有发布在进行" | `start_services` 启动行加 `9>&-` 显式关 fd；已中招的要杀一次旧守护进程释放 |
| 服务器老 `/opt/fastapi/restart.sh`（无括号无 setsid 自杀版） | 已被 `deploy/ci/release.sh` 取代，勿再用 |
| 部分"纯函数"测试 import 链读 config.json | `run_ci.sh` 自动生成 CI 专用 config.json（不进制品） |
| Jenkins 凭据 XML 内联私钥易被转义搞坏 | 用 scriptText groovy 重建凭据（见 memory:jenkins-ci-setup) |

待办（按 §8 优先级）：IOD 生产发布（`Jenkinsfile.release` + `iod-ssh-key` 凭据）、失败通知、JENKINS_HOME 每日备份。
