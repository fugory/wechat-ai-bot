# -*- coding: utf-8 -*-
"""表情发送改用 Alt+E 开面板（微信自带快捷键），不再依赖 😊 坐标。

实测：前台状态下 Alt+E 能稳定打开 643x643 的面板；
      😊 坐标在不同窗口尺寸下会点偏（用户校准那次就落到会话列表里了）。
"""
import io

P = r"D:\dsh\projects\ai-chat-bot\coords_send.py"

OLD = '''def send_sticker(hwnd, row: int, col: int, open_wait: float = 1.6) -> bool:
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
    return True'''

NEW = '''VK_ALT, VK_E, VK_ESC = 0x12, 0x45, 0x1B


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


def send_sticker(hwnd, row: int, col: int, open_wait: float = 1.8) -> bool:
    """Alt+E 开面板 → 点格子 → 关面板。

    返回 True 表示动作已发出，**发没发出去由调用方读数据库确认**。
    """
    if not _assert_fg(hwnd):
        return False
    if find_panel():
        close_panel()
    toggle_panel_hotkey()
    time.sleep(open_wait)
    panel = find_panel()
    if not panel:
        return False
    pos = panel_cell_screen(panel, row, col)
    if not pos:
        close_panel()
        return False
    click(pos[0], pos[1], delay=1.0)
    close_panel()
    return True'''

with io.open(P, "r", encoding="utf-8") as f:
    s = f.read()
assert OLD in s, "send_sticker 没匹配上"
s = s.replace(OLD, NEW, 1)
with io.open(P, "w", encoding="utf-8", newline="\n") as f:
    f.write(s)
print("coords_send: send_sticker 改用 Alt+E")
