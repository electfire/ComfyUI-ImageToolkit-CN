# -*- coding: utf-8 -*-
"""节点1：图像转换压缩（格式互转 / 质量压缩 / 按体积压缩 / 分辨率缩放）"""

from .core import (
    CATEGORY, FORMAT_INFO, FORMAT_NAMES, SCALE_MODES, RESAMPLE_MAP,
    calc_target_size, collect_inputs, encode_image, encode_to_target_size,
    human_size, pil_to_tensor, tensor_to_pil,
)


class ImageConvertCompressCN:
    """🖼️ 图像转换压缩：格式互转 + 压缩 + 缩放，支持单图与批量"""

    CATEGORY = CATEGORY
    FUNCTION = "run"
    RETURN_TYPES = ("IMAGE_LIST",)
    RETURN_NAMES = ("图像列表",)
    OUTPUT_NODE = False
    DESCRIPTION = "图像格式互转（PNG/JPG/WEBP/BMP/GIF/TIFF）、0-100 质量压缩、按目标体积压缩、等比分辨率缩放。可单图使用，也可接批量加载节点处理整个文件夹。"

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "目标格式": (FORMAT_NAMES, {
                    "default": "PNG",
                    "tooltip": "要转换成的图像格式，六种格式可任意互转。JPG/BMP 会去掉透明通道并垫白底，GIF 会转为 256 色调色板。",
                }),
                "压缩模式": (["按质量", "按体积"], {
                    "default": "按质量",
                    "tooltip": "按质量：直接用下面的 0-100 质量滑块。按体积：自动二分查找最高画质，使单张文件不超过「目标体积」。",
                }),
                "质量": ("INT", {
                    "default": 90, "min": 0, "max": 100, "step": 1, "display": "slider",
                    "tooltip": "0-100 质量滑块。数值越低文件越小、画质越差。JPG/WEBP 直接影响画质；PNG 会换算成压缩级别（0=最快体积大，100=压缩率最高但慢）；BMP/GIF/TIFF 为无损格式，此项无效。",
                }),
                "目标体积KB": ("INT", {
                    "default": 500, "min": 1, "max": 20480, "step": 1,
                    "tooltip": "「按体积」模式下每张图的目标体积上限（单位 KB），节点会自动寻找能压进去的最高画质。",
                }),
                "体积超标处理": (["仅降低质量", "允许降低分辨率"], {
                    "default": "仅降低质量",
                    "tooltip": "「按体积」模式下，如果质量降到最低仍超过目标体积：选「允许降低分辨率」会逐步把图缩到 85% 再压（最多 6 次），否则保持原分辨率、直接保存能压到的最小体积。",
                }),
                "缩放方式": (SCALE_MODES, {
                    "default": "不缩放",
                    "tooltip": "不缩放：保持原分辨率。指定宽高：等比缩放后完整放进指定宽高框内。按长边：把较长的一边缩到指定像素。按短边：把较短的一边缩到指定像素。按百分比：整体缩放。各方式的参数互相独立，切换不会影响其它方式。",
                }),
                "指定宽度": ("INT", {
                    "default": 1024, "min": 1, "max": 32768, "step": 1,
                    "tooltip": "仅「指定宽高」生效。等比缩放后整体宽度不会超过此值。",
                }),
                "指定高度": ("INT", {
                    "default": 1024, "min": 1, "max": 32768, "step": 1,
                    "tooltip": "仅「指定宽高」生效。等比缩放后整体高度不会超过此值。",
                }),
                "长边像素": ("INT", {
                    "default": 1024, "min": 1, "max": 32768, "step": 1,
                    "tooltip": "仅「按长边」生效。把宽高中较大的一边缩放到该像素值，另一边按比例自动计算。",
                }),
                "短边像素": ("INT", {
                    "default": 768, "min": 1, "max": 32768, "step": 1,
                    "tooltip": "仅「按短边」生效。把宽高中较小的一边缩放到该像素值，另一边按比例自动计算。",
                }),
                "缩放百分比": ("INT", {
                    "default": 50, "min": 1, "max": 400, "step": 1,
                    "tooltip": "仅「按百分比」生效。100=原尺寸，50=缩小一半，200=放大一倍。",
                }),
                "缩放算法": (list(RESAMPLE_MAP.keys()), {
                    "default": "LANCZOS",
                    "tooltip": "重采样算法。LANCZOS 缩小最锐利；BICUBIC 折中；BILINEAR 更柔和；NEAREST 最快但会有锯齿，适合像素图。仅在发生缩放时生效。",
                }),
                "尺寸对齐": (["不对齐", "2", "8", "16", "32", "64"], {
                    "default": "不对齐",
                    "tooltip": "把最终宽高向下取整到指定倍数（如 8 的倍数），方便后续送进对尺寸有要求的模型。选「不对齐」则完全保持计算出的比例尺寸。",
                }),
            },
            "optional": {
                "image": ("IMAGE", {"tooltip": "单图/批量张量输入：可接「加载图像」等任意标准图像节点。"}),
                "image_list": ("IMAGE_LIST", {"tooltip": "图像列表输入：接「图像批量加载」节点，可整批转换/压缩。"}),
            },
        }

    def run(self, 目标格式, 压缩模式, 质量, 目标体积KB, 体积超标处理,
            缩放方式, 指定宽度, 指定高度, 长边像素, 短边像素, 缩放百分比,
            缩放算法, 尺寸对齐, image=None, image_list=None):

        items = collect_inputs(image, image_list)
        pil_format, ext, _lossy = FORMAT_INFO[目标格式]
        resample = RESAMPLE_MAP.get(缩放算法, RESAMPLE_MAP["LANCZOS"])
        target_bytes = int(目标体积KB) * 1024
        allow_shrink = (体积超标处理 == "允许降低分辨率")

        out = []
        total_before = 0
        total_after = 0

        for idx, item in enumerate(items):
            pil = tensor_to_pil(item["image"])
            src_w, src_h = pil.size

            tw, th = calc_target_size(pil.size, 缩放方式, 指定宽度, 指定高度,
                                      长边像素, 短边像素, 缩放百分比, 尺寸对齐)
            if (tw, th) != (src_w, src_h):
                pil = pil.resize((tw, th), resample)

            if 压缩模式 == "按体积":
                data = encode_to_target_size(pil, pil_format, 质量, target_bytes, allow_shrink)
            else:
                data = encode_image(pil, pil_format, 质量)

            new_item = dict(item)
            new_item["image"] = pil_to_tensor(pil)     # 缩放后的图像，可继续传给下游
            new_item["data"] = data                    # 已编码字节，保存节点直接落盘
            new_item["ext"] = ext
            new_item["size"] = pil.size
            new_item["bytes"] = len(data)
            new_item["index"] = idx
            out.append(new_item)

            total_before += src_w * src_h
            total_after += len(data)

        if out:
            print(f"[图像工具箱] 转换压缩完成：{len(out)} 张 -> {目标格式}，"
                  f"缩放={缩放方式}，压缩={压缩模式}，"
                  f"平均体积 {human_size(total_after // len(out))}，"
                  f"示例尺寸 {out[0]['size'][0]}x{out[0]['size'][1]}")

        return (out,)
