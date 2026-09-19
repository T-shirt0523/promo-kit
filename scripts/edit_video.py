# -*- coding: utf-8 -*-
"""把录好的游戏片段剪成多规格宣传片 + 叠中文行动号召条。

为什么文字不交给 ffmpeg：
  很多 Windows 上预装的 ffmpeg 是不完整构建（例如**没有 drawtext / subtitles 滤镜**），
  也**没有 png 解码器**。所以：
    文字 → 无头浏览器渲染成透明背景 PNG → imgcodec 转 32 位 TGA
    → ffmpeg overlay（它认得 targa/bgra，alpha 正常）
  好处：中文 100% 正确、排版交给浏览器、0 积分、不依赖任何素材库。

为什么先归一化再拼接：
  concat 要求各段编码参数一致。所以每段都走同一条 scale/pad 链再 concat，
  而不是直接拿原片拼（原片尺寸/帧率只要有一点差异就会报错或音画错位）。

用法:
    # 横竖两版，带标题与 CTA
    python edit_video.py --clips ./raw/clips --out ./宣传视频 \
        --title "像素荒野生存" --cta "点击下载 · 即刻开玩"

    # 只出竖版，加背景音乐
    python edit_video.py --clips ./raw/clips --formats 9x16 --bgm ./bgm.mp3

    # 指定段与顺序（逗号分隔）
    python edit_video.py --clips a.mp4,b.mp4 --out ./out

退出码: 0 全部成功, 1 有规格失败, 3 用法错误（无 ffmpeg / 无片段 / 无浏览器）
"""

import argparse
import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import imgcodec as ic          # noqa: E402
import promo_common as pc      # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# 规格表：名字 → (宽, 高, 用途)
SPECS = {
    "16x9": (1920, 1080, "B站 / 抖音横版 / 官网嵌入"),
    "4x3":  (1440, 1080, "小红书横版 / 知乎"),
    "1x1":  (1080, 1080, "电商主图位 / 朋友圈"),
    "4x5":  (1080, 1350, "小红书竖版推荐比例"),
    "9x16": (1080, 1920, "抖音 / 视频号 / Reels"),
}
DEFAULT_FORMATS = "16x9,9x16,1x1"

H264_ENCODERS = ["h264_mf", "libx264", "mpeg4"]

# 图层样式（想改外观就改这里。尺寸一律按目标画布比例推导）
# 角标跟标题成组居中，不放四角 —— 四角几乎总是被游戏自己的 HUD（血条/任务/背包）占着，
# 独立定位必然撞车（已踩：角标和血条上的等级字样叠在一起）。
OVERLAY_HTML = """<!DOCTYPE html><html><head><meta charset="utf-8"><style>
html,body{{margin:0;padding:0;width:{W}px;height:{H}px;background:transparent;overflow:hidden}}
.wrap{{position:absolute;inset:0;font-family:"Microsoft YaHei","PingFang SC",\
"Noto Sans CJK SC","Source Han Sans SC",sans-serif;-webkit-font-smoothing:antialiased}}
.top{{position:absolute;left:{PAD}px;right:{PAD}px;top:{TOP}px;display:flex;
      flex-direction:column;align-items:center;gap:{GAP}px}}
.title{{color:#fff;font-size:{TS}px;font-weight:800;letter-spacing:.04em;line-height:1.28;
        text-align:center;text-shadow:0 {SH}px {SH2}px rgba(0,0,0,.85),0 0 {SH2}px rgba(0,0,0,.6);
        display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}}
.badge{{background:rgba(10,14,20,.72);color:#ffe08a;font-size:{BS}px;font-weight:600;
        padding:{BPY}px {BPX}px;border-radius:999px;white-space:nowrap;letter-spacing:.1em;
        border:1px solid rgba(255,224,138,.35)}}
.cta{{position:absolute;left:50%;transform:translateX(-50%);bottom:{BOT}px;
      background:linear-gradient(180deg,#ffd45c,#ffab00);color:#141a22;
      font-size:{CS}px;font-weight:800;padding:{CPY}px {CPX}px;border-radius:{CR}px;
      box-shadow:0 {SH}px {SH2}px rgba(0,0,0,.6);white-space:nowrap;letter-spacing:.02em}}
.vig{{position:absolute;inset:0;background:
      linear-gradient(180deg,rgba(0,0,0,.55) 0%,rgba(0,0,0,0) 26%,
      rgba(0,0,0,0) 62%,rgba(0,0,0,.6) 100%)}}
</style></head><body><div class="wrap"><div class="vig"></div>
{BLOCKS}
</div></body></html>
"""


# --------------------------------------------------------------------------
# 探测
# --------------------------------------------------------------------------
def probe_video(ff, path):
    """返回 (w, h, seconds)。ffprobe 没有就解析 ffmpeg 的 stderr。"""
    try:
        p = subprocess.run([ff, "-hide_banner", "-nostdin", "-i", path],
                           capture_output=True, timeout=60)
    except (subprocess.TimeoutExpired, OSError):
        return None
    txt = (p.stderr or b"").decode("utf-8", "ignore")
    w = h = 0
    m = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", txt)
    if m:
        w, h = int(m.group(1)), int(m.group(2))
    sec = 0.0
    d = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", txt)
    if d:
        sec = int(d.group(1)) * 3600 + int(d.group(2)) * 60 + float(d.group(3))
    if not w:
        return None
    return w, h, sec


def pick_encoder(ff):
    try:
        p = subprocess.run([ff, "-hide_banner", "-encoders"], capture_output=True, timeout=60)
    except (subprocess.TimeoutExpired, OSError):
        return "h264_mf"
    txt = (p.stdout or b"").decode("utf-8", "ignore")
    for e in H264_ENCODERS:
        if re.search(r"\s" + re.escape(e) + r"\s", txt):
            return e
    return "mpeg4"


# --------------------------------------------------------------------------
# 图层：浏览器渲染透明 PNG → TGA
# --------------------------------------------------------------------------
def build_overlay_html(w, h, title, cta, badge):
    """按画布尺寸推导字号。经验值：标题占高 7.5%，CTA 占 4.2%。"""
    scale = h / 1080.0
    css = dict(
        W=w, H=h,
        PAD=int(round(w * 0.045)),
        TOP=int(round(h * 0.055)),
        GAP=int(round(h * 0.016)),
        TS=int(round(h * 0.075)),
        CS=int(round(h * 0.040)),
        BS=int(round(h * 0.028)),
        CPY=int(round(h * 0.022)),
        CPX=int(round(w * 0.042)),
        BPY=int(round(h * 0.011)),
        BPX=int(round(w * 0.028)),
        CR=int(round(h * 0.018)),
        BOT=int(round(h * 0.062)),
        SH=max(2, int(round(4 * scale))),
        SH2=max(6, int(round(14 * scale))),
    )
    blocks = []
    top = []
    if title:
        top.append('<div class="title">%s</div>' % _esc(title))
    if badge:
        top.append('<div class="badge">%s</div>' % _esc(badge))
    if top:
        blocks.append('<div class="top">%s</div>' % "".join(top))
    if cta:
        blocks.append('<div class="cta">%s</div>' % _esc(cta))
    return OVERLAY_HTML.format(BLOCKS="\n".join(blocks), **css)


def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def render_overlay(browser, work_dir, idx, w, h, title, cta, badge, budget=5000):
    """返回 (tga_path, message)。任何一步失败都返回 None。"""
    import render_assets as ra          # 复用已有的浏览器渲染实现
    html_path = os.path.join(work_dir, "overlay_%02d.html" % idx)
    png_path = os.path.join(work_dir, "overlay_%02d.png" % idx)
    tga_path = os.path.join(work_dir, "overlay_%02d.tga" % idx)

    pc.write(html_path, build_overlay_html(w, h, title, cta, badge))
    ok, msg = ra.render_one(browser, html_path, png_path, w, h,
                            os.path.join(work_dir, "ud-%02d" % idx), budget,
                            transparent=True)
    if not ok:
        return None, "图层渲染失败：%s" % msg

    try:
        gw, gh, rgba = ic.read_png(png_path)
    except ic.ImageError as exc:
        return None, "图层解码失败：%s" % exc
    if (gw, gh) != (w, h):
        return None, "图层尺寸 %dx%d 与画布 %dx%d 不符" % (gw, gh, w, h)

    # 全不透明白底说明浏览器没吃到透明 flag；直接报出来，别默默输出一张糊死画面的图
    opaque = sum(1 for i in range(0, gw * gh, 97) if rgba[i * 4 + 3] == 255)
    sampled = len(range(0, gw * gh, 97))
    if sampled and opaque / float(sampled) > 0.97:
        return None, ("图层不透明（浏览器未接受透明背景），overlay 会把画面盖死；"
                      "已跳过叠加，视频仍会正常输出")

    ic.write_tga(tga_path, gw, gh, rgba)
    return tga_path, "图层 %dx%d" % (gw, gh)


# --------------------------------------------------------------------------
# 滤镜链
# --------------------------------------------------------------------------
def norm_chain(in_label, out_label, W, H, src_w, src_h):
    """把一段视频归一化到 W×H。

    比例接近就直接缩放；差得远（竖版放横片）用「放大模糊铺底 + 原片居中」，
    比强行裁切或加黑边好看得多。
    """
    src_ar = (src_w / float(src_h)) if src_h else 1.7778
    dst_ar = W / float(H)
    if abs(src_ar - dst_ar) / dst_ar <= 0.06:
        return "[%s]scale=%d:%d:flags=lanczos,setsar=1[%s]" % (in_label, W, H, out_label)

    bg = "%s_bg" % out_label
    fg = "%s_fg" % out_label
    return (
        "[%s]scale=%d:%d:force_original_aspect_ratio=increase,"
        "crop=%d:%d,gblur=sigma=%d,setsar=1[%s];"
        "[%s]scale=%d:-2:flags=lanczos[%s];"
        "[%s][%s]overlay=(W-w)/2:(H-h)/2[%s]"
    ) % (in_label, W, H, W, H, max(18, int(round(H / 60.0))), bg,
         in_label, W, fg, bg, fg, out_label)


def run_ffmpeg(ff, cmd, log_path):
    with open(log_path, "wb") as f:
        p = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT)
    return p.returncode


# --------------------------------------------------------------------------
# 单个规格的剪辑
# --------------------------------------------------------------------------
def cut_spec(ff, enc, clips, out_path, W, H, title, cta, badge, browser,
             work_dir, spec_name, fade=0.4, fps=60, bitrate="12M",
             bgm=None, bgm_volume=0.35, log_dir=None):
    """返回 (ok, message)。"""
    metas = []
    for c in clips:
        m = probe_video(ff, c)
        if not m:
            return False, "读不出视频信息：%s" % os.path.basename(c)
        metas.append(m)

    # 图层（叠在最上层）。渲染失败不算致命，继续出无文字版。
    tga, layer_msg = render_overlay(browser, work_dir, abs(hash(spec_name)) % 100,
                                    W, H, title, cta, badge)
    inputs, chains, cat_labels, total = [], [], [], 0.0
    for i, (c, (sw, sh, sec)) in enumerate(zip(clips, metas)):
        inputs += ["-i", c]
        chains.append(norm_chain("%d:v" % i, "n%d" % i, W, H, sw, sh))
        cat_labels.append("[n%d]" % i)
        total += sec

    # 每段时长不等时 concat 会按各自的 timebase 接，先统一帧率再拼
    if len(clips) > 1:
        chains.append("%sconcat=n=%d:v=1:a=0[cat]" % ("".join(cat_labels), len(clips)))
        cur = "[cat]"
    else:
        cur = cat_labels[0]

    n_in = len(clips)
    if tga:
        inputs += ["-i", tga]
        chains.append("%s[%d:v]overlay=0:0[ov]" % (cur, n_in))
        cur = "[ov]"

    # 淡入淡出 + 统一帧率/像素格式
    fades = "fade=t=in:st=0:d=%.2f" % fade
    if total > fade * 2:
        fades += ",fade=t=out:st=%.2f:d=%.2f" % (total - fade, fade)
    chains.append("%sfps=%d,%s,format=yuv420p[vout]" % (cur, fps, fades))

    cmd = [ff, "-hide_banner", "-nostdin", "-y"] + inputs
    if bgm and os.path.isfile(bgm):
        cmd += ["-stream_loop", "-1", "-i", bgm]
    cmd += ["-filter_complex", ";".join(chains), "-map", "[vout]"]
    if bgm and os.path.isfile(bgm):
        cmd += ["-map", "%d:a" % (n_in + (1 if tga else 0)),
                "-filter:a", "volume=%.2f" % bgm_volume,
                "-c:a", "aac", "-b:a", "160k", "-shortest"]
    cmd += ["-c:v", enc, "-b:v", bitrate, "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            out_path]

    log = os.path.join(log_dir or work_dir, "ffmpeg_%s.log" % spec_name)
    rc = run_ffmpeg(ff, cmd, log)
    if rc != 0 or not os.path.isfile(out_path):
        tail = ""
        try:
            with open(log, "r", encoding="utf-8", errors="ignore") as f:
                lines = [l for l in f.read().splitlines() if l.strip()]
            tail = " / ".join(lines[-3:])[:300]
        except OSError:
            pass
        return False, "编码失败（rc=%d）：%s" % (rc, tail)

    m = probe_video(ff, out_path)
    if not m:
        return False, "产物无法解码"
    ow, oh, osec = m
    if (ow, oh) != (W, H):
        return False, "产物尺寸 %dx%d 不符合 %dx%d" % (ow, oh, W, H)

    msg = "%dx%d · %.1fs · %d KB" % (ow, oh, osec, os.path.getsize(out_path) // 1024)
    if layer_msg:
        msg += " · " + layer_msg
    else:
        msg += " · 无文字图层"
    return True, msg


# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="把游戏片段剪成多规格宣传片")
    ap.add_argument("--clips", required=True,
                    help="片段目录，或逗号分隔的 mp4 列表（按给定顺序拼接）")
    ap.add_argument("--out", default="./宣传视频", help="输出目录")
    ap.add_argument("--formats", default=DEFAULT_FORMATS,
                    help="要出的规格，逗号分隔，可选：%s" % "/".join(SPECS))
    ap.add_argument("--title", default="", help="顶部标题（可空）")
    ap.add_argument("--cta", default="", help="底部行动号召条文字（可空）")
    ap.add_argument("--badge", default="", help="左上角小标签（可空）")
    ap.add_argument("--bgm", help="背景音乐文件（本地音频，注意版权）")
    ap.add_argument("--bgm-volume", type=float, default=0.35)
    ap.add_argument("--fps", type=int, default=60)
    ap.add_argument("--fade", type=float, default=0.4, help="首尾淡入淡出秒数")
    ap.add_argument("--bitrate", default="12M")
    ap.add_argument("--keep-temp", action="store_true")
    args = ap.parse_args()

    ff = pc.find_ffmpeg()
    if not ff:
        print("找不到 ffmpeg —— 无法剪辑。把 ffmpeg.exe 放进 D:\\GameDevTools\\ 即可。")
        return 3

    # 片段来源
    if os.path.isdir(args.clips):
        clips = [os.path.join(args.clips, n) for n in sorted(os.listdir(args.clips))
                 if n.lower().endswith(".mp4")]
    else:
        clips = [p.strip() for p in args.clips.split(",") if p.strip()]
    clips = [c for c in clips if os.path.isfile(c)]
    if not clips:
        print("没有可用的 mp4 片段：%s" % args.clips)
        print("先跑 capture_game.py 录制，再把它的 clips 目录传进来。")
        return 3

    import render_assets as ra
    browser = ra.find_browser()
    if not browser:
        print("找不到无头浏览器（Edge / Chrome / Chromium）。")
        print("没有浏览器就只能出无文字版：加 --layer-off 或手动装浏览器。")
        if args.title or args.cta or args.badge:
            return 3

    enc = pick_encoder(ff)
    print("ffmpeg 编码器：%s" % enc)
    print("片段 %d 个：" % len(clips))
    for c in clips:
        m = probe_video(ff, c)
        print("  %s  %s" % (os.path.basename(c),
                            "%dx%d %.1fs" % m if m else "读取失败"))

    wanted = []
    for name in [s.strip() for s in args.formats.split(",") if s.strip()]:
        if name not in SPECS:
            print("未知规格「%s」，可选：%s" % (name, "/".join(SPECS)))
            return 3
        wanted.append(name)

    os.makedirs(args.out, exist_ok=True)
    tmp = os.path.join(args.out, ".edit-tmp")
    os.makedirs(tmp, exist_ok=True)

    print("\n=== 剪辑 ===")
    print("| 规格 | 尺寸 | 结果 | 说明 |")
    print("|---|---|---|---|")
    rows, ok_n, fails = [], 0, []
    for name in wanted:
        W, H, _use = SPECS[name]
        out_mp4 = os.path.join(args.out, "宣传片_%s.mp4" % name)
        ok, msg = cut_spec(ff, enc, clips, out_mp4, W, H, args.title, args.cta,
                           args.badge, browser, tmp, name, fade=args.fade,
                           fps=args.fps, bitrate=args.bitrate, bgm=args.bgm,
                           bgm_volume=args.bgm_volume)
        rows.append((name, "%dx%d" % (W, H), ok, msg))
        if ok:
            ok_n += 1
        else:
            fails.append((name, msg))

    for name, size, ok, msg in rows:
        print("| %s | %s | %s | %s |" % (name, size, "OK" if ok else "失败", msg))

    if not args.keep_temp:
        shutil.rmtree(tmp, ignore_errors=True)

    # 交付说明
    notes = ["# 宣传片剪辑报告", "",
             "来源片段：%d 个" % len(clips),
             "编码器：%s　帧率：%d　淡入淡出：%.1fs" % (enc, args.fps, args.fade),
             "文字图层：无头浏览器本地渲染（0 积分，中文准确）", ""]
    if args.title or args.cta or args.badge:
        notes += ["叠加文字：",
                  "- 标题：%s" % (args.title or "（无）"),
                  "- 行动号召：%s" % (args.cta or "（无）"),
                  "- 角标：%s" % (args.badge or "（无）"), ""]
    if args.bgm:
        notes += ["背景音乐：%s（音量 %.2f）" % (os.path.basename(args.bgm), args.bgm_volume),
                  "> 音乐版权由你负责：优先用平台曲库或自制音频，别用流行歌。", ""]
    notes += ["## 规格清单", "", "| 规格 | 尺寸 | 用途 | 文件 |", "|---|---|---|---|"]
    for name in wanted:
        W, H, use = SPECS[name]
        notes.append("| %s | %dx%d | %s | 宣传片_%s.mp4 |" % (name, W, H, use, name))
    notes += ["", "## 下一步", "",
              "1. 静帧已由 capture_game.py 挑好，替换进 `06-宣传图` 的模板即可；",
              "2. 要加旁白/字幕条，改 `edit_video.py` 顶部的 OVERLAY_HTML 样式重跑；",
              "3. 要更长的片子，回 capture_game.py 加帧区间多录几段再拼。", ""]
    pc.write(os.path.join(args.out, "剪辑报告.md"), "\n".join(notes))

    print("\n报告：%s" % os.path.abspath(os.path.join(args.out, "剪辑报告.md")))
    if fails:
        print("失败 %d 个规格：" % len(fails))
        for name, msg in fails:
            print("  - %s: %s" % (name, msg))
        return 1
    print("全部成功，共 %d 个规格 → %s" % (ok_n, os.path.abspath(args.out)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
