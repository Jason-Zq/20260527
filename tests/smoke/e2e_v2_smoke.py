"""端到端 v2 后端流程测试：parse → quick-save → map-client → generate"""
import sys, os, json, requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

BASE = "http://127.0.0.1:8765"

# 1) parse
print("=== 1) parse ===")
with open(os.path.join(os.path.dirname(__file__), "..", "..", "POA 信息表.docx"), "rb") as f:
    r = requests.post(BASE + "/api/templates/parse", files={"file": ("POA 信息表.docx", f)}, timeout=600)
r.raise_for_status()
data = r.json()
token = data["temp_token"]
print("temp_token:", token)
print("anchors count:", len(data.get("anchors", [])))
print("first anchor:", json.dumps(data["anchors"][0], ensure_ascii=False)[:120] if data.get("anchors") else "EMPTY")
assert "anchors" in data, "v2 应返回 anchors 字段"
assert len(data["anchors"]) > 0, "扫到 0 anchor 异常"

# 2) quick-save
print("\n=== 2) quick-save ===")
r = requests.post(BASE + "/api/templates/quick-save", json={
    "name": "v2_e2e_poa",
    "filename": "POA 信息表.docx",
    "temp_token": token,
}, timeout=60)
r.raise_for_status()
qs = r.json()
tpl_id = qs["id"]
print(f"new template id: {tpl_id}, placeholders: {qs['placeholder_count']}")
assert qs["placeholder_count"] > 0

# 3) detail 看 anchor 形态
print("\n=== 3) detail ===")
r = requests.get(BASE + f"/api/templates/{tpl_id}")
r.raise_for_status()
detail = r.json()
phs = detail["placeholders"]
print(f"placeholders: {len(phs)}")
print(f"first: id={phs[0]['id']} kind={phs[0]['anchor']['kind']} field_hint={phs[0].get('field_hint')}")
assert phs[0].get("anchor", {}).get("kind") in ("cell", "run", "paragraph"), "anchor 格式错"

# 4) map-client（用客户 id=1：王余来）
print("\n=== 4) map-client ===")
r = requests.post(BASE + f"/api/templates/{tpl_id}/map-client", json={"client_id": 1}, timeout=120)
r.raise_for_status()
mc = r.json()
print("matched:", json.dumps(mc.get("matched", {}), ensure_ascii=False)[:200])
print("unmatched:", mc.get("unmatched", []))
print("from_cache:", mc.get("from_cache"))

# 5) generate
print("\n=== 5) generate ===")
# 用 map 的结果填值
values = {ph["id"]: mc["matched"].get(ph["id"], "") for ph in phs}
# 留空未匹配项
r = requests.post(BASE + f"/api/templates/{tpl_id}/generate", json={
    "client_id": 1,
    "anchor_values": values,
}, timeout=180)
print("status:", r.status_code)
print("X-Fallback-Docx:", r.headers.get("x-fallback-docx"))
print("Content-Type:", r.headers.get("content-type"))
print("body size:", len(r.content))
ext = ".pdf" if r.headers.get("x-fallback-docx") == "0" else ".docx"
out_path = os.path.join(os.path.dirname(__file__), "..", "..", "output", f"_e2e_v2_output{ext}")
with open(out_path, "wb") as f:
    f.write(r.content)
print("saved:", out_path)
print(f"\n>>> E2E PASS, template_id={tpl_id}, output={ext}")