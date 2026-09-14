# -*- coding: utf-8 -*-
"""Live2D 动作目录与「动作-表情-语音-文字」绑定方案。

素材盘点（来自 model3.json，本模型自带）
----------------------------------------
6 组 12 个动作，其中 11 个带**原始日文语音 + 中文台词**：

| 分组        | 数量 | 时长范围      | 语音 |
|-------------|------|---------------|------|
| Idle        | 1    | 8s            | 无   |
| Tapface     | 2    | 6 / 11.5s     | 有   |
| Taphair     | 2    | 3.4 / 5.03s   | 有   |
| Tapxiongbu  | 2    | 4 / 8s        | 有   |
| Tapqunzi    | 2    | 5 / 7s        | 有   |
| Tapleg      | 3    | 5.63 / 8 / 10s| 有   |

重要事实：**动作文件里已烘焙口型曲线**（92 条曲线含 `ParamMouthOpenY`
与 `ParamMouthForm`），播放动作时嘴会自己动，无需额外对口型。
另有 11 段台词（`Text` 字段）+ 11 个 wav（`Sound` 字段）。

三层设计方案
------------
**第一层：直接互动（用户点身体部位）—— 用模型原声**
    点某部位 -> 该部位随机动作 + 原始日文语音 + 中文台词字幕
    这是模型作者设计的交互，接近 1:1 使用。

**第二层：文字输出（LLM 对话）—— 用 TTS**
    LLM 回复 -> 情绪 -> 表情 + 动作，语音仍走项目 TTS。
    不使用模型原声：那 11 句是固定台词，与 LLM 回复内容无关，
    混用会顾此失彼。

**第三层：环境/状态**
    Idle 待机、思考、摸头、启动问好等，配对应动作。

原声与 TTS 的冲突处理（关键决策）
---------------------------------
模型原声与项目 TTS 都走同一条语音播放队列
（`pet._enqueue_voice_playback`），若同时触发会互相打断。
规则：

- 原声字幕**可显示**（是文字，不发声）
- 原声语音仅在「当前没有 LLM 对话进行中」时播放
  （即用户只是随手点了角色，没有在聊天）
- 若正在对话，跳过原声语音，并把字幕降级为「不打断对话」的短提示
"""
from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# 语义动作目录
#   key -> 定义。kind 用于按用途筛选：
#     ambient  待机/环境
#     reaction 对用户动作的即时反应
#     emotion  情绪表达
#     state    状态性（启动/思考）
# ---------------------------------------------------------------------------
MOTIONS: dict[str, dict[str, Any]] = {
    # ---- 待机 ----
    "idle": {
        "group": "Idle",
        "index": 0,
        "kind": "ambient",
        "label": "待机",
        "duration": 8.0,
        "voice": "",
        "text": "",
    },
    # ---- 情绪过渡层（静音微动作）----------------------------------------
    # 这 5 条不是模型自带的：模型原有 12 个动作里 11 个烤了语音+台词，
    # 拿它们当通用过渡会播出语义不搭的台词（例如 leg_ghost 说的是「不是幻觉」）。
    # 因此用 scripts/make_micro_motions.py **程序化生成**了一组无 Sound/Text 的
    # 微动作（分组名 Micro），有动作但不抢台词、不播原声。
    # 参数曲线见该脚本；此处只是手工登记进目录，让应用能按语义名引用。
    "micro_listen": {
        "group": "Micro",
        "index": 0,
        "kind": "ambient",
        "label": "倾听",
        "duration": 4.0,
        "voice": "",
        "text": "",
    },
    "trans_irritated": {
        "group": "Micro",
        "index": 1,
        "kind": "emotion",
        "label": "烦躁渐起",
        "duration": 3.0,
        "voice": "",
        "text": "",
    },
    "trans_relieved": {
        "group": "Micro",
        "index": 2,
        "kind": "emotion",
        "label": "烦躁渐消",
        "duration": 3.5,
        "voice": "",
        "text": "",
    },
    "trans_shy_shift": {
        "group": "Micro",
        "index": 3,
        "kind": "emotion",
        "label": "害羞小动作",
        "duration": 3.0,
        "voice": "",
        "text": "",
    },
    "trans_sigh": {
        "group": "Micro",
        "index": 4,
        "kind": "emotion",
        "label": "无奈叹气",
        "duration": 2.5,
        "voice": "",
        "text": "",
    },
    # ---- 摸脸 ----
    "face_intro": {
        "group": "Tapface",
        "index": 0,
        "kind": "reaction",
        "label": "自我介绍",
        "duration": 11.5,
        "voice": "dadad3c388137e58784eef1157f9a978.wav",
        "text": "吾名丛雨，乃是这“丛雨丸”的管理者……简单来说，也算是“丛雨丸”的灵魂",
    },
    "face_master": {
        "group": "Tapface",
        "index": 1,
        "kind": "reaction",
        "label": "认主",
        "duration": 6.0,
        "voice": "d078dc4f4cabba82674b11d70d7bbd75.wav",
        "text": "你，就是本座的主人？",
    },
    # ---- 摸头 ----
    "hair_point": {
        "group": "Taphair",
        "index": 0,
        "kind": "reaction",
        "label": "指这里",
        "duration": 5.03,
        "voice": "545915c0ce9f25d14a4448444af718a6.wav",
        "text": "在这里，这里",
    },
    "hair_restore": {
        "group": "Taphair",
        "index": 1,
        "kind": "reaction",
        "label": "整理好了",
        "duration": 3.4,
        "voice": "1279da4ab02be49e09e561360ba9d3a2.wav",
        "text": "你看，复原了",
    },
    # ---- 摸胸 ----
    "chest_master": {
        "group": "Tapxiongbu",
        "index": 0,
        "kind": "reaction",
        "label": "确认主人",
        "duration": 8.0,
        "voice": "0e6c3d06c5b56120b77d79355396de7d.wav",
        "text": "主人就是主人。是你拔出了丛雨丸吧？",
    },
    "chest_angry": {
        "group": "Tapxiongbu",
        "index": 1,
        "kind": "reaction",
        "label": "羞恼",
        "duration": 4.0,
        "voice": "c07c36f5d1cd403b471fa0ea585f4a3d.wav",
        "text": "你这————！！",
    },
    # ---- 裙子 ----
    "skirt_land": {
        "group": "Tapqunzi",
        "index": 0,
        "kind": "reaction",
        "label": "着陆",
        "duration": 5.0,
        "voice": "5dcbdc4f2745b2198bddb90c9b6644bf.wav",
        "text": "——着陆",
    },
    "skirt_ghost": {
        "group": "Tapqunzi",
        "index": 1,
        "kind": "reaction",
        "label": "否认幽灵",
        "duration": 7.0,
        "voice": "939791ef45588510e2012f8fdc36ef2e.wav",
        "text": "本座才不是幽灵！完全不是！不要把幽灵和本座相提并论！",
    },
    # ---- 腿 ----
    "leg_ghost": {
        "group": "Tapleg",
        "index": 0,
        "kind": "reaction",
        "label": "反驳幻觉",
        "duration": 10.0,
        "voice": "5c92871cc122f1ef2de0fca2f5c3f96b.wav",
        "text": "哪是什么幽灵，别……别别别把本座和那种毫无事实依据的东西混为一谈",
    },
    "leg_morning": {
        "group": "Tapleg",
        "index": 1,
        "kind": "reaction",
        "label": "早上好",
        "duration": 5.63,
        "voice": "d06700f343b44366866c90007e391544.wav",
        "text": "你醒了吗，主人。早上好",
    },
    "leg_not_ghost": {
        "group": "Tapleg",
        "index": 2,
        "kind": "reaction",
        "label": "不是幻觉",
        "duration": 8.0,
        "voice": "b7753ed3e888e0505954c46751e85b2d.wav",
        "text": "本座不是幻觉，更不是幽灵，主人！",
    },
}

# ---------------------------------------------------------------------------
# 命中区 -> 可用动作（第一层：直接互动）
# ---------------------------------------------------------------------------
HIT_AREA_MOTIONS: dict[str, list[str]] = {
    "face": ["face_intro", "face_master"],
    "hair": ["hair_point", "hair_restore"],
    "xiongbu": ["chest_master", "chest_angry"],
    "qunzi": ["skirt_land", "skirt_ghost"],
    "leg": ["leg_ghost", "leg_morning", "leg_not_ghost"],
}

# 命中区 -> 该部位动作的情绪（用于同时设表情）
HIT_AREA_EMOTION: dict[str, str] = {
    "face": "平静",
    "hair": "高兴",
    "xiongbu": "生气",
    "qunzi": "惊讶",
    "leg": "害羞",
}

# ---------------------------------------------------------------------------
# 情绪 -> 动作（第二层：文字输出时配动画）
#
# 设计约束：情绪驱动的动作**只播动作，不播固定原声**。
# 原因：那 11 句原声是绑定在「被触碰部位」上的固定台词，与 LLM 回复内容无关。
# 聊天时突然冒出「是你拔出了丛雨丸吧？」会非常突兀。
# 台词字幕同样不显示（以免盖住 LLM 回复）。
#
# 因此这里只挑「短促、语义不冲突」的动作；没有合适动作的情绪留空，
# 仅由表情表达 —— 宁可不动，也不要动作与情绪矛盾。
# ---------------------------------------------------------------------------
EMOTION_MOTIONS: dict[str, list[str]] = {
    "惊讶": ["skirt_land"],    # 「——着陆」5s，短促的踉跄，贴合受惊
    "着急": ["skirt_land"],    # 同上
    # 高兴 / 害羞 / 生气 / 平静 / 其它：留空。
    # 现有素材里没有「高兴」类动作（唯一相近的「你看，复原了」是关于整理头发的），
    # 强行映射会语义不符，故不配对。
}

# 情绪驱动动作是否播原声（默认 False；见上方说明）
EMOTION_MOTION_SPEAK = False

# ---------------------------------------------------------------------------
# 场景 -> 动作（第三层：环境与状态）
# ---------------------------------------------------------------------------
SCENE_MOTIONS: dict[str, list[str]] = {
    "startup": ["face_intro", "face_master"],   # 启动问好：自我介绍
    "head_pat": ["hair_point", "hair_restore"],  # 摸头
    "thinking": [],                              # 思考留空（Idle 更自然）
    "screen": ["skirt_land"],                    # 识屏搭话：轻动作
}

# 台词在白名单场景才播原声语音，避免「早上好」在深夜突兀出现
# （按未识别场景 -> 允许；这里列出需要时段校验的）
TIME_SENSITIVE_MOTIONS: dict[str, tuple[int, int]] = {
    "leg_morning": (5, 11),        # 早上好：仅 5-11 点
}


def motion(name: str) -> dict[str, Any] | None:
    return MOTIONS.get(name)


def motion_label(name: str) -> str:
    m = MOTIONS.get(name)
    return m["label"] if m else name


def motion_group(name: str) -> str | None:
    m = MOTIONS.get(name)
    return m["group"] if m else None


def motion_index(name: str) -> int | None:
    m = MOTIONS.get(name)
    return m["index"] if m else None


def motions_for_area(area: str) -> list[str]:
    return list(HIT_AREA_MOTIONS.get(area, []))


def emotion_for_area(area: str) -> str:
    return HIT_AREA_EMOTION.get(area, "平静")


def motions_for_emotion(emotion: str) -> list[str]:
    return list(EMOTION_MOTIONS.get((emotion or "").strip(), []))


def motions_for_scene(scene: str) -> list[str]:
    return list(SCENE_MOTIONS.get((scene or "").strip(), []))


def is_time_ok(name: str, hour: int) -> bool:
    """时段敏感动作的可用性检查（如「早上好」只在早晨播）。"""
    win = TIME_SENSITIVE_MOTIONS.get(name)
    if not win:
        return True
    lo, hi = win
    return lo <= int(hour) < hi


def has_voice(name: str) -> bool:
    return bool((MOTIONS.get(name) or {}).get("voice"))


def voice_file(name: str) -> str:
    return str((MOTIONS.get(name) or {}).get("voice") or "")


def motion_text(name: str) -> str:
    return str((MOTIONS.get(name) or {}).get("text") or "")


def duration(name: str) -> float:
    return float((MOTIONS.get(name) or {}).get("duration") or 0.0)


def all_motion_names() -> list[str]:
    return sorted(MOTIONS)


def catalog() -> list[dict[str, Any]]:
    """给管理后台用的动作目录。"""
    out = []
    for name in all_motion_names():
        m = dict(MOTIONS[name])
        m["name"] = name
        m["with_voice"] = bool(m.get("voice"))
        out.append(m)
    return out
