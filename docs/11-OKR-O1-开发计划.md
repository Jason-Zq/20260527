# 11-OKR-O1 开发计划：可信、易用的客户数据底座

> 周期：2026-08 ~ 2026-10（约 13 周）。本文是 OKR O1 的开发落地计划，与 [10-客户画像v2-复核与领域模型.md](10-客户画像v2-复核与领域模型.md) 衔接。

## 0. OKR → 现状对位

| KR | 要求 | 当前已有 | 差距 |
|---|---|---|---|
| KR1 准确性 | 高频证件关键字段提取准、复查率低 | 12 类分类器 + 提取规则(RULES_VERSION=4) + 多人模式 + 字段校验(身份证校验位修复/日期) + 质量评级 + 复核闭环 | **无准确率度量基线**；无回归评测手段；护照无 MRZ 级校验；复查率未量化 |
| KR2 来源可核对 | 每字段有来源、点击看原文、多源比对 | 字段可信度徽标 + 「字段来源与可信度」抽屉 + 原件预览(img/PDF/Office 转 PDF) + 一致/不一致标注 | **已基本完成**。已定粒度=文件级，只需核查覆盖率补漏 |
| KR3 一键反馈 | 零输入一键反馈 → IOD 后台 → 持续优化 | 无 | **全新**：反馈表 + 提交端点 + 企业微信/邮件通知 + 反馈管理页 + 处理闭环 |
| KR4 销售易上线 | 画像嵌入销售易客户页 + 节点提醒标红 | 画像数据接口、到期提醒逻辑(护照/准证/身份证 list_expiry_reminders)、前端画像弹窗 | **全新**： 精简嵌入页 + 销售易侧对接；子女年龄只需**展示**（不定项目上限规则） |

关键结论：**KR2 是收尾活，KR1 是 9 月主攻，KR3 最快见效先做，KR4 依赖外部条件（域名/销售易配置）放 10 月。**

## 1. 里程碑总览

| 阶段 | 时间 | 目标 | 工作项 |
|---|---|---|---|
| M1 | 8/4 – 8/29 | 反馈闭环跑通 + 准确率基线可量化 | W1 一键反馈、W2 度量基线、W3 KR2 补漏 |
| M2 | 9/1 – 9/26 | 准确性专项，复查率下降 | W4 MRZ 校验、W5 数据驱动优化、W6 复查率校准 |
| M3 | 9/28 – 10/31 | 销售易上线 + 验收 | W7 嵌入、W8 提醒/子女年龄、W9 验收文档 |

## 2. 工作项明细

### W1 一键反馈机制（KR3，约 1.5 周）

**表**（migration 026）`feedback_records`：
`id / household_id(FK) / person_id(可空) / customer_code(反范式,便于筛选) / field_key(可空,空=整文件反馈) / ai_value / source_file_id(customer_files.id,可空) / extract_result_id(可空) / note(可空备注) / status(new/processing/resolved/rejected) / created_by / created_at / resolved_at / resolve_note`

**后端**：
- `POST /api/profile/feedback`：一键提交，快照当前 AI 值+来源文件，零必填输入。
- `GET /api/admin/feedbacks` + `PUT /api/admin/feedbacks/{id}`（状态流转+处理备注），GC 不删（长期留作优化语料）。
- 新 `backend/notify_service.py`：企业微信群机器人 webhook（`config.json` 加 `notify.wecom_webhook`），fire-and-forget `create_task`，失败记 `system_events` 不阻塞提交；webhook 未配置时静默跳过。消息含客户/字段/AI 值/来源文件/管理页链接。

**前端**：
- 画像弹窗人员卡字段行 hover 出「反馈」图标按钮 → 点击即提交（可留空备注的确认弹层）→ toast 成功。嵌入页同款（W7 复用组件）。
- 新页 `/feedbacks`（FeedbackListPage，菜单「日志监控」组）：列表+按客户/字段/状态筛选+处理操作。

**闭环**：技术人员在反馈页定位 → 改规则/prompt（W5 输入）→ 对该家庭「重新生成画像」验证 → 标记 resolved。

### W2 准确率度量基线（KR1 前提，约 1 周）

没有基线就无法证明「准确率提上去、复查率打下来」，必须先做。

- **字段级准确率统计** `GET /api/admin/extract-accuracy?days=&doc_type=`：数据源 `doc_extract_results`（`review_status` + `corrected` JSONB）。口径：confirmed 行的写出字段=正确；corrected 行里 `corrected` 的 key=错误。输出 doc_type × field 的 total/corrected/accuracy。
- **复查率指标**：`customer_files` 的 `review_status/quality_score` 分布 + 任务 `needs_review_count/files` 占比，按周趋势。
- **管理页**：`/extract-accuracy` 简单表格页（先够用，不做图表工程）。
- **回归评测脚本** `tests/eval_extract.py`（一次性脚本风格，同 test_profile_api_import）：抽样已复核(done+confirmed/corrected)文件，按当前规则重跑提取对比人工值，输出字段级准确率报告。**每次改 prompt/规则前后各跑一次对比**，这是 W5 的安全网。

### W3 KR2 来源覆盖补漏（约 0.5 周）

- 核查所有 verified 字段（含 asset/case）都有来源入口；资产已有 `listHouseholdAssetFiles`，补齐缺口。
- 来源抽屉 polish：取值/一致标已有；确认 Office/PDF 大文件预览稳定性。
- 文件级粒度已定，不做页码/高亮。

### W4 护照 MRZ 解析校验（KR1 主攻，约 1 周）

护照是最高频证件，MRZ（底部两行 44 字符机读区）自带校验位，是准确率大杀器（同身份证校验位思路）：

- `field_validators.py` 加 `parse_mrz_td3(ocr_text)`：正则定位 MRZ 行 → 解析 passport_no/birth_date/expiry_date/nationality/姓名 → 逐段校验位验证。
- `validate_field_items` 扩展 passport 分支：MRZ 校验通过的值与 LLM 提取值交叉——一致=高可信；不一致以 MRZ 为准修复记 `write_stats.repairs`；MRZ 缺失/校验失败不动原值记 flags。
- 测试 `tests/test_mrz.py`（合成 MRZ 纯函数测试）。

### W5 数据驱动的 Top 错误优化（KR1，约 2 周，循环进行）

按 W2 看板中错误率 Top 字段逐个击破，每轮用 `eval_extract.py` 回归：
- prompt/规则微调（extract_rules.py + llm_service）；
- 日期/姓名归一化后处理增强（按需）；
- 分类落 other 的文件抽样分析，补 matcher 关键词；
- 存量家庭「重新生成画像」重跑生效（已有机制）。

### W6 复查率校准（KR1，约 0.5 周）

- 用 W2 数据看 `quality_score` 与人工复核结果的相关性，校准 `review_service.evaluate_file_quality` 规则权重；
- 高可信字段（多源一致+校验通过，credibility≥80）在复核队列降优先级/默认折叠；
- 目标：队列缩短且队列内真实错误占比（命中率）上升。

### W7 销售易 iframe 嵌入（KR4，约 2.5 周）

**硬前置（需外部配合，尽早启动）**：
1. 销售易是 https 页面，嵌 http iframe 会被浏览器 mixed-content 拦截。IOD 服务器（120.26.67.160）需配域名+证书（宝塔 Let's Encrypt）。**没有域名，KR4 无法上线**。
2. nginx 对嵌入路径放开 `X-Frame-Options` / 设 `frame-ancestors` 白名单销售易域名。

**鉴权方案（免登签名 URL）**：销售易 iframe 组件只能做 URL 模板插字段、算不了 HMAC，故采用**每客户固定签名链接**：
- `config.json` 加 `embed.secret`；`sign = HMAC_SHA256(secret, customer_code)` 截 32 位，永久有效、不可枚举。
- 管理端 `GET /api/admin/embed-links`：导出全部 household 的签名链接 CSV → 销售易侧批量导入客户自定义字段「画像链接」→ iframe 组件引用该字段。销售易侧零开发，管理员配置即可。
- `GET /api/embed/profile?customer_code=&sign=`：验签 → customer_code 查 household → 复用画像组装逻辑返回精简 JSON。auth middleware 白名单 `/api/embed/*`（自验签）。

**嵌入页** `/embed/profile`（EmbedProfilePage）：
- 无侧边栏（App.vue 的 `isLoginPage` 布局开关扩成 `/login|/embed` 裸页判断）；紧凑只读画像：人员卡+字段+可信度徽标+来源抽屉（复用 ReviewDrawer 的预览/FilePreviewDialog）+到期标红横幅+**一键反馈按钮**（W1 组件复用）。
- 修正操作不进嵌入页（顾问要改 → 链接跳完整后台），嵌入页聚焦「看+核对+反馈」。

**交付物**：销售易对接配置文档（管理员向：自定义字段+iframe 页签配置步骤+截图）。

### W8 节点提醒 + 子女年龄（KR4，约 0.5 周）

- **子女年龄展示**（已定：不做项目上限规则）：画像组装时按 `birth_date` 算 `age`，人员卡 relation=子/女 显示年龄徽标；嵌入页同款。工作量极小。
- **到期标红**：嵌入页顶部横幅复用 `attach_passport_expiry`/`list_expiry_reminders` 逻辑（护照/准证/身份证，expired/expiring≤180 天）。
- **可选（二期候选）**：每日定时把 7 天内到期清单推企业微信群（复用 W1 notify_service + asyncio 定时任务，仿 `_split_cleanup_loop` 模式）。

### W9 验收 + 文档（10 月底）

- 指标验收：字段准确率（8 月底基线 vs 10 月底）、需复核文件占比、反馈处理时长、嵌入页可用性。
- 更新 CLAUDE.md / AGENTS.md；销售易对接文档归档 docs/。

## 3. 风险与依赖

| 项 | 类型 | 说明 |
|---|---|---|
| 域名+HTTPS | **硬依赖（外部）** | 无域名则 KR4 阻塞，需尽早申请 |
| 销售易管理员配合 | 外部 | 自定义字段+iframe 页签配置+链接批量导入，需业务方排期（约 0.5 天） |
| 企业微信群机器人 webhook | 外部 | 需提供一个测试群+正式群地址，W1 前到位 |
| LLM 成本 | 内部 | 回归评测与存量重跑控制批量，避免打爆额度 |
| 嵌入页数据敏感性 | 安全 | 画像含证件号；签名 URL 不可枚举 + 链接仅在销售易登录态可见，泄露面可控；必要可加 Referer 校验 |

## 4. 验收口径（建议目标，8 月底基线出来后确认）

- KR1：高频字段（身份证号/护照号/出生日期/到期日）字段级准确率 ≥95%；需复核文件占比较基线降 ≥30%。
- KR2：verified 字段来源可查看覆盖率 100%。
- KR3：画像页+嵌入页反馈按钮全覆盖；反馈→群通知 <1 分钟；反馈 7 日内处理闭环。
- KR4：销售易客户页可打开嵌入画像；护照/准证/身份证到期标红；子女年龄可见。
