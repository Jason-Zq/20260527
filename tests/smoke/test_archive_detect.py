"""Smoke test for archive_detect_service end-to-end pipeline.

  cd e:/qoderproject/20260527
  PYTHONIOENCODING=utf-8 ./.venv312/Scripts/python.exe tests/test_archive_detect.py

测试要点:
  1) 提交 batch (upload 模式), 后台异步处理完毕
  2) 验证最终落库的 reason / key_points 已被 redactor 兜底脱敏
  3) 验证 OCR 原文不出现在响应里
"""
import os
import sys
import asyncio
import shutil

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "backend"))

import llm_service
import text_extractor
import archive_detect_service
from db import archive_detect_crud as crud


async def _wait_for_done(batch_id: str, timeout_s: int = 30):
    """轮询 batch 直到 status==done 或超时。"""
    for _ in range(timeout_s * 4):
        b = await archive_detect_service.get_batch(batch_id)
        if b and b.get("status") == "done":
            return b
        await asyncio.sleep(0.25)
    raise TimeoutError(f"batch {batch_id} 未在 {timeout_s}s 内完成")


async def main():
    # 加载配置（detect_archival 间接需要 document_types）
    llm_service.load_config()

    # 桩 _call_llm: 让它返回带敏感信息的 JSON
    def fake_llm(prompt, max_retries=3):
        return (
            '{"is_archival": true, "confidence": 92, '
            '"reason": "客户张三 (身份证 110101199003078212) 支付了 ¥50000 用于该项目", '
            '"key_points": ["手机 13800138000", "年薪 50万元"], '
            '"doc_category": "合同协议"}'
        )
    llm_service._call_llm = fake_llm

    # 桩 text_extractor.extract_text: 不依赖真实 OCR
    async def fake_extract(path, mime=None):
        return {"text": "假装这是 OCR 抽取后的全文" * 30,
                "source": "pdf_text", "page_count": 1, "char_count": 360}
    text_extractor.extract_text = fake_extract

    # 在 temp/archive_detect/ 下伪造一个文件
    upload_dir = archive_detect_service._upload_temp_dir()
    fake_path = os.path.join(upload_dir, "fake_test.pdf")
    with open(fake_path, "wb") as f:
        f.write(b"%PDF-1.4 fake test content")

    # 提交
    batch_id = await archive_detect_service.submit_batch(
        user_prompt="帮我检测文件是否是 测试客户 测试项目 进展留底文件",
        source_kind="upload",
        items=[
            {"local_path": fake_path, "filename": "fake_test.pdf",
             "mime_type": "application/pdf"},
        ],
    )
    print(f"[OK] 提交成功 batch_id={batch_id}")

    # 轮询
    b = await _wait_for_done(batch_id)
    assert b["status"] == "done", b
    assert b["total_files"] == 1
    assert b["done_files"] == 1
    assert len(b["files"]) == 1
    f = b["files"][0]
    print(f"[OK] 文件状态: {f['status']}, is_archival={f['is_archival']}, conf={f['confidence']}")
    assert f["status"] == "done", f
    assert f["is_archival"] is True

    # 关键：脱敏
    reason = f["reason"]
    key_points = f["key_points"]
    print(f"[INFO] reason: {reason}")
    print(f"[INFO] key_points: {key_points}")

    # 不应残留任何敏感信息
    for needle in ["110101199003078212", "13800138000", "50000", "50万元", "¥"]:
        assert needle not in reason, f"reason 里残留 {needle}: {reason}"
        assert all(needle not in kp for kp in key_points), f"key_points 里残留 {needle}: {key_points}"
    # 应该有占位符
    assert "[身份证]" in reason or "[金额]" in reason, f"reason 没被脱敏: {reason}"
    print("[OK] 敏感信息已脱敏")

    # OCR 原文不应出现在响应里
    serialized = str(b)
    assert "假装这是 OCR" not in serialized, "OCR 原文泄露到响应里了"
    print("[OK] OCR 原文未泄露到响应")

    # 清理
    await crud.delete_batch(batch_id)
    print(f"[OK] 已清理 batch={batch_id}")
    print("\n所有断言通过 ✓")


if __name__ == "__main__":
    asyncio.run(main())
