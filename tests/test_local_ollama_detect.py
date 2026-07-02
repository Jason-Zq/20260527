"""本地 Ollama 模型效果验证脚本(独立,不改生产代码 / 不改 config.json)。

从本地 PG 的 archive_detect_files 取一条真实 OCR 文本,套用生产同款
_build_archive_detect_prompt,打到本地 Ollama,打印原始返回 + 解析 JSON + 耗时。

用法(在项目根,已下好 ollama pull qwen2.5:7b):
  cd e:/qoderproject/20260527
  PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_local_ollama_detect.py

可选参数:
  --model qwen2.5:7b                  Ollama 模型名(默认 qwen2.5:7b)
  --base-url http://localhost:11434/v1  Ollama OpenAI 兼容地址
  --file-id 123                       指定 archive_detect_files.id(默认取最新一条有 ocr_text 的)
  --prompt "..."                      自定义判定标准(默认给一个通用留底判定)
  --stage pre_submit                  None|pre_submit|post_submit(默认 None=简化分类)
  --json-mode                         开启 response_format=json_object(测 Ollama 强制 JSON 效果)
"""
import os
import sys
import re
import json
import time
import argparse
import asyncio

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

import llm_service
from sqlalchemy import select, desc
from db.engine import async_session_maker
from db.models import ArchiveDetectFile

DEFAULT_PROMPT = "帮我检测该文件是否属于该客户该项目进展的留底材料,判断其类别与符合程度。"


def _strip_think(text: str) -> str:
    """剥离推理模型(如 deepseek-r1)的 <think>...</think> 段。qwen2.5-instruct 不受影响。"""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def _parse_json_loose(raw: str) -> dict | None:
    """容错解析:同 detect_archival,取第一个 { 到最后一个 }。"""
    try:
        start, end = raw.find("{"), raw.rfind("}")
        chunk = raw[start:end + 1] if start >= 0 and end > start else raw
        return json.loads(chunk)
    except json.JSONDecodeError:
        return None


async def _fetch_ocr_row(file_id: int | None) -> dict | None:
    """从 PG 取一条有 ocr_text 的记录。file_id 为空则取最新一条。"""
    async with async_session_maker() as session:
        stmt = select(
            ArchiveDetectFile.id,
            ArchiveDetectFile.filename,
            ArchiveDetectFile.ocr_text,
        ).where(ArchiveDetectFile.ocr_text.isnot(None))
        if file_id is not None:
            stmt = stmt.where(ArchiveDetectFile.id == file_id)
        else:
            stmt = stmt.order_by(desc(ArchiveDetectFile.id))
        stmt = stmt.limit(1)
        row = (await session.execute(stmt)).first()
        if not row:
            return None
        return {"id": row.id, "filename": row.filename, "ocr_text": row.ocr_text or ""}


def _call_ollama(client, model: str, prompt: str, json_mode: bool) -> str:
    kwargs = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    resp = client.chat.completions.create(**kwargs)
    return resp.choices[0].message.content.strip()


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen2.5:7b")
    ap.add_argument("--base-url", default="http://localhost:11434/v1")
    ap.add_argument("--file-id", type=int, default=None)
    ap.add_argument("--prompt", default=DEFAULT_PROMPT)
    ap.add_argument("--stage", default=None, choices=[None, "pre_submit", "post_submit"])
    ap.add_argument("--json-mode", action="store_true")
    args = ap.parse_args()

    # 需要 document_types 等配置(_build_archive_detect_prompt 间接用)
    llm_service.load_config()

    # 1) 取真实 OCR 文本
    row = await _fetch_ocr_row(args.file_id)
    if not row:
        print("[ERROR] PG 中没有找到含 ocr_text 的 archive_detect_files 记录。")
        print("        先跑一次业务审核让库里有数据,或用 --file-id 指定。")
        return
    ocr_text = row["ocr_text"]
    print(f"[取样] file_id={row['id']}  filename={row['filename']}  ocr_len={len(ocr_text)}")
    print(f"[取样] OCR 预览: {ocr_text[:120].replace(chr(10), ' ')}...\n")

    # 2) 套用生产同款 prompt(含输入截断)
    src = ocr_text.strip()
    limit = llm_service.ARCHIVE_DETECT_INPUT_LIMIT_CHARS
    if len(src) > limit:
        head_n = limit // 2
        src = src[:head_n] + f"\n\n...[省略 {len(ocr_text) - limit} 字]...\n\n" + src[-(limit - head_n):]
    prompt = llm_service._build_archive_detect_prompt(src, args.prompt.strip(), stage=args.stage)

    # 3) 打到本地 Ollama
    from openai import OpenAI
    client = OpenAI(base_url=args.base_url, api_key="ollama")
    print(f"[调用] model={args.model}  base_url={args.base_url}  json_mode={args.json_mode}")
    t0 = time.time()
    try:
        raw = _call_ollama(client, args.model, prompt, args.json_mode)
    except Exception as e:
        print(f"[ERROR] 调用 Ollama 失败: {e}")
        print("        确认 ollama 已运行、模型已 pull、base_url 正确。")
        return
    elapsed = time.time() - t0

    raw_clean = _strip_think(raw)
    print(f"[耗时] {elapsed:.1f}s\n")
    print("=" * 60)
    print("原始返回:")
    print(raw)
    print("=" * 60)

    # 4) 解析并体检
    data = _parse_json_loose(raw_clean)
    if data is None:
        print("[结果] ❌ JSON 解析失败 —— 7B 在该样本上未产出合法 JSON")
        print("        可加 --json-mode 重试(Ollama 强制 json_object)")
        return
    verdict = str(data.get("verdict", "")).lower()
    print(f"[结果] verdict     = {verdict}  {'✅' if verdict in ('match','partial','mismatch') else '⚠️ 非法值'}")
    print(f"[结果] match_score = {data.get('match_score')}")
    print(f"[结果] doc_category= {data.get('doc_category')}")
    print(f"[结果] reason      = {data.get('reason')}")
    print(f"[结果] key_points  = {data.get('key_points')}")
    print("\n[提示] 重点看:分类对不对、reason 是否切题、有没有泄露敏感原文。")


if __name__ == "__main__":
    asyncio.run(main())
