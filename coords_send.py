# -*- coding: utf-8 -*-
"""坐标通道（A 方案）：不依赖 UIA 树也能切会话 / 发文字 / 发表情。

为什么需要
----------
UIA 树会被微信重置且翻开关救不回来（实测诊断为 PROVIDER_DEAD），
但**发送本身不需要树**：点搜索框切会话、点输入框粘贴、点表情按钮和格子，
全部是坐标 + 数据库回读校验。

坐标存的是**相对客户区的比例**，窗口大小变了会自动按比例换算。
默认比例是在这台机器 1652x1140 客户区下量的，可用 calibrate() 重测。
"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import time

HERE = os.path.dirname(os.path.abspath(__file__))
COORD_PATH = os.path.join(HERE, "coords.json")

# 相对客户区的比例（x, y）
DEFAULT = {
    "search": {"ax": "left",  "dx": 205, "ay": "top",    "dy": 70},
    "input":  {"ax": "left",  "dx": 700, "ay": "bottom", "dy": -127},
    "emoji":  {"ax": "left",  "dx": 322, "ay": "bottom", "dy": -51},
    "send":   {"ax": "right", "dx": -71, "ay": "bottom", "dy": -51},
    "_note": "以窗口边缘为锚点的绝对偏移；会话列表宽度固定，不能用比例",
}

u = ctypes.windll.user32
gdi = ctypes.windll.gdi32


def load() -> dict:
    try:
        with open(COORD_PATH, "r", encoding="utf-8-sig") as f:
            d = json.load(f)
        return {**DEFAULT, **d}
    except Exception:
        return dict(DEFAULT)


def save(d: dict) -> None:
    tmp = COORD_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, COORD_PATH)


def find_wechat_any():
    """找微信主窗口（包括隐藏到托盘的），必要时唤醒并返回句柄。"""
    try:
        import psutil
        pids = {p.pid for p in psutil.process_iter(["pid", "name"])
                if (p.info.get("name") or "").lower() in ("weixin.exe", "wechat.exe")}
        if not pids:
            return 0
        best = [0, 0]

        def cb(h, _):
            pid = wt.DWORD()
            u.GetWindowThreadProcessId(h, ctypes.byref(pid))
            if pid.value not in pids:
                return True
            cls = ctypes.create_unicode_buffer(256)
            u.GetClassNameW(h, cls, 256)
            if not cls.value.startswith("Qt") or "Tray" in cls.value:
                return True
            r = wt.RECT()
            u.GetWindowRect(h, ctypes.byref(r))
            area = (r.right - r.left) * (r.bottom - r.top)
            if area > best[0]:
                best[0], best[1] = area, h
            return True

        u.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p,
                                        ctypes.c_void_p)(cb), 0)
        h = best[1]
        if not h:
            return 0
        if not u.IsWindowVisible(h):
            u.ShowWindow(h, 5)
            time.sleep(0.4)
        if u.IsIconic(h):
            u.ShowWindow(h, 9)
            time.sleep(0.4)
        return h
    except Exception:
        return 0


def client_box(hwnd):
    """返回客户区在屏幕上的矩形 (x, y, w, h)。"""
    r = wt.RECT()
    u.GetClientRect(hwnd, ctypes.byref(r))
    pt = wt.POINT(0, 0)
    u.ClientToScreen(hwnd, ctypes.byref(pt))
    return pt.x, pt.y, r.right - r.left, r.bottom - r.top


def point_of(hwnd, key: str):
    """按"边缘锚点 + 绝对偏移"算屏幕坐标。"""
    d = load()
    spec = d.get(key) or DEFAULT[key]
    if isinstance(spec, (list, tuple)):      # 兼容旧格式（比例），直接换算
        fx, fy = spec[0], spec[1]
        x, y, w, h = client_box(hwnd)
        return int(x + fx * w), int(y + fy * h)
    x, y, w, h = client_box(hwnd)
    px = (x + spec["dx"]) if spec["ax"] == "left" else (x + w + spec["dx"])
    py = (y + spec["dy"]) if spec["ay"] == "top" else (y + h + spec["dy"])
    return int(px), int(py)


# ---------------- 基础输入 ----------------

def foreground(hwnd) -> bool:
    """把微信置前，并确认它真的在前台且可见。"""
    try:
        if not u.IsWindowVisible(hwnd):
            u.ShowWindow(hwnd, 5)
            time.sleep(0.3)
        if u.IsIconic(hwnd):
            u.ShowWindow(hwnd, 9)
        u.SetForegroundWindow(hwnd)
        time.sleep(0.25)
        if u.GetForegroundWindow() == hwnd:
            return True
        # 附到前台线程再抢一次
        fg = u.GetForegroundWindow()
        tid_fg = u.GetWindowThreadProcessId(fg, None)
        tid_me = u.GetWindowThreadProcessId(hwnd, None)
        if tid_fg != tid_me:
            u.AttachThreadInput(tid_fg, tid_me, True)
            u.SetForegroundWindow(hwnd)
            u.AttachThreadInput(tid_fg, tid_me, False)
        time.sleep(0.2)
        return u.GetForegroundWindow() == hwnd
    except Exception:
        return False


def _wechat_pids():
    try:
        import psutil
        return {p.pid for p in psutil.process_iter(["pid", "name"])
                if (p.info.get("name") or "").lower() in ("weixin.exe", "wechat.exe")}
    except Exception:
        return set()


def _assert_fg(hwnd) -> bool:
    """按键前的前台守卫：主窗口还在、且前台窗口属于微信进程。

    不要求前台必须是主窗口 —— 微信的搜索弹窗、表情面板都是独立窗口，
    但它们同属 Weixin 进程，往那里输入是安全的（不会打到别的程序）。
    """
    try:
        if not hwnd or not u.IsWindow(hwnd):
            return False
        if not u.IsWindowVisible(hwnd) or u.IsIconic(hwnd):
            return False
        fg = u.GetForegroundWindow()
        if fg == hwnd:
            return True
        pid = wt.DWORD()
        u.GetWindowThreadProcessId(fg, ctypes.byref(pid))
        return pid.value in _wechat_pids()
    except Exception:
        return False


def click(x: int, y: int, delay: float = 0.25) -> None:
    u.SetCursorPos(int(x), int(y))
    time.sleep(0.05)
    u.mouse_event(0x0002, 0, 0, 0, 0)
    u.mouse_event(0x0004, 0, 0, 0, 0)
    time.sleep(delay)


def key(vk: int, mods=()) -> None:
    for m in mods:
        u.keybd_event(m, 0, 0, 0)
    u.keybd_event(vk, 0, 0, 0)
    u.keybd_event(vk, 0, 2, 0)
    for m in reversed(list(mods)):
        u.keybd_event(m, 0, 2, 0)
    time.sleep(0.08)


VK_CTRL, VK_A, VK_V, VK_RETURN, VK_ESC = 0x11, 0x41, 0x56, 0x0D, 0x1B


def clipboard_put(text: str) -> bool:
    try:
        import win32clipboard
        import win32con
        win32clipboard.OpenClipboard()
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
        win32clipboard.CloseClipboard()
        return True
    except Exception:
        return False


def clipboard_get() -> str:
    try:
        import win32clipboard
        import win32con
        win32clipboard.OpenClipboard()
        try:
            return win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT) or ""
        finally:
            win32clipboard.CloseClipboard()
    except Exception:
        return ""


# ---------------- 三个动作 ----------------

def open_chat(hwnd, name: str) -> bool:
    """用搜索框精确切会话（不依赖列表顺序和树的健康）。"""
    if not foreground(hwnd):
        return False
    x, y = point_of(hwnd, "search")
    click(x, y)
    if not _assert_fg(hwnd):
        return False
    key(VK_A, (VK_CTRL,))
    key(0x2E)                      # Delete 清空搜索框
    if not _assert_fg(hwnd):
        return False
    if not clipboard_put(name):
        return False
    key(VK_V, (VK_CTRL,))
    time.sleep(0.6)
    if not _assert_fg(hwnd):
        return False
    key(VK_RETURN)
    time.sleep(1.0)
    key(VK_ESC)                    # 收起搜索面板
    time.sleep(0.4)
    # 搜索弹窗关闭会把焦点还给上一个窗口（可能是浏览器）→ 重新拉回微信
    for _ in range(3):
        foreground(hwnd)
        time.sleep(0.35)
        if _assert_fg(hwnd):
            return True
    return False


def send_text(hwnd, text: str) -> bool:
    """点输入框 → 粘贴 → 回车。调用方负责数据库回读校验。"""
    if not foreground(hwnd):
        foreground(hwnd)
        time.sleep(0.4)
        if not foreground(hwnd):
            return False
    x, y = point_of(hwnd, "input")
    click(x, y)
    if not _assert_fg(hwnd):
        return False
    key(VK_A, (VK_CTRL,))
    key(0x2E)
    if not _assert_fg(hwnd):
        return False
    if not clipboard_put(text):
        return False
    key(VK_V, (VK_CTRL,))
    time.sleep(0.5)
    if not _assert_fg(hwnd):
        return False
    key(VK_RETURN)
    time.sleep(0.5)
    return True


def open_emoji_panel(hwnd) -> bool:
    if not foreground(hwnd):
        return False
    x, y = point_of(hwnd, "emoji")
    click(x, y, delay=1.0)
    return True


def calibrate(hwnd) -> dict:
    """按当前窗口重测三个比例（用默认比例反推，仅用于窗口比例变化后的自校正）。"""
    d = load()
    x, y, w, h = client_box(hwnd)
    # 默认比例是在 1652x1140 下量的；按当前尺寸保持"相对位置"即可
    d["_measured_client"] = [w, h]
    save(d)
    return d

# ---------------- 表情面板（纯坐标，不需要 UIA） ----------------

PANEL_REF = 643.0
PANEL_COLS = [101, 211, 321, 431, 541]
PANEL_ROWS = [100, 210, 319, 430, 523]


def find_panel():
    """找表情面板窗口（独立弹窗；用窗口枚举，不碰 UIA）。"""
    try:
        import psutil
        pids = {p.pid for p in psutil.process_iter(["pid", "name"])
                if (p.info.get("name") or "").lower() in ("weixin.exe", "wechat.exe")}
        if not pids:
            return 0
        found = []

        def cb(h, _):
            pid = wt.DWORD()
            u.GetWindowThreadProcessId(h, ctypes.byref(pid))
            if pid.value not in pids or not u.IsWindowVisible(h):
                return True
            cls = ctypes.create_unicode_buffer(256)
            u.GetClassNameW(h, cls, 256)
            if "ToolSaveBits" not in cls.value:
                return True
            r = wt.RECT()
            u.GetWindowRect(h, ctypes.byref(r))
            w, hh = r.right - r.left, r.bottom - r.top
            if 300 < w < 900 and 300 < hh < 900:
                found.append((w * hh, h))
            return True

        u.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p,
                                        ctypes.c_void_p)(cb), 0)
        found.sort(reverse=True)
        return found[0][1] if found else 0
    except Exception:
        return 0


def panel_cell_screen(panel_hwnd, row: int, col: int):
    """把 (行,列) 换算成屏幕坐标。"""
    r = wt.RECT()
    u.GetWindowRect(panel_hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    if w <= 0 or h <= 0:
        return None
    try:
        fx = PANEL_COLS[int(col)] / PANEL_REF
        fy = PANEL_ROWS[int(row)] / PANEL_REF
    except Exception:
        return None
    return int(r.left + fx * w), int(r.top + fy * h)


VK_ALT, VK_E, VK_ESC = 0x12, 0x45, 0x1B


def toggle_panel_hotkey() -> None:
    """Alt+E：微信自带的表情面板快捷键。"""
    u.keybd_event(VK_ALT, 0, 0, 0)
    time.sleep(0.05)
    u.keybd_event(VK_E, 0, 0, 0)
    time.sleep(0.05)
    u.keybd_event(VK_E, 0, 2, 0)
    u.keybd_event(VK_ALT, 0, 2, 0)


def close_panel() -> None:
    u.keybd_event(VK_ESC, 0, 0, 0)
    u.keybd_event(VK_ESC, 0, 2, 0)
    time.sleep(0.3)


def open_panel_robust(hwnd) -> int:
    """三级兜底开面板，返回面板句柄（0 = 没打开）。"""
    if find_panel():
        return find_panel()

    import ctypes
    import ctypes.wintypes as _wt
    rr = _wt.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(rr))
    W, H = rr.right - rr.left, rr.bottom - rr.top

    # ① 模板匹配：只在"输入区工具条"那条窄带里找（避开聊天气泡里的表情）
    try:
        import locate_icon
        band = (int(W * 0.18), int(H * 0.90), int(W * 0.55), H)
        pos = locate_icon.locate(hwnd, "emoji", region=band)
        if pos:
            click(pos[0], pos[1], delay=1.8)
            if find_panel():
                return find_panel()
    except Exception:
        pass

    # ② 锚点坐标
    try:
        x, y = point_of(hwnd, "emoji")
        click(x, y, delay=1.8)
        if find_panel():
            return find_panel()
    except Exception:
        pass

    # ③ Alt+E
    try:
        toggle_panel_hotkey()
        time.sleep(1.6)
        if find_panel():
            return find_panel()
    except Exception:
        pass
    return 0


def send_sticker(hwnd, row: int, col: int, open_wait: float = 1.8) -> bool:
    """开面板（三级兜底）→ 点格子 → 关面板。发没发出去由调用方读库确认。"""
    if not _assert_fg(hwnd):
        foreground(hwnd)
        time.sleep(0.4)
        if not _assert_fg(hwnd):
            return False
    panel = open_panel_robust(hwnd)
    if not panel:
        return False
    pos = panel_cell_screen(panel, row, col)
    if not pos:
        close_panel()
        return False
    click(pos[0], pos[1], delay=1.0)
    close_panel()
    return True