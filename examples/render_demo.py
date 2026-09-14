# -*- coding: utf-8 -*-
"""最小可运行示例：在一个 PyQt5 窗口里渲染 Live2D 模型。

运行：
    python examples/render_demo.py                       # 自动找模型
    python examples/render_demo.py path/to/model3.json   # 指定模型

操作：
    · 鼠标移动        头部跟随
    · 左键点击立绘    命中区触发对应动作（脸/头发/胸/裙/腿）
    · 空格            随机换个表情
    · 滚轮            缩放
    · Esc             退出
"""
from __future__ import annotations

import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

# 允许不安装、直接从源码树运行本示例
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import live2d_bridge as B  # noqa: E402

# ---------------------------------------------------------------------------
# 关键顺序：init_runtime() 必须早于 QApplication
#   · 它补 Qt 插件路径（否则报 Could not find the Qt platform plugin）
#   · 它调用 live2d.v3.init()（必须在 QApplication 之前；漏掉会 0xC0000005 崩溃）
# ---------------------------------------------------------------------------
if not B.init_runtime():
    print("Live2D 运行时不可用，无法渲染。")
    print(f"  原因: {B.Live2DRenderer.unavailable_reason()}")
    raise SystemExit(1)

from PyQt5.QtCore import Qt, QTimer  # noqa: E402
from PyQt5.QtGui import QSurfaceFormat  # noqa: E402
from PyQt5.QtWidgets import QApplication, QOpenGLWidget  # noqa: E402

import live2d.v3 as live2d  # noqa: E402

FPS = 60


def pick_model() -> str:
    if len(sys.argv) > 1:
        return sys.argv[1]
    found = B.paths.resolve_model_json()
    if not found:
        print("未找到模型。请传入 model3.json 路径，或设置环境变量 LIVE2D_MODEL_JSON。")
        raise SystemExit(1)
    return found


class DemoCanvas(QOpenGLWidget):
    """把 live2d_bridge 的渲染引擎接到 Qt 的 GL 生命周期上。

    本包只提供渲染引擎（``Live2DRenderer``），不提供 widget ——
    因为它要能嵌进任意宿主。这就是一个最小宿主。
    """

    def __init__(self, model_json: str) -> None:
        super().__init__()
        self._model_json = model_json
        self.renderer: B.Live2DRenderer | None = None
        self.error = ""
        self._zoom = 1.0
        self.setWindowTitle("live2d_bridge demo")
        self.setMouseTracking(True)
        self.resize(420, 860)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.update)
        self._timer.start(int(1000 / FPS))

    # ---- GL 生命周期 ----
    def initializeGL(self) -> None:
        try:
            live2d.glInit()
            r = B.Live2DRenderer(
                self._model_json,
                gl_init=False,          # 上面已经 glInit() 过
                fps=FPS,
                auto_blink=True,
                auto_breath=True,
                mouse_tracking=True,
            )
            if not r.load():
                self.error = "模型加载失败"
                return
            r.note_window_size(self.width(), self.height())
            r.resize(self.width(), self.height())
            r.set_hit_areas(list(B.HIT_AREA_MOTION.keys()))
            self.renderer = r
            print(f"已加载: 画布={r.canvas_size} 动作组={list(r.motion_groups)} "
                  f"表情={len(r.expressions)} 语义表情={len(r.semantic_expressions)}")
        except Exception as exc:  # pragma: no cover
            self.error = f"{type(exc).__name__}: {exc}"

    def paintGL(self) -> None:
        if self.renderer is None:
            return
        live2d.clearBuffer(0.0, 0.0, 0.0, 0.0)
        self.renderer.draw()

    def resizeGL(self, w: int, h: int) -> None:
        if self.renderer is not None:
            self.renderer.note_window_size(w, h)
            self.renderer.resize(w, h)

    # ---- 交互 ----
    def mouseMoveEvent(self, ev) -> None:
        if self.renderer is not None:
            self.renderer.mouse_move(ev.x(), ev.y())

    def mousePressEvent(self, ev) -> None:
        if self.renderer is None or ev.button() != Qt.LeftButton:
            return
        area = self.renderer.hit_test_any(ev.x(), ev.y())
        if area:
            name = self.renderer.trigger_area_motion(area)
            print(f"点击命中 {area} -> 动作 {name}")
            if not name:
                print("  （该命中区没有可用动作）")
        else:
            print("点击未命中任何区域")

    def wheelEvent(self, ev) -> None:
        self._zoom = max(0.4, min(2.5, self._zoom * (1.1 if ev.angleDelta().y() > 0 else 0.9)))
        self.resize(int(420 * self._zoom), int(860 * self._zoom))

    def keyPressEvent(self, ev) -> None:
        if ev.key() == Qt.Key_Escape:
            self.close()
        elif ev.key() == Qt.Key_Space and self.renderer is not None:
            names = self.renderer.expressions
            if names:
                pick = names[int(time.time() * 1000) % len(names)]
                self.renderer.set_expression_name(pick)
                print(f"表情 -> {pick} ({self.renderer.current_expression_label()})")


def main() -> int:
    fmt = QSurfaceFormat()
    fmt.setVersion(2, 0)
    fmt.setProfile(QSurfaceFormat.CompatibilityProfile)
    QSurfaceFormat.setDefaultFormat(fmt)

    app = QApplication(sys.argv)
    canvas = DemoCanvas(pick_model())
    canvas.show()
    if canvas.error:
        print(f"初始化失败: {canvas.error}")
        return 1
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
