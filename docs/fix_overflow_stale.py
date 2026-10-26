# -*- coding: utf-8 -*-
"""修：删掉表情后点"同步"，页面仍提示要删除。

根因：stickers_overflow.json 只在"有溢出"时被写；
      收藏回到 25 个以后没有溢出分支，文件既不清空也不删除 → 台账永远陈旧。

两层修复：
  1. sync_sticker_indices：没有溢出时删除台账文件
  2. /api/stickers/overflow：读台账后再按"当前真实收藏"过滤一遍（自愈，防陈旧）
"""
import io

BR = r"D:\dsh\projects\ai-chat-bot\bridge.py"
APP = r"D:\dsh\projects\ai-chat-bot\gui\app.py"

# ---------- 1. 同步时清空台账 ----------
with io.open(BR, "r", encoding="utf-8") as f:
    s = f.read()

OLD = '''    overflow = []
    if len(order) > cap:'''
NEW = '''    overflow = []
    if len(order) <= cap:
        # 没有溢出了：把旧台账删掉，否则界面会一直提示"请删除"
        try:
            _p = os.path.join(HERE, "stickers_overflow.json")
            if os.path.isfile(_p):
                os.remove(_p)
        except Exception:
            pass
    if len(order) > cap:'''
assert OLD in s, "bridge anchor miss"
s = s.replace(OLD, NEW, 1)

with io.open(BR, "w", encoding="utf-8", newline="\n") as f:
    f.write(s)
print("bridge: 无溢出时清台账")

# ---------- 2. 接口按真实收藏过滤 ----------
with io.open(APP, "r", encoding="utf-8") as f:
    a = f.read()

OLD2 = '''        over = data.get("overflow") or []
        desc = _load_json(DESC_PATH, {})
        return jsonify({"ok": True, "count": len(over), "at": data.get("at", ""),
                        "items": [{"md5": m, "tag": desc.get(m, "")} for m in over]})'''
NEW2 = '''        over = data.get("overflow") or []
        desc = _load_json(DESC_PATH, {})
        # 自愈：台账可能陈旧（用户已在微信里删了），按当前真实收藏过滤
        live = set()
        try:
            sys.path.insert(0, ROOT)
            import bridge as _b
            _b.load_dotenv(os.path.join(ROOT, ".env"))
            live = set(_b.sticker_order_md5s(_b._shared_db()))
        except Exception:
            pass
        if live:
            over = [m for m in over if m in live]
        return jsonify({"ok": True, "count": len(over), "at": data.get("at", ""),
                        "items": [{"md5": m, "tag": desc.get(m, "")} for m in over]})'''
assert OLD2 in a, "app anchor miss"
a = a.replace(OLD2, NEW2, 1)

with io.open(APP, "w", encoding="utf-8", newline="\n") as f:
    f.write(a)
print("app.py: overflow 接口按真实收藏自愈")
