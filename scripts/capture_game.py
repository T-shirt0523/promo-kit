# -*- coding: utf-8 -*-
"""从游戏本体录真实画面（宣传素材的第一步）。

两种录制路线，自动选：
  1. 无头离屏导出（首选）——游戏自己把每帧渲染结果写盘。不依赖窗口、不受
     桌面遮挡/独占全屏影响、60fps 定步长、可复现。要求游戏代码支持一个
     逐帧导出开关（本项目是 DEBUG_CAPTURE 宏，用法见 references/capture-guide.md）。
     实测成本：1792 帧全片约 6 秒。
  2. 窗口录屏（兜底）——游戏没有无头模式时，用 ffmpeg gdigrab 抓窗口客户区，
     同时注入键盘/鼠标。走 capture_game.ps1。

用法:
    # 先看全片长什么样，再决定录哪几段（强烈建议的第一步）
    python capture_game.py --project D:/MyGame --survey --out ./promo/raw

    # 按选定的帧区间正式录制
    python capture_game.py --project D:/MyGame --windows "540-660;1030-1070" --out ./promo/clips

退出码: 0 成功, 1 部分失败, 3 用法错误, 4 无法构建/缺少录制能力
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import imgcodec as ic      # noqa: E402
import promo_common as pc  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

DEFAULT_RECIPE = "promo-capture.json"
CAP_MARK = "DEBUG_CAPTURE"          # 游戏侧逐帧导出的开关宏


# --------------------------------------------------------------------------
# 构建配方：能自动探测就自动，探测不到就落一份样板让用户改
# --------------------------------------------------------------------------
def _score_raylib(c):
    """给一个 raylib 目录打分，-1 表示不可用。

    同一个机器上常常同时装着 win32 与 win64 两个包。按目录名顺序取第一个会
    选中 win32 版 —— 它只有 32 位的 libraylib.a，没有 libraylibdll.a，
    链接直接报 `cannot find -lraylibdll`（已踩）。所以必须按「能不能提供
    libraylibdll」来选，而不是按目录名排序。
    """
    if not os.path.isfile(os.path.join(c, "include", "raylib.h")):
        return -1
    lib = os.path.join(c, "lib")
    names = set()
    if os.path.isdir(lib):
        names = {n.lower() for n in os.listdir(lib)}
    if "libraylibdll.a" in names:
        score = 100
    elif "raylib.dll" in names:
        score = 45
    elif "libraylib.a" in names:
        score = 10
    else:
        return -1
    low = os.path.basename(c).lower()
    if "win64" in low or "x64" in low or "amd64" in low:
        score += 25
    if "win32" in low or "i686" in low:
        score -= 80
    if "msvc" in low:
        score -= 60
    return score


def _raylib_search_roots():
    """raylib 通常被解压在这种地方。想加自己的目录就写 local-paths.json。"""
    roots = []
    for k in ("PROMO_RAYLIB_ROOT", "RAYLIB_DIR"):
        v = os.environ.get(k)
        if v:
            roots.append(v)
    roots += [os.path.expanduser("~/raylib"), os.path.expanduser("~/devtools")]
    if os.name == "nt":
        for d in ("C:\\", "D:\\"):
            if os.path.isdir(d):
                roots.append(d)
                for sub in ("devtools", "GameDevTools", "game.dev", "sdk", "libs"):
                    p = os.path.join(d, sub)
                    if os.path.isdir(p):
                        roots.append(p)
    else:
        roots += ["/usr/local", "/opt", "/usr/local/raylib", "/opt/raylib"]
    return roots


def find_raylib(project):
    # 1) 显式指定优先（环境变量 / local-paths.json 里的 "raylib"）
    direct = pc.find_tool("raylib", (), None)
    if direct and _score_raylib(direct) > 0:
        return direct

    cands = []
    for pat in _raylib_search_roots():
        if not os.path.isdir(pat):
            continue
        try:
            for n in sorted(os.listdir(pat)):
                if n.lower().startswith("raylib"):
                    cands.append(os.path.join(pat, n))
        except OSError:
            pass
        if os.path.isfile(os.path.join(pat, "include", "raylib.h")):
            cands.append(pat)

    # 2) 从项目自己的构建脚本里捞（这是最可靠的线索，项目自己知道用哪个）
    for f in ("build.bat", "build.sh", "CMakeLists.txt", "Makefile"):
        p = os.path.join(project, f)
        if os.path.isfile(p):
            t, _ = pc.read_text(p, 200000)
            for m in re.finditer(r"([A-Za-z]:[\\/][^\s\"';]*raylib[^\s\"';]*|"
                                 r"/[^\s\"';]*raylib[^\s\"';]*)", t or ""):
                c = m.group(1).rstrip("\\/")
                cands.append(c)

    best, best_score = None, 0
    for c in cands:
        s = _score_raylib(c)
        if s > best_score:
            best, best_score = c, s
    return best


def find_gxx():
    """MinGW / 系统 g++。cross 前缀也认（CI 里常见）。"""
    extra = [r"C:\mingw64\bin\g++.exe", r"C:\msys64\mingw64\bin\g++.exe",
             r"D:\mingw64\bin\g++.exe", "/usr/bin/g++", "/usr/local/bin/g++"]
    hit = pc.find_tool("gxx", extra, "g++")
    if hit:
        return hit
    for name in ("x86_64-w64-mingw32-g++", "g++-13", "g++-12", "clang++"):
        hit = shutil.which(name)
        if hit:
            return hit
    return None


def collect_sources(project):
    """项目根目录下的 .cpp。子目录（构建产物、子项目）不参与。"""
    files = []
    for n in sorted(os.listdir(project)):
        p = os.path.join(project, n)
        if os.path.isfile(p) and n.endswith(".cpp"):
            files.append(n)
    return files


def supports_headless_capture(project):
    for n in collect_sources(project):
        t, _ = pc.read_text(os.path.join(project, n), 4000000)
        if t and CAP_MARK in t:
            return True
    return False


def find_game_exe(project):
    """窗口模式下要录的那个 exe。

    之前这里写死 game.exe，但真实项目里 exe 名千奇百怪（game_v5.exe / pw_verify.exe …），
    录错文件等于录了个别的版本，画面跟当前代码对不上。按优先级找最像正式版的那个。
    """
    exes = [n for n in os.listdir(project) if n.lower().endswith(".exe")]
    if not exes:
        return None
    # 调试/压测/验证变体一律不当正式版
    noise = re.compile(r"(_dbg|_debug|_stress|_diag|_test|_verify|_shot|_cap|_bak|_old)", re.I)
    keep = [n for n in exes if not noise.search(n)] or exes
    base = os.path.basename(project.rstrip("\\/")).lower()
    for want in (base + ".exe", "game.exe", "main.exe"):
        for n in keep:
            if n.lower() == want:
                return os.path.join(project, n)
    # 都没有就取最近构建的那个
    return max((os.path.join(project, n) for n in keep), key=os.path.getmtime)


def detect_recipe(project, rebuild=False):
    """返回 (recipe, message)。探测成功时把配方落盘，便于下次直接复用/手工改。"""
    srcs = collect_sources(project)
    # 先定模式：能不能逐帧导出，只取决于源码里有没有那个开关宏，跟会不会编译无关。
    mode = "headless" if (srcs and supports_headless_capture(project)) else "window"

    if mode == "headless":
        gxx = find_gxx()
        raylib = find_raylib(project)
        if not gxx:
            return None, "找不到 g++（无头导出需要编译）"
        if not raylib:
            return None, "找不到 raylib（无头导出需要它才能编译）"
        exe = os.path.join(project, "game_cap.exe")
        # net.cpp / progress.cpp 这类后期拆出来的源文件要一起编，否则链接缺符号。
        # 别照抄项目自带的 build.bat —— 本项目的 build.bat 就已经漏了这两个（实测）。
        cmd = ([gxx] + srcs + ["-o", "game_cap.exe", "-std=c++17", "-O2", "-DDEBUG_HEADLESS",
                               "-DDEBUG_AUTO_SHOT", "-DDEBUG_CAPTURE",
                               "-I" + os.path.join(raylib, "include"),
                               "-L" + os.path.join(raylib, "lib"),
                               "-lraylibdll", "-lopengl32", "-lgdi32", "-lwinmm", "-lws2_32"])
    else:
        # 窗口录屏不需要重新编译，直接用项目已有的 exe。之前这里也设成 game_cap.exe，
        # 那个文件在窗口模式下根本不存在，配方里就存了个假路径。
        exe = find_game_exe(project)
        if not exe:
            return None, ("没找到可执行文件 —— 窗口录屏需要先把项目构建出来。"
                          "或者给游戏加逐帧导出开关 %s（见 references/capture-guide.md）" % CAP_MARK)
        cmd = None

    recipe = {
        "project": project.replace("\\", "/"),
        "mode": mode,
        "exe": exe.replace("\\", "/"),
        "build_cmd": cmd,
        "sources": srcs,
        "survey_range": "1-1792",
        "note": ("自动探测生成。构建失败/需要额外宏或链接库时，直接改这个文件的 "
                 "build_cmd 与 survey_range 即可。" if mode == "headless"
                 else "未检测到逐帧导出开关（%s），改走窗口录屏。想用无头导出请先给游戏加这个开关，"
                      "见 references/capture-guide.md。" % CAP_MARK),
    }
    pc.write_json(os.path.join(project, DEFAULT_RECIPE), recipe)
    return recipe, ("已探测构建配方并写入 %s（%s 模式）" % (DEFAULT_RECIPE, mode))


def _recipe_stale(r):
    """配方里记的路径是否已经失效。失效就让调用方重新探测。"""
    # 窗口模式是直接录项目已构建好的 exe：它没了就说明项目重新出过 exe，得重挑。
    # 无头模式的 exe 是构建产物，首次运行前本来就不存在，不能据此判定失效。
    if r.get("mode") == "window":
        exe = r.get("exe")
        if exe and not os.path.isfile(exe):
            return "可执行文件不存在 %s" % exe
    cmd = r.get("build_cmd") or []
    need_dll = "-lraylibdll" in cmd
    for a in cmd:
        if a.startswith("-I"):
            d = a[2:]
            if d and not os.path.isdir(d):
                return "头文件目录不存在 %s" % d
        elif a.startswith("-L"):
            d = a[2:]
            if d and not os.path.isdir(d):
                return "库目录不存在 %s" % d
            if need_dll and os.path.isdir(d):
                names = {n.lower() for n in os.listdir(d)}
                # 指向 win32 包的库目录里没有 libraylibdll.a，链接必然失败
                if "libraylibdll.a" not in names and "raylib.dll" not in names:
                    return "库目录 %s 里没有 libraylibdll" % d
    if cmd and not os.path.isfile(cmd[0]):
        return "编译器不存在 %s" % cmd[0]
    skip_next = False
    for a in cmd:
        # `-o game_cap.exe` 里的 game_cap.exe 是产物名、不是编译器。
        # 不跳过它的话，配方会被永远判成"失效"、每次运行都重新探测并改写（已踩过）。
        if skip_next:
            skip_next = False
            continue
        if a == "-o":
            skip_next = True
            continue
        if a.lower().endswith(".exe") and not os.path.isfile(a):
            return "编译器不存在 %s" % a
    return None


def load_recipe(project, rebuild):
    p = os.path.join(project, DEFAULT_RECIPE)
    if os.path.isfile(p):
        r = pc.load_json(p)
        if r and r.get("exe"):
            # 配方是上次自动探测的产物。换了机器、删了目录、或上次就选错了 raylib 版本时，
            # 直接复用它等于拿一个必然失败的命令行去编译（已踩：选中 win32 版 raylib）。
            bad = _recipe_stale(r)
            if bad:
                nr, msg = detect_recipe(project, rebuild)
                return nr, "旧配方失效（%s），已重新探测。%s" % (bad, msg)
            return r, "复用 %s" % DEFAULT_RECIPE
    return detect_recipe(project, rebuild)


def build_exe(recipe, force=False):
    cmd = recipe.get("build_cmd")
    if not cmd:
        return True, "窗口模式无需构建"
    exe = recipe["exe"]
    if not force and os.path.isfile(exe):
        newest = 0
        for n in recipe.get("sources") or []:
            src = os.path.join(recipe["project"], n)
            if os.path.isfile(src):
                newest = max(newest, os.path.getmtime(src))
        if newest and newest < os.path.getmtime(exe):
            return True, "已是最新，跳过编译"
    work = recipe["project"]
    log = os.path.join(work, "_promo_build.log")
    with open(log, "w", encoding="utf-8", errors="ignore") as f:
        r = subprocess.run(cmd, cwd=work, stdout=f, stderr=subprocess.STDOUT)
    if r.returncode != 0 or not os.path.isfile(exe):
        t, _ = pc.read_text(log, 200000)
        tail = "\n".join((t or "").strip().splitlines()[-12:])
        # 编译失败但旧 exe 还在时不要直接放弃 —— 它照样能录，只是没吃到最新代码。
        if os.path.isfile(exe):
            return True, ("编译失败但沿用已存在的 %s（内容可能不是最新）：\n%s"
                          % (os.path.basename(exe), tail))
        return False, "编译失败（详见 %s）：\n%s" % (log, tail)
    return True, "编译成功 → %s" % os.path.basename(exe)


# --------------------------------------------------------------------------
# 帧区间
# --------------------------------------------------------------------------
def parse_windows(spec):
    out = []
    for part in re.split(r"[;,]", spec or ""):
        part = part.strip()
        m = re.match(r"^(\d+)\s*-\s*(\d+)$", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if b >= a:
                out.append((a, b))
    return out


# --------------------------------------------------------------------------
# 无头录制
# --------------------------------------------------------------------------
def run_headless(recipe, windows_spec, out_dir, every, fmt, timeout):
    exe = recipe["exe"]
    env = dict(os.environ)
    env["PW_CAP_OUT"] = out_dir.replace("\\", "/")
    env["PW_CAP_WINDOWS"] = windows_spec
    env["PW_CAP_EVERY"] = str(every)
    env["PW_CAP_FMT"] = fmt
    log = os.path.join(out_dir, "_run.log")
    with open(log, "w", encoding="utf-8", errors="ignore") as f:
        try:
            r = subprocess.run([exe], cwd=recipe["project"], env=env,
                               stdout=f, stderr=subprocess.STDOUT, timeout=timeout)
        except subprocess.TimeoutExpired:
            return False, "运行超时（>%ds）" % timeout
    t, _ = pc.read_text(log, 400000)
    if not t or "CAP: done" not in t:
        tail = "\n".join((t or "").strip().splitlines()[-8:])
        return False, "录制未正常结束（日志尾部）：\n%s" % tail
    return True, "录制完成"


def window_frames(out_dir, fmt):
    ext = "." + fmt.lstrip(".")
    hits = {}
    for n in os.listdir(out_dir):
        if not n.startswith("cap_") or not n.endswith(ext):
            continue
        m = re.search(r"(\d+)", n)
        if m:
            hits[int(m.group(1))] = os.path.join(out_dir, n)
    return hits


# --------------------------------------------------------------------------
# 编码 + 挑静帧
# --------------------------------------------------------------------------
def encode_clip(ff, frames_dir, fmt, f0, f1, out_mp4, fps, scale_h, bitrate):
    """把 [f0,f1] 的连续帧编成一个 MP4。最近邻放大，像素艺术不能糊。"""
    vf = "scale=-2:%d:flags=neighbor,format=yuv420p" % scale_h if scale_h else "format=yuv420p"
    cmd = [ff, "-hide_banner", "-nostdin", "-y",
           "-f", "image2", "-start_number", str(f0), "-framerate", str(fps),
           "-i", os.path.join(frames_dir, "cap_%%05d.%s" % fmt),
           "-vf", vf, "-c:v", "h264_mf", "-b:v", bitrate,
           "-movflags", "+faststart", out_mp4]
    log = out_mp4 + ".log"
    with open(log, "w", encoding="utf-8", errors="ignore") as f:
        r = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT)
    if r.returncode != 0 or not os.path.isfile(out_mp4):
        t, _ = pc.read_text(log, 200000)
        tail = "\n".join((t or "").strip().splitlines()[-6:])
        return False, "%dx%d 编码失败：%s" % (f0, f1, tail)
    os.remove(log)
    return True, "%.1f 秒 · %d KB" % ((f1 - f0 + 1) / float(fps),
                                      os.path.getsize(out_mp4) // 1024)


def pick_stills(frames_dir, fmt, f0, f1, still_dir, label, want=3):
    """从一段里挑静帧。评分 = 亮度 + 色彩丰富度，暗帧和灰帧直接淘汰。

    为什么不用均匀抽帧：本项目实测整片偏暗（平均亮度最高才 58/255），
    均匀抽帧会挑到一片黑，做封面就是废图。
    """
    os.makedirs(still_dir, exist_ok=True)
    scored = []
    for fno, path in sorted(window_frames(frames_dir, fmt).items()):
        if fno < f0 or fno > f1:
            continue
        try:
            w, h, rgb = ic.read_bmp(path) if fmt == "bmp" else (None, None, None)
        except ic.ImageError:
            continue
        if w is None:
            continue
        tot = n = 0
        colors = set()
        for i in range(0, w * h, 4):
            r, g, b = rgb[i * 3], rgb[i * 3 + 1], rgb[i * 3 + 2]
            tot += (r * 299 + g * 587 + b * 114) // 1000
            n += 1
            colors.add((r >> 4, g >> 4, b >> 4))
        bright = tot / n if n else 0
        score = bright + len(colors) * 0.9
        scored.append((score, fno, path, bright, len(colors), w, h, rgb))

    scored.sort(key=lambda t: -t[0])
    # 同一段里避免挑到几乎一样的相邻帧
    chosen = []
    for t in scored:
        if any(abs(t[1] - c[1]) < 6 for c in chosen):
            continue
        chosen.append(t)
        if len(chosen) >= want:
            break

    made = []
    for rank, (score, fno, path, bright, ncol, w, h, rgb) in enumerate(chosen, 1):
        dst = os.path.join(still_dir, "%s_%d_%d.png" % (label, rank, fno))
        rgba = bytearray(w * h * 4)
        for i in range(w * h):
            rgba[i * 4] = rgb[i * 3]
            rgba[i * 4 + 1] = rgb[i * 3 + 1]
            rgba[i * 4 + 2] = rgb[i * 3 + 2]
            rgba[i * 4 + 3] = 255
        ic.write_png(dst, w, h, bytes(rgba), level=9)
        made.append({"png": os.path.basename(dst), "frame": fno,
                     "brightness": round(bright, 1), "colors": ncol,
                     "score": round(score, 1)})
    return made


# --------------------------------------------------------------------------
# 兜底：窗口录屏
# --------------------------------------------------------------------------
def run_window_capture(recipe, plan_path, out_mp4, duration, fps, ps1):
    exe = recipe.get("exe") or find_game_exe(recipe["project"])
    if not exe or not os.path.isfile(exe):
        return False, "可执行文件不存在：%s" % exe
    cmd = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
           "-File", ps1, "-Exe", exe,
           "-Out", out_mp4, "-Duration", str(duration), "-Fps", str(fps), "-HideConsole"]
    if plan_path:
        cmd += ["-PlanJson", plan_path]
    # 把 python 侧探测到的 ffmpeg 传下去，避免 ps1 里再猜一遍
    ff = pc.find_ffmpeg()
    if ff:
        cmd += ["-Ffmpeg", ff]
    r = subprocess.run(cmd, capture_output=True, timeout=600)
    out = (r.stdout or b"").decode("utf-8", "ignore")
    ok = "OK" in out and os.path.isfile(out_mp4)
    return ok, out.strip()


# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True, help="游戏项目根目录")
    ap.add_argument("--out", required=True, help="输出目录")
    ap.add_argument("--windows", help='帧区间，如 "540-660;1030-1070"')
    ap.add_argument("--survey", action="store_true",
                    help="全片稀疏采样 + 出联络表，用来决定录哪几段")
    ap.add_argument("--every", type=int, default=1, help="每 N 帧录一张")
    ap.add_argument("--fps", type=int, default=60)
    ap.add_argument("--scale-height", type=int, default=720, help="输出高度（最近邻放大）")
    ap.add_argument("--bitrate", default="14M")
    ap.add_argument("--stills", type=int, default=3, help="每段挑几张静帧给宣传图用")
    ap.add_argument("--window-sec", type=int, default=20, help="窗口录屏单段秒数（仅窗口模式）")
    ap.add_argument("--keep-frames", action="store_true")
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--timeout", type=int, default=900)
    args = ap.parse_args()

    project = os.path.abspath(args.project)
    if not os.path.isdir(project):
        print("项目目录不存在：%s" % project)
        return 3
    out_dir = os.path.abspath(args.out)
    os.makedirs(out_dir, exist_ok=True)

    recipe, msg = load_recipe(project, args.rebuild)
    if not recipe:
        print("无法构建录制版本：%s" % msg)
        print("退路：用 capture_game.ps1 直接录窗口，或给游戏加逐帧导出开关。")
        return 4
    print("[配方] %s" % msg)

    ok, bmsg = build_exe(recipe, force=args.rebuild)
    print("[构建] %s" % bmsg)
    if not ok:
        return 4

    ff = pc.find_ffmpeg()
    if not ff:
        print("找不到 ffmpeg —— 无法编码。把 ffmpeg.exe 放进 D:\\GameDevTools\\ 即可。")
        return 4

    if recipe["mode"] != "headless":
        exe = recipe.get("exe") or ""
        if not os.path.isfile(exe):
            print("窗口录屏要录的可执行文件不存在：%s" % (exe or "(未探测到)"))
            print("先构建项目，或给游戏加逐帧导出开关 %s 走无头导出。" % CAP_MARK)
            return 4
        print("[窗口模式] 目标 %s（本模式不重新编译）" % os.path.basename(exe))
        ps1 = os.path.join(pc.SKILL_ROOT, "scripts", "capture_game.ps1")
        mp4 = os.path.join(out_dir, "clip_window.mp4")
        plan = os.path.join(pc.SKILL_ROOT, "scripts", "window-plan.example.json")
        plan_path = plan if os.path.isfile(plan) else None
        ok, out = run_window_capture(recipe, plan_path, mp4, args.window_sec, args.fps, ps1)
        print("[窗口录制] %s" % ("成功" if ok else "失败"))
        print(out[-600:])
        return 0 if ok else 1

    frames_dir = os.path.join(out_dir, "raw")
    os.makedirs(frames_dir, exist_ok=True)

    # ---- 采样模式：先看清全片，再决定录什么 ----
    if args.survey:
        rng = recipe.get("survey_range") or "1-1792"
        every = args.every if args.every > 1 else 24
        print("[采样] 区间 %s，每 %d 帧取一张" % (rng, every))
        ok, msg = run_headless(recipe, rng, frames_dir, every, "bmp", args.timeout)
        print("[采样] %s" % msg)
        if not ok:
            return 1
        cs = os.path.join(pc.SKILL_ROOT, "scripts", "contact_sheet.py")
        sheet = os.path.join(out_dir, "contact-sheet.png")
        r = subprocess.run([sys.executable, "-u", cs, "--frames", frames_dir,
                            "--out", sheet], capture_output=True)
        sys.stdout.write((r.stdout or b"").decode("utf-8", "ignore"))
        if r.returncode != 0:
            print("联络表生成失败")
            return 1
        print("\n下一步：看 %s，挑出亮度高、信息量大的帧区间，"
              "再用 --windows \"起-止;起-止\" 正式录制。" % sheet)
        return 0

    windows = parse_windows(args.windows)
    if not windows:
        print('需要 --windows "起-止" 或 --survey。')
        return 3

    print("[录制] %d 段：%s" % (len(windows), args.windows))
    ok, msg = run_headless(recipe, args.windows, frames_dir, args.every, "bmp", args.timeout)
    print("[录制] %s" % msg)
    if not ok:
        return 1

    clip_dir = os.path.join(out_dir, "clips")
    still_dir = os.path.join(out_dir, "stills")
    os.makedirs(clip_dir, exist_ok=True)
    report = {"mode": "headless", "fps": args.fps, "clips": [], "stills": []}

    fail = 0
    for i, (f0, f1) in enumerate(windows, 1):
        label = "seg%02d_%d-%d" % (i, f0, f1)
        mp4 = os.path.join(clip_dir, label + ".mp4")
        ok, msg = encode_clip(ff, frames_dir, "bmp", f0, f1, mp4,
                              args.fps, args.scale_height, args.bitrate)
        print("[编码] %s → %s" % (label, msg))
        if not ok:
            fail += 1
            continue
        report["clips"].append({"label": label, "from": f0, "to": f1,
                               "mp4": os.path.relpath(mp4, out_dir).replace("\\", "/"),
                               "seconds": round((f1 - f0 + 1) / float(args.fps), 2)})
        stills = pick_stills(frames_dir, "bmp", f0, f1, still_dir, label, args.stills)
        for s in stills:
            s["label"] = label
            s["png"] = "stills/" + s["png"]
        report["stills"] += stills
        print("[静帧] %s 挑出 %d 张：%s" % (
            label, len(stills), ", ".join("#%d(亮度%.0f)" % (s["frame"], s["brightness"])
                                          for s in stills)))

    if not args.keep_frames:
        shutil.rmtree(frames_dir, ignore_errors=True)
    else:
        print("[保留] 原始帧：%s" % frames_dir)

    pc.write_json(os.path.join(out_dir, "capture-report.json"), report)

    md = ["# 录制报告", "",
          "模式：无头离屏导出（游戏自己写盘，非窗口截屏）",
          "帧率：%d fps　输出高度：%d　原始帧：%s" % (args.fps, args.scale_height, "已清理" if not args.keep_frames else frames_dir),
          "", "## 片段", "", "| 片段 | 帧区间 | 时长 | 文件 |", "|---|---|---|---|"]
    for c in report["clips"]:
        md.append("| %s | %d–%d | %.1fs | %s |" % (c["label"], c["from"], c["to"],
                                                   c["seconds"], c["mp4"]))
    md += ["", "## 静帧（按亮度+色彩评分自动挑，可直接当宣传图底图）", "",
           "| 帧号 | 文件 | 亮度 | 色彩数 |", "|---|---|---|---|"]
    for s in report["stills"]:
        md.append("| %d | %s | %.0f | %d |" % (s["frame"], s["png"], s["brightness"], s["colors"]))
    md += ["", "## 下一步", "", "1. `edit_video.py` 把片段剪成多规格宣传片；",
           "2. 静帧替换进 `06-宣传图` 的模板，替掉非实机画面。"]
    pc.write(os.path.join(out_dir, "capture-report.md"), "\n".join(md) + "\n")

    print("\n报告：%s" % os.path.join(out_dir, "capture-report.md"))
    print("片段 %d 个，静帧 %d 张。" % (len(report["clips"]), len(report["stills"])))
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
