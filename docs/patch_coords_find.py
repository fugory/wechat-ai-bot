# -*- coding: utf-8 -*-
"""给 coords_send 加 find_wechat_any()：能找到隐藏到托盘的微信窗口并唤醒。

原因：wxwin.find_wechat 只认可见窗口，微信缩托盘后返回 None，坐标通道全失效。
"""
import io

P = r"D:\dsh\projects\ai-chat-bot\coords_send.py"

HELPER = '''def find_wechat_any():
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


'''

with io.open(P, "r", encoding="utf-8") as f:
    s = f.read()

if "def find_wechat_any" in s:
    print("已存在，跳过")
else:
    anchor = "def client_box(hwnd):"
    assert anchor in s, "anchor miss"
    s = s.replace(anchor, HELPER + anchor, 1)
    with io.open(P, "w", encoding="utf-8", newline="\n") as f:
        f.write(s)
    print("已加 find_wechat_any")
