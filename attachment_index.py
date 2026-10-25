# -*- coding: utf-8 -*-
"""附件索引 + 明确告知"占位符是已经看过的内容"。

症状：窗口里明明有一行
    【我自己】[10-22 19:08 图片：一碗红油汤底配有炸蛋、腐竹和空心菜的汤粉。]
用户问"昨天七点吃的啥"，模型仍答"我不知道，我又没长眼去看"。

原因两条：
  1. 337 行里夹一行附件，显著性太低，模型扫不到
  2. 没人告诉它 `[图片：…]` 是**系统已经看过并描述过的**，它以为是自己没权限

改法：
  * 问题里出现"图/照片/视频/文件/附件/几点/昨天"等词时，额外拼一个
    【最近附件】索引块，直接列时间+谁发的+内容
  * system 里加一句：占位符里的描述就是可用的信息，不要说"我没长眼"
"""
import io

P = r"D:\dsh\projects\ai-chat-bot\bridge.py"
with io.open(P, "r", encoding="utf-8") as f:
    s = f.read()

FUNC = '''ATTACH_HINT_RE = re.compile(r"(图|照片|截图|视频|文件|附件|表情包|几点|昨天|前天|刚才|早上|中午|晚上)")


def attachment_index(db, chat_wxid: str, nameof, my_wxid: str,
                     limit: int = 8, cache: Optional[Dict] = None) -> str:
    """最近附件索引：一行一个，带时间和描述。

    为什么单独做一块：附件在 300+ 行场景里太不显眼，用户一问"那张图"模型就找不到。
    单独列出来放问题旁边，命中率会高很多。
    """
    try:
        rows = db.get_messages(chat_wxid, limit=400) or []
    except Exception:
        return ""
    out = []
    img_left = 6
    for r in rows:                       # 新→旧
        rtype = r.get("type")
        if rtype not in ("图片", "视频", "文件/链接/卡片"):
            continue
        ts = int(r.get("create_time") or 0)
        sender = r.get("sender_username") or ""
        who = "我" if (my_wxid and sender == my_wxid) else (nameof(sender) or "有人")
        if rtype == "图片":
            desc = ""
            if img_left > 0:
                try:
                    desc = describe_image_row(db, chat_wxid, r)
                except Exception:
                    desc = ""
                if desc:
                    img_left -= 1
            body = "图片：%s" % desc if desc else "图片"
        elif rtype == "视频":
            body = "视频"
        else:
            content = str(r.get("content") or "")
            m = APPMSG_TYPE_RE.search(content)
            sub = m.group(1) if m else ""
            tm = TITLE_RE.search(content)
            title = " ".join((tm.group(1) if tm else "").split())[:40]
            if sub == "57":
                continue                   # 引用回复不算附件
            body = "%s：%s" % (APPMSG_LABEL.get(sub, "卡片"), title) if title else \\
                APPMSG_LABEL.get(sub, "卡片")
        try:
            stamp = datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")
        except Exception:
            stamp = ""
        out.append("  %s %s %s" % (stamp, who, body))
        if len(out) >= limit:
            break
    if not out:
        return ""
    return "【最近的图片/文件（系统已看过并描述，可直接引用）】\\n" + "\\n".join(out)


'''

anchor = "def render_message_body("
assert anchor in s
s = s.replace(anchor, FUNC + anchor, 1)

# 群回复处：命中关键词就拼附件索引
OLD = '''        try:
            vcfg = cfg.get("vision") or {}
            if vcfg.get("enabled", True):
                blk = recent_images_block('''
NEW = '''        # 问题里提到图/照片/几点之类，就给一份附件索引（附件在长场景里太不显眼）
        if ATTACH_HINT_RE.search(merged or ""):
            try:
                idx = attachment_index(db, wxid, nameof, my_wxid, 8)
                if idx:
                    ctx["attachments"] = idx
            except Exception as e:
                log("附件索引失败（忽略）：%s" % e)

        try:
            vcfg = cfg.get("vision") or {}
            if vcfg.get("enabled", True):
                blk = recent_images_block('''
assert OLD in s
s = s.replace(OLD, NEW, 1)

# reply()：把附件索引拼在 user 末尾
OLD2 = '''        sblk = (ctx.get("stickers_seen") or "").strip()
        if sblk:
            system = system + "\\n\\n【他们刚发的表情】\\n" + sblk'''
NEW2 = '''        sblk = (ctx.get("stickers_seen") or "").strip()
        if sblk:
            system = system + "\\n\\n【他们刚发的表情】\\n" + sblk

        # 附件索引放在 user 末尾（靠近问题，显著性高）
        att = (ctx.get("attachments") or "").strip()
        if att:
            msgs.append({"role": "user", "content": att})'''
assert OLD2 in s
s = s.replace(OLD2, NEW2, 1)

with io.open(P, "w", encoding="utf-8", newline="\n") as f:
    f.write(s)

with io.open(P, "r", encoding="utf-8") as f:
    t = f.read()
assert "def attachment_index" in t and 'ctx.get("attachments")' in t
print("附件索引已接入")
