# -*- coding: utf-8 -*-
"""微信窗口的"伪嵌入"控制：去标题栏 + 贴边跟随 + 显隐/关闭。

为什么不用 SetParent 真嵌入：
  Qt 自绘窗口被重设父窗口后，UIA 焦点、DPI 缩放、输入法位置都可能出问题，
  而我们的发送链路完全依赖 UIA，一旦坏掉就是"点不到、发不出"。
  所以只改外观和位置，不动父子关系。

所有操作都可逆：原样式保存在 gui/wx_style.json，随时可还原。
"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
STYLE_PATH = os.path.join(HERE, "wx_style.json")

GWL_STYLE = -16
WS_CAPTION = 0x00C00000
SW_RESTORE, SW_MINIMIZE, SW_HIDE, SW_SHOW = 9, 6, 0, 5
SWP_NOACTIVATE, SWP_SHOWWINDOW, SWP_NOZORDER = 0x0010, 0x0040, 0x0004

user32 = ctypes.windll.user32

_state = {"follow": True, "hidden": False, "caption_stripped": False}
_lock = threading.Lock()


def _win_title(h):
    n = user32.GetWindowTextLengthW(h)
    if n <= 0:
        return ""
    b = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(h, b, n + 1)
    return b.value


def _rect(h):
    r = wt.RECT()
    user32.GetWindowRect(h, ctypes.byref(r))
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def find_wechat():
    try:
        import psutil
        pids = {p.pid for p in psutil.process_iter(["pid", "name"])
                if (p.info.get("name") or "").lower() in ("weixin.exe", "wechat.exe")}
        if not pids:
            return None
        best = []

        def cb(hwnd, _):
            pid = wt.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value not in pids:
                return True
            cls = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cls, 256)
            if not cls.value.startswith("Qt") or "Tray" in cls.value:
                return True
            title = _win_title(hwnd)
            if "微信" not in title and "Weixin" not in title:
                return True
            visible = bool(user32.IsWindowVisible(hwnd))
            iconic = bool(user32.IsIconic(hwnd))
            if not visible and not iconic:
                return True
            l, t, w, h = _rect(hwnd)
            # 用**客户区**面积排序：微信可能同时存在主窗和若干小窗（登录/提示），
            # 只有客户区最大的那个才是能操作的主窗口
            cr = wt.RECT()
            user32.GetClientRect(hwnd, ctypes.byref(cr))
            w, h = cr.right - cr.left, cr.bottom - cr.top
            if not iconic and (l < -1000 or t < -1000):
                return True
            area = w * h if w > 0 and h > 0 else 1
            best.append((area, hwnd))
            return True

        user32.EnumWindows(ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)(cb), 0)
        return max(best)[1] if best else None
    except Exception:
        return None


def find_gui():
    """找控制台窗口（按标题）。"""
    found = []

    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            t = _win_title(hwnd)
            if "ai-chat-bot" in t:
                l, tt, w, h = _rect(hwnd)
                if w > 400 and h > 300:
                    found.append((w * h, hwnd))
        return True

    user32.EnumWindows(ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)(cb), 0)
    return max(found)[1] if found else None


# ---------------- 标题栏 ----------------

def _load_style():
    try:
        with open(STYLE_PATH, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_style(d):
    try:
        with open(STYLE_PATH, "w", encoding="utf-8") as f:
            json.dump(d, f)
    except Exception:
        pass


def strip_caption(h=None):
    """去掉微信标题栏（可逆）。"""
    h = h or find_wechat()
    if not h:
        return False
    try:
        cur = user32.GetWindowLongW(h, GWL_STYLE)
        saved = _load_style()
        if "style" not in saved:
            saved["style"] = cur
            _save_style(saved)
        new = saved["style"] & ~WS_CAPTION
        if cur != new:
            user32.SetWindowLongW(h, GWL_STYLE, new)
            user32.SetWindowPos(h, 0, 0, 0, 0, 0,
                                SWP_NOZORDER | SWP_NOACTIVATE | 0x0001 | 0x0002)
        _state["caption_stripped"] = True
        return True
    except Exception:
        return False


def restore_caption():
    h = find_wechat()
    saved = _load_style()
    if not h or "style" not in saved:
        return False
    try:
        user32.SetWindowLongW(h, GWL_STYLE, int(saved["style"]))
        user32.SetWindowPos(h, 0, 0, 0, 0, 0,
                            SWP_NOZORDER | SWP_NOACTIVATE | 0x0001 | 0x0002)
        _state["caption_stripped"] = False
        return True
    except Exception:
        return False


# ---------------- 位置与显隐 ----------------

def work_area():
    """主屏工作区（排除任务栏）。"""
    try:
        r = wt.RECT()
        user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(r), 0)  # SPI_GETWORKAREA
        return r.left, r.top, r.right - r.left, r.bottom - r.top
    except Exception:
        return 0, 0, 1920, 1080


def smart_target(gui_rect, w=1100, h=760):
    """算微信该放哪：右 → 左 → 夹进屏幕。"""
    gl, gt, gw, gh = gui_rect
    wl, wt_, ww, wh = work_area()
    y = max(wt_, min(gt + 30, wt_ + wh - h))
    right_x = gl + gw
    if right_x + w <= wl + ww:
        return right_x, y, "right"
    left_x = gl - w
    if left_x >= wl:
        return left_x, y, "left"
    # 都放不下：贴右边但夹进屏幕
    return max(wl, wl + ww - w), y, "clamped"


def client_size(h=None):
    """客户区尺寸（我们点击坐标的基准）。"""
    h = h or find_wechat()
    if not h:
        return None
    try:
        r = wt.RECT()
        user32.GetClientRect(h, ctypes.byref(r))
        return (r.right - r.left, r.bottom - r.top)
    except Exception:
        return None


def restore_size(w=1100, h=760, tries=3):
    """尝试把客户区调整到 w×h。微信可能拒绝，返回 False 时请让用户手动拖。

    防的就是"客户区读数无效 → 按比例越缩越小"这个坑。
    """
    hw = find_wechat()
    if not hw:
        return False
    try:
        if user32.IsIconic(hw):
            user32.ShowWindow(hw, SW_RESTORE)
            time.sleep(0.8)
        last = None
        for _ in range(tries):
            wr = wt.RECT()
            user32.GetWindowRect(hw, ctypes.byref(wr))
            cr = wt.RECT()
            user32.GetClientRect(hw, ctypes.byref(cr))
            cw, ch = cr.right - cr.left, cr.bottom - cr.top
            if cw >= 400 and ch >= 300 and abs(cw - w) <= 4 and abs(ch - h) <= 4:
                return True
            if cw < 400 or ch < 300:
                # 读数无效：别按比例，直接用"边框回退值"给绝对尺寸
                fw, fh = 18, 45
                want_w, want_h = w + fw, h + fh
            else:
                fw = max(0, (wr.right - wr.left) - cw)
                fh = max(0, (wr.bottom - wr.top) - ch)
                want_w, want_h = w + fw, h + fh
            cur = (wr.right - wr.left, wr.bottom - wr.top)
            if last == cur:
                return False        # 上一轮改不动 → 微信拒绝，别折腾了
            last = cur
            user32.SetWindowPos(hw, 0, wr.left, wr.top, want_w, want_h,
                                SWP_NOZORDER | SWP_NOACTIVATE)
            time.sleep(0.5)
        cw, ch = client_size(hw) or (0, 0)
        return abs(cw - w) <= 4 and abs(ch - h) <= 4
    except Exception:
        return False


def place(x, y, w=1100, h=760):
    hw = find_wechat()
    if not hw:
        return False
    try:
        if user32.IsIconic(hw):
            user32.ShowWindow(hw, SW_RESTORE)
        user32.SetWindowPos(hw, 0, int(x), int(y), int(w), int(h),
                            SWP_NOZORDER | SWP_NOACTIVATE | SWP_SHOWWINDOW)
        return True
    except Exception:
        return False


def show():
    hw = find_wechat()
    if not hw:
        return False
    user32.ShowWindow(hw, SW_RESTORE)
    _state["hidden"] = False
    return True


def hide():
    hw = find_wechat()
    if not hw:
        return False
    user32.ShowWindow(hw, SW_HIDE)
    _state["hidden"] = True
    return True


def minimize():
    hw = find_wechat()
    if not hw:
        return False
    user32.ShowWindow(hw, SW_MINIMIZE)
    return True


def close_wechat():
    hw = find_wechat()
    if not hw:
        return False
    WM_CLOSE = 0x0010
    user32.PostMessageW(hw, WM_CLOSE, 0, 0)
    return True


def set_follow(on: bool):
    _state["follow"] = bool(on)
    return _state["follow"]


def state():
    hw = find_wechat()
    gui = find_gui()
    return {
        "wechat": bool(hw),
        "gui": bool(gui),
        "follow": _state["follow"],
        "hidden": _state["hidden"],
        "caption_stripped": _state["caption_stripped"],
        "wechat_rect": _rect(hw) if hw else None,
        "gui_rect": _rect(gui) if gui else None,
    }


# ---------------- 跟随线程 ----------------

def start_monitor(width=1100, height=760, interval=0.9):
    """跟随：把微信摆到控制台右侧。

    重要：**不重设窗口样式**（会导致重绘闪烁）、**不激活窗口**（会抢输入焦点）。
    默认只在显式开启后才工作。
    """

    def loop():
        last = None
        while True:
            try:
                if _state["follow"] and not _state["hidden"]:
                    gui = find_gui()
                    hw = find_wechat()
                    if gui and hw and not user32.IsIconic(gui) and not user32.IsIconic(hw):
                        tx, ty, _how = smart_target(_rect(gui), width, height)
                        if last is None or abs(tx - last[0]) > 8 or abs(ty - last[1]) > 8:
                            cur = _rect(hw)
                            if abs(cur[0] - tx) > 8 or abs(cur[1] - ty) > 8:
                                user32.SetWindowPos(hw, 0, int(tx), int(ty), cur[2], cur[3],
                                                    SWP_NOZORDER | SWP_NOACTIVATE)
                                last = (tx, ty)
                time.sleep(interval)
            except Exception:
                time.sleep(1.5)

    t = threading.Thread(target=loop, daemon=True)
    t.start()
    return t
