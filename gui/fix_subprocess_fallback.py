# -*- coding: utf-8 -*-
"""修控制台三个"调外部脚本"的按钮在打包版里失效。

问题：诊断 UIA / 校准表情 / 测试 embedding 都是 subprocess 调 .venv\Scripts\python.exe，
      打包后没有 python，于是失败（这次还叠加了脚本被误归档）。

改法：
  * _py_exe()：打包版返回 None
  * 找不到 python 时，**在进程内执行**（import 模块直接跑，用 redirect_stdout 抓输出）
"""
import io

P = r"D:\dsh\projects\ai-chat-bot\gui\app.py"
with io.open(P, "r", encoding="utf-8") as f:
    s = f.read()

HELPER = '''def _py_exe():
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
        return False, buf.getvalue() + "\\n[in-process] %s" % e


'''

# 插到第一个 @app.route 之前
anchor = '@app.post("/api/uia/diagnose")'
assert anchor in s
s = s.replace(anchor, HELPER + anchor, 1)

# ---- 诊断 UIA：加进程内兜底 ----
OLD_D = '''    try:
        import subprocess as sp
        py = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
        if not os.path.isfile(py):
            py = sys.executable
        p = sp.run([py, "diag_uia_tree.py", "--json"], cwd=ROOT, capture_output=True,
                   text=True, timeout=180, encoding="utf-8", errors="replace")
        line = ""
        for ln in (p.stdout or "").splitlines():
            if ln.startswith("DIAG_RESULT "):
                line = ln[len("DIAG_RESULT "):]
        if not line:
            return jsonify({"ok": False,
                            "error": "诊断没有输出结果：" + (p.stderr or "")[-200:]})
        d = json.loads(line)
        d["ok"] = True
        return jsonify(d)'''
NEW_D = '''    try:
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
        return jsonify(d)'''
assert OLD_D in s, "diagnose anchor miss"
s = s.replace(OLD_D, NEW_D, 1)

# ---- 测试 embedding：加进程内兜底 ----
OLD_E = '''        import subprocess as sp
        py = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
        if not os.path.isfile(py):
            py = sys.executable
        p = sp.run([py, "check_embed.py"], cwd=ROOT, capture_output=True, text=True,
                   timeout=120, encoding="utf-8", errors="replace")
        out = (p.stdout or "") + (p.stderr or "")'''
NEW_E = '''        import subprocess as sp
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
                out = "❌ 进程内测试失败：%s" % _e'''
assert OLD_E in s, "embed test anchor miss"
s = s.replace(OLD_E, NEW_E, 1)

# ---- 校准表情：缺脚本时给明确提示 ----
OLD_C = '''        py = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
        if not os.path.isfile(py):
            py = sys.executable
        p = sp.run([py, "calib_stickers.py"], cwd=ROOT, capture_output=True,'''
NEW_C = '''        py = _py_exe()
        if not py:
            return jsonify({"ok": False,
                            "error": "打包版不支持校准（需要 Python 环境）；"
                                     "可改用「同步表情」，或直接用源码运行"})
        if not os.path.isfile(os.path.join(ROOT, "calib_stickers.py")):
            return jsonify({"ok": False, "error": "缺少 calib_stickers.py"})
        p = sp.run([py, "calib_stickers.py"], cwd=ROOT, capture_output=True,'''
assert OLD_C in s, "calibrate anchor miss"
s = s.replace(OLD_C, NEW_C, 1)

with io.open(P, "w", encoding="utf-8", newline="\n") as f:
    f.write(s)
print("app.py: 三个按钮已加兜底")
