"""
LLM 服务模块
调用大模型进行证件类型检测和结构化提取（含置信度）。
"""

import os
import json
import re
import threading
from typing import Optional
from concurrent.futures import ThreadPoolExecutor
import httpx
from openai import OpenAI

# 配置文件路径
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config.json")

# 全局配置
CONFIG = {}

# 进程级共享 OpenAI 客户端,避免每次调用都做 TLS 握手 + 新建连接池
# 由 _get_client() 在 load_config 之后懒初始化
_client: OpenAI | None = None
_client_lock = threading.Lock()

# LLM 调用超时(秒):上游卡死时单 worker 不会被永久占住
LLM_CONNECT_TIMEOUT = 10
LLM_READ_TIMEOUT = 60
LLM_WRITE_TIMEOUT = 30
LLM_POOL_TIMEOUT = 10


def load_config():
    """从项目根目录的 config.json 加载配置。"""
    global CONFIG, _client
    if not os.path.exists(CONFIG_PATH):
        raise FileNotFoundError(f"配置文件不存在: {CONFIG_PATH}")
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        CONFIG = json.load(f)
    # 配置变了,重置客户端,让下次 _get_client 重建
    with _client_lock:
        _client = None


def _get_client() -> OpenAI:
    """懒加载共享 OpenAI 客户端。带超时配置,防止上游卡死把 worker 占住。"""
    global _client
    if _client is not None:
        return _client
    with _client_lock:
        if _client is not None:
            return _client
        llm = CONFIG.get("llm", {})
        api_key = llm.get("api_key", "")
        if not api_key:
            raise ValueError("未配置大模型 API Key")
        _client = OpenAI(
            base_url=llm.get("base_url", ""),
            api_key=api_key,
            timeout=httpx.Timeout(
                connect=LLM_CONNECT_TIMEOUT,
                read=LLM_READ_TIMEOUT,
                write=LLM_WRITE_TIMEOUT,
                pool=LLM_POOL_TIMEOUT,
            ),
        )
        return _client


def _call_llm(prompt: str, max_retries: int = 3) -> str:
    """调用大模型 API，返回原始响应文本。支持重试机制。"""
    llm = CONFIG.get("llm", {})
    client = _get_client()

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
                # 记录限流事件(避免在 _call_llm 顶部 import 造成循环 import,这里延迟 import)
                try:
                    import event_service
                    event_service.log_event(
                        event_service.WARN,
                        event_service.CATEGORY_LLM_TIMEOUT,
                        f"LLM 限流 429,第 {attempt + 1}/{max_retries} 次重试,等待 {wait_time}s",
                        context={"attempt": attempt + 1, "max_retries": max_retries, "wait_sec": wait_time},
                    )
                except Exception:
                    pass
                time.sleep(wait_time)
            else:
                # 非限流错误或最后一次重试，直接抛出
                # 末次失败记一条事件,便于追踪
                if attempt == max_retries - 1:
                    try:
                        import event_service
                        event_service.log_event(
                            event_service.WARN,
                            event_service.CATEGORY_LLM_TIMEOUT,
                            f"LLM 调用失败({max_retries} 次重试用尽):{str(e)[:200]}",
                            context={
                                "attempt": attempt + 1,
                                "max_retries": max_retries,
                                "error_class": e.__class__.__name__,
                                "error_msg": str(e)[:300],
                            },
                        )
                    except Exception:
                        pass
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


# ==================== 文件留底检测 ====================

ARCHIVE_DETECT_INPUT_LIMIT_CHARS = 30000

# 公司真实文件留底分类体系(基于业务方提供的售后客户文件留底要求)
ARCHIVE_CATEGORIES_FULL = """
【递交前阶段应上传的 5 大类】
A. 客户基础文件:护照、身份证、中文信息表、个人简历、出生证明类文件、户口本、毕业证书&学位证书、结婚证&离婚证等婚姻状态文件、港澳通行证、房产证、工作证明信等
B. 客户个人文件:职业&专业证书、照片、社保/个税、不反对移民申明、无犯罪记录证明、体检类文件、地址证明、在读证明、资产/资信文件、公证/认证文件、成就类文件、学生签证、成绩单、疫苗本、翻译类文件、证书或获奖记录、客户访校指南、单身证明、个人文件等
C. 客户公司文件:营业执照、章程、股东会决议、验资报告、公司财报/审计报告、银行流水、业务合同/合作协议、组织架构图、股东名册、雇佣合同、公司介绍、办公室照片、办公室租赁合同等
D. 其他备用文件:律师文件、投资文件、批复留底文件、使馆申请表、合规部KYC留底文件、开户KYC文件、投资证明、购房文件、入境处申请表格、劳工卡申请、正签信、工作签证留底、商业计划书等
E. 转款凭证:服务过程中涉及的转账凭证等

【递交后阶段应上传的 4 大类】
F. 文案制作的递交文件类:递交全套留底、递交后补料留底等
G. 获批/失败:筛选/名额通知函、使馆信、入境处信件、批复函、录取通知、补料信、打款通知、体检通知、获身份文件、拒签信、撤案信等任何客户相关批复类文件
H. 其他文件:客户获批后协助客户留存的重要证件信息,如更新护照、入境小白条等
I. 停滞/放弃类文件:客户明确表示撤案、不再继续办理、不启动了/放弃办理了等主观原因不再继续办理项目的邮件、聊天等截图
"""

ARCHIVE_CATEGORIES_SIMPLE = """
A. 客户基础文件 / B. 客户个人文件 / C. 客户公司文件 / D. 其他备用文件 / E. 转款凭证 /
F. 文案制作的递交文件类 / G. 获批/失败 / H. 其他文件 / I. 停滞/放弃类文件
"""


def _build_archive_detect_prompt(
    text: str,
    user_prompt: str,
    stage: Optional[str] = None,
) -> str:
    """把用户输入的多行判定标准拼接进 LLM prompt。

    stage: None(匿名模式)用简化版分类; "pre_submit" / "post_submit" 用完整分类树 + 阶段提醒。
    输出 verdict 三态(match/partial/mismatch) + match_score(0-100)。
    is_archival/confidence 由 service 层从 verdict/match_score 推导。
    """
    if stage in ("pre_submit", "post_submit"):
        cat_block = ARCHIVE_CATEGORIES_FULL
        stage_label = "递交前" if stage == "pre_submit" else "递交后"
        stage_hint = f"\n当前阶段: {stage} ({stage_label})\n"
    else:
        cat_block = ARCHIVE_CATEGORIES_SIMPLE
        stage_hint = ""

    return (
        "你是一个公司文件留底审核助手。请根据下方公司分类标准 + 用户判定提示词,审核文件。\n\n"
        "---用户判定标准开始---\n"
        f"{user_prompt}\n"
        "---用户判定标准结束---\n\n"
        "---公司分类标准开始---"
        f"{stage_hint}{cat_block}\n"
        "---公司分类标准结束---\n\n"
        "重要判定指南:\n"
        "- 同一客户的文件集合中,属于该客户配偶、子女、父母、共同申请人的文件也视为相关。"
        "不要因为文件上的人名与客户姓名不一致而判为 mismatch。"
        "只要文件内容与项目类型、进展阶段匹配,且可归入分类体系,即视为符合。\n"
        "- 如果一份文件明显是某类证件的标准格式(如身份证、护照、房产证),"
        "即使 OCR 提取质量差、部分文字乱码,也应判为 match 或 partial,不要因 OCR 噪声判 mismatch。\n\n"
        "请输出:\n"
        "1. verdict: 三选一\n"
        "   - \"match\"   : 文件可明确归入上述某个分类,且符合用户判定标准\n"
        "   - \"partial\" : 可归类但部分指标不匹配(如阶段轻微错配)\n"
        "   - \"mismatch\": 无法归入任何分类,或与判定标准明显不符\n"
        "2. match_score: 0-100 整数\n"
        "3. doc_category: 字母编号+子类名, 如 \"A-护照\"、\"G-批复函\"。若无法精确到子类,只填字母编号+大类名如 \"A-客户基础文件\"\n"
        "4. reason: 30-120 字判断依据,仅引用与分类标准/用户判定相关的内容;"
        "**不要泄露金额、电话、身份证号、银行卡号、账号等敏感信息,遇到这类内容请用 [金额]/[手机号]/[身份证]/[银行卡] 等占位词代替**\n"
        "5. key_points: 3-6 条要点 bullets,遵循同样的脱敏要求\n\n"
        "返回严格 JSON,不要 markdown 代码块:\n"
        '{"verdict": "match", "match_score": 0, "doc_category": "A-护照", "reason": "...", "key_points": ["..."]}\n\n'
        "文件内容：\n---\n"
        f"{text}\n---\n"
    )


def detect_large_table_doc(text: str) -> dict:
    """判断一份文档是不是"大量表格数据为主"的材料(银行流水/社保/证券流水)。

    入参 text: 文档前 2 页 OCR 文本(几百到几千字)。

    返回:
      {
        "is_large_table": bool,
        "doc_type": "bank_statement" | "social_security" | "securities" | "other",
        "confidence": int 0-100,
        "_fallback": bool   # 仅在 LLM 调用失败时为 True
      }

    设计:
    - 失败/超时不抛错。按既定策略,LLM 抽风时**也返回 is_large_table=True**,
      让上游 text_extractor 走采样路径(保速度,牺牲少量精度)。
    - 走共享 _openai_client + _call_llm 的现有 429 重试机制。
    """
    fallback = {
        "is_large_table": True,
        "doc_type": "unknown",
        "confidence": 0,
        "_fallback": True,
    }
    if not text or not text.strip():
        return fallback

    # 输入限长:前 2 页文字最多取 4000 字符,够 LLM 判断
    src = text.strip()
    if len(src) > 4000:
        src = src[:4000]

    prompt = f"""你是文档分类助手。下面是某文件前 2 页 OCR 文本。
判断这是不是"以大量表格数据为主"的材料,典型包括:
  - 银行流水/银行对账单/账户明细
  - 社保缴纳记录/公积金明细
  - 证券账户流水/股票交易记录

特征: 首页含账户号/户名/查询期间等头部信息,后续页基本是逐条交易/缴纳记录。

**反例(以下情况一律 is_large_table=false,即使页数很多)**:
  - 护照(含个人页/签证页/签证戳/出入境章)
  - 身份证/驾照/居住证等证件扫描合集
  - 户口本/出生证/结婚证扫描合集
  - 学历/学位证书、成绩单、获奖证书
  - 合同/协议/公证文书
  - 简历/工作证明/在职证明
  - 房产证、产权证、租赁合同
  - 任何含多个独立成员资料的文档组(比如全家护照合集、家庭成员身份证集合)

判别要点:大表类的中间页价值低(重复交易明细);
非大表类的每一页都可能有独立关键信息(每个家庭成员、每张证件)。
拿不准时一律 false,不要漏识别。

严格输出 JSON(不要 markdown 代码块,不要任何解释):
{{
  "is_large_table": true/false,
  "doc_type": "bank_statement" | "social_security" | "securities" | "other",
  "confidence": 0-100
}}

文本:
---
{src}
---"""

    try:
        raw = _call_llm(prompt, max_retries=2)   # 不重试太多,失败就降级
        # 容错 JSON 解析
        raw = raw.strip()
        if raw.startswith("{") is False:
            # 找出第一个 { 和最后一个 }
            lb, rb = raw.find("{"), raw.rfind("}")
            if lb >= 0 and rb > lb:
                raw = raw[lb : rb + 1]
        data = json.loads(raw)
        return {
            "is_large_table": bool(data.get("is_large_table", False)),
            "doc_type": str(data.get("doc_type", "other")),
            "confidence": int(data.get("confidence", 0)),
            "_fallback": False,
        }
    except Exception as e:
        print(f"[detect_large_table_doc] LLM 调用/解析失败,降级采样: {e}")
        return fallback


def detect_archival(text: str, user_prompt: str, stage: Optional[str] = None) -> dict:
    """以用户提供的判定标准判断 text 符合程度（三态 verdict）。

    入参：
      text        - 已抽取的纯文本（OCR 或 docx 抽取）
      user_prompt - 用户输入的多行判定标准（必填）
      stage       - None(匿名/简化版分类) | "pre_submit" | "post_submit"(完整分类树+阶段提醒)

    返回：
      {
        "verdict": "match"|"partial"|"mismatch",
        "match_score": int 0-100,
        "is_archival": bool,           # = (verdict == "match"),向后兼容
        "confidence": int 0-100,       # = match_score,向后兼容
        "reason": str (LLM 已被 prompt 要求脱敏；服务层还会再用 redactor 兜底),
        "key_points": list[str] (同上),
        "doc_category": str,
      }

    异常：
      ValueError - 文本为空 / user_prompt 为空 / LLM 返回非 JSON
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

    prompt = _build_archive_detect_prompt(src, user_prompt.strip(), stage=stage)
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

    # ---- 字段兜底 ----

    # 1) verdict：白名单三态，兜底 mismatch
    raw_v = str(data.get("verdict", "")).strip().lower()
    if raw_v not in ("match", "partial", "mismatch"):
        raw_v = "mismatch"
    verdict = raw_v

    # 2) match_score：钳位 0-100
    try:
        match_score = int(data.get("match_score", 0))
    except (TypeError, ValueError):
        match_score = 0
    match_score = max(0, min(100, match_score))

    # 3) 向后兼容推导
    is_archival = (verdict == "match")
    confidence = match_score

    return {
        "verdict": verdict,
        "match_score": match_score,
        "is_archival": is_archival,
        "confidence": confidence,
        "reason": str(data.get("reason", "")).strip(),
        "key_points": [str(x).strip() for x in (data.get("key_points") or []) if str(x).strip()],
        "doc_category": str(data.get("doc_category", "其他")).strip() or "其他",
    }


# ==================== 批次总报告生成 ====================


def _build_summarize_batch_prompt(
    files_brief: list,
    user_prompt: str,
    overall_verdict: str,
    overall_score: int,
) -> str:
    """批次总报告 prompt:输入各文件简要明细 + 已算好的规则汇总结论,输出 80-200 字总体说明文本。"""
    lines = []
    for i, f in enumerate(files_brief, 1):
        kp = " | ".join(f.get("key_points") or [])
        lines.append(
            f"- 文件{i} 「{f.get('filename') or '?'}」: "
            f"verdict={f.get('verdict')}, score={f.get('match_score')}, "
            f"类别={f.get('doc_category') or '?'}, "
            f"reason={f.get('reason') or ''}"
            + (f", 要点={kp}" if kp else "")
        )
    detail = "\n".join(lines) if lines else "（无符合 done 状态的文件）"

    return (
        "你是一个文档留底审核总结助手。请基于以下信息撰写一段总体审核说明:\n\n"
        f"用户判定标准:\n{user_prompt}\n\n"
        f"规则汇总结论: verdict={overall_verdict}, 综合匹配度={overall_score}\n\n"
        f"文件检测明细(共 {len(files_brief)} 条):\n{detail}\n\n"
        "请围绕规则结论,用 80-200 字中文描述总体审核情况,包含:\n"
        "① 整体符合度评价(贴合 verdict)\n"
        "② 主要问题点(若有 mismatch / partial 文件,简要点出问题)\n"
        "③ 关键发现(突出共性或重要差异)\n\n"
        "**脱敏要求**:不要泄露金额、电话、身份证号、银行卡号、账号等敏感信息,"
        "遇到这类内容请用 [金额]/[手机号]/[身份证]/[银行卡] 等占位词代替。\n\n"
        "直接返回纯文本(不要 JSON、不要 markdown、不要前后缀说明文字):\n"
    )


def summarize_batch(
    files_brief: list,
    user_prompt: str,
    overall_verdict: str,
    overall_score: int,
) -> str:
    """对一个 batch 内所有已 done 文件生成总体审核说明文本。

    入参:
      files_brief - list[dict],每条含 {filename, verdict, match_score, doc_category, reason, key_points}
      user_prompt - 用户的审核判定标准
      overall_verdict - 规则汇总结论 match/partial/mismatch
      overall_score - 规则汇总平均分 0-100

    返回:
      str - 80-200 字总体说明纯文本(未脱敏,调用方再 redact 兜底)
    """
    prompt = _build_summarize_batch_prompt(files_brief, user_prompt, overall_verdict, overall_score)
    raw = _call_llm(prompt)
    # 简单清理:去掉首尾空白和潜在 markdown 代码块标记
    out = (raw or "").strip()
    if out.startswith("```"):
        out = out.strip("`")
        # 去掉可能的语言标识符
        if "\n" in out:
            out = out.split("\n", 1)[1]
        out = out.strip()
    return out


# ==================== 客户资料结构化抽取 ====================


def _build_client_profile_prompt(ocr_text: str, filename: str, doc_category: str) -> str:
    return (
        "你是移民客户档案结构化助手。请从单个客户文件 OCR 文本中抽取可写入 PostgreSQL 的结构化事实。\n"
        "只抽取文本中明确出现的信息；不确定不要编造；日期尽量规范为 YYYY-MM-DD；金额只保留数字和币种。\n\n"
        f"文件名:{filename or ''}\n"
        f"文件分类:{doc_category or ''}\n\n"
        "请返回严格 JSON，不要 markdown，结构如下：\n"
        '{"client_basic":{"name_en":"","gender":"","birth_date":"YYYY-MM-DD","birth_place":"","nationality":"","id_number":"","passport_no":"","passport_expiry_date":"YYYY-MM-DD","marital_status":""},'
        '"family_members":[{"relation":"child|spouse|parent|other","name":"","gender":"","birth_date":"YYYY-MM-DD","nationality":"","id_number":"","passport_no":"","birth_cert_no":"","birth_place":""}],'
        '"assets":[{"asset_type":"deposit|bank_statement|property|stock|vehicle|other","asset_name":"","owner_name":"","value_amount":null,"currency":"","bank_name":"","account_no":"","location_address":"","certificate_no":""}],'
        '"extra_info":[{"key":"","value":""}],"confidence_notes":["..."]}\n\n'
        "OCR 文本:\n---\n"
        f"{ocr_text}\n---\n"
    )


def extract_client_profile_facts(ocr_text: str, filename: str = "", doc_category: str = "") -> dict:
    """从单个文件 OCR 文本中抽取客户档案结构化事实。"""
    if not ocr_text or not ocr_text.strip():
        raise ValueError("OCR 文本为空，无法抽取客户档案")
    src = ocr_text.strip()
    if len(src) > ARCHIVE_DETECT_INPUT_LIMIT_CHARS:
        head_n = ARCHIVE_DETECT_INPUT_LIMIT_CHARS // 2
        tail_n = ARCHIVE_DETECT_INPUT_LIMIT_CHARS - head_n
        src = src[:head_n] + f"\n\n...[省略 {len(ocr_text) - ARCHIVE_DETECT_INPUT_LIMIT_CHARS} 字]...\n\n" + src[-tail_n:]

    raw = _call_llm(_build_client_profile_prompt(src, filename, doc_category))
    try:
        start = raw.find("{")
        end = raw.rfind("}")
        data = json.loads(raw[start:end + 1] if start >= 0 and end > start else raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"LLM 返回非合法客户档案 JSON：{raw[:200]}") from e

    return {
        "client_basic": data.get("client_basic") or {},
        "family_members": data.get("family_members") or [],
        "assets": data.get("assets") or [],
        "extra_info": data.get("extra_info") or [],
        "confidence_notes": data.get("confidence_notes") or [],
    }

