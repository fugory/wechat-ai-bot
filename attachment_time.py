# -*- coding: utf-8 -*-
"""附件类消息（图片/视频/文件/链接/合并）每行带时间。

症状：用户问「昨天七点那张图我吃的啥」，模型答「我不知道」。
真相：两张图片都在窗口里（19:08 汤粉、21:11 洗剂），但渲染成
      `【我自己】[图片：一碗红油汤底…]`——没有时间，模型无法把
      「七点」和其中一张对上。

修法：附件类本来就少（图片 2%、文件<1%），给它们每行加 `MM-DD HH:MM`，
成本极低，却能回答"哪张图、什么时候"这类问题。
"""
import io

P = r"D:\dsh\projects\ai-chat-bot\bridge.py"
with io.open(P, "r", encoding="utf-8") as f:
    s = f.read()

# 1) render_message_body 增加 ts 参数
OLD_SIG = '''def render_message_body(db, chat_wxid: str, r: dict, nameof,
                        my_names: Optional[List[str]] = None,
                        img_desc_left: int = 0,
                        desc_left: int = 0) -> Tuple[str, str, int, int]:'''
NEW_SIG = '''def render_message_body(db, chat_wxid: str, r: dict, nameof,
                        my_names: Optional[List[str]] = None,
                        img_desc_left: int = 0,
                        desc_left: int = 0,
                        ts: int = 0) -> Tuple[str, str, int, int]:'''
assert OLD_SIG in s
s = s.replace(OLD_SIG, NEW_SIG, 1)

# 2) 加一个时间前缀小工具
HELPER = '''def _stamp(ts: int) -> str:
    """附件行的时间前缀：`MM-DD HH:MM `。"""
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(ts).strftime("%m-%d %H:%M ") + " "
    except Exception:
        return ""


'''
anchor = "def render_message_body("
s = s.replace(anchor, HELPER + anchor, 1)

# 3) 图片/视频/文件/链接带时间
OLD_IMG = '''        return "text", ("[图片：%s]" % desc if desc else "[图片]"), img_desc_left, desc_left'''
NEW_IMG = '''        body = ("[图片：%s]" % desc if desc else "[图片]")
        return "text", "[" + _stamp(ts) + body.strip("[]") + "]", img_desc_left, desc_left'''
assert OLD_IMG in s
s = s.replace(OLD_IMG, NEW_IMG, 1)

OLD_VID = '''    if rtype == "视频":
        return "text", "[视频]", img_desc_left, desc_left'''
NEW_VID = '''    if rtype == "视频":
        return "text", "[%s视频]" % _stamp(ts), img_desc_left, desc_left'''
assert OLD_VID in s
s = s.replace(OLD_VID, NEW_VID, 1)

# 4) 文件/链接/合并消息带时间
OLD_CARD = '''        label = APPMSG_LABEL.get(sub, "卡片")
        return "text", ("[%s：%s]" % (label, title)) if title else ("[%s]" % label), \\
            img_desc_left, desc_left'''
NEW_CARD = '''        label = APPMSG_LABEL.get(sub, "卡片")
        stamp = _stamp(ts)
        body = ("[%s%s：%s]" % (stamp, label, title)) if title else ("[%s%s]" % (stamp, label))
        return "text", body, img_desc_left, desc_left'''
assert OLD_CARD in s
s = s.replace(OLD_CARD, NEW_CARD, 1)

# 5) 调用处传 ts
OLD_CALL = '''        kind, body, img_desc_left, desc_left = render_message_body(
            db, wxid, r, nameof, my_names, img_desc_left, desc_left)'''
NEW_CALL = '''        kind, body, img_desc_left, desc_left = render_message_body(
            db, wxid, r, nameof, my_names, img_desc_left, desc_left, ts)'''
assert OLD_CALL in s
s = s.replace(OLD_CALL, NEW_CALL, 1)

with io.open(P, "w", encoding="utf-8", newline="\n") as f:
    f.write(s)

with io.open(P, "r", encoding="utf-8") as f:
    t = f.read()
assert "_stamp(ts)" in t
print("附件行已带时间")
