# -*- coding: utf-8 -*-
"""把"验证方式没覆盖真实执行路径"写进 LESSONS + WORKFLOW。"""
import io

LESSONS = r"D:\dsh\projects\ai-chat-bot\LESSONS.md"
WORKFLOW = r"D:\dsh\projects\ai-chat-bot\docs\WORKFLOW.md"

ADD = '''

### 15.14 验证方式没覆盖真实执行路径（import 通过 ≠ 脚本能跑）

症状：给 UIA 入口加了串行锁，用 `import bridge` 冒烟测试全绿；
      但 bot 上线后每条消息都失败：
      `UIA 发送异常：name 'uia_open_chat' is not defined`

根因：包装函数被追加到**文件末尾**，而文件结构是

```python
def main(): ...
if __name__ == "__main__":
    sys.exit(main())        # ← 脚本模式在这里就进去了
# 追加的包装函数在这，永远执行不到
def uia_open_chat(): ...
```

两种执行方式的行为完全不同：

| 方式 | 结果 |
|---|---|
| `import bridge`（模块模式） | 整个文件执行完，包装函数有定义 ✅ |
| `python bridge.py`（脚本模式） | 进 `main()` 后不再往下，包装函数不存在 ❌ |

**我的冒烟测试用 `import`，正好绕开了生产实际用的脚本模式。**

规则：

1. **验证必须走生产真实入口**。GUI 怎么起 bot、你手动怎么跑，就怎么验。
   对带 `__main__` 守卫的文件，至少跑一次 `python 文件.py --help` 或真正起服务。
2. **任何"追加到文件末尾"的代码，都要先确认它在 `__main__` 之前**。
   插桩/包装/装饰器优先放在被调函数定义之后、`__main__` 之前。
3. **同一段代码要问"它会被谁以什么方式加载"**：import？脚本？打包 exe？子进程？
4. 冒烟测试的绿色不等于可用：**检查"验证路径"和"运行路径"是否同一条**。

同类陷阱清单（都算"验证路径 ≠ 运行路径"）：

- 用 `import` 测，生产用 `python x.py`
- 用源码目录测，生产用 PyInstaller 打包（`sys.frozen`）
- 在生产没有的 cwd 下测（相对路径失效）
- 用管理员权限测，用户以普通权限跑（UIPI）
- 用另一个 python 解释器测（依赖版本不同）
- 用 `pathlib` 的 POSIX 习惯测路径，Windows 上大小写/分隔符不同
'''

with io.open(LESSONS, "r", encoding="utf-8") as f:
    s = f.read()
if "15.14 验证方式没覆盖真实执行路径" in s:
    print("LESSONS 已有该条，跳过")
else:
    with io.open(LESSONS, "w", encoding="utf-8", newline="\n") as f:
        f.write(s.rstrip("\n") + ADD)
    print("LESSONS: 已加 15.14")

# WORKFLOW 也补一条
WF = '''

## 八、验证必须走生产入口

冒烟测试通过 ≠ 线上能跑。**验证路径必须和真实运行路径一致**。

| 生产怎么跑 | 就必须这样验 |
|---|---|
| `python bridge.py`（脚本模式） | 真的起一次脚本，不能只 `import` |
| GUI 拉起 bot（子进程） | 从 GUI 点「启动」验证 |
| 打包成 exe | 在干净目录跑 exe |
| 普通权限运行 | 不要用管理员测完就交付 |

带 `if __name__ == "__main__":` 的文件：**任何追加到末尾的插桩/包装都要放在这个守卫之前**，
否则脚本模式下根本不会被定义（`import` 测试看不出来）。
'''

with io.open(WORKFLOW, "r", encoding="utf-8") as f:
    w = f.read()
if "验证必须走生产入口" in w:
    print("WORKFLOW 已有该节，跳过")
else:
    with io.open(WORKFLOW, "w", encoding="utf-8", newline="\n") as f:
        f.write(w.rstrip("\n") + WF)
    print("WORKFLOW: 已加第八节")
