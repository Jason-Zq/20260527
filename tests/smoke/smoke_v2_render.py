"""临时冒烟脚本：验证 enrich + batch_apply_anchors。"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import template_service as t
from docx import Document

anchors = t.scan_anchors(os.path.join(os.path.dirname(__file__), "..", "..", "POA 信息表.docx"))
print(f"anchors count: {len(anchors)}")
enriched = t.enrich_anchors_with_llm(anchors[:5], docx_text=None)
for e in enriched:
    print(" ", e.get("field_hint"), "|", (e.get("description") or "")[:30])
print("---")
items = [(e["anchor"], f"V{i}", None) for i, e in enumerate(enriched)]
out_path = os.path.join(os.path.dirname(__file__), "..", "..", "output", "_batch_test.docx")
src = os.path.join(os.path.dirname(__file__), "..", "..", "POA 信息表.docx")
t.batch_apply_anchors(src, items, out_path)
print(f"batch apply OK → size {os.path.getsize(out_path)}")
os.remove(out_path)