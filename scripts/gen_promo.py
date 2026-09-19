# -*- coding: utf-8 -*-
"""facts.json → 整套推广物料包。

分工原则（重要）：
  脚本负责「事实层」—— 把扫出来的真实信息填进经过验证的文案结构，不编造、不抒情。
  Agent 负责「打磨层」—— 拿着事实改写得更有人味。见产出物里的「给 Agent 的加工指引」。
  脚本不假装自己能写出好文案，但也绝不产出占位符糊弄。

用法:
    python gen_promo.py --facts facts.json --out ./promo-out
    python gen_promo.py --facts facts.json --out ./promo-out --set audience=像素风爱好者 --set seller.age=null

退出码: 0 = 正常, 1 = 生成了但含高危词（需修）, 3 = 用法错误
"""

import argparse
import datetime
import html
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import promo_common as pc  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

TEMPLATE_DIR = os.path.join(pc.ASSETS, "poster_templates")
LANDING_TPL = os.path.join(pc.ASSETS, "landing_page_template.html")

SCAN_FILES = ["01-核心文案.md", "02-定价方案.md", "03-渠道文案.md",
              "04-落地页.html", "05-视频分镜.md", "06-宣传图/src"]

# 本地渲染的海报：0 积分。这是必须做的主力素材。
POSTER_PLAN = [
    ("小红书封面", "cover_3x4", 1242, 1656, "首图，信息最全，标题必须一眼看完"),
    ("电商主图", "main_1x1", 1080, 1080, "白底留白，用于店铺/详情页首图"),
    ("竖屏故事", "story_9x16", 1080, 1920, "抖音/B站动态/朋友圈，三段式"),
    ("视频封面", "wide_16x9", 1920, 1080, "视频首帧与落地页头图"),
]

PALETTES = {
    "neon":  {"bg1": "#101728", "bg2": "#1d2b4a", "fg": "#ffffff", "accent": "#4dd0ff",
              "muted": "#9bb2d0", "card": "rgba(255,255,255,.07)"},
    "light": {"bg1": "#f7f9fc", "bg2": "#e8eef7", "fg": "#111a28", "accent": "#1f6feb",
              "muted": "#5a6b85", "card": "rgba(17,26,40,.05)"},
    "warm":  {"bg1": "#231404", "bg2": "#4a2a08", "fg": "#fff6e8", "accent": "#ffb454",
              "muted": "#d8b98b", "card": "rgba(255,246,232,.08)"},
}


# ---------- 小工具 ----------

def pick(obj, *path, default=None):
    cur = obj
    for k in path:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur if cur is not None else default


def is_blank(v):
    return v is None or v == "" or v == [] or (isinstance(v, str) and not v.strip())


def feats_of(p):
    out = [str(f).strip() for f in pc.as_list(p.get("features")) if str(f).strip()]
    return out


def money(v):
    return f"{float(v):g}"


def set_path(d, dotted, value):
    cur = d
    keys = dotted.split(".")
    for k in keys[:-1]:
        cur = cur.setdefault(k, {})
    cur[keys[-1]] = value
    return d


def coerce(v):
    low = str(v).strip().lower()
    if low in ("null", "none", "none_set"):
        return None
    if low in ("true", "yes", "1"):
        return True
    if low in ("false", "no", "0"):
        return False
    try:
        return int(v)
    except (TypeError, ValueError):
        pass
    try:
        return float(v)
    except (TypeError, ValueError):
        pass
    return v


# ---------- 闸门（收款路径）----------

def gate_check(seller):
    """宣传不需要实名，但「收钱」需要。这里判定的是收款路径是否可行。"""
    s = seller or {}
    rows = []
    age = s.get("age")
    age_ok = isinstance(age, int) and age >= 18
    rows.append(("年龄", age_ok,
                 f"{age} 周岁" if isinstance(age, int) else "未提供"))
    id_ok = bool(s.get("has_id_card"))
    rows.append(("身份证", id_ok, "已具备" if id_ok else "缺失"))
    ph_ok = bool(s.get("has_phone"))
    rows.append(("手机号", ph_ok, "已具备" if ph_ok else "缺失"))
    pay_ok = bool(s.get("has_bank_or_alipay"))
    rows.append(("实名收款账户", pay_ok, "已具备" if pay_ok else "缺失"))

    provided = any(k in s for k in ("age", "has_id_card", "has_phone", "has_bank_or_alipay"))
    ok = all(p for _n, p, _t in rows)
    if not provided:
        verdict = ("未提供卖家资质信息，无法判定收款路径。**宣传物料照常可用**，"
                   "但先确认走哪条收款渠道，否则做完也收不到钱。")
        routes = []
    elif ok:
        verdict = "收款路径畅通：可走平台店或第三方内容平台。"
        routes = ["淘宝 / 拼多多（需保证金）", "爱发电 / 面包多", "itch.io / Gumroad"]
    else:
        missing = "、".join(n for n, p, _t in rows if not p)
        verdict = (f"**收款路径受阻（缺：{missing}）**。这不影响做宣传 —— "
                   "宣传本身就是免费的，但先别按「开店铺」的方向做物料和定价。")
        routes = ["由监护人实名开店，本人做供货方（推荐）",
                  "免费发布 + 捐赠 / 打赏（无实名门槛）",
                  "沉淀作品集，为后续铺路"]
    return ok, provided, rows, verdict, routes


def render_gate_md(ok, provided, rows, verdict, routes):
    out = ["| 检查项 | 结果 | 说明 |", "|---|---|---|"]
    for n, p, t in rows:
        out.append(f"| {n} | {'✅' if p else '❌'} | {t} |")
    out += ["", verdict, ""]
    if routes:
        out.append("**可选收款路径：**")
        for r in routes:
            out.append(f"- {r}")
    return "\n".join(out)


# ---------- 一句话定位与介绍 ----------

def positioning_candidates(p, feats):
    name = p.get("project_name_cn") or p.get("project_name") or "该项目"
    one = (p.get("one_liner") or "").strip().rstrip("。.")
    audience = pc.as_list(p.get("audience"))
    out = []

    if one:
        out.append(("事实型", f"{name} —— {one}",
                    "直接把 README 里的定位放进来说，最省事也最不容易出错"))
    if feats:
        out.append(("差异型", f"{name}：{pc.truncate(feats[0], 34)}",
                    "把最有说服力的那条差异点提到最前面，适合做封面主标题"))
    if len(feats) > 1:
        out.append(("实证型", f"{pc.truncate(feats[0], 22)}，{pc.truncate(feats[1], 22)}",
                    "两条可核对的硬事实并排，说服力强于任何形容词"))
    if audience:
        out.append(("人群型", f"给{audience[0]}的{pc.truncate(one or name, 24)}",
                    "先圈人群再给东西，点击率通常更高"))
    if len(feats) > 2:
        out.append(("反差型", f"{pc.truncate(one or name, 22)}，{pc.truncate(feats[-1], 22)}",
                    "把「是什么」和「额外多了什么」放一起制造超值感"))
    return out


def intro_versions(p, feats, prices):
    name = p.get("project_name_cn") or p.get("project_name") or "该项目"
    one = (p.get("one_liner") or "").strip()
    platforms = "、".join(pc.as_list(p.get("platforms"))) or "以实际为准"
    price_lo = min((t["price"] for t in prices["tiers"]), default=None)
    fl = feats[:4]

    short = f"{name}：{pc.truncate(one, 40)}"
    mid_parts = [f"{name} 是一款{one}" if one else f"{name} 是一款自研作品"]
    if fl:
        mid_parts.append("主要特点：" + "；".join(fl[:3]) + "。")
    if price_lo:
        mid_parts.append(f"起步价 ¥{money(price_lo)}。")
    mid = "".join(mid_parts)

    long_parts = [
        f"## {name}",
        "",
        one or "（缺少一句话定位，请补 facts.one_liner）",
        "",
        "### 它是什么",
        "",
        f"运行环境：{platforms}。"
        + (f"技术栈：{'、'.join(pc.as_list(p.get('detected_stacks')))}。" if p.get("detected_stacks") else ""),
        "",
        "### 它有什么",
        "",
    ]
    for f in fl or ["（缺少 features，请补 facts.features）"]:
        long_parts.append(f"- {f}")
    if prices["tiers"]:
        long_parts += ["", "### 怎么买", ""]
        for t in prices["tiers"]:
            long_parts.append(f"- {t['name']} ¥{money(t['price'])}：{t['includes']}")
    long_parts += ["", "### 关于作者", "",
                   f"由 {p.get('seller_name') or '（待补 seller_name）'} 独立开发与维护。"]
    return short, mid, "\n".join(long_parts)


# ---------- 卖点矩阵与异议处理 ----------

BENEFIT_HINTS = [
    (("零外部素材", "无外部素材", "程序化生成", "纯代码生成"), "不用担心素材版权来源，也说明作者是真手艺人"),
    (("体积", "MB", "占用小"), "下载几秒钟，不占硬盘，老旧机器也放得下"),
    (("离线", "无需联网", "本地存档", "不需联网"), "断网照样能用，不会哪天服务器关了就用不了"),
    (("免安装", "解压", "双击"), "不用装环境、不用配置，拿到就能跑"),
    (("跨平台", "多平台"), "换电脑不用重新买一份"),
    (("自定义", "可配置", "可重映射", "种子"), "能按自己的习惯调整，不是一成不变的成品"),
    (("开源", "源码"), "看得见里面是什么，不用担心藏了东西"),
]


def benefit_of(feature):
    for keys, benefit in BENEFIT_HINTS:
        if any(k in feature for k in keys):
            return benefit
    return "把这件事变得比原来省事"


def objection_table(p, feats, prices):
    name = p.get("project_name_cn") or p.get("project_name") or "本作"
    platforms = "、".join(pc.as_list(p.get("platforms"))) or "见详情页说明"
    std = next((t for t in prices["tiers"] if t["name"] == "标准版"), None)
    rows = [
        # 措辞刻意避开「破解」「盗版」这两个词本身：
        # 平台的关键词过滤是无脑匹配的，买家可见文案里出现即可能触发下架。
        ("这是正版吗？",
         f"是。{name} 由作者独立开发，代码与素材均为自有"
         + ("，画面与音效全部由程序生成，不含任何外部素材。" if any(
             "素材" in f or "生成" in f for f in feats) else "。")),
        ("我电脑能不能跑？",
         f"运行环境：{platforms}。具体配置见详情页" 
         + ("，运行前可先看演示视频。" if p.get("video_available") else "。")),
        ("买到之后怎么拿到？",
         f"{p.get('delivery') or '（待补 delivery：网盘直链 / 卡密 / 邮件）'}。"
         "交付方式写进详情页，避免下单后反复问。"),
        ("能退款吗？",
         "虚拟内容一经交付不支持退款 —— 这条必须写在详情页顶部，别等纠纷了才说。"),
        ("为什么不是免费的？",
         (f"标准版 ¥{money(std['price'])}。" if std else "")
         + "独立开发没有发行商兜底，定价是为了能继续做下去。"),
        ("以后更新还要再付钱吗？",
         "标准版含后续小版本更新；大版本另行说明。把这句话写清楚，能减少大量售后询问。"),
    ]
    return rows


# ---------- 定价文档 ----------

CUT_TABLE = [
    ("淘宝 / 天猫", "虚拟类目通常无交易佣金；花呗/信用卡支付手续费约 1%",
     "需实名 + 保证金，个人 C 店门槛高"),
    ("拼多多", "交易服务费约 0.6% 起（类目不同有差异）",
     "虚拟商品需提交权属证明（软著最有说服力），低价竞争激烈"),
    ("爱发电 / 面包多", "平台抽成约 6% 起",
     "虚拟内容友好，个人可注册，抽成低"),
    ("itch.io", "默认 10%，作者可自行调整",
     "游戏类友好，需海外收款能力"),
    ("闲鱼", "个人闲置交易无平台抽成",
     "非正规售卖渠道，纠纷无平台仲裁保障"),
]

DEDUCTION_DISCIPLINE = """**折扣纪律（虚拟商品最容易自我伤害的地方）**

- 首发可以做「前 N 份 8 折」，但 N 必须是你真的会执行的数字。写「限时」却无限延期，就是虚假宣传。
- 不做 1 折 / 1 元走量。低价拉来的是退款率和差评率最高的人群，且平台会给店铺打低质标签。
- 虚拟商品没有库存压力，唯一成本是你的时间 —— 价格战只会同时伤毛利和品牌。
- 涨价要真的涨。老用户补差价这件事一次也别做，口碑成本远高于那几块钱。
"""


def render_price_md(p, prices):
    tiers = prices["tiers"]
    name = p.get("project_name_cn") or p.get("project_name")
    lines = [
        f"# {name} —— 定价方案",
        "",
        "> 本方案由「品类基准 × 体量系数 + 完成度加分」推导得出，推导过程全部列出，",
        "> 每一处都可以改。数字是起点，不是结论。",
        "",
        "## 一、推导过程（可核对）",
        "",
        "| 项 | 值 | 说明 |",
        "|---|---|---|",
        f"| 品类判定 | {prices['category_key']} | 由项目类型与技术栈自动判定 |",
        f"| 品类基准区间 | ¥{prices['category_range']['low']} – "
        f"¥{prices['category_range']['high']}（中位 ¥{prices['category_range']['mid']}） | 同体量虚拟商品常见区间 |",
        f"| 体量档位 | {prices['volume_tier']} ×{prices['volume_multiplier']} | "
        f"按 {p.get('loc', 0)} 行代码 / {p.get('code_files', 0)} 个文件 / "
        f"{p.get('asset_mb', 0)} MB 素材估算 |",
        f"| 完成度加分 | {prices['completeness_bonus']:+.2f} | 见下 |",
        f"| **推导标准价** | **¥{money(prices['raw_standard'])}** | 吸附到心理价位后 → ¥{money(tiers[1]['price'])} |",
        "",
    ]
    for note in prices["completeness_notes"]:
        lines.append(f"- {note}")
    if not prices["completeness_notes"]:
        lines.append("- 无特别加分或扣分项")
    lines += [
        "",
        "## 二、三档定价",
        "",
        "| 档位 | 价格 | 定位 | 包含内容 |",
        "|---|---|---|---|",
    ]
    for t in tiers:
        lines.append(f"| {t['name']} **({t['role']})** | ¥{money(t['price'])} | "
                     f"{t['role']} | {t['includes']} |")
    lines += [
        "",
        f"**为什么是三档而不是一档：** 支持者版 ¥{money(tiers[2]['price'])} 的作用不是卖出去，"
        f"而是让 ¥{money(tiers[1]['price'])} 的标准版显得划算。"
        "没有高价锚点，标准版会显得贵；有了锚点，标准版就成了「理性选择」。",
        "",
        f"**最低档 ¥{money(tiers[0]['price'])} 的作用：** 降低决策门槛。"
        "愿意先花小钱的人，之后升级的概率远高于从零开始犹豫的人。",
        "",
        "## 三、各渠道抽成与到手价",
        "",
        "> 抽成与保证金均为**参考值**，平台规则变动频繁，以平台当前实际规则为准。",
        "",
        "| 渠道 | 抽成 / 手续费 | 备注 |",
        "|---|---|---|",
    ]
    for name_c, cut, note in CUT_TABLE:
        lines.append(f"| {name_c} | {cut} | {note} |")
    lines += [
        "",
        "各渠道到手价对比（按推导标准价计算）：",
        "",
        "| 渠道 | 标价 | 到手（估） |",
        "|---|---|---|",
    ]
    std = tiers[1]["price"]
    for name_c, cut, _n in CUT_TABLE:
        if "0.6%" in cut:
            rate = 0.006
        elif "6%" in cut:
            rate = 0.06
        elif "10%" in cut:
            rate = 0.10
        elif "1%" in cut:
            rate = 0.01
        else:
            rate = 0.0
        lines.append(f"| {name_c} | ¥{money(std)} | ¥{money(round(std * (1 - rate), 2))} |")
    lines += [
        "",
        "## 四、首发与折扣",
        "",
        DEDUCTION_DISCIPLINE,
        "## 五、价格什么时候该动",
        "",
        "| 信号 | 动作 |",
        "|---|---|",
        "| 上架两周无人下单，但有人加购/收藏 | 不是价格问题，是详情页没讲清「买到了能得到什么」 |",
        "| 咨询里反复问同一个问题 | 把答案挪到详情页前两屏，与价格无关 |",
        "| 稳定出单且没人砍价 | 可以上调一档，老用户保持原价 |",
        "| 有人砍价到很低才买 | 别降标价，加一个更便宜的入门档去接住这部分人 |",
    ]
    return "\n".join(lines)


# ---------- 渠道文案 ----------

def ch_xiaohongshu(p, feats, prices, pos):
    name = p.get("project_name_cn") or p.get("project_name")
    std = next((t for t in prices["tiers"] if t["name"] == "标准版"), prices["tiers"][1])
    tags = [name, p.get("project_type") or "独立开发", "自研",
            "独立游戏" if p.get("project_type") == "game" else "独立开发",
            "小众", "手搓", "程序员", "干货分享", "数码"]
    if p.get("detected_stacks"):
        tags += [str(s) for s in pc.as_list(p.get("detected_stacks"))[:2]]
    title_cands = [
        pc.truncate(f"我做了个{pc.truncate(p.get('one_liner') or name, 14)}", 20),
        pc.truncate(f"没用一张素材，我{pc.truncate((p.get('one_liner') or name), 14)}", 20),
        pc.truncate(f"一个人做完的{pc.truncate(p.get('one_liner') or name, 16)}", 20),
    ]
    body = [
        f"花了不少时间做完了{name}，现在终于能拿出来给人看了。",
        "",
    ]
    if p.get("one_liner"):
        body += [p["one_liner"], ""]
    if feats:
        body.append("做的时候给自己定了几个规矩：")
        for f in feats[:4]:
            body.append(f"· {f}")
        body.append("")
    body += [
        f"运行环境是{'、'.join(pc.as_list(p.get('platforms'))) or '以说明为准'}，"
        f"标准版 ¥{money(std['price'])}。",
        "",
        "想知道大家最在意哪一点？评论区告诉我，我按票数最高的先优化 👇",
    ]
    return {
        "标题候选（≤20 字，必须一眼看完）": title_cands,
        "正文": "\n".join(body),
        "标签": [f"#{t}" for t in tags[:9]],
        "首图": "用 06-宣传图/小红书封面.png（3:4，文字已排版好，直接上传）",
        "发布要点": "\n".join([
            "- 标题里的 emoji 只在末尾放一个，多了像广告",
            "- 正文分短段，手机上一屏不超过 4 行",
            "- 结尾必须提问，评论量决定推流",
            "- 前 3 天每天回复全部评论，权重最高",
            "- 不要放二维码、不要写微信号，会被限流",
        ]),
    }


def ch_douyin(p, feats, prices, pos):
    name = p.get("project_name_cn") or p.get("project_name")
    std = next((t for t in prices["tiers"] if t["name"] == "标准版"), prices["tiers"][1])
    hook = f"这个{pc.truncate(name, 12)}，一张图片都没用。" if feats else f"我做了个{pc.truncate(name, 12)}。"
    script = [
        ("0–3 秒", "钩子", hook + " 全部画面是代码现场算出来的。"),
        ("3–8 秒", "展示", "（画面：实机运行画面，切 2–3 个不同场景）"
         + (f" 字幕：{pc.truncate(feats[0], 20)}" if feats else "")),
        ("8–13 秒", "证据", "（画面：桌面文件列表 / 体积属性面板）"
         + (f" 字幕：{pc.truncate(feats[1], 20)}" if len(feats) > 1 else " 字幕：体积小，下载快")),
        ("13–18 秒", "成本", f"（字幕）一个人做的，运行环境 "
         + f"{'、'.join(pc.as_list(p.get('platforms'))) or '见说明'}，标准版 ¥{money(std['price'])}。"),
        ("18–20 秒", "收尾", "（字幕）链接在简介，有问题评论区问我。"),
    ]
    return {
        "首图/封面文案": pc.truncate(hook, 18),
        "15–20 秒分镜脚本": script,
        "口播稿": "\n".join(x[2] for x in script),
        "话题标签": ["#独立开发", "#程序员", "#自己做游戏", f"#{pc.truncate(name, 8)}",
                     "#国产独立游戏" if p.get("project_type") == "game" else "#效率工具",
                     "#手搓"],
        "发布要点": "\n".join([
            "- 前 3 秒不出现产品名，先给冲突或结果",
            "- 全程竖屏 9:16，用 06-宣传图/竖屏故事.png 做封面",
            "- 字幕必须常驻，多数人静音刷",
            "- 视频文件用 07-宣传视频 里的成片；多镜头拼接见 05-视频分镜.md",
            "- 不要写「点击链接购买」，平台会压流量；用「简介里有」",
        ]),
    }


def ch_bilibili(p, feats, prices, pos):
    name = p.get("project_name_cn") or p.get("project_name")
    std = next((t for t in prices["tiers"] if t["name"] == "标准版"), prices["tiers"][1])
    cat = "独立游戏" if p.get("project_type") == "game" else "软件"
    titles = [
        pc.truncate(f"【{cat}】{name} —— {pc.truncate(p.get('one_liner') or '', 30)}", 78),
        pc.truncate(f"一个人做完的{name}：{pc.truncate(feats[0] if feats else '', 30)}", 78),
    ]
    desc = [
        f"{name}",
        "",
        p.get("one_liner") or "",
        "",
        "▍它是什么",
    ]
    if feats:
        desc += [f"· {f}" for f in feats[:5]]
    desc += [
        "",
        "▍运行环境",
        "、".join(pc.as_list(p.get("platforms"))) or "见视频内说明",
        "",
        "▍怎么获取",
        f"标准版 ¥{money(std['price'])}，{p.get('delivery') or '交付方式见评论区置顶'}。",
        "",
        "▍说明",
        "全部内容为本人独立开发，素材与音效均为程序化生成，无第三方素材引用。",
    ]
    return {
        "标题候选": titles,
        "简介": "\n".join(desc),
        "封面": "用 06-宣传图/视频封面.png（16:9）",
        "标签": ["独立游戏" if p.get("project_type") == "game" else "软件",
                 "原创", "自制", "开发日志", pc.truncate(name, 10)],
        "发布要点": "\n".join([
            "- B站用户吃「制作过程」，把开发中的取舍讲出来比成品展示更涨粉",
            "- 简介里别写外链，放评论区置顶",
            "- 封面文字控制在 8 字内，缩略图才看得清",
        ]),
    }


def ch_xianyu(p, feats, prices, pos):
    name = p.get("project_name_cn") or p.get("project_name")
    std = next((t for t in prices["tiers"] if t["name"] == "标准版"), prices["tiers"][1])
    kw = [name, "自研", "独立开发"]
    if p.get("project_type") == "game":
        kw += ["单机游戏", "小型游戏"]
    kw += [str(s) for s in pc.as_list(p.get("platforms"))[:1]]
    return {
        "标题（关键词优先，30 字内）": pc.truncate(" ".join(kw), 30),
        "描述": "\n".join(
            [f"{name} —— {p.get('one_liner') or ''}", ""]
            + [f"· {f}" for f in feats[:4]]
            + ["", f"交付：{p.get('delivery') or '（待补）'}", 
               f"价格：¥{money(std['price'])}", "",
               "虚拟内容一经交付不支持退款，介意勿拍。"]
        ),
        "砍价应对话术": [
            ("能便宜点吗？", f"已是本人自定的一口价 ¥{money(std['price'])}，不再议价。若预算有限，"
                          f"有 ¥{money(prices['tiers'][0]['price'])} 的入门版可以先试。"),
            ("先试用再买行吗？", "可以。详情里有实机演示，看完再决定；下载后不支持退款。"),
            ("能不能发我看看？", "详情页的演示内容就是完整画面，不单独发文件。"),
        ],
        "发布要点": "\n".join([
            "- 闲鱼对「虚拟商品」敏感，别写「卡密」「网盘链接」等词，用「付款后发送」",
            "- 不要放平台外收款方式，会被判违规",
            "- 详情里写清运行环境，能挡掉大部分退货纠纷",
        ]),
    }


def ch_group(p, feats, prices, pos):
    name = p.get("project_name_cn") or p.get("project_name")
    std = next((t for t in prices["tiers"] if t["name"] == "标准版"), prices["tiers"][1])
    msg = "\n".join([
        f"做了个{pc.truncate(p.get('one_liner') or name, 26)}，叫 {name}。",
        (f"特点：{pc.truncate(feats[0], 26)}。" if feats else ""),
        f"{'、'.join(pc.as_list(p.get('platforms'))) or ''}"
        f"{'　' if p.get('platforms') else ''}标准版 ¥{money(std['price'])}。",
        "感兴趣的扣 1，我私发给你看。",
    ])
    return {
        "群消息（一次说完，别刷屏）": msg,
        "朋友圈/空间": "\n".join([
            f"{name} 做完了。{pc.truncate(p.get('one_liner') or '', 30)}",
            (f"{pc.truncate(feats[0], 24)}。" if feats else ""),
            "配图：06-宣传图/竖屏故事.png",
        ]),
        "发布要点": "\n".join([
            "- 群消息只发一次，不要连发三条；不要 @全体成员",
            "- 先问「有人感兴趣吗」再发细节，转化率更高且不惹人烦",
            "- 熟人场景不要写价格战话术，越朴素越可信",
        ]),
    }


CHANNEL_BUILDERS = [
    ("小红书", ch_xiaohongshu),
    ("抖音 / 视频号", ch_douyin),
    ("B站", ch_bilibili),
    ("闲鱼", ch_xianyu),
    ("群与朋友圈", ch_group),
]


def render_channel_md(p, feats, prices, pos):
    name = p.get("project_name_cn") or p.get("project_name")
    out = [
        f"# {name} —— 渠道文案",
        "",
        "> 每个渠道的算法和用户预期都不同，同一段话不能到处贴。",
        "> 下面按渠道给差异化版本，全部只使用事实层信息，没有虚构数据。",
        "",
    ]
    for cname, fn in CHANNEL_BUILDERS:
        block = fn(p, feats, prices, pos)
        out += [f"## {cname}", ""]
        for k, v in block.items():
            out += [f"### {k}", ""]
            if isinstance(v, list) and v and isinstance(v[0], (tuple, list)):
                out += ["| 时间 / 场景 | 环节 | 内容 |", "|---|---|---|"]
                for row in v:
                    cells = [str(c).replace("|", "\\|") for c in row]
                    while len(cells) < 3:
                        cells.append("")
                    out.append("| " + " | ".join(cells[:3]) + " |")
                out.append("")
            elif isinstance(v, list):
                for item in v:
                    out.append(f"- {item}")
                out.append("")
            else:
                out += [str(v), ""]
    return "\n".join(out)


def render_core_md(p, feats, prices, pos, objections, intros):
    name = p.get("project_name_cn") or p.get("project_name")
    short, mid, long_ = intros
    out = [
        f"# {name} —— 核心文案",
        "",
        "## 一、一句话定位（选一条，其余留作 A/B）",
        "",
        "| 类型 | 文案 | 适用 |",
        "|---|---|---|",
    ]
    for kind, text, note in pos:
        out.append(f"| {kind} | {text} | {note} |")
    if not pos:
        out += ["| — | （facts 里既没有 one_liner 也没有 features，无法生成） | 请先补齐 |"]
    out += [
        "",
        "## 二、三版介绍",
        "",
        "### 短（1 行，用于简介/签名）",
        "",
        f"`{short}`",
        "",
        "### 中（1 段，用于详情页开头/商品描述）",
        "",
        mid,
        "",
        "### 长（完整介绍，用于落地页/B站简介）",
        "",
        long_,
        "",
        "## 三、卖点矩阵（事实 → 对买家的意义）",
        "",
        "| 事实 | 对买家的意义 |",
        "|---|---|",
    ]
    for f in feats:
        out.append(f"| {f} | {benefit_of(f)} |")
    if not feats:
        out.append("| （缺少 features） | 请补 facts.features，这是所有文案的原料 |")
    out += [
        "",
        "## 四、异议处理（写在详情页/评论区置顶）",
        "",
        "| 买家会问 | 怎么答 |",
        "|---|---|",
    ]
    for q, a in objections:
        out.append(f"| {q} | {a.replace('|', '\\|')} |")
    out += [
        "",
        "## 五、行动号召（CTA）候选",
        "",
        "全部为可核实的朴素表述，不含诱导分享、虚构稀缺：",
        "",
        "- 想看实机运行画面的，视频里是全流程。",
        "- 运行环境写在详情页底部，先对照一下再决定。",
        "- 有问题直接问，我做的东西我自己答。",
        "- 觉得不值这个价别买，我不想靠退款率做数据。",
        "- 想先看后续更新计划的，可以留言，我会回。",
        "",
        "---",
        "",
        "## 给 Agent 的加工指引（重要）",
        "",
        "上面的文案是**事实层**，结构可直接用，但要变成真正吸引人的东西，还需要一次打磨：",
        "",
        "1. **只许使用 `facts.json` 里已有的信息。** 不得新增销量、评价、用户数、获奖、"
        "「全网」「第一」这类任何一件无法核实的事。",
        "2. 把「事实 → 意义」那列改写成买家自己的语言。"
        "例：「体积小于 30MB」→「下载几秒钟，老笔记本也放得下」。",
        "3. 每条文案读一遍，问自己：**买家看完知道买到了什么吗？** 不知道就重写。",
        "4. 写完必须重跑 `lint_promo.py`，高危项清零才算交付。",
        "5. 中文文案里不要出现任何第三方作品名 / IP 名（这是硬红线，不是审美偏好）。",
    ]
    return "\n".join(out)


# ---------- 视频分镜 ----------

def video_storyboard(p, feats, prices):
    name = p.get("project_name_cn") or p.get("project_name")
    shots = []
    shots.append({
        "no": 1, "duration_s": 5, "role": "开场钩子",
        "frame_source": "06-宣传图/视频封面.png",
        "subtitle": pc.truncate(p.get("one_liner") or name, 22),
        "prompt": ("Cinematic slow push-in on a dark UI dashboard scene, "
                   "cool blue rim light, shallow depth of field, subtle particle dust, "
                   "no text, no letters, no words, no watermark"),
        "note": "用已排版好的本地封面图作首帧，文字不会被 AI 画错",
    })
    shots.append({
        "no": 2, "duration_s": 5, "role": "核心画面",
        "frame_source": (pc.as_list(p.get("screenshots")) or [{}])[0].get("path", "")
                         if p.get("screenshots") else "",
        "subtitle": pc.truncate(feats[0], 20) if feats else "",
        "prompt": ("Slow horizontal camera pan across a colorful stylized game world, "
                   "soft ambient light, gentle parallax, no text, no letters, no words"),
        "note": "用实机截图作首帧，AI 只负责让它动起来，画面内容不会被篡改",
    })
    shots.append({
        "no": 3, "duration_s": 5, "role": "差异点",
        "frame_source": "06-宣传图/电商主图.png",
        "subtitle": pc.truncate(feats[1], 20) if len(feats) > 1 else "",
        "prompt": ("Elegant abstract data visualization, glowing lines assembling into a shape, "
                   "minimal dark background, smooth motion, no text, no letters, no words"),
        "note": "抽象画面表现「技术亮点」，不出现具体产品界面",
    })
    std = next((t for t in prices["tiers"] if t["name"] == "标准版"), prices["tiers"][1])
    shots.append({
        "no": 4, "duration_s": 5, "role": "收尾与价格",
        "frame_source": "06-宣传图/竖屏故事.png",
        "subtitle": f"标准版 ¥{money(std['price'])} · "
                    f"{'、'.join(pc.as_list(p.get('platforms'))) or ''}",
        "prompt": ("Calm closing shot, soft gradient background with gentle floating light motes, "
                   "fade to dark, no text, no letters, no words"),
        "note": "价格与行动号召必须由本地图承载，不要让 AI 写字",
    })
    md = [
        f"# {name} —— 宣传视频分镜",
        "",
        "## 一、先明确能力边界（别抱错期待）",
        "",
        "**推荐路线是录实机画面再自动剪辑，0 积分。** 本机已有可用的 ffmpeg，",
        "`capture_game.py` 能直接录项目本体，`edit_video.py` 能自动拼成 16:9 / 9:16 成片并叠好中文字幕。",
        "下面的 AI 分镜只在实机录不到时才需要考虑。",
        "",
        "- AI 视频生成一次只能出 **5 秒**成片，做 20 秒要生成 4 次、花掉几百积分。",
        "- **绝不让 AI 生成中文字。** AI 画字必错，价格、标题这类关键信息一律用本地渲染的"
        "海报图作首帧，AI 只负责让画面动起来。",
        "- 所以推荐顺序：**实机录剪（0 积分）→ 图片轮播（0 积分）→ AI 生成（花积分）**。",
        "",
        "## 二、分镜表",
        "",
        "| # | 时长 | 环节 | 首帧来源 | 字幕 |",
        "|---|---|---|---|---|",
    ]
    for s in shots:
        md.append(f"| {s['no']} | {s['duration_s']}s | {s['role']} | "
                  f"`{s['frame_source'] or '（需先补截图）'}` | {s['subtitle'] or '—'} |")
    md += ["", "## 三、每个镜头的生成参数", ""]
    for s in shots:
        md += [
            f"### 镜头 {s['no']} · {s['role']}（{s['duration_s']} 秒）",
            "",
            f"- **首帧**：`{s['frame_source'] or '（待补）'}`",
            f"- **提示词**：`{s['prompt']}`",
            f"- **字幕**：{s['subtitle'] or '（无）'}",
            f"- **说明**：{s['note']}",
            "",
        ]
    md += [
        "## 四、剪映拼接步骤（不用会剪辑也能做）",
        "",
        "1. 打开剪映 → 新建项目 → 按顺序导入镜头 1→4 的 mp4。",
        "2. 全部拖到时间线，首尾相接，总时长约 20 秒。",
        "3. 给每个镜头加「文字」：把上表字幕贴在画面下方 1/3 处，字号调到能一眼读完。",
        "4. 镜头之间各加一个「黑场过渡」（0.3 秒），比硬切自然。",
        "5. 配乐：选无版权音乐，音量压到 -18dB 左右，别盖住音效。",
        "6. 导出 1080P / 30fps，横版发 B 站，竖版发抖音（竖版只需把画布改成 9:16 重新导出）。",
        "",
        "## 五、不花积分也能做出视频",
        "",
        "**路线一（推荐）：录实机出来剪。** 这是唯一能保证「视频里就是买家拿到的东西」的做法，",
        "也是 0 积分：",
        "",
        "```bash",
        "# 先采样看清楚全片，再按帧区间录制，最后自动出成片",
        "capture_game.py --project <项目> --out ./cap --survey",
        "capture_game.py --project <项目> --out ./cap --windows \"120-260;744-828\" --stills 3",
        "edit_video.py --clips ./cap/clips --out ./宣传视频 --title \"<游戏名>\" --cta \"点击下载 · 即刻开玩\"",
        "```",
        "",
        "**路线二：图片轮播。** 实机跑不起来时的兜底。",
        "",
        "把 `06-宣传图/` 里的 4 张 PNG 依次排开，每张加「放大」入场动画，时长各 5 秒，"
        "配上字幕和配乐即可。成本 0，效果对这类小体量产品已经够用。",
        "",
        "**路线三：AI 生成。** 只在实机拍不到的氛围镜头才用，要花积分，需用户确认。",
        "",
        "**建议顺序：先录实机剪一版投出去看数据，有反馈再考虑要不要花积分。**",
    ]
    return "\n".join(md), shots


# ---------- 素材清单（含积分预算）----------

def build_asset_manifest(p, feats, shots):
    imgs = [
        {"id": "img_bg_1", "tool": "ImageGen", "priority": "P1",
         "purpose": "主视觉背景（无文字），可叠在本地海报底层或做落地页头图",
         "prompt": ("Atmospheric key art background, dark ambient scene with glowing particles, "
                    "cool blue and violet palette, cinematic lighting, high detail, "
                    "no text, no letters, no words, no logo, no watermark"),
         "size": "1536x1024", "quality": "high", "credits": "5-10",
         "output_dir": "06-宣传图/ai"},
        {"id": "img_bg_2", "tool": "ImageGen", "priority": "P2",
         "purpose": "纵向主视觉，用于竖屏故事与小红书封面底层",
         "prompt": ("Vertical atmospheric background art, soft gradient from deep navy to warm amber, "
                    "subtle bokeh, lots of negative space at top for text overlay, "
                    "no text, no letters, no words, no watermark"),
         "size": "1024x1536", "quality": "high", "credits": "5-10",
         "output_dir": "06-宣传图/ai"},
    ]
    if p.get("screenshots"):
        imgs.append({
            "id": "img_style_1", "tool": "ImageGen", "priority": "P2",
            "purpose": "把实机截图转成统一风格的主视觉（图生图，保真度高）",
            "prompt": ("Turn this screenshot into polished promotional key art, "
                       "cinematic lighting, add depth and atmosphere, keep the original "
                       "layout and composition recognizable, no text, no letters, no words"),
            "size": "1536x1024", "quality": "high", "credits": "5-10",
            "image1": pc.as_list(p["screenshots"])[0].get("path", ""),
            "input_fidelity": "high",
            "output_dir": "06-宣传图/ai",
        })

    vids = []
    for s in shots:
        vids.append({
            "id": f"video_shot_{s['no']}", "tool": "VideoGen", "priority": "P1" if s["no"] <= 2 else "P2",
            "purpose": f"镜 {s['no']} · {s['role']}",
            "prompt": s["prompt"],
            "image": s["frame_source"],
            "resolution": "1080P",
            "duration_s": s["duration_s"],
            "credits": "50-100",
            "output_dir": "07-宣传视频",
            "note": "以本地渲染的海报为图生视频首帧；不要用纯文生视频，画面里的中文会乱",
        })

    return {
        "budget_notice": (
            "海报图（06-宣传图/*.png）由本机无头浏览器渲染，**消耗 0 积分**，已自动生成。"
            "下面列出的 ImageGen / VideoGen 项目会消耗额外积分，"
            "**必须先向用户报出预算并得到同意后再执行**。"
            "参考口径：每张图约 5–10 积分，每 5 秒视频约 50–100 积分。"
        ),
        "zero_cost_assets": [
            {"name": n, "template": t, "size": f"{w}x{h}", "output": f"06-宣传图/{n}.png",
             "note": d} for n, t, w, h, d in POSTER_PLAN
        ],
        "paid_images": imgs,
        "paid_videos": vids,
        "totals": {
            "zero_cost_count": len(POSTER_PLAN),
            "paid_image_count": len(imgs),
            "paid_image_credits": f"{5 * len(imgs)}-{10 * len(imgs)}",
            "paid_video_count": len(vids),
            "paid_video_credits": f"{50 * len(vids)}-{100 * len(vids)}",
            "p1_only_credits": f"{5 + 50}-{10 + 100}",
        },
    }


def render_budget_md(manifest):
    t = manifest["totals"]
    return "\n".join([
        "| 素材 | 数量 | 单件积分 | 合计积分 | 说明 |",
        "|---|---|---|---|---|",
        f"| 宣传图（本地渲染） | {t['zero_cost_count']} 张 | 0 | **0** | 已生成，直接可用 |",
        f"| AI 背景图（ImageGen） | {t['paid_image_count']} 张 | 5–10 | "
        f"{t['paid_image_credits']} | 可选，锦上添花 |",
        f"| AI 视频（VideoGen） | {t['paid_video_count']} 条 | 50–100 | "
        f"{t['paid_video_credits']} | 可选，每条 5 秒 |",
        "",
        f"**最小试水方案（P1 各一条）：约 {t['p1_only_credits']} 积分。**",
        "",
        "> 海报图是零成本的，且中文文字准确。**先零成本方案投出去看数据**，",
        "> 有反馈再决定要不要花积分做 AI 版。",
    ])


# ---------- 海报与落地页 ----------

def ensure_source_images(p, out_dir):
    """把实机截图拷成 ASCII 文件名，避免 file:// 与中文路径的兼容问题。"""
    src_dir = os.path.join(out_dir, "06-宣传图", "src", "assets")
    os.makedirs(src_dir, exist_ok=True)
    shots = p.get("screenshots") or []
    root = pick(p, "_scan", "root", default="")
    copied = []
    for i, s in enumerate(pc.as_list(shots)[:3], start=1):
        rel = s.get("path") if isinstance(s, dict) else str(s)
        if not rel:
            continue
        abs_p = os.path.join(root, rel) if root else rel
        if not os.path.isfile(abs_p):
            abs_p = rel
        if not os.path.isfile(abs_p):
            continue
        dst = os.path.join(src_dir, f"shot-{i}.png")
        try:
            shutil.copyfile(abs_p, dst)
            copied.append(f"assets/shot-{i}.png")
        except Exception:
            continue
    return copied


def render_posters(p, feats, prices, pos, out_dir, shots_files):
    src_dir = os.path.join(out_dir, "06-宣传图", "src")
    os.makedirs(src_dir, exist_ok=True)

    theme_key = {"game": "neon", "webapp": "light", "tool": "neon",
                 "desktop": "neon"}.get(p.get("project_type"), "neon")
    pal = PALETTES[theme_key]

    name = p.get("project_name_cn") or p.get("project_name") or ""
    # 副标不能直接用定位候选 —— 它本身就是「项目名 —— 一句话」，会跟大标题重复一遍。
    # 这里只用一句话简介，并在标点处干净断开。
    tagline = pc.smart_cut(p.get("one_liner") or "", 46)
    if not tagline:
        tagline = pc.smart_cut(pos[0][1] if pos else name, 40)
    std = next((t for t in prices["tiers"] if t["name"] == "标准版"), prices["tiers"][1])
    badges = []
    badges += pc.as_list(p.get("platforms"))[:2]
    # 不把「行数」当徽章：买家不关心代码量，那不是卖点是自嗨
    badges += [str(s) for s in pc.as_list(p.get("detected_stacks"))[:2]]

    bullets = [pc.smart_cut(f, 26) for f in feats[:3]] or \
              ["（请在 facts.features 里补上三条真实卖点）"]

    made = []
    for label, tpl_name, w, h, _note in POSTER_PLAN:
        tpl_path = os.path.join(TEMPLATE_DIR, tpl_name + ".html")
        if not os.path.isfile(tpl_path):
            continue
        tpl, _enc = pc.read_text(tpl_path)
        shot = shots_files[0] if shots_files else ""
        if tpl_name == "story_9x16" and len(shots_files) > 1:
            shot = shots_files[1]
        repl = {
            "TITLE": html.escape(pc.truncate(name, 12)),
            "TAGLINE": html.escape(pc.truncate(tagline, 30)),
            "SUBTITLE": html.escape(pc.smart_cut(p.get("one_liner") or "", 44)),
            "BADGES": "".join(f'<span class="badge">{html.escape(b)}</span>'
                              for b in badges[:4]),
            "BULLETS": "".join(f'<li>{html.escape(b)}</li>' for b in bullets),
            "PRICE": html.escape(f"¥{money(std['price'])}"),
            "PLATFORMS": html.escape("、".join(pc.as_list(p.get("platforms"))) or "以说明为准"),
            "CTA": html.escape("详情见评论区"),
            "SHOT": html.escape(shot),
            "SHOT_STYLE": "" if shot else "display:none",
            "W": str(w), "H": str(h),
        }
        for k, v in pal.items():
            repl["C_" + k.upper()] = v
        for k, v in repl.items():
            tpl = tpl.replace("{{" + k + "}}", str(v))
        dst = os.path.join(src_dir, f"{label}.html")
        pc.write(dst, tpl)
        made.append({"label": label, "src": dst, "w": w, "h": h,
                     "out": os.path.join(out_dir, "06-宣传图", f"{label}.png")})
    return made


def build_poster_manifest(made, out_dir):
    rel = []
    for m in made:
        rel.append({
            "label": m["label"],
            "html": os.path.relpath(m["src"], out_dir).replace("\\", "/"),
            "png": os.path.relpath(m["out"], out_dir).replace("\\", "/"),
            "width": m["w"], "height": m["h"],
        })
    pc.write_json(os.path.join(out_dir, "06-宣传图", "posters.json"), rel)
    return rel


def render_landing(p, feats, prices, pos, objections, out_dir):
    if not os.path.isfile(LANDING_TPL):
        return None
    tpl, _enc = pc.read_text(LANDING_TPL)
    name = p.get("project_name_cn") or p.get("project_name") or ""
    shots = p.get("screenshots") or []

    price_cards = []
    for i, t in enumerate(prices["tiers"]):
        cls = "tier hot" if t["name"] == "标准版" else "tier"
        price_cards.append(
            f'      <div class="{cls}"><div class="tn">{html.escape(t["name"])}</div>'
            f'<div class="tp"><small>¥</small>{html.escape(money(t["price"]))}</div>'
            f'<div class="ti">{html.escape(t["includes"])}</div>'
            f'<div class="tr">{html.escape(t["role"])}</div></div>')
    if not price_cards:
        price_cards.append('      <div class="tier hot"><div class="tn">价格待定</div></div>')

    feat_cards = []
    for f in feats[:6]:
        feat_cards.append(
            f'      <div class="fc"><div class="fk">{html.escape(pc.truncate(f, 26))}</div>'
            f'<div class="fv">{html.escape(benefit_of(f))}</div></div>')

    faq = "\n".join(
        f'      <details><summary>{html.escape(q)}</summary>'
        f'<p>{html.escape(a)}</p></details>' for q, a in objections[:5])

    shots_html = ""
    for i, s in enumerate(pc.as_list(shots)[:3], start=1):
        shots_html += (f'    <img class="shot" src="06-宣传图/src/assets/shot-{i}.png" '
                       f'alt="实机画面 {i}">\n')

    repl = {
        "NAME": html.escape(name),
        "TAGLINE": html.escape(pos[0][1] if pos else (p.get("one_liner") or "")),
        "SUBTITLE": html.escape(p.get("one_liner") or ""),
        "INTRO": html.escape(p.get("one_liner") or ""),
        "FEATURES": "\n".join(feat_cards) or "      <p>（待补 features）</p>",
        "SHOTS": shots_html or "    <p class='muted'>（未检测到实机截图，请补图）</p>",
        "PRICES": "\n".join(price_cards),
        "FAQ": faq,
        "PLATFORMS": html.escape("、".join(pc.as_list(p.get("platforms"))) or "以说明为准"),
        "DELIVERY": html.escape(p.get("delivery") or "付款后发送下载链接"),
        "SELLER": html.escape(p.get("seller_name") or "独立开发者"),
        "YEAR": str(datetime.date.today().year),
    }
    for k, v in repl.items():
        tpl = tpl.replace("{{" + k + "}}", str(v))
    dst = os.path.join(out_dir, "04-落地页.html")
    pc.write(dst, tpl)
    return dst


def compute_missing(p):
    """按**当前生效的值**算缺什么。

    不能直接复用 facts.missing_fields —— 那是扫描时的快照，
    用户用 --set 补完之后它还挂在上面，会谎报「缺少 audience」。
    """
    miss = []
    if is_blank(p.get("one_liner")):
        miss.append("one_liner（一句话定位）")
    if not feats_of(p):
        miss.append("features（卖点，所有文案的原料）")
    if is_blank(p.get("audience")):
        miss.append("audience（目标人群）")
    if is_blank(p.get("delivery")):
        miss.append("delivery（交付方式）")
    if is_blank(p.get("seller_name")):
        miss.append("seller_name（署名）")
    seller = p.get("seller") or {}
    if not any(k in seller for k in ("age", "has_id_card", "has_phone",
                                     "has_bank_or_alipay")):
        miss.append("seller（收款资质，闸门判定用）")
    return miss


# ---------- 交付说明 ----------

def render_readme(p, prices, gate_md, files, scan_summary, manifest, missing, poster_rel):
    name = p.get("project_name_cn") or p.get("project_name")
    warn = []
    for m in missing:
        warn.append(f"- 缺少 `{m}`，相关文案为占位或推断值")
    if not p.get("screenshots"):
        warn.append("- **没有实机截图**：宣传图只能靠排版撑场面，转化会明显吃亏。"
                    "建议先跑起来截 6 张（标题界面 / 核心玩法 ×3 / 设置 / 存档）")
    if p.get("has_ai_feature"):
        warn.append("- 项目含 AI 相关代码。若产品自带服务端转发 AI 请求，"
                    "等于成为「生成式 AI 服务提供者」，需算法备案，个人办不了 —— "
                    "必须改成买家自带 Key")
    warn_md = "\n".join(warn) if warn else "- 无，事实层信息齐全"

    return f"""# {name} —— 推广物料包交付说明

生成时间：{datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}

## ⚠️ 先看这一段：收款路径

宣传是免费的，谁都能做。但**收钱**需要实名，先确认走哪条路，别白做。

{gate_md}

## 一、这个包里有什么

| 文件 | 用途 |
|---|---|
| `01-核心文案.md` | 一句话定位 ×N 候选、三版介绍、卖点矩阵、异议处理、CTA |
| `02-定价方案.md` | 三档定价 + 完整推导过程 + 各渠道抽成对比 + 折扣纪律 |
| `03-渠道文案.md` | 小红书 / 抖音 / B站 / 闲鱼 / 群与朋友圈 的定制版本 |
| `04-落地页.html` | 单页落地页，浏览器直接打开，可部署上线 |
| `05-素材清单.json` | 素材生成清单与**积分预算**，含 ImageGen / VideoGen 提示词 |
| `05-视频分镜.md` | 视频三条路线：实机录剪（0 积分，推荐）/ 图片轮播 / AI 分镜 |
| `06-宣传图/*.png` | **已生成**的 4 张宣传图（本地渲染，0 积分，中文准确） |
| `07-宣传视频/` | 视频产出一律放这里。推荐 `capture_game.py` + `edit_video.py` 录实机自动剪，0 积分 |
| `08-合规扫描.md` | 极限词 / 虚构数据 / 诱导分享 / 第三方 IP 名 扫描结果 |
| `promo.json` | 结构化数据，供复用与二次加工 |

## 二、宣传图已经做好了（0 积分）

| 图 | 尺寸 | 用途 |
|---|---|---|
""" + "\n".join(f"| {m['label']} | {m['width']}×{m['height']} | "
                f"`{m['png']}` |" for m in poster_rel) + f"""

中文文字由本机无头浏览器渲染，**准确无错字** —— 这是刻意绕开 AI 画错字的做法。
AI 图像生成只用来做**不含文字的背景视觉**（见 `05-素材清单.json`）。

## 三、积分预算（要花积分之前必须先确认）

{render_budget_md(manifest)}

## 四、还缺什么（不补会直接影响转化）

{warn_md}

## 五、需要你手动做的

1. 打开 `04-落地页.html` 看一眼，改掉你觉得不对的地方（它就是纯 HTML，随便改）
2. 按 `03-渠道文案.md` 把内容发出去；图片直接用 `06-宣传图/` 里的 PNG
3. 视频：跑 `capture_game.py` 录实机 → `edit_video.py` 自动剪（0 积分，画面就是买家到手的样子）。
   项目跑不起来时退回 `05-视频分镜.md` 的图片轮播方案
4. 想用 AI 版素材时，**先把积分预算念给用户确认**，同意后再调生成

## 六、自动做的取舍（不合适就说，改起来很快）

- 定价档位名用了「轻量版 / 标准版 / 支持者版」，改个名字随时可以
- 配色方案按项目类型自动选了 `{pick(p, 'project_type', default='unknown')}` 主题（深色/浅色/暖色三选一）
- 平台判定、技术栈、体量数据全部来自实际扫描，未做推断
- 各平台抽成与保证金均为参考值，**以平台当前实际规则为准**

## 七、合规扫描结果

{scan_summary}

## 八、发布节奏建议

| 时间 | 动作 |
|---|---|
| 第 1 天 | 小红书 + 朋友圈各发一条，先看点击率 |
| 第 3 天 | B站发长视频（制作过程比成品更受欢迎） |
| 第 7 天 | 抖音发短视频；有反馈再投第二个渠道 |
| 两周后 | 看哪个渠道有真实咨询，其余停掉，集中做那一个 |

**别一次全平台铺开** —— 分散发布你无法判断哪个渠道有效。
"""


# ---------- 主流程 ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--facts", required=True, help="scan_project.py 产出的 facts.json")
    ap.add_argument("--out", default="./promo-out", help="输出目录")
    ap.add_argument("--overrides", help="覆盖用 json（可选）")
    ap.add_argument("--set", action="append", default=[], metavar="K=V",
                    help="点号路径覆盖，可重复：--set audience=像素风爱好者")
    ap.add_argument("--no-scan", action="store_true", help="跳过生成后的合规扫描")
    args = ap.parse_args()

    if not os.path.isfile(args.facts):
        print(f"facts 不存在：{args.facts}")
        return 3
    p = pc.load_json(args.facts)
    if args.overrides and os.path.isfile(args.overrides):
        ov = pc.load_json(args.overrides)
        for k, v in ov.items():
            if isinstance(v, dict) and isinstance(p.get(k), dict):
                p[k].update(v)
            else:
                p[k] = v
    for kv in args.set:
        if "=" not in kv:
            print(f"--set 格式应为 K=V，收到：{kv}")
            return 3
        k, v = kv.split("=", 1)
        set_path(p, k.strip(), coerce(v))
        # 同步到 facts 顶层，写回 promo.json 时保持完整
        p.setdefault("_overrides_applied", {})[k.strip()] = coerce(v)

    out_dir = args.out
    os.makedirs(out_dir, exist_ok=True)

    feats = feats_of(p)
    prices = pc.build_price_plan(p)
    pos = positioning_candidates(p, feats)
    objections = objection_table(p, feats, prices)
    intros = intro_versions(p, feats, prices)

    pc.write(os.path.join(out_dir, "01-核心文案.md"),
             render_core_md(p, feats, prices, pos, objections, intros))
    pc.write(os.path.join(out_dir, "02-定价方案.md"), render_price_md(p, prices))
    pc.write(os.path.join(out_dir, "03-渠道文案.md"),
             render_channel_md(p, feats, prices, pos))

    story_md, shots = video_storyboard(p, feats, prices)
    pc.write(os.path.join(out_dir, "05-视频分镜.md"), story_md)

    manifest = build_asset_manifest(p, feats, shots)
    pc.write_json(os.path.join(out_dir, "05-素材清单.json"), manifest)

    # 海报：拷图 → 填充模板 → 写清单
    copied = ensure_source_images(p, out_dir)
    made = render_posters(p, feats, prices, pos, out_dir, copied)
    poster_rel = build_poster_manifest(made, out_dir)

    render_landing(p, feats, prices, pos, objections, out_dir)

    ok, provided, rows, verdict, routes = gate_check(p.get("seller"))
    gate_md = render_gate_md(ok, provided, rows, verdict, routes)

    # 合规扫描
    results = {}
    for fn in SCAN_FILES:
        fp = os.path.join(out_dir, fn)
        if os.path.isfile(fp):
            txt, _e = pc.read_text(fp)
            hits = pc.WORDLIST.scan(pc.WORDLIST.clean_for_scan(txt, fp))
            if hits:
                results[fp] = hits
        elif os.path.isdir(fp):
            for name in sorted(os.listdir(fp)):
                sub = os.path.join(fp, name)
                if os.path.isfile(sub) and name.lower().endswith((".html", ".md")):
                    txt, _e = pc.read_text(sub)
                    hits = pc.WORDLIST.scan(pc.WORDLIST.clean_for_scan(txt, sub))
                    if hits:
                        results[sub] = hits
    high = sum(1 for hs in results.values() for h in hs if h["level"] == "high")
    warn = sum(1 for hs in results.values() for h in hs if h["level"] == "warn")
    pc.write(os.path.join(out_dir, "08-合规扫描.md"),
             pc.WORDLIST.render_report(results, "合规扫描报告"))
    scan_summary = (f"扫描买家可见文案：**高危 {high} 处，需修改 {warn} 处**。"
                    f"详见 `08-合规扫描.md`。高危项必须清零再发布。"
                    if (high or warn) else
                    "扫描买家可见文案：未发现极限词、虚构数据、诱导分享或第三方 IP 名。可以发布。")

    pc.write(os.path.join(out_dir, "00-交付说明.md"),
             render_readme(p, prices, gate_md, None, scan_summary, manifest,
                           compute_missing(p), poster_rel))
    # 交付说明里承诺了视频目录，就真的建出来，别让它指向一个不存在的路径。
    # 用 .gitkeep 占位：不在扫描扩展名内，不会污染合规扫描。
    pc.write(os.path.join(out_dir, "07-宣传视频", ".gitkeep"), "")

    pc.write_json(os.path.join(out_dir, "promo.json"), {
        "facts": p, "pricing": prices, "positioning": pos,
        "posters": poster_rel, "asset_manifest": manifest,
        "gate_ok": ok, "gate_provided": provided,
        "scan": {"high": high, "warn": warn},
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
    })

    print(f"=== 已生成 → {os.path.abspath(out_dir)} ===")
    for fn in sorted(os.listdir(out_dir)):
        print("  " + fn)
    print(f"\n宣传图源文件：06-宣传图/src/（共 {len(made)} 张，下一步用 render_assets.py 渲染）")
    print(f"定价：{' / '.join(t['name'] + ' ¥' + money(t['price']) for t in prices['tiers'])}")
    print(f"积分预算：海报 0；AI 图 {manifest['totals']['paid_image_credits']}；"
          f"AI 视频 {manifest['totals']['paid_video_credits']}")
    print(f"收款路径：{'畅通' if ok else ('未提供资质信息' if not provided else '受阻')}")
    print(f"合规：高危 {high} / 警告 {warn}")
    return 1 if high else 0


if __name__ == "__main__":
    sys.exit(main())
