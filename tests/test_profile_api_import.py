"""一次性脚本(留作参考,非单元测试):从业务方"客户文件信息"接口直接跑出客户画像。

背景:接口 getAfterCustomerAllFiles 返回售后客户及其全部文件(样例见根目录 客户文件信息的接口.txt)。
本脚本把接口返回的每个客户适配成画像导入的 FileManifest(适配函数已迁入生产代码
profile_import_service.parse_api_manifest,本脚本直接复用),落库后复用现有
profile_import_service.run_import 流水线逐户串行跑:
  取 OCR(先全局复用 archive_detect,没有再按文件编号刷新地址下载+OCR) -> 分类 -> 提取 -> 归因写库。
下载通道:文件编号(cloud_file_id) -> file_fetcher.refresh_download_url -> OSS 临时地址,
与业务审核同源(config.json.file_url_service)。

清洗规则(接口数据比 Excel 脏,由 parse_api_manifest 统一处理):
  - 过滤 "._" 开头的 macOS AppleDouble 垃圾文件(OCR 只会出乱码);
  - 无文件编号的行跳过(无法刷新地址下载);
  - 按文件编号去重(保留首次);
  - 跳过测试客户 ArvinTest(本脚本黑名单)。

用法(仓库根目录):
  # 只拉清单+打印适配结果,不写库不跑任务(先跑这个验证)
  PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_profile_api_import.py --dry-run
  # 正式跑(6 户串行,耗时以小时计,建议后台)
  PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_profile_api_import.py
  # 只补跑指定客户(如网络故障后的恢复):
  PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_profile_api_import.py --only 贺禹,李杰
"""
import asyncio
import os
import sys

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

import profile_import_service
from profile_import_service import parse_api_manifest
from db import customer_file_crud, profile_crud

# llm_service.CONFIG 是模块级 {},由 main.py/worker_runner 启动时显式 load_config;
# 独立脚本必须自己加载,否则所有 LLM 调用报"未配置大模型 API Key"并静默降级
import llm_service
llm_service.load_config()

# 业务方"客户文件信息"接口(与 file_url_service 同主机;operation_user 保持一致)
API_URL = "https://restful.huanqiuyimin.com/Cloud/getAfterCustomerAllFiles"
OPERATION_USER = "Jason邹启"

# 本次只跑 txt 快照里的 6 个客户(接口全量返回 100 个客户,务必用白名单限定)
TARGET_CUSTOMERS = {"贺禹", "花荣斌", "李杰", "丁夕勇", "田达", "宋国娥"}

# 测试客户黑名单(留作参考:ArvinTest / 杨敬华测试 等都是测试数据)
SKIP_CUSTOMERS = {"ArvinTest", "杨敬华测试", "NULL", ""}

HEARTBEAT_SEC = 60


async def fetch_all_customers() -> list:
    """调业务方接口取全量售后客户文件清单。"""
    async with httpx.AsyncClient(timeout=httpx.Timeout(60, connect=15)) as client:
        resp = await client.get(
            API_URL, params={"affter_entryoid": "", "operation_user": OPERATION_USER})
        resp.raise_for_status()
        payload = resp.json()
    if payload.get("ret") != 200 or payload.get("code") != 0:
        raise RuntimeError(f"接口返回异常: {payload.get('msg') or payload}")
    return (payload.get("data") or {}).get("list") or []


async def _run_with_heartbeat(task_id: int, label: str) -> None:
    """跑 run_import 并每 60s 打印一次任务计数(长时间后台运行的可观测性)。"""
    t = asyncio.create_task(profile_import_service.run_import(task_id))
    while not t.done():
        await asyncio.sleep(HEARTBEAT_SEC)
        row = await customer_file_crud.get_import_task(task_id)
        if row:
            print(f"  [{label}] {row.get('processed_files')}/{row.get('total_files')}"
                  f" 复用{row.get('reused_count')} 新OCR{row.get('fresh_ocr_count')}"
                  f" 提取{row.get('extracted_count')} 失败{row.get('failed_count')}",
                  flush=True)
    await t  # run_import 内部吞异常标 error,这里仅为传播意外异常


async def main() -> None:
    dry_run = "--dry-run" in sys.argv
    only: set = set()
    for i, a in enumerate(sys.argv):
        if a == "--only" and i + 1 < len(sys.argv):
            only = {x.strip() for x in sys.argv[i + 1].split(",") if x.strip()}
    targets = only or TARGET_CUSTOMERS

    customers = await fetch_all_customers()
    print(f"接口返回客户 {len(customers)} 个(条目),本次目标 {sorted(targets)}", flush=True)

    # 同名客户可能出现多个条目(不同 affter_entryoid,如"田达"两条):按姓名合并文件,
    # 保证一户只建一个家庭/一个任务;文件级重复由 parse_api_manifest 按编号去重
    merged: dict = {}
    for c in customers:
        name = (c.get("customer_name") or "").strip()
        if name not in merged:
            merged[name] = {"customer_name": name, "files": []}
        merged[name]["files"].extend(c.get("files") or [])

    missing = targets - set(merged)
    if missing:
        print(f"!! 目标客户在接口中未找到: {sorted(missing)}", flush=True)

    for name, c in merged.items():
        if name not in targets or name in SKIP_CUSTOMERS:
            continue
        m = parse_api_manifest(c)
        raw_count = len(c.get("files") or [])
        print(f"\n== {name}: 原始文件 {raw_count} -> 有效 {len(m['files'])}"
              f" (垃圾 {m['skipped_junk']}, 重复 {m['duplicates']})", flush=True)
        if dry_run or not m["files"]:
            continue

        # 与 /api/profile/import-remote 端点相同的落库链路(2026-08 起不再建旧 clients 软关联)
        household = await profile_crud.get_or_create_household(
            name, legacy_client_id=None)
        task = await customer_file_crud.create_import_task(
            filename=f"接口导入-{name}", client_name=name,
            client_id=None, total_files=len(m["files"]),
            household_id=household["id"])
        counts = await customer_file_crud.upsert_task_files(
            task["id"], None, m["files"])
        print(f"  任务#{task['id']} 家庭#{household['id']}"
              f" 新建{counts['new']} 重链{counts['relinked']},开始导入", flush=True)

        await _run_with_heartbeat(task["id"], name)

        final = await customer_file_crud.get_import_task(task["id"])
        print(f"== {name} 结束: 状态{final.get('status')}"
              f" 处理{final.get('processed_files')}/{final.get('total_files')}"
              f" 复用{final.get('reused_count')} 重链{final.get('relinked_count')}"
              f" 新OCR{final.get('fresh_ocr_count')} 提取{final.get('extracted_count')}"
              f" 失败{final.get('failed_count')} 待复核{final.get('needs_review_count')}",
              flush=True)

    print("\n全部完成", flush=True)
    # event_service 是 fire-and-forget,退出前留一点时间让尾部事件落库
    await asyncio.sleep(2)


if __name__ == "__main__":
    asyncio.run(main())
