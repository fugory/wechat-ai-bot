# -*- coding: utf-8 -*-
"""A + C：按"用户明说的发送者约束"过滤附件，并修日志。

设计（用户 2026-10-23 确认）：
  * 自我指代（我发的/我的/我拍的/我截的/我照的）→ 约束 = 提问者
  * 提到成员名字，且名字旁有「图/照片/截图/视频/发的」→ 约束 = 该成员
  * 都没有 → 不约束，所有人按时间最近
  * 有约束但找不到 → **不插提示**（宁可不说，也不给错误事实）
  * 去掉原来的"自己发的加 5 分钟"临时补丁，避免两套机制互相干扰
"""
import io

P = r"D:\dsh\projects\ai-chat-bot\bridge.py"
with io.open(P, "r", encoding="utf-8") as f:
    s = f.read()

# 1) 新增约束解析
HELPER = '''SELF_REF_RE = re.compile(r"我(?:自己)?(?:\s*)(?:发|拍|截|照|传)的|我的图|我那张")
NEAR_ATTACH_RE = re.compile(r"(图|照片|截图|视频|拍|发)")


def parse_sender_constraint(text: str, asker: str, member_index: Dict[str, str],
                            my_wxid: str) -> Tuple[str, str]:
    """解析出"用户明确指认的发送者"。返回 (wxid 或空, 说明)。

    只在用户**明确**说了是谁的时候才给约束；没说就返回空，交给时间排序。
    """
    t = text or ""
    if SELF_REF_RE.search(t):
        me = my_wxid or ""
        if me:
            return me, "你说的「我发的」"
        return "", ""
    # 名字 + 附件词同时出现才算指认（避免"这张图里程好搞笑"被误判）
    best = ("", "")
    for name, wxid in (member_index or {}).items():
        if not name or len(name) < 2 or name not in t:
            continue
        i = t.find(name)
        window = t[max(0, i - 6): i + len(name) + 8]
        if NEAR_ATTACH_RE.search(window):
            if len(name) > len(best[1]):
                best = (wxid, name)
    if best[0]:
        return best[0], "你提到了「%s」" % best[1]
    return "", ""


'''
anchor = "def find_attachment_near("
assert anchor in s
s = s.replace(anchor, HELPER + anchor, 1)

# 2) find_attachment_near 支持 only_sender，并去掉 5 分钟加成
OLD_SIG = '''def find_attachment_near(db, chat_wxid: str, target: datetime, nameof,
                         my_wxid: str, asker: str = "",
                         window_min: int = 60) -> str:'''
NEW_SIG = '''def find_attachment_near(db, chat_wxid: str, target: datetime, nameof,
                         my_wxid: str, asker: str = "",
                         window_min: int = 60,
                         only_sender: str = "") -> str:'''
assert OLD_SIG in s
s = s.replace(OLD_SIG, NEW_SIG, 1)

OLD_SCORE = '''        delta = abs((when - target).total_seconds()) / 60.0
        if delta > window_min:
            continue
        score = delta - (5 if (asker and (nameof(r.get("sender_username") or "") == asker)) else 0)
        if best is None or score < best[0]:
            best = (score, when, r)'''
NEW_SCORE = '''        if only_sender and (r.get("sender_username") or "") != only_sender:
            continue                       # 用户指认了是谁，别人发的直接排除
        delta = abs((when - target).total_seconds()) / 60.0
        if delta > window_min:
            continue
        if best is None or delta < best[0]:  # 只按时间最近排序（不再给身份加时间）
            best = (delta, when, r)'''
assert OLD_SCORE in s
s = s.replace(OLD_SCORE, NEW_SCORE, 1)

# 3) 调用处：先算约束，再传进去；日志打印完整行
OLD_CALL = '''                if target:
                    q_for_prompt = merged + note       # 把"昨晚七点=10-22 19:00"给模型
                    hit = find_attachment_near(db, wxid, target, nameof, my_wxid, asker)
                    if hit:
                        ctx["attach_hint"] = hit
                        log("附件定位：%s → %s" % (note.strip("（）"), hit[14:60]))'''
NEW_CALL = '''                if target:
                    q_for_prompt = merged + note       # 把"昨晚七点=10-22 19:00"给模型
                    only, why = parse_sender_constraint(
                        merged, asker, group_member_index(db, wxid, {}), my_wxid)
                    hit = find_attachment_near(db, wxid, target, nameof, my_wxid,
                                               asker, only_sender=only)
                    if hit:
                        ctx["attach_hint"] = hit
                        log("附件定位：%s%s → %s"
                            % (note.strip("（）"), ("（%s）" % why) if why else "", hit))
                    elif only:
                        log("附件定位：按「%s」过滤后没找到 %s 左右的附件，不插提示"
                            % (why, target.strftime("%m-%d %H:%M")))'''
assert OLD_CALL in s
s = s.replace(OLD_CALL, NEW_CALL, 1)

with io.open(P, "w", encoding="utf-8", newline="\n") as f:
    f.write(s)

with io.open(P, "r", encoding="utf-8") as f:
    t = f.read()
assert "def parse_sender_constraint" in t and "only_sender=only" in t
print("A + C 已实现")
