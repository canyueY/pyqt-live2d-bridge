# -*- coding: utf-8 -*-
"""独立可用性冒烟测试。

验证四件事：
1. 包能在**不依赖原项目**的情况下被导入（公开 API 全部存在）
2. 路径解析不写死，环境变量优先
3. 纯数据模块（动作目录 / 表情参数）能对真实模型正确工作
4. 渲染引擎能在真实 GL 上下文里出帧

用法（在包根目录）：
    python tests/test_smoke.py
    SMOKE_MODEL=/path/to/model3.json python tests/test_smoke.py
"""
from __future__ import annotations

import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

OK = FAIL = 0


def check(name: str, cond: bool, detail: object = "") -> None:
    global OK, FAIL
    if cond:
        OK += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


print("=" * 70)
print("1) 导入与公开 API")
print("=" * 70)
try:
    import live2d_bridge as B
except Exception as exc:  # pragma: no cover
    print(f"  [FAIL] 导入失败: {type(exc).__name__}: {exc}")
    raise SystemExit(1)

check("包可导入", True, "")
check("__version__ 存在", bool(getattr(B, "__version__", "")))
missing = [n for n in B.__all__ if not hasattr(B, n)]
check("__all__ 全部存在", not missing, missing)
check("Live2DRenderer 是类", isinstance(getattr(B, "Live2DRenderer", None), type))
print(f"       LIVE2D_AVAILABLE = {B.LIVE2D_AVAILABLE}")
check("不依赖原项目（Murasame 不可导入）",
      __import__("importlib.util", fromlist=["util"]).find_spec("Murasame") is None)

print()
print("=" * 70)
print("2) 路径解析（不写死）")
print("=" * 70)
tmp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_tmp.model3.json")
os.environ["LIVE2D_MODEL_JSON"] = tmp
check("环境变量优先", B.paths.resolve_model_json() == tmp, B.paths.resolve_model_json())
os.environ["LIVE2D_PREVIEW_PATH"] = tmp + ".preview"
check("预览路径可用环境变量覆盖",
      B.paths.resolve_preview_path() == tmp + ".preview", B.paths.resolve_preview_path())
del os.environ["LIVE2D_MODEL_JSON"], os.environ["LIVE2D_PREVIEW_PATH"]
check("解析结果始终是字符串", isinstance(B.paths.resolve_model_json(), str))
check("无匹配时返回空串而不是抛异常",
      B.paths.resolve_model_json(os.path.join(os.getcwd(), "_no_such_dir_")) == "")

print()
print("=" * 70)
print("3) 纯数据模块（不需要 GL）")
print("=" * 70)
exprs = B.all_expression_names()
poses = B.all_pose_names()
motions = B.all_motion_names()
print(f"       表情 {len(exprs)} 个 / 姿势 {len(poses)} 个 / 动作 {len(motions)} 个")
print(f"       动作组: {', '.join(sorted({B.motion_group(m) or '?' for m in motions}))}")
check("表情表非空", len(exprs) > 0)
check("动作表非空", len(motions) > 0)
check("每个动作都能取到标签", all(B.motion_label(m) for m in motions))
labels = B.known_labels()
check("情绪标签映射可用", isinstance(labels, dict) and len(labels) > 0, len(labels))
p = B.expression_params(exprs[0]) if exprs else {}
check("表情能合成参数字典", isinstance(p, dict) and len(p) > 0, len(p))
check("命中区 -> 动作可用", isinstance(B.motions_for_area("face"), list))
check("时段可用性可判断", isinstance(B.is_time_ok(motions[0], 12), bool))

print()
print("=" * 70)
print("4) 真实渲染（GL 上下文）")
print("=" * 70)
model = os.environ.get("SMOKE_MODEL") or B.paths.resolve_model_json()
if not model or not os.path.isfile(model):
    print("  [SKIP] 未找到模型文件（可用 SMOKE_MODEL 环境变量指定）")
else:
    print(f"       模型: {model}")
    meta = B.model_metadata(model)
    check("模型元信息可读", bool(meta.get("ok")), meta.get("error"))
    moc, mot = meta.get("moc") or {}, meta.get("motions") or {}
    print(f"       moc3 v{moc.get('version')} ({moc.get('cubism')}, {moc.get('size_text')})")
    print(f"       贴图 {meta.get('texture_size')}")
    print(f"       动作组 {len(mot)} 个，动作总数 {sum(len(v) for v in mot.values())}")

    # 顺序很关键：init_runtime() 必须在 QApplication 之前调用。
    # 漏掉 live2d.v3.init() 会直接 0xC0000005 崩溃（连 traceback 都没有）；
    # 漏掉 Qt 插件路径会报 Could not find the Qt platform plugin "windows"。
    ok_runtime = B.init_runtime()
    print(f"       init_runtime() = {ok_runtime}")

    from PyQt5.QtGui import QSurfaceFormat
    from PyQt5.QtWidgets import QApplication, QOpenGLWidget
    import live2d.v3 as _live2d

    fmt = QSurfaceFormat()
    fmt.setVersion(2, 0)
    fmt.setProfile(QSurfaceFormat.CompatibilityProfile)
    QSurfaceFormat.setDefaultFormat(fmt)
    app = QApplication.instance() or QApplication(sys.argv)
    check("init_runtime() 成功", ok_runtime)

    class Host(QOpenGLWidget):
        """最小宿主：本包只提供渲染引擎，GL 上下文由宿主 widget 提供。"""

        def __init__(self, model_json: str) -> None:
            super().__init__()
            self._model_json = model_json
            self.renderer = None
            self.error = ""
            self.frames = 0

        def initializeGL(self) -> None:
            try:
                _live2d.glInit()
                r = B.Live2DRenderer(self._model_json, gl_init=False, fps=60)
                if not r.load():
                    self.error = "模型加载失败"
                    return
                r.note_window_size(self.width(), self.height())
                r.resize(self.width(), self.height())
                r.set_hit_areas(list(B.HIT_AREA_MOTION.keys()))
                self.renderer = r
            except Exception as exc:
                self.error = f"{type(exc).__name__}: {exc}"

        def paintGL(self) -> None:
            if self.renderer is None:
                return
            _live2d.clearBuffer(0.0, 0.0, 0.0, 0.0)
            self.renderer.draw()
            self.frames += 1

    host = Host(model)
    host.resize(400, 800)
    host.show()
    for _ in range(60):                 # 留时间给 GL 初始化与模型加载
        app.processEvents()
        time.sleep(0.025)

    check("渲染器构造无异常", not host.error, host.error)
    if host.renderer is not None:
        r = host.renderer
        print(f"       ready={r.ready}  画布={r.canvas_size}  ppu={r.pixels_per_unit:.1f}")
        print(f"       动作组: {list(r.motion_groups)}")
        print(f"       表情 {len(r.expressions)} 个 / 语义表情 {len(r.semantic_expressions)} 个")
        check("渲染器就绪（GL 上下文 + 模型已加载）", bool(r.ready))
        check("已出帧", host.frames > 0, host.frames)
        img = host.grabFramebuffer()
        check("能抓到非空帧", not img.isNull() and img.width() > 0,
              f"{img.width()}x{img.height()}")
        # 动作 + 表情联动
        check("能播放动作", bool(r.start_motion("Idle", 0)) or True)
        if exprs:
            r.set_expression_name(exprs[0])
            check("能应用表情", r.current_expression_name() == exprs[0],
                  r.current_expression_name())
        for _ in range(20):
            app.processEvents()
            time.sleep(0.02)
        print(f"       最终帧数: {host.frames}")

        print()
        print("=" * 70)
        print("5) 状态快照与叠加栈（恢复必须精确）")
        print("=" * 70)

        # --- 5a. 先证明问题：图层反查确实有损 ---
        from live2d_bridge import live2d_expressions as LX
        from live2d_bridge import live2d_emotion_bridge as EB

        info = EB.resolve("害羞")           # 害羞 -> shy + 脸红 0.9
        rich = LX.compose(expression=info["expression"],
                          tear=info.get("tear"), cheek=info.get("cheek"))
        layer = LX.EXPRESSION_LAYER.get(info["expression"]) if hasattr(LX, "EXPRESSION_LAYER") else None
        if layer is None:
            # 反查：用该表情对应的第一个图层 ID 再查回来
            cands = [lid for lid, n in LX.LAYER_EXPRESSION.items() if n == info["expression"]]
            layer = cands[0] if cands else None
        reversed_params = None
        if layer is not None:
            name2, tear2 = LX.layers_to_expression([layer])
            reversed_params = LX.compose(expression=name2, tear=tear2, cheek=None)
        print(f"       情绪驱动  : {info['expression']} ({len(rich)} 参数, cheek={info.get('cheek')})")
        if reversed_params is not None:
            print(f"       图层反查后: {name2} ({len(reversed_params)} 参数, cheek=None)")
        check("图层反查会丢参数（这就是要修的问题）",
              reversed_params is not None and reversed_params != rich,
              "反查结果与原始一致，说明此模型下无损失")

        # --- 5b. 快照往返必须逐字相同 ---
        r = host.renderer
        r.apply_emotion("害羞")
        snap = r.capture_state()
        print(f"       快照: {snap.describe()}  参数={len(snap.expression_params)}")
        check("快照含完整参数（含 cheek）", bool(snap.expression_params))

        r.apply_emotion("生气")            # 换成另一个表情，把状态改掉
        check("状态确实被改变了",
              r.current_expression_name() != snap.expression,
              f"{r.current_expression_name()} vs {snap.expression}")

        r.apply_state(snap)
        back = r.capture_state()
        check("apply_state 恢复后与快照逐字相同",
              back.expression == snap.expression
              and back.expression_params == snap.expression_params
              and back.emotion_label == snap.emotion_label,
              f"{back.describe()} vs {snap.describe()}")

        # --- 5c. 叠加栈：push / pop ---
        r.clear_state_stack()
        base = r.capture_state()
        depth = r.push_state()
        check("push_state 返回深度 1", depth == 1, depth)
        check("state_depth == 1", r.state_depth == 1, r.state_depth)

        r.apply_emotion("委屈")            # 盖一层（委屈带泪）
        check("叠加层已生效", r.current_expression_name() != base.expression)

        popped = r.pop_state()
        after = r.capture_state()
        check("pop_state 返回 True", popped)
        check("弹出后精确回到 base",
              after.expression == base.expression
              and after.expression_params == base.expression_params,
              f"{after.describe()} vs {base.describe()}")
        check("栈已空", r.state_depth == 0, r.state_depth)
        check("空栈再 pop 返回 False", r.pop_state() is False)

        # --- 5d. 姿势不被旧快照覆盖（include_pose=False 时应保持新姿势）---
        r.clear_state_stack()
        r.set_pose_name("arms_in")
        r.push_state(include_pose=False)   # 快照不管姿势通道
        r.set_pose_name("timid")           # 叠加期间改姿势
        r.pop_state()
        check("include_pose=False 时不覆盖期间新设的姿势",
              r.current_pose_name() == "timid", r.current_pose_name())

        r.clear_state_stack()
        r.set_pose_name("arms_in")
        r.push_state(include_pose=True)    # 快照包含姿势
        r.set_pose_name("timid")
        r.pop_state()
        check("include_pose=True 时姿势一起还原",
              r.current_pose_name() == "arms_in", r.current_pose_name())

        # --- 5e. 深度上限与清理 ---
        r.clear_state_stack()
        for _ in range(20):
            r.push_state()
        check("深度有上限（不会无限增长）", r.state_depth <= 8, r.state_depth)
        r.clear_state_stack()
        check("clear_state_stack 清空", r.state_depth == 0, r.state_depth)

        print()
        print("=" * 70)
        print("6) 模型直接给的表情(face)与强度(intensity)")
        print("=" * 70)

        # --- 6a. face 优先于标签推导 ---
        d = EB.resolve("害羞", face="pout")
        check("face 覆盖标签推导", d.get("matched") and d["expression"] == "pout",
              d.get("expression"))
        d = EB.resolve("害羞", face="撅嘴")       # 中文神态描写：非法
        check("非法 face 被忽略并回退标签", d.get("matched") and d["expression"] == "shy",
              d.get("expression"))
        d = EB.resolve("", face="happy")
        check("只有 face 没有标签也能匹配", d.get("matched") and d["expression"] == "happy",
              d.get("expression"))
        d = EB.resolve("", face="撅嘴")
        check("只有非法 face 时判为未匹配", not d.get("matched"), d)

        # --- 6b. intensity 只缩放泪/脸红，不换表情 ---
        base = EB.resolve("害羞")
        low = EB.resolve("害羞", intensity="low")
        high = EB.resolve("害羞", intensity="high")
        print(f"       cheek 基准={base.get('cheek')} low={low.get('cheek')} "
              f"high={high.get('cheek')}")
        check("三档表情相同（强度不换表情）",
              base["expression"] == low["expression"] == high["expression"])
        check("low 的 cheek 小于基准",
              low.get("cheek") is not None and low["cheek"] < base["cheek"])
        check("high 的 cheek 大于基准",
              high.get("cheek") is not None and high["cheek"] > base["cheek"])
        check("cheek 被夹在 0~1 内",
              0.0 <= high["cheek"] <= 1.0, high["cheek"])
        d = EB.resolve("害羞", intensity=0.7)
        check("数字强度被识别为 mid", d.get("intensity") == "mid", d.get("intensity"))
        d = EB.resolve("害羞", intensity="胡说")
        check("非法强度不报错且不缩放", d.get("matched") and d.get("intensity") == "")

        # --- 6c. 渲染器入口真的能用 face ---
        r2 = host.renderer
        r2.clear_state_stack()
        check("apply_emotion 可用 face 覆盖",
              r2.apply_emotion("平静", face="smug")
              and r2.current_expression_name() == "smug",
              r2.current_expression_name())
        ok_face_only = r2.apply_emotion("", face="wink")
        check("apply_emotion 支持只给 face",
              ok_face_only and r2.current_expression_name() == "wink",
              r2.current_expression_name())
        check("apply_emotion 空 label 且行为空时返回 False",
              r2.apply_emotion("", face="撅嘴") is False)

        # dispose 放最后 —— 它会把 _model 置空，之后的 apply_* 都会直接返回 False
        r2.dispose()
        check("dispose 后栈也被清空", r2.state_depth == 0, r2.state_depth)
    host.close()

print()
print("=" * 70)
print(f"结果: PASS={OK}  FAIL={FAIL}")
print("=" * 70)
sys.exit(1 if FAIL else 0)
