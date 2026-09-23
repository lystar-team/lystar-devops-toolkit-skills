# visual-gates

适用：Web、H5、截图、SVG、Canvas、WebGL、3D，以及具备项目 CaptureAdapter 的小程序与原生 App。

## Gate 0：Schema 与场景

- Adapter、Config、Fixture、Regions、Evidence、Assets 通过对应 Schema。
- `workflowMode` 为 `iteration` 或 `acceptance`；`accepted` 只允许在 `acceptance` 产生。
- 场景文档使用同一个 `scenarioId`。
- 产物写入 `scenarios/<scenarioId>/`。
- Manifest 记录输入、输出、哈希与生成阶段。

Schema 负责字段结构；Python 校验跨文件关系。

## Gate 1：目标页与证据

- Reference 路径、尺寸、哈希、坐标系存在记录；验收模式使用 `inspection/reference-meta.json`。
- 页面入口、状态、主题、字体、语言、时区、设备存在记录。
- `decisions.json` 包含 Contract。
- Region 包含 `id`、`bbox`、证据、负责人、允许属性、禁止变更。
- 无法确认的文字、图片、图标、指标、动作使用 `unavailable`。

## Gate 2：素材

每个图片、SVG、图标、Canvas、WebGL、视频、Lottie、Rive、原生媒体进入 `assets.json`。

检查内容：

- 来源路径；
- 自然尺寸与显示尺寸；
- 原始比例与显示比例；
- 裁切与可见边界；
- 背景与 Alpha；
- `visualOwner`；
- 证据 ID；运行时 Canvas/WebGL 素材还要在 `evidence.json` 中有对应证据；
- 审核状态。

圆形与圆环禁止比例变化。imagegen 输出检查文字、数字、Label、Logo、水印、方形底、黑边、灰边、重复轮廓和裁切。

项目文件素材检查文件事实；Canvas、WebGL、Lottie、Rive、原生媒体等运行时素材检查 `sourceKind`、`visualOwner`、`evidenceIds` 和 `evidence.json`。提供 Structure Map 时，素材显示比例与归属区域的归一化几何核对。

## Gate 3：图层负责人

每个复杂效果只有一个可见负责人。

| 效果 | 负责人 |
| --- | --- |
| 数据环形图 | `chart` |
| 静态环形边框 | `image` 或 `svg` |
| 标题文字 | `text` |
| 标题阴影 | `css` |
| 地图底图 | `image` |
| 地图点位 | `component` |
| Canvas 主画面 | `canvas` |
| 原生控件 | `native` |

检查图片文字与 DOM 文字、图片光晕与 CSS 阴影、图片边框与图表圆环、Canvas 与 CSS 图形的重复绘制。

## Gate 4：坐标与几何

- Reference 使用 `reference-image-px`。
- Geometry 使用 `css-viewport-px`。
- Render 使用 `render-image-px`。
- Structure Map 记录 DPR、滚动、Clip、缩放、平移。
- Structure Map 记录 Capture、Geometry、Regions 哈希和工作流模式。
- 接受模式还要记录参考图哈希、渲染图哈希和截图模式。
- 每个视口拥有独立 Structure Map。

DPR 大于 1、Clip、Scroll Segment、Full Page 场景需要坐标转换结果。Compare 不读取原始 CSS 坐标。

## Gate 5：字体与内容

每个活动区域记录边界、中心点、文字行数、字体、字号、字重、行高、字距、颜色、间距、阴影、描边、透明度。

数值、单位、Label 先确定组边界。长数字、长单位、长 Label 进入最大长度检查。参考图缺少阴影证据时，标题不增加阴影。

## Gate 6：图表与数据

- 数据图表使用正式图表组件。
- 图表配置、Fixture 数据、布局分开。
- Mock 与 Live 保持区域结构。
- 空数据保留页面骨架。
- CSS 条、渐变块、图片不代替数据图表。

## Gate 7：舞台与响应式

固定画布记录设计宽高，内容使用一个等比缩放值。内容舞台与外围背景分开。宽屏、高度不足、设计尺寸分别保存证据。

普通页面读取项目断点，保留流式布局、折叠、滚动和组件状态。每个视口绑定尺寸匹配的 Reference、Regions、Capture、Structure Map、Diff；缺少匹配 Reference 时保留 `unresolved`。

## Gate 8：运行就绪

Capture 状态分为 `requested`、`applied`、`observed`、`unsupported`。

截图前检查：

- 目标 URL；
- 登录页与空白页；
- Ready Selector；
- 字体；
- 图片；
- 页面错误；
- 动画状态；
- 页面数据状态；
- 视口与 DPR；
- 滚动与截图模式；
- 截图尺寸。

捕获器缺少 Fixture 请求能力时，状态进入 `blocked`。Uniform Screenshot 由 `allowSuspiciousScreenshot` 控制。

## Gate 9：差异

差异报告分开记录：

- Pixel Diff；
- Region Diff；
- Anchor Error；
- Structure Mapping；
- Mask Coverage；
- Accepted Variance；
- Limitations。

算法使用向量化像素差、遮罩位图、积分图区域统计和截图哈希。Structure Map 缺失、哈希失配、选择器缺失时，结构状态不能写成 `verified`。关键区域的独立阈值失败时，验收结果为 `blocked`。

## Gate 10：补丁

补丁前先确认当前结果不是 `blocked`。验收模式的 `accepted` 必须引用同轮 Diff；手工填写 `accepted` 不能代替 Diff、Capture、Preflight 和有效比较像素。

每个补丁绑定一个 Region、一个 Property Family、一组 Evidence、一组文件。

准备检查：

- Patch Schema；
- Scenario ID；
- Region 与 Evidence；
- `allowedProperties`；
- `forbiddenChanges`；
- `visualOwner`；
- 文件允许列表；
- 源文件哈希；
- 回退方法。

Pi edit 完成源码修改。`verify_applied_patch.py` 记录新哈希与变更状态。补丁完成后重新 Capture、Preflight、Compare。

## Gate 11：报告

报告分开写：视觉、功能、无障碍、响应式、跨平台、控制台、资源、未决证据、接受差异、未运行检查。

`iteration` 只用于快速定位差异，不写 `accepted`。`acceptance` 必须经过 Reference Meta、Capture、Preflight、Regions、Structure Map（适用时）、关键区域和 Diff 检查。`accepted` 只允许由验收模式产生。

视觉状态使用 `accepted`、`unresolved`、`blocked`、`not-run`。像素门通过不能替代结构门。

## 提效入口

- `ui_restore.py init`：建立场景目录与 Schema 文档。
- `asset_catalog.py`：文件缓存、素材元数据、重复文件。
- `run_matrix.py`：Session 复用、多视口 Capture、Geometry、Normalize、Preflight、Compare。
- `focus_diff.py`：Reference BBox 与 Render BBox 对照。
- `record_restore_state.py`：场景产物、补丁日志、哈希清单。
