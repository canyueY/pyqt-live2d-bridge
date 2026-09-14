# -*- coding: utf-8 -*-
"""程序化生成 Live2D ``.motion3.json``，并注册到 ``.model3.json``。

为什么需要这个模块
------------------
给模型加新动作通常要用 Cubism Editor 工程文件（``.cmo3``）。
但 ``motion3.json`` 本身只是「参数对时间的曲线」，格式公开且简单，
因此**没有工程文件也能加动作** —— 这对「从游戏里提取出来的模型」很关键：
这类模型往往只有 moc3 + 贴图 + 动作，没有可编辑工程。

实测出来的 ``Segments`` 编码（在 88 条曲线上逐条验证过，且与 Meta 的
``TotalSegmentCount`` / ``TotalPointCount`` 算术完全吻合）::

    Segments = [ 默认值, t=0 处的值, (段类型, 时间, 值) × N ]

    · 段类型 0 = 线性，2 = 阶跃，3 = 反向阶跃，1 = 贝塞尔(本模块不用)
    · Meta.TotalSegmentCount = ΣN
    · Meta.TotalPointCount   = ΣN + 曲线数
    · 每条曲线的末段「时间」必须等于 Meta.Duration

本模块用**逐帧采样 + 阶跃段**生成曲线（与原始动作文件的做法一致），
再对控制点之间做平滑插值，因此在 30fps 下视觉上是连续的。

用法::

    from live2d_bridge import motion3

    spec = motion3.spec(
        duration=3.0,
        curves={
            "ParamAngleZ": [(0.0, 0.0), (1.5, 2.0), (3.0, 0.0)],
            "ParamBreath": [(0.0, 0.0), (1.5, 1.0), (3.0, 0.0)],
        },
    )
    motion3.write_motion3("out/micro_sway.motion3.json", spec)
    motion3.register_motion_group("murasame.model3.json", "Micro",
                                  [{"File": "micro_sway.motion3.json"}])
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

__all__ = [
    "TYPE_LINEAR",
    "TYPE_STEPPED",
    "TYPE_INVERSE_STEPPED",
    "MotionSpec",
    "spec",
    "sample_curve",
    "build_motion3",
    "write_motion3",
    "register_motion_group",
    "unregister_motion_group",
    "verify_motion3",
]

TYPE_LINEAR = 0
TYPE_STEPPED = 2
TYPE_INVERSE_STEPPED = 3

DEFAULT_FPS = 30
DEFAULT_TARGET = "Parameter"


@dataclass
class MotionSpec:
    """一个动作的规格：每条参数一条控制点曲线。"""

    duration: float
    curves: dict[str, list[tuple[float, float]]] = field(default_factory=dict)
    loop: bool = False
    fps: int = DEFAULT_FPS
    target: str = DEFAULT_TARGET
    # 控制点之间的插值：smooth=平滑（默认），linear=折线
    easing: str = "smooth"
    # 采样后保留的小数位
    time_digits: int = 3
    value_digits: int = 4

    def parameters(self) -> list[str]:
        return list(self.curves)


def spec(
    duration: float,
    curves: dict[str, Sequence[tuple[float, float]]],
    *,
    loop: bool = False,
    fps: int = DEFAULT_FPS,
    target: str = DEFAULT_TARGET,
    easing: str = "smooth",
) -> MotionSpec:
    """构造 :class:`MotionSpec`，顺手做基本校验。"""
    if duration <= 0:
        raise ValueError("duration 必须为正")
    if not curves:
        raise ValueError("curves 不能为空")
    norm: dict[str, list[tuple[float, float]]] = {}
    for param, pts in curves.items():
        if not str(param).strip():
            raise ValueError("参数名不能为空")
        clean = sorted((float(t), float(v)) for t, v in pts)
        if len(clean) < 1:
            raise ValueError(f"{param} 至少要有一个控制点")
        if clean[0][0] > 0:
            # 允许不给 t=0 的点：自动用第一个控制点的值补上
            clean.insert(0, (0.0, clean[0][1]))
        if clean[-1][0] < duration:
            clean.append((float(duration), clean[-1][1]))
        if clean[0][0] < 0 or clean[-1][0] > duration + 1e-6:
            raise ValueError(f"{param} 的控制点时间超出 [0, {duration}]")
        norm[str(param)] = clean
    return MotionSpec(duration=float(duration), curves=norm, loop=bool(loop),
                      fps=int(fps), target=str(target), easing=str(easing))


def _smoothstep(t: float) -> float:
    return t * t * (3.0 - 2.0 * t)


def _interp(points: list[tuple[float, float]], t: float, easing: str) -> float:
    """在控制点之间求值。"""
    if t <= points[0][0]:
        return points[0][1]
    if t >= points[-1][0]:
        return points[-1][1]
    for i in range(len(points) - 1):
        t0, v0 = points[i]
        t1, v1 = points[i + 1]
        if t0 <= t <= t1:
            span = t1 - t0
            if span <= 1e-9:
                return v1
            k = (t - t0) / span
            if easing == "linear":
                return v0 + (v1 - v0) * k
            return v0 + (v1 - v0) * _smoothstep(k)
    return points[-1][1]


def sample_curve(points: list[tuple[float, float]], duration: float,
                 fps: int = DEFAULT_FPS, *, easing: str = "smooth",
                 start_index: int = 1) -> list[tuple[float, float]]:
    """把控制点采样成逐帧样本。

    ``start_index=1`` 表示从第 1 帧开始采样 —— 与原始动作文件一致：
    t=0 的值放在 Segments 头部的第二个位置，采样点从 1/fps 起。
    """
    step = 1.0 / max(1, int(fps))
    total = max(1, int(round(duration * fps)))
    out: list[tuple[float, float]] = []
    for i in range(start_index, total + 1):
        t = min(duration, i * step)
        out.append((t, _interp(points, t, easing)))
    if not out:
        out.append((duration, _interp(points, duration, easing)))
    return out


def build_motion3(s: MotionSpec) -> dict[str, Any]:
    """把 :class:`MotionSpec` 转成 ``motion3.json`` 的字典结构。"""
    curves: list[dict[str, Any]] = []
    total_segments = 0
    for param, points in s.curves.items():
        samples = sample_curve(points, s.duration, s.fps, easing=s.easing)
        v0 = _interp(points, 0.0, s.easing)
        segments: list[float] = [round(v0, s.value_digits), round(v0, s.value_digits)]
        for t, v in samples:
            segments.extend([
                TYPE_STEPPED,
                round(t, s.time_digits),
                round(v, s.value_digits),
            ])
        total_segments += len(samples)
        curves.append({"Target": s.target, "Id": param, "Segments": segments})

    curve_count = len(curves)
    return {
        "Version": 3,
        "Meta": {
            "Duration": round(s.duration, s.time_digits),
            "Fps": s.fps,
            "Loop": bool(s.loop),
            "AreBeziersRestricted": True,
            "CurveCount": curve_count,
            "TotalSegmentCount": total_segments,
            "TotalPointCount": total_segments + curve_count,
            "UserDataCount": 0,
            "TotalUserDataSize": 0,
        },
        "Curves": curves,
    }


def verify_motion3(data: dict[str, Any]) -> list[str]:
    """校验生成结果是否符合实测出来的编码约定；返回问题列表（空表示通过）。"""
    problems: list[str] = []
    meta = data.get("Meta") or {}
    curves = data.get("Curves") or []
    if data.get("Version") != 3:
        problems.append(f"Version 应为 3，实为 {data.get('Version')}")
    if meta.get("CurveCount") != len(curves):
        problems.append("Meta.CurveCount 与曲线数不一致")
    duration = float(meta.get("Duration") or 0)
    total_seg = 0
    for c in curves:
        seg = c.get("Segments") or []
        if len(seg) < 2 or (len(seg) - 2) % 3 != 0:
            problems.append(f"{c.get('Id')}: Segments 长度 {len(seg)} 不符合 2+3N")
            continue
        last_t = None
        n = 0
        for k in range(2, len(seg) - 2 + 1, 3):
            t, time, _v = seg[k], seg[k + 1], seg[k + 2]
            if t not in (TYPE_LINEAR, TYPE_STEPPED, TYPE_INVERSE_STEPPED, 1):
                problems.append(f"{c.get('Id')}: 非法段类型 {t}")
            if last_t is not None and time < last_t:
                problems.append(f"{c.get('Id')}: 时间非单调 {time} < {last_t}")
            last_t = time
            n += 1
        total_seg += n
        if last_t is not None and abs(float(last_t) - duration) > 1e-6:
            problems.append(f"{c.get('Id')}: 末段时间 {last_t} != Duration {duration}")
    if meta.get("TotalSegmentCount") != total_seg:
        problems.append("Meta.TotalSegmentCount 与实际不符")
    if meta.get("TotalPointCount") != total_seg + len(curves):
        problems.append("Meta.TotalPointCount 与实际不符")
    return problems


def write_motion3(path: str | os.PathLike[str], s: MotionSpec) -> dict[str, Any]:
    """生成并写入 ``.motion3.json``；写前自检，不通过就抛异常（不落盘）。"""
    data = build_motion3(s)
    problems = verify_motion3(data)
    if problems:
        raise ValueError("生成的 motion3 未通过自检: " + "; ".join(problems[:4]))
    p = os.fspath(path)
    parent = os.path.dirname(os.path.abspath(p))
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, p)
    return data


# ---------------------------------------------------------------------------
# 注册到 model3.json
# ---------------------------------------------------------------------------
def _load_model3(model3_path: str | os.PathLike[str]) -> dict[str, Any]:
    with open(os.fspath(model3_path), encoding="utf-8") as fh:
        return json.load(fh)


def _write_model3(model3_path: str | os.PathLike[str], data: dict[str, Any]) -> None:
    p = os.fspath(model3_path)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def register_motion_group(
    model3_path: str | os.PathLike[str],
    group: str,
    entries: Iterable[dict[str, Any]],
    *,
    backup: bool = True,
) -> dict[str, Any]:
    """把一组动作注册进 ``model3.json`` 的 ``FileReferences.Motions``。

    ``entries`` 每项至少要有 ``File``（相对 model3.json 的文件名）。
    **不带 ``Sound`` / ``Text`` 的条目就是静音无台词动作** —— 这正是
    「情绪过渡」层需要的：有动作但不抢台词、不播原声。

    返回 ``{"added": n, "replaced": bool, "backup": path|""}``。
    写之前会备份原文件（``backup=True``）。
    """
    p = os.fspath(model3_path)
    data = _load_model3(p)
    refs = data.setdefault("FileReferences", {})
    motions = refs.setdefault("Motions", {})
    items = [dict(e) for e in entries]
    replaced = group in motions
    motions[group] = items
    backup_path = ""
    if backup and not os.path.exists(p + ".orig"):
        backup_path = p + ".orig"
        shutil.copy2(p, backup_path)
    _write_model3(p, data)
    return {"added": len(items), "replaced": replaced, "backup": backup_path}


def unregister_motion_group(
    model3_path: str | os.PathLike[str],
    group: str,
) -> bool:
    """移除一个动作组（用于回滚）。返回是否真的移除了。"""
    p = os.fspath(model3_path)
    data = _load_model3(p)
    motions = ((data.get("FileReferences") or {}).get("Motions") or {})
    if group not in motions:
        return False
    del motions[group]
    _write_model3(p, data)
    return True
