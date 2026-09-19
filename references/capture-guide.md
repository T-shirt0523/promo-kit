# 实机录制与剪辑指南

宣传视频和宣传图里的画面**必须来自游戏本体**。AI 生成的画面与实机不符，
买家拿到手会立刻发现、要求退款。这份文档是唯一的正路。

## 一、两条路，先判断走哪条

| | 无头逐帧导出（首选） | 窗口录屏（兜底） |
|---|---|---|
| 前提 | 游戏源码里有 `DEBUG_CAPTURE` 开关 | 有能跑起来的 exe，不需要源码 |
| 画面来源 | 离屏 RenderTexture 直接导出 | `gdigrab` 抓窗口客户区 |
| 稳定性 | 高，不依赖桌面会话 | 依赖窗口能被切到前台 |
| 速度 | 极快（1792 帧 ≈ 6 秒） | 实时，1 秒录 1 秒 |
| 分辨率 | 游戏内部分辨率，无系统缩放污染 | 受 DPI 缩放影响 |

`capture_game.py` 会自动判断：源码里扫到 `DEBUG_CAPTURE` 就走无头，否则走窗口。
配方记在项目根的 `promo-capture.json`，可以直接手改。

**判断错了或配方过期不用怕** —— 脚本会校验配方里的 `-I` / `-L` 目录与编译器是否还存在，
失效就自动重新探测。

## 二、给游戏加逐帧导出开关（只有第一次要做）

用 `#ifdef` 包住，**不开宏时这段代码完全不存在**，不影响正式版体积与性能。

### 1. 环境变量与状态

```cpp
#ifdef DEBUG_CAPTURE
static int  gCapFrom[32] = { 0 }, gCapTo[32] = { 0 };
static int  gCapN = 0, gCapEvery = 1, gCapDumped = 0, gCapLastEnd = -1;
#ifdef DEBUG_AUTO_SHOT
// 与已有分镜脚本共用帧号，可直接复用它的帧表
#else
static int  gCapFno = 0;
#endif
static char gCapOut[512] = "cap";
static char gCapFmt[8] = "png";
static bool gCapInit = false;
#endif
```

读取四个环境变量：

| 变量 | 含义 | 默认 |
|---|---|---|
| `PW_CAP_OUT` | 输出目录（**必须已存在**） | `cap` |
| `PW_CAP_WINDOWS` | 帧区间 `"540-660;1050-1120"`，最多 32 段 | 空 = 不录 |
| `PW_CAP_EVERY` | 每 N 帧导一张 | 1 |
| `PW_CAP_FMT` | `png` 或 `bmp` | png |

### 2. 逐帧导出钩子

挂在主循环 `Render()` 之后：

```cpp
static bool CapTick(int fno, const RenderTexture2D& rt) {
    if (!gCapInit) CapInit();
    if (gCapN == 0) return false;
    bool inWin = false;
    for (int i = 0; i < gCapN; i++)
        if (fno >= gCapFrom[i] && fno <= gCapTo[i]) { inWin = true; break; }
    if (inWin && gCapEvery > 0 && (fno % gCapEvery) == 0) {
        Image img = LoadImageFromTexture(rt.texture);
        ImageFlipVertical(&img);          // ★ RT 纹理 Y 轴向下，导出前必须翻正
        char name[768];
        snprintf(name, sizeof(name), "%s/cap_%05d.%s", gCapOut, fno, gCapFmt);
        ExportImage(img, name);
        UnloadImage(img);
        gCapDumped++;
    }
    return gCapLastEnd > 0 && fno > gCapLastEnd;   // true = 录完，可以退出
}
```

**两个必须注意的点：**

1. **`ImageFlipVertical` 不能省。** RenderTexture 的 Y 轴向下，不翻正导出来是倒的。
2. **导出的是内部分辨率原图**（例如 640×360），不是窗口尺寸。像素艺术就该这样 ——
   放大交给 ffmpeg 的 `neighbor` 最近邻，比让游戏自己拉伸更干净。

### 3. 编译

```bash
g++ main.cpp world.cpp creature.cpp assets.cpp audio.cpp water.cpp net.cpp progress.cpp \
  -o game_cap.exe -std=c++17 -O2 \
  -DDEBUG_HEADLESS -DDEBUG_AUTO_SHOT -DDEBUG_CAPTURE \
  -I<raylib>/include -L<raylib>/lib \
  -lraylibdll -lopengl32 -lgdi32 -lwinmm -lws2_32
```

**不要照抄项目自带的 `build.bat`。** 实测本项目那份已经失效：漏了后期拆出的
`net.cpp` / `progress.cpp`，也没链 `-lws2_32`，按它构建必然失败。
`capture_game.py` 自己维护源文件列表（扫 `collect_sources`），比读 bat 可靠。

## 三、标准流程：先采样看清全片，再决定录什么

**不要一上来就盲录。** 直接录大概率挑到一片黑 —— 实测本项目全片偏暗，
整片最高平均亮度只有 58/255。

```bash
PY=python3          # 或你自己的 python 绝对路径
KIT="~/.workbuddy/skills/promo-kit"   # 改成你的实际安装位置

# 1) 全片稀疏采样 + 出联络表（1792 帧只要几秒）
"$PY" -u "$KIT/scripts/capture_game.py" --project <项目> --out ./cap --survey

# 2) ★ 打开 ./cap/contact-sheet.png，用眼睛挑出亮度高、信息量大的帧区间
#    联络表每格带帧号与亮度标注

# 3) 按挑中的区间正式录制
"$PY" -u "$KIT/scripts/capture_game.py" --project <项目> --out ./cap \
  --windows "120-260;744-828;1380-1440" --stills 3

# 4) 剪辑成宣传片
"$PY" -u "$KIT/scripts/edit_video.py" --clips ./cap/clips --out ./宣传视频 \
  --title "游戏名" --cta "点击下载 · 即刻开玩" --badge "实机演示"
```

### 静帧怎么挑的

`pick_stills` 的打分是 **亮度 + 色彩丰富度**，暗帧和灰帧直接淘汰。
这就是为什么不用均匀抽帧 —— 均匀抽必然挑到黑屏做封面。

### `--windows` 从哪来

见第一节的采样联络表。另外如果项目已有 `DEBUG_AUTO_SHOT` 的分镜帧表，
帧号是共用的，可以直接拿那份表来选段。

## 四、窗口录屏（没源码时的兜底）

```bash
"$PY" -u "$KIT/scripts/capture_game.py" --project <项目> --out ./cap --window-sec 20
```

它做的事：找正式版 exe（自动排除 `_dbg` / `_stress` / `_verify` / `_cap` 这类变体）
→ 启动或附着进程 → 切到前台 → 锁窗口客户区 → 边播输入脚本边 `gdigrab` 录制。

驱动游戏玩的输入脚本见 `scripts/window-plan.example.json`，支持字段：
`at`（秒）/ `hold`（按住数组）/ `ms`（时长）/ `tap`（点一下）/ `mouse`（0..1 相对坐标 + 点击）。

底层是 `capture_game.ps1`，也可单独用：

```powershell
powershell -File capture_game.ps1 -Exe <exe> -Out <out.mp4> `
  -Duration 20 -Fps 30 -PlanJson <plan.json> -HideConsole
```

**`.ps1` 必须保持纯 ASCII。** PowerShell 5.1 按 GBK 解码无 BOM 的 UTF-8，
中文注释会把语法搞崩（已踩过）。

## 五、ffmpeg 能力探测（决定了技术选型）

**别假设 ffmpeg 是完整版。** Windows 上很多 ffmpeg 是随其他软件分发的裁剪构建，
能力缺口很大。先跑一次 `ffmpeg -encoders` / `-devices` / `-filters` 看清手里这份有什么，
或者直接用 `promo_common.ffmpeg_caps()` 探测，它会把结果缓存进 facts。

参考基准（作者在 Windows 上实测过的一份「较完整但不全」的构建）：

| 能力 | 有无 | 后果 |
|---|---|---|
| `gdigrab` 录屏 | ✅ | 窗口录屏可行（Windows） |
| H.264 编码（`h264_mf`） | ✅ | 软件编码，无 GPU 加速 |
| `overlay` / `concat` / `gblur` / `amix` | ✅ | 图层、拼接、竖版补边、混音都能做 |
| **PNG 编解码** | ❌ | 图像只能走 BMP / TGA |
| **`drawtext` / `subtitles`** | ❌ | **字幕不能靠 ffmpeg 烧** |
| GPU 编码（nvenc / amf） | ❌ | 只能软编 |

缺能力不会让流程中断，脚本会自动绕开：没 PNG 就走 BMP/TGA（`imgcodec.py` 自己实现编解码），
没 `drawtext` 就走浏览器渲染文字图层。真正必需的只有 **H.264 编码**。

### 两条由此推出的设计

1. **文字图层走「HTML → 浏览器透明截图 → TGA → overlay」。**
   ffmpeg 烧不了字，反而逼出了更好的方案：浏览器渲染的中文断行、
   标点、字体全部正确，比 `drawtext` 强得多。透明背景靠浏览器的
   `--default-background-color=00000000`，截图后存成 **32 位带 alpha 的 TGA**
   （`imgcodec.write_tga`）—— 这是本机 ffmpeg 唯一能吃到 alpha 的格式。
   PNG 不行，因为它连 PNG 解码器都没有。

2. **竖版 9:16 用模糊背景补边，不裁主体。**
   横屏素材转竖屏时，若直接 `crop` 会把两侧游戏内容切掉。
   正解是 `scale→crop` 做放大模糊背景，前景用 `scale` + `overlay` 居中叠上去。

## 六、排查

| 现象 | 原因与解法 |
|---|---|
| 导出的帧是倒的 | 漏了 `ImageFlipVertical` |
| 编译报 `undefined reference to Net::` | 源文件列表漏了 `net.cpp` / `progress.cpp`，或没链 `-lws2_32`。别信 `build.bat` |
| 链接报找不到 `-lraylibdll` | 探测到了 win32 版 raylib（那个包只有 `raylib.lib`）。脚本会校验库目录里有没有 `libraylibdll.a`，命中就自动重探测 |
| 编译失败但还是"录到了" | 旧 exe 被复用了。已修：构建失败直接返回 4，不再继续 |
| 录出来全黑 | `--survey` 看一眼再选段。整片偏暗的题材要靠亮度打分挑帧 |
| 图层文字挡了游戏 HUD | 角标原本在左上角会撞血条，已改成与标题成组居中 |
| ffmpeg 报找不到编码器 | 编码器用 `h264_mf`，不要写 `libx264`（本机副本里被裁掉了） |

## 七、红线

- 录制内容**必须是游戏真实运行画面**，不得用 AI 画面、他人素材或预渲染动画冒充实机。
- `--bgm` 传入的背景音乐必须是自有或已获授权，**不能用别人的歌**。
- 剪辑不得通过拼接制造游戏里不存在的能力或效果。
