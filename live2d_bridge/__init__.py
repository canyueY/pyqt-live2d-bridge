# -*- coding: utf-8 -*-
"""pyqt-live2d-bridge —— PyQt5 里的 Live2D Cubism 渲染与动作/表情绑定。

设计目标：把「让一个 Live2D 模型在 PyQt5 窗口里活起来」这件事从业务代码里剥出来。

模块划分（文件名保留自原始项目，便于对照）：

======================  ==================================================
``live2d_render``       渲染器（OpenGL 画布、眨眼、命中区、动作播放、口型）
``live2d_motions``      动作目录：分组、索引、时段可用性、台词、语音文件
``live2d_expressions``  表情 / 姿势参数合成（可叠加，返回参数字典）
``live2d_emotion_bridge``  情绪标签 -> 表情 / 眼泪 / 脸红 / 姿势的映射
``live2d_info``         模型元信息、运行时快照、预览握手文件
``paths``               模型与预览文件路径解析（本包不假设目录结构）
======================  ==================================================

典型用法::

    from live2d_bridge import Live2DRenderer, prepare_qt_paths

    prepare_qt_paths()                       # 直接跑 python.exe 时补 Qt 插件路径
    renderer = Live2DRenderer("path/to/model3.json")

模型路径不写死：优先用你传入的路径，其次读环境变量 ``LIVE2D_MODEL_JSON``，
最后才在当前工作目录下按约定布局兜底查找（见 :mod:`live2d_bridge.paths`）。
"""

from __future__ import annotations

from . import paths
from . import motion3
from .live2d_emotion_bridge import (
    expression_for_emotion,
    expression_for_label,
    expression_for_tone,
    known_labels,
)
from .live2d_expressions import (
    all_expression_names,
    all_pose_names,
    compose,
    compose_full,
    expression_label,
    expression_params,
    layers_to_expression,
    pose_params,
)
from .live2d_info import (
    mark_applied,
    model_metadata,
    preview_status,
    read_preview,
    runtime_snapshot,
    write_preview,
)
from .live2d_motions import (
    EMOTION_MOTION_SPEAK,
    all_motion_names,
    catalog,
    duration,
    emotion_for_area,
    has_voice,
    is_time_ok,
    motion,
    motion_group,
    motion_index,
    motion_label,
    motion_text,
    motions_for_area,
    motions_for_emotion,
    motions_for_scene,
    voice_file,
)
from .live2d_render import (
    DEFAULT_MODEL_JSON,
    HIT_AREA_MOTION,
    LIVE2D_AVAILABLE,
    Live2DRenderer,
    RenderState,
    prepare_qt_paths,
    qt_paths_prepared,
)

__version__ = "0.1.0"

_runtime_ready = False


def init_runtime() -> bool:
    """在**创建 QApplication 之前**调用：补 Qt 插件路径 + 初始化 Live2D 原生运行时。

    这两步的顺序是有硬性要求的，踩错会得到原生层崩溃而不是 Python 异常：

    1. ``prepare_qt_paths()`` —— 直接在 venv 里跑 ``python.exe`` 时，Qt 的
       ``Qt5\\bin`` 与插件目录不在搜索路径里，必须先补上，否则 Qt 报
       ``Could not find the Qt platform plugin "windows"``。
    2. ``live2d.v3.init()`` —— 必须在 ``QApplication`` 构造**之前**调用
       （上游 live2d-py 的要求）。漏掉或顺序反了，渲染时会直接
       ``0xC0000005`` 访问违例，而且往往连 Python 的 traceback 都看不到。

    返回是否初始化成功；失败时仍可导入本包使用纯数据模块（动作目录 /
    表情合成 / 情绪映射），只是不能真正渲染。

    典型用法::

        import live2d_bridge as B
        B.init_runtime()                       # 必须在 QApplication 之前
        app = QApplication(sys.argv)
    """
    global _runtime_ready
    if _runtime_ready:
        return True
    if not prepare_qt_paths():
        return False
    if not LIVE2D_AVAILABLE:
        return False
    try:
        import live2d.v3 as _v3

        _v3.init()
    except Exception:  # pragma: no cover - 取决于环境
        return False
    _runtime_ready = True
    return True

__all__ = [
    # 路径
    "paths",
    # 动作生成
    "motion3",
    # 渲染
    "Live2DRenderer",
    "RenderState",
    "HIT_AREA_MOTION",
    "DEFAULT_MODEL_JSON",
    "LIVE2D_AVAILABLE",
    "init_runtime",
    "prepare_qt_paths",
    "qt_paths_prepared",
    # 动作
    "motion",
    "motion_label",
    "motion_group",
    "motion_index",
    "motions_for_area",
    "emotion_for_area",
    "motions_for_emotion",
    "motions_for_scene",
    "is_time_ok",
    "has_voice",
    "voice_file",
    "motion_text",
    "duration",
    "all_motion_names",
    "catalog",
    "EMOTION_MOTION_SPEAK",
    # 表情 / 姿势
    "expression_params",
    "expression_label",
    "all_expression_names",
    "pose_params",
    "all_pose_names",
    "compose",
    "compose_full",
    "layers_to_expression",
    # 情绪桥接
    "expression_for_emotion",
    "expression_for_tone",
    "expression_for_label",
    "known_labels",
    # 信息 / 预览
    "model_metadata",
    "runtime_snapshot",
    "read_preview",
    "write_preview",
    "preview_status",
    "mark_applied",
    # 元信息
    "__version__",
]
