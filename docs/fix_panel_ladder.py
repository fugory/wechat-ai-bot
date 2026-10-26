# -*- coding: utf-8 -*-
"""开表情面板改成三级兜底，每级都验证面板是否真的出现。

1. 模板匹配（限定"输入框工具条"那条窄带，避免匹配到聊天气泡里的表情）
2. 锚点坐标（用户校准值）
3. Alt+E 快捷键

任何一级成功就停；全失败才返回 False，绝不"点了就当成了"。
"""
import io

P = r"D:\dsh\projects\ai-chat-bot\coords_send.py"
with io.open(P, "r", encoding="utf-8") as f:
    s = f.read()

start = s.find("def send_sticker(")
assert start > 0
end = s.find("\n\n\n", start)
if end < 0:
    end = len(s)
old_block = s[start:end]

NEW = '''def open_panel_robust(hwnd) -> int:
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
    return True'''

s = s[:start] + NEW + s[end:]
with io.open(P, "w", encoding="utf-8", newline="\n") as f:
    f.write(s)
print("coords_send: 开面板改为三级兜底")
