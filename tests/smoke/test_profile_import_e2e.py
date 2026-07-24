"""客户画像端到端冒烟:登录 -> AI 起草+激活身份证规则 -> 导入真实 Excel -> 轮询到完成 -> 核对画像。

依赖运行中的后端(默认 http://127.0.0.1:8000)、真实 LLM、真实业务方下载接口。
手动运行:

  cd e:/qoderproject/20260527
  PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/smoke/test_profile_import_e2e.py

环境变量:
  BASE             后端地址(默认 http://127.0.0.1:8000)
  EXCEL_PATH       清单文件(默认 仓库根/客户文件信息例子.xlsx)
  POLL_TIMEOUT_SEC 任务轮询超时(默认 2700 秒)
"""
import sys
import os
import time

import httpx

BASE = os.getenv("BASE", "http://127.0.0.1:8000").rstrip("/")
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
EXCEL_PATH = os.getenv("EXCEL_PATH", os.path.join(ROOT, "客户文件信息例子.xlsx"))
POLL_TIMEOUT = int(os.getenv("POLL_TIMEOUT_SEC", "2700"))

sys.path.insert(0, ROOT)


def _login(client: httpx.Client) -> str:
    import json
    cfg = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
    a = cfg["auth"]
    r = client.post(f"{BASE}/api/auth/login",
                    json={"username": a["admin_user"], "password": a["admin_password"]})
    r.raise_for_status()
    data = r.json()
    assert data.get("ok") and data.get("token"), data
    return data["token"]


def main() -> None:
    with httpx.Client(timeout=60) as client:
        token = _login(client)
        client.headers["Authorization"] = f"Bearer {token}"
        print("PASS 登录")

        # ---- 1) 身份证规则:无 active 则 AI 起草并激活 ----
        r = client.get(f"{BASE}/api/doc-extract/rules",
                       params={"doc_type": "id_card", "status": "active"})
        r.raise_for_status()
        if r.json()["total"] > 0:
            rule = r.json()["items"][0]
            print(f"SKIP 已有 active 身份证规则: #{rule['id']} v{rule['version']}")
        else:
            r = client.post(f"{BASE}/api/doc-extract/rules/draft", json={"doc_type": "id_card"},
                            timeout=180)
            assert r.status_code == 200, r.text
            rule = r.json()
            print(f"PASS AI 起草身份证规则: #{rule['id']} v{rule['version']}")
            for f in rule["fields"]:
                col = (f.get("target") or {}).get("column")
                print(f"    - {f['key']} ({f['label']}) -> {col or '(只抽不写)'}")
            print(f"    prompt_extra: {(rule.get('prompt_extra') or '')[:120]}")
            assert any((f.get("target") or {}).get("column") == "id_number" for f in rule["fields"]), \
                "起草规则缺少 id_number 映射"
            r = client.post(f"{BASE}/api/doc-extract/rules/{rule['id']}/activate",
                            json={"reviewed_by": "smoke"})
            assert r.status_code == 200, r.text
            print("PASS 激活规则(smoke 审核)")
        r = client.get(f"{BASE}/api/doc-extract/rules",
                       params={"doc_type": "id_card", "status": "active"})
        assert r.json()["total"] == 1, "active 规则不唯一"
        print("PASS active 规则唯一")

        # ---- 2) 上传 Excel 创建导入任务 ----
        with open(EXCEL_PATH, "rb") as f:
            r = client.post(f"{BASE}/api/profile/import",
                            files={"file": (os.path.basename(EXCEL_PATH), f)},
                            timeout=120)
        assert r.status_code == 200, r.text
        task = r.json()
        task_id = task["task_id"]
        print(f"PASS 创建导入任务 #{task_id}: {task['client_name']} "
              f"共 {task['total_files']} 文件(new={task['new_files']}, relinked={task['relinked_files']})")
        assert task["total_files"] > 50, task

        # ---- 3) 轮询到完成 ----
        t0 = time.time()
        last_line = ""
        poll_errors = 0
        while True:
            try:
                r = client.get(f"{BASE}/api/profile/tasks/{task_id}", timeout=30)
                r.raise_for_status()
                t = r.json()
                poll_errors = 0
            except httpx.HTTPError as e:
                # OCR 高峰偶发连接中断(ReadError/ConnectError/RemoteProtocolError 等),重试即可
                poll_errors += 1
                assert poll_errors < 20, f"连续轮询失败: {e}"
                time.sleep(5)
                continue
            line = (f"  [{int(time.time() - t0)}s] {t['status']} "
                    f"{t['processed_files']}/{t['total_files']} "
                    f"fresh={t['fresh_ocr_count']} reused={t['reused_count']} "
                    f"relinked={t['relinked_count']} failed={t['failed_count']} "
                    f"四类(身份{t['id_card_count']}/户口{t['hukou_count']}"
                    f"/学位{t['degree_cert_count']}/出生{t['birth_cert_count']}) "
                    f"extracted={t['extracted_count']} current={t.get('current_file')}")
            if line != last_line:
                print(line, flush=True)
                last_line = line
            if t["status"] in ("done", "error"):
                break
            assert time.time() - t0 < POLL_TIMEOUT, "任务超时未完成"
            time.sleep(5)
        assert t["status"] == "done", f"任务失败: {t.get('error')}"
        print("PASS 任务完成")

        # ---- 4) 核对 ----
        assert t["processed_files"] == t["total_files"], t
        print(f"PASS 全部文件已处理(failed={t['failed_count']})")

        r = client.get(f"{BASE}/api/profile/tasks/{task_id}/files", params={"limit": 500})
        files = r.json()["items"]
        status_set = {f["status"] for f in files}
        assert status_set <= {"done", "error"}, status_set
        done_files = [f for f in files if f["status"] == "done"]
        with_text = [f for f in done_files if (f.get("char_count") or 0) > 0]
        print(f"PASS 文件清单: done={len(done_files)} error={len(files) - len(done_files)} "
              f"有文本={len(with_text)}")
        assert t["id_card_count"] >= 4, f"身份证筛出太少: {t['id_card_count']}"
        print(f"PASS 筛出 4 类证件: 身份证 {t['id_card_count']} 户口本 {t['hukou_count']} "
              f"学位证 {t['degree_cert_count']} 出生证明 {t['birth_cert_count']}")

        r = client.get(f"{BASE}/api/doc-extract/results",
                       params={"import_task_id": task_id, "status": "done", "limit": 100})
        results = r.json()
        assert results["total"] >= 1, "无成功提取记录"
        print(f"PASS 提取成功 {results['total']} 条")
        r = client.get(f"{BASE}/api/doc-extract/results",
                       params={"import_task_id": task_id, "status": "error", "limit": 100})
        if r.json()["total"]:
            print(f"WARN 提取失败 {r.json()['total']} 条(可查 /api/doc-extract/results?status=error)")

        # ---- 5) 画像 ----
        r = client.get(f"{BASE}/api/profile/tasks/{task_id}/profile")
        p = r.json()
        assert p["client"], "画像无客户"
        c = p["client"]
        print(f"PASS 客户卡: {c.get('name')} 性别={c.get('gender')} "
              f"出生={c.get('birth_date')} 证件号={'有' if c.get('id_number') else '无'}")
        print(f"  家庭成员 {len(p['family_members'])} 个:")
        for m in p["family_members"]:
            print(f"    - [{m.get('relation')}] {m.get('name')} "
                  f"性别={m.get('gender') or '-'} 出生={m.get('birth_date') or '-'} "
                  f"证件号={'有' if m.get('id_number') else '无'}")
        if not p["family_members"]:
            print("WARN 未建立家庭成员(检查提取/归因日志)")

    print("\n端到端冒烟全部通过")


if __name__ == "__main__":
    main()
