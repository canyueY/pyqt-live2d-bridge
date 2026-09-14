# -*- coding: utf-8 -*-
"""Live2D 模型信息读取与预览通道（供管理后台使用）。

**不依赖 live2d-py / GL 上下文**：管理后台（log_viewer.py）本身不渲染模型，
只需要读取元数据。真正的渲染在桌宠进程里，因此这里还提供一条
「预览意图文件」通道：

    管理后台写 data/_l2d_preview.json
        -> 桌宠轮询该文件
        -> 桌宠把它应用到模型上
        -> 桌宠把生效结果写回同一文件的 applied 字段
        -> 管理后台读回，显示「已生效」

这样后台无需直接触达桌宠进程，复用现有「文件 + 轮询」风格。
"""
from __future__ import annotations

import json
import os
import struct
import time
from typing import Any

from . import paths as _paths

# 路径不再写死到某个项目的目录结构里；解析规则见 live2d_bridge.paths。
PROJECT_ROOT = os.getcwd()
PREVIEW_PATH = _paths.resolve_preview_path()
DEFAULT_MODEL = _paths.resolve_model_json()

# moc3 版本号 -> Cubism 版本说明
_MOC3_VERSIONS = {
    3: "Cubism 3.0+",
    4: "Cubism 3.3+",
    5: "Cubism 4.0+",
    6: "Cubism 4.2+",
    7: "Cubism 5.0+",
}


def _png_size(path: str) -> tuple[int, int] | None:
    """读 PNG 头部拿宽高（IHDR 固定在前 24 字节）。"""
    try:
        with open(path, "rb") as fh:
            head = fh.read(24)
        if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n":
            return None
        w, h = struct.unpack(">II", head[16:24])
        return int(w), int(h)
    except Exception:
        return None


def _moc3_version(path: str) -> int | None:
    """moc3 第 5 字节是版本号（签名 'MOC3' 之后）。"""
    try:
        with open(path, "rb") as fh:
            head = fh.read(5)
        if len(head) < 5 or head[:4] != b"MOC3":
            return None
        return int(head[4])
    except Exception:
        return None


def _size_text(num_bytes: int) -> str:
    if num_bytes >= 1024 * 1024:
        return f"{num_bytes / 1024 / 1024:.2f} MB"
    if num_bytes >= 1024:
        return f"{num_bytes / 1024:.0f} KB"
    return f"{num_bytes} B"


def model_metadata(model_path: str | None = None) -> dict[str, Any]:
    """读取模型元数据。失败时返回 ok=False 与原因，不抛异常。"""
    path = model_path or DEFAULT_MODEL
    out: dict[str, Any] = {
        "ok": False,
        "path": path,
        "exists": os.path.isfile(path),
    }
    if not out["exists"]:
        out["error"] = "模型文件不存在"
        return out
    try:
        with open(path, encoding="utf-8") as fh:
            m3 = json.load(fh)
    except Exception as exc:
        out["error"] = f"model3.json 解析失败: {exc}"
        return out

    model_dir = os.path.dirname(path)
    refs = m3.get("FileReferences") or {}

    # moc3
    moc_name = refs.get("Moc") or ""
    moc_path = os.path.join(model_dir, moc_name) if moc_name else ""
    moc_ver = _moc3_version(moc_path) if moc_path else None
    out["moc"] = {
        "file": moc_name,
        "exists": bool(moc_path and os.path.isfile(moc_path)),
        "version": moc_ver,
        "cubism": _MOC3_VERSIONS.get(moc_ver or 0, "未知"),
        "size_text": _size_text(os.path.getsize(moc_path))
        if moc_path and os.path.isfile(moc_path) else "-",
    }

    # 贴图
    textures = []
    for tname in (refs.get("Textures") or []):
        tpath = os.path.join(model_dir, tname)
        size = _png_size(tpath) if os.path.isfile(tpath) else None
        textures.append({
            "file": tname,
            "exists": os.path.isfile(tpath),
            "width": size[0] if size else None,
            "height": size[1] if size else None,
            "size_text": _size_text(os.path.getsize(tpath))
            if os.path.isfile(tpath) else "-",
        })
    out["textures"] = textures
    if textures and textures[0].get("width"):
        out["texture_size"] = f"{textures[0]['width']}x{textures[0]['height']}"

    # 动作（含台词 / 语音）
    motions: dict[str, list[dict[str, Any]]] = {}
    total_motions = 0
    total_sounds = 0
    for group, items in (refs.get("Motions") or {}).items():
        rows = []
        for it in (items or []):
            if not isinstance(it, dict):
                continue
            snd = it.get("Sound") or ""
            rows.append({
                "file": it.get("File") or "",
                "sound": os.path.basename(snd) if snd else "",
                "text": it.get("Text") or "",
                "interruptable": bool(it.get("Interruptable", True)),
            })
            if snd:
                total_sounds += 1
        motions[group] = rows
        total_motions += len(rows)
    out["motions"] = motions
    out["motion_summary"] = {
        "groups": len(motions),
        "total": total_motions,
        "with_sound": total_sounds,
    }

    # 表情（预设）
    exprs = []
    for e in (refs.get("Expressions") or []):
        if isinstance(e, dict):
            exprs.append({"name": e.get("Name") or "", "file": e.get("File") or ""})
    out["expressions"] = exprs

    # 命中区
    hit_areas = []
    for h in (m3.get("HitAreas") or []):
        if isinstance(h, dict):
            hit_areas.append({
                "name": h.get("Name") or "",
                "id": h.get("Id") or "",
                "motion": h.get("Motion") or "",
            })
    out["hit_areas"] = hit_areas

    # 物理 / 控制器
    out["physics"] = bool(refs.get("Physics") or refs.get("PhysicsV2"))
    ctrls = m3.get("Controllers") or {}
    out["controllers"] = [k for k in ctrls.keys() if ctrls.get(k)]
    out["model_version"] = m3.get("Version")
    out["file_size_text"] = _size_text(os.path.getsize(path))
    out["ok"] = True
    return out


def runtime_snapshot(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """汇总「当前渲染配置 + 可用选项」，供后台表单使用。"""
    from . import live2d_expressions as LX
    from . import live2d_emotion_bridge as EB

    cfg = cfg or {}
    block = cfg.get("live2d") if isinstance(cfg.get("live2d"), dict) else {}
    cfg_model = str(block.get("model") or "").strip()
    meta = model_metadata(cfg_model or DEFAULT_MODEL)

    # 未装 live2d-py 时后台仍可用，只是标记为不可渲染
    available, reason = True, None
    try:
        from .live2d_render import Live2DRenderer

        available = Live2DRenderer.available()
        reason = Live2DRenderer.unavailable_reason()
    except Exception as exc:
        available, reason = False, f"{type(exc).__name__}: {exc}"

    expressions = [
        {"value": n, "label": LX.expression_label(n), "group": _expr_group(n)}
        for n in LX.all_expression_names()
    ]
    poses = [
        {"value": n, "label": LX.pose_label(n)}
        for n in LX.all_pose_names()
    ]
    bridge = EB.known_labels()

    return {
        "enable": bool(block.get("enable", False)),
        "render_mode": "live2d" if block.get("enable") else "sprite",
        "render_mode_label": "Live2D（新模型）" if block.get("enable") else "2D 图层立绘（原版）",
        "model": cfg_model or DEFAULT_MODEL,
        "model_is_default": not cfg_model,
        "fps": int(block.get("fps", 60)),
        "auto_blink": bool(block.get("auto_blink", False)),
        "auto_breath": bool(block.get("auto_breath", True)),
        "mouse_tracking": bool(block.get("mouse_tracking", True)),
        "tap_motion": bool(block.get("tap_motion", True)),
        "emotion_expression": bool(block.get("emotion_expression", True)),
        "emotion_motion": bool(block.get("emotion_motion", True)),
        "pose_follow_emotion": bool(block.get("pose_follow_emotion", False)),
        "idle_pose": str(block.get("idle_pose") or ""),
        "tap_pose": str(block.get("tap_pose") or ""),
        "hit_areas": list(block.get("hit_areas") or []),
        "available_hit_areas": [h.get("name") for h in meta.get("hit_areas", [])],
        "backend_available": available,
        "backend_error": reason,
        "expressions": expressions,
        "poses": poses,
        "motions": _motion_catalog(model_path=cfg_model or DEFAULT_MODEL),
        "emotion_map": {
            k: v for k, v in bridge.get("emotion", {}).items()
        } if isinstance(bridge.get("emotion"), dict) else {},
        "emotion_labels": bridge.get("emotion", []),
        "tone_labels": bridge.get("tone", []),
        "model_info": meta,
    }


def _motion_catalog(model_path: str) -> list[dict[str, Any]]:
    """语义动作目录（含原声是否存在），供管理后台预览与展示。"""
    try:
        from . import live2d_motions as MO

        model_dir = os.path.dirname(model_path)
        out = []
        for item in MO.catalog():
            row = dict(item)
            voice = str(row.get("voice") or "")
            row["voice_exists"] = bool(
                voice and os.path.isfile(os.path.join(model_dir, voice))
            )
            row["area"] = next(
                (a for a, names in MO.HIT_AREA_MOTIONS.items() if row["name"] in names),
                "",
            )
            out.append(row)
        return out
    except Exception:
        return []


def _expr_group(name: str) -> str:
    """给表情分类，便于后台分组显示。"""
    groups = {
        "平静": ("neutral", "calm"),
        "高兴": ("smile", "happy", "grin", "smug", "relieved"),
        "害羞": ("shy", "blush", "embarrassed"),
        "惊讶": ("surprised", "shocked", "startled"),
        "难过": ("sad", "crying", "sobbing", "lonely", "smile_tears"),
        "生气": ("angry", "furious", "pout", "sulky", "gloomy", "menacing"),
        "其它": ("sideeye", "skeptical", "serious", "thinking", "determined",
                 "teasing", "mischievous", "wink", "sleepy", "lost", "upward",
                 "resigned"),
    }
    for label, names in groups.items():
        if name in names:
            return label
    return "其它"


# ---------------------------------------------------------------------------
# 预览通道
# ---------------------------------------------------------------------------
def read_preview() -> dict[str, Any]:
    try:
        with open(PREVIEW_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def write_preview(kind: str, value: str, *, extra: dict | None = None) -> dict[str, Any]:
    """写入预览意图。kind ∈ expression / pose / motion / clear。"""
    payload = {
        "kind": str(kind or "").strip(),
        "value": str(value or "").strip(),
        "requested_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "seq": int(time.time() * 1000),
    }
    if extra:
        payload.update(extra)
    os.makedirs(os.path.dirname(PREVIEW_PATH), exist_ok=True)
    tmp = PREVIEW_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, PREVIEW_PATH)
    return payload


def clear_preview() -> None:
    try:
        if os.path.isfile(PREVIEW_PATH):
            os.remove(PREVIEW_PATH)
    except Exception:
        pass


def mark_applied(seq: int, *, label: str = "", note: str = "") -> bool:
    """桌宠应用完预览后回写状态，供后台显示「已生效」。

    只更新已有文件，不新建 —— 避免文件被清掉后又复活。
    """
    try:
        if not os.path.isfile(PREVIEW_PATH):
            return False
        with open(PREVIEW_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return False
        data["applied"] = int(seq)
        data["applied_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        if label:
            data["applied_label"] = str(label)
        if note:
            data["pet_note"] = str(note)
        tmp = PREVIEW_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, PREVIEW_PATH)
        return True
    except Exception:
        return False


def preview_status() -> dict[str, Any]:
    """读回预览状态，供后台显示「已生效 / 待桌宠应用」。"""
    data = read_preview()
    if not data:
        return {"pending": False}
    seq = data.get("seq")
    applied = data.get("applied")
    return {
        "pending": applied != seq,
        "intent": data,
        "applied": applied == seq,
        "applied_at": data.get("applied_at"),
        "applied_label": data.get("applied_label"),
        "pet_note": data.get("pet_note"),
    }
