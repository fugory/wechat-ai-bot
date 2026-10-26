# -*- coding: utf-8 -*-
"""给坐标通道加"前台守卫"：每次按键/粘贴前确认微信仍是前台，否则立刻中止。

事故：微信缩回托盘后 foreground() 仍返回 True，随后 Ctrl+A/Ctrl+V/Enter
      打到了别的窗口（DSH），把测试文字输入进了错误的程序。
"""
import io

P = r"D:\dsh\projects\ai-chat-bot\coords_send.py"

GUARD = '''def _assert_fg(hwnd) -> bool:
    """按键前的前台守卫：必须是这个窗口、且可见、且没最小化。"""
    try:
        if not hwnd or not u.IsWindow(hwnd):
            return False
        if not u.IsWindowVisible(hwnd) or u.IsIconic(hwnd):
            return False
        return u.GetForegroundWindow() == hwnd
    except Exception:
        return False


'''

with io.open(P, "r", encoding="utf-8") as f:
    s = f.read()

if "_assert_fg" not in s:
    anchor = "def click(x: int, y: int, delay: float = 0.25) -> None:"
    assert anchor in s
    s = s.replace(anchor, GUARD + anchor, 1)

# open_chat: 每次输入前检查
s = s.replace('''    click(x, y)
    key(VK_A, (VK_CTRL,))
    key(0x2E)                      # Delete 清空搜索框
    if not clipboard_put(name):
        return False
    key(VK_V, (VK_CTRL,))
    time.sleep(0.6)
    key(VK_RETURN)''', '''    click(x, y)
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
    key(VK_RETURN)''', 1)

# send_text: 同样处理
s = s.replace('''    click(x, y)
    key(VK_A, (VK_CTRL,))
    key(0x2E)
    if not clipboard_put(text):
        return False
    key(VK_V, (VK_CTRL,))
    time.sleep(0.5)
    key(VK_RETURN)''', '''    click(x, y)
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
    key(VK_RETURN)''', 1)

# foreground(): 置前后再校验一次可见性
s = s.replace('''def foreground(hwnd) -> bool:
    """把微信置前，并确认它真的在前台。"""
    try:
        if u.IsIconic(hwnd):
            u.ShowWindow(hwnd, 9)''', '''def foreground(hwnd) -> bool:
    """把微信置前，并确认它真的在前台且可见。"""
    try:
        if not u.IsWindowVisible(hwnd):
            u.ShowWindow(hwnd, 5)
            time.sleep(0.3)
        if u.IsIconic(hwnd):
            u.ShowWindow(hwnd, 9)''', 1)

with io.open(P, "w", encoding="utf-8", newline="\n") as f:
    f.write(s)
print("coords_send: 前台守卫已加")
