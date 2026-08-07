"""archive-detect 响应模型与 CRUD dict  helper 一致性回归测试（无外部依赖）。

背景（2026-08，migration 027 删旧客户体系）：CRUD 层 dict 改为
client.id=None / progress 无 client_id 键（换成 client_code/client_name），
但 main.py 的 Pydantic response_model 未同步，FastAPI 响应校验抛
ResponseValidationError → 文件信息/业务批次轮询/后台批次列表等接口全部 500。
本测试直接把 CRUD dict helper 的输出喂给 response_model 校验，盯住这个接缝。

  cd e:/qoderproject/20260527
  PYTHONIOENCODING=utf-8 ./.venv312/Scripts/python.exe tests/test_archive_detect_response_models.py
"""
import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from db.models import ArchiveDetectProgress, ArchiveDetectBatch, ArchiveDetectFile
from db.archive_detect_crud import (
    _progress_to_dict,
    _client_brief_from_progress,
    _file_to_dict,
    _batch_to_dict,
)
from main import (
    ArchiveDetectClientInfo,
    ArchiveDetectProgressInfo,
    ArchiveDetectAdminProgressItem,
    ArchiveDetectAdminFileDetail,
    ArchiveDetectBatchResponse,
    FileInfoListItem,
)


def _make_progress() -> ArchiveDetectProgress:
    return ArchiveDetectProgress(
        id=1,
        client_code="C_TEST_001",
        client_name="张三",
        handler="Jason邹启",
        project_name="新加坡家办",
        project_code="P001",
        project_detail_name="架构设计",
        project_detail_code="PD001",
        progress_oid="OID-1",
        progress_name="递交后进展中",
        created_at=datetime(2026, 8, 7, 12, 0, 0),
        updated_at=datetime(2026, 8, 7, 12, 0, 0),
    )


def _make_file() -> ArchiveDetectFile:
    return ArchiveDetectFile(
        id=10,
        batch_id="b-test",
        idx=0,
        progress_id=1,
        file_id="F-1",
        version=1,
        source_url="http://oss.example.com/a.pdf",
        filename="护照.pdf",
        mime_type="application/pdf",
        page_count=2,
        char_count=300,
        verdict="match",
        match_score=90,
        reason="符合",
        status="done",
        elapsed_sec=1.5,
    )


def test_client_brief_validates():
    item = ArchiveDetectClientInfo.model_validate(_client_brief_from_progress(_make_progress()))
    assert item.id is None  # clients 表已删,键保留恒 null
    assert item.client_code == "C_TEST_001"
    assert item.name == "张三"


def test_progress_info_validates():
    item = ArchiveDetectProgressInfo.model_validate(_progress_to_dict(_make_progress()))
    assert item.client_id is None  # 键保留恒 null
    assert item.client_code == "C_TEST_001"
    assert item.client_name == "张三"
    assert item.progress_oid == "OID-1"


def test_admin_progress_item_validates():
    p = _make_progress()
    d = _progress_to_dict(p)
    d["client"] = _client_brief_from_progress(p)
    d["created_at"] = p.created_at.strftime("%Y-%m-%d %H:%M:%S")
    d["updated_at"] = p.updated_at.strftime("%Y-%m-%d %H:%M:%S")
    item = ArchiveDetectAdminProgressItem.model_validate(d)
    assert item.client.id is None
    assert item.client_name == "张三"


def test_file_info_list_item_validates():
    p = _make_progress()
    d = _file_to_dict(_make_file())
    d["batch_id"] = "b-test"
    d["created_at"] = "2026-08-07 12:00:00"
    d["updated_at"] = "2026-08-07 12:00:00"
    d["client"] = _client_brief_from_progress(p)
    d["progress"] = _progress_to_dict(p)
    item = FileInfoListItem.model_validate(d)
    assert item.client.name == "张三"
    assert item.progress.client_code == "C_TEST_001"


def test_admin_file_detail_validates():
    p = _make_progress()
    d = _file_to_dict(_make_file())
    d["ocr_text"] = "脱敏文本"
    d["batch_id"] = "b-test"
    d["progress"] = _progress_to_dict(p)
    d["client"] = _client_brief_from_progress(p)
    item = ArchiveDetectAdminFileDetail.model_validate(d)
    assert item.ocr_text == "脱敏文本"
    assert item.client.id is None


def test_batch_response_validates():
    p = _make_progress()
    b = ArchiveDetectBatch(
        batch_id="b-test",
        user_prompt="判定标准",
        source_kind="batch",
        total_files=1,
        done_files=1,
        status="done",
        created_at=datetime(2026, 8, 7, 12, 0, 0),
        updated_at=datetime(2026, 8, 7, 12, 0, 0),
    )
    d = _batch_to_dict(b)
    d["criteria"] = d["user_prompt"]
    d["progress"] = _progress_to_dict(p)
    d["client"] = _client_brief_from_progress(p)
    d["files"] = [_file_to_dict(_make_file())]
    item = ArchiveDetectBatchResponse.model_validate(d)
    assert item.client.id is None
    assert item.progress.client_id is None
    assert item.progress.client_name == "张三"


def test_batch_response_quick_without_progress():
    """历史 quick 批次无 progress:client/progress 为 None 也要过校验。"""
    b = ArchiveDetectBatch(
        batch_id="b-quick",
        user_prompt="判定标准",
        source_kind="quick",
        total_files=0,
        done_files=0,
        status="done",
        created_at=datetime(2026, 8, 7, 12, 0, 0),
        updated_at=datetime(2026, 8, 7, 12, 0, 0),
    )
    d = _batch_to_dict(b)
    d["client"] = None
    d["progress"] = None
    item = ArchiveDetectBatchResponse.model_validate(d)
    assert item.client is None and item.progress is None


if __name__ == "__main__":
    test_client_brief_validates()
    test_progress_info_validates()
    test_admin_progress_item_validates()
    test_file_info_list_item_validates()
    test_admin_file_detail_validates()
    test_batch_response_validates()
    test_batch_response_quick_without_progress()
    print("All tests passed.")
