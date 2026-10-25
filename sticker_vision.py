# -*- coding: utf-8 -*-
"""表情包解码与理解。

微信 4.x 的收藏/缓存表情存放在：
    business/emoticon/Persist/<md5前两位>/<md5>
内容加密方式（来源：CN-Grace/Wechat-Emoticon-Parser v4.0-plus 的算法说明）：

    key = md5(f"{seed}{wxid}EMOTICON")[:16]     # seed 是账号级常量，在微信进程内存里
    AES-128-CBC + PKCS7，IV = key（key 自己当 IV）

解出来的明文多数是 `wxgf` 容器（内部 HEVC 裸流），需要用 ffmpeg 转首帧。
注意：`emoticon.db` 里的 `aes_key` 字段是给 CDN 下载用的，**不是**本地文件的密钥。

设计原则：
  * 不 import bridge（避免循环依赖），需要视觉描述时由调用方传入 describe 回调
  * seed/key 缓存到 emoji_key.json，用"解一个已知文件"来验证，失效就重扫
  * 表情描述按 md5 缓存到 sticker_desc.json，同一张永远只分析一次
"""
import ctypes
import ctypes.wintypes as wt
import hashlib
import json
import os
import re
import time
from typing import Callable, Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
KEY_CACHE = os.path.join(HERE, "emoji_key.json")
DESC_CACHE = os.path.join(HERE, "sticker_desc.json")

WECHAT_FILES_ROOTS = [
    r"D:\浏览器下载\qq类\xwechat_files",
]
EMOTICON_SUB = os.path.join("business", "emoticon", "Persist")

MAGICS = (b"\x89PNG", b"GIF8", b"\xff\xd8\xff", b"wxgf", b"RIFF")

# ---------- 内存扫描（照搬 v4.0-plus 的思路，只读不写） ----------

kernel32 = ctypes.windll.kernel32
PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400
MEM_COMMIT = 0x1000
MEM_PRIVATE = 0x20000
READABLE = {0x02, 0x04, 0x08, 0x20, 0x40, 0x80}
WRITABLE = {0x04, 0x08, 0x40}
CHUNK = 32 * 1024 * 1024
MAX_REGION = 0x40000000
RE_SEED = re.compile(rb"(?<![0-9])(\d{8,12})(?![0-9])")


class _MBI(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wt.DWORD),
        ("__alignment1", wt.DWORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wt.DWORD),
        ("Protect", wt.DWORD),
        ("Type", wt.DWORD),
        ("__alignment2", wt.DWORD),
    ]


def _read_mem(h, addr, size):
    buf = ctypes.create_string_buffer(size)
    n = ctypes.c_size_t(0)
    if kernel32.ReadProcessMemory(h, ctypes.c_uint64(addr), buf, size, ctypes.byref(n)):
        return buf.raw[: n.value]
    return None


def _regions(h):
    regs = []
    addr = 0
    mbi = _MBI()
    while addr < 0x7FFFFFFFFFFF:
        if kernel32.VirtualQueryEx(h, ctypes.c_uint64(addr), ctypes.byref(mbi),
                                   ctypes.sizeof(mbi)) == 0:
            break
        if (mbi.State == MEM_COMMIT and mbi.Protect in READABLE
                and 0 < mbi.RegionSize < MAX_REGION):
            if mbi.Type == MEM_PRIVATE and mbi.Protect in WRITABLE:
                regs.append((mbi.BaseAddress, mbi.RegionSize))
        nxt = (mbi.BaseAddress or 0) + (mbi.RegionSize or 0)
        if nxt <= addr:
            addr += 0x1000
            continue
        addr = nxt
    return regs


def _main_pid() -> Optional[int]:
    try:
        import psutil
    except Exception:
        return None
    best = None
    for p in psutil.process_iter(["pid", "name", "memory_info"]):
        if (p.info.get("name") or "").lower() != "weixin.exe":
            continue
        rss = p.info["memory_info"].rss if p.info.get("memory_info") else 0
        if best is None or rss > best[0]:
            best = (rss, p.info["pid"])
    return best[1] if best else None


def _derive(seed: int, wxid: str) -> bytes:
    d = hashlib.md5(("%s%sEMOTICON" % (seed, wxid)).encode()).hexdigest()
    return bytes.fromhex(d)[:16]


def _aes_cbc(key: bytes, data: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    dec = Cipher(algorithms.AES(key), modes.CBC(key)).decryptor()
    return dec.update(data) + dec.finalize()


def _unpad(data: bytes) -> bytes:
    if not data:
        return data
    pad = data[-1]
    if 1 <= pad <= 16 and data.endswith(bytes([pad]) * pad):
        return data[:-pad]
    return data


def _is_image(plain: bytes) -> bool:
    return plain[:4] in MAGICS or plain[:3] == b"\xff\xd8\xff"


# ---------- 定位数据目录与文件 ----------

def find_account_dir(explicit: Optional[str] = None) -> Optional[str]:
    """找当前账号的 xwechat_files 子目录（含 business/emoticon 的那个）。"""
    if explicit and os.path.isdir(explicit):
        return explicit
    for root in WECHAT_FILES_ROOTS:
        if not os.path.isdir(root):
            continue
        for name in os.listdir(root):
            p = os.path.join(root, name)
            if os.path.isdir(os.path.join(p, EMOTICON_SUB)):
                return p
    return None


def wxid_of(account_dir: str) -> str:
    """目录名去掉 `_xxxx` 后缀就是 wxid。"""
    name = os.path.basename(account_dir.rstrip("\\/"))
    # 目录名形如 wxid_c989c67pvp8122_8e10：只去掉最后那段后缀，
    # 不能 split("_")[0]，那会得到 "wxid"（踩过）。
    return name.rsplit("_", 1)[0] if "_" in name else name


def persist_dir(account_dir: str) -> str:
    return os.path.join(account_dir, EMOTICON_SUB)


def find_sticker_file(account_dir: str, md5: str) -> Optional[str]:
    md5 = (md5 or "").lower()
    if len(md5) < 4:
        return None
    p = os.path.join(persist_dir(account_dir), md5[:2], md5)
    if os.path.isfile(p):
        return p
    # 兜底：个别版本把头两位换成别的分桶，整体扫一遍（有缓存不会很慢）
    root = persist_dir(account_dir)
    for dp, _, fs in os.walk(root):
        for f in fs:
            if f.lower() == md5:
                return os.path.join(dp, f)
    return None


# ---------- key 的获取、验证、缓存 ----------

def _load_key_cache() -> Dict:
    try:
        with open(KEY_CACHE, "r", encoding="utf-8-sig") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _save_key_cache(d: Dict) -> None:
    try:
        with open(KEY_CACHE, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


def _get_key_raw(account_dir: str) -> Optional[bytes]:
    """取 key。先查缓存，用真实文件验证；不通过就扫内存。"""
    cache = _load_key_cache()
    wxid = wxid_of(account_dir)
    if cache.get("key") and cache.get("wxid") == wxid:
        try:
            k = bytes.fromhex(cache["key"])
            # 必须验证：微信重启后 seed 可能变，旧 key 会静默解出乱码。
            if _key_works(account_dir, k):
                return k
        except Exception:
            pass
    key = scan_key(account_dir)
    if key:
        _save_key_cache({"wxid": wxid, "key": key.hex(),
                         "seed": cache.get("seed"), "at": time.strftime("%F %T")})
    return key


def _key_works(account_dir: str, key: bytes) -> bool:
    """拿一个真实表情文件试解，能出图片魔数才算 key 有效。"""
    root = persist_dir(account_dir)
    for dp, _, fs in os.walk(root):
        for f in fs:
            try:
                plain = _unpad(_aes_cbc(key, open(os.path.join(dp, f), "rb").read(4096)))
            except Exception:
                continue
            if _is_image(plain):
                return True
            break            # 只试第一个能读的文件，避免遍历
        break
    return False


def scan_key(account_dir: str) -> Optional[bytes]:
    """扫微信进程内存找 seed，派生并验证出 key。找不到返回 None。"""
    wxid = wxid_of(account_dir)
    sample = None
    for dp, _, fs in os.walk(persist_dir(account_dir)):
        for f in fs:
            try:
                sample = open(os.path.join(dp, f), "rb").read(16)
            except Exception:
                continue
            break
        if sample:
            break
    if not sample:
        return None

    pid = _main_pid()
    if not pid:
        return None
    h = kernel32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
    if not h:
        return None
    found = None
    try:
        for base, size in _regions(h):
            off = 0
            tail = b""
            while off < size and found is None:
                cur = min(CHUNK, size - off)
                chunk = _read_mem(h, base + off, cur)
                if chunk:
                    data = tail + chunk
                    for m in RE_SEED.finditer(data):
                        v = int(m.group(0))
                        if not (100_000_000 < v < 4_000_000_000):
                            continue
                        k = _derive(v, wxid)
                        try:
                            if _is_image(_aes_cbc(k, sample)):
                                found = (v, k)
                                break
                        except Exception:
                            continue
                    tail = data[-64:]
                else:
                    tail = b""
                off += cur
    finally:
        kernel32.CloseHandle(h)
    if found:
        cache = _load_key_cache()
        cache["seed"] = found[0]
        _save_key_cache(cache)
    return found[1] if found else None


# ---------- 解码 ----------

def fetch_cdn(url: str, save_dir: str, md5: str) -> Optional[str]:
    """从 cdnurl 下载表情原图并落盘。实测返回明文 PNG/JPEG/GIF。

    cdnurl 里带 filekey，可能过期；过期就返回 None，由调用方决定是否跳过。
    """
    if not url:
        return None
    url = url.replace("&amp;", "&").strip()
    if not url.lower().startswith("http"):
        return None
    os.makedirs(save_dir, exist_ok=True)
    stem = os.path.join(save_dir, md5.lower())
    for ext in (".jpg", ".gif", ".png"):
        if os.path.isfile(stem + ext):
            return stem + ext
    try:
        import urllib.request
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = resp.read()
    except Exception:
        return None
    if not data:
        return None
    if data[:4] == b"\x89PNG":
        out = stem + ".png"
    elif data[:4] == b"GIF8":
        out = stem + ".gif"
    elif data[:3] == b"\xff\xd8\xff":
        out = stem + ".jpg"
    else:
        return None
    try:
        with open(out, "wb") as f:
            f.write(data)
    except Exception:
        return None
    return out


def decode_sticker(account_dir: str, md5: str, key: bytes,
                   save_dir: str) -> Optional[str]:
    """把一张表情解密成可读图片（png/gif/jpg），返回路径；失败返回 None。"""
    path = find_sticker_file(account_dir, md5)
    if not path:
        return None
    os.makedirs(save_dir, exist_ok=True)
    stem = os.path.join(save_dir, md5.lower())
    for ext in (".jpg", ".gif", ".png"):
        if os.path.isfile(stem + ext):
            return stem + ext          # 之前解过，直接复用
    try:
        plain = _unpad(_aes_cbc(key, open(path, "rb").read()))
    except Exception:
        return None
    if plain[:4] == b"GIF8":
        out = stem + ".gif"
        open(out, "wb").write(plain)
        return out
    if plain[:4] == b"\x89PNG":
        out = stem + ".png"
        open(out, "wb").write(plain)
        return out
    if plain[:3] == b"\xff\xd8\xff":
        out = stem + ".jpg"
        open(out, "wb").write(plain)
        return out
    if plain[:4] == b"wxgf":
        jpg = _wxgf_to_jpg(plain)
        if jpg:
            out = stem + ".jpg"
            open(out, "wb").write(jpg)
            return out
    return None


def _wxgf_to_jpg(data: bytes) -> Optional[bytes]:
    """wxgf（HEVC 裸流）转首帧 jpg。

    必须显式 `-f hevc`：靠扩展名自动识别会误判成 mpeg4，报 header damaged
    （实测变体文件只有强制指定才能解）。
    """
    import shutil
    import subprocess
    import tempfile

    start = data.find(bytes((0, 0, 0, 1)))
    if start < 0:
        return None
    raw = data[start:]

    exe = shutil.which("ffmpeg")
    if not exe:
        try:
            import imageio_ffmpeg
            exe = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            exe = None

    if exe:
        for fmt in ("hevc", "h264"):
            try:
                with tempfile.TemporaryDirectory() as td:
                    src = os.path.join(td, "in.bin")
                    dst = os.path.join(td, "out.jpg")
                    with open(src, "wb") as f:
                        f.write(raw)
                    r = subprocess.run(
                        [exe, "-y", "-v", "error", "-f", fmt, "-i", src,
                         "-frames:v", "1", dst],
                        capture_output=True, timeout=30)
                    if r.returncode == 0 and os.path.exists(dst):
                        with open(dst, "rb") as f:
                            out = f.read()
                        if out[:3] == bytes((0xFF, 0xD8, 0xFF)):
                            return out
            except Exception:
                continue

    # 退路：库里的实现
    try:
        from wechatauto import MediaDownloader, WeChatDB
        md = getattr(_wxgf_to_jpg, "_md", None)
        if md is None:
            md = MediaDownloader(WeChatDB())
            _wxgf_to_jpg._md = md
        return md._wxgf_to_jpg(data)
    except Exception:
        return None


def vision_ready(path: str) -> str:
    """给视觉模型用的文件：GIF / 大图先取首帧压成小 jpg。

    1.8MB 的动图直接上传会被接口拒（体积太大），压成首帧不影响理解。
    """
    try:
        size = os.path.getsize(path)
        ext = os.path.splitext(path)[1].lower()
        if ext not in (".gif", ".png") and size <= 800 * 1024:
            return path
        cache = os.path.splitext(path)[0] + ".vision.jpg"
        if os.path.isfile(cache) and os.path.getsize(cache) > 0:
            return cache
        from PIL import Image
        im = Image.open(path)
        try:
            im.seek(0)
        except Exception:
            pass
        im = im.convert("RGB")
        im.thumbnail((512, 512))
        im.save(cache, "JPEG", quality=88)
        return cache
    except Exception:
        return path


# ---------- 描述缓存 ----------

def _load_desc() -> Dict[str, str]:
    try:
        with open(DESC_CACHE, "r", encoding="utf-8-sig") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _save_desc(d: Dict[str, str]) -> None:
    try:
        with open(DESC_CACHE, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


PROMPT = ("这是一个微信表情包。用一句不超过 15 字的话说清它表达的情绪或场景，"
          "不要客套、不要解释。例如：捂脸无语、狂喜、嘲讽鄙视。")


def describe_sticker(account_dir: str, md5: str, describe: Callable[[str, str], str],
                     log: Optional[Callable[[str], None]] = None,
                     cdn_url: Optional[str] = None) -> str:
    """md5 → 一句描述。带缓存：同一张永远只分析一次。"""
    md5 = (md5 or "").lower()
    if not md5:
        return ""
    cache = _load_desc()
    if md5 in cache:
        return cache[md5]
    key = _get_key_raw(account_dir)
    if not key:
        if log:
            log("表情：拿不到解密 key（微信没开？），跳过")
        return ""
    save_dir = os.path.join(HERE, "imgs", "stickers")
    img = decode_sticker(account_dir, md5, key, save_dir)
    if not img:
        # 本地没有 → 用消息 XML 里的 cdnurl 下载（明文图，无需解密）
        img = fetch_cdn(cdn_url, save_dir, md5) if cdn_url else None
        if not img:
            if log:
                log("表情：本地没有且 CDN 也拿不到 %s" % md5[:10])
            return ""
        if log:
            log("表情 %s：本地没有，已从 CDN 下载" % md5[:8])
    try:
        desc = (describe(vision_ready(img), PROMPT) or "").replace("\n", " ").strip()[:40]
    except Exception as e:
        if log:
            log("表情：视觉描述失败 %s" % e)
        return ""
    if desc:
        cache[md5] = desc
        _save_desc(cache)
        if log:
            log("表情 %s → %s" % (md5[:8], desc))
    return desc


# ---------- 给提示词用的一段话 ----------

def recent_stickers_block(account_dir: str, rows: List[dict],
                          nameof: Callable[[str], str],
                          describe: Callable[[str, str], str],
                          log: Optional[Callable[[str], None]] = None,
                          max_n: int = 3) -> str:
    """从消息行里挑出表情消息，拼成「谁发了个什么表情」。"""
    out = []
    seen = set()
    for r in rows:
        if r.get("type") != "动画表情":
            continue
        content = str(r.get("content") or "")
        m = re.search(r'md5\s*=\s*"([0-9a-fA-F]+)"', content)
        if not m:
            continue
        md5 = m.group(1).lower()
        if md5 in seen:
            continue
        seen.add(md5)
        u = re.search(r'cdnurl\s*=\s*"([^"]+)"', content)
        cdn_url = u.group(1) if u else None
        desc = describe_sticker(account_dir, md5, describe, log, cdn_url)
        if not desc:
            continue
        who = nameof(r.get("sender_username") or "") or "有人"
        out.append("【%s 发了个表情：%s】" % (who, desc))
        if len(out) >= max_n:
            break
    return "\n".join(out)
