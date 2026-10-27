# -*- coding: utf-8 -*-
"""拆分 LESSONS：通用进工作区，项目专属留这里。

做法：从备份按行号切片重组，保证原文（含例子）不被改写。
"""
import io

BAK = r"D:\dsh\projects\ai-chat-bot\LESSONS.md.bak-split"
OUT = r"D:\dsh\projects\ai-chat-bot\LESSONS.md"

lines = io.open(BAK, encoding="utf-8").read().split("\n")


def seg(a, b):
    """取 1-based 闭区间的行。"""
    return "\n".join(lines[a - 1:b])


HEAD = """# LESSONS · 改动前必读（项目专属）

> **通用教训已提炼到工作区**：`D:\\dsh\\docs\\GENERAL_LESSONS.md`
> （验证方式、改代码的方法、数据与配置、排障方法、过程与交付）
>
> 本文件只留**跟微信/这个 bot 绑定**的坑。读这份之前建议先读通用的那份。
>
> 原则：宁可多花 30 秒验证，不要花 30 轮返工。

---

## 〇、项目专属与通用条目对照

| 通用条目 | 去哪了 |
|---|---|
| 大段替换删函数 / 补丁没生效 / 空值当基准 | 通用 B1、B2、C1 |
| 检查脚本写错 / 只测单点 | 通用 A3、A5 |
| 在错误层面勤奋（DPI）/ 用户说的问题 ≠ 我以为的 | 通用 D2、D4 |
| 改前固定动作 | 通用 B1 + 各条规则 |
| 快不等于好（8.1~8.5） | 通用 A2、D3、E1、E2、E3 |
| 保存类接口原样往返（第九章） | 通用 C2 |
| heredoc 吃 `$()` / 按标题正则删区块 | 通用 B3、B4 |
| 探针恒返回 0 | 通用 A4 |
| `None * 0` 崩溃 / dict vs list | 通用 C3、C4 |
| 验证路径 ≠ 运行路径（15.14） | 通用 A1 |
| 对照实验定位（16.5）/ 故障类型（16.2） | 通用 D1、D5 |
| 截图 DPI（15.3） | 通用 F1 |

---

"""

BODY = []

BODY.append("## 一、与微信交互的硬坑\n")
BODY.append(seg(48, 58))      # 4 假成功
BODY.append(seg(59, 70))      # 5 截图当断言
BODY.append(seg(71, 83))      # 6 破坏前提
BODY.append(seg(198, 217))    # 7 @ 卡输入框
BODY.append(seg(220, 247))    # 8 后台数据推断界面

BODY.append("---\n\n## 二、硬性红线\n")
BODY.append("\n".join(lines[164:172]))   # 163 之后到 172（跳过 ## 五 标题）

BODY.append("\n\n---\n\n## 三、当前架构的关键事实（免得重新发现）\n")
BODY.append("\n".join(lines[176:186]))   # 175 之后

BODY.append("\n\n---\n\n## 四、切会话与坐标（UIA 相关）\n")
BODY.append(seg(391, 458))    # 十
BODY.append(seg(460, 511))    # 十一
BODY.append(seg(513, 545))    # 十二
BODY.append(seg(547, 571))    # 十三

BODY.append("\n---\n\n## 五、表情与记忆（产品级约束）\n")
BODY.append(seg(573, 602))    # 十四
BODY.append(seg(625, 634))    # 15.3
BODY.append(seg(643, 650))    # 15.5 别每 90 秒拽前台
BODY.append(seg(651, 658))    # 15.6 自愈无限重试
BODY.append(seg(659, 666))    # 15.7 收藏排序
BODY.append(seg(667, 674))    # 15.8 坐标缓存错位
BODY.append(seg(675, 685))    # 15.9 embedding 404
BODY.append(seg(694, 701))    # 15.11 检索下限
BODY.append(seg(702, 709))    # 15.12 @别人

BODY.append("\n---\n\n## 六、卡死与节流（2026-10-26/27）\n")
BODY.append(seg(763, 781))    # 16.1
BODY.append(seg(801, 810))    # 16.3
BODY.append(seg(811, 824))    # 16.4

text = HEAD + "\n".join(BODY).strip() + "\n"

io.open(OUT, "w", encoding="utf-8", newline="\n").write(text)

n_before = len(lines)
n_after = len(text.split("\n"))
print("原 %d 行 → 新 %d 行" % (n_before, n_after))
