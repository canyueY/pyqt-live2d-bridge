# -*- coding: utf-8 -*-
"""Live2D 表情/情绪合成库。

背景
----
本模型只提供 7 个预设表情（`exp1`~`exp7`，`exp3` 是固定参数组合），
而原 2D 立绘有 42 个表情图层。直接一对一映射会大幅丢表情。

本模块改为**参数级合成**：把表情拆成相互独立的维度，再按语义组合。
Cubism 的 `SetParameterValue` 会按参数定义范围自动钳制，因此可安全
地把值推到极端。

维度（依据 exp3 文件反解 + 模型参数表）
--------------------------------------
  眼睛      : 睁 / 闭 / 弯眼笑 / 半闭 / 睁大 / 眼珠偏移
  眉毛      : 高度(BrowY) / 角度(BrowAngle) / 形态(BrowForm)
  嘴        : MouthForm(形状) / MouthOpenY(开合)
  效果      : 泪(TeShuEyeChuXian) / 黑化(HeiHuaShadow) / 线条(XianTiaoChuXian)
  附加      : 脸红(Cheek) / 特殊嘴(TeShuZuiCX) / 眼珠缩放 / 高光

约定
----
- 正面情绪 MouthForm = +1，负面 = -1（由 exp2/exp3 对照得出）
- 眉毛上扬 = BrowY +1，下压 = -1；BrowAngle 负=外扬(开心)，正=内压(生气/困扰)
- 泪使用连续强度 0.0~1.0，比原 2D 的 10 个离散泪图层更细腻
"""
from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# 单维度基础构件（值为要写入的参数）
# ---------------------------------------------------------------------------

# 眼睛
EYE_OPEN: dict[str, float] = {"ParamEyeLOpen": 1.0, "ParamEyeROpen": 1.0}
EYE_CLOSED: dict[str, float] = {"ParamEyeLOpen": 0.0, "ParamEyeROpen": 0.0}
EYE_SMILE: dict[str, float] = {"ParamEyeLSmile": 1.0, "ParamEyeRSmile": 1.0}
EYE_HALF: dict[str, float] = {"ParamEyeLOpen": 0.5, "ParamEyeROpen": 0.5}
EYE_WIDE: dict[str, float] = {"ParamEyeLOpen": 1.0, "ParamEyeROpen": 1.0,
                              "ParamYanZhuSuoFangL": -1.0, "ParamYanZhuSuoFangR": -1.0}

# 眉毛
BROW_UP: dict[str, float] = {"ParamBrowLY": 1.0, "ParamBrowRY": 1.0,
                             "ParamBrowLAngle": -1.0, "ParamBrowRAngle": -1.0,
                             "ParamBrowLForm": 0.7, "ParamBrowRForm": 0.7}
BROW_DOWN: dict[str, float] = {"ParamBrowLY": -1.0, "ParamBrowRY": -1.0,
                               "ParamBrowLAngle": 1.0, "ParamBrowRAngle": 1.0,
                               "ParamBrowLForm": 0.7, "ParamBrowRForm": 0.7}
BROW_FLAT: dict[str, float] = {"ParamBrowLY": 0.0, "ParamBrowRY": 0.0,
                               "ParamBrowLAngle": 0.0, "ParamBrowRAngle": 0.0,
                               "ParamBrowLForm": 0.0, "ParamBrowRForm": 0.0}
BROW_SAD: dict[str, float] = {"ParamBrowLY": 1.0, "ParamBrowRY": 1.0,
                              "ParamBrowLAngle": 1.0, "ParamBrowRAngle": 1.0,
                              "ParamBrowLForm": 1.0, "ParamBrowRForm": 1.0}

# 嘴
MOUTH_SMILE: dict[str, float] = {"ParamMouthForm": 1.0, "ParamMouthOpenY": 0.0}
MOUTH_FROWN: dict[str, float] = {"ParamMouthForm": -1.0, "ParamMouthOpenY": 0.0}
MOUTH_NEUTRAL: dict[str, float] = {"ParamMouthForm": 0.0, "ParamMouthOpenY": 0.0}
MOUTH_OPEN: dict[str, float] = {"ParamMouthOpenY": 0.8}
MOUTH_OPEN_WIDE: dict[str, float] = {"ParamMouthOpenY": 1.0}
MOUTH_SMALL_O: dict[str, float] = {"ParamMouthForm": 0.3, "ParamMouthOpenY": 0.45}


def _merge(*parts: dict[str, float]) -> dict[str, float]:
    out: dict[str, float] = {}
    for p in parts:
        if p:
            out.update(p)
    return out


# ---------------------------------------------------------------------------
# 语义表情库
#   name -> (描述, 参数)
# 覆盖原 2D 的 24 个基础表情，并补充原 2D 没有的细腻情绪。
# ---------------------------------------------------------------------------
EXPRESSIONS: dict[str, tuple[str, dict[str, float]]] = {
    # ---- 平静 / 基础 ----
    "neutral": ("平静", _merge(EYE_OPEN, BROW_FLAT, MOUTH_NEUTRAL)),
    "calm": ("安心", _merge(EYE_HALF, BROW_FLAT, MOUTH_SMILE)),

    # ---- 高兴系 ----
    "smile": ("微笑", _merge(EYE_OPEN, BROW_UP, MOUTH_SMILE)),
    "happy": ("开心", _merge(EYE_OPEN, EYE_SMILE, BROW_UP, MOUTH_SMILE, MOUTH_OPEN)),
    "grin": ("大笑", _merge(EYE_SMILE, BROW_UP, {"ParamMouthForm": 1.0, "ParamMouthOpenY": 1.0})),
    "smug": ("得意坏笑", _merge(EYE_HALF, EYE_SMILE, BROW_UP,
                                {"ParamMouthForm": 1.0, "ParamMouthOpenY": 0.15,
                                 "ParamEyeBallX": 0.25})),
    "relieved": ("欣慰", _merge(EYE_SMILE, EYE_HALF, BROW_UP, MOUTH_SMILE)),

    # ---- 害羞 / 脸红 ----
    "shy": ("害羞", _merge(EYE_HALF, BROW_SAD, MOUTH_SMALL_O, {"ParamCheek": 1.0})),
    "blush": ("脸红", _merge(EYE_OPEN, BROW_UP, MOUTH_SMALL_O, {"ParamCheek": 1.0})),
    "embarrassed": ("难为情", _merge(EYE_CLOSED, EYE_SMILE, BROW_SAD,
                                     MOUTH_SMALL_O, {"ParamCheek": 1.0})),

    # ---- 惊讶 ----
    "surprised": ("惊讶", _merge(EYE_WIDE, BROW_UP, MOUTH_OPEN)),
    "shocked": ("震惊", _merge(EYE_WIDE, BROW_UP, MOUTH_OPEN_WIDE,
                               {"ParamTeShuZuiCX": 1.0})),
    "startled": ("吓一跳", _merge(EYE_WIDE, BROW_UP, MOUTH_OPEN_WIDE,
                                  {"ParamTeShuZuiCX": 1.0, "ParamXianTiaoChuXian": 1.0})),

    # ---- 难过 / 泪 ----
    "sad": ("难过", _merge(EYE_HALF, BROW_SAD, MOUTH_FROWN)),
    "crying": ("哭泣", _merge(EYE_HALF, BROW_SAD, MOUTH_FROWN,
                              {"ParamTeShuEyeChuXian": 0.8})),
    "sobbing": ("大哭", _merge(EYE_CLOSED, BROW_SAD,
                               {"ParamMouthForm": -1.0, "ParamMouthOpenY": 0.9,
                                "ParamTeShuEyeChuXian": 1.0})),
    "lonely": ("寂寞", _merge(EYE_HALF, BROW_SAD, MOUTH_FROWN,
                              {"ParamTeShuEyeChuXian": 0.35})),

    # ---- 生气 / 闹别扭 ----
    "angry": ("生气", _merge(EYE_OPEN, BROW_DOWN, MOUTH_FROWN)),
    "furious": ("愤怒大叫", _merge(EYE_WIDE, BROW_DOWN,
                                   {"ParamMouthForm": -1.0, "ParamMouthOpenY": 1.0,
                                    "ParamXianTiaoChuXian": 1.0})),
    "pout": ("闹别扭/鼓脸", _merge(EYE_HALF, BROW_DOWN,
                                   {"ParamMouthForm": -1.0, "ParamMouthOpenY": 0.0,
                                    "ParamCheek": 0.7})),
    "sulky": ("不高兴", _merge(EYE_HALF, BROW_DOWN, MOUTH_FROWN)),
    "gloomy": ("阴沉", _merge(EYE_HALF, BROW_FLAT, MOUTH_FROWN,
                              {"ParamHeiHuaShadow": 1.0})),
    "menacing": ("黑化", _merge(EYE_HALF, BROW_DOWN,
                                {"ParamMouthForm": 1.0, "ParamMouthOpenY": 0.0,
                                 "ParamHeiHuaShadow": 1.0, "ParamXianTiaoChuXian": 0.5})),

    # ---- 白眼 / 怀疑 ----
    "sideeye": ("白眼", _merge(EYE_HALF, BROW_FLAT,
                               {"ParamMouthForm": -0.3, "ParamEyeBallX": 0.7,
                                "ParamEyeBallY": 0.15})),
    "skeptical": ("怀疑", _merge({"ParamEyeLOpen": 0.75, "ParamEyeROpen": 0.4},
                                 BROW_DOWN, MOUTH_FROWN, {"ParamEyeBallX": 0.3})),

    # ---- 认真 / 沉思 ----
    "serious": ("认真", _merge({"ParamEyeLOpen": 0.7, "ParamEyeROpen": 0.7},
                               BROW_DOWN, {"ParamMouthForm": -0.3, "ParamMouthOpenY": 0.0})),
    "thinking": ("沉思", _merge(EYE_HALF, BROW_FLAT,
                                {"ParamMouthForm": -0.2, "ParamMouthOpenY": 0.0,
                                 "ParamEyeBallX": -0.3, "ParamEyeBallY": 0.3})),
    "determined": ("坚定", _merge({"ParamEyeLOpen": 0.85, "ParamEyeROpen": 0.85},
                                  BROW_DOWN, MOUTH_NEUTRAL)),

    # ---- 调皮 ----
    "teasing": ("坏笑调侃", _merge(EYE_HALF, EYE_SMILE, {"ParamBrowLY": 1.0, "ParamBrowRY": 0.3,
                                                        "ParamBrowLAngle": -1.0, "ParamBrowRAngle": 0.0,
                                                        "ParamBrowLForm": 0.7, "ParamBrowRForm": 0.7},
                                   {"ParamMouthForm": 1.0, "ParamMouthOpenY": 0.2})),
    "mischievous": ("贱兮兮", _merge(EYE_SMILE, {"ParamBrowLY": 0.6, "ParamBrowRY": 1.0,
                                                 "ParamBrowLAngle": 0.0, "ParamBrowRAngle": -1.0,
                                                 "ParamBrowLForm": 0.7, "ParamBrowRForm": 0.7},
                                      MOUTH_SMILE, {"ParamEyeBallX": 0.4})),

    # ---- 其它 ----
    "wink": ("眨眼", _merge({"ParamEyeLOpen": 0.0, "ParamEyeROpen": 1.0},
                            EYE_SMILE, BROW_UP, MOUTH_SMILE)),
    "sleepy": ("困倦", _merge({"ParamEyeLOpen": 0.25, "ParamEyeROpen": 0.25},
                              BROW_FLAT, MOUTH_SMALL_O)),
    "lost": ("放空/达观", _merge(EYE_HALF, BROW_FLAT,
                                 {"ParamMouthForm": 0.0, "ParamMouthOpenY": 0.25,
                                  "ParamEyeBallY": -0.2})),
    "upward": ("抬眼仰望", _merge(EYE_OPEN, BROW_UP, MOUTH_SMALL_O,
                                  {"ParamEyeBallY": -0.6, "ParamAngleY": 6.0})),
    "resigned": ("无奈服从", _merge(EYE_CLOSED, BROW_SAD, MOUTH_SMALL_O)),
    "smile_tears": ("含泪而笑", _merge(EYE_SMILE, EYE_HALF, BROW_UP, MOUTH_SMILE,
                                       {"ParamTeShuEyeChuXian": 0.7})),
}

# ---------------------------------------------------------------------------
# 原 2D 图层 ID -> 语义表情名
#   覆盖 EXPR_B 的全部 42 个图层（含 10 个泪强度变体 -> 连续强度）
# ---------------------------------------------------------------------------
LAYER_EXPRESSION: dict[int, str] = {
    # 24 个基础表情
    1306: "neutral",      # ベース
    1329: "happy",        # 笑顔2
    1352: "smile",        # 微笑み
    1376: "surprised",    # 驚き
    1406: "shy",          # 恥ずかしい
    1429: "sad",          # 悲しい
    1452: "sideeye",      # ジト目
    1475: "pout",         # 拗ねる
    1505: "shocked",      # 目を見開き驚く
    1524: "serious",      # 真剣
    1616: "upward",       # 上目使い
    1641: "furious",      # 怒り叫び
    1681: "determined",   # 真面目な顔2
    1704: "lost",         # 達観
    1710: "furious",      # キシャー
    1711: "gloomy",       # ぐぬぬ
    1712: "gloomy",       # ぐぬぬ2
    # 7 个复合表情（b/e/m 三段式）
    1721: "happy",
    1722: "smile",
    1723: "sad",
    1724: "sideeye",
    1725: "smile",
    1726: "sad",
    1727: "surprised",
    1728: "embarrassed",
    1729: "sad",
    1730: "furious",
    1731: "furious",
    1732: "sulky",
    1733: "embarrassed",
    # 10 个泪图层：原为离散强度，这里映射到「基础情绪 + 泪强度」
    1745: "crying",
    1747: "surprised",    # 惊奇（泪）
    1748: "relieved",     # 欣慰高兴闭眼（泪）
    1749: "happy",        # 高兴（泪）
    1750: "relieved",
    1751: "lonely",       # 失落（泪）
    1752: "shy",          # 害羞（泪）
    1753: "EYE_CLOSED",   # 闭眼（泪）—— 实为闭眼层，见 TEAR_LAYER_EYE_CLOSED
    1754: "angry",        # 有些生气，指责（泪）
    1755: "sad",          # 伤心（泪）
    1765: "sobbing",      # 大哭2
    1787: "sobbing",      # 大哭
}

# 这些图层在原系统里语义是「泪强度」而非情绪本身，单独给泪强度
TEAR_LAYER_STRENGTH: dict[int, float] = {
    1745: 0.30,
    1747: 0.55,
    1748: 0.70,
    1749: 0.45,
    1750: 0.40,
    1751: 0.50,
    1752: 0.60,
    1753: 1.00,
    1754: 0.65,
    1755: 0.85,
    1765: 0.90,
    1787: 1.00,
}

# 该图层其实是闭眼（blink.py 用它做闭眼帧）
EYE_CLOSED_LAYER = 1753


def expression_params(name: str) -> dict[str, float]:
    """取语义表情的参数；未知名称返回空 dict。"""
    item = EXPRESSIONS.get(name)
    return dict(item[1]) if item else {}


def expression_label(name: str) -> str:
    item = EXPRESSIONS.get(name)
    return item[0] if item else name


def compose(*,
            expression: str | None = None,
            tear: float | None = None,
            cheek: float | None = None,
            eye_open: float | None = None,
            extra: dict[str, float] | None = None) -> dict[str, float]:
    """合成一组参数。

    tear / cheek / eye_open 为叠加控制，优先级高于表情自带值，
    用于「同一情绪 + 不同泪强度」这类组合。
    """
    params = expression_params(expression) if expression else {}
    if tear is not None:
        params["ParamTeShuEyeChuXian"] = max(0.0, min(1.0, float(tear)))
    if cheek is not None:
        params["ParamCheek"] = max(0.0, min(1.0, float(cheek)))
    if eye_open is not None:
        v = max(0.0, min(1.0, float(eye_open)))
        params["ParamEyeLOpen"] = v
        params["ParamEyeROpen"] = v
    if extra:
        params.update(extra)
    return params


def layers_to_expression(layers: list[int] | None) -> tuple[str | None, float | None]:
    """把 2D 图层列表翻译成 (语义表情名, 泪强度)。

    返回的泪强度仅在该图层属于泪系时有值。
    """
    for lid in (layers or []):
        try:
            i = int(lid)
        except (TypeError, ValueError):
            continue
        if i in LAYER_EXPRESSION:
            name = LAYER_EXPRESSION[i]
            tear = TEAR_LAYER_STRENGTH.get(i)
            if name == "EYE_CLOSED":
                return None, tear
            return name, tear
    return None, None


def all_expression_names() -> list[str]:
    return sorted(EXPRESSIONS)


# ---------------------------------------------------------------------------
# 姿势 / 姿态参数
# ---------------------------------------------------------------------------
# 经 `analyze_custom_params.py` 逐像素实测（比较 -1.0 与 +1.0 的渲染差异），
# 以下参数确认有可见效果；其它自定义参数（眼珠缩放、高光、大腿、头发段）
# 实测无可见变化，故不纳入。
#
# 注意：本模型**没有换装能力** —— 服装是烘进 moc3 网格与贴图的，
# 不存在替换服装的参数。这里提供的是「姿态与裙摆/手臂形态」层面的变化，
# 不是换装。
POSE_PARAMS: dict[str, str] = {
    "ParamShenTiQianHou": "身体前后倾",
    "Paramqunzi": "裙摆",
    "Paramhudiejie": "蝴蝶结",
    "Paramzuoxiaobi": "左小臂",
    "Paramzuodabi": "左大臂",
    "Paramyoudabi": "右大臂",
    "Paramyouxiaobi": "右小臂",
}

# 语义姿势预设：name -> (描述, 参数)
POSES: dict[str, tuple[str, dict[str, float]]] = {
    "idle": ("自然站姿", {}),
    "lean_forward": ("身体前倾（凑近）", {"ParamShenTiQianHou": 1.0}),
    "lean_back": ("身体后仰（躲开）", {"ParamShenTiQianHou": -1.0}),
    "arms_in": ("双臂收拢（乖巧）",
                {"Paramzuodabi": 1.0, "Paramzuoxiaobi": 1.0,
                 "Paramyoudabi": 1.0, "Paramyouxiaobi": 1.0}),
    "arms_out": ("双臂张开（欢迎）",
                 {"Paramzuodabi": -1.0, "Paramzuoxiaobi": -1.0,
                  "Paramyoudabi": -1.0, "Paramyouxiaobi": -1.0}),
    "skirt_flutter": ("裙摆飘动", {"Paramqunzi": 1.0}),
    "skirt_still": ("裙摆静止", {"Paramqunzi": -1.0}),
    "bow_sway": ("蝴蝶结摆动", {"Paramhudiejie": 1.0}),
    "bow_still": ("蝴蝶结静止", {"Paramhudiejie": -1.0}),
    "timid": ("拘谨（收臂前倾）",
              {"ParamShenTiQianHou": 0.6,
               "Paramzuodabi": 0.8, "Paramzuoxiaobi": 0.8,
               "Paramyoudabi": 0.8, "Paramyouxiaobi": 0.8}),
    "confident": ("自信（张臂后仰）",
                  {"ParamShenTiQianHou": -0.5,
                   "Paramzuodabi": -0.7, "Paramzuoxiaobi": -0.7,
                   "Paramyoudabi": -0.7, "Paramyouxiaobi": -0.7}),
}


def pose_params(name: str) -> dict[str, float]:
    item = POSES.get(name)
    return dict(item[1]) if item else {}


def pose_label(name: str) -> str:
    item = POSES.get(name)
    return item[0] if item else name


def all_pose_names() -> list[str]:
    return sorted(POSES)


def compose_full(*,
                 expression: str | None = None,
                 pose: str | None = None,
                 tear: float | None = None,
                 cheek: float | None = None,
                 eye_open: float | None = None,
                 extra: dict[str, float] | None = None) -> dict[str, float]:
    """表情 + 姿势 一起合成（姿势后写入，优先级更高）。"""
    params = compose(expression=expression, tear=tear, cheek=cheek,
                     eye_open=eye_open, extra=extra)
    if pose:
        params.update(pose_params(pose))
    return params
