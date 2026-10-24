# -*- coding: utf-8 -*-
"""节点3：图像批量保存（output 下自动新建文件夹）"""

import os
import time

import folder_paths

from .core import (
    CATEGORY, FORMAT_INFO, FORMAT_NAMES, collect_inputs, encode_image,
    human_size, safe_name, tensor_to_pil,
)
from .meta import build_metadata, embed_metadata

# 保存方式的下拉选项
SAVE_MODES = ["每次执行后自动保存", "仅执行（不自动保存）"]

FOLDER_POLICIES = [
    "自动新建（重名加序号）",
    "自动新建（加时间戳）",
    "固定文件夹（覆盖同名文件）",
]

NAME_MODES = [
    "保持原文件名",
    "自定义前缀 + 序号",
    "自定义前缀 + 原文件名",
    "纯序号",
]

MAX_PREVIEW = 24


def _unique_dir(parent, base):
    """返回一个不存在的文件夹路径：base、base_1、base_2 ..."""
    candidate = os.path.join(parent, base)
    if not os.path.exists(candidate):
        return candidate
    i = 1
    while True:
        candidate = os.path.join(parent, f"{base}_{i}")
        if not os.path.exists(candidate):
            return candidate
        i += 1


class BatchImageSaverCN:
    """💾 图像批量保存：在 output 里自动新建文件夹并批量落盘"""

    CATEGORY = CATEGORY
    FUNCTION = "run"
    RETURN_TYPES = ("STRING", "INT")
    RETURN_NAMES = ("保存目录", "保存数量")
    OUTPUT_NODE = True
    DESCRIPTION = "在 ComfyUI 的 output 文件夹内自动创建一个新文件夹，批量保存上游处理好的图片。文件名可保持原名，也可自定义前缀。"

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "保存方式": (SAVE_MODES, {
                    "default": "每次执行后自动保存",
                    "tooltip": "每次执行后自动保存：工作流每跑一次就往 output 落盘一次。仅执行（不自动保存）：只执行并记住结果，之后点节点上的「保存当前结果」按钮再落盘，可反复保存、随时保存，不需要重跑工作流。",
                }),
                "子文件夹名称": ("STRING", {
                    "default": "批量导出",
                    "multiline": False,
                    "tooltip": "在 output 目录下创建的文件夹名。默认策略为「自动新建（重名加序号）」，同名时会自动变成 批量导出_1、批量导出_2，不会覆盖旧结果。",
                }),
                "文件夹策略": (FOLDER_POLICIES, {
                    "default": "自动新建（重名加序号）",
                    "tooltip": "点击「保存当前结果」按钮时生效。自动新建（重名加序号）：每次保存都开一个新文件夹，重名自动变成 批量导出_1、批量导出_2，不会覆盖旧结果。自动新建（加时间戳）：文件夹名后追加年月日时分秒。固定文件夹：始终用同名文件夹，配合「同名文件处理」决定是否覆盖。",
                }),
                "文件命名": (NAME_MODES, {
                    "default": "保持原文件名",
                    "tooltip": "保持原文件名：沿用「图像批量加载」读进来的文件名。自定义前缀 + 序号：如 我的图_0001。自定义前缀 + 原文件名：如 我的图_原名。纯序号：如 0001、0002。",
                }),
                "自定义前缀": ("STRING", {
                    "default": "图片_",
                    "multiline": False,
                    "tooltip": "仅在选择带「自定义前缀」的命名方式时生效。",
                }),
                "序号起始": ("INT", {
                    "default": 1, "min": 0, "max": 999999, "step": 1,
                    "tooltip": "序号从几开始计数（用于「+序号」和「纯序号」）。目标文件夹里若已有更靠后的序号，会自动接着最大序号继续往后编（例如已有 001~005，则从 006 开始），不会重头再来。",
                }),
                "序号位数": ("INT", {
                    "default": 4, "min": 1, "max": 8, "step": 1,
                    "tooltip": "序号补零位数。4 位即 0001、0002；6 位即 000001。",
                }),
                "输出格式": (["跟随上游"] + FORMAT_NAMES, {
                    "default": "跟随上游",
                    "tooltip": "跟随上游：直接保存「图像转换压缩」节点已经编码好的结果（推荐，所见即所得）。也可在这里强行指定另一种格式重新编码。若上游没有编码（如直接接采样器），则按此处格式保存。",
                }),
                "质量": ("INT", {
                    "default": 90, "min": 0, "max": 100, "step": 1, "display": "slider",
                    "tooltip": "仅在本节点需要重新编码时生效（即「输出格式」不是跟随上游，或上游没有编码过）。含义与节点1 的质量一致。",
                }),
                "同名文件处理": (["覆盖", "自动加序号"], {
                    "default": "覆盖",
                    "tooltip": "点击「保存当前结果」按钮时生效，仅对「保持原文件名」「前缀 + 原文件名」两种命名方式有效。同一文件夹内文件名撞车时：直接覆盖，或自动追加 _1、_2 保留两份。使用「纯序号」「前缀 + 序号」时不受此项影响，会接着已有最大序号继续编号。",
                }),
                "保留工作流信息": (["保留", "不保留"], {
                    "default": "保留",
                    "tooltip": "保留：把当前工作流的节点与参数写进图片文件。PNG 写的是 ComfyUI 标准的 workflow/prompt 字段，把图片拖回 ComfyUI 即可还原整条工作流；JPG 写进 COM 注释段、WEBP 写进 XMP 块、GIF 写进注释扩展、TIFF 写进 ImageDescription。BMP 格式无处存放元数据，会自动跳过。用字节级注入，画质零损失。不保留：只存纯净图片，文件更小。",
                }),
            },
            "optional": {
                "image_list": ("IMAGE_LIST", {"tooltip": "接「图像批量加载」或「图像转换压缩」的输出。"}),
                "image": ("IMAGE", {"tooltip": "也可直接接标准图像（如采样器输出）进行批量保存。"}),
            },
            "hidden": {
                # ComfyUI 会自动注入当前工作流数据，界面上不显示
                "prompt": "PROMPT",
                "extra_pnginfo": "EXTRA_PNGINFO",
            },
        }

    # ---------------------------------------------------------------- 执行

    def run(self, 保存方式, 子文件夹名称, 文件夹策略, 文件命名, 自定义前缀, 序号起始, 序号位数,
            输出格式, 质量, 同名文件处理, 保留工作流信息="保留",
            image_list=None, image=None, prompt=None, extra_pnginfo=None):

        items = collect_inputs(image, image_list)
        output_dir = folder_paths.get_output_directory()

        # 工作流信息：只整理一次，逐张复用
        meta = build_metadata(prompt, extra_pnginfo) if 保留工作流信息 == "保留" else {}
        meta_skipped = 0

        base = safe_name(子文件夹名称, "批量导出")
        # 先把结果落到 ComfyUI 临时目录（前端按 type=temp 预览，不进 output）
        temp_root = folder_paths.get_temp_directory()
        os.makedirs(temp_root, exist_ok=True)

        force_format = None
        if 输出格式 != "跟随上游":
            pil_format, force_ext, _ = FORMAT_INFO[输出格式]
            force_format = (pil_format, force_ext)

        prefix = safe_name(自定义前缀, "图片_")
        digits = max(1, min(8, int(序号位数)))
        start = max(0, int(序号起始))
        saved = 0
        results = []
        used = set()

        for i, item in enumerate(items):
            # 1) 决定文件字节与扩展名
            data = item.get("data")
            ext = item.get("ext")
            if force_format is not None:
                pil_format, ext = force_format
                data = encode_image(tensor_to_pil(item["image"]), pil_format, 质量)
            elif data is None:
                # 上游没编码过（直接接的普通图像），按 PNG 兜底
                ext = "png"
                data = encode_image(tensor_to_pil(item["image"]), "PNG", 质量)

            # 2) 决定文件名（直接落在 temp 根目录，不再建子文件夹）
            stem = safe_name(item.get("name") or "", f"image_{i + 1:05d}")
            if 文件命名 == "保持原文件名":
                fname = stem
            elif 文件命名 == "自定义前缀 + 序号":
                fname = f"{prefix}{start + i:0{digits}d}"
            elif 文件命名 == "自定义前缀 + 原文件名":
                fname = f"{prefix}{stem}"
            else:
                fname = f"{start + i:0{digits}d}"

            fname = safe_name(fname, f"image_{i + 1:05d}")
            final_name, n = fname, 1
            while True:
                candidate = f"{final_name}.{ext}"
                if (candidate.lower() not in used
                        and not os.path.exists(os.path.join(temp_root, candidate))):
                    break
                final_name = f"{fname}_{n}"
                n += 1
            fname = f"{final_name}.{ext}"
            used.add(fname.lower())
            file_path = os.path.join(temp_root, fname)

            # 3) 需要的话把工作流信息写进文件
            if meta:
                data, ok = embed_metadata(data, ext, meta)
                if not ok:
                    meta_skipped += 1

            with open(file_path, "wb") as f:
                f.write(data)
            saved += 1

            results.append({
                "filename": fname,
                "subfolder": "",
                "type": "temp",
            })

        # 自动保存模式：直接复用按钮那一套落盘逻辑，保证「自动保存」和
        # 「点按钮保存」的行为（文件夹策略、同名处理）完全一致
        if 保存方式 != "仅执行（不自动保存）":
            from .server import save_now as _save_now
            done = _save_now({
                "files": results,
                "子文件夹名称": 子文件夹名称,
                "文件夹策略": 文件夹策略,
                "同名文件处理": 同名文件处理,
                "文件命名": 文件命名,
                "自定义前缀": 自定义前缀,
                "序号起始": 序号起始,
                "序号位数": 序号位数,
            })
            if done.get("ok"):
                output_dir_real = done["target"]
                lines = [f"已保存 {done['saved']} 张图片到：{output_dir_real}（共 {human_size(done['total_bytes'])}）"]
                if meta:
                    if meta_skipped:
                        lines.append(f"已写入工作流信息：{saved - meta_skipped} 张；{meta_skipped} 张因格式不支持（BMP）已跳过")
                    else:
                        lines.append("已写入工作流信息（PNG 可直接拖回 ComfyUI 还原工作流）")
                print("[图像工具箱] 批量保存完成：" + " / ".join(lines))
                # 前端预览改用 output 里的最终路径
                results = done["files"]
                return {
                    "ui": {
                        "images": results[:MAX_PREVIEW],
                        "text": lines,
                        # ComfyUI 会把 ui 的每个 value 展平合并，snapshot / savestat
                        # 都必须套一层 list，否则里面会被展平成 key 列表
                        "snapshot": [{"files": done["files"]}],
                        # 供前端「保存记录面板」直接写入一条历史（方案 B）：
                        # 自动保存执行完即落盘，记录随之出现在节点上
                        "savestat": [{
                            "time": time.strftime("%H:%M:%S"),
                            "count": done["saved"],
                            "target": output_dir_real,
                            "total_bytes": done["total_bytes"],
                            "files": done["files"][:MAX_PREVIEW],
                        }],
                    },
                    "result": (output_dir_real, done["saved"]),
                }
            else:
                print(f"[图像工具箱] 自动保存失败（{done.get('error')}），结果仍在临时目录，可点按钮重试")

        lines = [f"已暂存 {saved} 张图片到：{temp_root}（临时目录，未进 output）"]
        print("[图像工具箱] 已暂存结果：" + lines[0] + "，可在节点上点击「保存当前结果」按钮落盘")

        return {
            "ui": {
                "images": results[:MAX_PREVIEW],
                "text": lines,
                "snapshot": [{"files": results}],
            },
            "result": (temp_root, saved),
        }

