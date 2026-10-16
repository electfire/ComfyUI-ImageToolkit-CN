# -*- coding: utf-8 -*-
"""节点2：图像批量加载（选择文件夹，整批读入）"""

import os

import folder_paths

from .core import CATEGORY, load_pil, pil_to_tensor

ROOT_OPTION = "（input 根目录）"
CUSTOM_OPTION = "📁 自定义路径（在下方填写）"

SORT_MODES = [
    "文件名 升序",
    "文件名 降序",
    "修改时间 新→旧",
    "修改时间 旧→新",
    "文件大小 大→小",
    "文件大小 小→大",
]

DEFAULT_EXTS = "png,jpg,jpeg,webp,bmp,gif,tif,tiff,jfif,ppm,pgm"


def _list_input_subfolders(limit=60):
    """列出 ComfyUI input 目录下一级子文件夹，供下拉框选择。"""
    result = []
    try:
        root = folder_paths.get_input_directory()
        if os.path.isdir(root):
            for name in sorted(os.listdir(root)):
                p = os.path.join(root, name)
                if os.path.isdir(p) and not name.startswith("."):
                    result.append(f"input/{name}")
                if len(result) >= limit:
                    break
    except Exception:
        pass
    return result


class BatchImageLoaderCN:
    """📂 图像批量加载：选择一个文件夹，把里面的图片整批读进来"""

    CATEGORY = CATEGORY
    FUNCTION = "run"
    RETURN_TYPES = ("IMAGE_LIST",)
    RETURN_NAMES = ("图像列表",)
    OUTPUT_NODE = False
    DESCRIPTION = "选择 ComfyUI input 下的文件夹（或任意绝对路径），批量读取其中的图片，输出图像列表供「图像转换压缩」或「图像批量保存」使用。"

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "目录来源": ([ROOT_OPTION] + _list_input_subfolders() + [CUSTOM_OPTION], {
                    "default": ROOT_OPTION,
                    "tooltip": "选择要加载的文件夹。默认列出 ComfyUI 的 input 目录及其一级子文件夹；选最后一项可在下方「自定义路径」里填电脑上的任意绝对路径（如 D:\\照片\\测试）。新增文件夹后在网页点「刷新」即可出现在列表里。",
                }),
                "自定义路径": ("STRING", {
                    "default": "",
                    "multiline": False,
                    "tooltip": "仅当上面选择「自定义路径」时生效。填写任意绝对路径，例如 D:\\photos 或 /home/user/pics。也支持相对 ComfyUI input 目录的路径。",
                }),
                "包含子文件夹": ("BOOLEAN", {
                    "default": False,
                    "tooltip": "开启后递归读取所有子文件夹里的图片。",
                }),
                "文件扩展名": ("STRING", {
                    "default": DEFAULT_EXTS,
                    "multiline": False,
                    "tooltip": "只加载这些后缀的图片，用英文逗号分隔。留空表示加载所有图片格式。",
                }),
                "排序方式": (SORT_MODES, {
                    "default": "文件名 升序",
                    "tooltip": "决定图片的处理顺序，也会影响下游编号顺序。",
                }),
                "起始索引": ("INT", {
                    "default": 0, "min": 0, "max": 100000, "step": 1,
                    "tooltip": "从第几张开始读取（0 表示第一张），常用于跳过前面几张。",
                }),
                "加载数量": ("INT", {
                    "default": 0, "min": 0, "max": 100000, "step": 1,
                    "tooltip": "最多读取多少张。填 0 表示读取全部。",
                }),
            },
        }

    # ---------------------------------------------------------------- 工具

    @staticmethod
    def _resolve_dir(目录来源, 自定义路径):
        root = folder_paths.get_input_directory()
        if 目录来源 == CUSTOM_OPTION:
            p = (自定义路径 or "").strip().strip('"').strip("'")
            if not p:
                return root
            if not os.path.isabs(p):
                p = os.path.join(root, p)
            return os.path.abspath(p)
        if 目录来源 == ROOT_OPTION or not 目录来源:
            return root
        if 目录来源.startswith("input/"):
            return os.path.join(root, 目录来源[len("input/"):])
        return os.path.join(root, 目录来源)

    @classmethod
    def _collect(cls, 目录来源, 自定义路径, 包含子文件夹, 文件扩展名, 排序方式, 起始索引, 加载数量):
        folder = cls._resolve_dir(目录来源, 自定义路径)
        if not os.path.isdir(folder):
            raise FileNotFoundError(f"文件夹不存在：{folder}")

        exts = set()
        for e in (文件扩展名 or "").replace("，", ",").split(","):
            e = e.strip().lower().lstrip(".")
            if e:
                exts.add(e)

        files = []
        if 包含子文件夹:
            for dirpath, dirnames, filenames in os.walk(folder):
                dirnames[:] = [d for d in dirnames if not d.startswith(".")]
                for fn in filenames:
                    if not exts or fn.rsplit(".", 1)[-1].lower() in exts:
                        files.append(os.path.join(dirpath, fn))
        else:
            for fn in os.listdir(folder):
                p = os.path.join(folder, fn)
                if os.path.isfile(p) and (not exts or fn.rsplit(".", 1)[-1].lower() in exts):
                    files.append(p)

        reverse = "降序" in 排序方式 or "新→旧" in 排序方式 or "大→小" in 排序方式
        if 排序方式.startswith("修改时间"):
            files.sort(key=lambda p: os.path.getmtime(p), reverse=reverse)
        elif 排序方式.startswith("文件大小"):
            files.sort(key=lambda p: os.path.getsize(p), reverse=reverse)
        else:
            files.sort(key=lambda p: os.path.basename(p).lower(), reverse=reverse)

        start = max(0, int(起始索引))
        count = max(0, int(加载数量))
        if count > 0:
            files = files[start:start + count]
        elif start > 0:
            files = files[start:]
        return folder, files

    # ---------------------------------------------------------------- 执行

    def run(self, 目录来源, 自定义路径, 包含子文件夹, 文件扩展名, 排序方式, 起始索引, 加载数量):
        folder, files = self._collect(目录来源, 自定义路径, 包含子文件夹,
                                      文件扩展名, 排序方式, 起始索引, 加载数量)
        if not files:
            raise RuntimeError(f"文件夹内没有匹配到图片：{folder}")

        items = []
        failed = 0
        for idx, path in enumerate(files):
            try:
                pil = load_pil(path)
                items.append({
                    "image": pil_to_tensor(pil),
                    "name": os.path.splitext(os.path.basename(path))[0],
                    "src": path,
                    "index": idx,
                })
            except Exception as e:
                failed += 1
                print(f"[图像工具箱] 读取失败，已跳过：{path} ({e})")

        if not items:
            raise RuntimeError(f"所有图片都读取失败：{folder}")

        print(f"[图像工具箱] 批量加载：{folder} -> 成功 {len(items)} 张"
              + (f"，失败 {failed} 张" if failed else ""))
        return (items,)

    @classmethod
    def IS_CHANGED(cls, 目录来源, 自定义路径, 包含子文件夹, 文件扩展名, 排序方式, 起始索引, 加载数量):
        """文件夹内容变化时自动重新加载，避免用到缓存里的旧图。"""
        try:
            folder, files = cls._collect(目录来源, 自定义路径, 包含子文件夹,
                                         文件扩展名, 排序方式, 起始索引, 加载数量)
            sig = [folder, str(len(files))]
            for p in files[:300]:
                try:
                    st = os.stat(p)
                    sig.append(f"{p}|{int(st.st_mtime)}|{st.st_size}")
                except Exception:
                    sig.append(p)
            return "|".join(sig)
        except Exception as e:
            return str(e)
