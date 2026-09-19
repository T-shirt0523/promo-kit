# -*- coding: utf-8 -*-
"""用本机无头浏览器把 06-宣传图/src/*.html 批量渲染成精确尺寸的 PNG。

为什么不用 PIL / ImageMagick 合成：本机没有这两个东西，而且它们排版中文很痛苦。
浏览器天然会排版，字体、换行、标点全部正确 —— 这是最省事也最准的一条路。

用法:
    python render_assets.py --dir ./promo-out
    python render_assets.py --dir ./promo-out --only 小红书封面

退出码: 0 = 全部成功, 1 = 有失败项, 3 = 用法错误（找不到浏览器或清单）
"""

import argparse
import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import promo_common as pc  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

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
    for path in BROWSERS:
        if path and os.path.isfile(path):
            return path
    for pat in ("msedge", "chrome", "chromium"):
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
        return (int.from_bytes(head[16:20], "big"),
                int.from_bytes(head[20:24], "big"))
    except Exception:
        return (0, 0)


def render_one(browser, html_path, out_png, w, h, ud_dir, budget_ms, timeout=90,
               transparent=False):
    """渲染单张。返回 (ok, message)。

    transparent=True 时输出带 alpha 的 PNG（视频字幕图层用）。
    注意：**必须靠 --default-background-color 让浏览器把画布留空**，
    光在 CSS 里写 background:transparent 没用 —— 浏览器默认给不透明白底。
    """
    url = "file:///" + os.path.abspath(html_path).replace("\\", "/")
    os.makedirs(ud_dir, exist_ok=True)
    if os.path.exists(out_png):
        try:
            os.remove(out_png)
        except OSError:
            pass

    cmd = [
        browser,
        "--headless=new",
        "--disable-gpu",
        "--hide-scrollbars",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
        "--disable-background-networking",
        # 每次都换一个 user-data-dir：复用会让上一次的窗口尺寸/缓存影响这一次（已踩过）
        "--user-data-dir=" + ud_dir,
        "--force-device-scale-factor=1",
        "--window-size=%d,%d" % (w, h),
        "--virtual-time-budget=%d" % budget_ms,
        "--screenshot=" + os.path.abspath(out_png),
    ]
    if transparent:
        cmd.insert(-1, "--default-background-color=00000000")
    cmd.append(url)
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"超时（>{timeout}s）"
    except OSError as exc:
        return False, f"启动浏览器失败：{exc}"

    if not os.path.isfile(out_png):
        err = (proc.stderr or b"").decode("utf-8", "ignore").strip().splitlines()
        tail = err[-1] if err else "无 stderr 输出"
        return False, f"未产出文件（{tail[:120]}）"

    gw, gh = png_size(out_png)
    if (gw, gh) != (w, h):
        return False, f"尺寸不符：期望 {w}x{h}，实际 {gw}x{gh}"
    return True, f"{gw}x{gh} · {os.path.getsize(out_png) // 1024} KB"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="./promo-out", help="gen_promo.py 的输出目录")
    ap.add_argument("--only", help="只渲染名字包含该字符串的海报")
    ap.add_argument("--budget", type=int, default=8000, help="virtual-time-budget(ms)")
    ap.add_argument("--keep-tmp", action="store_true", help="保留临时 user-data-dir")
    args = ap.parse_args()

    manifest_path = os.path.join(args.dir, "06-宣传图", "posters.json")
    if not os.path.isfile(manifest_path):
        print(f"找不到海报清单：{manifest_path}\n请先运行 gen_promo.py")
        return 3

    posters = pc.load_json(manifest_path)
    if args.only:
        posters = [p for p in posters if args.only in p.get("label", "")]
        if not posters:
            print(f"没有匹配「{args.only}」的海报")
            return 3

    browser = find_browser()
    if not browser:
        print("找不到无头浏览器（Edge / Chrome / Chromium 都没有）。")
        print("降级方案：直接用浏览器打开 06-宣传图/src/*.html，手动截整页图。")
        return 3
    print(f"浏览器：{browser}")

    tmp_root = os.path.join(args.dir, ".render-tmp")
    ok_n, fail = 0, []
    results = []

    for i, item in enumerate(posters, start=1):
        label = item.get("label", f"poster-{i}")
        html_path = os.path.join(args.dir, item["html"])
        out_png = os.path.join(args.dir, item["png"])
        w, h = int(item["width"]), int(item["height"])
        if not os.path.isfile(html_path):
            fail.append((label, f"源文件缺失：{item['html']}"))
            continue
        os.makedirs(os.path.dirname(out_png), exist_ok=True)
        # 每张换一个干净目录，串行渲染
        ok, msg = render_one(browser, html_path, out_png, w, h,
                             os.path.join(tmp_root, "ud-%d" % i), args.budget)
        results.append((label, w, h, ok, msg))
        if ok:
            ok_n += 1
        else:
            fail.append((label, msg))

    if not args.keep_tmp:
        shutil.rmtree(tmp_root, ignore_errors=True)

    print("\n=== 宣传图渲染结果 ===")
    print("| 图 | 尺寸 | 结果 | 说明 |")
    print("|---|---|---|---|")
    for label, w, h, ok, msg in results:
        print(f"| {label} | {w}x{h} | {'OK' if ok else '失败'} | {msg} |")

    if fail:
        print(f"\n失败 {len(fail)} 项：")
        for label, msg in fail:
            print(f"  - {label}: {msg}")
        print("\n常见原因：文案太长导致排版溢出（缩短标题/副标）、"
              "HTML 里引用了不存在的图片路径、浏览器被安全软件拦截。")
        return 1

    print(f"\n全部成功，共 {ok_n} 张 → {os.path.abspath(os.path.join(args.dir, '06-宣传图'))}")
    print("注意：海报是本地渲染的，消耗 0 积分，中文文字准确无误字。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
