# promo-kit

把本地做好的项目（游戏 / 软件 / 工具 / 网站）变成一整套能直接对外发的推广物料。

**输入一个项目目录（或 zip），输出宣传图、宣传视频、多渠道文案、落地页和定价方案。**
全程不需要调用任何图像/视频生成 API —— 默认路线 0 成本、0 积分。

> 宣传物料里的画面必须来自项目本体。AI 生成的画面跟实物对不上，买家到手就退款。
> 所以这套东西的核心能力是**从项目本体录制/截取画面再自动剪辑**，而不是让 AI 画。

---

## 它能产出什么

```
promo-out/
├── 00-交付说明.md       总览 + 收款闸门结论 + 积分预算 + 手动事项
├── 01-核心文案.md       一句话定位多候选 / 三版介绍 / 卖点矩阵 / 异议处理 / CTA
├── 02-定价方案.md       三档定价 + 完整推导过程 + 渠道抽成对比 + 折扣纪律
├── 03-渠道文案.md       小红书 / 抖音 / B站 / 闲鱼 / 群与朋友圈 各一套
├── 04-落地页.html       单文件零依赖，可直接部署
├── 05-素材清单.json     AI 素材提示词 + 积分预算（仅在你要用 AI 时才看）
├── 05-视频分镜.md       分镜表 + 拼接步骤
├── 06-宣传图/*.png      4 张精确尺寸宣传图（3:4 / 1:1 / 9:16 / 16:9）
├── 07-宣传视频/         实机录制剪辑出的 16:9 / 9:16 宣传片
├── 08-合规扫描.md       极限词 / 虚构数据 / 诱导分享 / 第三方 IP 扫描结果
└── promo.json           结构化数据
```

## 四个设计决定

**1. 双轨制出图。** AI 画中文必错。所以**带文字的一切**（标题、卖点、价格、CTA）都由本地浏览器
渲染，中文断行/标点全对；AI 只做无文字的氛围视觉，且需要你确认积分预算才调用。

**2. 画面来自项目本体。** 支持两条路线，都会自动选：
- **无头逐帧导出**（源码里加一个受宏保护的钩子，不影响正式版）—— 最快，1792 帧只要 6 秒
- **窗口录屏**（ffmpeg `gdigrab` + 键鼠驱动）—— 通用兜底，任何能跑的程序都行

两条都不通就明确告诉你「需要先能让项目跑起来」，**不会偷偷拿 AI 画面顶替**。

**3. 脚本出事实层，你来出打磨层。** 生成器不假装能写好文案，但也绝不吐占位符 ——
它把扫出来的**真实**信息填进验证过的结构，再附一份加工指引。所有自动做的选择
（定价、配色、平台假设）都在 `00-交付说明.md` 里列出来并注明可改。

**4. 定价可核对。** 品类基准 × 体量系数 + 完成度加分 → 吸附心理价位点 → 三档锚定。
完整推导表写在 `02-定价方案.md`，每一处都能改。

**5. 合规是硬闸。** 不编销量、不造虚假稀缺、不诱导分享、不蹭第三方 IP。
高危项不为 0 时 `lint_promo.py` 退出码非 0 —— 这是有意设计的阻断，不是误报。

## 安装

作为 skill 使用（WorkBuddy / CodeBuddy 等支持 `SKILL.md` 的 Agent）：

```bash
git clone https://github.com/T-shirt0523/promo-kit.git ~/.workbuddy/skills/promo-kit
```

也可以纯命令行使用，脚本之间没有耦合，单独调用任意一个都可以。

## 依赖

| 依赖 | 必需？ | 用途 |
|---|---|---|
| Python 3.8+ | ✅ | 全部脚本只用标准库，无第三方包 |
| Edge / Chrome / Chromium | ✅ | 渲染宣传图与文字图层（中文准确性依赖它） |
| ffmpeg | 仅视频 | 帧序列编码、片段拼接、图层叠加 |
| g++ / MinGW + raylib | 仅 C++ 项目的无头导出 | 编译带导出钩子的版本 |

Python 无第三方依赖是刻意的 —— clone 下来就能跑，不用装环境。

### 工具定位

脚本不会假设这些工具装在哪。定位顺序：

1. 环境变量 `PROMO_FFMPEG` / `PROMO_BROWSER` / `PROMO_GXX` / `PROMO_RAYLIB`
2. `scripts/local-paths.json`（复制 `scripts/local-paths.example.json` 改）
3. `PATH`
4. 各平台常见安装位置

`local-paths.json` 已在 `.gitignore` 里，不会把你的机器路径提交上去。

## 用法

```bash
PY=python3
KIT=~/.workbuddy/skills/promo-kit

# 1) 扫描项目 → 事实清单（支持目录或 zip）
"$PY" -u "$KIT/scripts/scan_project.py" --path <项目目录或.zip> --out facts.json

# 2) 生成全部物料。--set 用来补扫描器读不到的事实
"$PY" -u "$KIT/scripts/gen_promo.py" --facts facts.json --out ./promo-out \
  --set audience=像素风怀旧玩家 --set delivery=网盘直链 --set seller_name=你的署名

# 3) 渲染 4 张宣传图
"$PY" -u "$KIT/scripts/render_assets.py" --dir ./promo-out

# 4) 合规复检（改完文案后必须重跑）
"$PY" -u "$KIT/scripts/lint_promo.py" --dir ./promo-out --facts facts.json
```

脚本一律加 `-u`：不加会因缓冲丢失全部输出。

### 实机宣传图 / 宣传视频

会启动程序并占用桌面，注意保存手头的工作。

```bash
# 5a) 全片稀疏采样，出一张联络表 —— 先看清全片再决定录哪些段
"$PY" -u "$KIT/scripts/capture_game.py" --project <项目目录> --out ./cap --survey
#     打开 ./cap/contact-sheet.png，挑亮度高、信息量大的帧区间

# 5b) 按挑中的区间正式录制（同时抽静帧供宣传图使用）
"$PY" -u "$KIT/scripts/capture_game.py" --project <项目目录> --out ./cap \
  --windows "120-260;744-828" --stills 3

# 5c) 剪辑成 16:9 / 9:16 / 1:1 宣传片
"$PY" -u "$KIT/scripts/edit_video.py" --clips ./cap/clips --out ./promo-out/07-宣传视频 \
  --title "<项目名>" --cta "点击下载 · 即刻开玩" --badge "实机演示"
```

细节、游戏侧钩子的写法、故障排查见 [`references/capture-guide.md`](references/capture-guide.md)。

### 退出码

| 脚本 | 退出码 |
|---|---|
| `scan_project.py` | 1 = 扫到第三方 IP 名（阻断） |
| `gen_promo.py` | 1 = 文案含高危词 |
| `lint_promo.py` | 1 = 有高危项 / 2 = 仅警告 |

**高危不为 0 就不要发布。**

## 第三方 IP 词表

宣传物料里出现他人作品名既是侵权风险，也是平台拒审的常见原因。
本仓库**不内置任何真实作品名**（那本身就是分发他人 IP 名，不合适），名单走外置：

```bash
cp assets/ip-watchlist.example.txt assets/ip-watchlist.txt
# 一行一个，填上你要盯的名字；# 开头是注释
```

也可以用环境变量 `PROMO_IP_WATCHLIST` 指向自己的文件。
如果装了姊妹 skill `app-listing-kit`，会自动复用它的词库。

## 目录结构

```
promo-kit/
├── SKILL.md                     Agent 主流程（作为 skill 时的入口）
├── README.md
├── assets/
│   ├── poster_templates/        4 套 HTML 海报模板
│   ├── landing_page_template.html
│   ├── facts.example.json
│   └── ip-watchlist.example.txt
├── references/
│   ├── capture-guide.md         实机录制完整指南
│   ├── visual-specs.md          视觉与 AI 素材规格
│   ├── channel-playbook.md      各渠道文案公式
│   ├── pricing-playbook.md      定价方法论
│   └── redlines.md              合规红线
└── scripts/
    ├── promo_common.py          共享模块：词库、定价引擎、工具定位、浏览器渲染
    ├── scan_project.py          项目扫描 → facts.json
    ├── gen_promo.py             facts.json → 全部物料
    ├── render_assets.py         海报 HTML → PNG
    ├── lint_promo.py            合规扫描
    ├── capture_game.py          实机录制编排
    ├── capture_game.ps1         窗口录屏（键鼠驱动）
    ├── contact_sheet.py         采样帧联络表
    ├── edit_video.py            片段 → 成片（含文字图层）
    ├── imgcodec.py              纯 Python 的 BMP/PNG/TGA 编解码
    ├── window-plan.example.json
    └── local-paths.example.json
```

`imgcodec.py` 存在的理由：很多 Windows 上的 ffmpeg 是裁剪构建，没有 PNG 编解码器，
而文字图层又必须带 alpha 通道 —— 只能自己实现。

## 边界

本 skill 只做物料。它**不**代登录、**不**代发帖、**不**代收款、**不**操作任何平台后台。
平台均未对个人开放商品发布 API，用脚本操作后台属违规，会封店扣保证金。

要在淘宝 / 拼多多上架，用姊妹 skill `app-listing-kit`
（本 skill 解决「怎么让人想买」，它解决「怎么挂到平台上卖」）。

## 许可

MIT，见 [LICENSE](LICENSE)。
