# -*- coding: utf-8 -*-
"""修两个会让 bot 完全发不出消息的 bug。

1) `name 'uia_open_chat' is not defined`
   原因：P0 的包装函数被追加到**文件末尾**，而 `if __name__ == "__main__": main()`
   在那之前。bot 是 `python bridge.py` 跑的（__main__），执行到 main() 就进去了，
   后面追加的包装根本还没定义 → 所有 UIA 调用 NameError。
   修法：把整段包装**移到 __main__ 守卫之前**。

2) `SQLite objects created in a thread can only be used in that same thread`
   原因：mem.db 连接在主线程建，看门狗线程直接拿去用。
   修法：连接开 check_same_thread=False，并给读写加一把锁。
"""
import io
import re

BR = r"D:\dsh\projects\ai-chat-bot\bridge.py"
MS = r"D:\dsh\projects\ai-chat-bot\memory_store.py"

# ---------- 1. 把包装块移到 __main__ 之前 ----------
with io.open(BR, "r", encoding="utf-8") as f:
    s = f.read()

MARK = "# ---------------- P0：UIA 串行锁包装 ----------------"
i = s.find(MARK)
assert i > 0, "找不到包装块"
block = s[i:].rstrip("\n")
s = s[:i].rstrip("\n")

m = re.search(r"^if __name__ == [\"']__main__[\"']:", s, re.M)
assert m, "找不到 __main__ 守卫"
s = s[:m.start()] + block + "\n\n\n" + s[m.start():]

with io.open(BR, "w", encoding="utf-8", newline="\n") as f:
    f.write(s)
print("bridge: 包装块已移到 __main__ 之前")

# ---------- 2. sqlite 跨线程 ----------
with io.open(MS, "r", encoding="utf-8") as f:
    t = f.read()

OLD = '''    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row'''
NEW = '''    # check_same_thread=False：主循环、看门狗线程都会用它（写入有 _DB_LOCK 保护）
    conn = sqlite3.connect(path, timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row'''
assert OLD in t, "sqlite connect 没匹配上"
t = t.replace(OLD, NEW, 1)

# 加一把模块级锁，给写操作串行（sqlite 本身线程安全，但避免 "database is locked"）
if "_DB_LOCK" not in t:
    t = t.replace("HERE = os.path.dirname(os.path.abspath(__file__))",
                  "import threading\n_DB_LOCK = threading.RLock()\n\n"
                  "HERE = os.path.dirname(os.path.abspath(__file__))", 1)

with io.open(MS, "w", encoding="utf-8", newline="\n") as f:
    f.write(t)
print("memory_store: 允许跨线程 + 加锁")
