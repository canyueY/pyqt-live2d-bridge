# pyqt-live2d-bridge

在 **PyQt5** 里渲染 **Live2D Cubism** 模型，并做动作 / 表情 / 情绪绑定。

把「让一个 Live2D 模型在 Qt 窗口里活起来」这件事从业务代码里剥出来：
本包只提供**渲染引擎**，不提供窗口部件——宿主可以是任何 `QOpenGLWidget`。

> 本包不含任何模型素材。Live2D 模型有各自的版权与授权条款，请自行准备。

## 安装

```bash
# 从 GitHub 安装
pip install git+https://github.com/canyueY/pyqt-live2d-bridge.git
# 或从源码
git clone https://github.com/canyueY/pyqt-live2d-bridge.git
cd pyqt-live2d-bridge && pip install -e .
```

> 暂未发布到 PyPI，所以 `pip install pyqt-live2d-bridge` 还不能用。

依赖：`PyQt5`、[`live2d-py`](https://pypi.org/project/live2d-py/)（Live2D Cubism Native 的
第三方 Python 绑定）、`pyopengl`。

## 快速开始

```python
import sys
import live2d_bridge as B

# ① 必须在 QApplication 之前调用，见下方「两个坑」
B.init_runtime()

from PyQt5.QtWidgets import QApplication, QOpenGLWidget
import live2d.v3 as live2d

class Canvas(QOpenGLWidget):
    def initializeGL(self):
        live2d.glInit()
        self.r = B.Live2DRenderer("path/to/model.model3.json",
                                  gl_init=False, auto_blink=True, auto_breath=True)
        self.r.load()
        self.r.resize(self.width(), self.height())

    def paintGL(self):
        live2d.clearBuffer(0.0, 0.0, 0.0, 0.0)
        self.r.draw()

app = QApplication(sys.argv)
c = Canvas(); c.resize(400, 800); c.show()
app.exec_()
```

完整可运行版本（含鼠标跟随、点击命中区触发动作、滚轮缩放、空格换表情）见
[`examples/render_demo.py`](examples/render_demo.py)。

## ⚠️ 两个必须踩对的顺序

这两处搞错都会得到**原生层崩溃而非 Python 异常**，很难查：

**1. `live2d.v3.init()` 必须在 `QApplication` 构造之前**

顺序反了或漏掉，渲染时直接 `0xC0000005` 访问违例，连 traceback 都看不到。
`B.init_runtime()` 封装了这件事，放在 `QApplication` 之前调用即可。

**2. Qt 插件路径要显式补上**

直接在 venv 里跑 `python.exe`（而不是 `python -m`）时，`Qt5\bin` 与插件目录
不在搜索路径里，Qt 会报 `Could not find the Qt platform plugin "windows"`。
`init_runtime()` 一并处理了。

```python
B.init_runtime()          # ← 在 QApplication 之前
app = QApplication(sys.argv)
```

## 模型路径不写死

本包**不假设任何目录结构**。解析优先级：

1. 调用方显式传入（推荐）
2. 环境变量 `LIVE2D_MODEL_JSON`
3. 当前工作目录下按约定布局兜底查找（`live2d模型/*/*.model3.json` 等）

```python
B.paths.resolve_model_json()      # 解析默认模型
B.paths.resolve_preview_path()    # 预览握手文件（供外部工具驱动）
B.paths.clear_caches()            # 改过环境变量后刷新
```

## 模块一览

| 模块 | 职责 |
| --- | --- |
| `live2d_render` | 渲染引擎 `Live2DRenderer`：GL 画布、眨眼、呼吸、命中区、动作播放、口型 |
| `live2d_motions` | 动作目录：分组、索引、时段可用性、台词、语音文件、情绪→动作 |
| `live2d_expressions` | 表情 / 姿势参数合成（可叠加，产出参数字典） |
| `live2d_emotion_bridge` | 情绪标签 → 表情 / 眼泪 / 脸红 / 姿势的映射 |
| `live2d_info` | 模型元信息、运行时快照、预览握手文件 |
| `paths` | 模型与预览文件路径解析 |

## 关于「纯数据」用法

动作目录与表情合成**不需要 GL 上下文**，也不强制需要 `live2d-py`：

```python
import live2d_bridge as B

B.all_expression_names()          # 36 个（取决于模型）
B.expression_params("happy")      # {'ParamEyeLSmile': 1.0, ...}
B.motions_for_area("face")        # 命中区 -> 可用动作
B.is_time_ok("Idle_0", 12)        # 时段可用性
```

## 授权与第三方

- 本包代码：**MIT**（见 [LICENSE](LICENSE)）
- [`live2d-py`](https://github.com/Arkueid/live2d-py)：第三方封装，与本包作者无关
- **Live2D Cubism SDK**：[Live2D Inc.](https://www.live2d.com/) 的专有许可。
  使用前请自行确认你的使用场景符合其条款（例如达到一定营收规模需要付费授权）。
  「Live2D」「Cubism」是 Live2D Inc. 的商标。
- 模型素材：版权归各自权利人，**不在本仓库内**。

## 来源与开发位置

本包从作者自己的桌宠项目 [MurasamePet](https://github.com/canyueY/MurasamePet)
（AGPL-3.0）中抽出。抽取的五个模块是该项目自己新增的部分（其上游
[kuxiaowo/AIpet-Murasame](https://github.com/kuxiaowo/AIpet-Murasame) 使用 2D 立绘，
不含 Live2D 相关代码），因此本包可独立以 MIT 发布。

> **本仓库是镜像；权威源码在 MurasamePet 仓库里。**
>
> 开发位置：`MurasamePet/packages/pyqt-live2d-bridge/`。
> 桌宠通过 **path 依赖**直接使用它 —— 不再保留任何副本，改这里立即可见。
> 这个独立仓库用于对外发布与展示，内容由 Monorepo 同步而来。
>
> **为什么不反过来（本仓库为源、桌宠用 `git` 依赖）？**
> 开发机上网关受限：`github.com` 必须走本地代理，而 `uv` 拉 git 依赖时
> 用不上该代理（实测 `git fetch` 失败）。path 依赖离线可用、不受代理开关影响。
