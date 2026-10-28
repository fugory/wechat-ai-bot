# -*- coding: utf-8 -*-
"""补上 renderPanelView 的调用（之前只插了定义，没插调用点）。"""
import io

P = r"D:\dsh\projects\ai-chat-bot\gui\static\index.html"
with io.open(P, "r", encoding="utf-8") as f:
    h = f.read()

OLD = """    stItems = r.items || [];
    renderStickers();
  }catch(e){ $("stGrid").innerHTML = '<div class="err">请求失败：' + esc(e) + '</div>'; }"""
NEW = """    stItems = r.items || [];
    renderStickers();
    renderPanelView();          // 5 列面板视图（和微信一致）
  }catch(e){ $("stGrid").innerHTML = '<div class="err">请求失败：' + esc(e) + '</div>'; }"""

if "renderPanelView();" in h:
    print("已经有调用点，跳过")
else:
    assert OLD in h, "loadStickers 锚点没匹配上"
    h = h.replace(OLD, NEW, 1)
    with io.open(P, "w", encoding="utf-8", newline="\n") as f:
        f.write(h)
    print("已补调用点")
