# -*- coding: utf-8 -*-
"""ai-chat-bot 的本地控制台后端。

设计要点：
  * 只绑 127.0.0.1，且所有 /api 请求都要带 token（防止别的网页跨站调你的本地接口）
  * GUI 以**子进程**方式管 bot：能启停、能拿到实时输出，bot 崩了 GUI 还在
  * 日志同时写回 bridge.log，保持原有工具链可用
  * 状态不只看"进程存在"，还看 bridge.log 是否在更新（防"假运行"）
"""
import collections
import ctypes
import ctypes.wintypes as wt
import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time

from flask import Flask, jsonify, request, send_from_directory

HERE = os.path.dirname(os.path.abspath(__file__))
if getattr(sys, "frozen", False):
    # 打包后：代码在 _internal/gui，数据在 exe 目录
    ROOT = os.path.dirname(os.path.abspath(sys.executable))
else:
    ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

LOG_PATH = os.path.join(ROOT, "bridge.log")
RUN_LOG = os.path.join(HERE, "run.log")   # GUI 启的 bot 写这里，独占不冲突
LOCK_PATH = os.path.join(ROOT, "bridge.lock")
TOKEN = secrets.token_urlsafe(18)

def _static_dir() -> str:
    """静态目录：源码运行 / 打包后 / 极端情况下都在候选里找一遍。"""
    cands = [
        os.path.join(HERE, "static"),
        os.path.join(getattr(sys, "_MEIPASS", "") or "", "gui", "static"),
        os.path.join(ROOT, "gui", "static"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "static"),
    ]
    for c in cands:
        if c and os.path.isdir(c):
            return c
    return cands[0]


app = Flask(__name__, static_folder=_static_dir(), static_url_path="/s")

_logs = collections.deque(maxlen=3000)
_proc = None
_lock = threading.Lock()


# ---------------- 进程与状态 ----------------

ACTIVE_PATH = os.path.join(HERE, "active.json")


def _touch_activity():
    """记录一次"用户在操作控制台"。bot 只在要切会话时会看它。"""
    try:
        tmp = ACTIVE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"at": time.time()}, f)
        os.replace(tmp, ACTIVE_PATH)
    except Exception:
        pass


def _read_lock():
    try:
        with open(LOCK_PATH, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return {}


def find_bot_pids():
    """找 bot 实例（每条命令算一个实例，父子进程合并）。

    bridge 启动时会出现"父进程拉起同命令行子进程"的情况（真正拿锁的是子进程），
    直接按进程数判断会误报"起了两个"，所以只保留根进程。
    返回值按 (pid, 创建时间) 给出。
    """
    procs = []
    try:
        import psutil
        for p in psutil.process_iter(["pid", "name", "cmdline", "create_time", "ppid"]):
            try:
                cmd = " ".join(p.info.get("cmdline") or [])
            except Exception:
                continue
            if "bridge.py" in cmd and "--live" in cmd:
                procs.append((p.info["pid"], p.info.get("ppid") or 0,
                              p.info.get("create_time") or 0))
    except Exception:
        pass
    pids = {x[0] for x in procs}
    roots = [x for x in procs if x[1] not in pids]
    return [(pid, ct) for pid, _ppid, ct in (roots or procs)]


def _bot_running():
    """bot 是否在跑：(running, pid, uptime)。以真实进程为准。"""
    procs = find_bot_pids()
    if not procs:
        return False, 0, 0
    pid, ct = procs[0]
    uptime = int(time.time() - float(ct)) if ct else 0
    return True, int(pid), uptime


def _wechat_state():
    """微信进程 + 主窗口状态（供用户判断"能不能发"）。"""
    out = {"running": False, "pids": [], "window": None, "visible": False,
           "iconic": False, "rect": None}
    try:
        import psutil
        pids = [p.pid for p in psutil.process_iter(["pid", "name"])
                if (p.info.get("name") or "").lower() in ("weixin.exe", "wechat.exe")]
        out["running"] = bool(pids)
        out["pids"] = pids
        if not pids:
            return out
        best = None
        user32 = ctypes.windll.user32
        setp = set(pids)

        def cb(hwnd, _):
            nonlocal best
            pid = wt.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value not in setp:
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
            r = wt.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            # 托盘/隐藏窗口的坐标常是 -25600 / -32000 这种，必须排除
            if r.left < -1000 or r.top < -1000:
                return True
            area = (r.right - r.left) * (r.bottom - r.top)
            if area <= 0:
                return True
            visible = bool(user32.IsWindowVisible(hwnd))
            score = area * (2 if visible else 1)
            if best is None or score > best[0]:
                best = (score, hwnd, r)
            return True

        user32.EnumWindows(
            ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)(cb), 0)
        if best:
            _, hwnd, r = best
            out["window"] = int(hwnd)
            out["visible"] = bool(user32.IsWindowVisible(hwnd))
            out["iconic"] = bool(user32.IsIconic(hwnd))
            out["rect"] = [r.left, r.top, r.right - r.left, r.bottom - r.top]
    except Exception as e:
        out["error"] = str(e)[:120]
    return out


def _uia_state() -> dict:
    """读 bot 写的 UIA 健康状态（没有就当未知）。"""
    try:
        p = os.path.join(ROOT, "uia_state.json")
        if not os.path.isfile(p):
            return {"ok": None, "at": "", "fails": 0}
        with open(p, "r", encoding="utf-8-sig") as f:
            d = json.load(f)
        return {"ok": bool(d.get("ok")), "at": d.get("at", ""),
                "fails": int(d.get("fails") or 0)}
    except Exception:
        return {"ok": None, "at": "", "fails": 0}


def _sticker_count():
    try:
        from wechatauto import WeChatDB
        db = WeChatDB()
        rel = [r for r, _, _ in db._db_files if "emoticon" in r][0]
        row = db._open(rel).execute(
            "select count(*) from kNonStoreEmoticonTable").fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return None


def ensure_utf8_log():
    """旧 bridge.log 若是 UTF-16，仅提示不强行转换（文件可能被别处占用）。"""
    if not os.path.isfile(LOG_PATH):
        return
    try:
        with open(LOG_PATH, "rb") as f:
            head = f.read(2)
        if head in (b"\xff\xfe", b"\xfe\xff"):
            _logs.append("[gui] 旧的 bridge.log 是 UTF-16（另一终端占用中）；"
                         "GUI 启的 bot 会写到 gui/run.log，不受影响")
    except Exception:
        pass


def _tail_log(n=300):
    """读日志尾巴：优先 GUI 自己的 run.log，其次旧的 bridge.log。

    老 bridge.log 可能被 PowerShell 写成 UTF-16，读取时按 BOM 猜编码。
    """
    path = RUN_LOG if os.path.isfile(RUN_LOG) else LOG_PATH
    if not os.path.isfile(path):
        return []
    raw = b""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 300 * 300))
            raw = f.read()
    except Exception:
        return []
    encs = ("utf-16", "utf-8", "gbk") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") \
        else ("utf-8", "utf-16", "gbk")
    for enc in encs:
        try:
            text = raw.decode(enc)
            return [ln.rstrip("\r") for ln in text.splitlines()[-n:]]
        except Exception:
            continue
    return [ln for ln in raw.decode("utf-8", "replace").splitlines()[-n:]]


# ---------------- 日志采集 ----------------

def _pump(proc):
    """把子进程输出同时写进 ring buffer 和 bridge.log。"""
    with open(RUN_LOG, "a", encoding="utf-8", errors="replace") as lf:
        for raw in iter(proc.stdout.readline, ""):
            line = raw.rstrip("\n")
            if not line:
                continue
            _logs.append(line)
            try:
                lf.write(line + "\n")
                lf.flush()
            except Exception:
                pass


def _wechat_exe() -> str:
    """找微信可执行文件：配置 → 开始菜单快捷方式（含所有用户）→ 常见路径。"""
    cfg = {}
    try:
        cfg = _load_cfg()
        p = ((cfg.get("wechat") or {}).get("exe_path") or "").strip()
        if p and os.path.isfile(p):
            return p
    except Exception:
        pass

    cands = []

    def from_lnk(lnk):
        try:
            import subprocess as sp
            out = sp.run(
                ["powershell", "-NoProfile", "-Command",
                 "(New-Object -ComObject WScript.Shell).CreateShortcut('%s').TargetPath" % lnk],
                capture_output=True, text=True, timeout=12)
            t = (out.stdout or "").strip()
            if t and os.path.isfile(t):
                cands.append(t)
        except Exception:
            pass

    # ① 开始菜单（当前用户 + 所有用户）
    roots = []
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                           r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders")
        roots.append(winreg.QueryValueEx(k, "Programs")[0])
    except Exception:
        pass
    roots.append(os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"),
                              r"Microsoft\Windows\Start Menu\Programs"))
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        for dirpath, _dirs, files in os.walk(root):
            for fn in files:
                low = fn.lower()
                if low.endswith(".lnk") and ("微信" in fn or "wechat" in low or "weixin" in low):
                    from_lnk(os.path.join(dirpath, fn))

    # ② 常见安装路径
    for p in (r"C:\Program Files\Tencent\Weixin\Weixin.exe",
              r"C:\Program Files (x86)\Tencent\Weixin\Weixin.exe",
              r"D:\Program Files\Tencent\Weixin\Weixin.exe",
              r"D:\Program Files (x86)\Tencent\Weixin\Weixin.exe",
              r"D:\浏览器下载\qq类\Weixin\Weixin.exe"):
        if os.path.isfile(p):
            cands.append(p)

    if not cands:
        return ""

    exe = cands[0]
    # 找到就记住，下次直接命中
    try:
        cfg = cfg or _load_cfg()
        cfg.setdefault("wechat", {})["exe_path"] = exe
        _save_cfg(cfg)
        _logs.append("[gui] 已记住微信路径：%s" % exe)
    except Exception:
        pass
    return exe


def _wechat_running() -> bool:
    try:
        import psutil
        return any((p.info.get("name") or "").lower() in ("weixin.exe", "wechat.exe")
                   for p in psutil.process_iter(["name"]))
    except Exception:
        return False


def _revive_wechat_main():
    """找微信主窗（不要求可见），必要时显示出来。返回 (w,h) 客户区尺寸。"""
    try:
        import ctypes
        import ctypes.wintypes as wt
        import psutil
        u = ctypes.windll.user32
        pids = {p.pid for p in psutil.process_iter(["pid", "name"])
                if (p.info.get("name") or "").lower() in ("weixin.exe", "wechat.exe")}
        if not pids:
            return (0, 0)
        best = [0, 0, None]

        def cb(h, _):
            pid = wt.DWORD()
            u.GetWindowThreadProcessId(h, ctypes.byref(pid))
            if pid.value not in pids:
                return True
            cls = ctypes.create_unicode_buffer(256)
            u.GetClassNameW(h, cls, 256)
            if not cls.value.startswith("Qt") or "Tray" in cls.value:
                return True
            n = u.GetWindowTextLengthW(h)
            buf = ctypes.create_unicode_buffer(n + 1)
            u.GetWindowTextW(h, buf, n + 1)
            if "微信" not in buf.value and "Weixin" not in buf.value:
                return True
            c = wt.RECT()
            u.GetClientRect(h, ctypes.byref(c))
            w, hh = c.right - c.left, c.bottom - c.top
            if w * hh > best[0] * best[1]:
                best[0], best[1], best[2] = w, hh, h
            return True

        u.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p,
                                        ctypes.c_void_p)(cb), 0)
        if best[2] is None:
            return (0, 0)
        h = best[2]
        # 隐藏或最小化 → 显示/还原
        if not u.IsWindowVisible(h):
            u.ShowWindow(h, 5)      # SW_SHOW
            time.sleep(0.5)
        if u.IsIconic(h):
            u.ShowWindow(h, 9)      # SW_RESTORE
            time.sleep(0.5)
        c = wt.RECT()
        u.GetClientRect(h, ctypes.byref(c))
        return (c.right - c.left, c.bottom - c.top)
    except Exception:
        return (0, 0)


def ensure_wechat(launch: bool = True, wait_s: int = 45) -> dict:
    """确保微信在运行。返回 {ok, started, need_login, msg}。"""
    import time as _t
    try:
        import wxwin
    except Exception as e:
        return {"ok": False, "started": False, "need_login": False,
                "msg": "窗口模块不可用：%s" % e}

    if _wechat_running():
        w, hh = _revive_wechat_main()
        if w >= 500 and hh >= 400:
            return {"ok": True, "started": False, "need_login": False,
                    "msg": "微信已在运行（%dx%d）" % (w, hh)}
        if w > 0:
            return {"ok": False, "started": False, "need_login": True,
                    "msg": "微信在运行但停在登录窗（%dx%d），请扫码登录后再启动" % (w, hh)}
        return {"ok": False, "started": False, "need_login": False,
                "msg": "微信进程在，但主窗口没出来（可能在托盘），请手动打开一下"}

    if not launch:
        return {"ok": False, "started": False, "need_login": False,
                "msg": "微信没在运行"}

    exe = _wechat_exe()
    if not exe:
        return {"ok": False, "started": False, "need_login": False,
                "msg": "找不到微信安装路径，请在配置里填 wechat.exe_path"}
    try:
        subprocess.Popen([exe], cwd=os.path.dirname(exe))
    except Exception as e:
        return {"ok": False, "started": False, "need_login": False,
                "msg": "启动微信失败：%s" % e}

    deadline = _t.time() + wait_s
    while _t.time() < deadline:
        _t.sleep(1.2)
        h = wxwin.find_wechat()
        if not h:
            continue
        size = wxwin.client_size(h) or (0, 0)
        if size[0] >= 500:
            return {"ok": True, "started": True, "need_login": False,
                    "msg": "已自动启动微信并登录"}
        if size[0] < 500:
            # 还是登录窗，继续等一会儿
            continue
    h = wxwin.find_wechat()
    if h:
        return {"ok": False, "started": True, "need_login": True,
                "msg": "微信已启动，但需要扫码登录"}
    return {"ok": False, "started": True, "need_login": False,
            "msg": "微信已启动但 %d 秒内没看到主窗口" % wait_s}


def _start_bot() -> dict:
    global _proc
    running, _, _ = _bot_running()
    if running:
        return {"ok": False, "msg": "已经在运行"}

    # 先确保微信在跑（没跑就拉起来；停在登录窗则明确提示）
    wx = ensure_wechat(launch=True)
    if not wx.get("ok"):
        return {"ok": False, "msg": wx.get("msg", "微信未就绪"),
                "need_login": bool(wx.get("need_login"))}
    py = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
    if not os.path.isfile(py):
        py = sys.executable
    creation = 0
    if os.name == "nt":
        creation = subprocess.CREATE_NEW_PROCESS_GROUP
    proc = subprocess.Popen(
        [py, "-u", "bridge.py", "--live"],
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
        bufsize=1, creationflags=creation)
    _proc = proc
    threading.Thread(target=_pump, args=(proc,), daemon=True).start()
    time.sleep(2.0)
    msg = "已启动"
    if wx.get("started"):
        msg = "已启动（顺带拉起了微信）"
    return {"ok": True, "msg": msg, "pid": proc.pid,
            "wechat_started": bool(wx.get("started"))}


def _stop_bot() -> dict:
    global _proc
    killed = []
    for pid, _ct in find_bot_pids():
        try:
            import psutil
            p = psutil.Process(int(pid))
            for c in p.children(recursive=True):
                try:
                    c.kill()
                except Exception:
                    pass
            p.kill()
            killed.append(int(pid))
        except Exception:
            pass
    pid = None
    if _proc and _proc.poll() is None:
        try:
            _proc.terminate()
            killed.append(_proc.pid)
        except Exception:
            pass
    if pid:
        try:
            import psutil
            p = psutil.Process(int(pid))
            for c in p.children(recursive=True):
                try:
                    c.kill()
                except Exception:
                    pass
            p.kill()
            killed.append(int(pid))
        except Exception:
            pass
    # 子进程被强杀时 lock 会残留
    time.sleep(0.8)
    running, _, _ = _bot_running()
    if not running and os.path.isfile(LOCK_PATH):
        try:
            os.remove(LOCK_PATH)
        except Exception:
            pass
    return {"ok": True, "msg": "已停止", "killed": killed}


# ---------------- 路由 ----------------

def _auth() -> bool:
    return request.args.get("t") == TOKEN


@app.before_request
def _guard():
    if request.path.startswith("/api") and not _auth():
        return jsonify({"error": "unauthorized"}), 401
    # 用户主动操作才算"在操作"：POST 一律算；GET 里的轮询不算
    if request.path.startswith("/api") and request.method == "POST":
        _touch_activity()
    return None


@app.get("/")
def index():
    return send_from_directory(_static_dir(), "index.html")


@app.post("/api/wechat/size/sync")
def api_size_sync():
    """把微信当前的实际尺寸记进配置（用户拖完窗口点一下即可）。"""
    try:
        import wxwin
        cur = wxwin.client_size()
        if not cur:
            return jsonify({"ok": False, "error": "读不到微信窗口尺寸"})
        cfg = _load_cfg()
        wc = cfg.setdefault("wechat", {})
        wc["ref_w"], wc["ref_h"] = int(cur[0]), int(cur[1])
        _save_cfg(cfg)
        return jsonify({"ok": True, "client": list(cur),
                        "msg": "已记录当前尺寸 %d×%d" % (cur[0], cur[1])})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.get("/api/wechat/size")
def api_wechat_size():
    try:
        import wxwin
        cfg = _load_cfg()
        wc = cfg.get("wechat") or {}
        return jsonify({"ok": True,
                        "client": list(wxwin.client_size() or []),
                        "min": [int(wc.get("min_w") or 860), int(wc.get("min_h") or 600)],
                        "keep_size": bool(wc.get("keep_size", True)),
                        "default": [1100, 760]})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.post("/api/wechat/size")
def api_wechat_size_set():
    """应用自定义尺寸。小于下限会被拒绝（再小输入区/表情面板会被裁掉）。"""
    data = request.get_json(force=True, silent=True) or {}
    try:
        w = int(data.get("w") or 0)
        h = int(data.get("h") or 0)
    except Exception:
        return jsonify({"ok": False, "error": "宽高必须是数字"})
    cfg = _load_cfg()
    wc = cfg.get("wechat") or {}
    minw, minh = int(wc.get("min_w") or 860), int(wc.get("min_h") or 600)
    if w < minw or h < minh:
        return jsonify({"ok": False,
                        "error": "不能小于 %d×%d（再小功能入口会被裁掉）" % (minw, minh)})
    if w > 2600 or h > 1600:
        return jsonify({"ok": False, "error": "尺寸过大"})
    try:
        import wxwin
        ok = wxwin.restore_size(w, h)
        return jsonify({"ok": bool(ok), "client": list(wxwin.client_size() or []),
                        "msg": "已应用 %d×%d" % (w, h) if ok else "应用失败（微信可能拒绝该尺寸）"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.get("/api/wechat/calibrate")
def api_calibrate():
    """坐标系自检：只读，不点击不发送。"""
    try:
        sys.path.insert(0, HERE)
        import diag
        return jsonify(diag.calibrate())
    except Exception as e:
        return jsonify({"ok": False, "error": "自检失败：%s" % e})


@app.post("/api/wechat/launch")
def api_wechat_launch():
    try:
        return jsonify(ensure_wechat(launch=True))
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)})


def _py_exe():
    """返回可用的 python 解释器路径；打包版返回 None（改用进程内执行）。"""
    if getattr(sys, "frozen", False):
        return None
    cands = [sys.executable,
             os.path.join(ROOT, ".venv", "Scripts", "python.exe")]
    for p in cands:
        if p and os.path.isfile(p) and os.path.basename(p).lower().startswith("python"):
            return p
    return None


def _run_inprocess(mod_name: str, arg_mode: bool = True):
    """在当前进程里跑一个脚本模块，抓它的 stdout，返回 (ok, 文本)。"""
    import contextlib
    import importlib
    buf = io.StringIO()
    try:
        sys.path.insert(0, ROOT)
        mod = importlib.import_module(mod_name)
        argv_backup = sys.argv
        try:
            sys.argv = [mod_name] + (["--json"] if arg_mode else [])
            with contextlib.redirect_stdout(buf):
                mod.run(json_mode=arg_mode) if hasattr(mod, "run") else mod.main()
        finally:
            sys.argv = argv_backup
        return True, buf.getvalue()
    except Exception as e:
        return False, buf.getvalue() + "\n[in-process] %s" % e


@app.post("/api/uia/diagnose")
def api_uia_diagnose():
    """跑独立进程诊断 UIA 树：分清"可恢复"还是"必须重启微信"。"""
    try:
        import subprocess as sp
        out = ""
        py = _py_exe()
        if py:
            if not os.path.isfile(os.path.join(ROOT, "diag_uia_tree.py")):
                return jsonify({"ok": False, "error": "缺少 diag_uia_tree.py（被误删？）"})
            p = sp.run([py, "diag_uia_tree.py", "--json"], cwd=ROOT, capture_output=True,
                       text=True, timeout=180, encoding="utf-8", errors="replace")
            out = (p.stdout or "") + (p.stderr or "")
        else:
            ok_ip, out = _run_inprocess("diag_uia_tree", True)
        line = ""
        for ln in out.splitlines():
            if ln.startswith("DIAG_RESULT "):
                line = ln[len("DIAG_RESULT "):]
        if not line:
            return jsonify({"ok": False, "error": "诊断没有输出结果：" + out[-300:]})
        d = json.loads(line)
        d["ok"] = True
        return jsonify(d)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ---------------- 记忆页 ----------------

def _mem_db():
    sys.path.insert(0, ROOT)
    sys.path.insert(0, HERE)
    import memory_store
    return memory_store.connect(os.path.join(ROOT, "memory.db"))


@app.get("/api/memory/stats")
def api_memory_stats():
    try:
        import memory_store as ms
        conn = _mem_db()
        cfg = _load_cfg()
        mv = cfg.get("memory_v2") or {}
        v3 = mv.get("v3") or {}
        facts = mv.get("facts") or {}
        return jsonify({
            "ok": True,
            "profiles": conn.execute("SELECT COUNT(*) n FROM profiles").fetchone()["n"],
            "facts": conn.execute("SELECT COUNT(*) n FROM facts").fetchone()["n"],
            "vectors": conn.execute("SELECT COUNT(*) n FROM vec").fetchone()["n"],
            "enabled": bool(mv.get("enabled", True)),
            "facts_enabled": bool(facts.get("enabled", True)),
            "vec_enabled": bool(v3.get("enabled")),
            "format": v3.get("format") or "ark_multimodal",
            "url": v3.get("url") or "",
            "model": v3.get("model") or "",
            "key_env": v3.get("key_env") or "ARK_EMBED_KEY",
            "has_key": bool(os.environ.get(v3.get("key_env") or "ARK_EMBED_KEY")
                            or os.environ.get("ARK_EMBED_KEY")),
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.post("/api/memory/config")
def api_memory_config():
    data = request.get_json(force=True, silent=True) or {}
    try:
        cfg = _load_cfg()
        mv = cfg.setdefault("memory_v2", {})
        if "enabled" in data:
            mv["enabled"] = bool(data["enabled"])
        if "facts_enabled" in data:
            mv.setdefault("facts", {})["enabled"] = bool(data["facts_enabled"])
        v3 = mv.setdefault("v3", {})
        for k in ("format", "url", "model", "key_env"):
            if data.get(k) is not None:
                v3[k] = data[k]
        if "vec_enabled" in data:
            v3["enabled"] = bool(data["vec_enabled"])
        _save_cfg(cfg)
        return jsonify({"ok": True, "msg": "记忆配置已保存（重启 bot 生效）"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.post("/api/memory/models")
def api_memory_models():
    """拉取 embedding 模型列表：从 URL 推出 base，再 GET {base}/models。"""
    data = request.get_json(force=True, silent=True) or {}
    try:
        import json as _json
        import urllib.request
        cfg = _load_cfg()
        v3 = (cfg.get("memory_v2") or {}).get("v3") or {}
        url = (data.get("url") or v3.get("url") or "").strip()
        key_env = data.get("key_env") or v3.get("key_env") or "ARK_EMBED_KEY"
        if not url:
            return jsonify({"ok": False, "error": "先填接口地址"})
        key = os.environ.get(key_env) or os.environ.get("ARK_EMBED_KEY") or ""
        if not key:
            return jsonify({"ok": False, "error": "没找到密钥（先保存密钥）"})
        base = url.rstrip("/")
        for tail in ("/embeddings/multimodal", "/embeddings", "/chat/completions"):
            if base.endswith(tail):
                base = base[: -len(tail)]
                break
        req = urllib.request.Request(base + "/models",
                                     headers={"Authorization": "Bearer " + key})
        with urllib.request.urlopen(req, timeout=20) as resp:
            d = _json.loads(resp.read().decode("utf-8", "replace"))
        items = d.get("data") if isinstance(d, dict) else d
        ids = []
        for x in (items or []):
            mid = x.get("id") if isinstance(x, dict) else str(x)
            if mid:
                ids.append(mid)
        # 优先只给 embedding 相关的；一个都没有就把全部给出来
        emb = [i for i in ids if "embed" in i.lower()]
        use = emb or ids
        use.sort()
        return jsonify({"ok": True, "models": use, "count": len(use),
                        "filtered": bool(emb),
                        "msg": "拉取到 %d 个%s模型" % (len(use), "embedding " if emb else "")})
    except Exception as e:
        return jsonify({"ok": False, "error": "拉取失败：%s" % e})


@app.post("/api/memory/test")
def api_memory_test():
    try:
        import subprocess as sp
        out = ""
        py = _py_exe()
        if py and os.path.isfile(os.path.join(ROOT, "check_embed.py")):
            p = sp.run([py, "check_embed.py"], cwd=ROOT, capture_output=True, text=True,
                       timeout=120, encoding="utf-8", errors="replace")
            out = (p.stdout or "") + (p.stderr or "")
        else:
            # 打包版：直接在当前进程里测一次 embedding
            try:
                sys.path.insert(0, ROOT)
                sys.path.insert(0, HERE)
                import bridge as _b
                _b.load_dotenv(os.path.join(ROOT, ".env"))
                cfg = _load_cfg()
                probe = dict(cfg)
                probe["memory_v2"] = dict(cfg.get("memory_v2") or {})
                v3 = dict(probe["memory_v2"].get("v3") or {})
                probe["memory_v2"]["v3"] = dict(v3, enabled=True)
                vec = _b._mem_embed(probe, "测试")
                out = ("✅ 可用，向量维度 %d" % len(vec)) if vec else "❌ 不可用（看日志）"
            except Exception as _e:
                out = "❌ 进程内测试失败：%s" % _e
        ok = "✅" in out
        dim = ""
        for line in out.splitlines():
            if "向量维度" in line:
                dim = line.strip()
        return jsonify({"ok": ok, "dim": dim,
                        "msg": dim if ok else "不可用，看日志：gui/client.log 或终端输出",
                        "raw": out[-400:]})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


_OWNER_CACHE = {"at": 0.0, "map": {}}


def _owner_names() -> dict:
    """owner(wxid) -> 可读名字。白名单里群只填了名字，要按名字反查 wxid。"""
    now = time.time()
    if _OWNER_CACHE["map"] and now - _OWNER_CACHE["at"] < 300:
        return _OWNER_CACHE["map"]
    names = {}
    try:
        cfg = _load_cfg()
        for f in (cfg.get("allow_friends") or []):
            w = f.get("wxid") or ""
            if w:
                names[w] = f.get("name") or w[:12]
        groups = [g for g in (cfg.get("allow_groups") or []) if isinstance(g, dict)]
        need = [g for g in groups if g.get("name")]
        if need:
            try:
                from wechatauto import WeChatDB
                db = WeChatDB()
                for g in need:
                    w = g.get("wxid") or ""
                    if not w:
                        try:
                            for hit in (db.search_contact(g["name"]) or []):
                                u = hit.get("username") or ""
                                if u.endswith("@chatroom"):
                                    w = u
                                    break
                        except Exception:
                            pass
                    if w:
                        names[w] = g["name"]
                try:
                    db._close() if hasattr(db, "_close") else None
                except Exception:
                    pass
            except Exception as e:
                _logs.append("[gui] 解析群名失败（忽略）：%s" % e)
    except Exception:
        pass
    _OWNER_CACHE["map"] = names
    _OWNER_CACHE["at"] = now
    return names


@app.get("/api/memory/facts")
def api_memory_facts():
    try:
        conn = _mem_db()
        rows = conn.execute(
            "SELECT id, scope, owner, text, used_count FROM facts"
            " ORDER BY scope, id DESC LIMIT 300").fetchall()
        desc = {}
        try:
            desc = _load_json(DESC_PATH, {}) or {}
        except Exception:
            desc = {}
        names = _owner_names()
        out = []
        for r in rows:
            _o = r["owner"] or ""
            _disp = names.get(_o)
            if not _disp:
                _disp = ("群 " + _o.replace("@chatroom", "")[:10]) if _o.endswith("@chatroom") else (_o[:14] or "未知")
            out.append({"id": r["id"], "scope": r["scope"],
                        "owner": _disp,
                        "text": r["text"], "used": r["used_count"]})
        return jsonify({"ok": True, "items": out})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.post("/api/memory/facts/delete")
def api_memory_facts_delete():
    data = request.get_json(force=True, silent=True) or {}
    try:
        fid = int(data.get("id") or 0)
        if not fid:
            return jsonify({"ok": False, "error": "缺少 id"})
        conn = _mem_db()
        conn.execute("DELETE FROM facts WHERE id=?", (fid,))
        conn.execute("DELETE FROM vec WHERE fact_id=?", (fid,))
        conn.commit()
        return jsonify({"ok": True, "msg": "已删除"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.get("/api/wechat/win")
def api_wechat_win():
    import wxwin
    return jsonify({"ok": True, **wxwin.state()})


@app.post("/api/wechat/<action>")
def api_wechat_action(action):
    import wxwin
    if action == "show":
        ok = wxwin.show()
    elif action == "hide":
        ok = wxwin.hide()
    elif action == "close":
        ok = wxwin.close_wechat()
    elif action == "restore_caption":
        ok = wxwin.restore_caption()
    elif action == "strip_caption":
        ok = wxwin.strip_caption()
    elif action == "follow_on":
        wxwin.set_follow(True)
        ok = True
    elif action == "follow_off":
        wxwin.set_follow(False)
        ok = True
    elif action == "restore_size":
        ok = wxwin.restore_size()
    else:
        return jsonify({"ok": False, "error": "未知操作：%s" % action})
    return jsonify({"ok": bool(ok)})


@app.get("/api/status")
def api_status():
    running, pid, uptime = _bot_running()
    log_age = None
    log_src = RUN_LOG if os.path.isfile(RUN_LOG) else LOG_PATH
    if os.path.isfile(log_src):
        log_age = int(time.time() - os.path.getmtime(log_src))
    return jsonify({
        "bot": {"running": running, "pid": pid, "uptime_s": uptime,
                "count": len(find_bot_pids()),
                "log_age_s": log_age, "stale": bool(running and log_age and log_age > 180)},
        "wechat": _wechat_state(),
        "stickers": _sticker_count(),
        "uia": _uia_state(),
        "server_time": time.strftime("%F %T"),
    })


@app.get("/api/logs")
def api_logs():
    n = min(int(request.args.get("n") or 200), 1000)
    if _logs:
        return jsonify({"lines": list(_logs)[-n:], "from": "buffer"})
    return jsonify({"lines": _tail_log(n), "from": "file"})


@app.post("/api/bot/start")
def api_start():
    with _lock:
        return jsonify(_start_bot())


@app.post("/api/bot/stop")
def api_stop():
    with _lock:
        return jsonify(_stop_bot())


CFG_PATH = os.path.join(ROOT, "config.json")


def _seed_dir() -> str:
    """种子目录：打包后在 _internal 里，源码运行在项目里。"""
    base = getattr(sys, "_MEIPASS", "") or (ROOT if not getattr(sys, "frozen", False)
                                            else os.path.dirname(os.path.abspath(sys.executable)))
    for cand in (os.path.join(base, "data_seed"), os.path.join(ROOT, "data_seed")):
        if os.path.isdir(cand):
            return cand
    return ""


def load_env_into_environ() -> int:
    """把数据目录下的 .env 读进 os.environ（不覆盖已存在的）。返回条数。"""
    p = os.path.join(ROOT, ".env")
    if not os.path.isfile(p):
        return 0
    n = 0
    try:
        with open(p, "r", encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v
                    n += 1
    except Exception:
        pass
    return n


def ensure_data_files() -> list:
    """首次运行：把**缺失的**数据文件从种子复制过来，已有的一律不动。"""
    made = []
    seed = _seed_dir()
    if not seed:
        return made
    for name in ("config.json", "memory.md", "stickers.json",
                 "sticker_desc.json", "image_desc.json", "state.json"):
        dst = os.path.join(ROOT, name)
        src = os.path.join(seed, name)
        if os.path.isfile(dst) or not os.path.isfile(src):
            continue
        try:
            shutil.copy2(src, dst)
            made.append(name)
        except Exception:
            pass
    if made:
        _logs.append("[gui] 首次运行，已生成：%s" % "、".join(made))
    return made


def _load_cfg():
    with open(CFG_PATH, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def _save_cfg(cfg):
    """原子写 + 备份。写坏配置会让 bot 起不来，必须稳。"""
    tmp = CFG_PATH + ".tmp"
    bak = CFG_PATH + ".bak"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    if os.path.isfile(CFG_PATH):
        try:
            import shutil
            shutil.copy2(CFG_PATH, bak)
        except Exception:
            pass
    os.replace(tmp, CFG_PATH)


def _cfg_view(cfg):
    """给前端看的配置视图（不含任何密钥值）。"""
    bk = cfg.get("backend") or {}
    vb = cfg.get("vision_backend") or {}
    friends = [{"name": e.get("name") or e.get("wxid", "")[:10],
                "wxid": e.get("wxid", "")}
               for e in (cfg.get("allow_friends") or []) if isinstance(e, dict)]
    # config 里群只存名字；这里尽量解析出 wxid 供界面显示（解析不到就留空）
    wxid_map = {}
    try:
        sys.path.insert(0, ROOT)
        import bridge as _b
        for g_ in (_b.resolve_groups(_b._shared_db(), cfg) or []):
            wxid_map[g_.get("name")] = g_.get("wxid", "")
    except Exception:
        pass
    def _chime(g):
        try:
            v = float(g.get("chime_prob") or 0)
        except Exception:
            v = 0.0
        return max(0.0, min(100.0, v))

    groups = [{"name": g.get("name", ""),
               "wxid": wxid_map.get(g.get("name", ""), ""),
               "require_at": g.get("require_at", True),
               "triggers": g.get("triggers") or [],
               "min_interval_s": g.get("min_interval_s"),
               "chime_prob": _chime(g)}
              for g in (cfg.get("allow_groups") or []) if isinstance(g, dict)]
    return {
        "chat": {"url": bk.get("url", ""), "model": bk.get("model", ""),
                 "key_env": bk.get("api_key_env", ""),
                 "thinking": bool(bk.get("thinking", False)),
                 "max_tokens": int(bk.get("max_tokens") or 300)},
        "vision": {"enabled": bool(vb.get("enabled", False)),
                   "url": vb.get("url", ""), "model": vb.get("model", ""),
                   "key_env": vb.get("api_key_env", "")},
        "persona": {"group": bk.get("group_system", ""),
                    "friend": bk.get("system", "")},
        "friends": friends,
        "groups": groups,
    }


STICKERS_PATH = os.path.join(ROOT, "stickers.json")
DESC_PATH = os.path.join(ROOT, "sticker_desc.json")
THUMB_DIR = os.path.join(ROOT, "imgs", "stickers")


def _load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


@app.get("/api/steal")
def api_steal_get():
    try:
        cfg = _load_cfg()
        return jsonify({"ok": True, "enabled": bool((cfg.get("steal") or {}).get("enabled"))})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.post("/api/steal")
def api_steal_set():
    data = request.get_json(force=True, silent=True) or {}
    try:
        cfg = _load_cfg()
        st = cfg.setdefault("steal", {})
        st["enabled"] = bool(data.get("enabled"))
        _save_cfg(cfg)
        return jsonify({"ok": True, "enabled": st["enabled"],
                        "msg": "偷表情已%s（下条消息生效）" % ("开启" if st["enabled"] else "关闭")})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


MODEL_PRESETS = [
    {"id": "deepseek", "label": "DeepSeek 官方",
     "url": "https://api.deepseek.com/v1/chat/completions",
     "model": "deepseek-flash", "key_env": "DEEPSEEK_API_KEY"},
    {"id": "ark", "label": "火山方舟（豆包）",
     "url": "https://ark.cn-beijing.volces.com/api/v3/chat/completions",
     "model": "doubao-seed-2-0-lite-260428", "key_env": "ARK_API_KEY"},
    {"id": "openai", "label": "OpenAI 官方",
     "url": "https://api.openai.com/v1/chat/completions",
     "model": "gpt-4o-mini", "key_env": "OPENAI_API_KEY"},
    {"id": "relay", "label": "中转站 / 其他 OpenAI 兼容（自己填地址）",
     "url": "", "model": "", "key_env": "OPENAI_API_KEY"},
    {"id": "local", "label": "本地 llama.cpp / Ollama",
     "url": "http://127.0.0.1:8080/v1/chat/completions",
     "model": "local-model", "key_env": "LOCAL_API_KEY"},
]


def _key_set(env_name: str) -> bool:
    return bool(env_name and os.environ.get(env_name))


@app.get("/api/models")
def api_models_get():
    try:
        cfg = _load_cfg()
        bk = cfg.get("backend") or {}
        vb = cfg.get("vision_backend") or {}
        return jsonify({
            "ok": True,
            "presets": MODEL_PRESETS,
            "chat": {"url": bk.get("url", ""), "model": bk.get("model", ""),
                     "key_env": bk.get("api_key_env", ""),
                     "has_key": _key_set(bk.get("api_key_env", ""))},
            "vision": {"enabled": bool(vb.get("enabled", False)),
                       "url": vb.get("url", ""), "model": vb.get("model", ""),
                       "key_env": vb.get("api_key_env", ""),
                       "has_key": _key_set(vb.get("api_key_env", ""))},
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.post("/api/models")
def api_models_set():
    data = request.get_json(force=True, silent=True) or {}
    try:
        cfg = _load_cfg()
        changed = []
        ck = data.get("chat") or {}
        bk = cfg.setdefault("backend", {})
        for k, field in (("url", "url"), ("model", "model"), ("key_env", "api_key_env")):
            v = (ck.get(k) or "").strip()
            if v and v != bk.get(field):
                if k == "url" and not v.startswith("http"):
                    return jsonify({"ok": False, "error": "聊天接口地址必须以 http 开头"})
                bk[field] = v
                changed.append("聊天." + k)
        vk = data.get("vision") or {}
        vb = cfg.setdefault("vision_backend", {})
        if "enabled" in vk and bool(vk["enabled"]) != bool(vb.get("enabled", False)):
            vb["enabled"] = bool(vk["enabled"])
            changed.append("视觉.启用")
        for k, field in (("url", "url"), ("model", "model"), ("key_env", "api_key_env")):
            v = (vk.get(k) or "").strip()
            if v and v != vb.get(field):
                if k == "url" and not v.startswith("http"):
                    return jsonify({"ok": False, "error": "视觉接口地址必须以 http 开头"})
                vb[field] = v
                changed.append("视觉." + k)
        if not changed:
            return jsonify({"ok": True, "msg": "没有改动", "changed": []})
        _save_cfg(cfg)
        return jsonify({"ok": True, "changed": changed, "restart": True,
                        "msg": "已保存：%s（重启 bot 生效）" % "、".join(changed)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.post("/api/client-log")
def api_client_log():
    """接收前端 JS 错误（排障用，只写本地文件）。"""
    data = request.get_json(force=True, silent=True) or {}
    msg = (data.get("msg") or "")[:800]
    where = (data.get("where") or "")[:200]
    try:
        p = os.path.join(HERE, "client.log")
        with open(p, "a", encoding="utf-8") as f:
            f.write("%s | %s | %s\n" % (time.strftime("%F %T"), where, msg))
    except Exception:
        pass
    return jsonify({"ok": True})


@app.post("/api/models/list")
def api_models_list():
    """拉取 OpenAI 兼容服务的模型列表（GET {base}/models）。"""
    data = request.get_json(force=True, silent=True) or {}
    which = data.get("which") or "chat"
    try:
        cfg = _load_cfg()
        if which == "vision":
            vb = cfg.get("vision_backend") or {}
            url, env = vb.get("url"), vb.get("api_key_env")
        else:
            bk = cfg.get("backend") or {}
            url, env = bk.get("url"), bk.get("api_key_env")
        if not url:
            return jsonify({"ok": False, "error": "先填接口地址"})
        key = os.environ.get(env or "", "")
        if not key:
            return jsonify({"ok": False, "error": "没找到密钥（先保存密钥）"})
        base = url.rstrip("/")
        for tail in ("/chat/completions", "/completions", "/responses"):
            if base.endswith(tail):
                base = base[: -len(tail)]
                break
        import json as _json
        import urllib.request
        req = urllib.request.Request(base + "/models",
                                     headers={"Authorization": "Bearer " + key})
        with urllib.request.urlopen(req, timeout=20) as resp:
            d = _json.loads(resp.read().decode("utf-8", "replace"))
        ids = [x.get("id") for x in (d.get("data") or []) if x.get("id")]
        ids.sort()
        return jsonify({"ok": True, "models": ids, "count": len(ids),
                        "msg": "拉取到 %d 个模型" % len(ids)})
    except Exception as e:
        return jsonify({"ok": False, "error": "拉取失败：%s" % e})


@app.post("/api/models/test")
def api_models_test():
    """真发一次最小请求，确认地址/模型/密钥能用。"""
    data = request.get_json(force=True, silent=True) or {}
    which = data.get("which") or "chat"
    try:
        cfg = _load_cfg()
        if which == "vision":
            vb = cfg.get("vision_backend") or {}
            url, model = vb.get("url"), vb.get("model")
            key = os.environ.get(vb.get("api_key_env") or "", "")
        else:
            bk = cfg.get("backend") or {}
            url, model = bk.get("url"), bk.get("model")
            key = os.environ.get(bk.get("api_key_env") or "", "")
        if not url or not model:
            return jsonify({"ok": False, "error": "地址或模型名为空"})
        if not key:
            return jsonify({"ok": False, "error": "没找到密钥（先在上面填）"})
        import json as _json
        import time as _t
        import urllib.request
        body = {"model": model,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 8, "thinking": {"type": "disabled"}}
        req = urllib.request.Request(url, data=_json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json",
                                              "Authorization": "Bearer " + key})
        t0 = _t.time()
        with urllib.request.urlopen(req, timeout=25) as resp:
            d = _json.loads(resp.read().decode("utf-8", "replace"))
        ms = int((_t.time() - t0) * 1000)
        out = (d.get("choices") or [{}])[0].get("message", {}).get("content", "")
        return jsonify({"ok": True, "ms": ms, "model": model,
                        "sample": (out or "")[:40],
                        "msg": "连通正常 · 模型 %s · %d ms" % (model, ms)})
    except Exception as e:
        return jsonify({"ok": False, "error": "连接失败：%s" % e})


@app.get("/api/emoji-prompts")
def api_emoji_prompts_get():
    try:
        cfg = _load_cfg()
        p = ((cfg.get("emoji") or {}).get("prompts") or {})
        return jsonify({"ok": True,
                        "steal": p.get("steal", ""),
                        "delete": p.get("delete", "")})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.post("/api/emoji-prompts")
def api_emoji_prompts_set():
    data = request.get_json(force=True, silent=True) or {}
    try:
        cfg = _load_cfg()
        ec = cfg.setdefault("emoji", {})
        pr = ec.setdefault("prompts", {})
        if isinstance(data.get("steal"), str):
            pr["steal"] = data["steal"].strip()
        if isinstance(data.get("delete"), str):
            pr["delete"] = data["delete"].strip()
        _save_cfg(cfg)
        return jsonify({"ok": True, "msg": "提示词已保存（下一条消息生效）"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


def _stickers_missing_desc() -> list:
    """返回"标签为空或占位"的 md5 列表。"""
    import json as _json
    try:
        items = _load_json(STICKERS_PATH, [])
        desc = _load_json(DESC_PATH, {})
        out = []
        for it in items:
            md5 = (it.get("md5") or "").lower()
            tag = (it.get("tag") or "").strip()
            if not md5:
                continue
            if not tag or tag.startswith("（待补") or not desc.get(md5):
                out.append(md5)
        return out
    except Exception:
        return []


def _describe_missing(md5s: list) -> None:
    """后台补描述：逐个让视觉模型看图，写回 stickers.json / sticker_desc.json。"""
    try:
        sys.path.insert(0, ROOT)
        import bridge as b
        import sticker_vision as sv
        b.load_dotenv(os.path.join(ROOT, ".env"))
        done = 0
        for md5 in md5s:
            try:
                d = sv.describe_sticker(None, md5, b.vision_describe, None, None)
            except Exception:
                d = ""
            if not d:
                continue
            items = _load_json(STICKERS_PATH, [])
            desc = _load_json(DESC_PATH, {})
            for it in items:
                if (it.get("md5") or "").lower() == md5:
                    it["tag"] = d[:24]
            desc[md5] = d[:24]
            _save_json(STICKERS_PATH, items)
            _save_json(DESC_PATH, desc)
            done += 1
        _logs.append("[gui] 表情描述补齐完成：%d/%d" % (done, len(md5s)))
    except Exception as e:
        _logs.append("[gui] 补描述失败：%s" % e)


@app.post("/api/stickers/calibrate")
def api_stickers_calibrate():
    """校准：跑独立子进程（GUI 进程里 UIA 不可用）。"""
    try:
        import subprocess as sp
        py = _py_exe()
        if not py:
            return jsonify({"ok": False,
                            "error": "打包版不支持校准（需要 Python 环境）；"
                                     "可改用「同步表情」，或直接用源码运行"})
        if not os.path.isfile(os.path.join(ROOT, "calib_stickers.py")):
            return jsonify({"ok": False, "error": "缺少 calib_stickers.py"})
        p = sp.run([py, "calib_stickers.py"], cwd=ROOT, capture_output=True,
                   text=True, timeout=240, encoding="utf-8", errors="replace")
        line = ""
        for ln in (p.stdout or "").splitlines():
            if ln.startswith("CALIB_RESULT "):
                line = ln[len("CALIB_RESULT "):]
        if not line:
            tail = ((p.stderr or "") + (p.stdout or ""))[-300:]
            return jsonify({"ok": False, "error": "校准进程没有输出结果：" + tail})
        return jsonify(json.loads(line))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.get("/api/stickers/overflow")
def api_stickers_overflow():
    """被挤出菜单（面板装不下）的表情数量，提示用户去微信手动删。"""
    try:
        p = os.path.join(ROOT, "stickers_overflow.json")
        data = {}
        if os.path.isfile(p):
            with open(p, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
        over = data.get("overflow") or []
        desc = _load_json(DESC_PATH, {})
        # 自愈：台账可能陈旧（用户已在微信里删了），按当前真实收藏过滤
        live = set()
        try:
            sys.path.insert(0, ROOT)
            import bridge as _b
            _b.load_dotenv(os.path.join(ROOT, ".env"))
            live = set(_b.sticker_order_md5s(_b._shared_db()))
        except Exception:
            pass
        if live:
            over = [m for m in over if m in live]
        return jsonify({"ok": True, "count": len(over), "at": data.get("at", ""),
                        "items": [{"md5": m, "tag": desc.get(m, "")} for m in over]})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.post("/api/stickers/sync")
def api_stickers_sync():
    """从微信收藏同步表情清单（新收藏的会自动进菜单）。"""
    try:
        sys.path.insert(0, ROOT)
        import bridge as b
        b.load_dotenv(os.path.join(ROOT, ".env"))
        n = b.sync_sticker_indices(b._shared_db())
        # 后台给"还没有描述"的表情补描述（新号预存的表情会走这里）
        missing = _stickers_missing_desc()
        if missing:
            threading.Thread(target=_describe_missing, args=(missing,), daemon=True).start()
        return jsonify({"ok": True, "count": n, "missing": len(missing),
                        "msg": "已同步 %d 个表情，%d 个缺描述（后台补齐中）" % (n, len(missing))})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.get("/api/stickers")
def api_stickers():
    items = _load_json(STICKERS_PATH, [])
    desc = _load_json(DESC_PATH, {})
    out = []
    for it in items:
        md5 = (it.get("md5") or "").lower()
        if not md5:
            continue
        has = False
        for ext in (".jpg", ".png", ".gif"):
            if os.path.isfile(os.path.join(THUMB_DIR, md5 + ext)):
                has = True
                break
        out.append({"index": it.get("index"), "md5": md5,
                    "tag": it.get("tag") or desc.get(md5, ""),
                    "love": int(it.get("love") or 1),
                    "uses": int(it.get("uses") or 0),
                    "has_thumb": has})
    out.sort(key=lambda x: (x["index"] is None, x["index"]))
    return jsonify({"ok": True, "items": out})


@app.get("/api/stickers/thumb/<md5>")
def api_sticker_thumb(md5):
    md5 = (md5 or "").lower()
    if len(md5) != 32 or not md5.isalnum():
        return jsonify({"error": "bad md5"}), 400
    for ext in (".jpg", ".png", ".gif"):
        p = os.path.join(THUMB_DIR, md5 + ext)
        if os.path.isfile(p):
            return send_from_directory(THUMB_DIR, md5 + ext)
    # 没有缓存图就现解一张
    try:
        sys.path.insert(0, ROOT)
        import sticker_vision as sv
        acc = sv.find_account_dir()
        key = sv._get_key_raw(acc) if acc else None
        if acc and key:
            out = sv.decode_sticker(acc, md5, key, THUMB_DIR)
            if out and os.path.isfile(out):
                return send_from_directory(THUMB_DIR, os.path.basename(out))
    except Exception as e:
        return jsonify({"error": "解码失败：%s" % e}), 500
    return jsonify({"error": "没有这张图"}), 404


@app.post("/api/stickers/update")
def api_sticker_update():
    data = request.get_json(force=True, silent=True) or {}
    md5 = (data.get("md5") or "").lower()
    if not md5:
        return jsonify({"ok": False, "error": "缺少 md5"})
    items = _load_json(STICKERS_PATH, [])
    desc = _load_json(DESC_PATH, {})
    hit = False
    for it in items:
        if (it.get("md5") or "").lower() != md5:
            continue
        hit = True
        if isinstance(data.get("tag"), str) and data["tag"].strip():
            it["tag"] = data["tag"].strip()[:40]
            desc[md5] = it["tag"]
        if "love" in data:
            try:
                it["love"] = max(1, min(5, int(data["love"])))
            except Exception:
                pass
    if not hit:
        return jsonify({"ok": False, "error": "菜单里没有这个 md5"})
    _save_json(STICKERS_PATH, items)
    _save_json(DESC_PATH, desc)
    return jsonify({"ok": True, "msg": "已保存"})


@app.post("/api/stickers/redesc")
def api_sticker_redesc():
    """用当前视觉模型重新描述一张表情（会覆盖标签）。"""
    data = request.get_json(force=True, silent=True) or {}
    md5 = (data.get("md5") or "").lower()
    if not md5:
        return jsonify({"ok": False, "error": "缺少 md5"})
    try:
        sys.path.insert(0, ROOT)
        import bridge as b
        import sticker_vision as sv
        b.load_dotenv(os.path.join(ROOT, ".env"))
        acc = sv.find_account_dir()
        if not acc:
            return jsonify({"ok": False, "error": "找不到微信数据目录"})
        sv._load_desc().pop(md5, None)      # 清掉这一条，强制重算
        cache = sv._load_desc()
        cache.pop(md5, None)
        sv._save_desc(cache)
        desc = sv.describe_sticker(acc, md5, b.vision_describe, None, None)
        if not desc:
            return jsonify({"ok": False, "error": "识别失败（本地无图或视觉接口异常）"})
        items = _load_json(STICKERS_PATH, [])
        for it in items:
            if (it.get("md5") or "").lower() == md5:
                it["tag"] = desc[:40]
        _save_json(STICKERS_PATH, items)
        return jsonify({"ok": True, "tag": desc})
    except Exception as e:
        return jsonify({"ok": False, "error": "重跑失败：%s" % e})


@app.get("/api/contacts")
def api_contacts():
    """搜联系人和群，供白名单添加。q 为空时返回最近会话。"""
    q = (request.args.get("q") or "").strip()
    out = []
    try:
        sys.path.insert(0, ROOT)
        import bridge as b
        b.load_dotenv(os.path.join(ROOT, ".env"))
        db = b._shared_db()
        if q:
            for c in (db.search_contact(q) or []):
                wx = c.get("username") or ""
                out.append({
                    "type": "group" if wx.endswith("@chatroom") else "friend",
                    "wxid": wx,
                    "name": c.get("remark") or c.get("nick_name") or ""})
        else:
            for c in (db.get_sessions(limit=20) or []):
                wx = c.get("username") or ""
                if not wx:
                    continue
                out.append({"type": "group" if wx.endswith("@chatroom") else "friend",
                            "wxid": wx, "name": str(c.get("summary") or wx[:10])[:24]})
        for g in (db.get_groups() or []):
            name = g.get("name") or ""
            if q and q not in name:
                continue
            out.append({"type": "group", "wxid": g.get("username") or "",
                        "name": name, "member_count": g.get("member_count")})
    except Exception as e:
        return jsonify({"ok": False, "error": "搜索失败：%s" % e})
    seen, uniq = set(), []
    for c in out:
        if not c["wxid"] or c["wxid"] in seen:
            continue
        seen.add(c["wxid"])
        uniq.append(c)
    return jsonify({"ok": True, "items": uniq})


@app.post("/api/whitelist")
def api_whitelist():
    """整体替换白名单（friends / groups 两个列表）。"""
    data = request.get_json(force=True, silent=True) or {}
    try:
        cfg = _load_cfg()
    except Exception as e:
        return jsonify({"ok": False, "error": "读配置失败：%s" % e})

    old_groups = {g.get("name"): g for g in (cfg.get("allow_groups") or [])
                  if isinstance(g, dict)}

    friends = []
    for f in (data.get("friends") or []):
        wx = (f.get("wxid") or "").strip()
        if not wx or wx.endswith("@chatroom"):
            continue
        friends.append({"wxid": wx, "name": (f.get("name") or "").strip() or wx[:10]})

    groups = []
    for g in (data.get("groups") or []):
        name = (g.get("name") or "").strip()
        if not name:
            continue          # 群只需要名字：config 里本来就不存 wxid
        base = dict(old_groups.get(name) or {})
        base.setdefault("name", name)
        base["name"] = name
        base["triggers"] = [t for t in (g.get("triggers") or []) if str(t).strip()]
        base["require_at"] = bool(g.get("require_at", True))
        base["min_interval_s"] = float(g.get("min_interval_s") or 2)
        # 未@插话概率（0~100，0=关闭；必须@ 勾上时这个值被忽略但保留）
        try:
            base["chime_prob"] = max(0.0, min(100.0, float(g.get("chime_prob") or 0)))
        except Exception:
            base["chime_prob"] = 0.0
        groups.append(base)

    if not friends and not groups:
        return jsonify({"ok": False, "error": "白名单不能为空——否则它不会回任何人"})

    cfg["allow_friends"] = friends
    cfg["allow_groups"] = groups
    try:
        _save_cfg(cfg)
    except Exception as e:
        return jsonify({"ok": False, "error": "写配置失败：%s" % e})
    return jsonify({"ok": True, "restart": True,
                    "msg": "白名单已保存：好友 %d 个 / 群 %d 个（需重启 bot 生效）"
                           % (len(friends), len(groups))})


ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env")


def _env_path() -> str:
    """数据目录下的 .env（打包后就是 exe 旁边那个）。"""
    return os.path.join(ROOT, ".env")


def _read_env_names() -> list:
    names = []
    p = _env_path()
    if not os.path.isfile(p):
        return names
    try:
        with open(p, "r", encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                names.append(line.split("=", 1)[0].strip())
    except Exception:
        pass
    return names


@app.get("/api/secrets")
def api_secrets_get():
    """只返回"哪些变量已经填了"，绝不返回值。"""
    names = _read_env_names()
    out = []
    for n in names:
        v = os.environ.get(n) or ""
        out.append({"name": n, "set": bool(v)})
    return jsonify({"ok": True, "items": out, "path": _env_path()})


@app.post("/api/secrets")
def api_secrets_post():
    """把密钥写进 .env。只写不读；返回里也不会出现值。"""
    data = request.get_json(force=True, silent=True) or {}
    name = (data.get("name") or "").strip().upper()
    value = (data.get("value") or "").strip()
    if not name or not value:
        return jsonify({"ok": False, "error": "变量名和值都要填"})
    if not all(c.isalnum() or c == "_" for c in name):
        return jsonify({"ok": False, "error": "变量名只能包含字母、数字、下划线"})
    p = _env_path()
    lines, found = [], False
    if os.path.isfile(p):
        with open(p, "r", encoding="utf-8-sig") as f:
            lines = f.read().splitlines()
    for i, line in enumerate(lines):
        if line.strip().startswith(name + "="):
            lines[i] = "%s=%s" % (name, value)
            found = True
            break
    if not found:
        lines.append("%s=%s" % (name, value))
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, p)
    os.environ[name] = value          # 当前进程立即生效（测试台能用）
    return jsonify({"ok": True, "msg": "%s 已保存（重启 bot 生效）" % name})


@app.get("/api/config")
def api_config_get():
    try:
        return jsonify({"ok": True, "config": _cfg_view(_load_cfg())})
    except Exception as e:
        return jsonify({"ok": False, "error": "读配置失败：%s" % e})


@app.post("/api/config")
def api_config_post():
    """局部更新：只改提交上来的字段，其余保持原样。"""
    data = request.get_json(force=True, silent=True) or {}
    try:
        cfg = _load_cfg()
    except Exception as e:
        return jsonify({"ok": False, "error": "读配置失败：%s" % e})

    bk = cfg.setdefault("backend", {})
    changed = []

    chat = data.get("chat") or {}
    for key, field in (("url", "url"), ("model", "model"),
                       ("key_env", "api_key_env")):
        v = (chat.get(key) or "").strip()
        if v and v != bk.get(field):
            if key == "url" and not v.startswith("http"):
                return jsonify({"ok": False, "error": "对话模型 URL 必须以 http 开头"})
            bk[field] = v
            changed.append("chat." + key)
    if "thinking" in chat and bool(chat["thinking"]) != bool(bk.get("thinking", False)):
        bk["thinking"] = bool(chat["thinking"])
        changed.append("chat.thinking")
    if "max_tokens" in chat:
        try:
            mt = int(chat["max_tokens"])
            if 50 <= mt <= 4000:
                if mt != int(bk.get("max_tokens") or 300):
                    bk["max_tokens"] = mt
                    changed.append("chat.max_tokens")
        except Exception:
            pass

    vis = data.get("vision") or {}
    vb = cfg.setdefault("vision_backend", {})
    if "enabled" in vis and bool(vis["enabled"]) != bool(vb.get("enabled", False)):
        vb["enabled"] = bool(vis["enabled"])
        changed.append("vision.enabled")
    for key, field in (("url", "url"), ("model", "model"),
                       ("key_env", "api_key_env")):
        v = (vis.get(key) or "").strip()
        if v and v != vb.get(field):
            if key == "url" and not v.startswith("http"):
                return jsonify({"ok": False, "error": "视觉模型 URL 必须以 http 开头"})
            vb[field] = v
            changed.append("vision." + key)

    persona = data.get("persona") or {}
    g = persona.get("group")
    if isinstance(g, str) and g.strip() and g.strip() != bk.get("group_system"):
        bk["group_system"] = g.strip()
        changed.append("人设·群聊")
    f = persona.get("friend")
    if isinstance(f, str) and f.strip() and f.strip() != bk.get("system"):
        bk["system"] = f.strip()
        changed.append("人设·私聊")

    if not changed:
        return jsonify({"ok": True, "changed": [], "restart": False,
                        "msg": "没有改动"})
    try:
        _save_cfg(cfg)
    except Exception as e:
        return jsonify({"ok": False, "error": "写配置失败：%s" % e})
    return jsonify({"ok": True, "changed": changed, "restart": True,
                    "msg": "已保存：%s（需重启 bot 生效）" % "、".join(changed)})


@app.post("/api/test")
def api_test():
    """干跑：模拟一句话，返回它会看到什么 / 会回什么（不发送）。"""
    data = request.get_json(force=True, silent=True) or {}
    text = (data.get("text") or "").strip()
    kind = data.get("kind") or "group"
    asker = (data.get("asker") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "请输入要模拟的话"})
    if len(text) > 2000:
        return jsonify({"ok": False, "error": "太长了（上限 2000 字）"})
    try:
        sys.path.insert(0, HERE)
        import testbench
        return jsonify(testbench.simulate(text, kind, asker))
    except Exception as e:
        return jsonify({"ok": False, "error": "测试台异常：%s" % e})


@app.post("/api/touch")
def api_touch():
    """前端在切页/输入时调用，标记"有人正在操作"。"""
    _touch_activity()
    return jsonify({"ok": True})


@app.get("/api/ping")
def api_ping():
    return jsonify({"ok": True})


def serve(port: int = 8765):
    ensure_data_files()
    load_env_into_environ()
    ensure_utf8_log()
    app.run(host="127.0.0.1", port=port, threaded=True, use_reloader=False)


if __name__ == "__main__":
    print("token =", TOKEN)
    print("http://127.0.0.1:8765/?t=" + TOKEN)
    serve()
