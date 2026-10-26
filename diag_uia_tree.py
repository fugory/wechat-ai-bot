# -*- coding: utf-8 -*-
"""UIA 树故障诊断：分清"开关关了（可恢复）"和"界面提供者死了（要重启微信）"。

为什么需要它
------------
现在自愈是一把抓：点聊天页、热激活、还原窗口、重置 UIA 全试一遍，
试不通就提示"重启微信"。但其中有些故障其实**不用重启微信**——
只是开关没翻成功，换个全新进程重翻一次就能救回来。

分阶段（每步都记录耗时和结果）
------------------------------
  1. 窗口：进程在不在、窗口在不在、是否最小化、客户区多大
  2. 只读探测：ControlFromHandle 直接遍历（不碰库、不激活）
  3. 库探测：bridge.uia_session_items()（库自己的路径）
  4. 重激活：reset_uia → 全新 WeChatUIA → ensure_window（翻 gate）→ 再读
  5. 冷却重试：隔 2 秒再来两轮

判定
----
  HEALTHY              一开始就能读到
  RECOVERABLE          需要重激活才读到 → 说明可以自动恢复，不用重启微信
  PROVIDER_DEAD        重激活也读不到 → 只能重启微信
  WECHAT_GONE          微信进程/窗口都没有

用法
----
  python diag_uia_tree.py            # 人读的
  python diag_uia_tree.py --json     # 最后多打一行 DIAG_RESULT {...}（给控制台用）
"""
import io
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "gui"))


def run(json_mode: bool = False) -> int:
    stages = []
    t_start = time.time()

    def note(name, ok, detail="", ms=0):
        stages.append({"stage": name, "ok": bool(ok), "detail": detail,
                       "ms": int(ms)})
        print("[%-14s] %s  %s%s" % (name, "OK " if ok else "FAIL",
                                    detail, (" (%d ms)" % ms) if ms else ""))

    # ---------- 1. 窗口 ----------
    t0 = time.time()
    win_info = {}
    try:
        import ctypes
        import ctypes.wintypes as wt
        import psutil
        import wxwin
        pids = [p.pid for p in psutil.process_iter(["pid", "name"])
                if (p.info.get("name") or "").lower() in ("weixin.exe", "wechat.exe")]
        h = wxwin.find_wechat()
        if not pids:
            note("window", False, "微信进程不在", (time.time() - t0) * 1000)
            verdict = "WECHAT_GONE"
            return _finish(stages, verdict, stages, json_mode, t_start)
        if not h:
            note("window", False, "进程在（%d 个），但找不到主窗口" % len(pids),
                 (time.time() - t0) * 1000)
            verdict = "WECHAT_GONE"
            return _finish(stages, verdict, stages, json_mode, t_start)
        size = wxwin.client_size(h) or (0, 0)
        minimized = bool(ctypes.windll.user32.IsIconic(h))
        visible = bool(ctypes.windll.user32.IsWindowVisible(h))
        win_info = {"hwnd": hex(h), "client": size, "minimized": minimized,
                    "visible": visible, "pids": pids}
        note("window", True, "客户区 %dx%d 可见=%s 最小化=%s"
             % (size[0], size[1], visible, minimized), (time.time() - t0) * 1000)
    except Exception as e:
        note("window", False, "检查异常：%s" % e)
        return _finish(stages, "PROVIDER_DEAD", stages, json_mode, t_start)

    # ---------- 2. 只读探测 ----------
    t0 = time.time()
    quiet_n = 0
    try:
        import uiautomation as auto
        root = auto.ControlFromHandle(h)
        names = []

        def walk(el, d=0):
            if d > 26:
                return
            try:
                aid = el.AutomationId or ""
            except Exception:
                return
            if aid.startswith("session_item_"):
                names.append(aid)
            try:
                for k in el.GetChildren():
                    walk(k, d + 1)
            except Exception:
                pass

        if root is not None:
            walk(root)
        quiet_n = len(names)
        note("read_quiet", quiet_n > 0, "读到 %d 个会话项" % quiet_n,
             (time.time() - t0) * 1000)
    except Exception as e:
        note("read_quiet", False, "只读探测异常：%s" % e)

    # ---------- 3. 库探测 ----------
    import bridge as b
    b.load_dotenv(os.path.join(HERE, ".env"))

    t0 = time.time()
    lib_n = 0
    try:
        lib_n = len(b.uia_session_items())
        note("read_lib", lib_n > 0, "库读到 %d 个会话项" % lib_n,
             (time.time() - t0) * 1000)
    except Exception as e:
        note("read_lib", False, "库读取异常：%s" % e)

    if lib_n > 0 or quiet_n > 0:
        return _finish(stages, "HEALTHY", stages, json_mode, t_start)

    # ---------- 4. 重激活（全新进程里翻 gate） ----------
    t0 = time.time()
    healed = 0
    try:
        b.reset_uia()
        u = b.get_uia()          # 新建实例
        ok_win = False
        try:
            ok_win = bool(u.ensure_window())
        except Exception:
            ok_win = False
        time.sleep(1.0)
        healed = len(b.uia_session_items())
        note("reactivate", healed > 0,
             "ensure_window=%s，重激活后读到 %d 个会话项" % (ok_win, healed),
             (time.time() - t0) * 1000)
    except Exception as e:
        note("reactivate", False, "重激活异常：%s" % e)

    if healed > 0:
        return _finish(stages, "RECOVERABLE", stages, json_mode, t_start)

    # ---------- 5. 冷却重试 ----------
    for i in (1, 2):
        t0 = time.time()
        try:
            time.sleep(2.0)
            b.reset_uia()
            u = b.get_uia()
            try:
                u.ensure_window()
            except Exception:
                pass
            time.sleep(0.8)
            n = len(b.uia_session_items())
            note("retry_%d" % i, n > 0, "第 %d 次重试读到 %d 个" % (i, n),
                 (time.time() - t0) * 1000)
            if n > 0:
                return _finish(stages, "RECOVERABLE", stages, json_mode, t_start)
        except Exception as e:
            note("retry_%d" % i, False, "重试异常：%s" % e)

    return _finish(stages, "PROVIDER_DEAD", stages, json_mode, t_start)


def _finish(stages, verdict, _s, json_mode, t_start):
    advice = {
        "HEALTHY": "树正常，不用处理",
        "RECOVERABLE": "重激活就能恢复 → 可以给 bot 加自动恢复，不用重启微信",
        "PROVIDER_DEAD": "重激活也读不到 → 只能重启微信客户端",
        "WECHAT_GONE": "微信没在运行（或没登录），先把它打开",
    }.get(verdict, "")
    total = int((time.time() - t_start) * 1000)
    print("\n判定：%s —— %s（共 %d ms）" % (verdict, advice, total))
    if json_mode:
        print("DIAG_RESULT " + json.dumps({
            "verdict": verdict, "advice": advice, "total_ms": total,
            "stages": stages}, ensure_ascii=False))
    return 0 if verdict == "HEALTHY" else 1


if __name__ == "__main__":
    sys.exit(run(json_mode="--json" in sys.argv))
