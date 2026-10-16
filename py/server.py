# -*- coding: utf-8 -*-
"""
图像工具箱 · 服务端路由
------------------------------------------------------------------
给「图像批量保存」节点的「保存当前结果」按钮用：
按钮不进队列、不重跑工作流，直接把节点上次执行时暂存在 temp 目录的
结果拷贝到 output 文件夹里。
"""

import os
import shutil
import time
from io import BytesIO

import folder_paths
from aiohttp import web
from PIL import Image
from server import PromptServer

from .core import safe_name
from .node_saver import _unique_dir


def _file_path(entry):
    """根据前端输出的 {filename, subfolder, type} 还原磁盘路径。"""
    root = (folder_paths.get_temp_directory()
            if entry.get("type") == "temp"
            else folder_paths.get_output_directory())
    return os.path.join(root, entry.get("subfolder", ""), entry.get("filename", ""))


def save_now(params):
    """把一批暂存文件落盘到 output。params 来自前端按钮请求。"""
    files = params.get("files") or []
    if not isinstance(files, list) or not files:
        return {"ok": False, "error": "没有可保存的结果，请先运行一次工作流"}

    policy = params.get("文件夹策略") or "自动新建（重名加序号）"
    dup = params.get("同名文件处理") or "覆盖"
    base = safe_name(params.get("子文件夹名称") or "", "批量导出")

    output_dir = folder_paths.get_output_directory()
    if policy == "自动新建（加时间戳）":
        base = f"{base}_{time.strftime('%Y%m%d_%H%M%S')}"
        target = _unique_dir(output_dir, base)
    elif policy == "自动新建（重名加序号）":
        target = _unique_dir(output_dir, base)
    else:
        target = os.path.join(output_dir, base)
        os.makedirs(target, exist_ok=True)

    os.makedirs(target, exist_ok=True)
    used = set()
    written = []
    total = 0
    for entry in files:
        src = _file_path(entry)
        if not os.path.isfile(src):
            continue
        fname = safe_name(entry.get("filename") or "", "image.png")
        if dup == "自动加序号":
            final, n = fname, 1
            stem, dot, ext = fname.rpartition(".")
            while final.lower() in used or os.path.exists(os.path.join(target, final)):
                final = f"{stem}_{n}.{ext}" if dot else f"{fname}_{n}"
                n += 1
            fname = final
        used.add(fname.lower())

        dst = os.path.join(target, fname)
        shutil.copy2(src, dst)
        total += os.path.getsize(dst)
        written.append({
            "filename": fname,
            "subfolder": os.path.relpath(target, output_dir).replace("\\", "/"),
            "type": "output",
        })

    if not written:
        return {"ok": False, "error": "暂存文件已过期或丢失，请重新运行工作流"}

    return {
        "ok": True,
        "saved": len(written),
        "target": target,
        "files": written,
        "total_bytes": total,
    }


def _thumb_path(params):
    """根据前端输出的 {filename, subfolder, type} 还原磁盘路径（带越界保护）。"""
    root = (folder_paths.get_temp_directory()
            if params.get("type") == "temp"
            else folder_paths.get_output_directory())
    sub = params.get("subfolder") or ""
    fname = os.path.basename(params.get("filename") or "")
    if not fname:
        return None
    target = os.path.normpath(os.path.join(root, sub, fname))
    if os.path.commonpath((os.path.abspath(target), os.path.abspath(root))) != os.path.abspath(root):
        return None
    return target if os.path.isfile(target) else None


def make_thumbnail(path, size=512, quality=85):
    """生成缩略图字节流（webp）。大图不会再整张传给前端。"""
    with Image.open(path) as im:
        im = im.convert("RGBA") if im.mode == "RGBA" else im.convert("RGB")
        im.thumbnail((size, size))
        buf = BytesIO()
        im.save(buf, format="WEBP", quality=quality, method=4)
        return buf.getvalue()


def add_routes():
    """注册路由。在插件 __init__ 里调用一次即可。"""
    routes = PromptServer.instance.routes

    @routes.post("/imagetoolkit/save/save_now")
    async def _save_now(request):
        try:
            params = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "请求体不是合法 JSON"}, status=400)
        try:
            result = save_now(params)
        except Exception as ex:
            print(f"[图像工具箱] 保存失败：{ex}")
            return web.json_response({"ok": False, "error": str(ex)}, status=500)
        status = 200 if result.get("ok") else 400
        return web.json_response(result, status=status)

    @routes.get("/imagetoolkit/view/thumb")
    async def _view_thumb(request):
        """缩略图：?filename=..&subfolder=..&type=output&size=512"""
        q = request.rel_url.query
        try:
            size = max(64, min(1024, int(q.get("size") or 512)))
        except ValueError:
            size = 512
        path = _thumb_path({
            "filename": q.get("filename") or "",
            "subfolder": q.get("subfolder") or "",
            "type": q.get("type") or "output",
        })
        if not path:
            return web.Response(status=404)
        try:
            body = make_thumbnail(path, size)
        except Exception as ex:
            print(f"[图像工具箱] 缩略图生成失败：{ex}")
            return web.Response(status=500)
        return web.Response(
            body=body,
            content_type="image/webp",
            headers={"Cache-Control": "private, max-age=3600"},
        )

