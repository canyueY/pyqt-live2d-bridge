# -*- coding: utf-8 -*-
"""路径解析：模型文件与预览握手文件的默认位置。

本包不再假设自己位于某个特定项目的目录结构里。默认路径按以下优先级解析：

1. 调用方显式传入（推荐，也是唯一不会歧义的方式）
2. 环境变量 ``LIVE2D_MODEL_JSON`` / ``LIVE2D_PREVIEW_PATH``
3. 当前工作目录下的约定位置（向后兼容老项目的布局）：
   - 模型：``./live2d模型/<任意子目录>/*.model3.json``，取排序后第一个
   - 预览：``./data/_l2d_preview.json``

第 3 条只是兜底。若解析不到，模型相关函数会得到空字符串，
调用方应自行传入模型路径。
"""

from __future__ import annotations

import glob
import os

__all__ = [
    "ENV_MODEL_JSON",
    "ENV_PREVIEW_PATH",
    "resolve_model_json",
    "resolve_preview_path",
    "clear_caches",
]

ENV_MODEL_JSON = "LIVE2D_MODEL_JSON"
ENV_PREVIEW_PATH = "LIVE2D_PREVIEW_PATH"

# 约定布局下的搜索模式（兼容旧项目）
_LEGACY_MODEL_GLOBS = (
    os.path.join("live2d模型", "*", "*.model3.json"),
    os.path.join("live2d_models", "*", "*.model3.json"),
    os.path.join("models", "*", "*.model3.json"),
)
_LEGACY_PREVIEW_RELPATH = os.path.join("data", "_l2d_preview.json")


def resolve_model_json(cwd: str | None = None) -> str:
    """解析默认模型中 (.model3.json) 的路径；解析不到返回空串。"""
    env = os.environ.get(ENV_MODEL_JSON, "").strip()
    if env:
        return env
    base = cwd or os.getcwd()
    for pattern in _LEGACY_MODEL_GLOBS:
        hits = sorted(glob.glob(os.path.join(base, pattern)))
        if hits:
            return hits[0]
    return ""


def resolve_preview_path(cwd: str | None = None) -> str:
    """解析预览握手文件的路径（用于管理后台 / 外部工具驱动预览）。"""
    env = os.environ.get(ENV_PREVIEW_PATH, "").strip()
    if env:
        return env
    base = cwd or os.getcwd()
    return os.path.join(base, _LEGACY_PREVIEW_RELPATH)


def clear_caches() -> None:
    """清空模块级缓存（改过环境变量或工作目录后调用）。

    本模块自身不缓存，但 ``live2d_render`` / ``live2d_info`` 在导入时
    会把默认路径固化成模块常量。需要重新解析时用这个函数刷新它们。
    """
    import importlib

    for mod in ("live2d_bridge.live2d_render", "live2d_bridge.live2d_info"):
        try:
            m = importlib.import_module(mod)
        except Exception:
            continue
        if mod.endswith("live2d_render"):
            m.DEFAULT_MODEL_JSON = resolve_model_json()
        else:
            m.DEFAULT_MODEL = resolve_model_json()
            m.PREVIEW_PATH = resolve_preview_path()
