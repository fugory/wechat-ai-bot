# -*- coding: utf-8 -*-
"""修：保存白名单后"未@插话概率"被重置。

根因：loadConfig() 把后端返回的群列表映射成 wlGroups 时**只挑了 5 个字段**，
      漏掉 chime_prob。于是：
        保存 → 后端存住 40 → loadConfig() 重新加载 → wlGroups 里 chime_prob 丢失
        → 界面滑块回 0 → 下次保存再把 0 写回后端
"""
import io

P = r"D:\dsh\projects\ai-chat-bot\gui\static\index.html"

with io.open(P, "r", encoding="utf-8") as f:
    h = f.read()

OLD = ("wlGroups = (c.groups||[]).map(g => ({wxid:g.wxid, name:g.name, "
       "require_at:!!g.require_at, triggers:g.triggers||[], "
       "min_interval_s:g.min_interval_s||2}));")
NEW = ("wlGroups = (c.groups||[]).map(g => ({wxid:g.wxid, name:g.name, "
       "require_at:!!g.require_at, triggers:g.triggers||[], "
       "min_interval_s:g.min_interval_s||2, "
       "chime_prob: (g.chime_prob===undefined||g.chime_prob===null) ? 0 : Number(g.chime_prob)}));")

if OLD not in h:
    # 可能格式略有差异：退化成按关键字替换
    import re
    pat = re.compile(r"wlGroups = \(c\.groups\|\|\[\]\)\.map\(g => \(\{[\s\S]{0,400}?\}\)\);")
    m = pat.search(h)
    assert m, "找不到 wlGroups 的加载映射"
    print("用正则命中，原文：\n" + m.group(0)[:300])
    h = h[:m.start()] + NEW + h[m.end():]
else:
    h = h.replace(OLD, NEW, 1)

with io.open(P, "w", encoding="utf-8", newline="\n") as f:
    f.write(h)
print("index.html: loadConfig 已保留 chime_prob")
