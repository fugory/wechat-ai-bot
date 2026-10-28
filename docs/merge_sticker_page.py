# -*- coding: utf-8 -*-
"""表情页合并：一套 5 列网格（顺序=微信面板），保留标签/喜好/次数，去掉面板视图与试发。"""
import io
import re

UI = r"D:\dsh\projects\ai-chat-bot\gui\static\index.html"
APP = r"D:\dsh\projects\ai-chat-bot\gui\app.py"

with io.open(UI, "r", encoding="utf-8") as f:
    h = f.read()

# ---------- 1. 删掉「面板视图」卡片 ----------
m = re.search(r'    <div class="card" style="margin-bottom:14px">\s*<h2>面板视图.*?</div>\s*</div>\s*\n', h, re.S)
if m:
    h = h[:m.start()] + h[m.end():]
    print("已删除面板视图卡片")
else:
    print("⚠ 没找到面板视图卡片")

# ---------- 2. 删掉 renderPanelView 函数 ----------
i = h.find("// 面板视图：与微信面板同构")
j = h.find("function renderStickers", i)
if i > 0 and j > i:
    h = h[:i] + h[j:]
    print("已删除 renderPanelView")
else:
    print("⚠ 没找到 renderPanelView")

# ---------- 3. 删掉调用与试发事件 ----------
h = h.replace("    renderPanelView();          // 5 列面板视图（和微信一致）\n", "")
h = re.sub(r"// 面板视图：悬停显示「试发」.*?\n\}\);\n", "", h, flags=re.S)
h = h.replace("    renderStickers(); renderPanelView();", "    renderStickers();")
h = h.replace('$("stFilter").oninput = () => { renderStickers(); renderPanelView(); };',
              '$("stFilter").oninput = renderStickers;')

# ---------- 4. 网格 5 列 ----------
if ".stGrid" not in h:
    h = h.replace(".stCard{background:#fbfcfd",
                  "#stGrid{display:grid;grid-template-columns:repeat(5,1fr);gap:10px}\n"
                  ".stCard{background:#fbfcfd", 1)
    print("已加 5 列网格样式")

# ---------- 5. 卡片改成面板顺序 + 行列标注 ----------
OLD_MAP = """  $("stGrid").innerHTML = list.map(s => {
    const img = s.has_thumb
      ? `<img src="/api/stickers/thumb/${s.md5}?t=${T}" loading="lazy">`
      : `<div class="ph">无图</div>`;
    const hearts = [1,2,3,4,5].map(n =>
      `<i class="${n<=s.love?'on':''}" data-md5="${s.md5}" data-love="${n}"></i>`).join("");
    return `<div class="stCard" data-md5="${s.md5}">
      ${img}
      <input type="text" class="stTag" value="${esc(s.tag||'')}" placeholder="标签">
      <div class="meta"><span>#${s.index}</span><span>用了 ${s.uses} 次</span></div>
      <div class="love">${hearts}</div>"""

NEW_MAP = """  const n0 = stItems.length;
  // 面板顺序 = 收藏顺序倒序（最新在左上），和微信面板一致
  const ordered = list.slice().sort((a,b) => (a.index ?? 0) - (b.index ?? 0)).reverse();
  $("stGrid").innerHTML = ordered.map(s => {
    const img = s.has_thumb
      ? `<img src="/api/stickers/thumb/${s.md5}?t=${T}" loading="lazy">`
      : `<div class="ph">无图</div>`;
    const hearts = [1,2,3,4,5].map(n =>
      `<i class="${n<=s.love?'on':''}" data-md5="${s.md5}" data-love="${n}"></i>`).join("");
    const p = n0 - 1 - (s.index ?? 0);
    const rc = `第${Math.floor(p/5)+1}行第${p%5+1}列`;
    return `<div class="stCard" data-md5="${s.md5}">
      <div class="meta" style="margin:0 0 4px;font-size:11px">
        <span title="面板序号">${p+1}</span><span>${rc}</span>
      </div>
      ${img}
      <input type="text" class="stTag" value="${esc(s.tag||'')}" placeholder="标签">
      <div class="meta"><span>#${s.index}</span><span>用了 ${s.uses} 次</span></div>
      <div class="love">${hearts}</div>"""

assert OLD_MAP in h, "卡片模板锚点没匹配上"
h = h.replace(OLD_MAP, NEW_MAP, 1)

with io.open(UI, "w", encoding="utf-8", newline="\n") as f:
    f.write(h)
print("表情页：已合并为 5 列网格（面板顺序）")

# ---------- 6. 删掉试发接口与脚本 ----------
with io.open(APP, "r", encoding="utf-8") as f:
    a = f.read()
i = a.find('@app.post("/api/stickers/send")')
if i > 0:
    j = a.find("@app.post(", i + 10)
    a = a[:i] + a[j:] if j > i else a[:i]
    with io.open(APP, "w", encoding="utf-8", newline="\n") as f:
        f.write(a)
    print("已删除 /api/stickers/send")
else:
    print("没有该接口")
