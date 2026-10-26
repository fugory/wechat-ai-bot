# -*- coding: utf-8 -*-
"""UIA 调用的统一守卫：全局串行锁 + COM 异常记录。

为什么需要
----------
现场审查发现：
  * 主循环线程和看门狗线程同时使用同一个 WeChatUIA 实例
  * 全项目没有任何 UIA 互斥锁
  * 出错时只 log 一句中文，没有任何 HRESULT/COM 错误码

这三点叠加，任何一次并发都会在微信的无障碍 provider 上留下烂摊子，
而且崩了之后我们完全不知道是哪一步、什么错误码。

用法
----
    from uia_guard import uia_call

    def uia_session_items():
        with uia_call("session_items"):
            return _uia_session_items_impl()

    # 慢调用会单独记一条（> warn_ms），方便定位卡顿
"""
import contextlib
import threading
import time

# RLock：允许同一线程重入（有些函数内部会再调另一个受保护的函数）
LOCK = threading.RLock()

STATS = {
    "calls": 0,
    "errors": 0,
    "slow": 0,
    "last_error": "",
    "last_hresult": "",
    "last_error_at": "",
    "lock_wait_ms": 0,
    "max_call_ms": 0,
}

_LOGGER = None


def set_logger(fn):
    """由 bridge 注入它的 log()，避免循环 import。"""
    global _LOGGER
    _LOGGER = fn


def _log(msg: str) -> None:
    try:
        if _LOGGER:
            _LOGGER(msg)
        else:
            print("[uia_guard] " + msg)
    except Exception:
        pass


def _hresult_of(exc) -> str:
    """尽量从异常里挖出 HRESULT（pywin32 的 com_error 带 hresult）。"""
    for attr in ("hresult", "winerror"):
        v = getattr(exc, attr, None)
        if isinstance(v, int):
            return "0x%08X" % (v & 0xFFFFFFFF)
    args = getattr(exc, "args", None)
    if args:
        for a in args:
            if isinstance(a, int):
                return "0x%08X" % (a & 0xFFFFFFFF)
    return ""


@contextlib.contextmanager
def uia_call(name: str, warn_ms: int = 1200):
    """所有 UIA 操作都从这里过：串行 + 计时 + 异常记录。"""
    STATS["calls"] += 1
    t0 = time.time()
    LOCK.acquire()
    waited = (time.time() - t0) * 1000
    if waited > STATS["lock_wait_ms"]:
        STATS["lock_wait_ms"] = int(waited)
    try:
        yield
    except BaseException as exc:          # noqa: BLE001 —— 要记录所有异常再抛出
        STATS["errors"] += 1
        hr = _hresult_of(exc)
        STATS["last_error"] = "%s: %s" % (type(exc).__name__, str(exc)[:160])
        STATS["last_hresult"] = hr
        STATS["last_error_at"] = time.strftime("%F %T")
        _log("UIA 调用失败 [%s] %s%s"
             % (name, type(exc).__name__,
                (" (HRESULT %s)" % hr) if hr else ""))
        _log("    详情：%s" % str(exc)[:240])
        raise
    finally:
        dur = (time.time() - t0) * 1000
        if dur > STATS["max_call_ms"]:
            STATS["max_call_ms"] = int(dur)
        if dur > warn_ms:
            STATS["slow"] += 1
            _log("UIA 调用偏慢 [%s] %d ms（锁等待 %d ms）"
                 % (name, int(dur), int(waited)))
        LOCK.release()


def busy() -> bool:
    """是否有 UIA 调用正在进行（看门狗用它避免插队）。"""
    got = LOCK.acquire(blocking=False)
    if got:
        LOCK.release()
        return False
    return True


def snapshot() -> dict:
    return dict(STATS)
