"""客户画像-文件清单导入编排。

流程:从业务方接口(getAfterCustomerAllFiles)拉客户文件清单 -> 落 customer_files
-> 逐文件取 OCR(先复用 archive_detect,没有再下载+OCR) -> 分类筛 4 类
-> 按 extract_rules 提取 -> 归因写入客户档案。详见 docs/09。

本文件当前包含:文件来源协议 + 接口清单适配(纯函数,可单测) + run_import 编排。
"""
from typing import Optional, Protocol, TypedDict


class ManifestFile(TypedDict):
    file_code: str
    filename: str
    folder_name: Optional[str]
    rel_path: Optional[str]
    client_name: Optional[str]  # 行级客户姓名(可空)
    affter_entryoid: Optional[str]  # 售后项目OID(项目案件路由键;扁平形态/旧数据=None)
    project_name: Optional[str]     # 项目显示名(projectname_detailed || projectname)


class FileManifest(TypedDict):
    client_name: str            # 主客户
    files: list[ManifestFile]


class FileSourceProvider(Protocol):
    """文件来源协议:给个来源描述,返回统一客户文件清单。

    业务方查询接口(getAfterCustomerAllFiles)经 parse_api_manifest 适配成
    FileManifest,run_import 及下游零改动。
    """
    async def fetch_manifest(self, source: dict) -> FileManifest: ...


def _flatten_api_files(customer: dict) -> list:
    """接口单客户条目 -> 原始文件列表(嵌套形态把项目外壳字段注入每个文件行)。

    兼容两种返回形态:拉全量是扁平 entry.files[];传 customer_code 是
    按项目嵌套 entry.list[].files[](拍平)。样例见根目录 客户文件信息的接口.txt。
    注入键:affter_entryoid/projectno/projectname/projectno_detailed/
    projectname_detailed/project_create_time(项目 create_time 改名,避让文件级 create_time)。
    """
    files = customer.get("files")
    if files:
        return files
    flat: list = []
    for proj in customer.get("list") or []:
        proj_info = {k: proj.get(k) for k in (
            "affter_entryoid", "projectno", "projectname",
            "projectno_detailed", "projectname_detailed")}
        proj_info["project_create_time"] = proj.get("create_time")
        for f in proj.get("files") or []:
            flat.append({**f, **proj_info})
    return flat


def parse_api_manifest(customer: dict) -> dict:
    """接口单客户对象 -> {client_name, files, projects, skipped_junk, duplicates}(与 FileManifest 同构)。

    清洗(接口数据脏):
      - 过滤 "._" 开头的 macOS AppleDouble 垃圾文件(OCR 只会出乱码);
      - 无文件编号(cloud_file_id)的行跳过(无法刷新地址下载);
      - 按文件编号去重(保留首次)。
    folder_name 取 affter_progressname(进展名,对 doc_type_matcher 是强提示);
    rel_path 取 relative_path(文件夹线索)。
    projects: 按 affter_entryoid 分组的项目摘要(从注入后全部原始行构建,含被过滤行,
    保证项目列表完整;file_count 只计清洗后文件;entryoid 为空的行不进 projects)。
    """
    name = (customer.get("customer_name") or "").strip()
    files: list = []
    projects: dict = {}
    seen: set = set()
    skipped_junk = 0
    duplicates = 0
    for f in _flatten_api_files(customer):
        entryoid = (f.get("affter_entryoid") or "").strip() or None
        if entryoid and entryoid not in projects:
            projects[entryoid] = {
                "affter_entryoid": entryoid,
                "projectno": (f.get("projectno") or "").strip() or None,
                "projectname": (f.get("projectname") or "").strip() or None,
                "projectno_detailed": (f.get("projectno_detailed") or "").strip() or None,
                "projectname_detailed": (f.get("projectname_detailed") or "").strip() or None,
                "project_create_time": (f.get("project_create_time") or "").strip() or None,
                "file_count": 0,
            }
        code = str(f.get("cloud_file_id") or "").strip()
        fname = (f.get("file_name") or "").strip()
        if fname.startswith("._") or not code:
            skipped_junk += 1
            continue
        if code in seen:
            duplicates += 1
            continue
        seen.add(code)
        if entryoid:
            projects[entryoid]["file_count"] += 1
        files.append({
            "file_code": code,
            "filename": fname,
            "folder_name": (f.get("affter_progressname") or "").strip() or None,
            "rel_path": (f.get("relative_path") or "").strip() or None,
            "client_name": name or None,
            "affter_entryoid": entryoid,
            "project_name": (f.get("projectname_detailed") or f.get("projectname") or "").strip() or None,
        })
    return {"client_name": name, "files": files, "projects": list(projects.values()),
            "skipped_junk": skipped_junk, "duplicates": duplicates}


def group_api_customers(customers: list) -> list[dict]:
    """接口客户条目按姓名合并:同名多条目(不同 affter_entryoid)并成一户。

    返回 [{customer_name, customer_code, crm_oid, entry_count, customer}],customer 是
    拍平合并后的单客户对象(直接喂 parse_api_manifest),保证一户只建一个家庭/任务;
    文件级重复由 parse_api_manifest 按编号去重。空姓名条目跳过(无法建家庭)。
    customer_code/crm_oid 取首个非空。
    """
    merged: dict = {}
    order: list = []
    for c in customers:
        name = (c.get("customer_name") or "").strip()
        if not name:
            continue
        if name not in merged:
            merged[name] = {"customer_code": "", "crm_oid": "", "entry_count": 0, "files": []}
            order.append(name)
        m = merged[name]
        m["files"].extend(_flatten_api_files(c))
        m["entry_count"] += 1
        if not m["customer_code"]:
            m["customer_code"] = (c.get("customer_code") or "").strip()
        if not m["crm_oid"]:
            m["crm_oid"] = (c.get("crm_oid") or "").strip()
    return [{"customer_name": name,
             "customer_code": merged[name]["customer_code"],
             "crm_oid": merged[name]["crm_oid"],
             "entry_count": merged[name]["entry_count"],
             "customer": {"customer_name": name, "files": merged[name]["files"]}}
            for name in order]


# ==================== 导入编排 ====================

import asyncio
import os
import time
from datetime import datetime, timedelta

import httpx

import doc_type_matcher
import event_service
import extract_rules
import file_fetcher
import llm_service
import review_service
import text_extractor
from db import customer_file_crud, doc_extract_crud, profile_crud

# 4 类证件 -> 任务计数器字段名
_TYPE_COUNTER = {
    "id_card": "id_card_count",
    "hukou": "hukou_count",
    "degree_cert": "degree_cert_count",
    "birth_cert": "birth_cert_count",
}

# 原件落盘目录(相对 output/ 存 DB,绝对路径落盘);GC 按 file_keep_until 清理
_OUTPUT_DIR = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "output"))
_CUSTOMER_FILES_SUBDIR = "customer_files"
_FILE_KEEP_DAYS = 30


async def fetch_after_customer_files(*, customer_code: str = "",
                                     operation_user: str = "") -> list:
    """调业务方"客户文件信息"接口(getAfterCustomerAllFiles)取客户文件清单。

    传 customer_code 查该客户全部售后项目数据;不传查最近 100 条(接口固定,
    不支持条数参数)。返回 data.list(客户条目列表);接口/网络异常抛异常。
    与 file_fetcher.refresh_download_url 同源(config.json file_url_service)。
    """
    cfg = (file_fetcher._load_config().get("file_url_service") or {})
    if not cfg.get("enabled", False):
        raise ValueError("未启用 file_url_service")
    url = (cfg.get("customer_files_url") or "").strip()
    if not url:
        base_url = (cfg.get("base_url") or "").strip()
        if not base_url:
            raise ValueError("file_url_service.base_url 未配置")
        url = base_url.rsplit("/", 1)[0] + "/getAfterCustomerAllFiles"

    params = {
        "customer_code": (customer_code or "").strip(),
        "operation_user": (operation_user or "").strip()
                          or cfg.get("operation_user") or "Jason邹启",
    }
    # 全量 100 户响应体可能数 MB,超时给足
    timeout = httpx.Timeout(60, connect=15)

    client = await file_fetcher.get_http_client()
    t0 = time.time()
    try:
        resp = await client.get(url, params=params, timeout=timeout)
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("ret") != 200 or payload.get("code") != 0:
            raise ValueError(f"查询客户文件清单失败: {payload.get('msg') or payload}")
        data = payload.get("data") or {}
        customers = data.get("list") or []
    except Exception as e:
        file_fetcher._log_external_api(
            service="customer_files", url=url, params=params,
            response=None, status="error", error_msg=str(e),
            elapsed_ms=int((time.time() - t0) * 1000), file_id=None,
        )
        raise
    # 响应全文可能数 MB,只记小摘要
    file_fetcher._log_external_api(
        service="customer_files", url=url, params=params,
        response={"ret": payload.get("ret"), "code": payload.get("code"),
                  "total": data.get("total"), "customer_count": len(customers)},
        status="ok", error_msg=None,
        elapsed_ms=int((time.time() - t0) * 1000), file_id=None,
    )
    return customers


def _persist_local_file(src_path: str, file_code: str, filename: str) -> str:
    """把下载的原件从临时目录移存到 output/customer_files/,返回相对 output/ 的路径。"""
    dest_dir = os.path.join(_OUTPUT_DIR, _CUSTOMER_FILES_SUBDIR)
    os.makedirs(dest_dir, exist_ok=True)
    safe_name = file_fetcher._sanitize_filename(filename or "file")
    dest = os.path.join(dest_dir, f"{file_code}_{safe_name}")
    os.replace(src_path, dest)
    return f"{_CUSTOMER_FILES_SUBDIR}/{file_code}_{safe_name}"


async def ensure_local_file(row: dict) -> tuple[str, str]:
    """返回 (原件绝对路径, mime)。本地有直接用;没有/已 GC 则刷新 URL 重下并顺延 30 天。

    抛 FileNotFoundError: 无本地原件且无 file_code 可重下。
    """
    rel = row.get("local_path")
    if rel:
        abs_path = os.path.join(_OUTPUT_DIR, rel)
        if os.path.exists(abs_path):
            return abs_path, row.get("mime_type") or "application/octet-stream"
    file_code = (row.get("file_code") or "").strip()
    if not file_code or file_code.startswith("nocode-"):
        raise FileNotFoundError("无本地原件且无法重新下载(缺文件编码)")
    url, _ = await file_fetcher.refresh_download_url(file_code)
    tmp_path, fname, mime = await file_fetcher.fetch_url_to_temp(url)
    rel_path = _persist_local_file(tmp_path, file_code, fname)
    await customer_file_crud.update_file_local(
        row["id"], local_path=rel_path,
        file_keep_until=datetime.now() + timedelta(days=_FILE_KEEP_DAYS))
    return os.path.join(_OUTPUT_DIR, rel_path), (mime or "application/octet-stream")


async def run_infer_relations(household_id: int, *, trigger: str = "import") -> dict:
    """家庭关系交叉推导 + 事件留痕(run_import 收尾与管理端点共用)。"""
    result = await profile_crud.infer_family_relations(household_id)
    for inf in result.get("inferred") or []:
        event_service.log_event(
            event_service.INFO, event_service.CATEGORY_PROFILE_RELATION_INFER,
            f"家庭关系自动推导: person#{inf['person_id']} → {inf['relation']}",
            context={"household_id": household_id, "trigger": trigger, **inf})
    return result


async def run_import(task_id: int) -> None:
    """导入任务主流程(主进程 asyncio.create_task 串行跑)。

    逐文件: 取 OCR(relink/复用/下载) -> 分类(关键词->LLM 兜底) -> 4 类提取+归因写库。
    单文件异常只标该文件 error,不杀任务;任务级异常落 finish_import_task('error')。
    """
    task = await customer_file_crud.get_import_task(task_id)
    if not task:
        return
    counters = {
        "processed_files": 0, "reused_count": 0, "relinked_count": 0,
        "fresh_ocr_count": 0, "failed_count": 0, "extracted_count": 0,
        "id_card_count": 0, "hukou_count": 0, "degree_cert_count": 0,
        "birth_cert_count": 0, "needs_review_count": 0,
    }
    try:
        rows = await customer_file_crud.list_pending_files(task_id)
        for row in rows:
            # 任务被删除(列表页删除按钮)时在下一个文件边界协作停止
            if await customer_file_crud.get_import_task(task_id) is None:
                print(f"[profile_import:{task_id}] 任务已被删除,停止导入")
                return
            counters["processed_files"] += 1
            await customer_file_crud.update_task_progress(
                task_id, current_file=row.get("filename") or row.get("file_code"), **counters)
            try:
                await _process_one_file(task, row, counters)
            except Exception as e:
                counters["failed_count"] += 1
                await customer_file_crud.mark_file_error(row["id"], f"{type(e).__name__}: {e}")
                print(f"[profile_import:{task_id}] 文件 {row.get('file_code')} 处理失败: {e}")
            await customer_file_crud.update_task_progress(task_id, **counters)

        # 家庭关系交叉推导(全部文件处理完后;异常不杀任务)
        household_id = task.get("household_id")
        if household_id:
            try:
                await run_infer_relations(household_id, trigger="import")
            except Exception as e:
                print(f"[profile_import:{task_id}] 关系推导失败(忽略): {e}")
                event_service.log_event(
                    event_service.WARN, event_service.CATEGORY_PROFILE_RELATION_INFER,
                    f"家庭关系推导失败: {e}",
                    context={"task_id": task_id, "household_id": household_id,
                             "error": str(e)})

        await customer_file_crud.finish_import_task(task_id, "done")
        event_service.log_event(
            event_service.INFO, event_service.CATEGORY_PROFILE_IMPORT_DONE,
            f"客户画像导入完成: {task.get('client_name')} 共 {counters['processed_files']} 个文件",
            context={"task_id": task_id, **counters},
        )
    except Exception as e:
        await customer_file_crud.finish_import_task(task_id, "error", f"{type(e).__name__}: {e}")
        event_service.log_event(
            event_service.ERROR, event_service.CATEGORY_PROFILE_IMPORT_ERROR,
            f"客户画像导入失败: {e}",
            context={"task_id": task_id, "error": str(e)},
        )


async def run_imports_sequential(task_ids: list[int]) -> None:
    """多任务串行跑(接口导入一次建多户任务时避免并发打爆 OCR/LLM)。

    run_import 内部自吞异常标 error,不会中断后续任务。
    """
    for tid in task_ids:
        await run_import(tid)


async def _process_one_file(task: dict, row: dict, counters: dict) -> None:
    task_id = task["id"]
    file_code = row.get("file_code") or ""
    ocr_text = row.get("ocr_text") or ""

    # ---- 1) 取 OCR ----
    if row.get("status") == "done" and ocr_text:
        counters["relinked_count"] += 1  # 本库已有 done 行,直接复用
    else:
        await customer_file_crud.set_file_status(row["id"], "fetching")
        reused = await customer_file_crud.find_reusable_ocr(file_code)
        if reused:
            ocr_text = reused["ocr_text"] or ""
            await customer_file_crud.update_file_ocr(
                row["id"], status="ocr", ocr_source="reused", ocr_text=ocr_text,
                mime_type=reused.get("mime_type"),
                page_count=reused.get("page_count"),
                char_count=reused.get("char_count"),
            )
            counters["reused_count"] += 1
        else:
            if file_code.startswith("nocode-"):
                raise ValueError("缺少文件编码,无法刷新下载地址")
            url, _ = await file_fetcher.refresh_download_url(file_code)
            tmp_path, fname, mime = await file_fetcher.fetch_url_to_temp(url)
            result = await text_extractor.extract_text(tmp_path, mime)
            ocr_text = result.get("text") or ""
            # 原件落盘留存(30 天 GC;复核在线查看用),不再删除
            rel_path = _persist_local_file(tmp_path, file_code, fname)
            await customer_file_crud.update_file_local(
                row["id"], local_path=rel_path,
                file_keep_until=datetime.now() + timedelta(days=_FILE_KEEP_DAYS))
            await customer_file_crud.update_file_ocr(
                row["id"], status="ocr", ocr_source="fresh", ocr_text=ocr_text,
                mime_type=mime,
                page_count=result.get("page_count"),
                char_count=result.get("char_count"),
            )
            counters["fresh_ocr_count"] += 1

    # ---- 2) 分类 ----
    doc_type = None
    classify_by = "none"
    classify_score = None
    if ocr_text.strip():
        m = doc_type_matcher.classify(
            row.get("folder_name"), row.get("filename"),
            ocr_text[: doc_type_matcher.OCR_HEAD_CHARS], row.get("rel_path"))
        if m["doc_type"]:
            doc_type = m["doc_type"]
            classify_by, classify_score = "keyword", m["score"]
        else:
            r = await asyncio.to_thread(
                llm_service.recognize_doc_type, ocr_text[:2000],
                task_id=str(task_id), file_id=file_code)
            doc_type = r["doc_type"]
            classify_by, classify_score = "llm", r["confidence"]
        await customer_file_crud.update_file_classify(
            row["id"], doc_type=doc_type, classify_by=classify_by,
            classify_score=classify_score)
        if doc_type in _TYPE_COUNTER:
            counters[_TYPE_COUNTER[doc_type]] += 1
    else:
        await customer_file_crud.update_file_classify(
            row["id"], doc_type=None, classify_by="none", classify_score=None)

    # ---- 3) 4 类提取 + 归因写库(无 active 规则则留 skipped 痕) ----
    extract_outcome = None
    if doc_type in llm_service.DOC_EXTRACT_TYPES and ocr_text.strip():
        extract_outcome = await _extract_one(task, row, doc_type, ocr_text, counters)

    # ---- 4) 质量评级(纯规则,复核轻重缓急的依据) ----
    q = review_service.evaluate_file_quality(
        ocr_text=ocr_text, folder_name=row.get("folder_name"),
        doc_type=doc_type, classify_by=classify_by, classify_score=classify_score,
        extract_status=(extract_outcome or {}).get("status"),
        extract_skip_reason=(extract_outcome or {}).get("skip_reason"),
        id_masked=bool((extract_outcome or {}).get("id_masked")))
    await customer_file_crud.update_file_review(
        row["id"], quality_score=q["quality_score"],
        review_status=q["review_status"], review_reason=q["review_reason"])
    if q["review_status"] == "needs_review":
        counters["needs_review_count"] += 1


def _clean_field_items(rule: dict, extracted: dict) -> tuple:
    """字段清洗(单人/多人分支共用):去空 → field_items + 归因三要素 + id_masked。

    返回 (field_items, id_number, name, name_en, id_masked)。
    masked 值不候选归因/写库(在 apply 内记 skipped_masked);乱码假名置 None。
    """
    field_items = []
    for f in rule.get("fields") or []:
        v = extracted.get(f.get("key"))
        if v is None or (isinstance(v, str) and not v.strip()):
            continue
        field_items.append({
            "key": f.get("key"), "label": f.get("label"),
            "value": str(v).strip(),
            "column": (f.get("target") or {}).get("column"),
            "layer": (f.get("target") or {}).get("layer"),
            "entity": (f.get("target") or {}).get("entity") or "person",
        })
    id_number = next(
        (it["value"] for it in field_items
         if it["column"] == "id_number"
         and not doc_extract_crud.is_masked(it["value"])
         and doc_extract_crud.valid_id_number(it["value"])), None)
    name = next(
        (it["value"] for it in field_items
         if it["column"] == "name" and not doc_extract_crud.is_masked(it["value"])), None)
    if name and not profile_crud.plausible_person_name(name):
        name = None  # 乱码假名(如 "钅 lil蝴哪")不参与归因/建人,按无姓名走 no_person
    # 拼音名归因(批复/永居卡等英文证件没有中文名;词序无关匹配 person_fields.name_en)
    name_en = next(
        (it["value"] for it in field_items
         if it["column"] == "name_en" and not doc_extract_crud.is_masked(it["value"])), None)
    id_masked = any(
        it["column"] == "id_number" and doc_extract_crud.is_masked(it["value"])
        for it in field_items)
    return field_items, id_number, name, name_en, id_masked


async def _extract_one_multi(task: dict, row: dict, doc_type: str, ocr_text: str,
                             counters: dict, rule: dict, outcome: dict, t0: float) -> dict:
    """多人模式提取(rule.multi=True,如整本户口本):逐人归因写库,仍写一行 doc_extract_results。

    write_stats 保留顶层 person_id(=首个归属人,兼容 ReviewDrawer 预填)+ persons 明细列表。
    """
    task_id = task["id"]
    household_id = task.get("household_id")
    file_code = row.get("file_code") or ""

    raw = await asyncio.to_thread(
        llm_service.extract_doc_fields_multi, ocr_text, rule,
        task_id=str(task_id), file_id=file_code)
    persons_raw = raw.get("persons") or []

    all_mapped: list = []
    persons_stats: list = []
    agg = {"written": 0, "updated": 0, "person_created": 0}
    first_pid, first_matched_by = None, None
    for pext in persons_raw:
        field_items, id_number, name, name_en, id_masked = _clean_field_items(rule, pext)
        outcome["id_masked"] = outcome["id_masked"] or id_masked
        if not id_number and not name and not name_en:
            all_mapped.append({"key": "*", "field": None, "person_id": None,
                               "action": "skipped_no_name"})
            continue
        match = await profile_crud.find_person_match(household_id, id_number, name, name_en)
        write = await profile_crud.apply_extracted_fields_v2(
            household_id, match, field_items, source_file_id=row["id"])
        pid = write.get("person_id")
        pname = name or next((p["person_name"] for p in persons_stats
                              if p["person_id"] == pid), None)
        value_by_key = {it["key"]: it["value"] for it in field_items}
        for m in write["mapped"]:
            m["person_name"] = pname
            # 带上提取值:交叉验证(attach_field_conflicts)多人模式按条目取值用
            m["value"] = value_by_key.get(m.get("key"))
        all_mapped += write["mapped"]
        ws = write["write_stats"]
        persons_stats.append({
            "person_id": pid, "person_name": pname,
            "matched_by": ws.get("matched_by"),
            "written": ws.get("written", 0), "updated": ws.get("updated", 0),
            "person_created": ws.get("person_created", 0)})
        for k in agg:
            agg[k] += ws.get(k, 0)
        if first_pid is None and pid:
            first_pid, first_matched_by = pid, ws.get("matched_by")

    if not persons_stats:
        await doc_extract_crud.insert_result(
            customer_file_id=row["id"], import_task_id=task_id, file_id=file_code,
            client_id=task.get("client_id"), doc_type=doc_type,
            rule_id=None, rule_version=rule["version"],
            status="skipped", skip_reason="no_person",
            extracted={"persons": persons_raw},
            elapsed_ms=int((time.time() - t0) * 1000))
        event_service.log_event(
            event_service.WARN, event_service.CATEGORY_EXTRACT_SKIP,
            f"多人提取结果全部无法归属: {file_code}",
            context={"task_id": task_id, "file_code": file_code, "doc_type": doc_type,
                     "reason": "no_person"})
        outcome.update(status="skipped", skip_reason="no_person")
        return outcome

    write_stats = {"matched_by": first_matched_by, "person_id": first_pid,
                   "person_count": len(persons_stats), "persons": persons_stats, **agg}
    await doc_extract_crud.insert_result(
        customer_file_id=row["id"], import_task_id=task_id, file_id=file_code,
        client_id=task.get("client_id"), doc_type=doc_type,
        rule_id=None, rule_version=rule["version"],
        status="done", extracted={"persons": persons_raw},
        mapped=all_mapped, write_stats=write_stats,
        elapsed_ms=int((time.time() - t0) * 1000))
    counters["extracted_count"] += 1
    event_service.log_event(
        event_service.INFO, event_service.CATEGORY_EXTRACT_DONE,
        f"证件信息提取完成(多人 {len(persons_stats)} 人): {file_code} ({doc_type})",
        context={"task_id": task_id, "file_code": file_code, "doc_type": doc_type,
                 "rule_version": rule["version"], "person_count": len(persons_stats),
                 "writes": dict(agg)})
    outcome.update(status="done")
    return outcome


async def _extract_one(task: dict, row: dict, doc_type: str,
                       ocr_text: str, counters: dict) -> dict:
    """4 类证件提取 + 归因 + 写 profile_person_fields。返回 outcome 供质量评级使用。"""
    task_id = task["id"]
    household_id = task.get("household_id")
    file_code = row.get("file_code") or ""
    t0 = time.time()
    outcome = {"status": None, "skip_reason": None, "id_masked": False}

    rule = extract_rules.get_rule(doc_type)
    if not rule:
        await doc_extract_crud.insert_result(
            customer_file_id=row["id"], import_task_id=task_id, file_id=file_code,
            client_id=task.get("client_id"), doc_type=doc_type, rule_id=None,
            rule_version=None, status="skipped", skip_reason="no_active_rule")
        event_service.log_event(
            event_service.INFO, event_service.CATEGORY_EXTRACT_SKIP,
            f"证件类型 {doc_type} 无 active 提取规则,跳过提取",
            context={"task_id": task_id, "file_code": file_code, "doc_type": doc_type,
                     "reason": "no_active_rule"})
        outcome.update(status="skipped", skip_reason="no_active_rule")
        return outcome

    # 乱码文本不提取(垃圾进垃圾出:实测歪扫户口页会产出乱码人名并错误建人;
    # 该文件已在质量评级中标 garbled 进复核队列)
    if review_service.is_garbled(ocr_text):
        await doc_extract_crud.insert_result(
            customer_file_id=row["id"], import_task_id=task_id, file_id=file_code,
            client_id=task.get("client_id"), doc_type=doc_type,
            rule_id=None, rule_version=rule["version"],
            status="skipped", skip_reason="garbled_text")
        event_service.log_event(
            event_service.WARN, event_service.CATEGORY_EXTRACT_SKIP,
            f"OCR 乱码,跳过提取: {file_code}",
            context={"task_id": task_id, "file_code": file_code, "doc_type": doc_type,
                     "reason": "garbled_text"})
        outcome.update(status="skipped", skip_reason="garbled_text")
        return outcome

    try:
        if rule.get("multi"):
            return await _extract_one_multi(
                task, row, doc_type, ocr_text, counters, rule, outcome, t0)

        raw = await asyncio.to_thread(
            llm_service.extract_doc_fields, ocr_text, rule,
            task_id=str(task_id), file_id=file_code)
        extracted = raw.get("fields") or {}

        # 字段清洗:去空;masked 不候选归因/写库(在 apply 内记 skipped_masked)
        field_items, id_number, name, name_en, id_masked = _clean_field_items(rule, extracted)
        outcome["id_masked"] = id_masked

        match = await profile_crud.find_person_match(household_id, id_number, name, name_en)
        case_items = [it for it in field_items if it.get("entity") == "case"]
        if match.get("person_id") is None and not name and not case_items:
            await doc_extract_crud.insert_result(
                customer_file_id=row["id"], import_task_id=task_id, file_id=file_code,
                client_id=task.get("client_id"), doc_type=doc_type,
                rule_id=None, rule_version=rule["version"],
                status="skipped", skip_reason="no_person",
                extracted=extracted,
                elapsed_ms=int((time.time() - t0) * 1000))
            event_service.log_event(
                event_service.WARN, event_service.CATEGORY_EXTRACT_SKIP,
                f"提取结果无姓名无法归属: {file_code}",
                context={"task_id": task_id, "file_code": file_code, "doc_type": doc_type,
                         "reason": "no_person"})
            outcome.update(status="skipped", skip_reason="no_person")
            return outcome

        write = await profile_crud.apply_extracted_fields_v2(
            household_id, match, field_items, source_file_id=row["id"])
        # entity=asset 的字段写入家庭资产表(房产证类),去重靠 attrs.cert_no/name
        asset_items = [it for it in field_items if it.get("entity") == "asset"]
        if asset_items:
            aw = await profile_crud.apply_extracted_asset(
                household_id, write.get("person_id"), asset_items,
                source_file_id=row["id"])
            write["mapped"] += aw["mapped"]
            write["write_stats"].update(aw["stats"])
        # entity=case 的字段写入案件时间线(递交/签收/批复里程碑,按文件所属项目路由到项目案件)
        if case_items:
            cw = await profile_crud.apply_case_milestones(
                household_id, case_items, source_file_id=row["id"],
                affter_entryoid=row.get("affter_entryoid"),
                project_name_hint=row.get("project_name"))
            write["mapped"] += cw["mapped"]
            write["write_stats"].update(cw["stats"])
        await doc_extract_crud.insert_result(
            customer_file_id=row["id"], import_task_id=task_id, file_id=file_code,
            client_id=task.get("client_id"), doc_type=doc_type,
            rule_id=None, rule_version=rule["version"],
            status="done", extracted=extracted,
            mapped=write["mapped"], write_stats=write["write_stats"],
            elapsed_ms=int((time.time() - t0) * 1000))
        counters["extracted_count"] += 1
        event_service.log_event(
            event_service.INFO, event_service.CATEGORY_EXTRACT_DONE,
            f"证件信息提取完成: {file_code} ({doc_type})",
            context={"task_id": task_id, "file_code": file_code, "doc_type": doc_type,
                     "rule_version": rule["version"],
                     "matched_by": write["write_stats"].get("matched_by"),
                     "writes": {k: v for k, v in write["write_stats"].items()
                                if k in ("written", "updated", "person_created")}})
        outcome.update(status="done")
        return outcome
    except Exception as e:
        await doc_extract_crud.insert_result(
            customer_file_id=row["id"], import_task_id=task_id, file_id=file_code,
            client_id=task.get("client_id"), doc_type=doc_type,
            rule_id=None, rule_version=rule["version"],
            status="error", error_msg=f"{type(e).__name__}: {e}",
            elapsed_ms=int((time.time() - t0) * 1000))
        event_service.log_event(
            event_service.WARN, event_service.CATEGORY_EXTRACT_ERROR,
            f"证件信息提取失败: {file_code}: {e}",
            context={"task_id": task_id, "file_code": file_code, "doc_type": doc_type,
                     "error": str(e)})
        outcome.update(status="error")
        return outcome
