"""OCR 内存治理回归测试。

  cd e:/qoderproject/20260527
  PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_ocr_memory.py

只验证可以静态校验的部分(不真正跑 PaddleOCR,避免依赖):
- OCR_RENDER_SCALE = 200/72 (从 300/72 降下来)
- MAX_PIXELS 兜底:大图 5000x5000 渲染后会被缩到 ≤ MAX_PIXELS
- _OCR_ENGINE_LOCK 是 threading.Lock 实例
- run_ocr 暴露在公共接口
- split_ocr_service 复用 ocr_service 全局 PaddleOCR(不再 thread-local)
- main.py 启动前已经设置 OMP_NUM_THREADS=2
"""
import sys
import os
import threading

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))


def test_ocr_render_scale_is_200_dpi():
    import ocr_service
    expected = 200 / 72
    assert abs(ocr_service.OCR_RENDER_SCALE - expected) < 1e-6, ocr_service.OCR_RENDER_SCALE


def test_max_pixels_constant_is_set():
    import ocr_service
    # 至少不能 > 4500×4500,否则单页内存兜底失效
    assert ocr_service.MAX_PIXELS <= 4500 * 4500
    # 但也别太小,1024×1024 是身份证图片下限附近
    assert ocr_service.MAX_PIXELS >= 1024 * 1024


def test_downscale_handles_large_image():
    """单页超过 MAX_PIXELS 时必须被等比缩到 ≤ MAX_PIXELS。"""
    from PIL import Image
    import ocr_service

    # 构造一张 5000×5000 = 25M pixels 的纯色图,远超 MAX_PIXELS=16M
    big = Image.new("RGB", (5000, 5000), (255, 255, 255))
    out, shrunk = ocr_service._downscale_if_too_large(big)
    assert shrunk is True
    w, h = out.size
    assert w * h <= ocr_service.MAX_PIXELS, f"{w}x{h}={w*h} > MAX_PIXELS={ocr_service.MAX_PIXELS}"
    # 等比缩放后,宽高比应保持(允许 1px 截断)
    assert abs(w - h) <= 1


def test_downscale_passes_through_small_image():
    """小图不应被改动。"""
    from PIL import Image
    import ocr_service
    small = Image.new("RGB", (800, 600), (0, 0, 0))
    out, shrunk = ocr_service._downscale_if_too_large(small)
    assert shrunk is False
    assert out.size == (800, 600)


def test_engine_lock_is_threading_lock():
    """跨线程池调用 OCR 必须用真正的 threading.Lock,不是 asyncio.Lock。"""
    import ocr_service
    # threading.Lock() 返回的是 _thread.allocate_lock 类型,没法用 isinstance 直接判断
    # 但有 acquire/release 方法且不是 coroutine
    lock = ocr_service._OCR_ENGINE_LOCK
    assert hasattr(lock, "acquire") and hasattr(lock, "release")
    # 确认 acquire 不是 coroutine(asyncio.Lock.acquire 是 coroutine)
    import inspect
    assert not inspect.iscoroutinefunction(lock.acquire)


def test_run_ocr_is_public():
    import ocr_service
    assert hasattr(ocr_service, "run_ocr") and callable(ocr_service.run_ocr)


def test_split_ocr_reuses_global_engine():
    """split_ocr_service 应该走 ocr_service.run_ocr,不应该自己维护 thread-local 引擎。"""
    import split_ocr_service
    # 旧版本有 _thread_local 和 _get_thread_ocr,新版本应该没有
    assert not hasattr(split_ocr_service, "_thread_local"), "split_ocr_service 仍在用 thread-local 引擎,会内存翻倍"
    assert not hasattr(split_ocr_service, "_get_thread_ocr")


def test_omp_threads_capped_in_main():
    """main.py 必须在 import paddlepaddle/numpy 之前 setdefault OMP_NUM_THREADS=2,
    防止 PaddleOCR 拉满 CPU 吃内存。

    通过读源码检查,而不是 import main —— paddlepaddle 一旦先于 main 被 import,
    会在 main 之前把 OMP 设成 1,setdefault 就 no-op 了。
    """
    import re
    main_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend", "main.py")
    with open(main_path, "r", encoding="utf-8") as f:
        src = f.read()
    # 必须 setdefault 到 2
    assert 'os.environ.setdefault("OMP_NUM_THREADS", "2")' in src

    # 截取 setdefault 之前的代码部分,确认里面不含真正的 paddle/numpy import 语句
    omp_pos = src.find('os.environ.setdefault("OMP_NUM_THREADS"')
    prefix = src[:omp_pos]
    # 去掉注释行后再检测,避免误判
    lines_wo_comments = "\n".join(
        l for l in prefix.split("\n") if not l.lstrip().startswith("#")
    )
    forbidden_patterns = [
        r"^\s*import\s+numpy",
        r"^\s*from\s+numpy\s",
        r"^\s*import\s+paddle",
        r"^\s*from\s+paddle",
        r"^\s*import\s+cv2",
        r"^\s*from\s+cv2\s",
        r"^\s*import\s+ocr_service",
        r"^\s*import\s+split_ocr_service",
        r"^\s*import\s+text_extractor",
        r"^\s*import\s+llm_service",
    ]
    for pat in forbidden_patterns:
        if re.search(pat, lines_wo_comments, re.MULTILINE):
            raise AssertionError(
                f"main.py 在设置 OMP_NUM_THREADS 之前已经匹配 `{pat}`,paddle 会先把 OMP 设成 1"
            )


if __name__ == "__main__":
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  OK   {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"  ERR  {t.__name__}: {type(e).__name__}: {e}")
    if failed:
        print(f"\n{failed}/{len(tests)} 失败")
        sys.exit(1)
    print(f"\nAll {len(tests)} tests passed.")
