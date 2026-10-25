# -*- coding: utf-8 -*-
"""M3 自检：一条命令确认 embedding 能不能用。

用法：
  python check_embed.py          # 只测试接口
  python check_embed.py --backfill   # 顺便给已有事实补向量
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "gui"))


def main() -> int:
    import bridge as b
    b.load_dotenv(os.path.join(HERE, ".env"))
    cfg = b.load_config()
    v3 = ((cfg.get("memory_v2") or {}).get("v3") or {})
    print("端点:", v3.get("url"))
    print("模型:", v3.get("model"))
    print("key_env:", v3.get("key_env"), "| 有 key:",
          bool(os.environ.get(v3.get("key_env") or "ARK_EMBED_KEY")
               or os.environ.get("ARK_API_KEY")))

    probe = dict(cfg)
    probe["memory_v2"] = dict(cfg.get("memory_v2") or {})
    probe["memory_v2"]["v3"] = dict(v3, enabled=True)     # 临时启用测一次

    v = b._mem_embed(probe, "天很蓝，海很深")
    if not v:
        print("\n❌ 不可用 —— 看上面那行报错。")
        print("   若提示 ModelNotOpen：去方舟控制台「开通管理」开通该模型。")
        return 1
    print("\n✅ 可用，向量维度 %d" % len(v))

    if "--backfill" in sys.argv:
        import memory_store as ms
        conn = b.mem_conn()
        rows = conn.execute("SELECT id, text FROM facts").fetchall()
        ok = 0
        for r in rows:
            if ms.get_vec(conn, r["id"]):
                continue
            vec = b._mem_embed(probe, r["text"])
            if vec:
                ms.set_vec(conn, r["id"], vec)
                ok += 1
        print("已给 %d 条事实补向量（共 %d 条）" % (ok, len(rows)))

        # 顺便验证"换说法能不能搜到"
        real = dict(cfg)
        real["memory_v2"] = dict(cfg.get("memory_v2") or {})
        real["memory_v2"]["v3"] = dict(v3, enabled=True)
        b.load_config = lambda: real     # 让检索看到启用状态
        for q in ("程的背怎么了", "谁被叫大哥", "西餐汁"):
            blk = b.mem_facts_block("group", "44434946651@chatroom", q)
            print("  %-10s -> %s" % (q, blk.replace("\n", " ")[:80] or "（空）"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
