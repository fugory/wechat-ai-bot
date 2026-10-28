# -*- coding: utf-8 -*-
"""两件事：
  1) 匹配门槛加"独一性"判断：最佳 ≤16 且明显优于第二名（差 ≥6）也放行
  2) 控制台接口 /api/stickers/send + 面板视图每格「试发」按钮
"""
import io

SM = r"D:\dsh\projects\ai-chat-bot\sticker_match.py"
APP = r"D:\dsh\projects\ai-chat-bot\gui\app.py"
UI = r"D:\dsh\projects\ai-chat-bot\gui\static\index.html"

# ---------- 1. 门槛 ----------
with io.open(SM, "r", encoding="utf-8") as f:
    s = f.read()

OLD = '''    best = scored[0][0]
    b.log("面板匹配：第 1 页共 %d 格，最佳距离 %d" % (len(cells), best))'''
NEW = '''    best = scored[0][0]
    second = scored[1][0] if len(scored) > 1 else 99
    b.log("面板匹配：第 1 页共 %d 格，最佳距离 %d（第二名 %d）"
          % (len(cells), best, second))'''
assert OLD in s, "匹配日志锚点没匹配上"
s = s.replace(OLD, NEW, 1)

# 判定条件：<=12 直接过；<=16 且与第二名差 >=6 也过
OLD2 = '''    if best > 12:
        bg.close_emoji_panel()'''
NEW2 = '''    confident = (best <= 12) or (best <= 16 and (second - best) >= 6)
    if not confident:
        bg.close_emoji_panel()'''
assert OLD2 in s, "门槛判断锚点没匹配上"
s = s.replace(OLD2, NEW2, 1)

s = s.replace('b.log("面板里没有这张（翻完 %d 页，最佳距离 %d 过大），放弃发送"\n              % (len(pages), best))',
              'b.log("面板里没有这张（最佳 %d / 第二 %d，不够确定），放弃发送"\n              % (best, second))', 1)

with io.open(SM, "w", encoding="utf-8", newline="\n") as f:
    f.write(s)
print("sticker_match: 门槛加'独一性'判断")

# ---------- 2. 接口 ----------
with io.open(APP, "r", encoding="utf-8") as f:
    a = f.read()

if "/api/stickers/send" not in a:
    ROUTE = '''@app.post("/api/stickers/send")
def api_stickers_send():
    """试发一个表情（发到文件传输助手）。必须 bot 停止，走子进程。"""
    data = request.get_json(force=True, silent=True) or {}
    md5 = (data.get("md5") or "").strip().lower()
    if len(md5) < 32:
        return jsonify({"ok": False, "error": "md5 不对"})
    running, _pid, _up = _bot_running()
    if running:
        return jsonify({"ok": False,
                        "error": "bot 正在运行，先停止再试发（两个进程同时操作微信会互相打架）"})
    try:
        import subprocess as sp
        py = _py_exe()
        if not py:
            return jsonify({"ok": False, "error": "打包版不支持试发（需要 Python 环境）"})
        p = sp.run([py, "send_one_sticker.py", md5], cwd=ROOT, capture_output=True,
                   text=True, timeout=180, encoding="utf-8", errors="replace")
        line = ""
        for ln in (p.stdout or "").splitlines():
            if ln.startswith("SEND_RESULT "):
                line = ln[len("SEND_RESULT "):]
        if not line:
            return jsonify({"ok": False,
                            "error": ((p.stderr or "") + (p.stdout or ""))[-300:] or "没有输出"})
        import json as _j
        d = _j.loads(line)
        _logs.append("[gui] 试发表情 %s：%s" % (md5[:8], d.get("msg")))
        return jsonify(d)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


'''
    anchor = '@app.post("/api/stickers/calibrate")'
    assert anchor in a
    a = a.replace(anchor, ROUTE + anchor, 1)
    with io.open(APP, "w", encoding="utf-8", newline="\n") as f:
        f.write(a)
    print("app.py: /api/stickers/send 已加")

# ---------- 3. 前端按钮 ----------
with io.open(UI, "r", encoding="utf-8") as f:
    h = f.read()

if "stSend" not in h:
    OLD_UI = '''      <span style="position:absolute;right:4px;bottom:3px;font-size:10px;color:#8b949e;
        background:rgba(13,17,23,.75);padding:0 3px;border-radius:3px">${rc}</span>'''
    NEW_UI = '''      <span style="position:absolute;right:4px;bottom:3px;font-size:10px;color:#8b949e;
        background:rgba(13,17,23,.75);padding:0 3px;border-radius:3px">${rc}</span>
      <button class="stSend" data-md5="${s.md5}"
        style="position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);
               opacity:0;transition:.15s;font-size:11px;padding:2px 8px">试发</button>'''
    assert OLD_UI in h, "面板视图格锚点没匹配上"
    h = h.replace(OLD_UI, NEW_UI, 1)

    # 悬停显示按钮 + 点击处理（放在 renderPanelView 之后）
    HOVER = '''
// 面板视图：悬停显示「试发」
document.addEventListener("mouseover", e => {
  const cell = e.target.closest && e.target.closest("#stPanelGrid > div");
  if (cell) { const b = cell.querySelector(".stSend"); if (b) b.style.opacity = 1; }
});
document.addEventListener("mouseout", e => {
  const cell = e.target.closest && e.target.closest("#stPanelGrid > div");
  if (cell && !cell.contains(e.relatedTarget)) {
    const b = cell.querySelector(".stSend"); if (b) b.style.opacity = 0;
  }
});
document.addEventListener("click", async e => {
  const btn = e.target.closest && e.target.closest(".stSend");
  if (!btn) return;
  const md5 = btn.dataset.md5;
  btn.disabled = true; btn.textContent = "发送中";
  try{
    const r = await api("/api/stickers/send", {method:"POST",
      headers:{"Content-Type":"application/json"}, body: JSON.stringify({md5})});
    btn.textContent = r.ok ? "已发出" : "失败";
    btn.title = r.msg || r.error || "";
    if (!r.ok) alert("试发失败：" + (r.error || r.msg || ""));
  }catch(err){ btn.textContent = "失败"; }
  finally{ setTimeout(() => { btn.disabled = false; btn.textContent = "试发"; }, 2500); }
});
'''
    idx = h.find("// ---------------- 坐标校准 ----------------")
    h = h[:idx] + HOVER + h[idx:]
    with io.open(UI, "w", encoding="utf-8", newline="\n") as f:
        f.write(h)
    print("index.html: 每格加「试发」按钮")
