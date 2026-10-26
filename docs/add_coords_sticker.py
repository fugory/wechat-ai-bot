# -*- coding: utf-8 -*-
"""给坐标通道加"发表情"：开面板 → 点格子 → 数据库校验，全程不需要 UIA 树。

为什么
----
UIA 树跑 20~40 分钟就死（实测），而发表情只需要两个坐标动作：
  1. 点输入框左侧的 😊 按钮打开面板
  2. 点面板里对应的格子
两者都是固定坐标，跟树无关；发没发出去用数据库回读判定。

面板几何（643x643 时实测）：
  列中心 101/211/321/431/541，行中心 100/210/319/430/523
用比例存，面板大小变了自动换算。
"""
import io

P = r"D:\dsh\projects\ai-chat-bot\coords_send.py"

ADD = '''

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


def send_sticker(hwnd, row: int, col: int, open_wait: float = 1.6) -> bool:
    """点 😊 打开面板 → 点格子。

    返回 True 表示"点击动作已发出"，**发没发出去要调用方读数据库确认**。
    """
    if not _assert_fg(hwnd):
        return False
    x, y = point_of(hwnd, "emoji")
    click(x, y, delay=open_wait)
    panel = find_panel()
    if not panel:
        return False
    pos = panel_cell_screen(panel, row, col)
    if not pos:
        return False
    click(pos[0], pos[1], delay=1.0)
    # 收起面板（再点一次 😊）
    if _assert_fg(hwnd):
        click(x, y, delay=0.3)
    return True
'''

with io.open(P, "r", encoding="utf-8") as f:
    s = f.read()

if "def send_sticker(" in s:
    print("已存在，跳过")
else:
    with io.open(P, "w", encoding="utf-8", newline="\n") as f:
        f.write(s.rstrip("\n") + ADD)
    print("coords_send: 已加 send_sticker（纯坐标）")
