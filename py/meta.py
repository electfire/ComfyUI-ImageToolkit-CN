# -*- coding: utf-8 -*-
"""
工作流信息（metadata）嵌入
------------------------------------------------------------------
把当前工作流的 prompt / workflow 写进图片文件，方便以后把图片拖回
ComfyUI 还原整条工作流。

做法全部是「字节级注入」——不重新编码像素，所以画质零损失：
    PNG  -> tEXt 数据块（与 ComfyUI 官方 SaveImage 完全一致，可直接拖回还原）
    JPG  -> COM 注释段
    WEBP -> RIFF 里的 XMP 块
    GIF  -> Comment Extension
    TIFF -> 用 Pillow 重写 ImageDescription（TIFF 本身无损）
    BMP  -> 格式本身没有放元数据的位置，自动跳过
"""

import io
import json
import struct
import zlib

# 哪些格式支持写入工作流信息
META_OK = {"png", "jpg", "jpeg", "webp", "gif", "tif", "tiff"}


def build_metadata(prompt, extra_pnginfo):
    """整理成 ComfyUI 兼容的键值（与官方 SaveImage 保持一致）"""
    meta = {}
    try:
        # 注意：PNG 的 tEXt 规范用 Latin-1 存文本，所以 JSON 统一转义成 ASCII，
        # 中文节点名才不会变成乱码（ComfyUI 官方 SaveImage 也是这么做的）
        if extra_pnginfo:
            for key, value in extra_pnginfo.items():
                meta[str(key)] = json.dumps(value)
        if prompt is not None:
            meta["prompt"] = json.dumps(prompt)
    except Exception as e:
        print(f"[图像工具箱] 工作流信息整理失败（忽略）：{e}")
    return meta


def embed_metadata(data, ext, meta):
    """
    把工作流信息写进已编码好的图片字节。
    返回 (新字节, 是否写入成功)；不支持或失败时原样返回。
    """
    if not meta or not data:
        return data, False

    e = (ext or "").lower().lstrip(".")
    if e not in META_OK:
        return data, False

    try:
        if e == "png":
            return _png(data, meta), True
        if e in ("jpg", "jpeg"):
            return _jpeg(data, meta), True
        if e == "webp":
            return _webp(data, meta), True
        if e == "gif":
            return _gif(data, meta), True
        if e in ("tif", "tiff"):
            return _tiff(data, meta)
    except Exception as ex:
        print(f"[图像工具箱] 写入工作流信息失败（{e}，已按原样保存）：{ex}")

    return data, False


# ------------------------------------------------------------------ PNG

def _png_text_chunk(key, text):
    body = key.encode("latin-1", "replace") + b"\x00" + text.encode("utf-8", "replace")
    return (struct.pack(">I", len(body)) + b"tEXt" + body +
            struct.pack(">I", zlib.crc32(b"tEXt" + body) & 0xFFFFFFFF))


def _png(data, meta):
    blocks = b"".join(_png_text_chunk(k, v) for k, v in meta.items())
    # 插到第一个 IDAT（图像数据）之前 —— 这是惯例位置，各解析器都能读到
    pos = 8
    while pos + 8 <= len(data):
        length = int.from_bytes(data[pos:pos + 4], "big")
        if data[pos + 4:pos + 8] == b"IDAT":
            break
        pos += 12 + length
    else:
        pos = data.rfind(b"\x00\x00\x00\x00IEND")     # 没有 IDAT 就退回插到 IEND 前
        if pos < 0:
            return data
    return data[:pos] + blocks + data[pos:]


# ------------------------------------------------------------------ JPG

def _jpeg(data, meta):
    if not data.startswith(b"\xff\xd8"):
        return data
    out = bytearray(data[:2])
    for key, value in meta.items():
        buf = f"{key}={value}".encode("utf-8", "replace")
        # 单个 COM 段最多 65533 字节，超长就拆成多段
        for i in range(0, len(buf), 65531):
            part = buf[i:i + 65531]
            out += b"\xff\xfe" + struct.pack(">H", len(part) + 2) + part
    out += data[2:]
    return bytes(out)


# ------------------------------------------------------------------ WEBP

def _xml_escape(text):
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                .replace('"', "&quot;").replace("'", "&apos;"))


def _xmp_packet(meta):
    fields = "".join(
        f"<cnitk:{k}>{_xml_escape(v)}</cnitk:{k}>" for k, v in meta.items()
    )
    return (
        '<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/" '
        'xmlns:cnitk="ComfyUI-ImageToolkit-CN">'
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        f'<rdf:Description rdf:about="" xmlns:cnitk="ComfyUI-ImageToolkit-CN">{fields}</rdf:Description>'
        '</rdf:RDF></x:xmpmeta>'
        '<?xpacket end="w"?>'
    ).encode("utf-8", "replace")


def _webp(data, meta):
    if data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        return data
    xmp = _xmp_packet(meta)
    chunk = b"XMP " + struct.pack("<I", len(xmp)) + xmp
    if len(xmp) % 2:          # RIFF 块按偶数字节对齐
        chunk += b"\x00"
    # 追加到文件末尾：WebP 的图像块必须紧跟在 WEBP 头之后，元数据块只能排在后面
    size = struct.unpack("<I", data[4:8])[0] + len(chunk)
    return data[:4] + struct.pack("<I", size) + data[8:] + chunk


# ------------------------------------------------------------------ GIF

def _gif(data, meta):
    if data[:6] not in (b"GIF87a", b"GIF89a"):
        return data
    pos = 13
    flags = data[10] if len(data) > 10 else 0
    if flags & 0x80:                                   # 有全局色表
        pos += 3 * (2 ** ((flags & 0x07) + 1))
    pos = min(pos, len(data))

    buf = b"".join(f"{k}={v}".encode("utf-8", "replace") + b"\n" for k, v in meta.items())
    block = bytearray(b"\x21\xfe")                     # Comment Extension
    for i in range(0, len(buf), 255):                  # 子块每段最多 255 字节
        part = buf[i:i + 255]
        block += bytes([len(part)]) + part
    block += b"\x00"
    return data[:pos] + bytes(block) + data[pos:]


# ------------------------------------------------------------------ TIFF

def _tiff(data, meta):
    """TIFF 没有简单的插入点，用 Pillow 重写（格式本身无损）"""
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(data))
        img.load()
        desc = "\n".join(f"{k}={v}" for k, v in meta.items())
        out = io.BytesIO()
        img.save(out, "TIFF", description=desc)
        return out.getvalue(), True
    except Exception:
        return data, False
