# -*- coding: utf-8 -*-
"""Live2D 渲染后端 —— 与原 2D 图层渲染并存的替代实现。

设计原则
--------
1. **不引入硬依赖**：未安装 live2d-py 或模型缺失时，`Live2DRenderer.available()`
   返回 False，pet.py 自动回退到原图层渲染。
2. **接口收敛**：只暴露 pet.py 需要的几件事 —— 挂载/尺寸、表情、眨眼、动作、命中。
3. **表情用参数合成**：预设只有 7 个 exp3，远少于原 2D 的 42 个表情图层。
   因此表情由 `live2d_expressions` 做参数级合成（36 个语义表情），
   泪强度等为连续值，表达力优于原离散图层。

坐标系
------
`HitTest` 接受的是 **窗口像素坐标**（与 `Resize(w,h)` 同一坐标系），
实测确认无需换算到画布坐标。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from . import live2d_expressions as LX
from . import live2d_emotion_bridge as _EB
from . import live2d_motions as _MO
from . import paths as _paths

# ---- 可选依赖检测 ----------------------------------------------------------
_LIVE2D_IMPORT_ERROR: str | None = None
try:
    import live2d.v3 as _live2d

    LIVE2D_AVAILABLE = True
except Exception as _exc:  # pragma: no cover - 取决于环境
    _live2d = None  # type: ignore[assignment]
    LIVE2D_AVAILABLE = False
    _LIVE2D_IMPORT_ERROR = f"{type(_exc).__name__}: {_exc}"


# ---- 模型默认路径 ----------------------------------------------------------
# 不再写死到某个项目的目录结构里；解析规则见 live2d_bridge.paths。
# 调用方通常应显式传入模型路径，这里只作兜底。
DEFAULT_MODEL_JSON = _paths.resolve_model_json()

# 叠加栈深度上限：只用于「临时表情盖在稳定状态之上」，
# 超过就丢最旧的，避免异常路径下无限增长。
_MAX_STATE_STACK = 8


@dataclass
class RenderState:
    """一份完整的表情 / 姿势状态快照。

    存在的意义：**恢复不能靠反查**。
    从图层 ID 反推表情是有损的（丢了泪、脸红等叠加项，也丢了对表情参数的精确取值），
    而快照保存的是已经合成好的参数字典，恢复即逐字还原。

    ``pose_params`` 为 ``None`` 表示这份快照不管姿势通道；
    ``apply_state`` 只恢复快照实际携带的通道，避免用旧姿势覆盖新姿势。
    """

    expression: str | None = None
    expression_params: dict[str, float] = field(default_factory=dict)
    emotion_label: str = ""
    pose: str | None = None
    pose_params: dict[str, float] | None = None

    def describe(self) -> str:
        return (
            f"{self.expression or '-'}"
            f"({len(self.expression_params)} 参数)"
            f"{' +' + self.pose if self.pose_params is not None and self.pose else ''}"
        )

# ---- Qt 路径注入 -----------------------------------------------------------
# 直接运行 .venv\Scripts\python.exe 时（而非 `uv run`），Qt 的 DLL 与插件目录
# 不会进入搜索路径，Qt 会报找不到 windows 平台插件。这里显式补上。
_QT_PATHS_DONE = False


def qt_paths_prepared() -> bool:
    return _QT_PATHS_DONE


def prepare_qt_paths() -> bool:
    """为 PyQt5 注入 Qt5\\bin 与插件路径。可重复调用。"""
    global _QT_PATHS_DONE
    if _QT_PATHS_DONE:
        return True
    try:
        import PyQt5  # noqa: F401

        base = os.path.dirname(PyQt5.__file__)
        qtbin = os.path.join(base, "Qt5", "bin")
        qtplugins = os.path.join(base, "Qt5", "plugins")
        if hasattr(os, "add_dll_directory"):
            for d in (qtbin, os.path.join(qtplugins, "platforms")):
                if os.path.isdir(d):
                    try:
                        os.add_dll_directory(d)
                    except OSError:
                        pass
        os.environ.setdefault("QT_PLUGIN_PATH", qtplugins)
        from PyQt5.QtCore import QCoreApplication

        QCoreApplication.addLibraryPath(qtplugins)
        _QT_PATHS_DONE = True
        return True
    except Exception:
        return False


# ---- 图层 ID -> 表情参数映射（兼容旧接口）----------------------------------
# 由 live2d_expressions.LAYER_EXPRESSION 生成，覆盖原 EXPR_B 的全部 42 个图层。
LAYER_EXPRESSION_MAP: dict[int, tuple[list[str], dict[str, float]]] = {
    lid: ([], LX.compose(expression=name,
                         tear=LX.TEAR_LAYER_STRENGTH.get(lid)))
    for lid, name in LX.LAYER_EXPRESSION.items()
    if name != "EYE_CLOSED"
}
# 闭眼图层（原 blink 用）
LAYER_EXPRESSION_MAP[LX.EYE_CLOSED_LAYER] = (
    [], {"ParamEyeLOpen": 0.0, "ParamEyeROpen": 0.0})

BLINK_CLOSED_LAYER = LX.EYE_CLOSED_LAYER
BLINK_OPEN_LAYER = 1306

# 命中区名 -> 动作组（来自 model3.json 的 HitAreas）
HIT_AREA_MOTION: dict[str, str] = {
    "face": "Tapface",
    "hair": "Taphair",
    "xiongbu": "Tapxiongbu",
    "qunzi": "Tapqunzi",
    "leg": "Tapleg",
}


class Live2DRenderer:
    """封装 live2d-py 的模型生命周期、表情、眨眼、动作与命中检测。

    线程约束：所有方法必须在 Qt 主线程（即拥有 GL 上下文的线程）调用。

    状态合成模型
    ------------
    表情由「语义名」驱动而非预设 ID。`_compose()` 每帧把三路状态写入模型：
      1. 当前语义表情的参数（来自 live2d_expressions）
      2. 眨眼值（手动驱动时覆盖 ParamEyeLOpen/ROpen）
      3. 鼠标跟随参数
    这样表情、眨眼、视线不会互相覆盖（早期版本用 ResetParameters
    会在切换表情时冲掉眨眼值）。
    """

    def __init__(self, model_json: str | None = None,
                 gl_init: bool = True,
                 fps: int = 60,
                 auto_blink: bool = True,
                 auto_breath: bool = True,
                 mouse_tracking: bool = True,
                 pose_follow_emotion: bool = False) -> None:
        self._model_json = model_json or DEFAULT_MODEL_JSON
        self._model: Any = None
        self._gl_ready = False
        self._gl_init = gl_init
        self._fps = max(1, int(fps))
        self._auto_blink = bool(auto_blink)
        self._auto_breath = bool(auto_breath)
        self._mouse_tracking = bool(mouse_tracking)

        self._expressions: list[str] = []
        self._motion_groups: dict[str, int] = {}
        self._param_ids: list[str] = []
        self._hit_areas: list[str] = []
        self._canvas: tuple[float, float] = (1.0, 2.0)
        self._ppu: float = 1500.0

        # 状态
        self._eye_open: float = 1.0
        self._mouse: tuple[float, float] | None = None
        self._win_size: tuple[int, int] = (0, 0)

        # 当前表情（语义名）及其参数缓存
        self._expr_name: str | None = None
        self._expr_params: dict[str, float] = {}
        # 当前姿势
        self._pose_name: str | None = None
        self._pose_params: dict[str, float] = {}
        # 情绪驱动
        self._emotion_label: str = ""
        self._pose_follow_emotion: bool = bool(pose_follow_emotion)
        # 表情/姿势状态的叠加栈（见 push_state / pop_state）
        self._state_stack: list[RenderState] = []
        # 语义动作
        self._motion_name: str | None = None
        self._motion_finish_cb = None

    # ---- 生命周期 ---------------------------------------------------------
    @staticmethod
    def available() -> bool:
        return LIVE2D_AVAILABLE

    @staticmethod
    def unavailable_reason() -> str | None:
        return _LIVE2D_IMPORT_ERROR

    @staticmethod
    def model_exists(model_json: str | None = None) -> bool:
        return os.path.isfile(model_json or DEFAULT_MODEL_JSON)

    @property
    def ready(self) -> bool:
        return self._model is not None

    def initialize_gl(self) -> None:
        """在当前 GL 上下文中初始化 Cubism Core（每个上下文一次）。"""
        if self._gl_ready or not LIVE2D_AVAILABLE:
            return
        _live2d.glInit()
        self._gl_ready = True

    def load(self) -> bool:
        if not LIVE2D_AVAILABLE:
            return False
        if self._model is not None:
            return True
        if not os.path.isfile(self._model_json):
            return False
        self.initialize_gl()
        model = _live2d.LAppModel()
        model.LoadModelJson(self._model_json)
        self._model = model
        self._probe_capabilities()
        self.set_auto_blink(self._auto_blink)
        self.set_auto_breath(self._auto_breath)
        return True

    def _probe_capabilities(self) -> None:
        m = self._model
        for attr, fn in (
            ("_canvas", lambda: tuple(m.GetCanvasSize())),
            ("_ppu", lambda: float(m.GetPixelsPerUnit())),
            ("_expressions", lambda: list(m.GetExpressionIds())),
            ("_motion_groups", lambda: dict(m.GetMotionGroups())),
            ("_param_ids", lambda: list(m.GetParamIds())),
        ):
            try:
                setattr(self, attr, fn())
            except Exception:
                pass

    def set_hit_areas(self, names: list[str]) -> None:
        self._hit_areas = list(names)

    def resize(self, width: int, height: int) -> None:
        self.note_window_size(width, height)
        if self._model is not None:
            self._model.Resize(int(width), int(height))

    def note_window_size(self, width: int, height: int) -> None:
        self._win_size = (int(width), int(height))

    def dispose(self) -> None:
        self._model = None
        # 模型没了，快照里的参数也就没有意义了，一并清掉避免误恢复
        self._state_stack.clear()

    def draw(self) -> None:
        if self._model is None:
            return
        self._compose()
        self._model.Update()
        self._model.Draw()

    # ---- 能力查询 ---------------------------------------------------------
    @property
    def canvas_size(self) -> tuple[float, float]:
        return self._canvas

    @property
    def pixels_per_unit(self) -> float:
        return self._ppu

    def canvas_aspect(self) -> float:
        w, h = self._canvas
        return float(w) / float(h) if h > 0 else 0.5

    @property
    def expressions(self) -> list[str]:
        """模型自带的预设表情 ID（7 个）。"""
        return list(self._expressions)

    @property
    def semantic_expressions(self) -> list[str]:
        """可用的语义表情名（36 个）。"""
        return LX.all_expression_names()

    @property
    def expression_layer_keys(self) -> list[int]:
        return sorted(LAYER_EXPRESSION_MAP)

    @property
    def motion_groups(self) -> dict[str, int]:
        return dict(self._motion_groups)

    @property
    def param_ids(self) -> list[str]:
        return list(self._param_ids)

    # ---- 表情 -------------------------------------------------------------
    def set_expression_name(self, name: str, *,
                            tear: float | None = None,
                            cheek: float | None = None,
                            extra: dict[str, float] | None = None) -> bool:
        """按语义名设置表情。返回是否识别到该表情。"""
        if self._model is None:
            return False
        if name not in LX.EXPRESSIONS:
            return False
        self._expr_name = name
        self._expr_params = LX.compose(expression=name, tear=tear, cheek=cheek,
                                       extra=extra)
        return True

    def set_expression_params(self, params: dict[str, float],
                              name: str | None = None) -> None:
        """直接给一组参数（高级用法）。"""
        if self._model is None:
            return
        self._expr_name = name
        self._expr_params = dict(params or {})

    def clear_expression(self) -> None:
        self._expr_name = None
        self._expr_params = {}

    def current_expression_name(self) -> str | None:
        return self._expr_name

    def current_expression_label(self) -> str:
        return LX.expression_label(self._expr_name) if self._expr_name else ""

    # ---- 姿势（裙摆 / 蝴蝶结 / 手臂 / 身体前后）---------------------------
    @property
    def poses(self) -> list[str]:
        return LX.all_pose_names()

    def set_pose_name(self, name: str) -> bool:
        """按语义名设置姿势。返回是否识别到。

        注意：本模型没有换装能力（服装烘在 moc3 里），
        姿势只改变裙摆/蝴蝶结/手臂/身体的形态。
        """
        if self._model is None:
            return False
        if name not in LX.POSES:
            return False
        self._pose_name = name
        self._pose_params = LX.pose_params(name)
        return True

    def current_pose_name(self) -> str | None:
        return self._pose_name

    def current_pose_label(self) -> str:
        return LX.pose_label(self._pose_name) if self._pose_name else ""

    def apply_expression_layers(self, layers: list[int] | None,
                                *, emotion: str = "",
                                prefer_tone: bool = False,
                                face: str = "",
                                intensity: str = "") -> bool:
        """把 2D 图层 ID 列表翻译成语义表情。返回是否识别到。

        优先级：``face``（模型直接指定）> ``emotion``（情绪/语气标签）> 图层反查。

        图层反查放最后是有原因的：实测 2D 的 tone->图层映射把所有语气都指向
        同一组图层，经图层反推会丢失情绪信息。

        ``intensity``（low/mid/high）只缩放泪与脸红，不改变表情本身。

        只记录意图，实际写入在下一帧 `_compose()` 完成。
        """
        if self._model is None:
            return False
        # 1) 模型直接给的表情，或情绪标签驱动
        if (face or emotion) and self.apply_emotion(
                emotion, prefer_tone=prefer_tone, face=face, intensity=intensity):
            return True
        # 2) 退回图层映射
        name, tear = LX.layers_to_expression(layers)
        if name is None:
            return False
        if name == self._expr_name:
            return True
        return self.set_expression_name(name, tear=tear)

    def apply_expression_layer(self, layer_id: int) -> bool:
        return self.apply_expression_layers([layer_id])

    def current_expression_layer(self) -> int | None:
        """反查当前语义表情对应的首个图层 ID（兼容旧调用）。"""
        for lid, n in LX.LAYER_EXPRESSION.items():
            if n == self._expr_name:
                return lid
        return None

    # ---- 情绪驱动（文本输出时配相应动画）--------------------------------
    def apply_emotion(self, label: str, *, prefer_tone: bool = False,
                      face: str = "", intensity: str = "") -> bool:
        """用情绪/语气标签（或模型直接给的表情）驱动表情。返回是否识别到。

        label 来自两处：
          - 情感分析标签（chat.get_emotion），如「害羞」「高兴」
          - 分段语气 tone（ChatSegment.tone），如「活泼」「娇嗔」

        ``face`` 为模型在回复里直接指定的语义表情名（合法时优先）。
        ``intensity`` 为强度档位，只缩放泪/脸红。
        映射表见 live2d_emotion_bridge。
        """
        if self._model is None:
            return False
        info = _EB.resolve(label, prefer_tone=prefer_tone,
                           face=face, intensity=intensity)
        if not info.get("matched"):
            return False
        self._expr_name = info["expression"]
        self._expr_params = LX.compose(
            expression=info["expression"],
            tear=info.get("tear"),
            cheek=info.get("cheek"),
        )
        self._emotion_label = info.get("label", "")
        # 姿势只在明确配置时改变，避免频繁切换显得抽搐
        pose = info.get("pose")
        if pose and self._pose_follow_emotion:
            self._pose_name = pose
            self._pose_params = LX.pose_params(pose)
        return True

    def current_emotion_label(self) -> str:
        return self._emotion_label

    # ---- 状态快照 / 叠加栈 -------------------------------------------------
    # 用途：临时表情（张嘴、害羞、腮红等）盖在稳定状态之上，到点后**精确**还原，
    # 而不是靠「图层 ID -> 语义表情」反查。反查是有损的：泪、脸红、精确参数都会丢。
    def capture_state(self, *, include_pose: bool = False) -> RenderState:
        """抓一份当前状态的完整快照。

        ``include_pose=True`` 时把姿势也纳入快照；否则快照不管姿势通道，
        恢复时不会用旧姿势覆盖期间新设的姿势。
        """
        return RenderState(
            expression=self._expr_name,
            expression_params=dict(self._expr_params),
            emotion_label=self._emotion_label,
            pose=self._pose_name if include_pose else None,
            pose_params=dict(self._pose_params) if include_pose else None,
        )

    def apply_state(self, state: RenderState) -> None:
        """按快照恢复。只恢复快照实际携带的通道（姿势为 None 则不动姿势）。"""
        self._expr_name = state.expression
        self._expr_params = dict(state.expression_params)
        self._emotion_label = state.emotion_label
        if state.pose_params is not None:
            self._pose_name = state.pose
            self._pose_params = dict(state.pose_params)

    def push_state(self, *, include_pose: bool = False) -> int:
        """把当前状态压入叠加栈，返回压入后的深度。"""
        self._state_stack.append(self.capture_state(include_pose=include_pose))
        if len(self._state_stack) > _MAX_STATE_STACK:
            del self._state_stack[0]
        return len(self._state_stack)

    def pop_state(self) -> bool:
        """弹出栈顶并精确恢复；栈空返回 False（调用方需自行兜底）。"""
        if not self._state_stack:
            return False
        self.apply_state(self._state_stack.pop())
        return True

    @property
    def state_depth(self) -> int:
        """当前叠加栈深度。"""
        return len(self._state_stack)

    def clear_state_stack(self) -> None:
        """清空叠加栈（例如切换模型或重置时）。"""
        self._state_stack.clear()

    def _compose(self) -> None:
        """把当前状态合成到模型参数上（每帧调用）。"""
        m = self._model
        if m is None:
            return

        # 1) 表情参数
        if self._expr_params:
            self._set_params(self._expr_params)

        # 1b) 姿势参数（后写，优先级高于表情）
        if self._pose_params:
            self._set_params(self._pose_params)

        # 2) 眨眼覆盖
        if not self._auto_blink:
            for pid in ("ParamEyeLOpen", "ParamEyeROpen"):
                try:
                    m.SetParameterValue(pid, self._eye_open)
                except Exception:
                    pass

        # 3) 鼠标跟随
        if self._mouse is not None and self._mouse_tracking:
            ww, wh = self._win_size
            if ww > 0 and wh > 0:
                mx, my = self._mouse
                nx = max(-1.0, min(1.0, (mx / ww) * 2.0 - 1.0))
                ny = max(-1.0, min(1.0, (my / wh) * 2.0 - 1.0))
                self._set_params({
                    "ParamAngleX": nx * 30.0,
                    "ParamAngleY": -ny * 30.0,
                    "ParamAngleZ": nx * 10.0,
                    "ParamEyeBallX": nx,
                    "ParamEyeBallY": -ny,
                    "ParamBodyAngleX": nx * 10.0,
                })

    def _set_params(self, params: dict[str, float]) -> None:
        m = self._model
        if m is None:
            return
        for k, v in params.items():
            try:
                m.SetParameterValue(k, float(v))
            except Exception:
                continue

    def set_params(self, params: dict[str, float]) -> None:
        """公开的即时参数写入（不参与每帧合成）。"""
        self._set_params(params)

    def reset_expressions(self) -> None:
        if self._model is None:
            return
        self.clear_expression()
        try:
            self._model.ResetExpressions()
        except Exception:
            pass

    # ---- 眨眼 -------------------------------------------------------------
    def set_auto_blink(self, enabled: bool) -> None:
        self._auto_blink = bool(enabled)
        if self._model is not None:
            try:
                self._model.SetAutoBlinkEnable(bool(enabled))
            except Exception:
                pass

    def set_auto_breath(self, enabled: bool) -> None:
        self._auto_breath = bool(enabled)
        if self._model is not None:
            try:
                self._model.SetAutoBreathEnable(bool(enabled))
            except Exception:
                pass

    def set_eye_open(self, value: float) -> None:
        """value 1.0=睁眼，0.0=闭眼（原 blink 的 blend 需取反）。"""
        self._eye_open = max(0.0, min(1.0, float(value)))

    @property
    def eye_open(self) -> float:
        return self._eye_open

    # ---- 动作 -------------------------------------------------------------
    def start_motion(self, group: str, priority: int | None = None) -> bool:
        if self._model is None or group not in self._motion_groups:
            return False
        try:
            pri = (priority if priority is not None
                   else getattr(_live2d, "MotionPriority").FORCE)
            self._model.StartRandomMotion(group, pri)
            return True
        except Exception:
            return False

    # ---- 语义动作（带原声 / 台词）----------------------------------------
    def play_motion(self, name: str) -> dict[str, Any] | None:
        """播放语义动作，返回该动作的原声与台词信息（供上层决定如何处理）。

        返回 dict：group / index / label / voice_path / text / duration
        未识别或播放失败返回 None。

        兼容性：若传入的是**动作分组名**（如 "Taphair"）而非语义名
        （如 "hair_point"），则按分组随机播放，并尝试反查原声信息。

        注意：本模型的动作文件**已烘焙口型曲线**（含 ParamMouthOpenY），
        播放时嘴会自己动，无需额外对口型。
        """
        if self._model is None:
            return None
        info = _MO.motion(name)
        if not info:
            # 不是语义名：尝试按分组名处理（兼容调用方传原始分组）
            return self._play_motion_by_group(name)
        group = info["group"]
        idx = int(info["index"])
        if group not in self._motion_groups:
            return None
        try:
            pri = getattr(_live2d, "MotionPriority").FORCE
            if self._motion_finish_cb is not None:
                self._model.StartMotion(group, idx, pri,
                                        onFinish=self._on_motion_finished)
            else:
                self._model.StartMotion(group, idx, pri)
        except Exception:
            return None
        self._motion_name = name
        return {
            "name": name,
            "group": group,
            "index": idx,
            "label": info.get("label", name),
            "voice_path": self.voice_path(name),
            "text": info.get("text", ""),
            "duration": float(info.get("duration") or 0.0),
            "kind": info.get("kind", ""),
        }

    def _play_motion_by_group(self, group: str) -> dict[str, Any] | None:
        """按动作分组随机播放（兼容旧调用），并尽力反查原声/台词。"""
        if group not in self._motion_groups:
            return None
        try:
            pri = getattr(_live2d, "MotionPriority").FORCE
            self._model.StartRandomMotion(group, pri)
        except Exception:
            return None
        # 反查该分组下的语义动作，取第一个带原声的作为信息代表
        candidates = [n for n, m in _MO.MOTIONS.items() if m["group"] == group]
        with_voice = [n for n in candidates if _MO.has_voice(n)]
        pick = (with_voice or candidates or [None])[0]
        if not pick:
            self._motion_name = None
            return {"name": "", "group": group, "index": -1,
                    "label": group, "voice_path": "", "text": "",
                    "duration": 0.0, "kind": ""}
        self._motion_name = pick
        md = _MO.motion(pick) or {}
        return {
            "name": pick,
            "group": group,
            "index": int(md.get("index", -1)),
            "label": md.get("label", group),
            "voice_path": self.voice_path(pick),
            "text": md.get("text", ""),
            "duration": float(md.get("duration") or 0.0),
            "kind": md.get("kind", ""),
        }

    def _on_motion_finished(self, group: str, no: int) -> None:
        """动作播完回调（在当前 GL 上下文的线程内触发）。"""
        cb = self._motion_finish_cb
        if cb is None:
            return
        try:
            cb(self._motion_name or group)
        except Exception:
            pass

    def set_motion_finish_callback(self, cb) -> None:
        """设置动作完成回调，参数为语义动作名。"""
        self._motion_finish_cb = cb

    def voice_path(self, name: str) -> str:
        """语义动作对应原声 wav 的绝对路径（无原声返回空串）。"""
        fname = _MO.voice_file(name)
        if not fname:
            return ""
        path = os.path.join(os.path.dirname(self._model_json), fname)
        return path if os.path.isfile(path) else ""

    def motion_catalog(self) -> list[dict[str, Any]]:
        """给管理后台用的动作目录（补上语音文件是否存在）。"""
        out = []
        for item in _MO.catalog():
            row = dict(item)
            row["voice_exists"] = bool(self.voice_path(row["name"]))
            out.append(row)
        return out

    # ---- 命中检测与鼠标 ---------------------------------------------------
    def hit_test(self, area: str, x: float, y: float) -> bool:
        """窗口像素坐标下的命中测试。"""
        if self._model is None:
            return False
        try:
            return bool(self._model.HitTest(area, float(x), float(y)))
        except Exception:
            return False

    def hit_test_any(self, x: float, y: float,
                     areas: list[str] | None = None) -> str | None:
        for area in (areas or list(self._hit_areas) or list(HIT_AREA_MOTION)):
            if self.hit_test(area, x, y):
                return area
        return None

    def trigger_area_motion(self, area: str) -> bool:
        group = HIT_AREA_MOTION.get(area)
        return self.start_motion(group) if group else False

    def mouse_move(self, x: float, y: float) -> None:
        self._mouse = (float(x), float(y))

    def clear_mouse(self) -> None:
        self._mouse = None

    def window_to_canvas(self, x: float, y: float) -> tuple[float, float]:
        """窗口像素 -> 画布像素（仅用于需要画布坐标的场合）。"""
        ww, wh = self._win_size
        if ww <= 0 or wh <= 0:
            return float(x), float(y)
        cw, ch = self._canvas
        return (float(x) * float(cw) * self._ppu / ww,
                float(y) * float(ch) * self._ppu / wh)
