# -*- coding: utf-8 -*-
"""首启引导：纯文字弹窗（不做花哨 UI）。

触发条件：白名单为空（既没有群也没有好友），说明还没配置过。
只弹一次，记住"已看过"（localStorage），不打扰老用户。
"""
import io

UI = r"D:\dsh\projects\ai-chat-bot\gui\static\index.html"

HTML = '''
<!-- ========== 首启引导（纯文字） ========== -->
<div id="firstRun" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.55);
     z-index:9999;align-items:center;justify-content:center">
  <div style="background:#161b22;border:1px solid #30363d;border-radius:10px;
       max-width:460px;padding:22px 24px;line-height:1.9;color:#e6edf3;
       font-family:system-ui,'Microsoft YaHei',sans-serif">
    <h2 style="margin:0 0 12px;font-size:16px">第一次用，先做这三件事</h2>
    <div style="font-size:13.5px">
      <div style="margin:6px 0"><b>①</b> 打开「模型」页，填上 DeepSeek 的 Key（必填）</div>
      <div style="margin:6px 0"><b>②</b> 打开「人格」页，把要它参与的群加进白名单</div>
      <div style="margin:6px 0"><b>③</b> 回到「状态」页，点「启动」</div>
      <div style="margin-top:12px;color:#8b949e">
        另外两件事：微信要登录着、别最小化；<br>
        想让它发表情，先去「表情」页同步一次。
      </div>
    </div>
    <div style="margin-top:16px;text-align:right">
      <button id="frOk" class="primary" style="padding:6px 16px">知道了</button>
    </div>
  </div>
</div>
'''

JS = '''
// 首启引导：白名单为空时弹一次纯文字说明
function maybeFirstRun(cfg){
  try{
    if (localStorage.getItem("fr_seen") === "1") return;
    const g = (cfg.groups || []).length, f = (cfg.friends || []).length;
    if (g + f > 0) return;
    const el = $("firstRun");
    if (!el) return;
    el.style.display = "flex";
    const btn = $("frOk");
    if (btn) btn.onclick = () => {
      el.style.display = "none";
      try{ localStorage.setItem("fr_seen", "1"); }catch(e){}
    };
  }catch(e){}
}
'''

with io.open(UI, "r", encoding="utf-8") as f:
    h = f.read()

if 'id="firstRun"' not in h:
    anchor = '<!-- ========== 状态 ========== -->'
    if anchor not in h:
        anchor = '<div class="view" id="view-status">'
    assert anchor in h, "页面锚点没找到"
    h = h.replace(anchor, HTML + "\n" + anchor, 1)

if "maybeFirstRun" not in h:
    anchor2 = "// ---------------- 坐标校准 ----------------"
    h = h.replace(anchor2, JS + anchor2, 1)
    # 在 loadConfig 成功后调用（找 cfg 赋值处）
    for pat in ('$("cfgMsg").textContent = "配置已加载";',
                "cfgMsg\").textContent = \"配置已加载\";"):
        if pat in h:
            break
    # 更稳妥：在 loadConfig 内 return 前插入。找 "function loadConfig" 后的第一个 "}" 结束较难，
    # 改为在 loadStatus/loadCoords 之外单独调一次接口。
    INIT = '''
// 启动时单独拉一次配置，判断是否首启
(async () => {
  try{
    const r = await api("/api/config");
    if (r && r.ok) maybeFirstRun(r.config || {});
  }catch(e){}
})();
'''
    h = h.rstrip("\n") + "\n" + INIT + "\n</script>" if False else h
    # 直接追加到 script 结束标签之前
    idx = h.rfind("</script>")
    h = h[:idx] + INIT + h[idx:]

with io.open(UI, "w", encoding="utf-8", newline="\n") as f:
    f.write(h)
print("index.html: 首启引导（纯文字）已加")
