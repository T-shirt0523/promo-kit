# -*- coding: utf-8 -*-
"""promo-kit 共享模块：文本读取、词库、定价引擎、通用工具。

词库策略：同机若存在 app-listing-kit，则复用其 banned_words.py，
避免两份极限词表长期漂移；找不到时回落到本文件内置的兜底表。
"""

import os
import re
import sys
import io
import json
import shutil
import subprocess

SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(SKILL_ROOT, "assets")
TEMPLATES = os.path.join(ASSETS, "poster_templates")

# app-listing-kit 同机共存时的复用路径
_SIBLING = os.path.abspath(os.path.join(SKILL_ROOT, "..", "app-listing-kit", "scripts"))


# ---------- 基础 IO ----------

_ENC_TRY = ("utf-8", "utf-8-sig", "gbk", "big5", "latin-1")


def read_text(path, limit=None):
    """按多种编码依次尝试读取，返回 (text, encoding)。

    项目源码编码不可控，直接 open(encoding='utf-8') 会在 GBK 文件上炸掉。
    """
    with open(path, "rb") as f:
        raw = f.read() if limit is None else f.read(limit)
    for enc in _ENC_TRY:
        try:
            return raw.decode(enc), enc
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="ignore"), "utf-8/ignore"


def write(path, text):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def write_json(path, obj):
    write(path, json.dumps(obj, ensure_ascii=False, indent=2))


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def as_list(v):
    if v is None:
        return []
    if isinstance(v, str):
        return [v]
    return list(v)


def title_weight(s):
    """平台标题权重：汉字 2、ASCII 1。"""
    return sum(2 if ord(c) > 127 else 1 for c in s)


def truncate(s, n):
    s = str(s or "")
    return s if len(s) <= n else s[: n - 1] + "…"


# 中文里在这些标点处断开是自然的，硬切到第 n 个字会出现「像素生…」这种半截词
_STRONG_BREAKS = "。！？!?"
_WEAK_BREAKS = "；，、：,;:"


def smart_cut(s, n, min_ratio=0.6):
    """优先在标点处断开；找不到合适断点才硬切加省略号。

    句号类断点保留标点（句子完整）；逗号类断点丢掉标点（不要以「，」结尾）。
    """
    s = str(s or "").strip()
    if len(s) <= n:
        return s
    window = s[:n]
    floor = int(n * min_ratio)
    strong = max((window.rfind(ch) for ch in _STRONG_BREAKS), default=-1)
    if strong + 1 >= floor:
        return window[:strong + 1].rstrip()
    weak = max((window.rfind(ch) for ch in _WEAK_BREAKS), default=-1)
    if weak >= floor:
        return window[:weak].rstrip()
    return window.rstrip() + "…"


def stable_slug(s):
    out = re.sub(r"[^0-9A-Za-z\u4e00-\u9fa5]+", "-", str(s or "project")).strip("-")
    return out or "project"


def human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024.0


# ---------- 词库：优先复用 app-listing-kit ----------

def _load_sibling_wordlist():
    if not os.path.isdir(_SIBLING):
        return None
    sys.path.insert(0, _SIBLING)
    try:
        import banned_words as bw  # noqa: WPS433
        if hasattr(bw, "scan") and hasattr(bw, "clean_for_scan"):
            return bw
    except Exception:
        pass
    finally:
        if _SIBLING in sys.path:
            sys.path.remove(_SIBLING)
    return None


# ---------- 宣传语境专属补充词表 ----------
# 说明：极限词/虚拟商品高危词/虚构数据/「XX风格」类 IP 模式由 app-listing-kit 词库覆盖。
# 下面这些是「推广获客」场景才高频出现的，平台词库里没有，必须单独盯。

PROMO_LIMIT = [
    "全宇宙", "世界第一", "全球唯一", "绝版", "颠覆行业", "重新定义",
    "吊打", "秒杀同类", "碾压", "无敌", "神作", "封神",
]

# 虚构稀缺：宣传里最爱写，但做不到就是虚假宣传
FAKE_SCARCITY_PATTERNS = [
    (r"限时\s*\d+\s*(小时|分钟|天)", "虚构限时"),
    (r"仅剩\s*\d+\s*(份|个|名额)?", "虚构库存"),
    (r"最后\s*\d+\s*(份|个|名额)", "虚构库存"),
    (r"前\s*\d+\s*(名|位|个)\s*(免费|半价|优惠)", "虚构名额（除非真会执行）"),
    (r"涨价在即", "虚构涨价紧迫感"),
    (r"倒计时\s*(抢购|结束)", "虚构紧迫感"),
    (r"今日必抢", "虚构紧迫感"),
    (r"错过\s*(再等|要等)\s*\d+", "虚构稀缺"),
]

# 诱导分享 / 诱导下载：微信生态重点打击
INDUCE_PATTERNS = [
    (r"转发\s*\d+\s*(个)?\s*群", "诱导分享（微信违规）"),
    (r"分享到\s*朋友圈\s*(即可|才能)", "诱导分享（微信违规）"),
    (r"集赞", "诱导分享（微信违规）"),
    (r"助力", "诱导分享（微信违规）"),
    (r"拉\s*\d+\s*人", "诱导拉新（微信违规）"),
    (r"扫码\s*(免费)?领", "诱导关注/领取"),
]

PROMO_SUGGESTIONS = {
    "限时": "（改为真实活动时间，或删掉时间限制）",
    "绝版": "（删除，虚拟商品不存在绝版）",
    "无敌": "（删除，无依据）",
    "神作": "（删除，主观夸大）",
    "封神": "（删除，主观夸大）",
    "颠覆行业": "（改为具体改变了什么）",
    "重新定义": "（改为具体做到了什么）",
}

# ---------- 第三方 IP 名表 ----------
# 宣传物料里出现他人作品名既是侵权风险，也是平台拒审的常见原因。
# 命中即高危，必须改名（改成描述效果本身，不引他人作品）。
#
# 本仓库**不内置任何真实作品名**（那本身就是分发他人 IP 名，不合适）。
# 名单走外置，三选一，按优先级：
#   1. 环境变量 PROMO_IP_WATCHLIST 指向的文本文件
#   2. assets/ip-watchlist.txt（自己填，已在 .gitignore 里）
#   3. 装了姊妹 skill `app-listing-kit` 时复用它的词库
# 仓库里带的是 assets/ip-watchlist.example.txt（只有格式说明），复制改名即可。

def _load_ip_watchlist():
    names = []
    env = os.environ.get("PROMO_IP_WATCHLIST")
    cands = [env] if env else []
    cands.append(os.path.join(SKILL_ROOT, "assets", "ip-watchlist.txt"))
    for p in cands:
        if not p or not os.path.isfile(p):
            continue
        try:
            with io.open(p, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    s = line.strip()
                    if s and not s.startswith("#"):
                        names.append(s)
        except OSError:
            pass
        if names:
            break
    # 去重保序
    seen, uniq = set(), []
    for n in names:
        if n not in seen:
            seen.add(n)
            uniq.append(n)
    return uniq


KNOWN_IP = _load_ip_watchlist()


def ip_check_enabled():
    """IP 这道闸到底通没通电。

    名单外置之后默认是空的。如果不把这个状态暴露出去，报告会打印
    「未发现第三方作品名」——那是「没查」，不是「查过没问题」，
    会让人误以为已经体检过。调用方据此改口径。
    """
    if KNOWN_IP:
        return True
    return _load_sibling_wordlist() is not None

# ---------- 统一扫描接口 ----------

class Wordlist(object):
    """包装可用的词库：优先 app-listing-kit，其次内置兜底。"""

    def __init__(self):
        self.sibling = _load_sibling_wordlist()
        self.source = "app-listing-kit/banned_words.py" if self.sibling else "promo-kit 内置兜底表"

    # --- 兼容接口 ---
    def clean_for_scan(self, text, path=""):
        if self.sibling:
            return self.sibling.clean_for_scan(text, path)
        return _strip_html(text) if str(path).lower().endswith((".html", ".htm")) else text

    def render_report(self, results, title="合规扫描报告"):
        if self.sibling:
            base = self.sibling.render_report(results)
            base = base.replace("# 风控扫描报告", f"# {title}", 1)
            # 姊妹 skill 面向「电商上架」，本 skill 面向「对外发布」，措辞统一一下
            base = base.replace("高危项必须改完再上架。警告项建议改。",
                                "高危项必须清零才能发布。警告项建议改。")
            return base
        return _fallback_report(results, title)

    def scan(self, text):
        """扫描一段文本。返回 [{level, word, kind, suggestion}]"""
        hits = []
        if self.sibling:
            hits.extend(self.sibling.scan(text))

        for w in PROMO_LIMIT:
            if w in text:
                hits.append({"level": "warn", "word": w, "kind": "宣传语境夸大词",
                             "suggestion": PROMO_SUGGESTIONS.get(w, "（改为可核实的具体描述）")})
        for pat, kind in FAKE_SCARCITY_PATTERNS:
            for m in re.finditer(pat, text):
                hits.append({"level": "high", "word": m.group(0), "kind": kind,
                             "suggestion": "（除非你确实会执行，否则删除）"})
        for pat, kind in INDUCE_PATTERNS:
            for m in re.finditer(pat, text):
                hits.append({"level": "high", "word": m.group(0), "kind": kind,
                             "suggestion": "（删除；改为一对一咨询，不做裂变诱导）"})
        for name in KNOWN_IP:
            if name in text:
                hits.append({"level": "high", "word": name, "kind": "第三方作品名/IP",
                             "suggestion": "（必须删除；只描述效果本身，不引他人作品）"})

        # 去重
        seen, uniq = set(), []
        for h in hits:
            key = (h["level"], h["word"], h["kind"])
            if key not in seen:
                seen.add(key)
                uniq.append(h)
        return uniq


_BLOCK = re.compile(r"<(style|script)\b[^>]*>.*?</\1\s*>", re.I | re.S)
_TAG = re.compile(r"<[^>]+>")


def _strip_html(text):
    return _TAG.sub(" ", _BLOCK.sub(" ", text))


def _fallback_report(results, title):
    if not results:
        return f"# {title}\n\n未发现违规词。可以交付。\n"
    high = sum(1 for hs in results.values() for h in hs if h["level"] == "high")
    warn = sum(1 for hs in results.values() for h in hs if h["level"] == "warn")
    lines = [f"# {title}", "", f"**高危 {high} 处 · 需修改 {warn} 处**", ""]
    for path, hits in results.items():
        lines += [f"## {os.path.basename(path)}", "", "| 级别 | 命中 | 类别 | 建议 |", "|---|---|---|---|"]
        for h in hits:
            flag = "🔴 高危" if h["level"] == "high" else "🟡 警告"
            lines.append(f"| {flag} | `{h['word']}` | {h['kind']} | {h['suggestion']} |")
        lines.append("")
    return "\n".join(lines)


WORDLIST = Wordlist()


# ---------- 定价引擎 ----------

# 品类基准区间（元）。虚拟商品，无物流成本。
CATEGORY_BASE = {
    "game":     (6, 19, 49),
    "tool":     (3, 9, 25),
    "desktop":  (9, 29, 69),
    "webapp":   (9, 39, 99),
    "template": (9, 39, 99),
    "course":   (5, 19, 59),
    "unknown":  (9, 29, 69),
}

# 心理价位锚点：优先吸附到这些数，避开 13.7 这种随手价
PRICE_POINTS = [3, 5, 6, 8, 9, 12, 15, 19, 25, 29, 39, 49, 59, 69, 89,
                99, 129, 159, 199, 249, 299, 399, 499, 699, 999]

PRICE_FLOOR = 6          # 低于此价不做：低价吸引的是退款率与差评率最高的人群
SUPPORTER_RATIO = 2.2    # 锚定档倍率
ENTRY_RATIO = 0.6        # 入门档倍率


def classify(facts):
    """按项目事实归类到 CATEGORY_BASE 的 key。"""
    text = " ".join([
        str(facts.get("project_type", "")),
        str(facts.get("category", "")),
        str(facts.get("one_liner", "")),
        " ".join(as_list(facts.get("detected_stacks"))),
        " ".join(as_list(facts.get("entry_points"))),
    ]).lower()

    if any(k in text for k in ("game", "游戏", "raylib", "pygame", "three.js", "phaser")):
        return "game"
    if any(k in text for k in ("模板", "素材", "theme", "template", "preset", "素材包")):
        return "template"
    if any(k in text for k in ("教程", "课程", "course", " ebook", "文档", "指南")):
        return "course"
    if any(k in text for k in ("web", "网站", "网页", "saas", "chrome extension", "浏览器插件")):
        return "webapp"
    if any(k in text for k in ("cli", "命令行", "script", "脚本", "工具", "tool", "库", "library")):
        return "tool"
    if any(k in text for k in ("desktop", "桌面", "electron", "wpf", "winform", "qt", "gtk")):
        return "desktop"
    return "unknown"


def volume_multiplier(facts):
    """按代码体量给倍率。loc 是估算工作量最朴素的代理指标。"""
    loc = int(facts.get("loc", 0) or 0)
    files = int(facts.get("code_files", 0) or 0)
    mb = float(facts.get("asset_mb", 0) or 0)

    if loc < 1200 and files < 12:
        tier, mult = "S", 0.75
    elif loc < 6000:
        tier, mult = "M", 1.0
    elif loc < 20000:
        tier, mult = "L", 1.35
    else:
        tier, mult = "XL", 1.8

    # 素材体量单独加分：10MB 以上说明有美术/音频工作量
    if mb >= 100:
        mult += 0.25
    elif mb >= 10:
        mult += 0.12
    return tier, round(mult, 3)


def completeness_bonus(facts):
    """完成度加分与扣分项，来自可核对的事实。"""
    bonus, notes = 0.0, []
    if facts.get("has_readme"):
        bonus += 0.05
    if as_list(facts.get("screenshots")):
        bonus += 0.10
        notes.append("有真实截图（宣传素材可直接用）")
    else:
        bonus += -0.10
        notes.append("**无截图** —— 宣传图缺主力素材，建议先跑起来截图")
    if as_list(facts.get("features")):
        n = len(as_list(facts.get("features")))
        bonus += min(0.12, 0.03 * n)
    if facts.get("has_audio"):
        bonus += 0.03
    if int(facts.get("loc", 0) or 0) < 400:
        notes.append("代码量偏小，定价不宜高于同品类中位")
    return round(bonus, 3), notes


def snap_price(x, floor=3.0):
    """吸附到心理价位点，avoid 13.7 这种随手价。

    floor 默认 3 元而非 PRICE_FLOOR —— 地板价只约束「标准版」，
    入门档需要能落到标准版下方，否则两档会塌成同一个价格。
    """
    x = max(float(floor), float(x))
    if x >= PRICE_POINTS[-1]:
        return float(PRICE_POINTS[-1])
    lo = float(floor)
    for p in PRICE_POINTS:
        if p >= x:
            # 就近取：x 更靠近 lo 就取 lo
            return float(p if (p - x) <= (x - lo) else lo)
        lo = float(p)
    return lo


def build_price_plan(facts, platform_cut=None):
    """生成三档定价方案。返回 dict，含推导过程（可核对，非拍脑袋）。"""
    cat = classify(facts)
    lo, mid, hi = CATEGORY_BASE[cat]
    tier, vol = volume_multiplier(facts)
    bonus, notes = completeness_bonus(facts)

    raw_std = mid * vol + mid * bonus * 1.5
    std = snap_price(raw_std, floor=PRICE_FLOOR)
    std = max(PRICE_FLOOR, std)

    # 入门档：吸附后若塌到标准版同价，则退到标准版下方最大的一档
    entry = snap_price(std * ENTRY_RATIO)
    if entry >= std:
        smaller = [p for p in PRICE_POINTS if p < std]
        entry = float(smaller[-1]) if smaller else 3.0

    supporter = snap_price(std * SUPPORTER_RATIO)
    if supporter <= std:
        supporter = snap_price(max(std * 1.6, std + 5))

    return {
        "category_key": cat,
        "category_range": {"low": lo, "mid": mid, "high": hi},
        "volume_tier": tier,
        "volume_multiplier": vol,
        "completeness_bonus": bonus,
        "completeness_notes": notes,
        "raw_standard": round(raw_std, 2),
        "tiers": [
            {"name": "轻量版", "price": entry, "role": "降低决策门槛",
             "includes": facts.get("entry_includes") or "核心功能，不含后续更新"},
            {"name": "标准版", "price": std, "role": "主推（承担大部分销量）",
             "includes": facts.get("standard_includes") or "完整功能 + 后续小版本更新"},
            {"name": "支持者版", "price": supporter, "role": "锚定档，让标准版显得划算",
             "includes": facts.get("supporter_includes") or "标准版全部内容 + 可商用授权 / 全部更新"},
        ],
        "platform_cut": platform_cut or [],
    }


# --------------------------------------------------------------------------
# 无头浏览器（海报渲染 + 联络表 + 落地页截图共用一份，避免多处漂移）
# --------------------------------------------------------------------------
BROWSERS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe"),
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/microsoft-edge",
]


def find_browser():
    hit = find_tool("browser", BROWSERS, None)
    if hit:
        return hit
    for pat in ("msedge", "chrome", "chromium", "google-chrome"):
        hit = shutil.which(pat)
        if hit:
            return hit
    return None


def png_size(path):
    try:
        with open(path, "rb") as f:
            head = f.read(24)
        if head[:8] != b"\x89PNG\r\n\x1a\n":
            return (0, 0)
        return (int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big"))
    except Exception:
        return (0, 0)


def browser_shot(browser, html_path, out_png, w, h, ud_dir, budget_ms=8000, timeout=120):
    """把一个本地 HTML 渲染成精确 w×h 的 PNG。返回 (ok, message)。

    每次都换 user-data-dir：复用会让上一次的窗口尺寸/缓存影响这一次（已踩过）。
    """
    url = "file:///" + os.path.abspath(html_path).replace("\\", "/")
    os.makedirs(ud_dir, exist_ok=True)
    if os.path.exists(out_png):
        try:
            os.remove(out_png)
        except OSError:
            pass
    cmd = [
        browser, "--headless=new", "--disable-gpu", "--hide-scrollbars",
        "--no-first-run", "--no-default-browser-check", "--disable-extensions",
        "--disable-background-networking",
        "--user-data-dir=" + ud_dir,
        "--force-device-scale-factor=1",
        "--window-size=%d,%d" % (w, h),
        "--virtual-time-budget=%d" % budget_ms,
        "--screenshot=" + os.path.abspath(out_png),
        url,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, "超时（>%ds）" % timeout
    except OSError as exc:
        return False, "启动浏览器失败：%s" % exc
    if not os.path.isfile(out_png):
        err = (proc.stderr or b"").decode("utf-8", "ignore").strip().splitlines()
        tail = err[-1] if err else "无 stderr 输出"
        return False, "未产出文件（%s）" % tail[:120]
    gw, gh = png_size(out_png)
    if (gw, gh) != (w, h):
        return False, "尺寸不符：期望 %dx%d，实际 %dx%d" % (w, h, gw, gh)
    return True, "%dx%d · %d KB" % (gw, gh, os.path.getsize(out_png) // 1024)


# --------------------------------------------------------------------------
# 外部工具定位
#
# 本 skill 不自带 ffmpeg / 浏览器，也不假设它们装在哪。定位顺序统一为：
#   1. 环境变量            PROMO_FFMPEG / PROMO_BROWSER / PROMO_GXX / PROMO_RAYLIB
#   2. 本地覆盖文件        scripts/local-paths.json（已在 .gitignore 里）
#   3. PATH                shutil.which
#   4. 各平台常见安装位置
# 这样仓库里不会写死任何一台机器的私有路径。
# --------------------------------------------------------------------------
LOCAL_PATHS_FILE = "local-paths.json"
_ENV_KEYS = {"ffmpeg": "PROMO_FFMPEG", "browser": "PROMO_BROWSER",
             "gxx": "PROMO_GXX", "raylib": "PROMO_RAYLIB"}


def _local_paths():
    """读 scripts/local-paths.json。文件不存在/损坏都返回 {}，不影响主流程。"""
    p = os.path.join(SKILL_ROOT, "scripts", LOCAL_PATHS_FILE)
    if not os.path.isfile(p):
        return {}
    try:
        with io.open(p, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def find_tool(kind, extra_candidates=(), which_name=None):
    """按「环境变量 → local-paths.json → PATH → 常见位置」找一个可执行文件。"""
    env = os.environ.get(_ENV_KEYS.get(kind, ""))
    if env and os.path.isfile(env):
        return env
    loc = _local_paths().get(kind)
    if loc and os.path.isfile(loc):
        return loc
    hit = shutil.which(which_name or kind)
    if hit:
        return hit
    for p in extra_candidates:
        if p and os.path.isfile(p):
            return p
    return None


def _ffmpeg_common_paths():
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    out = [
        os.path.join(pf, "ffmpeg", "bin", "ffmpeg.exe"),
        os.path.join(pf86, "ffmpeg", "bin", "ffmpeg.exe"),
        r"C:\ffmpeg\bin\ffmpeg.exe",
        r"D:\ffmpeg\bin\ffmpeg.exe",
    ]
    if os.name != "nt":
        out += ["/usr/local/bin/ffmpeg", "/opt/homebrew/bin/ffmpeg", "/usr/bin/ffmpeg"]
    return out


def find_ffmpeg():
    return find_tool("ffmpeg", _ffmpeg_common_paths(), "ffmpeg")


def ffmpeg_caps(ff):
    """探测这份 ffmpeg 的能力。缓存到 facts 里，避免每次重复探测。"""
    def _grab(args):
        try:
            r = subprocess.run([ff] + args, capture_output=True, timeout=60)
            return (r.stdout or b"").decode("utf-8", "ignore")
        except Exception:
            return ""

    enc = _grab(["-hide_banner", "-encoders"])
    dev = _grab(["-hide_banner", "-devices"])
    dem = _grab(["-hide_banner", "-demuxers"])
    flt = _grab(["-hide_banner", "-filters"])
    return {
        "h264": any(k in enc for k in ("libx264", "h264_mf", "libo264rt")),
        "png": (" png " in enc) or ("png_pipe" in dem and False),
        "bmp_in": "bmp" in dem,
        "gdigrab": "gdigrab" in dev,
        "overlay": " overlay " in flt,
        "drawtext": " drawtext " in flt,
    }
