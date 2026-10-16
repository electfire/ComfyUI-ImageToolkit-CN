# -*- coding: utf-8 -*-
"""
ComfyUI 图像工具箱（全中文）
------------------------------------------------------------------
三个节点：图像转换压缩 / 图像批量加载 / 图像批量保存
仅依赖 ComfyUI 自带环境（Pillow、numpy、torch），无需安装任何额外依赖。
"""

from .py.node_convert import ImageConvertCompressCN
from .py.node_loader import BatchImageLoaderCN
from .py.node_saver import BatchImageSaverCN
from .py.server import add_routes

add_routes()

NODE_CLASS_MAPPINGS = {
    "ImageConvertCompressCN": ImageConvertCompressCN,
    "BatchImageLoaderCN": BatchImageLoaderCN,
    "BatchImageSaverCN": BatchImageSaverCN,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "ImageConvertCompressCN": "🖼️ 图像转换压缩",
    "BatchImageLoaderCN": "📂 图像批量加载",
    "BatchImageSaverCN": "💾 图像批量保存",
}

WEB_DIRECTORY = "./web"

__all__ = [
    "NODE_CLASS_MAPPINGS",
    "NODE_DISPLAY_NAME_MAPPINGS",
    "WEB_DIRECTORY",
]

print("[图像工具箱] 已加载：图像转换压缩 / 图像批量加载 / 图像批量保存")
