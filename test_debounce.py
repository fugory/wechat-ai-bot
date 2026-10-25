# -*- coding: utf-8 -*-
"""debounce（合并碎片）+ 回看（按时间捞群记录）的行为测试。

不连微信：FakeDB 提供假消息，FakeClock 控制时间，FakeBackend 记录模型收到的输入。
"""
import sys

sys.path.insert(0, r"D:\dsh\projects\ai-chat-bot")
import bridge as b  # noqa: E402


class FakeClock:
    def __init__(self, t=1000.0):
        self.t = t

    def time(self):
        return self.t

    def sleep(self, s):
        self.t += s

    def monotonic(self):
        return self.t


class FakeDB:
    def __init__(self, rows):
        self.rows = rows

    def get_new_messages(self, wxid, since_seq=0, limit=200):
        out = [r for r in self.rows if int(r["sort_seq"]) > since_seq]
        out.sort(key=lambda r: r["sort_seq"])
        return out[:limit]

    def get_messages(self, wxid, limit=200):
        return sorted(self.rows, key=lambda r: r["sort_seq"], reverse=True)[:limit]

    def nickname_map(self):
        return {"wxid_a": "阿强", "wxid_b": "小美"}

    def get_group_members(self, gid):
        return []


class FakeBackend:
    name = "fake"

    def __init__(self):
        self.calls = []

    def reply(self, text, ctx):
        self.calls.append({"text": text, "messages": ctx.get("messages")})
        return "回：%s" % text[:20]


def row(seq, sender, text, ts, gid="g1"):
    return {
        "type": "文本", "sender_username": sender, "sender_id": 11,
        "content": "%s:\n%s" % (sender, text),
        "create_time": ts, "sort_seq": seq, "local_id": seq,
    }


def make_cfg(wait=2.0, max_wait=6.0):
    return {
        "limits": {"quiet_hours": [], "per_friend_per_hour": 8},
        "debounce": {"enabled": True, "wait_s": wait, "max_wait_s": max_wait,
                     "lookback_s": 300, "max_messages": 40},
        "backend": {"group_system": "test"},
        "allow_groups": [{"name": "测试群", "triggers": ["大肥鲸"],
                          "require_at": True, "min_interval_s": 0,
                          "self_names": ["大肥鲸-aiBot"], "pre_send_delay_s": [0, 0],
                          "bubbles": {"enabled": False}}],
    }


def run_case(title, rows, steps, wait=2.0):
    print("\n=== %s ===" % title)
    clock = FakeClock(rows[0]["create_time"] if rows else 1000.0)
    b.time = clock
    cfg = make_cfg(wait)
    entry = cfg["allow_groups"][0]
    backend = FakeBackend()
    db = FakeDB(rows)
    state, pending = {}, {}
    guard = b.Guard(cfg["limits"])
    groups = [{"wxid": "g1", "name": "测试群", "entry": entry}]

    for step, moment in enumerate(steps, 1):
        clock.t = moment
        n = b.one_round_groups(db, cfg, backend, guard, state, True, False,
                               "wxid_me", groups, None, None, {}, pending)
        print("  t=%.0f 回复%d次 待处理%d条" % (moment, n, len(pending)))

    print("  模型被调用 %d 次" % len(backend.calls))
    for c in backend.calls:
        print("    输入=%r" % c["text"])
        if c["messages"]:
            head = c["messages"][0]["content"].split("\n")[0]
            print("    上下文首行=%r" % head)
    if backend.calls and backend.calls[0]["messages"]:
        print("  --- 完整上下文 ---")
        for line in backend.calls[0]["messages"][0]["content"].split("\n"):
            print("  | " + line)
    return backend


# 1) 同一个人连发两条（间隔 1 秒）→ 应该只回一次，且两条合并
run_case("同一人连发，合并成一次",
         [row(1, "wxid_a", "@大肥鲸-aiBot 今晚吃啥", 1000),
          row(2, "wxid_a", "那家新开的店", 1001)],
         [1001, 1002.5, 1004])

# 2) 两个人分别 @ → 各自回一次
run_case("两个人分别 @，各回各的",
         [row(1, "wxid_a", "@大肥鲸-aiBot 在吗", 1000),
          row(2, "wxid_b", "@大肥鲸-aiBot 帮我看个东西", 1001)],
         [1001, 1004])

# 3) 没人 @ → 一条都不回
run_case("没人 @，保持沉默",
         [row(1, "wxid_a", "今天好热啊", 1000),
          row(2, "wxid_b", "确实", 1001)],
         [1001, 1005])
