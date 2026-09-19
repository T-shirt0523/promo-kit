# -*- coding: utf-8 -*-
"""扫描一个项目目录（或 zip），产出一份可核对的事实清单 facts.json。

设计原则：只写「扫出来的东西」，不猜测、不美化。
扫不到的字段留在 missing_fields 里，交由主 Agent 向用户追问，绝不填占位符糊过去。

用法:
    python scan_project.py --path ./my-project --out facts.json
    python scan_project.py --path ./my-project.zip --name "像素荒野" --out facts.json

退出码: 0 = 正常, 1 = 扫出阻断项（第三方 IP / 外部素材风险）, 3 = 用法或路径错误
"""

import argparse
import json
import os
import re
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import promo_common as pc  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# ---------- 目录与文件分类 ----------

IGNORE_DIRS = {
    ".git", ".svn", ".hg", "node_modules", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", "dist", "build", "out", "output", "target",
    "obj", "bin", "release", "debug", ".venv", "venv", "env", ".env",
    ".idea", ".vscode", ".vs", "coverage", "htmlcov", ".next", ".nuxt",
    "vendor", "third_party", "thirdparty", "tmp", "temp", ".workbuddy",
    ".cache", "generated-images", "generated-videos", "promo-out", ".promo-work",
}

CODE_EXT = {
    ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".py", ".c", ".cc", ".cpp",
    ".cxx", ".h", ".hpp", ".hxx", ".cs", ".java", ".kt", ".kts", ".rs", ".go",
    ".rb", ".php", ".swift", ".lua", ".gd", ".dart", ".zig", ".sh", ".bat",
    ".ps1", ".html", ".htm", ".css", ".scss", ".less", ".vue", ".svelte",
    ".glsl", ".frag", ".vert", ".hlsl", ".sql", ".r", ".m", ".mm", ".ex", ".exs",
}

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
AUDIO_EXT = {".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac"}
VIDEO_EXT = {".mp4", ".webm", ".mov", ".avi"}
FONT_EXT = {".ttf", ".otf", ".woff", ".woff2"}

# 这些路径/文件名下的图不是「游戏画面截图」，是资源图，进不了宣传图
ASSET_PATH_HINT = (
    "sprite", "tileset", "tile_", "texture", "icon", "logo", "favicon",
    "avatar", "badge", "cursor", "ui/", "\\ui\\", "atlas", "font", "glyph",
    "particle", "shader", "noise", "normal_map", "shadow",
)

README_NAMES = [
    "readme.md", "readme.mdx", "readme.txt", "readme.rst", "readme",
    "readme_cn.md", "readme.zh.md", "说明.md", "自述.md", "项目说明.md",
]

# 依赖清单：扩展名不在 CODE_EXT 里，但技术栈全靠它们识别，必须单独读
MANIFEST_NAMES = {
    "package.json", "package-lock.json", "requirements.txt", "pipfile",
    "cargo.toml", "pyproject.toml", "setup.py", "setup.cfg", "pom.xml",
    "build.gradle", "build.gradle.kts", "go.mod", "composer.json", "gemfile",
    "cmakelists.txt", "makefile", "dockerfile", "manifest.json",
    "project.godot", "info.plist", "packages.config", "vcpkg.json",
    "conanfile.txt", "project.json", "tsconfig.json", "vite.config.js",
    "vite.config.ts", "webpack.config.js", "rollup.config.js", "nuxt.config.ts",
}

LICENSE_NAMES = ["license", "license.md", "license.txt", "licence", "copying"]

# ---------- 技术栈识别 ----------

STACK_HINTS = {
    "three.js": ("three", "threejs", "three.js"),
    "react": ("react", "react-dom"),
    "vue": ("vue",),
    "svelte": ("svelte",),
    "electron": ("electron",),
    "tauri": ("tauri",),
    "vite": ("vite",),
    "webpack": ("webpack",),
    "tailwind": ("tailwind",),
    "cannon-es": ("cannon-es", "cannon"),
    "gsap": ("gsap",),
    "matter.js": ("matter-js",),
    "pixi.js": ("pixi.js", "@pixi"),
    "phaser": ("phaser",),
    "raylib": ("raylib",),
    "pygame": ("pygame",),
    "arcade": ("arcade",),
    "SDL2": ("sdl2", "sdl"),
    "SFML": ("sfml",),
    "OpenGL": ("opengl", "glfw", "glew", "glad"),
    "Vulkan": ("vulkan",),
    "DirectX": ("d3d11", "d3d12", "directx", "dxgi"),
    "Unity": ("unityengine", "unity"),
    "Godot": ("godot",),
    "MonoGame": ("monogame", "xna"),
    "Qt": ("qt5", "qt6", "pyside", "pyqt", "qtcore"),
    "Tkinter": ("tkinter",),
    "WinForms": ("winforms", "windows.forms"),
    "WPF": ("presentationframework", "wpf"),
    "Flask": ("flask",),
    "Django": ("django",),
    "FastAPI": ("fastapi",),
    "Express": ("express",),
    "Pillow": ("pillow", "pil"),
    "NumPy": ("numpy",),
    "OpenCV": ("opencv",),
    "Dear ImGui": ("imgui",),
}

AI_HINTS = ("openai", "anthropic", "claude", "gpt-", "chat/completions",
            "api_key", "apikey", "base_url", "baseurl", "llm", "大模型",
            "azure.openai", "dashscope", "deepseek", "gemini")

# 外部素材风险：出现这些词说明素材可能不是自有。
# 注意：「素材来源」单独出现不算风险 —— README 里写「素材来源：全部程序化生成」
# 恰恰是安全声明。只有指向外部站点时才是风险（已踩过误报）。
EXTERNAL_ASSET_HINTS = (
    "素材下载", "素材提取", "提取自", "解包", "素材网", "爱给网",
    "opengameart", "texture pack", "non-commercial", "非商用",
    "仅限个人学习", "请勿商用", "仅供学习交流", "网络收集",
)

# 指向外部站点的「素材来源」
EXTERNAL_SOURCE_PAT = re.compile(
    r"素材来源\s*[：:]\s*(https?://|\S*(?:网|站|论坛|贴吧|盘)\S*)"
)


# ---------- 工具 ----------

def image_size(path):
    """不依赖 PIL 读图片尺寸。失败返回 (0, 0)。"""
    try:
        with open(path, "rb") as f:
            head = f.read(32)
            if head[:8] == b"\x89PNG\r\n\x1a\n":
                return (int.from_bytes(head[16:20], "big"),
                        int.from_bytes(head[20:24], "big"))
            if head[:3] == b"GIF":
                return (int.from_bytes(head[6:8], "little"),
                        int.from_bytes(head[8:10], "little"))
            if head[:2] == b"\xff\xd8":  # JPEG：扫 SOF 标记
                f.seek(2)
                while True:
                    b = f.read(1)
                    if not b:
                        return (0, 0)
                    if b != b"\xff":
                        continue
                    marker = f.read(1)
                    while marker == b"\xff":
                        marker = f.read(1)
                    if marker in (b"\xc0", b"\xc1", b"\xc2", b"\xc3",
                                  b"\xc5", b"\xc6", b"\xc7", b"\xc9",
                                  b"\xca", b"\xcb", b"\xcd", b"\xce", b"\xcf"):
                        f.read(3)
                        h = int.from_bytes(f.read(2), "big")
                        w = int.from_bytes(f.read(2), "big")
                        return (w, h)
                    seg = f.read(2)
                    if len(seg) < 2:
                        return (0, 0)
                    f.seek(int.from_bytes(seg, "big") - 2, 1)
            if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
                if head[12:16] == b"VP8X":
                    w = int.from_bytes(head[24:27], "little") + 1
                    h = int.from_bytes(head[27:30], "little") + 1
                    return (w, h)
    except Exception:
        pass
    return (0, 0)


def walk_project(root):
    """遍历项目文件，跳过忽略目录。返回 [(abspath, relpath, size)]"""
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d.lower() not in IGNORE_DIRS and not d.startswith(".")]
        for name in filenames:
            ap = os.path.join(dirpath, name)
            try:
                size = os.path.getsize(ap)
            except OSError:
                continue
            found.append((ap, os.path.relpath(ap, root), size))
    return found


def safe_extract(zip_path, dest):
    """解压 zip，防 zip slip 与绝对路径穿越。"""
    os.makedirs(dest, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            name = info.filename.replace("\\", "/")
            if name.startswith("/") or ".." in name.split("/"):
                continue
            target = os.path.join(dest, name)
            if not os.path.abspath(target).startswith(os.path.abspath(dest)):
                continue
            if info.is_dir():
                os.makedirs(target, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as dst:
                dst.write(src.read())


# ---------- 各字段探测 ----------

def find_name(root, files, forced=None):
    if forced:
        return forced, "命令行指定"
    for ap, rel, _s in files:
        base = os.path.basename(rel).lower()
        if base == "package.json":
            try:
                pkg = pc.load_json(ap)
                for k in ("displayName", "productName", "name"):
                    if pkg.get(k):
                        return str(pkg[k]), f"package.json 的 {k}"
            except Exception:
                pass
        if base == "cargo.toml":
            t, _ = pc.read_text(ap, 20000)
            m = re.search(r'^\s*name\s*=\s*"([^"]+)"', t, re.M)
            if m:
                return m.group(1), "Cargo.toml"
        if base in ("pyproject.toml", "setup.py"):
            t, _ = pc.read_text(ap, 20000)
            m = re.search(r'^\s*name\s*=\s*"([^"]+)"', t, re.M)
            if m:
                return m.group(1), base
        if base.endswith(".csproj"):
            t, _ = pc.read_text(ap, 20000)
            m = re.search(r"<AssemblyName>([^<]+)</AssemblyName>", t)
            if m:
                return m.group(1), base
            return os.path.splitext(os.path.basename(rel))[0], base
        if base == "cmakelists.txt":
            t, _ = pc.read_text(ap, 20000)
            m = re.search(r"project\s*\(\s*([^\s)]+)", t, re.I)
            if m:
                return m.group(1), "CMakeLists.txt"
    for ap, rel, _s in files:
        if os.path.basename(rel).lower() in ("index.html", "main.html"):
            t, _ = pc.read_text(ap, 200000)
            m = re.search(r"<title>\s*([^<]+?)\s*</title>", t, re.I | re.S)
            if m and m.group(1).strip():
                return m.group(1).strip(), "index.html 的 <title>"
    return os.path.basename(os.path.abspath(root)), "目录名（兜底）"


def read_readme(files):
    for ap, rel, size in files:
        base = os.path.basename(rel).lower()
        if base in README_NAMES and "/" not in rel.replace("\\", "/"):
            t, enc = pc.read_text(ap, 400000)
            return t, rel, enc
    return None, None, None


# 卖点候选排序：README 的列表项里既有「差异化能力」也有「玩法叙述」，
# 后者当卖点用会显得没信息量。按「像不像一条可核对的差异点」打分排序。
DIFF_KEYS = ("零", "无外部", "不含", "无需", "不用", "免安装", "离线", "本地",
             "自动", "支持", "可自定义", "可重映射", "一键", "程序化", "生成",
             "体积", "字节", "开源", "自研", "独立", "免费")
VERB_HEAD = ("在", "用", "把", "通过", "每局", "进入", "打开", "点击", "按住")


def feature_score(item):
    score = 0
    if any(k in item for k in DIFF_KEYS):
        score += 3
    if re.search(r"\d", item):
        score += 2
    if any(u in item for u in ("MB", "KB", "GB", "秒", "帧", "%", "×", "倍")):
        score += 2
    if re.match(r"^(" + "|".join(VERB_HEAD) + ")", item):
        score -= 2
    if len(item) > 42:
        score -= 1
    return score


def parse_readme(text):
    """从 README 提取一句话定位与卖点候选。只做提取，不做润色。"""
    if not text:
        return None, [], None
    lines = text.splitlines()
    title, one_liner, features = None, None, []
    section_hits = False
    feat_kw = ("功能", "特性", "特色", "玩法", "亮点", "卖点", "feature", "highlight")

    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if title is None and line.startswith("#"):
            title = re.sub(r"^#+\s*", "", line).strip()
            continue
        # 第一个非标题、非徽章、非引用、非列表的段落 = 一句话简介
        if one_liner is None:
            if not line.startswith(("#", ">", "|", "!", "[", "-", "*", "<", "<!--", "```")):
                if not re.match(r"^[-=*_]{3,}$", line):
                    one_liner = pc.truncate(re.sub(r"[`*_\[\]()]", "", line), 200)
                    continue
        # 收集列表项
        if re.match(r"^([-*+]|\d+[.)])\s+", line):
            item = re.sub(r"^([-*+]|\d+[.)])\s+", "", line)
            item = re.sub(r"^\s*\[[ xX]\]\s*", "", item)          # 去 checkbox
            item = re.sub(r"[`*_]", "", item).strip()
            item = re.sub(r"^[\u2705\u274c\u2b50\U0001f300-\U0001faff]\s*", "", item)
            if 4 <= len(item) <= 80:
                if section_hits or any(k in item.lower() for k in
                                       ("无需", "不需要", "支持", "可", "零", "自动", "离线", "本地")):
                    features.append(item)
        if line.startswith("#") and any(k in line.lower() for k in feat_kw):
            section_hits = True
        elif line.startswith("#"):
            section_hits = False

    # 去重保序，再按「差异点成色」排序（sorted 是稳定排序，同分保持原顺序）
    seen, uniq = set(), []
    for f in features:
        if f not in seen:
            seen.add(f)
            uniq.append(f)
    uniq.sort(key=feature_score, reverse=True)
    return one_liner, uniq[:10], title


def detect_entry_points(files):
    names = ("index.html", "main.py", "app.py", "main.cpp", "main.c", "main.cs",
             "main.js", "app.js", "server.js", "main.go", "main.rs", "main.java",
             "run.bat", "start.bat", "启动.bat")
    out = []
    for ap, rel, _s in files:
        base = os.path.basename(rel).lower()
        if base in names or base.endswith(".exe"):
            out.append(rel.replace("\\", "/"))
    return sorted(set(out))[:12]


def detect_platforms(hints, ptype):
    """返回 (平台列表, 是否为默认假设值)。"""
    t = hints.lower()
    plats = []
    if any(k in t for k in ("windows", ".exe", "win32", "winforms", "wpf",
                            "d3d", "directx", "msvc", "winmm", "win64")):
        plats.append("Windows")
    if any(k in t for k in ("macos", "mac os", "darwin", "cocoa", "appkit")):
        plats.append("macOS")
    if any(k in t for k in ("linux", "gtk", "x11", "wayland", "ubuntu")):
        plats.append("Linux")
    if any(k in t for k in ("android", "ios", "flutter", "react native",
                            "uniapp", "小程序")):
        plats.append("移动端")
    assumed = False
    if not plats:
        if ptype == "webapp":
            plats = ["任意系统（浏览器）"]
        else:
            plats = ["Windows"]
            assumed = True
    return plats, assumed


# ---------- 主扫描 ----------

def scan(root, forced_name=None):
    files = walk_project(root)
    if not files:
        raise SystemExit(f"目录里没有可扫描的文件：{root}")

    loc_by_ext, code_files = {}, 0
    asset_bytes, audio_bytes, video_bytes = 0, 0, 0
    total_bytes = 0
    screenshots, fonts, texts = [], [], []
    stack_text_parts = []
    ai_hits, ext_asset_hits, ip_hits = set(), [], []
    ip_sources = {}

    for ap, rel, size in files:
        total_bytes += size
        ext = os.path.splitext(rel)[1].lower()
        low_rel = rel.lower().replace("/", "\\")

        if ext in CODE_EXT:
            code_files += 1
            if ext in (".html", ".htm", ".css", ".scss", ".less"):
                pass
            try:
                t, _enc = pc.read_text(ap, 1024 * 1024)
                loc_by_ext[ext] = loc_by_ext.get(ext, 0) + t.count("\n") + 1
                if size < 400000:
                    texts.append((rel, t))
                    stack_text_parts.append(t[:6000].lower())
            except Exception:
                pass

        if ext in IMAGE_EXT:
            asset_bytes += size
            w, h = image_size(ap)
            is_asset = any(k in low_rel for k in ASSET_PATH_HINT)
            # 判定用「尺寸」而非「体积」：程序化生成/扁平色系的画面 PNG 压缩率极高，
            # 一张 1280x720 的游戏截图可能只有 20 KB，按体积卡阈值必然漏判（已踩过）。
            if (not is_asset) and w * h >= 640 * 360 and size >= 8000:
                screenshots.append({"path": rel.replace("\\", "/"), "w": w, "h": h,
                                    "kb": round(size / 1024.0, 1)})
        elif ext in AUDIO_EXT:
            audio_bytes += size
            asset_bytes += size
        elif ext in VIDEO_EXT:
            video_bytes += size
            asset_bytes += size
        elif ext in FONT_EXT:
            asset_bytes += size
            fonts.append(rel.replace("\\", "/"))

        if ext in (".md", ".txt"):
            try:
                t, _e = pc.read_text(ap, 400000)
                texts.append((rel, t))
            except Exception:
                pass

        # 依赖清单：扩展名不在 CODE_EXT 内，但技术栈全靠它们识别
        if os.path.basename(rel).lower() in MANIFEST_NAMES:
            try:
                t, _e = pc.read_text(ap, 400000)
                stack_text_parts.append(t[:20000].lower())
            except Exception:
                pass

    # 关键词命中
    stack_blob = " ".join(stack_text_parts)
    for stack, keys in STACK_HINTS.items():
        if any(k in stack_blob for k in keys):
            stack_text_parts.append(stack.lower())

    all_text_blob = "\n".join(t for _r, t in texts)
    lower_blob = all_text_blob.lower()
    for k in AI_HINTS:
        if k in lower_blob:
            ai_hits.add(k)
    for k in EXTERNAL_ASSET_HINTS:
        if k in all_text_blob:
            ext_asset_hits.append(k)
    m = EXTERNAL_SOURCE_PAT.search(all_text_blob)
    if m:
        ext_asset_hits.append(m.group(0)[:40])
    for name in pc.KNOWN_IP:
        if name in all_text_blob:
            owner = next((r for r, t in texts if name in t), "?")
            ip_hits.append(name)
            ip_sources[name] = owner

    detected_stacks = sorted({s for s in STACK_HINTS if s.lower() in stack_text_parts})

    # README
    readme_text, readme_rel, readme_enc = read_readme(files)
    one_liner, readme_feats, readme_title = parse_readme(readme_text)

    name, name_src = find_name(root, files, forced_name)
    entry_points = detect_entry_points(files)
    loc = sum(loc_by_ext.values())

    # 类型判定：文件名本身就是强信号（game.js / player.cpp 之类），
    # 之前只看依赖名导致「用了 three.js 的游戏」被判成工具，已修正。
    rel_blob = " ".join(r.lower() for _a, r, _s in files)
    hints = " ".join([
        name, " ".join(entry_points), " ".join(detected_stacks),
        " ".join(str(k) for k in loc_by_ext), rel_blob,
    ])
    platform_hints = hints + " " + (readme_text or "")[:3000]

    stacks_low = [s.lower() for s in detected_stacks]
    GAME_STACKS = ("raylib", "pygame", "phaser", "godot", "unity", "monogame",
                   "sfml", "pixi.js", "arcade", "love2d", "bevy")
    GAME_WORDS = ("game", "游戏", "生存", "冒险", "闯关", "打怪", "rogue",
                  "shooter", "rpg", "puzzle", "player", "enemy", "boss",
                  "level", "world", "sprite", "tilemap", "pixel")
    is_game = (any(s in stacks_low for s in GAME_STACKS)
               or any(w in hints.lower() for w in GAME_WORDS))

    is_desktop_stack = any(s in stacks_low for s in
                           ("electron", "tauri", "qt", "wpf", "winforms",
                            "dear imgui", "tkinter"))
    has_web_entry = any(e.lower().endswith((".html", ".htm")) for e in entry_points)

    if is_game:
        ptype = "game"
    elif is_desktop_stack:
        ptype = "desktop"
    elif any(s in stacks_low for s in ("react", "vue", "svelte")) or has_web_entry:
        ptype = "webapp"
    elif code_files and loc < 4000:
        ptype = "tool"
    else:
        ptype = "unknown"

    platforms, platforms_assumed = detect_platforms(platform_hints, ptype)

    # 阻断与警告
    blockers, warnings = [], []
    if ip_hits:
        pairs = "、".join(f"{n}（见 {ip_sources.get(n, '?')}）" for n in ip_hits[:10])
        blockers.append(f"扫到第三方作品名：{pairs} —— 交付物里绝对不能出现，先改名再宣传")
    if ext_asset_hits:
        warnings.append("疑似外部素材来源术语：" + "、".join(sorted(set(ext_asset_hits)))
                        + "。必须确认素材授权范围含商用，否则不能卖")
    if ai_hits:
        warnings.append("检测到 AI 相关依赖或密钥字段（" + "、".join(sorted(ai_hits)[:6])
                        + "）。若产品自带 AI 服务端转发，需算法备案，个人办不了；"
                          "必须改成买家自带 Key（BYOK）")
    if not screenshots:
        warnings.append("没有可用作宣传主图的截图。AI 生成的画面不能代替实机截图，"
                        "建议先运行项目截 6–10 张（含 1 张标题界面、1 张核心玩法、1 张设置/存档界面）")
    if not readme_text:
        warnings.append("没有 README，一句话定位与卖点无法自动提取，需向用户追问")

    missing = []
    if platforms_assumed:
        warnings.append(f"项目里没有明确的运行环境线索，平台暂按 {platforms[0]} 处理 —— "
                        "这是假设值，交付前请确认")
    if not readme_text:
        missing.append("one_liner / features（无 README）")
    if not one_liner:
        missing.append("one_liner（README 里没有可提取的简介段）")
    for f in ("audience", "delivery", "seller_name", "seller"):
        missing.append(f)

    facts = {
        "project_name": name,
        "project_name_source": name_src,
        "readme_title": readme_title,
        "project_type": ptype,
        "one_liner": one_liner,
        "features": readme_feats,
        "git_features_are_draft": True,
        "detected_stacks": detected_stacks,
        "entry_points": entry_points,
        "platforms": platforms,
        "platforms_assumed": platforms_assumed,
        "languages": {k: v for k, v in sorted(loc_by_ext.items(), key=lambda x: -x[1])[:10]},
        "code_files": code_files,
        "loc": loc,
        "asset_mb": round(asset_bytes / 1048576.0, 2),
        "video_mb": round(video_bytes / 1048576.0, 2),
        "audio_mb": round(audio_bytes / 1048576.0, 2),
        "total_mb": round(total_bytes / 1048576.0, 2),
        "has_readme": bool(readme_text),
        "readme_path": readme_rel,
        "readme_encoding": readme_enc,
        "screenshots": sorted(screenshots, key=lambda s: -s["w"] * s["h"])[:16],
        "fonts": fonts[:8],
        "has_audio": audio_bytes > 0,
        "has_ai_feature": bool(ai_hits),
        "ai_keywords": sorted(ai_hits),
        "license_file": next((r for r in (os.path.basename(a).lower() for a, _b, _c in files)
                              if r in LICENSE_NAMES), None),
        "blockers": blockers,
        "warnings": warnings,
        "missing_fields": missing,
        "_scan": {
            "root": os.path.abspath(root),
            "file_count": len(files),
            "scanned_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        },
    }
    return facts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", required=True, help="项目目录或 zip 文件")
    ap.add_argument("--out", default="facts.json", help="输出的 facts.json 路径")
    ap.add_argument("--name", help="强制指定项目名（覆盖自动探测）")
    ap.add_argument("--workdir", default=".promo-work", help="zip 解压目录")
    args = ap.parse_args()

    if not os.path.exists(args.path):
        print(f"路径不存在：{args.path}")
        return 3

    root = args.path
    if os.path.isfile(root) and root.lower().endswith(".zip"):
        dest = os.path.join(args.workdir, pc.stable_slug(os.path.splitext(
            os.path.basename(root))[0]))
        safe_extract(root, dest)
        # 单层嵌套目录时下沉一层
        entries = [e for e in os.listdir(dest) if not e.startswith("__")]
        if len(entries) == 1 and os.path.isdir(os.path.join(dest, entries[0])):
            dest = os.path.join(dest, entries[0])
        print(f"zip 已解压 → {dest}")
        root = dest
    elif not os.path.isdir(root):
        print(f"不是目录也不是 zip：{root}")
        return 3

    facts = scan(root, args.name)
    pc.write_json(args.out, facts)

    print(f"=== 扫描完成 → {os.path.abspath(args.out)} ===")
    print(f"  项目名    {facts['project_name']}（来源：{facts['project_name_source']}）")
    print(f"  类型      {facts['project_type']}    平台 {'、'.join(facts['platforms'])}")
    print(f"  技术栈    {'、'.join(facts['detected_stacks']) or '未识别'}")
    print(f"  体量      {facts['loc']} 行 / {facts['code_files']} 个代码文件 / "
          f"{facts['total_mb']} MB（素材 {facts['asset_mb']} MB）")
    print(f"  截图      {len(facts['screenshots'])} 张可用")
    print(f"  一句话    {facts['one_liner'] or '（未提取到）'}")
    print(f"  卖点候选  {len(facts['features'])} 条")
    for b in facts["blockers"]:
        print(f"  [阻断] {b}")
    for wmsg in facts["warnings"]:
        print(f"  [警告] {wmsg}")
    print(f"  待补字段  {'、'.join(facts['missing_fields'])}")

    return 1 if facts["blockers"] else 0


if __name__ == "__main__":
    sys.exit(main())
