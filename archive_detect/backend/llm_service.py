"""
LLM 服务（极简版，仅供"文件留底检测"使用）。

只暴露：
  - load_config()        : 启动时调用，读 ../config.json
  - detect_archival(text, user_prompt) : 调 LLM 给出留底判定 JSON

完整版（含证件解析、模板填写等）在主项目 backend/llm_service.py，本子项目用不到。
"""

import os
import json
import re
import time
from openai import OpenAI


CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config.json")
CONFIG: dict = {}

ARCHIVE_DETECT_INPUT_LIMIT_CHARS = 30000


def load_config():
    """从子项目根目录的 config.json 加载配置。"""
    global CONFIG
    if not os.path.exists(CONFIG_PATH):
        raise FileNotFoundError(f"配置文件不存在: {CONFIG_PATH}（请复制 config.example.json 为 config.json 并填入 LLM key）")
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        CONFIG = json.load(f)


def _call_llm(prompt: str, max_retries: int = 3) -> str:
    """调用大模型 API，返回原始响应文本。429 退避 2/4/6s。"""
    llm = CONFIG.get("llm", {})
    api_key = llm.get("api_key", "")
    base_url = llm.get("base_url", "")
    model = llm.get("model", "")
    temperature = llm.get("temperature", 0.2)

    if not api_key or not base_url or not model:
        raise RuntimeError("LLM 配置不完整，请检查 config.json 的 llm.api_key / base_url / model")

    client = OpenAI(api_key=api_key, base_url=base_url)

    last_exc = None
    for attempt in range(max_retries):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
            )
            raw = (resp.choices[0].message.content or "").strip()
            # 去掉 ``` 围栏与可能的 <tool_call> 残留
            raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.IGNORECASE)
            raw = re.sub(r"<tool_call>.*?</tool_call>", "", raw, flags=re.DOTALL)
            return raw.strip()
        except Exception as e:
            last_exc = e
            msg = str(e).lower()
            if "429" in msg or "rate" in msg or "throttle" in msg:
                wait = 2 * (attempt + 1)
                print(f"[llm] 429/限流，{wait}s 后重试 ({attempt + 1}/{max_retries})...")
                time.sleep(wait)
                continue
            raise
    raise last_exc or RuntimeError("LLM 调用失败（达到最大重试次数）")


def _build_archive_detect_prompt(text: str, user_prompt: str) -> str:
    """把用户输入的多行判定标准拼接进 LLM prompt。"""
    doc_types = CONFIG.get("document_types") or [
        "身份证件", "学历证明", "婚姻证明", "财务证明", "工作证明",
        "申请表单", "合同协议", "报告说明", "简历", "其他",
    ]
    cat_str = " / ".join(doc_types)

    return (
        "你是一个文档归档（留底）判定助手。下面是用户描述的判定标准（请严格按它判定，未提到的维度不要发挥）：\n"
        "---用户判定标准开始---\n"
        f"{user_prompt}\n"
        "---用户判定标准结束---\n\n"
        "请阅读以下文件内容，判定该文件是否符合上述判定标准，并输出：\n"
        "1. is_archival: true / false\n"
        "2. confidence: 0-100 整数（基于文本证据强度）\n"
        "3. reason: 30-120 字判断依据，仅引用与判定标准直接相关的内容；"
        "**不要泄露金额、电话、身份证号、银行卡号、账号等敏感信息，遇到这类内容请用 [金额]/[手机号]/[身份证]/[银行卡] 等占位词代替**\n"
        "4. key_points: 3-6 条要点 bullets，遵循同样的脱敏要求\n"
        f"5. doc_category: 从下列中选一个：{cat_str}\n\n"
        "返回严格 JSON，不要 markdown 代码块、不要任何额外解释：\n"
        '{"is_archival": true, "confidence": 0, "reason": "...", "key_points": ["..."], "doc_category": "..."}\n\n'
        "文件内容：\n---\n"
        f"{text}\n---\n"
    )


def detect_archival(text: str, user_prompt: str) -> dict:
    """以用户提供的判定标准判断 text 是否符合留底要求。

    入参：
      text        - 已抽取的纯文本（OCR 或 docx 抽取）
      user_prompt - 用户输入的多行判定标准（必填）

    返回：
      {is_archival: bool, confidence: int 0-100, reason: str,
       key_points: list[str], doc_category: str}

    异常：ValueError - 文本为空 / user_prompt 为空 / LLM 返回非 JSON
    """
    if not text or not text.strip():
        raise ValueError("文件内容为空，无法判定")
    if not user_prompt or not user_prompt.strip():
        raise ValueError("判定标准 user_prompt 不能为空")

    src = text.strip()
    if len(src) > ARCHIVE_DETECT_INPUT_LIMIT_CHARS:
        head_n = ARCHIVE_DETECT_INPUT_LIMIT_CHARS // 2
        tail_n = ARCHIVE_DETECT_INPUT_LIMIT_CHARS - head_n
        src = (
            src[:head_n]
            + f"\n\n...[省略 {len(text) - ARCHIVE_DETECT_INPUT_LIMIT_CHARS} 字]...\n\n"
            + src[-tail_n:]
        )

    prompt = _build_archive_detect_prompt(src, user_prompt.strip())
    raw = _call_llm(prompt)

    # 解析 JSON（容错）
    try:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            data = json.loads(raw[start:end + 1])
        else:
            data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"LLM 返回非合法 JSON：{raw[:200]}") from e

    is_archival = data.get("is_archival")
    if isinstance(is_archival, str):
        is_archival = is_archival.strip().lower() in ("true", "1", "yes", "是")
    is_archival = bool(is_archival)

    try:
        confidence = int(data.get("confidence", 0))
    except (TypeError, ValueError):
        confidence = 0
    confidence = max(0, min(100, confidence))

    return {
        "is_archival": is_archival,
        "confidence": confidence,
        "reason": str(data.get("reason", "")).strip(),
        "key_points": [str(x).strip() for x in (data.get("key_points") or []) if str(x).strip()],
        "doc_category": str(data.get("doc_category", "其他")).strip() or "其他",
    }
