# -*- coding: utf-8 -*-
"""拟人节流 A+C：

A. 放宽"惩罚"：burst 6→12、cooloff 30~75s→8~15s（最小间隔 gap 不动）
C. 可见化：/api/rhythm 暴露"近 120 秒已写 N 次"，状态页显示

参数放 config.json 的 rhythm 段，用户可改。
"""
import io
import json

BR = r"D:\dsh\projects\ai-chat-bot\bridge.py"
CFG = r"D:\dsh\projects\ai-chat-bot\config.json"
APP = r"D:\dsh\projects\ai-chat-bot\gui\app.py"
UI = r"D:\dsh\projects\ai-chat-bot\gui\static\index.html"

# ---------- 1. 配置 ----------
with io.open(CFG, "r", encoding="utf-8-sig") as f:
    cfg = json.load(f)
cfg["rhythm"] = {
    "profile": "natural",
    "burst": 12,
    "cooloff": [8, 15],
    "window": 120,
    "_note": "burst=窗口内最多写几次；cooloff=撞上限后冷却秒数；gap 不动（保节奏）",
}
with io.open(CFG, "w", encoding="utf-8") as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2)
print("config.json: rhythm 段已加")

# ---------- 2. bridge：启动时套用 ----------
with io.open(BR, "r", encoding="utf-8") as f:
    s = f.read()

FN = '''def apply_rhythm_config() -> None:
    """把 config.rhythm 套到库的拟人节奏上（放宽冷却，保留间隔）。"""
    try:
        from wechatauto import rhythm as R
        rc = (load_config().get("rhythm") or {})
        prof = (rc.get("profile") or "natural").strip().lower()
        if prof in ("natural", "calm", "fast", "off"):
            R.set_profile(prof)
        kw = {}
        try:
            b = int(rc.get("burst") or 0)
            if b > 0:
                kw["burst"] = b
        except Exception:
            pass
        try:
            c = rc.get("cooloff") or []
            if len(c) == 2:
                kw["cooloff"] = (float(c[0]), float(c[1]))
        except Exception:
            pass
        try:
            w = float(rc.get("window") or 0)
            if w > 0:
                kw["window"] = w
        except Exception:
            pass
        if kw:
            R.configure(**kw)
        p = R.profile()
        log("拟人节奏：%s ｜ %ds 内最多写 %d 次 ｜ 超限冷却 %.0f~%.0f 秒"
            % (p.name, p.window, p.burst, p.cooloff[0], p.cooloff[1]))
    except Exception as e:
        log("应用拟人节奏配置失败（忽略）：%s" % e)


'''
if "def apply_rhythm_config" not in s:
    anchor = "def main(argv: Optional[List[str]] = None) -> int:"
    assert anchor in s
    s = s.replace(anchor, FN + anchor, 1)
    # main 开头调用
    s = s.replace('''def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser''',
                  '''def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser''', 1)
    # 在 main 的 args 解析后插入调用：找 args = ap.parse_args
    import re
    m = re.search(r"\n(\s+)args = ap\.parse_args\([^\)]*\)\n", s)
    if m:
        indent = m.group(1)
        s = s[:m.end()] + indent + "apply_rhythm_config()\n" + s[m.end():]
        print("bridge: main 里已插入 apply_rhythm_config()")
    else:
        print("⚠ 没找到 args = ap.parse_args，需要手工插入")

with io.open(BR, "w", encoding="utf-8", newline="\n") as f:
    f.write(s)

# ---------- 3. 接口 ----------
with io.open(APP, "r", encoding="utf-8") as f:
    a = f.read()

if "/api/rhythm" not in a:
    ROUTE = '''@app.get("/api/rhythm")
def api_rhythm():
    """拟人节流的实时状态：档位 + 窗口内已写次数。"""
    try:
        import json as _j
        import os as _os
        st = {}
        p = _os.path.join(_os.path.expanduser("~"), ".wechatauto", "rhythm.json")
        if _os.path.isfile(p):
            with open(p, "r", encoding="utf-8-sig") as f:
                st = _j.load(f) or {}
        stamps = st.get("stamps") or []
        now = time.time()
        try:
            sys.path.insert(0, ROOT)
            import bridge as _b
            prof = _b.load_config().get("rhythm") or {}
        except Exception:
            prof = {}
        window = float(prof.get("window") or 120)
        burst = int(prof.get("burst") or 12)
        recent = [x for x in stamps if 0 <= now - x <= window]
        last = max(recent) if recent else 0
        cool = (prof.get("cooloff") or [8, 15])
        return jsonify({"ok": True, "profile": prof.get("profile") or st.get("profile") or "natural",
                        "window": window, "burst": burst,
                        "used": len(recent), "last_age": int(now - last) if last else -1,
                        "cooloff": cool,
                        "near_limit": len(recent) >= max(1, burst - 2)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


'''
    anchor = '@app.get("/api/rhythm")' if False else '@app.get("/api/logs")'
    a = a.replace(anchor, ROUTE + anchor, 1)
    with io.open(APP, "w", encoding="utf-8", newline="\n") as f:
        f.write(a)
    print("app.py: /api/rhythm 已加")

# ---------- 4. 前端显示 ----------
with io.open(UI, "r", encoding="utf-8") as f:
    h = f.read()

if "rhythmLine" not in h:
    # 放在状态页 UIA 卡片后面
    anchor = '      <h2>坐标校准（表情/发送走坐标通道用）</h2>'
    CARD = '''      <h2 style="margin-top:16px">拟人节流</h2>
      <div id="rhythmLine" class="hint" style="line-height:1.8;margin-bottom:8px">—</div>
'''
    h = h.replace(anchor, CARD + anchor, 1)

    JS = '''
async function loadRhythm(){
  try{
    const r = await api("/api/rhythm");
    const el = $("rhythmLine"); if (!el) return;
    if (!r.ok) { el.textContent = "读取失败：" + (r.error||""); return; }
    const pct = r.burst ? Math.round(r.used / r.burst * 100) : 0;
    let color = "#3fb950";
    if (r.used >= r.burst) color = "#e5484d";
    else if (r.near_limit) color = "#d29922";
    el.innerHTML = `档位 <b>${r.profile}</b> ｜ 近 ${r.window} 秒已发 ` +
      `<b style="color:${color}">${r.used}/${r.burst}</b> ｜ ` +
      `超限冷却 ${r.cooloff[0]}~${r.cooloff[1]} 秒` +
      (r.used >= r.burst ? '<br><b style="color:#e5484d">正在冷却，最多 ' + r.cooloff[1] + ' 秒后继续</b>' : '');
  }catch(e){}
}
'''
    anchor2 = "// ---------------- 坐标校准 ----------------"
    h = h.replace(anchor2, JS + anchor2, 1)
    h = h.replace('''document.querySelector('.nav-item[data-view="status"]').addEventListener("click", () => {
  loadCoords();
});''', '''document.querySelector('.nav-item[data-view="status"]').addEventListener("click", () => {
  loadCoords();
  loadRhythm();
});''')

    with io.open(UI, "w", encoding="utf-8", newline="\n") as f:
        f.write(h)
    print("index.html: 拟人节流显示已加")
