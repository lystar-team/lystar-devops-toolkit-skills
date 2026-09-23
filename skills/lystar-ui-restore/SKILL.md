---
name: lystar-ui-restore
description: 证据驱动的 UI 视觉还原与验收。Web、H5 页面使用 agent-browser 完成捕获、几何探测、差异比较和补丁追踪；截图、设计稿、源码、Canvas、WebGL、SVG 进入同一证据链。小程序与原生 App 需要项目提供 CaptureAdapter。
allowed-tools: Bash(agent-browser:*) Bash(python3:*) Bash(node:*)
---

# LYStar UI Restore

## 目标

把参考来源还原成可运行页面，保存参考证据、运行截图、几何映射、差异、补丁和报告。

参考来源证明可见外观。项目源码和配置决定路由、接口、权限、数据、文案、资产、交互和平台行为。视觉任务不补业务事实。

## 能力边界

| 目标 | 执行方式 |
| --- | --- |
| Web、Vue、React、SPA、iframe | `agent-browser` CaptureAdapter |
| uni-app H5、移动 Web | `agent-browser` CaptureAdapter，结论限于 H5 |
| SVG、Canvas、WebGL、3D | 浏览器截图与对应运行模式；DOM 证据限于可读部分 |
| 小程序 | 项目 CaptureAdapter、平台工具、真机或模拟器 |
| 原生 App | 项目 CaptureAdapter、设备或模拟器 |
| 截图、设计稿 | 图片证据、人工区域、项目事实源 |

缺少平台 CaptureAdapter 时，状态使用 `unavailable` 或 `blocked`。Web 截图不能替代小程序或原生运行证据。

## 场景 Contract

场景开始前，在 `decisions.json` 写入：

```text
场景：截图 / 设计稿 / 公开网页 / 本地运行页 / 源码
目标：页面或组件的视觉还原目标
重点区域：本轮处理区域
允许展开：与视觉结果相关的结构、样式、资源、可见状态
禁止新增：接口、权限、路由、字段、业务规则、登录方式、未授权功能
必须保留：项目路由、权限、接口、文案、资产、交互、无障碍语义
停止条件：视觉状态、未决项、未运行检查的记录方式
```

模块名称只限定处理区域，不授权周边功能改造。

## 核心模型

### 1. 证据状态

- `verified`：截图、设计稿、源码、项目资产或运行结果可见。
- `inferred`：两条独立测量关系支持判断。
- `reconstructed`：公开运行结果或编译结果支持还原。
- `unavailable`：材料不足。

截图不能证明 DOM 层级、组件来源、接口、权限、字体文件、交互、响应式规则和原始资产。无法辨认的内容不猜测。

### 2. 单一视觉负责人

复杂效果记录一个 `visualOwner`：

`image`、`svg`、`icon`、`css`、`text`、`chart`、`canvas`、`webgl`、`native`、`runtime`、`component`、`unknown`。

图片、CSS、SVG、图表、文字不能重复绘制同一个圆环、边框、阴影、标题、数字或标签。

### 3. 正式图表

数据图表使用项目图表组件、ECharts、AntV、Chart.js 或同类组件。CSS 高度条、渐变块和普通 `div` 只能承担装饰。

图表配置、Fixture 数据、页面布局分开。空数据保留区域骨架。

### 4. 图片比例

图片记录自然尺寸、显示尺寸、比例、裁切、透明状态、背景、可见边界和 `object-fit`。

圆形、文字、人物、地图、图标、设备图禁止独立横纵拉伸。使用 `contain`、`cover` 或裁切时，记录依据。

### 5. 坐标转换

系统区分三套坐标：

- `reference-image-px`
- `css-viewport-px`
- `render-image-px`

`normalize_geometry.py` 负责 DPR、滚动、Clip、全页截图的转换。`compare_visual.py` 不直接读取 CSS 坐标。

### 6. 运行状态

Capture 把状态分成：

- `requested`：Fixture 与 Config 请求的状态。
- `applied`：捕获器完成的状态。
- `observed`：页面运行结果。
- `unsupported`：捕获器缺少的能力。

`freezeTime`、API Fixture、Seed 等字段缺少捕获器实现时，截图停止。记录请求值不能代替执行事实。

### 7. 双通道工作流

`workflowMode` 只有 `iteration` 和 `acceptance`：

- `iteration`：用于高频调试。可以只做 Capture、局部 Compare、Focus Diff 和单区域修补；报告输出指标、差异和未决项，不输出 `accepted`。
- `acceptance`：用于交付判断。必须绑定 Reference Meta、Capture、Preflight、Regions、Structure Map（DOM 类页面）、Diff 和关键区域检查。任一证据缺失、哈希不一致、运行状态不匹配或有效比较像素为 0，结果为 `blocked` 或 `unresolved`。

同一轮的 Capture、Geometry、Normalize、Preflight、Compare 使用同一个场景、视口、URL、DPR、滚动位置、截图模式和输入哈希。重跑事实工具只更新事实字段，不覆盖 Contract、区域归属、图层负责人、审核状态和人工证据。

## 目录

```text
.visual-restore/
  adapter.json
  scenarios/<scenario>/
    manifest.json
    config.json
    fixture.json
    evidence.json
    regions.json
    tokens.json
    content.json
    assets.json
    decisions.json
    state.json
    report.json
    patches.jsonl
    record-manifest.json
    inspection/
      reference-meta.json
      asset-catalog.json
      geometry.json
    renders/<iteration>/
    preflight/<iteration>/
    diffs/<iteration>/
    focus/<iteration>/
    matrix/
```

场景产物写入场景目录。检查文件不进入业务源码。

## 工作流

### 0. 初始化场景

标准安装器把运行依赖放在 `$LYSTAR_HOME/runtime/vendor`。执行脚本前，将依赖目录加入 `PYTHONPATH`，并把 `LYSTAR_UI_RESTORE_ROOT` 设为当前安装目录（包含 `SKILL.md`）：

```bash
export PYTHONPATH="${LYSTAR_HOME:-$HOME/.lystar}/runtime/vendor${PYTHONPATH:+:$PYTHONPATH}"
export LYSTAR_UI_RESTORE_ROOT="<lystar-ui-restore 安装目录>"
```

开发仓库时，`LYSTAR_UI_RESTORE_ROOT` 指向 `skills/lystar-ui-restore`。

```bash
python3 "$LYSTAR_UI_RESTORE_ROOT/scripts/ui_restore.py" init \
  --root . \
  --scenario-id <scenario> \
  --reference-mode screenshot \
  --target web \
  --route /page \
  --width 1440 \
  --height 900 \
  --dpr 1 \
  --workflow-mode iteration
```

`iteration` 适合开发调试；交付判断使用 `--workflow-mode acceptance`。填写 `decisions.json` Contract、`adapter.json` 项目事实、`config.json` 阈值。

### 1. 观察参考来源

截图或设计稿读取 [references/screenshot-only.md](references/screenshot-only.md)。

```bash
python3 "$LYSTAR_UI_RESTORE_ROOT/scripts/ui_restore.py" inspect \
  --reference reference.png \
  --scenario-dir .visual-restore/scenarios/<scenario> \
  --scenario-id <scenario> \
  --regions manual-regions.json
```

`inspect_reference.py` 把检查产物写入 `inspection/`，把场景文档写入场景根目录。

### 2. 扫描素材

```bash
python3 "$LYSTAR_UI_RESTORE_ROOT/scripts/ui_restore.py" catalog \
  --root . \
  --adapter .visual-restore/adapter.json \
  --scenario-id <scenario> \
  --out .visual-restore/scenarios/<scenario>/inspection/asset-catalog.json \
  --assets-out .visual-restore/scenarios/<scenario>/assets.json
```

素材目录使用文件缓存。路径、大小、修改时间一致时复用哈希和元数据。

### 3. 输入预检

```bash
python3 "$LYSTAR_UI_RESTORE_ROOT/scripts/ui_restore.py" preflight \
  --root . \
  --reference .visual-restore/scenarios/<scenario>/reference.png \
  --reference-meta .visual-restore/scenarios/<scenario>/inspection/reference-meta.json \
  --regions .visual-restore/scenarios/<scenario>/regions.json \
  --render .visual-restore/scenarios/<scenario>/renders/<iteration>/render.png \
  --capture .visual-restore/scenarios/<scenario>/renders/<iteration>/capture.json \
  --structure-map .visual-restore/scenarios/<scenario>/renders/<iteration>/structure-map.json \
  --assets .visual-restore/scenarios/<scenario>/assets.json \
  --config .visual-restore/scenarios/<scenario>/config.json \
  --mode acceptance \
  --out .visual-restore/scenarios/<scenario>/preflight/<iteration>
```

`iteration` 可省略 Capture、Structure Map 和 Reference Meta，用于发现差异；`acceptance` 需要这些证据。运行时 Canvas/WebGL 素材还要传入 `--evidence evidence.json`。预检会校验参考图哈希、截图哈希、页面错误、运行时素材、区域映射和同轮绑定。

### 4. 实现页面

复用项目路由、组件、图表、字体、资源和交互。每个修改绑定：

- 一个区域；
- 一个 `propertyFamily`：`geometry`、`structure`、`content`、`typography`、`surface`、`state`；
- 一组证据；
- 一组允许文件。

### 5. 捕获、几何与归一化

Web 与 H5 读取 [references/web-runtime.md](references/web-runtime.md)；uni-app H5 补读 [references/h5-runtime.md](references/h5-runtime.md)。

```bash
python3 "$LYSTAR_UI_RESTORE_ROOT/scripts/ui_restore.py" capture \
  --adapter .visual-restore/adapter.json \
  --config .visual-restore/scenarios/<scenario>/config.json \
  --fixture .visual-restore/scenarios/<scenario>/fixture.json \
  --out .visual-restore/scenarios/<scenario>/renders/<iteration> \
  --keep-session

python3 "$LYSTAR_UI_RESTORE_ROOT/scripts/ui_restore.py" geometry \
  --adapter .visual-restore/adapter.json \
  --config .visual-restore/scenarios/<scenario>/config.json \
  --fixture .visual-restore/scenarios/<scenario>/fixture.json \
  --regions .visual-restore/scenarios/<scenario>/regions.json \
  --out .visual-restore/scenarios/<scenario>/inspection/geometry.json \
  --session <session>

python3 "$LYSTAR_UI_RESTORE_ROOT/scripts/ui_restore.py" normalize \
  --geometry .visual-restore/scenarios/<scenario>/inspection/geometry.json \
  --capture .visual-restore/scenarios/<scenario>/renders/<iteration>/capture.json \
  --config .visual-restore/scenarios/<scenario>/config.json \
  --regions .visual-restore/scenarios/<scenario>/regions.json \
  --out .visual-restore/scenarios/<scenario>/renders/<iteration>/structure-map.json
```

捕获门检查目标 URL、登录页、空白页、Ready Selector、字体、图片、视口、DPR、滚动、截图尺寸和页面错误。

### 6. 差异比较

```bash
python3 "$LYSTAR_UI_RESTORE_ROOT/scripts/ui_restore.py" preflight \
  --root . \
  --reference .visual-restore/scenarios/<scenario>/reference.png \
  --reference-meta .visual-restore/scenarios/<scenario>/inspection/reference-meta.json \
  --regions .visual-restore/scenarios/<scenario>/regions.json \
  --render .visual-restore/scenarios/<scenario>/renders/<iteration>/render.png \
  --capture .visual-restore/scenarios/<scenario>/renders/<iteration>/capture.json \
  --structure-map .visual-restore/scenarios/<scenario>/renders/<iteration>/structure-map.json \
  --assets .visual-restore/scenarios/<scenario>/assets.json \
  --config .visual-restore/scenarios/<scenario>/config.json \
  --mode acceptance \
  --out .visual-restore/scenarios/<scenario>/preflight/<iteration>

python3 "$LYSTAR_UI_RESTORE_ROOT/scripts/ui_restore.py" compare \
  --reference .visual-restore/scenarios/<scenario>/reference.png \
  --render .visual-restore/scenarios/<scenario>/renders/<iteration>/render.png \
  --regions .visual-restore/scenarios/<scenario>/regions.json \
  --structure-map .visual-restore/scenarios/<scenario>/renders/<iteration>/structure-map.json \
  --capture .visual-restore/scenarios/<scenario>/renders/<iteration>/capture.json \
  --preflight .visual-restore/scenarios/<scenario>/preflight/<iteration>/preflight.json \
  --mode acceptance \
  --out .visual-restore/scenarios/<scenario>/diffs/<iteration>
```

差异算法使用向量化像素计算、遮罩位图和区域积分图。结构门要求 Structure Map 与 Capture、Regions 哈希一致；接受模式还要求 Preflight 为 `pass`、参考图与渲染图哈希一致、有效比较像素大于 0。

差异聚焦：

```bash
python3 "$LYSTAR_UI_RESTORE_ROOT/scripts/ui_restore.py" focus \
  --diff-report .visual-restore/scenarios/<scenario>/diffs/<iteration>/diff.json \
  --reference .visual-restore/scenarios/<scenario>/reference.png \
  --render .visual-restore/scenarios/<scenario>/renders/<iteration>/render.png \
  --out .visual-restore/scenarios/<scenario>/focus/<iteration>
```

### 7. 多视口

```bash
python3 "$LYSTAR_UI_RESTORE_ROOT/scripts/ui_restore.py" matrix \
  --adapter .visual-restore/adapter.json \
  --config .visual-restore/scenarios/<scenario>/config.json \
  --fixture .visual-restore/scenarios/<scenario>/fixture.json \
  --reference .visual-restore/scenarios/<scenario>/reference.png \
  --regions .visual-restore/scenarios/<scenario>/regions.json \
  --assets .visual-restore/scenarios/<scenario>/assets.json \
  --mode acceptance \
  --root . \
  --out .visual-restore/scenarios/<scenario>/matrix
```

Matrix 复用命名 Session。验收模式按视口执行 Capture、Geometry、Normalize、Preflight、Compare。每个视口需要匹配尺寸的 Reference；没有匹配参考图的视口记录为 `unresolved`，不能写成 `accepted`。`--only <viewport-id>` 用于单视口复核。

### 8. 补丁

```bash
python3 "$LYSTAR_UI_RESTORE_ROOT/scripts/ui_restore.py" prepare-patch \
  --patch .visual-restore/scenarios/<scenario>/patch.json \
  --adapter .visual-restore/adapter.json \
  --root . \
  --regions .visual-restore/scenarios/<scenario>/regions.json \
  --evidence .visual-restore/scenarios/<scenario>/evidence.json \
  --out .visual-restore/scenarios/<scenario>/validated-patch.json
```

准备阶段检查 Schema、场景 ID、区域、证据、`allowedProperties`、`forbiddenChanges`、`visualOwner`、文件边界、文件哈希。源码修改使用 Pi edit。

修改完成后：

```bash
python3 "$LYSTAR_UI_RESTORE_ROOT/scripts/verify_applied_patch.py" \
  --patch .visual-restore/scenarios/<scenario>/validated-patch.json \
  --root . \
  --out .visual-restore/scenarios/<scenario>/applied-verification.json
```

补丁验证完成后重新 Capture、Preflight、Compare。区域变好且全局变差时回退。

### 9. 状态与报告

```bash
python3 "$LYSTAR_UI_RESTORE_ROOT/scripts/ui_restore.py" record \
  --root .visual-restore \
  --input .visual-restore/scenarios/<scenario>/restore-result.json
```

`record_restore_state.py` 按场景写入状态、报告、文档、补丁日志和产物哈希清单。

## 运行模式参考

- 公开网页、本地网页、iframe、Bundle：[references/web-runtime.md](references/web-runtime.md)
- uni-app H5：[references/h5-runtime.md](references/h5-runtime.md)
- 小程序：[references/miniprogram-runtime.md](references/miniprogram-runtime.md)
- 原生 App：[references/native-runtime.md](references/native-runtime.md)
- 跨类型视觉门：[references/visual-gates.md](references/visual-gates.md)

## 状态与退出码

场景状态：`observing`、`implementing`、`converging`、`accepted`、`unresolved`、`blocked`。

- `0`：命令完成；结果读取 JSON 状态。
- `2`：输入或 Schema 错误。
- `3`：证据、阈值、文件边界、就绪状态或运行环境阻塞。
- `4`：浏览器、设备、截图、资源或写入失败。

`unresolved` 代表命令完成且结论缺失，不能写成视觉通过。

## 停止条件

完成时分开记录：

- 视觉状态；
- 功能检查；
- 无障碍检查；
- 响应式视口；
- 跨平台结果；
- 控制台与资源问题；
- 未决证据；
- 接受差异；
- 未运行检查。

像素门不能代替结构门。Structure Map 缺失或哈希不一致时，视觉状态保留 `unresolved`。

视觉验收不执行未经授权的 E2E、全量构建、跨平台完整测试或无关测试。
