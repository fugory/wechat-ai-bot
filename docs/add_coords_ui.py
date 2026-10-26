# -*- coding: utf-8 -*-
"""状态页加「坐标校准」卡片：四个目标，倒计时采样记录。"""
import io

UI = r"D:\dsh\projects\ai-chat-bot\gui\static\index.html"

CARD = '''
    <section class="card">
      <h2>坐标校准（表情/发送走坐标通道用）</h2>
      <div class="hint" style="line-height:1.8;margin-bottom:8px">
        点下面任意按钮 → <b>3 秒内</b>把鼠标移到微信里对应的位置停住 → 时间到自动记录。<br>
        记录的是"离哪条边近就用哪条边"的偏移，窗口尺寸变了也能用；差太多就再校准一次。
      </div>
      <div id="cdCur" class="hint" style="margin-bottom:8px">鼠标：—</div>
      <div id="cdRows"></div>
      <div class="row" style="margin-top:8px">
        <button id="cdReset">恢复默认</button>
        <span id="cdMsg" class="hint"></span>
      </div>
    </section>
'''

JS = '''
// ---------------- 坐标校准 ----------------
let cdNames = {};
async function loadCoords(){
  try{
    const r = await api("/api/coords/state");
    if (!r.ok) { $("cdMsg").textContent = r.error || "读取失败"; return; }
    cdNames = r.names || {};
    const specs = r.specs || {};
    $("cdRows").innerHTML = Object.keys(cdNames).map(k => {
      const s = specs[k] || {};
      const tip = s.ax ? `锚点 ${s.ax}/${s.ay} 偏移 (${s.dx},${s.dy})` : "未设置";
      return `<div class="wlLine" style="margin:4px 0">
        <span style="min-width:110px">${esc(cdNames[k])}</span>
        <span class="hint" style="flex:1">${esc(tip)}</span>
        <button class="cdGo" data-k="${k}">记录</button>
      </div>`;
    }).join("");
    document.querySelectorAll(".cdGo").forEach(btn => btn.onclick = async () => {
      const k = btn.dataset.k;
      btn.disabled = true; btn.textContent = "3…";
      $("cdMsg").textContent = "现在把鼠标移到「" + (cdNames[k]||k) + "」上，停住不要动";
      try{
        const r2 = await api("/api/coords/capture", {method:"POST",
          headers:{"Content-Type":"application/json"},
          body: JSON.stringify({name:k, delay:3})});
        $("cdMsg").textContent = r2.ok ? ("✅ " + r2.msg) : ("❌ " + (r2.error||"失败"));
      }catch(e){ $("cdMsg").textContent = "请求失败：" + e; }
      finally{ btn.disabled = false; btn.textContent = "记录"; loadCoords(); }
    });
    $("cdCur").textContent = r.window
      ? ("微信客户区：" + r.client[2] + "×" + r.client[3] +
         (r.measured_client ? ("；上次校准于 " + r.measured_client.join("×")) : ""))
      : "找不到微信窗口";
  }catch(e){ $("cdMsg").textContent = "请求失败：" + e; }
}

if ($("cdReset")) {
  $("cdReset").onclick = async () => {
    const r = await api("/api/coords/reset", {method:"POST"});
    $("cdMsg").textContent = r.ok ? "已恢复默认" : (r.error || "失败");
    loadCoords();
  };
  setInterval(async () => {
    try{
      const c = await api("/api/coords/cursor", {method:"POST"});
      if (c.ok) $("cdCur").textContent = "鼠标：" + c.x + ", " + c.y +
        ($("cdCur").dataset.base ? ("   " + $("cdCur").dataset.base) : "");
    }catch(e){}
  }, 500);
}

'''

with io.open(UI, "r", encoding="utf-8") as f:
    h = f.read()

if "id=\"cdRows\"" not in h:
    anchor = '      <h2 style="margin-top:16px">UIA 树诊断</h2>'
    assert anchor in h, "状态页锚点没找到"
    h = h.replace(anchor, CARD.strip("\n") + "\n" + anchor, 1)

if "loadCoords" not in h:
    anchor2 = "// ---------------- 记忆页 ----------------"
    assert anchor2 in h, "JS 锚点没找到"
    h = h.replace(anchor2, JS + anchor2, 1)
    # 状态页切过去时加载一次
    h = h.replace('''document.querySelector('.nav-item[data-view="memory"]').addEventListener("click", () => {
  loadMemory();
});''', '''document.querySelector('.nav-item[data-view="memory"]').addEventListener("click", () => {
  loadMemory();
});
document.querySelector('.nav-item[data-view="status"]').addEventListener("click", () => {
  loadCoords();
});''')

with io.open(UI, "w", encoding="utf-8", newline="\n") as f:
    f.write(h)
print("index.html: 坐标校准卡片已加")
