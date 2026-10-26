# -*- coding: utf-8 -*-
"""UIA 修复阶梯 + 可选自动重启微信。

背景
----
实测：树被重置后，"翻开关"（热激活）**通常救不回来**，只有重启微信能恢复。
但我们没法证明"永远救不回来"，所以做成阶梯：便宜的招先试，最后才是重启。

阶梯（每步失败才进入下一步，全部有日志）
----
1. 热激活 + 重读                     （最便宜，可能有效）
2. 点聊天页 / 切两个 tab 强迫重绘      （PostMessage，不抢焦点）
3. 最小化 → 还原                      （会短暂抢焦点）
4. 重启微信（**可选**，默认关）

配置：config.wechat.repair = {auto_restart: false, idle_s: 300, max_step: 3}
"""
import io
import json

BR = r"D:\dsh\projects\ai-chat-bot\bridge.py"
CFG = r"D:\dsh\projects\ai-chat-bot\config.json"

with io.open(CFG, "r", encoding="utf-8-sig") as f:
    cfg = json.load(f)
wc = cfg.setdefault("wechat", {})
wc.setdefault("repair", {
    "auto_restart": False,
    "idle_s": 300,
    "max_step": 3,
    "_note": "树挂了自动尝试修复；auto_restart=True 时最后会重启微信（可能需要扫码）",
})
with io.open(CFG, "w", encoding="utf-8") as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2)
print("config.wechat.repair 已加")

with io.open(BR, "r", encoding="utf-8") as f:
    s = f.read()

LADDER = '''def uia_repair_ladder(force: bool = False) -> bool:
    """UIA 树挂了时的修复阶梯。返回是否修好。

    只在"看门狗已经判定挂了"时调用，内部有节流，不会反复折腾。
    """
    now = time.time()
    if not force and now - float(_UIA_WATCH.get("last_ladder") or 0) < 180:
        return bool(_UIA_WATCH.get("ok"))
    _UIA_WATCH["last_ladder"] = now

    rp = ((load_config().get("wechat") or {}).get("repair") or {})
    max_step = int(rp.get("max_step") or 3)

    # 1) 热激活 + 重读
    try:
        reset_uia()
        u = get_uia()
        try:
            u.ensure_window()
        except Exception:
            pass
        time.sleep(1.2)
        if uia_session_items():
            log("UIA 修复成功：热激活（第 1 步）")
            return True
    except Exception as e:
        log("修复第 1 步异常（忽略）：%s" % e)
    if max_step < 2:
        return False

    # 2) 切两个 tab 强迫重绘（PostMessage，不抢焦点）
    try:
        bg = get_bg()
        for name in ("通讯录", "微信"):
            c = bg.uia_screen_center(lambda e, n=name: (e.Name or "") == n)
            if c:
                bg.click_screen(*c)
                time.sleep(0.8)
        time.sleep(1.0)
        if uia_session_items():
            log("UIA 修复成功：切换 tab 重绘（第 2 步）")
            return True
    except Exception as e:
        log("修复第 2 步异常（忽略）：%s" % e)
    if max_step < 3:
        return False

    # 3) 最小化 → 还原（会短暂抢焦点）
    try:
        import wxwin
        h = wxwin.find_wechat()
        if h:
            ctypes.windll.user32.ShowWindow(h, 6)   # MINIMIZE
            time.sleep(1.0)
            ctypes.windll.user32.ShowWindow(h, 9)   # RESTORE
            time.sleep(1.5)
        if uia_session_items():
            log("UIA 修复成功：最小化还原（第 3 步）")
            return True
    except Exception as e:
        log("修复第 3 步异常（忽略）：%s" % e)

    if not rp.get("auto_restart"):
        log("UIA 修复：前三步都失败；自动重启微信未开启（wechat.repair.auto_restart=false）")
        return False

    # 4) 重启微信（可选）
    try:
        if not uia_user_idle(rp.get("idle_s") or 300):
            log("UIA 修复：检测到你正在操作，暂不重启微信（空闲后再试）")
            return False
        exe = ""
        try:
            exe = ((load_config().get("wechat") or {}).get("exe_path") or "")
        except Exception:
            pass
        if not exe or not os.path.isfile(exe):
            log("UIA 修复：没有配置微信路径，无法自动重启")
            return False
        log("UIA 修复：准备重启微信（会自动重新登录；若停在扫码界面请手动扫）")
        import psutil
        for p in psutil.process_iter(["pid", "name"]):
            if (p.info.get("name") or "").lower() in ("weixin.exe", "wechat.exe"):
                try:
                    p.kill()
                except Exception:
                    pass
        time.sleep(6)
        import subprocess
        subprocess.Popen([exe], cwd=os.path.dirname(exe))
        for _ in range(40):
            time.sleep(1.5)
            if uia_session_items():
                log("UIA 修复成功：重启微信（第 4 步）")
                return True
        log("UIA 修复：重启后仍读不到 —— 可能停在扫码界面，请手动登录")
        return False
    except Exception as e:
        log("修复第 4 步异常：%s" % e)
        return False


def uia_user_idle(seconds: int) -> bool:
    """最近 N 秒没有键鼠输入（用 GetLastInputInfo）。"""
    try:
        import ctypes
        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
        li = LASTINPUTINFO()
        li.cbSize = ctypes.sizeof(li)
        ctypes.windll.user32.GetLastInputInfo(ctypes.byref(li))
        tick = ctypes.windll.kernel32.GetTickCount()
        idle_ms = tick - li.dwTime
        return idle_ms >= seconds * 1000
    except Exception:
        return True


'''

anchor = "def uia_healthy("
assert anchor in s, "anchor miss"
s = s.replace(anchor, LADDER + anchor, 1)

# 看门狗：判定挂了之后先走阶梯，再决定是否放弃
OLD = '''    if now - _UIA_WATCH["last_heal"] >= wait:
        _UIA_WATCH["last_heal"] = now
        _UIA_WATCH["attempts"] = attempts + 1'''
NEW = '''    if now - _UIA_WATCH["last_heal"] >= wait:
        _UIA_WATCH["last_heal"] = now
        _UIA_WATCH["attempts"] = attempts + 1
        # 先走修复阶梯（比单次 uia_heal 更有层次），成功就直接返回
        try:
            if uia_repair_ladder():
                _UIA_WATCH["ok"] = True
                _UIA_WATCH["fails"] = 0
                _UIA_WATCH["attempts"] = 0
                _write_uia_state()
                return True
        except Exception:
            pass'''
assert OLD in s, "watch anchor miss"
s = s.replace(OLD, NEW, 1)

with io.open(BR, "w", encoding="utf-8", newline="\n") as f:
    f.write(s)
print("bridge: 修复阶梯已接入看门狗")
