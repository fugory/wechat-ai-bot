# -*- coding: utf-8 -*-
"""修 "保存白名单后未@插话概率被重置" 的真正根因。

后端 _cfg_view() 构造 groups 时只挑了 5 个字段，chime_prob 根本没返回给前端；
前端 loadConfig() 又只按返回的字段建对象。两边一起丢，所以：
    保存 40 → config.json 里是 40 → 前端重新加载拿到 null → 显示 0
    → 下次保存把 0 写回去

两处都补：
  1. app.py::_cfg_view  → groups 增加 chime_prob
  2. index.html loadConfig → 保留 chime_prob（上一轮已改，这里只确认）
"""
import io

APP = r"D:\dsh\projects\ai-chat-bot\gui\app.py"

with io.open(APP, "r", encoding="utf-8") as f:
    s = f.read()

OLD = '''    groups = [{"name": g.get("name", ""),
               "wxid": wxid_map.get(g.get("name", ""), ""),
               "require_at": g.get("require_at", True),
               "triggers": g.get("triggers") or [],
               "min_interval_s": g.get("min_interval_s")}
              for g in (cfg.get("allow_groups") or []) if isinstance(g, dict)]'''

NEW = '''    def _chime(g):
        try:
            v = float(g.get("chime_prob") or 0)
        except Exception:
            v = 0.0
        return max(0.0, min(100.0, v))

    groups = [{"name": g.get("name", ""),
               "wxid": wxid_map.get(g.get("name", ""), ""),
               "require_at": g.get("require_at", True),
               "triggers": g.get("triggers") or [],
               "min_interval_s": g.get("min_interval_s"),
               "chime_prob": _chime(g)}
              for g in (cfg.get("allow_groups") or []) if isinstance(g, dict)]'''

assert OLD in s, "_cfg_view groups 映射没匹配上"
s = s.replace(OLD, NEW, 1)

with io.open(APP, "w", encoding="utf-8", newline="\n") as f:
    f.write(s)
print("app.py: _cfg_view 现在会返回 chime_prob")
