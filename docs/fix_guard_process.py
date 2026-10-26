# -*- coding: utf-8 -*-
"""修前台守卫：点搜索框后前台是"搜索弹窗"（也属于微信进程），不该被拒。

改判据：前台窗口的**进程**是 Weixin 即可（而不是必须是主窗口句柄）。
这样既能防止打到别的程序，又不会误伤微信自己的弹窗（搜索/表情面板）。
"""
import io

P = r"D:\dsh\projects\ai-chat-bot\coords_send.py"

OLD = '''def _assert_fg(hwnd) -> bool:
    """按键前的前台守卫：必须是这个窗口、且可见、且没最小化。"""
    try:
        if not hwnd or not u.IsWindow(hwnd):
            return False
        if not u.IsWindowVisible(hwnd) or u.IsIconic(hwnd):
            return False
        return u.GetForegroundWindow() == hwnd
    except Exception:
        return False'''

NEW = '''def _wechat_pids():
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
        return False'''

with io.open(P, "r", encoding="utf-8") as f:
    s = f.read()
assert OLD in s, "_assert_fg 没匹配上"
s = s.replace(OLD, NEW, 1)
with io.open(P, "w", encoding="utf-8", newline="\n") as f:
    f.write(s)
print("coords_send: 守卫改为按进程判定")
