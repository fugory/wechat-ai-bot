# -*- coding: utf-8 -*-
"""P0-3：看门狗降频（90s → 300s）+ 只在空闲时探针 + 状态带统计。

理由：
  * 全树遍历很重，90 秒一次没必要
  * 用户正在操作时探针会抢资源、干扰体验
  * 把 uia_guard 的统计写进 uia_state.json，控制台能看到"调用多少次/错几次/最慢多久"
"""
import io
import re

BR = r"D:\dsh\projects\ai-chat-bot\bridge.py"

with io.open(BR, "r", encoding="utf-8") as f:
    s = f.read()

# 1) 默认间隔 90 → 300，并加空闲/占用的前置判断
OLD = '''def uia_healthy(force: bool = False, interval: float = 90.0) -> bool:
    """轻量健康探测 + 退避自愈。返回当前是否健康。"""
    now = time.time()
    if not force and now - _UIA_WATCH["last_check"] < interval:
        return _UIA_WATCH["ok"]
    _UIA_WATCH["last_check"] = now'''
NEW = '''def _impl_uia_healthy(force: bool = False, interval: float = 300.0) -> bool:
    """轻量健康探测 + 退避自愈。返回当前是否健康。

    P0：默认 300 秒（原 90 秒）；用户正在操作时不探（避免干扰）；
        已有 UIA 调用在进行时不插队。
    """
    now = time.time()
    if not force and now - _UIA_WATCH["last_check"] < interval:
        return _UIA_WATCH["ok"]

    # 已有 UIA 调用在进行 → 不插队
    try:
        import uia_guard
        if not force and uia_guard.busy():
            return _UIA_WATCH["ok"]
    except Exception:
        pass

    # 用户最近 60 秒有输入 → 只在"已经坏了"的时候才探（坏了要修）
    try:
        if not force and _UIA_WATCH["ok"] and not uia_user_idle(60):
            return _UIA_WATCH["ok"]
    except Exception:
        pass

    _UIA_WATCH["last_check"] = now'''
assert OLD in s, "healthy anchor miss"
s = s.replace(OLD, NEW, 1)

# 2) 状态文件带上 uia_guard 统计
OLD2 = '''            json.dump({"ok": _UIA_WATCH["ok"],
                       "fails": _UIA_WATCH["fails"],
                       "gave_up": _UIA_WATCH.get("gave_up", False),
                       "attempts": _UIA_WATCH.get("attempts", 0),
                       "at": datetime.now().strftime("%F %T")}, f)'''
NEW2 = '''            payload = {"ok": _UIA_WATCH["ok"],
                       "fails": _UIA_WATCH["fails"],
                       "gave_up": _UIA_WATCH.get("gave_up", False),
                       "attempts": _UIA_WATCH.get("attempts", 0),
                       "at": datetime.now().strftime("%F %T")}
            try:
                import uia_guard
                st = uia_guard.snapshot()
                payload["stats"] = {
                    "calls": st.get("calls"), "errors": st.get("errors"),
                    "slow": st.get("slow"),
                    "max_call_ms": st.get("max_call_ms"),
                    "lock_wait_ms": st.get("lock_wait_ms"),
                    "last_hresult": st.get("last_hresult"),
                    "last_error": st.get("last_error"),
                }
            except Exception:
                pass
            json.dump(payload, f, ensure_ascii=False)'''
assert OLD2 in s, "state anchor miss"
s = s.replace(OLD2, NEW2, 1)

# 3) 看门狗循环里把 30 秒的 sleep 也放宽到 60 秒（探针本身有节流）
s = s.replace('''                    mem_maintain(_memdb, load_config())
                except Exception:
                    pass
                time.sleep(30)''', '''                    mem_maintain(_memdb, load_config())
                except Exception:
                    pass
                time.sleep(60)''')

with io.open(BR, "w", encoding="utf-8", newline="\n") as f:
    f.write(s)
print("bridge: 看门狗已降频 + 空闲门控 + 统计上报")
