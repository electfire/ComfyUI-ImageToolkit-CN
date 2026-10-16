# -*- coding: utf-8 -*-
"""
图像工具箱 · 公共模块
------------------------------------------------------------------
只依赖 ComfyUI 自带环境（Pillow / numpy / torch），无需额外安装任何包。
提供：格式表、张量与 PIL 互转、分辨率缩放计算、质量压缩与按体积压缩。
"""

import io
import os
import re

import numpy as np
import torch
from PIL import Image, ImageOps

# ---------------------------------------------------------------- 格式表

# 中文显示名 -> (Pillow 保存格式名, 文件扩展名, 是否有损)
FORMAT_INFO = {
    "PNG":  ("PNG",  "png", False),
    "JPG":  ("JPEG", "jpg", True),
    "WEBP": ("WEBP", "webp", True),
    "BMP":  ("BMP",  "bmp", False),
    "GIF":  ("GIF",  "gif", False),
    "TIFF": ("TIFF", "tif", False),
}

FORMAT_NAMES = list(FORMAT_INFO.keys())

# 五种缩放方式（切换时互不影响，各用各的参数）
SCALE_MODES = ["不缩放", "指定宽高", "按长边", "按短边", "按百分比"]

# 缩放重采样算法
RESAMPLE_MAP = {
    "LANCZOS":  Image.Resampling.LANCZOS,
    "BICUBIC":  Image.Resampling.BICUBIC,
    "BILINEAR": Image.Resampling.BILINEAR,
    "NEAREST":  Image.Resampling.NEAREST,
}

# 文件名非法字符
INVALID_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t]+')

# 三个节点在 ComfyUI 节点菜单里的分类
CATEGORY = "图像工具箱"


# ---------------------------------------------------------------- 张量 <-> PIL

def tensor_to_pil(t) -> Image.Image:
    """ComfyUI IMAGE 张量 -> PIL 图像。支持 [H,W,C] 与 [B,H,W,C]。"""
    if t is None:
        raise ValueError("没有可处理的图像")
    if hasattr(t, "detach"):
        t = t.detach().cpu()
    if t.dim() == 4:
        t = t[0]
    if t.dim() != 3:
        raise ValueError(f"无法识别的图像张量维度: {tuple(t.shape)}")
    arr = t.numpy()
    if arr.shape[2] == 1:
        arr = np.repeat(arr, 3, axis=2)
    elif arr.shape[2] > 3:
        arr = arr[:, :, :3]
    arr = np.clip(255.0 * arr, 0, 255).astype(np.uint8)
    return Image.fromarray(arr, mode="RGB")


def pil_to_tensor(img: Image.Image) -> torch.Tensor:
    """PIL 图像 -> ComfyUI 风格的 [H,W,3] float32 张量（0~1）。"""
    arr = np.array(img.convert("RGB"), dtype=np.float32) / 255.0
    return torch.from_numpy(arr.copy())


def load_pil(path: str) -> Image.Image:
    """从磁盘读图：自动纠正 EXIF 方向，透明通道合成到白底（ComfyUI 图像为 RGB）。"""
    img = Image.open(path)
    try:
        img = ImageOps.exif_transpose(img)
    except Exception:
        pass
    # 多帧（GIF/TIFF）只取第一帧
    try:
        if getattr(img, "n_frames", 1) > 1:
            img.seek(0)
    except Exception:
        pass
    img.load()
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        bg = Image.new("RGB", rgba.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.split()[-1])
        return bg
    if img.mode != "RGB":
        return img.convert("RGB")
    return img


# ---------------------------------------------------------------- 尺寸计算

def calc_target_size(size, 缩放方式, 指定宽度, 指定高度, 长边像素, 短边像素, 缩放百分比, 尺寸对齐):
    """按所选方式计算目标尺寸，始终保持宽高比。"""
    ow, oh = int(size[0]), int(size[1])
    tw, th = ow, oh

    if 缩放方式 == "指定宽高":
        # 等比缩放后完整放入指定的宽高框内（不会拉伸变形）
        box_w = max(1, int(指定宽度))
        box_h = max(1, int(指定高度))
        s = min(box_w / ow, box_h / oh)
        tw, th = int(round(ow * s)), int(round(oh * s))
    elif 缩放方式 == "按长边":
        s = max(1, int(长边像素)) / max(ow, oh)
        tw, th = int(round(ow * s)), int(round(oh * s))
    elif 缩放方式 == "按短边":
        s = max(1, int(短边像素)) / min(ow, oh)
        tw, th = int(round(ow * s)), int(round(oh * s))
    elif 缩放方式 == "按百分比":
        s = max(1, int(缩放百分比)) / 100.0
        tw, th = int(round(ow * s)), int(round(oh * s))

    tw, th = max(1, tw), max(1, th)

    if 尺寸对齐 and 尺寸对齐 != "不对齐":
        try:
            n = int(尺寸对齐)
        except Exception:
            n = 0
        if n > 1:
            tw = max(n, (tw // n) * n)
            th = max(n, (th // n) * n)
    return tw, th


# ---------------------------------------------------------------- 编码压缩

def prepare_for_format(img: Image.Image, pil_format: str) -> Image.Image:
    """按目标格式调整颜色模式（JPG/BMP 去透明并垫白底，GIF 转 256 色调色板）。"""
    if pil_format in ("JPEG", "BMP"):
        if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
            rgba = img.convert("RGBA")
            bg = Image.new("RGB", rgba.size, (255, 255, 255))
            bg.paste(rgba, mask=rgba.split()[-1])
            return bg
        if img.mode != "RGB":
            return img.convert("RGB")
        return img
    if pil_format == "GIF":
        return img.convert("P", palette=Image.Palette.ADAPTIVE, colors=256)
    if img.mode not in ("RGB", "RGBA"):
        return img.convert("RGB")
    return img


def encode_image(img: Image.Image, pil_format: str, quality: int) -> bytes:
    """按格式与质量编码为字节流。PNG 用质量反推压缩级别，TIFF 用 LZW 无损压缩。"""
    q = int(max(0, min(100, int(quality))))
    img = prepare_for_format(img, pil_format)
    buf = io.BytesIO()
    kw = {}
    if pil_format == "JPEG":
        kw = {"quality": max(1, q), "optimize": True, "progressive": True}
    elif pil_format == "WEBP":
        kw = {"quality": max(1, q), "method": 4}
    elif pil_format == "PNG":
        kw = {"optimize": True, "compress_level": int(round((100 - q) / 100.0 * 9))}
    elif pil_format == "TIFF":
        kw = {"compression": "tiff_lzw"}
    img.save(buf, format=pil_format, **kw)
    return buf.getvalue()


def encode_to_target_size(img: Image.Image, pil_format: str, quality: int,
                          target_bytes: int, allow_shrink: bool) -> bytes:
    """按体积压缩：二分查找能压进目标体积的最高画质；必要时可逐步降低分辨率。"""
    qmax = int(max(1, min(100, int(quality))))
    best = encode_image(img, pil_format, qmax)
    if len(best) <= target_bytes:
        return best

    lo, hi = 1, qmax
    best_q = qmax
    # 二分查找：找出能压进目标体积的最高画质
    while lo <= hi:
        mid = (lo + hi) // 2
        data = encode_image(img, pil_format, mid)
        if len(data) <= target_bytes:
            best, best_q = data, mid
            lo = mid + 1          # 尽量再提高画质
        else:
            hi = mid - 1

    if len(best) > target_bytes:
        # 连最低画质都超体积：改用最低画质的结果作为后续缩小尺寸的基线
        lowest = encode_image(img, pil_format, 1)
        if len(lowest) <= len(best):
            best, best_q = lowest, 1

    # 质量已降到最低仍超体积，且允许降分辨率 -> 每次缩到 85% 再试
    if allow_shrink and len(best) > target_bytes:
        cur = img
        for _ in range(6):
            w, h = cur.size
            nw, nh = max(8, int(w * 0.85)), max(8, int(h * 0.85))
            if (nw, nh) == (w, h):
                break
            cur = cur.resize((nw, nh), Image.Resampling.LANCZOS)
            data = encode_image(cur, pil_format, best_q)
            if len(data) < len(best):
                best = data
            if len(best) <= target_bytes:
                break
    return best


# ---------------------------------------------------------------- 杂项

def safe_name(name: str, fallback: str = "image") -> str:
    """清理非法文件名字符。"""
    name = (name or "").strip()
    name = INVALID_CHARS.sub("_", name)
    name = name.strip(" .")
    return name or fallback


def human_size(num_bytes: int) -> str:
    if num_bytes >= 1024 * 1024:
        return f"{num_bytes / 1024 / 1024:.2f} MB"
    if num_bytes >= 1024:
        return f"{num_bytes / 1024:.1f} KB"
    return f"{num_bytes} B"


def collect_inputs(image=None, image_list=None):
    """把可选输入统一成图像列表（每个元素为 dict）。"""
    if image_list:
        return [dict(it) for it in image_list]
    if image is not None:
        items = []
        for i in range(image.shape[0]):
            items.append({
                "image": image[i],
                "name": f"image_{i + 1:05d}",
                "src": "",
                "index": i,
            })
        return items
    raise ValueError("请至少接入一张图像（image 或 image_list 输入端口）")
