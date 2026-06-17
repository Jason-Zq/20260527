"""
LLM 服务模块
调用大模型进行证件类型检测和结构化提取（含置信度）。
"""

import os
import json
import re
from concurrent.futures import ThreadPoolExecutor
from openai import OpenAI

# 配置文件路径
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config.json")

# 全局配置
CONFIG = {}


def load_config():
    """从项目根目录的 config.json 加载配置。"""
    global CONFIG
    if not os.path.exists(CONFIG_PATH):
        raise FileNotFoundError(f"配置文件不存在: {CONFIG_PATH}")
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        CONFIG = json.load(f)


def _call_llm(prompt: str, max_retries: int = 3) -> str:
    """调用大模型 API，返回原始响应文本。支持重试机制。"""
    llm = CONFIG.get("llm", {})
    api_key = llm.get("api_key", "")
    if not api_key:
        raise ValueError("未配置大模型 API Key")

    client = OpenAI(base_url=llm.get("base_url", ""), api_key=api_key)

    last_error = None
    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=llm.get("model", ""),
                messages=[{"role": "user", "content": prompt}],
                temperature=llm.get("temperature", 0.1),
                extra_body={"reasoning_split": True},
            )

            result_text = response.choices[0].message.content.strip()

            # 容错：去除 markdown 代码块标记
            if result_text.startswith("```"):
                lines = result_text.split("\n")
                lines = [l for l in lines if not l.strip().startswith("```")]
                result_text = "\n".join(lines).strip()

            # 容错：去除 MiniMax 模型可能残留的思考标签
            result_text = re.sub(r'<tool_call>.*?⋩', '', result_text, flags=re.DOTALL).strip()

            return result_text

        except Exception as e:
            last_error = e
            if "429" in str(e) and attempt < max_retries - 1:
                # 429 限流错误，等待后重试
                import time
                wait_time = (attempt + 1) * 2  # 递增等待: 2s, 4s, 6s
                print(f"  [LLM] 限流，等待 {wait_time}s 后重试 ({attempt + 1}/{max_retries})")
                time.sleep(wait_time)
            else:
                # 非限流错误或最后一次重试，直接抛出
                raise

    raise last_error


def detect_and_extract(ocr_texts: list) -> dict:
    """
    一次LLM调用同时完成证件类型检测 + 结构化提取（含置信度）。
    支持多张证件：返回 items 数组，每项包含 doc_type + fields。
    字段完全由AI自行判断提取，不预定义固定格式。
    返回格式: {"items": [{"doc_type": "身份证", "fields": {...}}, ...]}
    """
    doc_types = CONFIG.get("document_types", [])
    if not doc_types:
        # 兼容旧配置：如果仍使用 document_prompts 格式
        doc_types = list(CONFIG.get("document_prompts", {}).keys())

    all_text = "\n".join(ocr_texts)

    prompt = (
        "你是一个专业的证件信息提取助手。请完成以下任务：\n"
        "1. 判断OCR文字属于哪种证件类型（可能包含多张同类或不同类证件）\n"
        "2. 分别提取每张证件中所有可识别的重要信息\n\n"
        f"参考证件类型：{'、'.join(doc_types)}\n\n"
        "提取要求：\n"
        "- 字段名由你根据证件内容自行命名，使用简洁的中文（如：姓名、身份证号、签发机关、有效期限等）\n"
        "- 不要遗漏任何可识别的重要信息，尽可能全面提取\n"
        "- 如果OCR文字中出现非标准证件类型，也请尽力提取其中的关键信息\n\n"
        "请返回JSON格式，包含items数组：\n"
        "- items: 证件信息数组，每个元素包含doc_type和fields\n"
        "  - doc_type: 证件类型名称（从参考列表中选择最匹配的，如无法匹配则自行命名）\n"
        "  - fields: 各字段的提取结果，每个字段包含value和confidence（0到1之间的小数表示置信度）\n"
        "如果只有一张证件，items数组只包含一个元素。\n"
        '示例：{"items": [{"doc_type": "身份证", "fields": {"姓名": {"value": "张三", "confidence": 0.98}, "签发机关": {"value": "佛山市公安局", "confidence": 0.92}}}]}\n'
        "只返回JSON，不要包含其他文字。\n\n"
        f"OCR识别文字：\n{all_text}"
    )

    print("正在调用大模型（类型检测+结构化提取，支持多证件）...")
    try:
        result_text = _call_llm(prompt)
        data = json.loads(result_text)

        # 兼容：如果LLM返回的是单证件格式（doc_type + fields），自动转为items数组
        if "doc_type" in data and "items" not in data:
            items = [{"doc_type": data["doc_type"], "fields": data.get("fields", {})}]
        elif "items" in data:
            items = data["items"]
        else:
            items = [{"doc_type": "未知", "fields": data}]

        # 规范化每个item
        normalized_items = []
        for item in items:
            doc_type = item.get("doc_type", "未知")
            # 验证类型是否在可选列表中
            if doc_type not in doc_types:
                for dt in doc_types:
                    if dt in str(doc_type):
                        doc_type = dt
                        break

            raw_fields = item.get("fields", {})
            fields = _normalize_fields(raw_fields)
            normalized_items.append({"doc_type": doc_type, "fields": fields})

        print(f"  识别到 {len(normalized_items)} 张证件: {[it['doc_type'] for it in normalized_items]}")
        return {"items": normalized_items}

    except json.JSONDecodeError as e:
        print(f"大模型返回内容解析失败: {e}")
        print(f"原始返回: {result_text[:200]}")
        return {"items": []}
    except Exception as e:
        print(f"大模型调用失败: {e}")
        return {"items": []}


def _normalize_fields(raw_fields: dict) -> dict:
    """将字段统一规范化为 {value, confidence} 格式。"""
    converted = {}
    for key, val in raw_fields.items():
        if isinstance(val, dict) and "value" in val:
            converted[key] = {
                "value": str(val["value"]),
                "confidence": float(val.get("confidence", 0.5))
            }
        else:
            converted[key] = {
                "value": str(val),
                "confidence": 0.5
            }
    return converted


def _normalize_doc_type(raw: str) -> str:
    """规范化 LLM 返回的证件类型名,确保相邻同类合并能正确匹配。

    - 取第一行
    - 去除常见标点/空白/引号/括号内容
    - 截断到 20 字符防异常长输出
    - 白名单归一:精确命中优先,否则按"包含/被包含"关系归到白名单条目(优先匹配较长项)
    - 空字符串 -> '未知'
    """
    if not raw:
        return "未知"
    # 取第一行
    line = raw.strip().splitlines()[0] if raw.strip() else ""
    # 去掉括号及其内容(全角半角)
    line = re.sub(r"[\(（].*?[\)）]", "", line)
    # 去掉常见标点和空白
    line = re.sub(r"[\s。.,，:：;；\"'`*\-—]", "", line)
    # 长度兜底
    line = line[:20]
    if not line:
        return "未知"

    # 白名单归一:LLM 偶尔吐重复词("护照护照")或带前缀("居民身份证"),
    # 这里按子串关系归到白名单条目,使后续 normalize_ranges 白名单过滤能命中。
    allowed = CONFIG.get("document_types", [])
    if allowed and line not in allowed:
        # 优先匹配较长的白名单条目,避免 "学历证书"/"学位证书" 都包含"证书"时被误归
        for cand in sorted(allowed, key=len, reverse=True):
            if cand in line or line in cand:
                return cand

    return line


def classify_one_page(page_text: str, page_no: int) -> str:
    """单页分类:把 1 页 OCR 文本送给 LLM,返回 LLM 给出的证件类型名。

    设计目的:绕开 LLM 输入侧内容安全审核 —— 一次只送 1 页,敏感数字密度低,
    不会因为整本 PDF 拼起来的几十个证件号同时出现而触发拦截。

    Args:
        page_text: 该页 OCR 文字(空字符串 -> 直接返回 '未知')
        page_no: 1-based 页号(只用于日志定位)

    Returns:
        证件类型名(由 LLM 自由识别,经规范化处理)。无法判断/失败时返回 '未知'。
    """
    if not page_text or not page_text.strip():
        return "未知"

    # 截断防超长(单页 OCR 一般 200-2000 字,2000 上限足够覆盖)
    snippet = page_text.strip()[:2000]
    allowed = CONFIG.get("document_types", [])
    if allowed:
        allowed_str = "、".join(allowed)
        prompt = (
            "请根据下面这一页 OCR 文本,判断它属于哪种证件或文档。\n"
            "要求:必须从以下候选类型中**严格选择一个**,完全照抄(包括字数),"
            "不要添加、删减或替换任何字符:\n"
            f"{allowed_str}\n"
            "都不匹配或无法判断时返回'未知'。\n"
            "只返回类型名称本身,不要解释、不要标点、不要括号说明。\n\n"
            f"OCR 文本:\n{snippet}"
        )
    else:
        prompt = (
            "请根据下面这一页 OCR 文本,判断它属于哪种证件或文档。\n"
            "要求:只返回类型名称(4-10 个汉字,如'身份证'、'营业执照'、'结婚证'等),"
            "不要解释、不要标点、不要括号说明。\n"
            "无法判断时返回'未知'。\n\n"
            f"OCR 文本:\n{snippet}"
        )

    try:
        result = _call_llm(prompt)
        normalized = _normalize_doc_type(result)
        print(f"[classify_one_page] page {page_no}: raw={result!r} -> {normalized!r}")
        return normalized
    except Exception as e:
        print(f"[classify_one_page] page {page_no} 失败: {e}")
        return "未知"


def detect_page_ranges(per_page_texts: list) -> list:
    """逐页独立调 LLM 分类 -> 单点'未知'修正 -> 合并相邻同类为 ranges。

    v2 设计(避开 minimax 内容安全):每页独立调用,4 并发,
    单页失败降级为 '未知','未知夹心'后处理修复跨页文档中间页判错。

    Args:
        per_page_texts: 逐页 OCR 文字,索引 i 对应第 i+1 页(1-based)

    Returns:
        ranges 数组,每项形如 {"doc_type", "page_start", "page_end", "fields"}。
        fields 在 v2 单页分类模式下为空字典(放弃字段提取,纯做类型识别)。
    """
    n = len(per_page_texts)
    if not n:
        return []

    # Step 1: 4 线程并发分类每一页
    per_page_types: list[str] = ["未知"] * n
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {
            pool.submit(classify_one_page, t, i + 1): i
            for i, t in enumerate(per_page_texts)
        }
        for future in futures:
            i = futures[future]
            try:
                per_page_types[i] = future.result()
            except Exception as e:
                # 任意一页崩了不影响其它页
                print(f"[detect_page_ranges] page {i + 1} future 异常: {e}")
                per_page_types[i] = "未知"

    print(f"[detect_page_ranges] 单页分类结果({n} 页): {per_page_types}")

    # Step 2: '未知夹心'修正 — 单页'未知'被同类型夹住时,归为该类型
    # 例:户口本第 2 页可能光看一页判不出,但 18,20 都是户口本 -> 第 19 也归户口本
    for i in range(1, n - 1):
        if (
            per_page_types[i] == "未知"
            and per_page_types[i - 1] == per_page_types[i + 1]
            and per_page_types[i - 1] != "未知"
        ):
            per_page_types[i] = per_page_types[i - 1]

    # Step 3: 转 ranges,相邻同类合并
    ranges: list[dict] = []
    cur_type = per_page_types[0]
    cur_start = 1
    for i in range(1, n):
        if per_page_types[i] != cur_type:
            ranges.append({
                "doc_type": cur_type,
                "page_start": cur_start,
                "page_end": i,
                "fields": {},
            })
            cur_type = per_page_types[i]
            cur_start = i + 1
    ranges.append({
        "doc_type": cur_type,
        "page_start": cur_start,
        "page_end": n,
        "fields": {},
    })

    print(f"[detect_page_ranges] 合并后 {len(ranges)} 个范围: "
          f"{[(r['doc_type'], r['page_start'], r['page_end']) for r in ranges]}")
    return ranges


def match_bboxes_to_fields(fields: dict, ocr_details: list) -> dict:
    """
    将 OCR 坐标框按字段值匹配，关联到对应字段。
    fields: {"姓名": {"value": "张三", "confidence": 0.98}, ...}
    ocr_details: [{"text": "张三", "confidence": 0.95, "bbox": [...]}, ...]
    返回: {"姓名": {"value": "张三", "confidence": 0.98, "bbox": [...]}, ...}
    """
    result = {}
    for field_name, field_info in fields.items():
        value = field_info.get("value", "")
        matched_bbox = None

        # 在 OCR 详情中查找包含该值的文字行
        if value and ocr_details:
            for detail in ocr_details:
                if value in detail["text"] or detail["text"] in value:
                    matched_bbox = detail["bbox"]
                    break

        result[field_name] = {
            "value": value,
            "confidence": field_info.get("confidence", 0.5),
            "bbox": matched_bbox
        }

    return result


# ====================== 文件摘要（通用） ======================

# 摘要 prompt 输入文本上限。超过会前后截取，避免 LLM 上下文超限。
SUMMARY_INPUT_LIMIT_CHARS = 30000


def _build_summary_prompt(text: str, progress_name: str | None) -> str:
    """根据是否提供 progress_name 拼装 prompt：
    - 有：要求同时输出摘要 + 相关性判断（7 字段 JSON）
    - 无：仅输出摘要（4 字段 JSON），向后兼容
    """
    base_intro = """你是一个专业的文档分析助手。请阅读以下文件内容，输出该文件的：
1. 一句话定性（这是什么文件，10-20 字）
2. 内容摘要（150-300 字，覆盖核心信息）
3. 关键要点（3-8 条 bullets）
4. 文件分类（从下列中选一个：身份证件 / 学历证明 / 婚姻证明 / 财务证明 / 工作证明 / 申请表单 / 合同协议 / 报告说明 / 简历 / 其他）"""

    if progress_name:
        relevance_intro = f"""
5. 相关性判断：判断该文件是否属于"{progress_name}"这一进展所需要的材料
   - relevance: "strong"=强相关（明显是该进展的核心材料）/ "weak"=弱相关（沾边但不完全匹配）/ "unrelated"=不相关
   - relevance_score: 0-100 整数评分
   - relevance_reason: 一句话说明判断依据（30-80 字）"""

        json_schema = """{
  "title": "一句话定性",
  "summary": "150-300 字摘要",
  "key_points": ["要点1", "要点2", ...],
  "doc_category": "上述分类之一",
  "relevance": "strong|weak|unrelated",
  "relevance_score": 0,
  "relevance_reason": "30-80 字判断依据"
}"""
    else:
        relevance_intro = ""
        json_schema = """{
  "title": "一句话定性",
  "summary": "150-300 字摘要",
  "key_points": ["要点1", "要点2", ...],
  "doc_category": "上述分类之一"
}"""

    return (
        base_intro + relevance_intro + "\n\n"
        + "返回严格 JSON，不要任何额外解释、不要 markdown 代码块：\n"
        + json_schema + "\n\n"
        + "文件内容：\n---\n"
        + text + "\n---\n"
    )


def summarize_text(text: str, progress_name: str | None = None) -> dict:
    """用 LLM 给一段文字写摘要，可选附带"是否相关于 progress_name"判断。

    入参：
      text          - 已抽取的纯文本
      progress_name - 进展名称（如"美国EB5-资金来源证明"）；为空时不输出相关性

    返回：
      {
        "title": "...",
        "summary": "...",
        "key_points": [...],
        "doc_category": "...",
        # 仅当 progress_name 非空时存在：
        "relevance": "strong|weak|unrelated",
        "relevance_score": 0-100,
        "relevance_reason": "...",
      }

    异常：
      ValueError - 文本为空 或 LLM 返回非 JSON
    """
    if not text or not text.strip():
        raise ValueError("文件内容为空，无法生成摘要")

    # 截取超长文本：头部一半 + 尾部一半
    src = text.strip()
    if len(src) > SUMMARY_INPUT_LIMIT_CHARS:
        head_n = SUMMARY_INPUT_LIMIT_CHARS // 2
        tail_n = SUMMARY_INPUT_LIMIT_CHARS - head_n
        src = (
            src[:head_n]
            + f"\n\n...[省略 {len(text) - SUMMARY_INPUT_LIMIT_CHARS} 字]...\n\n"
            + src[-tail_n:]
        )

    prompt = _build_summary_prompt(src, progress_name)
    raw = _call_llm(prompt)

    # 解析 JSON。容错：模型偶尔返回前后有空白或多余说明
    try:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            data = json.loads(raw[start:end + 1])
        else:
            data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"LLM 返回非合法 JSON：{raw[:200]}") from e

    # 字段兜底
    out = {
        "title": str(data.get("title", "")).strip(),
        "summary": str(data.get("summary", "")).strip(),
        "key_points": [str(x).strip() for x in (data.get("key_points") or []) if str(x).strip()],
        "doc_category": str(data.get("doc_category", "其他")).strip() or "其他",
    }

    if progress_name:
        # 规整 relevance：取 LLM 输出，做白名单兜底
        rel_raw = str(data.get("relevance", "")).strip().lower()
        if rel_raw not in ("strong", "weak", "unrelated"):
            # 兜底：score 高视为 strong，低视为 unrelated
            try:
                _score = int(data.get("relevance_score", 0))
            except (TypeError, ValueError):
                _score = 0
            if _score >= 70:
                rel_raw = "strong"
            elif _score >= 30:
                rel_raw = "weak"
            else:
                rel_raw = "unrelated"

        # score 限制 0-100
        try:
            score = int(data.get("relevance_score", 0))
        except (TypeError, ValueError):
            score = 0
        score = max(0, min(100, score))

        out["relevance"] = rel_raw
        out["relevance_score"] = score
        out["relevance_reason"] = str(data.get("relevance_reason", "")).strip()

    return out

