# -*- coding: utf-8 -*-
"""把一堆录制帧排成一张联络表（contact sheet），供人/AI 快速挑片段。

为什么需要它：录制成本极低（本项目全片 1792 帧无头跑完约 6 秒），
但"哪几帧值得剪进宣传片"必须看着真画面决定，不能靠猜。这个脚本把
抽样帧排成一版并标上帧号、平均亮度、色彩数，选段就有了依据。

用法:
    python contact_sheet.py --frames ./raw --out ./sheet.png
    python contact_sheet.py --frames ./raw --out ./sheet.png --cols 6 --thumb-w 300

退出码: 0 成功, 1 渲染失败, 3 用法错误
"""

import argparse
import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import imgcodec as ic      # noqa: E402
import promo_common as pc  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

FRAME_EXT = (".bmp", ".png", ".jpg", ".jpeg", ".tga", ".ppm")


def frame_key(name):
    """从文件名里抽出帧号，用于按真实顺序排列（而不是字典序）。"""
    hits = re.findall(r"(\d+)", os.path.splitext(name)[0])
    return int(hits[-1]) if hits else 0


def load_any(path):
    """统一读成 (w, h, rgb)。BMP 直读；其余格式借 ffmpeg 转一道。"""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".bmp":
        return ic.read_bmp(path)
    if ext == ".png":
        w, h, rgba = ic.read_png(path)
        rgb = bytearray(w * h * 3)
        for i in range(w * h):
            rgb[i * 3] = rgba[i * 4]
            rgb[i * 3 + 1] = rgba[i * 4 + 1]
            rgb[i * 3 + 2] = rgba[i * 4 + 2]
        return w, h, bytes(rgb)
    raise ic.ImageError("联络表暂不支持 %s，请先转成 bmp/png" % ext)


def stats_rgb(w, h, rgb, stride=3):
    """平均亮度 + 粗略色彩数。降采样算，够用且快。"""
    tot = n = 0
    colors = set()
    for i in range(0, w * h, stride):
        r = rgb[i * 3]
        g = rgb[i * 3 + 1]
        b = rgb[i * 3 + 2]
        tot += (r * 299 + g * 587 + b * 114) // 1000
        n += 1
        colors.add((r >> 4, g >> 4, b >> 4))
    return (tot / n if n else 0.0), len(colors)


def nearest_thumb(w, h, rgb, tw, th):
    """最近邻缩略图：像素艺术必须用最近邻，双线性会把像素糊掉。"""
    out = bytearray(tw * th * 3)
    for y in range(th):
        sy = min(h - 1, int(y * h / th))
        for x in range(tw):
            sx = min(w - 1, int(x * w / tw))
            s = (sy * w + sx) * 3
            d = (y * tw + x) * 3
            out[d] = rgb[s]
            out[d + 1] = rgb[s + 1]
            out[d + 2] = rgb[s + 2]
    return bytes(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", required=True, help="帧目录")
    ap.add_argument("--out", required=True, help="输出 PNG")
    ap.add_argument("--cols", type=int, default=6)
    ap.add_argument("--thumb-w", type=int, default=300)
    ap.add_argument("--max", type=int, default=240, help="最多列多少帧")
    ap.add_argument("--budget", type=int, default=15000)
    args = ap.parse_args()

    if not os.path.isdir(args.frames):
        print("帧目录不存在：%s" % args.frames)
        return 3

    files = [n for n in os.listdir(args.frames)
             if os.path.splitext(n)[1].lower() in FRAME_EXT]
    files.sort(key=frame_key)
    if not files:
        print("目录里没有可用的帧图")
        return 3
    if len(files) > args.max:
        step = len(files) / float(args.max)
        files = [files[int(i * step)] for i in range(args.max)]

    # 缩略图先落盘成 PNG，HTML 才引用得到（浏览器读 bmp 也行，但统一格式更稳）
    tmp = os.path.join(os.path.dirname(os.path.abspath(args.out)) or ".", ".sheet-tmp")
    os.makedirs(tmp, exist_ok=True)
    thumbs = []
    for n in files:
        try:
            w, h, rgb = load_any(os.path.join(args.frames, n))
        except ic.ImageError as exc:
            print("  跳过 %s：%s" % (n, exc))
            continue
        th = max(1, int(args.thumb_w * h / w))
        small = nearest_thumb(w, h, rgb, args.thumb_w, th)
        tn = "t_%s.png" % os.path.splitext(n)[0]
        tp = os.path.join(tmp, tn)
        rgba = bytearray(args.thumb_w * th * 4)
        for i in range(args.thumb_w * th):
            rgba[i * 4] = small[i * 3]
            rgba[i * 4 + 1] = small[i * 3 + 1]
            rgba[i * 4 + 2] = small[i * 3 + 2]
            rgba[i * 4 + 3] = 255
        ic.write_png(tp, args.thumb_w, th, bytes(rgba), level=6)
        bright, ncol = stats_rgb(w, h, rgb)
        thumbs.append({"file": tn, "label": "%s" % os.path.splitext(n)[0],
                       "bright": bright, "colors": ncol, "w": w, "h": h})

    if not thumbs:
        print("没有可用帧")
        return 3

    thumb_h = thumbs[0]["h"] * args.thumb_w // thumbs[0]["w"]
    pad, gap, label_h = 6, 10, 26
    cell_w = args.thumb_w + pad * 2
    cell_h = thumb_h + label_h + pad * 2
    cols = max(1, args.cols)
    rows = int(math.ceil(len(thumbs) / float(cols)))
    sheet_w = cols * cell_w + (cols + 1) * gap
    sheet_h = rows * cell_h + (rows + 1) * gap + 56

    cards = []
    for i, t in enumerate(thumbs):
        # 亮度分档上色：暗帧做宣传素材吃亏，一眼标出来
        tone = "#7a1f2b" if t["bright"] < 45 else ("#7a5a1f" if t["bright"] < 90 else "#1f5f3a")
        cards.append(
            '<div class="cell">'
            '<img src="%s/%s">'
            '<div class="lab"><span class="f">%s</span>'
            '<span class="s" style="background:%s">亮度 %.0f</span>'
            '<span class="s2">%d 色</span></div>'
            '</div>' % (tmp.replace("\\", "/"), t["file"], t["label"], tone,
                        t["bright"], t["colors"], ))
    html = """<!DOCTYPE html><html><head><meta charset="utf-8"><style>
*{margin:0;padding:0;box-sizing:border-box}
html,body{background:#11151c;font-family:"Microsoft YaHei",sans-serif}
.head{color:#e8eefc;font-size:26px;padding:16px 22px 0;font-weight:700}
.head span{color:#8fa3c4;font-size:18px;font-weight:400;margin-left:14px}
.grid{display:flex;flex-wrap:wrap;gap:%dpx;padding:%dpx}
.cell{width:%dpx;background:#1b212c;border:1px solid #2b3342;border-radius:6px;padding:%dpx}
.cell img{width:%dpx;display:block;border-radius:3px;image-rendering:pixelated}
.lab{display:flex;align-items:center;gap:7px;height:%dpx;margin-top:%dpx;flex-wrap:nowrap}
.f{color:#cfe0ff;font-size:15px;font-family:Consolas,monospace}
.s{color:#fff;font-size:12px;padding:2px 7px;border-radius:9px}
.s2{color:#7f8ea8;font-size:12px}
</style></head><body>
<div class="head">录制帧联络表<span>%d 帧 · 缩略 %dx%d · 挑亮度高、色彩多的段落进宣传片</span></div>
<div class="grid">%s</div></body></html>""" % (
        gap, gap, cell_w, pad, args.thumb_w, label_h, gap,
        len(thumbs), args.thumb_w, thumb_h, "".join(cards))

    sheet_html = os.path.splitext(os.path.abspath(args.out))[0] + ".html"
    with open(sheet_html, "w", encoding="utf-8") as f:
        f.write(html)

    browser = pc.find_browser()
    if not browser:
        print("找不到无头浏览器。HTML 已生成，自行打开截图：%s" % sheet_html)
        return 3

    ok, msg = pc.browser_shot(browser, sheet_html, args.out, sheet_w, sheet_h,
                              os.path.join(tmp, "ud"), args.budget)
    if not ok:
        print("联络表渲染失败：%s" % msg)
        return 1

    bright = sorted(thumbs, key=lambda t: -t["bright"])[:5]
    print("联络表：%s  (%dx%d, %d 帧)" % (args.out, sheet_w, sheet_h, len(thumbs)))
    print("最亮的 5 帧（优先考虑）：" + ", ".join(
        "%s(%.0f)" % (t["label"], t["bright"]) for t in bright))
    darkest = sorted(thumbs, key=lambda t: t["bright"])[:3]
    print("最暗的 3 帧（避开）：" + ", ".join(
        "%s(%.0f)" % (t["label"], t["bright"]) for t in darkest))
    return 0


if __name__ == "__main__":
    sys.exit(main())
