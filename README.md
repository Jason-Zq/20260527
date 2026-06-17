# 客户材料 AI 解析系统

自动识别 PDF 证件材料中的文字，并利用大模型进行结构化提取，建立公司级"客户信息档案库"。

> ## ⚠️ 仓库可见性提醒
>
> 本仓库 git 历史中**曾经包含** `config.json` 文件（含 LLM API Key 与数据库密码）。
> 虽然新提交已通过 `git rm --cached` + `.gitignore` 把它从索引剔除，**git 历史里仍能查到旧 commit 的配置内容**。
>
> - **当前为 Private 仓库**：仅限授权成员访问，可继续使用
> - **若未来要改为 Public**：必须先用 `git filter-repo` 或 `BFG Repo-Cleaner` 清理整段历史，并强制作废当前 LLM API Key（到火山引擎控制台重新生成）
> - **新成员加入**：clone 后请复制 `config.json.example` 为 `config.json`，填入实际配置；切勿将 `config.json` 重新加回 git


## 功能特性

- **PDF 类型自动检测** — 自动判断文字型 / 扫描图片型 PDF，分别采用直接提取或 OCR 方式
- **高精度 OCR 识别** — 基于 PaddleOCR（PP-OCRv4），支持中文场景的高精度文字识别
- **PDF 切图保存** — 图片型 PDF 每页渲染为 300dpi PNG 图片并保存
- **证件类型自动检测** — AI 自动判断属于 12 类证件中的哪一类
- **结构化信息提取** — 根据证件类型调用大模型，返回标准 JSON 格式的字段信息
- **配置驱动** — API Key、模型参数、提示词模板均通过 config.json 配置，无需改代码

## 技术栈

| 组件 | 技术 | 说明 |
|------|------|------|
| OCR 引擎 | PaddleOCR 2.x (PP-OCRv4) | 中文高精度文字识别 |
| PDF 渲染 | pypdfium2 | 将 PDF 页面渲染为图片，无需安装 Poppler |
| PDF 文字提取 | pdfplumber | 文字型 PDF 直接提取文本和表格 |
| 大模型 | MiniMax M2.7-highspeed | 证件类型检测 + 结构化提取 |
| LLM 调用 | openai SDK | 兼容 OpenAI API 格式 |
| 运行环境 | Python 3.12 | PaddlePaddle 限制，需 3.9-3.13 |

## 环境要求

- Python 3.9 ~ 3.13（PaddlePaddle 不支持 3.14+）
- 推荐 Python 3.12

## 安装部署

### 1. 创建虚拟环境

```bash
# 使用 Python 3.12 创建虚拟环境
python -m venv .venv312

# 激活虚拟环境
# Windows:
.venv312\Scripts\activate
# Linux/Mac:
source .venv312/bin/activate
```

### 2. 安装依赖

```bash
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### 3. 配置 config.json

在项目根目录创建或编辑 `config.json`，填入大模型 API Key：

```json
{
  "llm": {
    "api_key": "你的API Key",
    "base_url": "https://api.minimaxi.com/v1",
    "model": "MiniMax-M2.7-highspeed",
    "temperature": 0.1
  },
  "document_prompts": {
    "身份证": "...",
    "护照": "..."
  }
}
```

## 配置说明

### llm — 大模型 API 配置

| 字段 | 说明 | 默认值 |
|------|------|--------|
| api_key | 大模型 API Key | 必填 |
| base_url | API 地址 | https://api.minimaxi.com/v1 |
| model | 模型名称 | MiniMax-M2.7-highspeed |
| temperature | 生成温度 (0, 1] | 0.1 |

### document_prompts — 证件类型提示词

Key 为证件类型名称，Value 为对应的提取提示词模板。系统会自动将 OCR 识别文字追加到提示词末尾。

支持增删证件类型，只需在 `document_prompts` 中添加或移除条目即可。

## 输出目录结构

```
output/
└── <PDF文件名>/
    ├── images/                          # PDF每页切图
    │   ├── page_1.png
    │   ├── page_2.png
    │   └── ...
    ├── <PDF文件名>-ORC识别后.txt         # OCR识别的原始文字
    └── <PDF文件名>-解析后.json           # 大模型结构化提取结果
```

### 结构化 JSON 格式

```json
{
  "document_type": "身份证",
  "data": [
    {
      "姓名": "张三",
      "性别": "男",
      "民族": "汉",
      "出生日期": "1990-01-01",
      "住址": "广东省深圳市...",
      "身份证号": "440300199001011234"
    }
  ]
}
```

## 支持的 12 类证件

| 证件类型 | 提取字段 |
|----------|----------|
| 身份证 | 姓名、性别、民族、出生日期、住址、身份证号 |
| 护照 | 姓名、英文名、国籍、护照号、有效期、签发日期 |
| 户口本 | 户主姓名、关系、姓名、身份证号、住址 |
| 结婚证 | 男方姓名、女方姓名、登记日期、登记机关 |
| 出生证明 | 婴儿姓名、性别、出生日期、出生地点、父亲姓名、母亲姓名 |
| 学历证书 | 姓名、性别、出生日期、学校名称、专业、学历层次、入学日期、毕业日期、证书编号 |
| 学位证书 | 姓名、性别、出生日期、学校名称、学科门类、学位级别、获得日期、证书编号 |
| 工作证明 | 姓名、性别、身份证号、公司名称、职位、入职日期、证明日期 |
| 银行流水 | 户名、账号、开户银行、交易日期、交易金额、交易描述 |
| 存款证明 | 姓名、证件号、存款金额、存款类型、开立日期、到期日期、银行名称 |
| 房产证 | 不动产权证号、权利人、共有情况、坐落、面积、用途、使用期限 |
| 无犯罪证明 | 姓名、性别、身份证号、户籍地址、证明机构、出具日期、有效期 |

## 处理流程

```
输入PDF
  │
  ├─ 文字型PDF ──→ pdfplumber直接提取文本
  │
  └─ 图片型PDF ──→ pypdfium2渲染为图片 ──→ PaddleOCR识别文字
  │                                                │
  │                                           保存切图到images/
  │
  └─────────────────┴────────────────────────┘
                    │
              OCR识别文字
                    │
          AI检测证件类型（12类）
                    │
          按类型选择提示词模板
                    │
          调用大模型结构化提取
                    │
          保存结果（TXT + JSON）
```
