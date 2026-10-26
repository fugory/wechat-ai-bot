# -*- coding: utf-8 -*-
"""状态页显示"卡死/已放弃"，并给一个「我重启好了」按钮。"""
import io

APP = r"D:\dsh\projects\ai-chat-bot\gui\app.py"
UI = r"D:\dsh\projects\ai-chat-bot\gui\static\index.html"

# ---------- 后端 ----------
with io.open(APP, "r", encoding="utf-8") as f:
    a = f.read()

OLD = '''        return {"ok": bool(d.get("ok")), "at": d.get("at", ""),
                "fails": int(d.get("fails") or 0)}'''
NEW = '''        return {"ok": bool(d.get("ok")), "at": d.get("at", ""),
                "hung": bool(d.get("hung")),
                "gave_up": bool(d.get("gave_up")),
                "next_try_in": int(d.get("next_try_in") or 0),
                "fails": int(d.get("fails") or 0)}'''
assert OLD in a
a = a.replace(OLD, NEW, 1)

if "/api/uia/reset" not in a:
    ROUTE = '''@app.post("/api/uia/reset")
def api_uia_reset():
    """用户重启微信后点一下：清空卡死/放弃状态并重新探测。"""
    try:
        sys.path.insert(0, ROOT)
        import bridge as b
        b.load_dotenv(os.path.join(ROOT, ".env"))
        ok = b.uia_reset_after_restart()
        return jsonify({"ok": True, "healthy": bool(ok),
                        "msg": "已重置：" + ("UIA 正常" if ok else "仍然不可用，请确认微信用的是同一个账号")})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


'''
    anchor = '@app.get("/api/logs")'
    a = a.replace(anchor, ROUTE + anchor, 1)

with io.open(APP, "w", encoding="utf-8", newline="\n") as f:
    f.write(a)
print("app.py: 状态字段 + reset 接口")

# ---------- 前端 ----------
with io.open(UI, "r", encoding="utf-8") as f:
    h = f.read()

OLD_UI = '''    if (!r.ok) { $("uiaMsg").textContent = r.error || "读取失败"; return; }'''
NEW_UI = '''    if (!r.ok) { $("uiaMsg").textContent = r.error || "读取失败"; return; }
    const st = (r.state || {});
    if (st.hung) {
      $("uiaMsg").innerHTML = '<b style="color:#e5484d">微信进程卡死了（拖不动窗口那种）</b><br>' +
        '请关掉微信重新打开，然后点下面的「我重启好了」。';
    } else if (st.gave_up) {
      $("uiaMsg").innerHTML = '已停止自动尝试（连续失败太多次）。<br>' +
        '请重启微信后点「我重启好了」。' +
        (st.next_try_in ? ('（' + Math.round(st.next_try_in/60) + ' 分钟后才会再试）') : '');
    }'''
if OLD_UI in h:
    h = h.replace(OLD_UI, NEW_UI, 1)

# 按钮：放在「诊断 UIA」旁边
OLD_BTN = '<button id="btnDiag">诊断 UIA</button>'
NEW_BTN = ('<button id="btnDiag">诊断 UIA</button>'
           '<button id="btnUiaReset" title="重启微信后点这里，清空卡死状态">我重启好了</button>')
if OLD_BTN in h and 'btnUiaReset' not in h:
    h = h.replace(OLD_BTN, NEW_BTN, 1)

if 'btnUiaReset")' not in h:
    JS_ADD = '''
if ($("btnUiaReset")) {
  $("btnUiaReset").onclick = async () => {
    $("uiaMsg").textContent = "重置中…";
    try{
      const r = await api("/api/uia/reset", {method:"POST"});
      $("uiaMsg").textContent = r.ok ? ("✅ " + r.msg) : ("❌ " + (r.error||"失败"));
      loadUia();
    }catch(e){ $("uiaMsg").textContent = "请求失败：" + e; }
  };
}
'''
    # 插到诊断按钮逻辑之后（以 btnDiag 的 onclick 结束为锚）
    idx = h.find('$("btnDiag").onclick')
    if idx > 0:
        end = h.find("\n};", idx)
        if end > 0:
            h = h[:end + 3] + JS_ADD + h[end + 3:]

with io.open(UI, "w", encoding="utf-8", newline="\n") as f:
    f.write(h)
print("index.html: 卡死提示 + 我重启好了按钮")
