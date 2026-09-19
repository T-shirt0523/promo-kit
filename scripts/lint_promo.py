# -*- coding: utf-8 -*-
"""推广物料合规扫描（可独立复检，也是发布前的最后一道闸）。

扫的是「买家能看到的东西」，不扫交付说明与报告自身（否则报告里引用的违规词会自我命中）。

用法:
    python lint_promo.py --dir ./promo-out
    python lint_promo.py --dir ./promo-out --no-write     # 只看结果不写报告
    python lint_promo.py --facts facts.json               # 顺带复查项目里的 IP 风险

退出码: 0 = 干净, 1 = 有高危项（不许发布）, 2 = 仅警告, 3 = 用法错误
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import promo_common as pc  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# 只扫买家可见物料。00-交付说明 与 08-合规扫描 是内部文档，排除。
SKIP_NAMES = {"00-交付说明.md", "08-合规扫描.md", "promo.json", "posters.json",
              "05-素材清单.json"}
SCAN_EXT = (".md", ".html", ".htm", ".txt")


def collect(target):
    """递归收集待扫描文件。素材清单里的提示词也算（它们会进 AI 生成内容）。"""
    files = []
    for root, dirs, names in os.walk(target):
        dirs[:] = [d for d in dirs if d not in (".render-tmp", "__pycache__")]
        for n in names:
            if n in SKIP_NAMES:
                continue
            if n.lower().endswith(SCAN_EXT):
                files.append(os.path.join(root, n))
    return sorted(files)


def scan_text_of(path):
    """HTML 先剥掉 style/script 与标签，否则 CSS 里的 100% 会假阳性。"""
    txt, _enc = pc.read_text(path)
    return pc.WORDLIST.clean_for_scan(txt, path)


def scan_dir(target, write_report=True):
    files = collect(target)
    results = {}
    for fp in files:
        try:
            hits = pc.WORDLIST.scan(scan_text_of(fp))
        except Exception:
            continue
        if hits:
            results[fp] = hits

    print(f"=== 合规扫描（{len(files)} 个文件，词库来源：{pc.WORDLIST.source}） ===")
    if not pc.ip_check_enabled():
        print("  [未启用] 第三方作品名名单为空，本次未做 IP 检查"
              "（cp assets/ip-watchlist.example.txt assets/ip-watchlist.txt 可启用）")
    if not results:
        if pc.ip_check_enabled():
            print("  未发现极限词、虚构数据、虚构稀缺、诱导分享或第三方 IP 名。")
        else:
            print("  未发现极限词、虚构数据、虚构稀缺或诱导分享。")
    for fp, hits in results.items():
        rel = os.path.relpath(fp, target).replace("\\", "/")
        print(f"\n  [{rel}]")
        for h in hits:
            flag = "高危" if h["level"] == "high" else "警告"
            print(f"    - {flag}  `{h['word']}`  ({h['kind']}) → {h['suggestion']}")

    high = sum(1 for hs in results.values() for h in hs if h["level"] == "high")
    warn = sum(1 for hs in results.values() for h in hs if h["level"] == "warn")
    print(f"\n合计：高危 {high} 处，警告 {warn} 处")

    if write_report:
        out = os.path.join(target, "08-合规扫描.md")
        body = pc.WORDLIST.render_report(results, "合规扫描报告")
        extra = [
            "",
            "---",
            "",
            "## 判定标准",
            "",
            "| 级别 | 含义 | 处置 |",
            "|---|---|---|",
            "| 🔴 高危 | 第三方 IP 名 / 虚构数据 / 虚构稀缺 / 诱导分享 / 盗版语义 | "
            "**必须清零，不许发布** |",
            "| 🟡 警告 | 《广告法》极限词、主观夸大词 | 建议改，改不掉要有可核实依据 |",
            "",
            "改完重跑本脚本，退出码为 0 才算通过。",
            "",
            "## 为什么盯这几类",
            "",
            "- **第三方 IP 名**：既是侵权，也会被平台判「傍名牌」。描述效果就描述效果本身，"
            "不要引他人作品名。",
            "- **虚构数据**：新产品没有销量、没有评价，写「已售」「好评如潮」是虚假宣传。",
            "- **虚构稀缺**：「限时」「仅剩 N 份」如果不会真的执行，就是骗人，"
            "而且会被平台判虚假促销。",
            "- **诱导分享**：在微信生态里是明确的违规项，会直接封链接。",
            "- **极限词**：《广告法》明令禁止，市场监管可以处罚。",
        ]
        pc.write(out, body + "\n".join(extra) + "\n")
        print(f"报告已写入 {out}")

    if high:
        print("存在高危项，不要发布。")
        return 1
    if warn:
        print("仅有警告项，建议改完后发布。")
        return 2
    return 0


def scan_facts(path):
    """单独复查 facts.json：项目本身是否含第三方 IP 名。"""
    facts = pc.load_json(path)
    blockers = facts.get("blockers") or []
    print(f"=== 项目侧复查：{os.path.basename(os.path.dirname(path)) or path} ===")
    if blockers:
        for b in blockers:
            print(f"  [阻断] {b}")
        print("  先清掉项目里的第三方作品名，再对外宣传。")
        return 1
    if pc.ip_check_enabled():
        print("  未发现第三方作品名。")
    else:
        # 名单为空时不能报「未发现」——那是「没查」，不是「查过没问题」。
        # 报成前者会让人误以为已经体检过，实际上这道闸根本没通电。
        print("  [未启用] 第三方作品名名单为空，本次未做 IP 检查。")
        print("           配置方法：cp assets/ip-watchlist.example.txt assets/ip-watchlist.txt 后填入名单，")
        print("           或用环境变量 PROMO_IP_WATCHLIST 指向自己的文件。")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", help="物料目录")
    ap.add_argument("--facts", help="可选：facts.json，顺带复查项目侧 IP 风险")
    ap.add_argument("--no-write", action="store_true", help="不写回 08-合规扫描.md")
    args = ap.parse_args()

    if not args.dir and not args.facts:
        ap.error("至少提供 --dir 或 --facts 之一")

    rc = 0
    if args.dir:
        if not os.path.isdir(args.dir):
            print(f"目录不存在：{args.dir}")
            return 3
        rc = scan_dir(args.dir, write_report=not args.no_write)
    if args.facts:
        if not os.path.isfile(args.facts):
            print(f"文件不存在：{args.facts}")
            return 3
        rc = max(rc, scan_facts(args.facts))
    return rc


if __name__ == "__main__":
    sys.exit(main())
