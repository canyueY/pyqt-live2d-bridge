# -*- coding: utf-8 -*-
"""情绪 -> Live2D 表情 桥接层。

项目里有两套情绪词汇，来源不同：

1. **情感分析标签**（`chat.get_emotion`）：从
   `models/Murasame_SoVITS/reference_voices/` 的子目录名动态读取，
   当前为 害羞 / 平静 / 惊讶 / 生气 / 着急 / 高兴。
   用途：驱动 TTS 参考音；本模块也用它驱动表情。

2. **分段语气 tone**（`chat_reply.ChatSegment.tone`）：由模型在分段
   JSON 里给出，见 `segment_reply_prompt_hint()`，示例值为
   活泼 / 害羞 / 平静。

另外 2D 系统里还有一层「情绪 -> 图层 ID」的映射
（`chat_reply._TONE_LAYER_MAP`）。实测该映射把所有语气都指向同一组图层
`[1717, 1306, 1261]`（1306 是平静表情），因此 2D 下情绪对表情几乎没有影响。
Live2D 这边改为**直接用情绪标签驱动语义表情**，表达能力更强。

本模块只做映射，不依赖 live2d-py，便于单独测试。
"""
from __future__ import annotations

from . import live2d_expressions as LX

# ---------------------------------------------------------------------------
# 情感分析标签 -> 语义表情
#   键为 reference_voices 下的目录名（动态读取），故按当前实际值映射，
#   并对常见同义标签做兼容。
# ---------------------------------------------------------------------------
EMOTION_EXPRESSION: dict[str, str] = {
    "平静": "neutral",
    "高兴": "happy",
    "害羞": "shy",
    "惊讶": "surprised",
    "生气": "angry",
    "着急": "startled",     # 着急 -> 慌张，比 surprised 更贴切
    # 兼容同义/扩展标签
    "开心": "happy",
    "快乐": "happy",
    "愉快": "happy",
    "兴奋": "grin",
    "悲伤": "sad",
    "伤心": "sad",
    "难过": "sad",
    "失落": "lonely",
    "委屈": "crying",
    "害怕": "startled",
    "恐惧": "startled",
    "困惑": "thinking",
    "疑惑": "skeptical",
    "无奈": "resigned",
    "得意": "smug",
    "调皮": "mischievous",
    "认真": "serious",
    "困": "sleepy",
}

# ---------------------------------------------------------------------------
# 分段语气 tone -> 语义表情
# ---------------------------------------------------------------------------
TONE_EXPRESSION: dict[str, str] = {
    "平静": "neutral",
    "活泼": "happy",
    "害羞": "shy",
    "生气": "angry",
    "开心": "happy",
    "高兴": "happy",
    "惊讶": "surprised",
    "着急": "startled",
    "娇嗔": "pout",
    "撒娇": "pout",
    "得意": "smug",
    "调皮": "mischievous",
    "认真": "serious",
    "难过": "sad",
    "悲伤": "sad",
    "温柔": "smile",
    "微笑": "smile",
}

# 情绪 -> 泪强度（用于情感分析标签；None 表示不加泪）
EMOTION_TEAR: dict[str, float] = {
    "委屈": 0.7,
    "伤心": 0.6,
    "悲伤": 0.6,
    "难过": 0.5,
    "失落": 0.35,
}

# 情绪 -> 脸颊红度（害羞类强化脸红）
EMOTION_CHEEK: dict[str, float] = {
    "害羞": 0.9,
    "娇嗔": 0.7,
    "撒娇": 0.7,
}

# 情绪 -> 姿势（可选；默认不改姿势，避免频繁变化显得抽搐）
EMOTION_POSE: dict[str, str] = {
    "生气": "arms_in",
    "害羞": "timid",
    "高兴": "arms_out",
}


def _norm(label: str) -> str:
    return (label or "").strip()


def expression_for_emotion(label: str) -> str | None:
    """情感分析标签 -> 语义表情名；无法识别返回 None。"""
    return EMOTION_EXPRESSION.get(_norm(label))


def expression_for_tone(tone: str) -> str | None:
    """分段 tone -> 语义表情名；无法识别返回 None。"""
    return TONE_EXPRESSION.get(_norm(tone))


def expression_for_label(label: str) -> str | None:
    """合并查表：先按情感标签，再按 tone，再尝试直接作为表情名。"""
    s = _norm(label)
    if not s:
        return None
    if s in EMOTION_EXPRESSION:
        return EMOTION_EXPRESSION[s]
    if s in TONE_EXPRESSION:
        return TONE_EXPRESSION[s]
    if s in LX.EXPRESSIONS:
        return s
    return None


def tear_for_emotion(label: str) -> float | None:
    return EMOTION_TEAR.get(_norm(label))


def cheek_for_emotion(label: str) -> float | None:
    return EMOTION_CHEEK.get(_norm(label))


def pose_for_emotion(label: str) -> str | None:
    return EMOTION_POSE.get(_norm(label))


def resolve(label: str, *,
            prefer_tone: bool = False,
            face: str = "",
            intensity: str = "") -> dict:
    """把情绪/语气标签（可选：模型直接给的表情与强度）解析成渲染参数。

    ``face`` —— 模型直接指定的语义表情名。合法时**优先于**标签推导，
    因为这比 17 条 tone→表情 的映射更精确（模型看得见全部台词）。

    ``intensity`` —— 强度档位 ``low`` / ``mid`` / ``high``（也接受 0~1 数字），
    用于缩放泪与脸红；不改变表情本身。

    返回 dict，键为 live2d_render 的调用参数：
      expression / tear / cheek / pose / label / matched / intensity
    """
    s = _norm(label)
    face_name = _norm(face)
    band = _norm_intensity(intensity)
    if face_name not in LX.EXPRESSIONS:
        face_name = ""

    if not s and not face_name:
        return {"matched": False, "label": s, "intensity": band}

    expr = face_name
    if not expr:
        expr = (expression_for_tone(s) if prefer_tone else expression_for_emotion(s))
        if expr is None:
            expr = expression_for_label(s)
    if expr is None:
        return {"matched": False, "label": s, "intensity": band}

    return {
        "matched": True,
        "label": s,
        "expression": expr,
        "expression_label": LX.expression_label(expr),
        "tear": _scaled(tear_for_emotion(s), band),
        "cheek": _scaled(cheek_for_emotion(s), band),
        "pose": pose_for_emotion(s),
        "intensity": band,
    }


# 强度档位 -> 泪/脸红 的缩放倍率。
# 档位名与 Murasame/chat_reply.py 的 INTENSITY_BANDS 一致（有漂移测试保证）。
_INTENSITY_SCALE = {"low": 0.5, "mid": 1.0, "high": 1.5}


def _norm_intensity(value) -> str:
    """归一强度：档位名 / 中文别名 / 0~1 数字 -> low|mid|high；识别不了返回空串。"""
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        v = float(value)
        return "low" if v < 0.55 else ("mid" if v < 0.75 else "high")
    s = str(value).strip().lower()
    if not s:
        return ""
    s = {"低": "low", "中": "mid", "高": "high",
         "medium": "mid", "弱": "low", "强": "high"}.get(s, s)
    if s in _INTENSITY_SCALE:
        return s
    try:
        return _norm_intensity(float(s))
    except (TypeError, ValueError):
        return ""


def _scaled(value, band: str):
    """按强度缩放泪/脸红；值为 None 时保持 None（该情绪本来就不加这一项）。"""
    if value is None:
        return None
    return max(0.0, min(1.0, float(value) * _INTENSITY_SCALE.get(band, 1.0)))


def known_labels() -> dict:
    """列出已知标签，便于排查未识别的情绪。"""
    return {
        "emotion": sorted(EMOTION_EXPRESSION),
        "tone": sorted(TONE_EXPRESSION),
        "expression": LX.all_expression_names(),
    }
