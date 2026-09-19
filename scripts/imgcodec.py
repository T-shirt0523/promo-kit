# -*- coding: utf-8 -*-
"""本机 ffmpeg 的补丁层：极简 PNG / BMP 读写。

为什么需要它：
很多 Windows 上预装的 ffmpeg 是不完整构建（例如**没有 PNG 编解码器**），
但 gdigrab 录屏、BMP 序列读取、H.264 编码、剪辑滤镜全都有。
于是链路被切成：
    游戏导出 PNG  ──[本模块]──>  BMP 序列  ──ffmpeg──>  MP4
    ffmpeg 抽帧 BMP  ──[本模块]──>  PNG（宣传图 / 落地页用）
两个方向都走这一个模块，纯标准库（zlib），不依赖 pillow。

只实现我们真实会用到的子集：
  PNG 解码：8/16 位、灰度/RGB/索引/RGBA、非隔行；隔行直接报错而不是给错图。
  PNG 编码：8 位 RGB，自适应行筛选（比固定 filter 0 小很多，宣传图动辄 1MB 级）。
  BMP 读写：24/32 位 BI_RGB，兼容自下而上与自上而下两种行序。
"""

import struct
import zlib

PNG_SIG = b"\x89PNG\r\n\x1a\n"

_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}


class ImageError(Exception):
    pass


# --------------------------------------------------------------------------
# PNG 解码
# --------------------------------------------------------------------------
def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def _unfilter(data, width, height, bpp, stride):
    """按 PNG 规范逐行还原。返回连续的原始行数据（无 filter 字节）。"""
    out = bytearray(stride * height)
    pos = 0
    prev = bytearray(stride)
    for y in range(height):
        if pos >= len(data):
            raise ImageError("IDAT 数据不足，文件可能被截断")
        ft = data[pos]
        pos += 1
        line = bytearray(data[pos:pos + stride])
        if len(line) != stride:
            raise ImageError("扫描行长度不足")
        pos += stride
        if ft == 0:
            pass
        elif ft == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif ft == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ft == 3:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 0xFF
        elif ft == 4:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                c = prev[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + _paeth(a, prev[i], c)) & 0xFF
        else:
            raise ImageError("未知 PNG 行筛选类型 %d" % ft)
        out[y * stride:(y + 1) * stride] = line
        prev = line
    return bytes(out)


def read_png(path):
    """返回 (w, h, rgba_bytes)。统一转 RGBA，调用方不用关心源格式。"""
    with open(path, "rb") as f:
        blob = f.read()
    if blob[:8] != PNG_SIG:
        raise ImageError("不是 PNG 文件：%s" % path)

    pos = 8
    idat = bytearray()
    palette = None
    trns = None
    width = height = bitdepth = colortype = interlace = None

    while pos + 8 <= len(blob):
        (length,) = struct.unpack(">I", blob[pos:pos + 4])
        ctype = blob[pos + 4:pos + 8]
        body = blob[pos + 8:pos + 8 + length]
        pos += 12 + length            # 4 长度 + 4 类型 + 数据 + 4 CRC
        if ctype == b"IHDR":
            width, height, bitdepth, colortype, _comp, _filt, interlace = \
                struct.unpack(">IIBBBBB", body)
        elif ctype == b"PLTE":
            palette = body
        elif ctype == b"tRNS":
            trns = body
        elif ctype == b"IDAT":
            idat += body
        elif ctype == b"IEND":
            break

    if width is None:
        raise ImageError("缺少 IHDR")
    if interlace != 0:
        raise ImageError("不支持隔行 PNG（Adam7）")
    if colortype not in _CHANNELS:
        raise ImageError("不支持的颜色类型 %d" % colortype)

    ch = _CHANNELS[colortype]
    if bitdepth == 8:
        bpp = ch
        stride = width * ch
    elif bitdepth == 16:
        bpp = ch * 2                       # 只取高字节
        stride = width * ch * 2
    elif bitdepth < 8 and colortype in (0, 3):
        bpp = 1
        stride = (width * bitdepth + 7) // 8
    else:
        raise ImageError("不支持的位深 %d" % bitdepth)

    raw = _unfilter(zlib.decompress(bytes(idat)), width, height, bpp, stride)

    # 拆成每像素 8 位分量
    px = []
    if bitdepth == 8:
        for y in range(height):
            base = y * stride
            row = raw[base:base + stride]
            if ch == 1:
                px.extend((v, v, v, 255) for v in row)
            elif ch == 2:
                px.extend((row[i], row[i], row[i], row[i + 1]) for i in range(0, stride, 2))
            elif ch == 3:
                px.extend((row[i], row[i + 1], row[i + 2], 255) for i in range(0, stride, 3))
            else:
                px.extend(tuple(row[i:i + 4]) for i in range(0, stride, 4))
    elif bitdepth == 16:
        for y in range(height):
            base = y * stride
            row = raw[base:base + stride]
            hi = row[0::2]
            if ch == 3:
                px.extend((hi[i], hi[i + 1], hi[i + 2], 255) for i in range(0, len(hi), 3))
            elif ch == 4:
                px.extend((hi[i], hi[i + 1], hi[i + 2], hi[i + 3]) for i in range(0, len(hi), 4))
            elif ch == 1:
                px.extend((v, v, v, 255) for v in hi)
            else:
                px.extend((hi[i], hi[i], hi[i], hi[i + 1]) for i in range(0, len(hi), 2))
    else:                                   # 1/2/4 位，仅灰度与索引
        mask = (1 << bitdepth) - 1
        for y in range(height):
            base = y * stride
            row = raw[base:base + stride]
            for x in range(width):
                bitpos = x * bitdepth
                byte = row[bitpos >> 3]
                shift = 8 - bitdepth - (bitpos & 7)
                idx = (byte >> shift) & mask
                if colortype == 3:
                    if palette is None or (idx * 3 + 2) >= len(palette):
                        raise ImageError("索引超出调色板范围")
                    r, g, b = palette[idx * 3], palette[idx * 3 + 1], palette[idx * 3 + 2]
                    a = trns[idx] if (trns and idx < len(trns)) else 255
                    px.append((r, g, b, a))
                else:
                    v = idx * (255 // mask)
                    px.append((v, v, v, 255))

    if colortype == 3 and palette is None:
        raise ImageError("索引 PNG 缺少 PLTE")
    return width, height, bytes(v for p in px for v in p)


# --------------------------------------------------------------------------
# PNG 编码
# --------------------------------------------------------------------------
def _chunk(ctype, body):
    return (struct.pack(">I", len(body)) + ctype + body
            + struct.pack(">I", zlib.crc32(ctype + body) & 0xFFFFFFFF))


def _filter_opts(line, prev, bpp):
    """返回 [(filter_type, filtered_bytes)]，调用方挑最小的那个。"""
    n = len(line)
    none = bytes(line)
    sub = bytearray(n)
    for i in range(n):
        sub[i] = (line[i] - (line[i - bpp] if i >= bpp else 0)) & 0xFF
    up = bytearray(n)
    for i in range(n):
        up[i] = (line[i] - prev[i]) & 0xFF
    avg = bytearray(n)
    for i in range(n):
        a = line[i - bpp] if i >= bpp else 0
        avg[i] = (line[i] - ((a + prev[i]) >> 1)) & 0xFF
    pae = bytearray(n)
    for i in range(n):
        a = line[i - bpp] if i >= bpp else 0
        c = prev[i - bpp] if i >= bpp else 0
        pae[i] = (line[i] - _paeth(a, prev[i], c)) & 0xFF
    cands = [(0, none), (1, bytes(sub)), (2, bytes(up)), (3, bytes(avg)), (4, bytes(pae))]
    return min(cands, key=lambda t: sum(abs(v if v < 128 else 256 - v) for v in t[1]))


def write_png(path, width, height, rgba, level=9):
    """rgba 为 w*h*4 字节。存成 8 位 RGBA（保留透明通道，overlay 素材需要）。"""
    need = width * height * 4
    if len(rgba) < need:
        raise ImageError("像素数据不足：需要 %d 字节，实得 %d" % (need, len(rgba)))
    rows = []
    prev = bytearray(width * 4)
    for y in range(height):
        line = bytearray(rgba[y * width * 4:(y + 1) * width * 4])
        ft, filtered = _filter_opts(line, prev, 4)
        rows.append(bytes([ft]) + filtered)
        prev = line
    body = zlib.compress(b"".join(rows), level)
    blob = (PNG_SIG
            + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + _chunk(b"IDAT", body)
            + _chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(blob)
    return len(blob)


# --------------------------------------------------------------------------
# BMP 读写（这是给 ffmpeg 吃的格式）
# --------------------------------------------------------------------------
def _mask_shift_bits(mask):
    if mask == 0:
        return 0, 0
    shift = 0
    while not (mask >> shift) & 1:
        shift += 1
    bits = 0
    while (mask >> (shift + bits)) & 1:
        bits += 1
    return shift, bits


def _expand(v, mask):
    shift, bits = _mask_shift_bits(mask)
    if bits == 0:
        return 255
    raw = (v >> shift) & ((1 << bits) - 1)
    top = (1 << bits) - 1
    if bits == 8:
        return raw
    return (raw * 255 + top // 2) // top


def read_bmp(path):
    """返回 (w, h, rgb_bytes)，丢弃 alpha。

    必须支持 BI_BITFIELDS(3)：raylib 的 ExportImage 存 32 位 BMP 时用的就是它，
    只认 BI_RGB 会直接把游戏导出的帧判成"不支持的格式"（已踩）。
    """
    with open(path, "rb") as f:
        blob = f.read()
    if blob[:2] != b"BM":
        raise ImageError("不是 BMP 文件：%s" % path)
    offset = struct.unpack("<I", blob[10:14])[0]
    hdr = struct.unpack("<I", blob[14:18])[0]
    if hdr < 40:
        raise ImageError("不支持的 BMP 头大小 %d" % hdr)
    width, height = struct.unpack("<ii", blob[18:26])
    bpp = struct.unpack("<H", blob[28:30])[0]
    comp = struct.unpack("<I", blob[30:34])[0]

    if comp == 0:
        if bpp == 24:
            rmask, gmask, bmask = 0x00FF0000, 0x0000FF00, 0x000000FF
        elif bpp == 32:
            rmask, gmask, bmask = 0x00FF0000, 0x0000FF00, 0x000000FF
        else:
            raise ImageError("只支持 24/32 位未压缩 BMP（bpp=%d）" % bpp)
    elif comp == 3:
        if hdr >= 108:                     # BITMAPV4/V5：掩码在头内
            base = 14 + 40
        else:                              # BITMAPINFOHEADER：掩码紧跟其后
            base = 14 + hdr
        if base + 12 > len(blob):
            raise ImageError("BMP 位域掩码缺失")
        rmask, gmask, bmask = struct.unpack("<III", blob[base:base + 12])
    else:
        raise ImageError("不支持的 BMP 压缩方式 %d" % comp)

    topdown = height < 0
    height = abs(height)
    px = bpp // 8
    stride = ((width * px + 3) // 4) * 4
    out = bytearray(width * height * 3)
    for y in range(height):
        src_y = y if topdown else (height - 1 - y)
        base = offset + src_y * stride
        row = blob[base:base + stride]
        if len(row) < width * px:
            raise ImageError("BMP 数据被截断")
        o = y * width * 3
        if bpp == 24:
            for x in range(width):
                s = x * 3
                d = o + x * 3
                out[d] = row[s + 2]
                out[d + 1] = row[s + 1]
                out[d + 2] = row[s]
        else:
            for x in range(width):
                v = struct.unpack_from("<I", row, x * 4)[0]
                d = o + x * 3
                out[d] = _expand(v, rmask)
                out[d + 1] = _expand(v, gmask)
                out[d + 2] = _expand(v, bmask)
    return width, height, bytes(out)


def write_bmp(path, width, height, rgb):
    """rgb 为 w*h*3 字节，写成 24 位自下而上 BMP。"""
    need = width * height * 3
    if len(rgb) < need:
        raise ImageError("像素数据不足")
    stride = ((width * 3 + 3) // 4) * 4
    pad = stride - width * 3
    header = (b"BM" + struct.pack("<IHHI", 14 + 40 + stride * height, 0, 0, 14 + 40)
              + struct.pack("<IiiHHIIiiII", 40, width, height, 1, 24, 0,
                            stride * height, 2835, 2835, 0, 0))
    with open(path, "wb") as f:
        f.write(header)
        for y in range(height - 1, -1, -1):
            base = y * width * 3
            line = bytearray(width * 3)
            for x in range(width):
                s = base + x * 3
                d = x * 3
                line[d] = rgb[s + 2]
                line[d + 1] = rgb[s + 1]
                line[d + 2] = rgb[s]
            f.write(bytes(line))
            if pad:
                f.write(b"\x00" * pad)
    return header and True


# --------------------------------------------------------------------------
# TGA 写入：给 ffmpeg 叠字幕用。BMP 那条路没法带 alpha，而这份 ffmpeg
# 既没有 PNG 解码器也没有 drawtext 滤镜 —— 32 位 TGA 是唯一能落地的带透明通道格式。
# --------------------------------------------------------------------------
def write_tga(path, width, height, rgba):
    """rgba 为 w*h*4 字节。type 2 未压缩真彩，descriptor 0x28 = 8 位 alpha + 左上原点。"""
    need = width * height * 4
    if len(rgba) < need:
        raise ImageError("像素数据不足：需要 %d 字节，实得 %d" % (need, len(rgba)))
    header = bytearray(18)
    header[2] = 2
    struct.pack_into("<HH", header, 12, width, height)
    header[16] = 32
    header[17] = 0x28
    body = bytearray(need)
    for i in range(width * height):
        s = i * 4
        d = i * 4
        body[d] = rgba[s + 2]        # TGA 也是 BGR 序
        body[d + 1] = rgba[s + 1]
        body[d + 2] = rgba[s]
        body[d + 3] = rgba[s + 3]
    with open(path, "wb") as f:
        f.write(bytes(header))
        f.write(bytes(body))
    return 18 + need


# --------------------------------------------------------------------------
# 便于被脚本直接调用
# --------------------------------------------------------------------------
def bmp_to_png(src, dst):
    w, h, rgb = read_bmp(src)
    rgba = bytearray(w * h * 4)
    for i in range(w * h):
        rgba[i * 4] = rgb[i * 3]
        rgba[i * 4 + 1] = rgb[i * 3 + 1]
        rgba[i * 4 + 2] = rgb[i * 3 + 2]
        rgba[i * 4 + 3] = 255
    return write_png(dst, w, h, bytes(rgba))


def png_to_bmp(src, dst):
    w, h, rgba = read_png(src)
    rgb = bytearray(w * h * 3)
    for i in range(w * h):
        rgb[i * 3] = rgba[i * 4]
        rgb[i * 3 + 1] = rgba[i * 4 + 1]
        rgb[i * 3 + 2] = rgba[i * 4 + 2]
    return write_bmp(dst, w, h, bytes(rgb))


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 4:
        print("用法: imgcodec.py <png2bmp|bmp2png> <in> <out>")
        sys.exit(2)
    op, a, b = sys.argv[1], sys.argv[2], sys.argv[3]
    if op == "png2bmp":
        png_to_bmp(a, b)
    elif op == "bmp2png":
        bmp_to_png(a, b)
    else:
        print("未知操作", op)
        sys.exit(2)
    print("ok ->", b)
