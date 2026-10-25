# -*- coding: utf-8 -*-
"""桌面壳：用 pywebview（系统 Edge WebView2）把控制台装进一个原生窗口。

顺带把微信窗口对齐到右侧——不是真的嵌进去（SetParent 会破坏 UIA 和坐标标定），
而是"视觉上摆在一起"：
    左边：控制台（本窗口）
    右边：微信（独立窗口，SetWindowPos 摆到那里）
"""
import ctypes
import io
import os
import ctypes.wintypes as wt
import socket
import sys
import threading
import time

if getattr(sys, "frozen", False):
    HERE = os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "gui")
    if not os.path.isdir(HERE):
        HERE = os.path.dirname(os.path.abspath(__file__))
else:
    HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, r"D:\dsh\projects\ai-chat-bot")

import app as backend  # noqa: E402


def free_port(preferred: int = 8765) -> int:
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", preferred))
        s.close()
        return preferred
    except OSError:
        s.close()
        s2 = socket.socket()
        s2.bind(("127.0.0.1", 0))
        port = s2.getsockname()[1]
        s2.close()
        return port


def wait_port(port: int, timeout: float = 20.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        with socket.socket() as s:
            s.settimeout(0.5)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.15)
    return False


def _find_wechat():
    try:
        import psutil
        pids = {p.pid for p in psutil.process_iter(["pid", "name"])
                if (p.info.get("name") or "").lower() in ("weixin.exe", "wechat.exe")}
        if not pids:
            return None
        user32 = ctypes.windll.user32
        best = None

        def cb(hwnd, _):
            nonlocal best
            pid = wt.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value not in pids:
                return True
            cls = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cls, 256)
            if not cls.value.startswith("Qt") or "Tray" in cls.value:
                return True
            n = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            if "微信" not in buf.value and "Weixin" not in buf.value:
                return True
            visible = bool(user32.IsWindowVisible(hwnd))
            iconic = bool(user32.IsIconic(hwnd))
            if not visible and not iconic:
                return True          # 收进托盘了，拉不出来
            r = wt.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            area = (r.right - r.left) * (r.bottom - r.top)
            if not iconic and (r.left < -1000 or r.top < -1000):
                return True          # 位置异常（隐藏态）
            score = area if area > 0 else 1     # 最小化时面积是 0，给它一个底分
            if best is None or score > best[0]:
                best = (score, hwnd)
            return True

        user32.EnumWindows(
            ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)(cb), 0)
        return best[1] if best else None
    except Exception:
        return None


def align_wechat(right_x: int, width: int = 1100, height: int = 760):
    """把微信摆到屏幕右侧（保持 1100x760 这个标定过的尺寸）。"""
    hwnd = _find_wechat()
    if not hwnd:
        return False
    user32 = ctypes.windll.user32
    SWP_NOACTIVATE, SWP_SHOWWINDOW = 0x0010, 0x0040
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)      # SW_RESTORE
    user32.SetWindowPos(hwnd, 0, int(right_x), 80, width, height,
                        SWP_NOACTIVATE | SWP_SHOWWINDOW)
    return True


def _setup_dpi():
    """声明 DPI 感知，返回系统缩放比例（1.0 / 1.25 / 1.5 ...）。

    不声明的话，系统按 125% 放大渲染，而窗口物理宽度不变，
    结果页面只能显示一部分，右侧控件被裁掉。
    """
    aware = False
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        aware = True
    except Exception:
        pass
    if not aware:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
            aware = True
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass
    try:
        dpi = ctypes.windll.user32.GetDpiForSystem()
        if dpi and dpi > 0:
            return dpi / 96.0
    except Exception:
        pass
    return 1.0


def main():
    scale = _setup_dpi()
    port = free_port(8765)
    threading.Thread(target=backend.serve, args=(port,), daemon=True).start()
    if not wait_port(port):
        print("后端启动失败")
        return 1
    view = os.environ.get("GUI_START_VIEW", "")
    url = "http://127.0.0.1:%d/?t=%s%s" % (
        port, backend.TOKEN, ("&view=" + view) if view else "")
    # 写给本地工具/排障用：放在**数据目录**（打包后就是 exe 旁边）
    try:
        with io.open(os.path.join(backend.ROOT, "gui.token"), "w", encoding="utf-8") as f:
            f.write("%d\n%s" % (port, backend.TOKEN))
    except Exception:
        pass
    print("控制台:", url.replace(backend.TOKEN, "***"))

    import webview
    win = webview.create_window("大肥鲸控制台", url,
                                width=1000, height=860, min_size=(820, 560))

    def on_loaded():
        # 窗口出来后把微信摆到右边，视觉上"两个并排"
        time.sleep(1.2)
        try:
            align_wechat(win.x + win.width + 8 if hasattr(win, "x") else 1200)
        except Exception as e:
            print("对齐微信失败（不影响使用）：", e)

    def on_loaded2():
        time.sleep(1.2)
        try:
            import wxwin
            # 注意：绝不自动改窗口样式（会导致重绘闪烁、甚至把微信搞崩）
            # 跟随默认关闭，避免"用户一动窗口就被拽回去、还抢焦点"
            wxwin.set_follow(False)
            print("已就绪（跟随默认关闭；需要贴边时在界面里手动开启）")
        except Exception as e:
            print("初始化窗口控制失败：", e)

    webview.start(func=on_loaded2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
