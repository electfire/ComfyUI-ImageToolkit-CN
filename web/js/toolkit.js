/**
 * ComfyUI 图像工具箱 · 前端联动
 * ------------------------------------------------------------------
 * 作用：
 *   1) 切换「压缩模式 / 缩放方式 / 目录来源 / 文件命名」时，
 *      自动隐藏当前用不到的参数，让节点面板保持清爽。
 *   2) 锁定手动调整过的节点尺寸：调好一次就固定住，
 *      重新加载 / 切换工作流都保持原样，除非再次手动拖动。
 *      只影响界面显示，不影响任何计算逻辑。
 *   3) 在「图像批量保存」节点上放一个「保存当前结果」按钮：
 *      点击后直接把上次执行的结果落盘到 output，
 *      不重跑工作流、不进队列。
 *   4) 同节点常驻一块「保存记录」面板（方案 B）：
 *      最近 5 条保存记录（时间 · 张数 · 文件名 · 文件夹），
 *      点击任意行直接弹出该次保存的图片预览；除「保存方式」外的
 *      保存参数默认折叠，需要时从面板右上角或右键菜单展开。
 *      面板是控件区最后一个控件（图片预览下方），高度固定；图片预览
 *      保持原生弹性，拖动节点时图片跟着缩放，面板位置始终在最底部。
 */

const ITK_APP =
  (typeof window !== "undefined" && window.comfyAPI && window.comfyAPI.app)
    ? (window.comfyAPI.app.app || window.comfyAPI.app)
    : null;

/** 各节点的显隐规则：{ 控制控件: { 被控控件: 显示条件(字符串 / 数组 / 函数) } } */
const ITK_RULES = {
    ImageConvertCompressCN: {
        "压缩模式": {
            "质量": ["按质量"],
            "目标体积KB": ["按体积"],
            "体积超标处理": ["按体积"],
        },
        "缩放方式": {
            "指定宽度": ["指定宽高"],
            "指定高度": ["指定宽高"],
            "长边像素": ["按长边"],
            "短边像素": ["按短边"],
            "缩放百分比": ["按百分比"],
            "缩放算法": ["指定宽高", "按长边", "按短边", "按百分比"],
        },
    },

    BatchImageLoaderCN: {
        "目录来源": {
            "自定义路径": (v) => typeof v === "string" && v.indexOf("自定义路径") >= 0,
        },
    },

    BatchImageSaverCN: {
        "文件命名": {
            "自定义前缀": ["自定义前缀 + 序号", "自定义前缀 + 原文件名"],
            "序号起始": ["自定义前缀 + 序号", "纯序号"],
            "序号位数": ["自定义前缀 + 序号", "纯序号"],
        },
    },
};

/* ==================================================================
 * 尺寸锁定
 * ------------------------------------------------------------------
 * 原则：手动拖过一次就锁死，之后谁来都不改 —— 除非再次手动拖动。
 *
 *   - 手动信号用 node.onResize（ComfyUI 拖动节点走 setSize → 回调 onResize），
 *     并用「是否等于自己刚施加的尺寸」把 setSize 的自触发排掉。
 *   - 锁定值写进 node.properties，随工作流 JSON 一起保存，重载原样恢复。
 *   - 没锁定过的节点：宽度沿用工作流保存值，高度贴合控件自动计算。
 *   - 高度只在「内容真的放不下」时才长高，绝不因为切换工作流而累加。
 * ================================================================== */

const ITK_MEM_KEY = "ImageToolkitCN.userSize.v2";  // 新建节点沿用上次尺寸
const ITK_PROP = "__itkSize";                      // 存在 properties 里，随工作流保存
const ITK_MIN_W = 180;
const ITK_MIN_H = 60;

function itkMemLoad() {
    try { return JSON.parse(window.localStorage.getItem(ITK_MEM_KEY) || "{}"); }
    catch (e) { return {}; }
}

function itkMemSave(typeName, w, h) {
    if (!typeName || !w || !h) return;
    try {
        const m = itkMemLoad();
        m[typeName] = [Math.round(w), Math.round(h)];
        window.localStorage.setItem(ITK_MEM_KEY, JSON.stringify(m));
    } catch (e) { /* 隐私模式下 localStorage 可能不可用，忽略即可 */ }
}

function itkMemClear(typeName) {
    try {
        const m = itkMemLoad();
        delete m[typeName];
        window.localStorage.setItem(ITK_MEM_KEY, JSON.stringify(m));
    } catch (e) { /* 忽略 */ }
}

/** 当前可见控件下的「自动尺寸」 */
function itkAutoSize(node) {
    let cs = null;
    try { cs = node.computeSize(); } catch (e) { cs = null; }
    const w = Math.max(ITK_MIN_W, (cs && cs[0]) || ITK_MIN_W);
    const h = Math.max(ITK_MIN_H, (cs && cs[1]) || ITK_MIN_H);
    // 「保存记录」面板本身是固定高度的自定义控件，computeSize 已包含它，
    // 不用再额外加高度，否则会和布局引擎重复计算。
    return [w, h];
}

/** 锁定当前尺寸：写 properties（随工作流）+ localStorage（新建节点沿用） */
function itkLockSize(node, w, h) {
    if (!w || !h) return;
    node.__itkLock = { w: Math.max(ITK_MIN_W, w), h: Math.max(ITK_MIN_H, h) };
    try {
        if (!node.properties) node.properties = {};
        node.properties[ITK_PROP] = [Math.round(w), Math.round(h)];
    } catch (e) { /* 忽略 */ }
    itkMemSave(node.__itkType, w, h);
}

function itkUnlock(node) {
    node.__itkLock = null;
    try { if (node.properties) delete node.properties[ITK_PROP]; } catch (e) { /* 忽略 */ }
    itkMemClear(node.__itkType);
}

function itkApplySize(node) {
    try {
        if (node.flags && node.flags.collapsed) return;   // 折叠状态不去动它

        const [autoW, autoH] = itkAutoSize(node);
        const lock = node.__itkLock;
        // 锁定过 → 宽高都用它；没锁定 → 宽度沿用工作流保存值、高度贴合内容
        const w = lock ? lock.w : (node.__itkSavedW || autoW);
        const h = lock ? Math.max(lock.h, autoH) : autoH;  // 只有内容放不下才长高

        node.__itkSelfSize = [w, h];     // 标记：接下来那次 onResize 是我们自己触发的
        node.setSize([w, h]);
        node.__itkApplied = [w, h];
        node.__itkReady = true;

        if (node.setDirtyCanvas) node.setDirtyCanvas(true, true);
        else if (node.graph && node.graph.setDirtyCanvas) node.graph.setDirtyCanvas(true, true);
    } catch (e) { /* 尺寸调整失败也不影响使用 */ }
}

function itkResetSize(node) {
    itkUnlock(node);
    node.__itkSavedW = 0;      // 连工作流里保存的宽度也一起放弃，回到自动尺寸
    itkApplySize(node);
}

/* ==================================================================
 * 「保存当前结果」按钮（图像批量保存）
 * ------------------------------------------------------------------
 * 按钮不参与队列执行。点击时：
 *   1) 从 node.properties.itk_last_output 取出上次执行暂存的结果清单；
 *   2) 把节点当前的「子文件夹名称 / 文件夹策略 / 同名文件处理」一起 POST
 *      到节点自带的服务端路由 /imagetoolkit/save/save_now；
 *   3) 后端把 temp 里的暂存文件拷到 output，前端 toast 提示并刷新节点输出。
 *
 * 暂存清单的写入见下方 onExecuted 钩子 —— 后端每次执行都会在 ui 里带回它。
 * ================================================================== */

const ITK_SNAPSHOT_PROP = "itk_last_output";

/** 读取节点的某个控件值（不在图表里时回退到 values 缓存） */
function itkWidgetValue(node, name) {
    const w = node.widgets && node.widgets.find((x) => x.name === name);
    if (w && w.value !== undefined && w.value !== null) return w.value;
    if (node.properties && node.properties.values &&
        node.properties.values[name] !== undefined) {
        return node.properties.values[name];
    }
    return null;
}

/* ------------------------------------------------------------------
 * toast 通知
 * 这版前端没有暴露可用的 toast API，干脆自建一个轻量 DOM 通知：
 * 右上角堆叠、3.8 秒自动消失、点击可提前关闭、失败时再兜底 alert。
 * ------------------------------------------------------------------ */

function itkToast(message, type) {
    try {
        let stack = document.getElementById("itk-toast-stack");
        if (!stack) {
            stack = document.createElement("div");
            stack.id = "itk-toast-stack";
            stack.style.cssText = ("position:fixed;top:12px;right:12px;z-index:100001;" +
                "display:flex;flex-direction:column;gap:8px;pointer-events:none");
            document.body.appendChild(stack);
        }
        const err = type === "error";
        const item = document.createElement("div");
        item.style.cssText = ("pointer-events:auto;max-width:360px;padding:9px 14px;" +
            "border-radius:8px;font:12px Microsoft YaHei,sans-serif;color:#eee;line-height:1.5;" +
            "background:" + (err ? "#7a2e2e" : "#2f5d3f") + ";" +
            "border:1px solid " + (err ? "#a04848" : "#4c8a63") + ";" +
            "box-shadow:0 6px 20px rgba(0,0,0,0.45);cursor:pointer;white-space:pre-wrap");
        item.textContent = message;
        item.onclick = () => { if (item.parentNode) item.remove(); };
        stack.appendChild(item);
        setTimeout(() => { if (item.parentNode) item.remove(); }, 3800);
    } catch (e) {
        if (type === "error") window.alert("[图像工具箱] " + message);
    }
}

/**
 * 保存记录面板（图像批量保存 · 方案 B）
 * ------------------------------------------------------------------
 * 节点常驻一块只读状态区，记录最近 5 次保存：
 *     时间 · 张数 · 文件名 · 目标文件夹
 *   · 点击任意记录行 → 直接弹出该次保存的图片预览
 *   · 自动保存（每次执行后自动保存）执行完会自动多出一条记录
 *   · 自动保存的目标文件夹取路径最后一段显示，完整路径在弹层标题里
 *   · 面板右上角（或节点右键菜单）可折叠 / 展开全部保存参数
 *
 * 布局结构（从上到下）：保存参数 → 「保存当前结果」按钮 → 图片预览 → 本面板。
 * 面板是节点控件区的最后一个控件（图片预览是执行后才挂上来的，
 * 所以面板每帧绘制时会校正自己的位置），高度固定为记录行数对应的高度；
 * 图片预览保持原生弹性，拖动节点尺寸时图片跟着缩放。
 * ================================================================== */

const ITK_HISTORY_PROP = "itk_save_history";   // 随工作流保存
const ITK_FOLD_PROP = "itk_params_folded";     // 随工作流保存
const ITK_HISTORY_MAX = 5;                     // 最多保留 5 条
const ITK_FILES_PER_REC = 24;                  // 每条记录最多带 24 个文件（缩略图弹层用）

// 除「保存方式」以外，其它保存参数默认全部折叠
const ITK_FOLDABLE = new Set([
    "子文件夹名称", "文件夹策略", "文件命名", "自定义前缀", "序号起始",
    "序号位数", "输出格式", "质量", "同名文件处理", "保留工作流信息",
]);

const ITK_PANEL = {
    padX: 8,           // 面板左右内边距
    headerH: 20,       // 标题行高度
    rowH: 18,          // 每条记录行高度
    font: "11px sans-serif",
};

/** 面板文字的左缩进（内边距 + 6px） */
function itkPadX() { return ITK_PANEL.padX + 6; }

function itkNow() {
    const d = new Date();
    const p = (n) => String(n).padStart(2, "0");
    return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

function itkHumanSize(n) {
    if (n === null || n === undefined) return "";
    n = Number(n) || 0;
    if (n >= 1024 * 1024) return (n / 1024 / 1024).toFixed(2) + " MB";
    if (n >= 1024) return (n / 1024).toFixed(1) + " KB";
    return n + " B";
}

/** 按当前画布字体宽度截断文本（尾部加省略号） */
function itkTruncateText(ctx, text, maxW) {
    if (!text) return "";
    if (!maxW || maxW <= 0) return text;
    if (ctx.measureText(text).width <= maxW) return text;
    let lo = 1, hi = text.length;
    while (lo < hi) {
        const mid = (lo + hi + 1) >> 1;
        if (ctx.measureText(text.slice(0, mid) + "…").width <= maxW) lo = mid;
        else hi = mid - 1;
    }
    return text.slice(0, Math.max(1, lo)) + "…";
}

function itkHistory(node) {
    if (!node.properties) node.properties = {};
    let h = node.properties[ITK_HISTORY_PROP];
    if (!Array.isArray(h)) { h = []; node.properties[ITK_HISTORY_PROP] = h; }
    return h;
}

/** 记录一条保存历史（来源：服务端响应 / 自动保存的 ui savestat） */
function itkAddSaveRecord(node, rec) {
    if (!rec) return;
    const target = rec.target || "";
    const hist = itkHistory(node);
    hist.push({
        time: rec.time || itkNow(),
        count: rec.count || 0,
        folder: (target.split(/[\\/]/).pop() || target).slice(0, 48),
        path: target,
        total_bytes: rec.total_bytes || 0,
        files: Array.isArray(rec.files) ? rec.files.slice(0, ITK_FILES_PER_REC) : [],
    });
    while (hist.length > ITK_HISTORY_MAX) hist.shift();
    // 面板高度随行数变化，刷新布局（重排由前端布局引擎在下一帧完成）
    itkLayoutRefresh(node);
}

/** 面板高度（随记录行数变化；面板是可伸缩控件，这是下限） */
function itkPanelHeight(node) {
    const rows = Math.max(1, itkHistory(node).length);
    return ITK_PANEL.headerH + rows * ITK_PANEL.rowH + 4;
}

const ITK_PREVIEW_NAME = "$$canvas-image-preview";

/**
 * 面板布局维护：
 *   1. 把面板挪到控件区末尾（图片预览下面）——预览控件是执行后才挂到
 *      node.widgets 上的，挂上来时排在面板后面，所以每帧都要校正一次；
 *   2. 面板保持固定高度（高度下限 = 内容高度），图片预览则保持原生弹性：
 *      拖动节点时图片跟着缩放，面板只在节点底部占一条固定高度的带。
 *   3. 有变化时打个布局脏标记，让前端布局引擎下一帧重排。
 */
function itkLayoutRefresh(node) {
    const panel = node.__itkPanelWidget;
    const ws = node.widgets || [];
    if (!panel) return;
    let changed = false;

    // 面板挪到最后（预览是执行后才挂上去的，所以每帧都要校正）
    const i = ws.indexOf(panel);
    if (i !== -1 && i !== ws.length - 1) {
        ws.splice(i, 1);
        ws.push(panel);
        changed = true;
    }

    const newH = itkPanelHeight(node);
    if (panel.__itkPanelH !== newH) {
        panel.__itkPanelH = newH;
        changed = true;
    }

    if (changed) {
        try { node._widgetSlotsDirty = true; } catch (e) { /* 忽略 */ }
        if (node.setDirtyCanvas) node.setDirtyCanvas(true, true);
    }
}

/**
 * 绘制保存记录面板（作为自定义控件，由布局引擎排布 y，自己只管画）。
 * widget 是 addCustomWidget 返回的控件对象；命中区域存进 widget.__itkRows /
 * widget.__itkFoldBtn，给 onPointerDown / 悬停检测用。
 */
function itkDrawPanel(widget, node, ctx, w, top) {
    if (node.flags && node.flags.collapsed) { widget.__itkRows = null; return; }
    const hist = itkHistory(node);
    const innerW = Math.max(60, w - ITK_PANEL.padX * 2);
    const rowN = Math.max(1, hist.length);
    const panelH = ITK_PANEL.headerH + rowN * ITK_PANEL.rowH + 4;
    // 面板是固定高度控件（min = max），布局引擎给的 computedHeight 就等于 panelH
    const boxH = Math.max(panelH, widget.computedHeight || 0);

    ctx.save();
    ctx.font = ITK_PANEL.font;
    ctx.textBaseline = "middle";

    // 背板（铺满控件被分配到的整个高度）
    ctx.fillStyle = "rgba(16,16,20,0.4)";
    ctx.strokeStyle = "#46464f";
    ctx.lineWidth = 1;
    ctx.fillRect(ITK_PANEL.padX, top, innerW, boxH);
    ctx.strokeRect(ITK_PANEL.padX + 0.5, top + 0.5, innerW - 1, boxH - 1);

    // 标题 + 折叠开关
    ctx.textAlign = "left";
    ctx.fillStyle = "#9a9aa8";
    ctx.fillText("保存记录", itkPadX(), top + ITK_PANEL.headerH / 2);

    const foldLabel = node.__itkFolded ? "显示参数 ▾" : "折叠参数 ▴";
    ctx.textAlign = "right";
    ctx.fillStyle = widget.__itkFoldHover ? "#e8e8f2" : "#7d7d8c";
    ctx.fillText(foldLabel, w - ITK_PANEL.padX - 4, top + ITK_PANEL.headerH / 2);
    const labelW = ctx.measureText(foldLabel).width;
    widget.__itkFoldBtn = {
        x: w - ITK_PANEL.padX - 4 - labelW - 8, y: top,
        w: labelW + 16, h: ITK_PANEL.headerH,
    };

    const rowY = top + ITK_PANEL.headerH;
    const rows = [];
    if (!hist.length) {
        ctx.textAlign = "left";
        ctx.fillStyle = "#6e6e7c";
        ctx.fillText("暂无保存记录 · 先运行一次工作流",
            itkPadX(), rowY + ITK_PANEL.rowH / 2);
    } else {
        hist.forEach((rec, i) => {
            const ry = rowY + i * ITK_PANEL.rowH;
            if (widget.__itkHover === i) {
                ctx.fillStyle = "rgba(140,170,255,0.16)";
                ctx.fillRect(ITK_PANEL.padX, ry, innerW, ITK_PANEL.rowH);
            }
            const midY = ry + ITK_PANEL.rowH / 2;
            // 时间
            ctx.textAlign = "left";
            ctx.fillStyle = "#8f9ad2";
            ctx.fillText(rec.time || "", itkPadX(), midY);
            let x = itkPadX() + ctx.measureText(rec.time || "").width + 8;
            // 张数
            const rest = `${rec.count || 0} 张`;
            ctx.fillStyle = "#c8c8d2";
            ctx.fillText(rest, x, midY);
            x += ctx.measureText(rest).width + 8;
            // 文件名（不带扩展名，取该次保存的第一个文件）
            const fname = (rec.files && rec.files[0] && rec.files[0].filename) || "";
            const dot = fname.lastIndexOf(".");
            const stem = dot > 0 ? fname.slice(0, dot) : fname;
            if (stem) {
                ctx.fillStyle = "#c9b38a";
                ctx.fillText(itkTruncateText(ctx, stem, Math.max(24, w - ITK_PANEL.padX - 4 - x)),
                    x, midY);
                x += ctx.measureText(itkTruncateText(ctx, stem, Math.max(24, w - ITK_PANEL.padX - 4 - x))).width + 8;
            }
            // 目标文件夹（超宽截断）
            if (rec.folder) {
                ctx.fillStyle = "#7ec8a0";
                ctx.fillText("→ " + itkTruncateText(ctx, rec.folder, Math.max(24, w - ITK_PANEL.padX - 4 - x)),
                    x, midY);
            }
            rows.push({ x: ITK_PANEL.padX, y: ry, w: innerW, h: ITK_PANEL.rowH, rec });
        });
    }
    ctx.restore();
    widget.__itkRows = rows;
}

/** 缩略图弹层：点击某条记录时打开该次保存的图片 */
let ITK_POPUP = null;

function itkCloseSavePopup() {
    if (ITK_POPUP && ITK_POPUP.parentNode) ITK_POPUP.parentNode.removeChild(ITK_POPUP);
    ITK_POPUP = null;
}

function itkViewUrl(entry, preview) {
    const api = window.comfyAPI && window.comfyAPI.api;
    const q = new URLSearchParams({
        filename: (entry && entry.filename) || "",
        subfolder: (entry && entry.subfolder) || "",
        type: (entry && entry.type) || "output",
    });
    if (preview) q.append("preview", String(preview));
    const rel = "view?" + q.toString();
    return (api && typeof api.apiURL === "function") ? api.apiURL(rel) : rel;
}

/**
 * 缩略图 URL：走插件自己的 /imagetoolkit/view/thumb 路由，服务端真正缩小到
 * size 像素再返回 webp（/view 的 preview 参数其实是质量不是尺寸，不适合做大图预览）。
 */
function itkThumbUrl(entry, size) {
    const api = window.comfyAPI && window.comfyAPI.api;
    const q = new URLSearchParams({
        filename: (entry && entry.filename) || "",
        subfolder: (entry && entry.subfolder) || "",
        type: (entry && entry.type) || "output",
        size: String(size || 448),
    });
    const rel = "imagetoolkit/view/thumb?" + q.toString();
    return (api && typeof api.apiURL === "function") ? api.apiURL(rel) : "/" + rel;
}

function itkOpenSavePopup(node, rec, e) {
    if (!rec) return;
    itkCloseSavePopup();

    const pop = document.createElement("div");
    pop.className = "itk-save-popup";
    pop.style.cssText = ("position:fixed;z-index:100000;background:#26262b;color:#e6e6ec;" +
        "border:1px solid #555560;border-radius:8px;padding:10px 12px;" +
        "box-shadow:0 10px 34px rgba(0,0,0,0.55);font:12px Microsoft YaHei,sans-serif;" +
        "max-width:min(1080px,92vw);max-height:86vh;overflow:auto");

    // 点弹层本身不要冒泡到全局关闭逻辑
    pop.addEventListener("pointerdown", (ev) => ev.stopPropagation());

    // 关闭按钮
    const close = document.createElement("div");
    close.textContent = "✕";
    close.style.cssText = "position:absolute;top:6px;right:10px;cursor:pointer;color:#888";
    close.onclick = itkCloseSavePopup;
    pop.appendChild(close);

    const files = Array.isArray(rec.files) ? rec.files : [];
    const header = document.createElement("div");
    header.style.cssText = "margin:2px 20px 8px 0;line-height:1.5;color:#c8c8d2";
    header.textContent = `保存于 ${rec.time || ""} · 共 ${rec.count || files.length} 张` +
        (rec.total_bytes ? ` · ${itkHumanSize(rec.total_bytes)}` : "") +
        (rec.path ? `\n${rec.path}` : "");
    pop.appendChild(header);

    if (!files.length) {
        const empty = document.createElement("div");
        empty.textContent = "该记录没有图片清单（可能来自旧版本工作流）";
        empty.style.cssText = "color:#888";
        pop.appendChild(empty);
    } else {
        const grid = document.createElement("div");
        grid.style.cssText = "display:flex;flex-wrap:wrap;gap:10px";
        files.forEach((entry) => {
            const box = document.createElement("a");
            box.href = itkViewUrl(entry);
            box.target = "_blank";
            box.rel = "noopener";
            box.title = entry.filename || "";
            box.style.cssText = "display:block;width:224px;text-decoration:none;color:#8f9aa8;text-align:center";
            const img = document.createElement("img");
            img.src = itkThumbUrl(entry, 448);
            img.alt = entry.filename || "";
            img.style.cssText = "width:224px;height:224px;object-fit:contain;" +
                "background:#1c1c20;border:1px solid #444;border-radius:4px;display:block";
            img.onerror = () => {
                img.style.display = "none";
                const ph = document.createElement("div");
                ph.textContent = entry.filename || "";
                ph.style.cssText = ("width:224px;height:224px;font-size:11px;line-height:1.3;" +
                    "overflow:hidden;background:#1c1c20;border:1px solid #444;border-radius:4px");
                if (img.nextSibling) box.insertBefore(ph, img.nextSibling);
                else box.appendChild(ph);
            };
            box.appendChild(img);
            const cap = document.createElement("div");
            cap.style.cssText = "font-size:11px;max-width:224px;overflow:hidden;" +
                "text-overflow:ellipsis;white-space:nowrap";
            cap.textContent = entry.filename || "";
            box.appendChild(cap);
            grid.appendChild(box);
        });
        pop.appendChild(grid);
        if (rec.count && rec.count > files.length) {
            const more = document.createElement("div");
            more.style.cssText = "margin-top:8px;color:#888;font-size:11px";
            more.textContent = `（仅显示前 ${files.length} 张，共 ${rec.count} 张；点图片可打开原图）`;
            pop.appendChild(more);
        }
    }

    // 锚点在点击处附近，避免越出视口
    const x = Math.min((e && e.clientX) || 200, Math.max(8, window.innerWidth - 1100));
    const y = Math.min((e && e.clientY) || 120, Math.max(8, window.innerHeight - 180));
    pop.style.left = Math.max(8, x + 8) + "px";
    pop.style.top = Math.max(8, y + 6) + "px";

    document.body.appendChild(pop);
    ITK_POPUP = pop;

    // 点击弹层以外任意处关闭（下一次 pointerdown 起生效）
    setTimeout(() => {
        if (!ITK_POPUP) return;
        const handler = (ev) => {
            if (ITK_POPUP && !ITK_POPUP.contains(ev.target)) itkCloseSavePopup();
            document.removeEventListener("pointerdown", handler, true);
        };
        document.addEventListener("pointerdown", handler, true);
    }, 0);
}

/** 折叠 / 展开保存参数 */
function itkSetFold(node, folded) {
    node.__itkFolded = !!folded;
    if (!node.properties) node.properties = {};
    node.properties[ITK_FOLD_PROP] = node.__itkFolded;
    if (typeof node.__itkApplyVisibility === "function") node.__itkApplyVisibility(true);
    if (node.setDirtyCanvas) node.setDirtyCanvas(true, true);
}

/**
 * 面板点击：走控件的 onPointerDown(pointer, node, canvas) 入口。
 * 命中判断直接按行高算术计算（行高固定），不依赖绘制缓存的 __itkRows ——
 * 即使布局/绘制暂时没跟上，每一行也都能点开图片。
 * 返回 true 表示事件已被面板吃掉。
 */
function itkPanelClick(widget, node, canvas, pointer) {
    const ev = pointer && pointer.eDown;
    if (ev && ev.button !== undefined && ev.button !== 0) return false;   // 只响应左键
    // 优先用事件自带的画布坐标，回退到 graph_mouse
    let gx = ev && Number(ev.canvasX);
    let gy = ev && Number(ev.canvasY);
    if (!(gx >= 0) || !(gy >= 0)) {
        gx = (canvas && canvas.graph_mouse && canvas.graph_mouse[0]) || 0;
        gy = (canvas && canvas.graph_mouse && canvas.graph_mouse[1]) || 0;
    }
    const lx = gx - node.pos[0];
    const ly = gy - node.pos[1];

    // 面板顶沿：布局引擎每帧都会把控件 y 写进 last_y / y
    const top = (widget.last_y !== undefined ? widget.last_y : widget.y) || 0;
    const w = node.size[0] || 0;
    const hist = itkHistory(node);
    const rowsN = Math.max(1, hist.length);

    // 折叠开关：右上角（绘制时量过宽度，没量到就用一个保守宽度）
    const fb = widget.__itkFoldBtn;
    const fbW = (fb && fb.w) || 92;
    if (lx >= w - ITK_PANEL.padX - 4 - fbW && lx <= w - ITK_PANEL.padX - 4 &&
        ly >= top && ly <= top + ITK_PANEL.headerH) {
        itkSetFold(node, !node.__itkFolded);
        return true;
    }

    // 记录行：top + headerH 起，每行 rowH
    const rowsTop = top + ITK_PANEL.headerH;
    if (hist.length && lx >= ITK_PANEL.padX && lx <= w - ITK_PANEL.padX &&
        ly >= rowsTop && ly < rowsTop + rowsN * ITK_PANEL.rowH) {
        const idx = Math.floor((ly - rowsTop) / ITK_PANEL.rowH);
        const rec = hist[idx];
        if (rec) {
            itkOpenSavePopup(node, rec, ev);
            return true;
        }
    }
    // 点在面板空白处也让事件到此为止，避免误触发节点拖拽
    return true;
}

/** 悬停高亮（widget 版） */
function itkPanelHover(widget, node, pos) {
    let hover = -1;
    let foldHover = false;
    const rows = widget.__itkRows;
    const fb = widget.__itkFoldBtn;
    if (rows && pos) {
        for (let i = 0; i < rows.length; i++) {
            const r = rows[i];
            if (pos[1] >= r.y && pos[1] <= r.y + r.h) { hover = i; break; }
        }
    }
    if (fb && pos) {
        foldHover = pos[0] >= fb.x && pos[0] <= fb.x + fb.w &&
            pos[1] >= fb.y && pos[1] <= fb.y + fb.h;
    }
    if (hover !== widget.__itkHover || foldHover !== widget.__itkFoldHover) {
        widget.__itkHover = hover;
        widget.__itkFoldHover = foldHover;
        if (node.setDirtyCanvas) node.setDirtyCanvas(true, true);
    }
}

/** 发起保存请求 */
async function itkSaveNow(node) {
    const snapshot = node.properties && node.properties[ITK_SNAPSHOT_PROP];
    const files = snapshot && snapshot.files;
    if (!files || !files.length) {
        itkToast("还没有可保存的结果，请先运行一次工作流", "error");
        return;
    }
    const btn = node.widgets && node.widgets.find((w) => w.name === "保存当前结果");
    try {
        if (btn) btn.disabled = true;

        const params = {
            "子文件夹名称": itkWidgetValue(node, "子文件夹名称") || "批量导出",
            "文件夹策略": itkWidgetValue(node, "文件夹策略") || "自动新建（重名加序号）",
            "同名文件处理": itkWidgetValue(node, "同名文件处理") || "覆盖",
            "files": files,
        };

        const res = await fetch("/imagetoolkit/save/save_now", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(params),
        });
        const data = await res.json().catch(() => null);
        if (!res.ok || !data || !data.ok) {
            itkToast((data && data.error) || `保存失败（HTTP ${res.status}）`, "error");
            return;
        }

        itkAddSaveRecord(node, {
            time: itkNow(),
            count: data.saved,
            target: data.target,
            total_bytes: data.total_bytes,
            files: data.files || [],
        });
        itkToast(`已保存 ${data.saved} 张图片到：${data.target}`);

        // 按钮文字短暂反馈（主要状态看下方记录面板）
        if (btn) {
            btn.label = `✅ 已保存 ${data.saved} 张`;
            setTimeout(() => {
                btn.label = "保存";
                if (node.setDirtyCanvas) node.setDirtyCanvas(true, true);
            }, 2200);
        }

        if (node.graph) {
            try { node.graph.setDirtyCanvas(true, true); } catch (e) { /* 忽略 */ }
        }
    } catch (e) {
        itkToast("保存失败：" + (e && e.message || e), "error");
    } finally {
        if (btn) btn.disabled = false;
    }
}

function itkSetupSaveButton(node) {
    if (node.__itkSaveBtn) return;
    node.__itkSaveBtn = true;

    node.__itkFoldable = ITK_FOLDABLE;
    // 默认折叠保存参数；工作流里保存过状态就用它
    const stored = node.properties && node.properties[ITK_FOLD_PROP];
    node.__itkFolded = stored === undefined ? true : !!stored;

    // onExecuted：把后端回传的暂存清单存进 properties（随工作流保存）
    // 注意：ComfyUI 会把节点 ui 返回值按 value 展平合并，所以后端把
    // snapshot / savestat 包在了 list 里，这里取第 0 个。
    const prevExecuted = node.onExecuted;
    node.onExecuted = function (output) {
        try {
            const snap = output && output.snapshot;
            const entry = Array.isArray(snap) ? snap[0] : snap;
            if (entry && Array.isArray(entry.files) && entry.files.length) {
                if (!node.properties) node.properties = {};
                node.properties[ITK_SNAPSHOT_PROP] = entry;
            }
            // 自动保存：后端已经落盘，直接把这条记录写进历史
            const stat = output && output.savestat;
            const statEntry = Array.isArray(stat) ? stat[0] : stat;
            if (statEntry && statEntry.count) itkAddSaveRecord(this, statEntry);
            // 图片预览控件在执行后才挂上来：把面板挪到它下面、冻结预览高度
            itkLayoutRefresh(this);
        } catch (e) { /* 忽略 */ }
        return prevExecuted ? prevExecuted.apply(this, arguments) : undefined;
    };

    // 按钮放在节点控件末尾；serialize=false 不写进工作流
    const btn = node.addWidget("button", "保存当前结果", "保存", () => itkSaveNow(node));
    btn.serialize = false;

    // 「保存记录」面板：一个固定高度的自定义控件（min === max，布局引擎
    // 不会给它塞额外空间），排在控件区末尾（图片预览下方）。图片预览保持
    // 原生弹性：拖动节点时图片跟着缩放，面板只在底部占一条固定高度的带。
    const panel = node.addCustomWidget({
        type: "itk_save_panel",
        name: "保存记录",
        value: "",
        serialize: false,
        options: { surfaces: { canvas: "shown", vueNode: "never", panel: "never" } },
        __itkPanelH: itkPanelHeight(node),
        __itkHover: -1,
        __itkFoldHover: false,
        computeLayoutSize() {
            return { minHeight: this.__itkPanelH, maxHeight: this.__itkPanelH, minWidth: 1 };
        },
        draw(ctx, self, width, y, h, showText) {
            // 每帧先校正一下位置（图片预览可能在这之后才被挂上来）
            itkLayoutRefresh(self);
            itkDrawPanel(this, self, ctx, width, y);
        },
        onPointerDown(pointer, self, canvas) {
            return itkPanelClick(this, self, canvas, pointer);
        },
    });
    node.__itkPanelWidget = panel;
    itkLayoutRefresh(node);

    // 悬停高亮：节点级鼠标移动里顺带算一下面板的命中
    const prevMove = node.onMouseMove;
    node.onMouseMove = function (e, pos) {
        try {
            if (this.__itkPanelWidget) itkPanelHover(this.__itkPanelWidget, this, pos);
        } catch (e) { /* 忽略 */ }
        return prevMove ? prevMove.apply(this, arguments) : undefined;
    };

    // 折叠状态先按工作流里的值生效一次（itkSetup 的显隐刷新在 onNodeCreated
    // 里先跑过一轮，这里补齐折叠，并强制重算尺寸）
    if (typeof node.__itkApplyVisibility === "function") {
        node.__itkApplyVisibility(true);
    }
}

/* ==================================================================
 * 显隐联动
 * ================================================================== */

function itkMatches(cond, value) {
    if (typeof cond === "function") {
        try { return !!cond(value); } catch (e) { return true; }
    }
    if (Array.isArray(cond)) return cond.indexOf(value) >= 0;
    return cond === value;
}

function itkSetVisible(widget, show) {
    widget.hidden = !show;
    // ComfyUI 的文本/数字控件是 DOM 元素，需要一并隐藏，避免残留空白输入框
    const els = [widget.element, widget.inputEl, widget.labelEl];
    for (const el of els) {
        if (el && el.style) {
            el.style.display = show ? "" : "none";
        }
    }
}

function itkSetup(node, rules, typeName) {
    const find = (name) => node.widgets && node.widgets.find((w) => w.name === name);

    node.__itkType = typeName || "";

    // 新建节点：沿用同类型节点上次锁定的尺寸
    if (!node.__itkLock) {
        const mem = itkMemLoad()[node.__itkType];
        if (mem && mem[0] && mem[1]) {
            node.__itkLock = { w: Math.max(ITK_MIN_W, mem[0]), h: Math.max(ITK_MIN_H, mem[1]) };
        }
    }

    /** 只更新显隐，返回「可见控件集合」的指纹 */
    const updateVisibility = () => {
        for (const ctrlName of Object.keys(rules)) {
            const ctrl = find(ctrlName);
            if (!ctrl) continue;
            const map = rules[ctrlName];
            for (const targetName of Object.keys(map)) {
                const target = find(targetName);
                if (!target) continue;
                itkSetVisible(target, itkMatches(map[targetName], ctrl.value));
            }
        }
        // 折叠保存参数（图像批量保存）：整组强制隐藏，与上面的规则互相独立
        if (node.__itkFoldable) {
            for (const w of node.widgets || []) {
                if (node.__itkFoldable.has(w.name)) itkSetVisible(w, !node.__itkFolded);
            }
        }
        return (node.__itkFolded ? "F1|" : "F0|") +
            (node.widgets || []).map((w) => (w.hidden ? 0 : 1)).join("");
    };

    /** 重新计算并应用尺寸。force=true 表示忽略指纹变化强制刷新 */
    const apply = (force) => {
        const key = updateVisibility();
        if (!force && key === node.__itkVisKey) return;   // 显隐没变 → 完全不动尺寸
        node.__itkVisKey = key;
        itkApplySize(node);
    };

    // 供保存记录面板 / 折叠逻辑在参数变化后强制刷新一轮显隐 + 尺寸
    node.__itkApplyVisibility = () => { apply(true); };

    for (const ctrlName of Object.keys(rules)) {
        const ctrl = find(ctrlName);
        if (!ctrl || ctrl.__itkHooked) continue;
        ctrl.__itkHooked = true;
        const prev = ctrl.callback;
        ctrl.callback = function (value) {
            const r = prev ? prev.apply(this, arguments) : undefined;
            apply(false);
            return r;
        };
    }

    /**
     * 用户手动拖动的可靠信号。
     * litegraph 的 setSize() 内部也会回调 onResize，
     * 所以用「是否等于自己刚施加的尺寸」把自触发排掉。
     */
    if (!node.__itkResizeHooked) {
        node.__itkResizeHooked = true;
        const prevResize = node.onResize;
        node.onResize = function (size) {
            const self = node.__itkSelfSize;
            const own = self && size &&
                Math.abs(size[0] - self[0]) < 0.5 && Math.abs(size[1] - self[1]) < 0.5;
            if (own) {
                node.__itkSelfSize = null;
            } else if (node.__itkReady && size && size[0] && size[1]) {
                // 布局引擎按内容自动撑高节点时也会回调 onResize（l = 内容高度），
                // 那不算手动调整：只有高度明显偏离内容高度时才锁定
                let autoH = ITK_MIN_H;
                try {
                    const cs = node.computeSize();
                    if (cs && cs[1]) autoH = cs[1];
                } catch (e) { /* 忽略 */ }
                if (Math.abs(size[1] - autoH) > 4) itkLockSize(node, size[0], size[1]);   // 锁定，写进工作流 properties
            }
            return prevResize ? prevResize.apply(this, arguments) : undefined;
        };
    }

    // 从工作流载入：优先用它保存的锁定尺寸
    if (!node.__itkConfigureHooked) {
        node.__itkConfigureHooked = true;
        const prevCfg = node.onConfigure;
        node.onConfigure = function (info) {
            const r = prevCfg ? prevCfg.apply(this, arguments) : undefined;
            // 同步记下工作流里的尺寸：延时回调时 node.size 可能已被中间状态改动
            const src = (info && info.size) ? info.size : node.size;
            node.__itkSavedW = src && src[0] ? Math.max(ITK_MIN_W, src[0]) : 0;

            // 只认自己写的锁定标记；没有标记就说明没手动调过，高度按内容贴合
            const p = node.properties && node.properties[ITK_PROP];
            if (Array.isArray(p) && p[0] > 0 && p[1] > 0) {
                node.__itkLock = { w: Math.max(ITK_MIN_W, p[0]), h: Math.max(ITK_MIN_H, p[1]) };
            } else {
                node.__itkLock = null;
            }

            setTimeout(() => {
                try {
                    node.__itkVisKey = updateVisibility();     // 先按保存的参数刷新显隐
                    itkApplySize(node);
                } catch (e) { /* 忽略 */ }
            }, 0);
            return r;
        };
    }

    apply(true);
    setTimeout(() => apply(false), 0);
}

if (ITK_APP && typeof ITK_APP.registerExtension === "function") {
    ITK_APP.registerExtension({
        name: "ImageToolkitCN.WidgetVisibility",

        beforeRegisterNodeDef(nodeType, nodeData) {
            const typeName = nodeData && nodeData.name;
            const rules = typeName ? ITK_RULES[typeName] : null;
            if (!rules) return;

            const prevCreated = nodeType.prototype.onNodeCreated;
            nodeType.prototype.onNodeCreated = function () {
                const r = prevCreated ? prevCreated.apply(this, arguments) : undefined;
                try { itkSetup(this, rules, typeName); } catch (e) {
                    console.warn("[图像工具箱] 界面联动初始化失败（不影响运行）：", e);
                }
                if (typeName === "BatchImageSaverCN") {
                    try { itkSetupSaveButton(this); } catch (e) {
                        console.warn("[图像工具箱] 保存按钮初始化失败（不影响运行）：", e);
                    }
                }
                return r;
            };

            // 右键菜单：解除锁定，恢复自动尺寸
            const prevMenu = nodeType.prototype.getExtraMenuOptions;
            nodeType.prototype.getExtraMenuOptions = function (canvas, options) {
                const r = prevMenu ? prevMenu.apply(this, arguments) : undefined;
                const node = this;
                if (options && Array.isArray(options)) {
                    if (typeName === "BatchImageSaverCN") {
                        options.push({
                            content: node.__itkFolded ? "展开保存参数" : "折叠保存参数",
                            callback: () => {
                                try { itkSetFold(node, !node.__itkFolded); } catch (e) { /* 忽略 */ }
                            },
                        });
                    }
                    options.push({
                        content: "解除尺寸锁定（恢复自动尺寸）",
                        callback: () => { try { itkResetSize(node); } catch (e) { /* 忽略 */ } },
                    });
                }
                return r;
            };
        },
    });
} else {
    console.warn("[图像工具箱] 未获取到 ComfyUI 前端 app，跳过界面联动（节点功能不受影响）");
}
