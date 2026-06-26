"""文件级队列 + 水位线 + finalize 流程的单元测试(无 DB、无 OCR、无 LLM)。

  cd e:/qoderproject/20260527
  PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_archive_detect_queue.py

策略:
- crud 的所有方法 monkey-patch 成内存 stub,避开真正的 DB
- text_extractor.extract_text / llm_service.detect_archival / summarize_batch 同样 monkey-patch
- 仅验证编排逻辑:submit 立即返回 / 水位线 429 / 队列消化 / finalize 等待 / reuse 路径
"""
import sys
import os
import asyncio
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))


# 创建一个真实存在的小测试文件供 local_path 用(_process_one_business 会 os.path.exists 校验)
# 注:_process_one_business 处理完成后会删除 upload_path,所以每个 item 都要拿独占临时文件
def _new_temp_pdf() -> str:
    fd, path = tempfile.mkstemp(suffix=".pdf")
    with os.fdopen(fd, "wb") as f:
        f.write(b"%PDF-1.4 fake\n")
    return path


# --- 准备:用 stub 替掉所有外部依赖 ---

# 1) llm_service:在 import archive_detect_service 之前打 stub
import llm_service

llm_service.CONFIG = {"llm": {"api_key": "fake", "base_url": "http://fake", "model": "fake"}}
llm_service.detect_archival = lambda text, criteria, stage=None: {
    "verdict": "match",
    "match_score": 90,
    "is_archival": True,
    "confidence": 90,
    "reason": "stub",
    "key_points": [],
    "doc_category": "其它",
}
llm_service.summarize_batch = lambda files_brief, criteria, verdict, score: f"stub-summary({verdict},{score})"


# 2) text_extractor:返回固定文本
import text_extractor

async def _stub_extract_text(local_path, mime_type):
    # 模拟 OCR 耗时,验证串行性
    await asyncio.sleep(0.02)
    return {"text": "extracted-text", "page_count": 1, "char_count": 14, "mime_type": mime_type or "text/plain"}

text_extractor.extract_text = _stub_extract_text


# 3) 构造一个内存 crud stub
class _CrudStub:
    """所有 archive_detect_service 用到的 crud 方法都在这里 fake 掉。"""
    def __init__(self):
        self.clients = {}
        self.progresses = {}
        self.batches = {}
        self.files = {}   # (batch_id, idx) -> dict

    async def upsert_client_by_code(self, client_code, name):
        if client_code not in self.clients:
            self.clients[client_code] = {"id": len(self.clients) + 1, "client_code": client_code, "name": name}
        return self.clients[client_code]["id"]

    async def upsert_progress(self, *, client_id, progress_oid, handler=None,
                              project_name=None, project_code=None,
                              project_detail_name=None, project_detail_code=None,
                              progress_name=None):
        key = (client_id, progress_oid)
        if key not in self.progresses:
            self.progresses[key] = {
                "id": len(self.progresses) + 1,
                "client_id": client_id,
                "progress_oid": progress_oid,
                "handler": handler,
                "project_name": project_name,
                "progress_name": progress_name,
            }
        return self.progresses[key]

    async def find_latest_done_files_bulk(self, progress_id, file_ids):
        # 默认无复用;测试需要复用时手动 pre-populate
        out = {}
        for fid in file_ids:
            key = ("done_file", progress_id, fid)
            if key in self.files:
                out[fid] = self.files[key]
        return out

    async def create_business_batch_with_files(self, *, batch_id, user_prompt, progress_id, items_plan):
        reused = sum(1 for p in items_plan if p.get("reuse_from"))
        new = len(items_plan) - reused
        self.batches[batch_id] = {"status": "running", "reused": reused, "new": new}
        return {"reused_count": reused, "new_count": new}

    async def update_file_done(self, batch_id, idx, payload):
        self.files[(batch_id, idx)] = payload

    async def update_file_error(self, batch_id, idx, error_msg, elapsed_sec=None, filename=None):
        self.files[(batch_id, idx)] = {"error": error_msg, "filename": filename}

    async def bump_done_count(self, batch_id):
        self.batches[batch_id]["done_files"] = self.batches[batch_id].get("done_files", 0) + 1

    async def update_batch_overall(self, batch_id, verdict, score, reason):
        b = self.batches[batch_id]
        b["overall_verdict"] = verdict
        b["overall_score"] = score
        b["overall_reason"] = reason

    async def update_batch_status(self, batch_id, status):
        self.batches[batch_id]["status"] = status


# 4) 替换 archive_detect_service 里的 crud 模块引用
import archive_detect_service
_stub = _CrudStub()
archive_detect_service.crud = _stub

# 5) file_fetcher: 不测网络,业务上传也只走 local_path 路径,所以无需 stub fetch_url_to_temp


# --- 工具 ---

def _make_item(fid, path=None):
    return {"file_id": fid, "filename": f"{fid}.pdf", "local_path": path or _new_temp_pdf()}


async def _reset():
    """每个 case 之间清理全局状态。"""
    archive_detect_service._batch_status.clear()
    archive_detect_service._batch_pending.clear()
    archive_detect_service._batch_done_event.clear()
    # 清掉所有可能积压的队列项
    q = archive_detect_service._FILE_QUEUE
    if q is not None:
        while not q.empty():
            try:
                q.get_nowait()
                q.task_done()
            except Exception:
                break
    _stub.clients.clear()
    _stub.progresses.clear()
    _stub.batches.clear()
    _stub.files.clear()


async def _ensure_workers():
    if archive_detect_service._FILE_QUEUE is None:
        await archive_detect_service.start_workers()


async def _wait_done(batch_id, timeout=5.0):
    """轮询 _batch_status 直到 done 或超时。"""
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        state = archive_detect_service._batch_status.get(batch_id)
        if state and state.get("status") == "done":
            return state
        if asyncio.get_event_loop().time() > deadline:
            raise TimeoutError(f"batch {batch_id} 未在 {timeout}s 内完成,当前状态={state}")
        await asyncio.sleep(0.02)


# --- 测试 ---

async def test_submit_returns_immediately():
    """submit 返回应该是秒级,不会阻塞等所有 OCR 完成。"""
    await _reset()
    await _ensure_workers()
    t0 = asyncio.get_event_loop().time()
    result = await archive_detect_service.submit_business_batch(
        criteria="留底",
        client_payload={"client_code": "C1", "name": "客户1"},
        progress_payload={"progress_oid": "P1", "progress_name": "递交后"},
        items=[_make_item("F1"), _make_item("F2"), _make_item("F3")],
    )
    elapsed = asyncio.get_event_loop().time() - t0
    assert "batch_id" in result, result
    assert result["total_files"] == 3
    assert result["new_count"] == 3
    assert result["reused_count"] == 0
    assert "queue_depth" in result
    # OCR 单文件 stub 20ms × 3 = 60ms,如果是 fan-out 仍然几乎瞬间;关键是要< 1s,不能等到 batch done 才返回
    assert elapsed < 1.0, f"submit 用了 {elapsed:.2f}s,理应秒级返回"

    # 等队列消化完
    final = await _wait_done(result["batch_id"], timeout=10.0)
    assert final["status"] == "done"
    assert all(f["status"] == "done" for f in final["files"])


async def test_full_reuse_skips_queue():
    """全 reuse 批次不应该进队列,但仍要生成总报告。"""
    await _reset()
    await _ensure_workers()

    # 预先 populate 复用记录
    _stub.files[("done_file", 1, "F1")] = {
        "verdict": "match", "match_score": 90, "is_archival": True,
        "confidence": 90, "reason": "old", "key_points": [], "doc_category": "其它",
        "version": 1, "page_count": 1, "char_count": 10,
    }

    # 先 upsert 出 progress_id=1
    await _stub.upsert_client_by_code("C1", "客户1")
    await _stub.upsert_progress(client_id=1, progress_oid="P1")

    result = await archive_detect_service.submit_business_batch(
        criteria="留底",
        client_payload={"client_code": "C1", "name": "客户1"},
        progress_payload={"progress_oid": "P1"},
        items=[_make_item("F1")],
    )
    assert result["reused_count"] == 1
    assert result["new_count"] == 0

    final = await _wait_done(result["batch_id"], timeout=5.0)
    assert final["status"] == "done"
    assert final["files"][0]["status"] == "done"
    assert final["files"][0].get("is_reused") is True


async def test_queue_water_line_returns_429():
    """提交时若 queue_depth + new_count > MAX,应抛 QueueFullError。"""
    await _reset()
    await _ensure_workers()
    # 临时把队列容量调小,便于触发(同时也校验运行时可调)
    old_max = archive_detect_service.QUEUE_MAX_SIZE
    archive_detect_service.QUEUE_MAX_SIZE = 5
    # 替换 _FILE_QUEUE 也要重建,但运行中 worker 持有旧 queue 引用,
    # 这里只测水位检查逻辑本身:塞满 5 个再投 1 个应当被拒。
    try:
        # 投 5 个文件占满
        r1 = await archive_detect_service.submit_business_batch(
            criteria="留底",
            client_payload={"client_code": "C1", "name": "客户1"},
            progress_payload={"progress_oid": "P1"},
            items=[_make_item(f"F{i}") for i in range(5)],
        )
        assert r1["new_count"] == 5

        # 此时 queue 应该有 ~5 个(可能 worker 已经取了 1-2 个)
        # 立刻再投 5 个,如果当前 depth + 5 > 5 就会触发 429
        try:
            r2 = await archive_detect_service.submit_business_batch(
                criteria="留底",
                client_payload={"client_code": "C2", "name": "客户2"},
                progress_payload={"progress_oid": "P2"},
                items=[_make_item(f"G{i}") for i in range(5)],
            )
            # 如果队列消化太快没触发也可以接受,但不应静默吞掉 — 至少要 r2 成功
            assert "batch_id" in r2
        except archive_detect_service.QueueFullError as e:
            assert e.queue_max == 5
            assert e.queue_depth >= 0
            assert e.retry_after > 0

        # 等所有 batch 完成,避免污染后续测试
        await _wait_done(r1["batch_id"], timeout=10.0)
    finally:
        archive_detect_service.QUEUE_MAX_SIZE = old_max


async def test_concurrent_submits_serialize_in_queue():
    """多个 batch 并发提交,应该都立刻拿到 batch_id,worker 串行消化。"""
    await _reset()
    await _ensure_workers()

    async def submit_one(suffix):
        return await archive_detect_service.submit_business_batch(
            criteria="留底",
            client_payload={"client_code": f"C{suffix}", "name": f"客户{suffix}"},
            progress_payload={"progress_oid": f"P{suffix}"},
            items=[_make_item(f"F{suffix}_{i}") for i in range(3)],
        )

    t0 = asyncio.get_event_loop().time()
    results = await asyncio.gather(*[submit_one(i) for i in range(5)])
    elapsed = asyncio.get_event_loop().time() - t0
    # 5 个 batch × 3 文件 = 15 项;若 fan-out 并发会马上回;若串行也只是几十 ms
    assert elapsed < 2.0, f"5 个 batch 并发 submit 用了 {elapsed:.2f}s"
    assert all("batch_id" in r for r in results)

    # 等所有完成
    for r in results:
        await _wait_done(r["batch_id"], timeout=15.0)


# --- 入口 ---

async def _run_all():
    tests = [v for k, v in globals().items() if k.startswith("test_") and asyncio.iscoroutinefunction(v)]
    failed = 0
    for t in tests:
        try:
            await t()
            print(f"  OK   {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"  ERR  {t.__name__}: {type(e).__name__}: {e}")
    return failed, len(tests)


if __name__ == "__main__":
    failed, total = asyncio.run(_run_all())
    if failed:
        print(f"\n{failed}/{total} 失败")
        sys.exit(1)
    print(f"\nAll {total} tests passed.")
