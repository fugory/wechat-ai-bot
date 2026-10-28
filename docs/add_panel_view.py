# -*- coding: utf-8 -*-
"""表情页加「面板视图」：5 列网格，顺序与微信面板一致（最新在左上）。

为什么要这个：
  微信面板是「先收藏的在后面」，即数据库收藏顺序的倒序；
  坐标 = 第 (i//5) 行、第 (i%5) 列。把界面做成同样的 5 列，
  看到的格子就直接等于要点的那一格，出问题能一眼说清"第几行第几列"。
"""
import io

UI = r"D:\dsh\projects\ai-chat-bot\gui\static\index.html"

CARD = '''    <div class="card" style="margin-bottom:14px">
      <h2>面板视图（和微信一致：5 列，最新在左上）</h2>
      <div class="hint" style="margin-bottom:8px;line-height:1.7">
        每一格对应微信表情面板里同一位置的格子。点某格 = 发送时点那一格。<br>
        微信规则：<b>先收藏的排在后面</b>，所以左上角是最新收藏，右下角是最早收藏。
      </div>
      <div id="stPanelGrid" style="display:grid;grid-template-columns:repeat(5,1fr);gap:6px"></div>
      <div id="stPanelMsg" class="hint" style="margin-top:8px"></div>
    </div>
'''

JS = '''
// 面板视图：与微信面板同构（5 列，最新在左上）
function renderPanelView(){
  const box = $("stPanelGrid");
  if (!box) return;
  if (!stItems.length){ box.innerHTML = ""; $("stPanelMsg").textContent = "还没有表情"; return; }
  const n = stItems.length;
  // index 是收藏顺序（0 = 最早）；面板序号 = n-1-index
  const cells = stItems.map(s => ({s, p: n - 1 - (s.index ?? 0)})).sort((a,b) => a.p - b.p);
  box.innerHTML = cells.map(({s,p}) => {
    const img = s.has_thumb
      ? `<img src="/api/stickers/thumb/${s.md5}?t=${T}" loading="lazy"
             style="width:100%;height:100%;object-fit:contain;border-radius:6px">`
      : `<div class="hint" style="display:flex;align-items:center;justify-content:center;height:100%">无图</div>`;
    const rc = `第${Math.floor(p/5)+1}行第${p%5+1}列`;
    return `<div title="${esc(s.tag||s.md5)} · ${rc}"
      style="position:relative;aspect-ratio:1/1;background:#0d1117;border:1px solid #30363d;
             border-radius:8px;overflow:hidden">
      ${img}
      <span style="position:absolute;left:4px;top:3px;font-size:10px;color:#8b949e;
        background:rgba(13,17,23,.75);padding:0 3px;border-radius:3px">${p+1}</span>
      <span style="position:absolute;right:4px;bottom:3px;font-size:10px;color:#8b949e;
        background:rgba(13,17,23,.75);padding:0 3px;border-radius:3px">${rc}</span>
    </div>`;
  }).join("");
  $("stPanelMsg").textContent = `共 ${n} 个 · 5 列 · 总数不超过 25 时一页显示完`;
}
'''

with io.open(UI, "r", encoding="utf-8") as f:
    h = f.read()

if 'id="stPanelGrid"' not in h:
    anchor = '    <div id="stGrid"'
    assert anchor in h, "stGrid 锚点没找到"
    h = h.replace(anchor, CARD + anchor, 1)
    print("已插入面板视图卡片")

if "function renderPanelView" not in h:
    anchor2 = "function renderStickers(){"
    assert anchor2 in h
    h = h.replace(anchor2, JS + "\n" + anchor2, 1)
    print("已插入 renderPanelView")

# loadStickers 里调用
if "renderPanelView()" not in h.split("async function loadStickers")[1][:600]:
    h = h.replace('''    stItems = r.items || [];
    renderStickers();''', '''    stItems = r.items || [];
    renderStickers();
    renderPanelView();''', 1)

# 过滤时也刷新面板视图（保持两处一致）
if "renderStickers(); renderPanelView();" not in h:
    h = h.replace("$(\"stFilter\").oninput = renderStickers;",
                  "$(\"stFilter\").oninput = () => { renderStickers(); renderPanelView(); };")

with io.open(UI, "w", encoding="utf-8", newline="\n") as f:
    f.write(h)
print("index.html: 面板视图已加")
