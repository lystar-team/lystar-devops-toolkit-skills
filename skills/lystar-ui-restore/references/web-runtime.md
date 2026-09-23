# web-runtime 运行流程

适用：本地 Web、公开 Web、SPA、iframe、公开 Bundle、浏览器可观察的 Vue 与 React 页面。

## Session

页面捕获使用命名 Session。Capture、Geometry、Normalize 共享页面状态。多视口任务使用 `run_matrix.py` 的场景 Session。

```bash
SESSION="$(agent-browser session id --scope worktree --prefix lystar-ui-restore)"
agent-browser --session "$SESSION" set viewport 1440 900 1
agent-browser --session "$SESSION" --restore open "$PAGE_URL"
```

页面导航、刷新、提交、状态变化会使 `@eN` 失效。Fixture 使用稳定 CSS、`data-testid`、`data-restore-region` 或语义定位。

`workflowMode` 控制证据门：

- `iteration` 用于 Capture、局部 Compare 和 Focus Diff。结果不写 `accepted`。
- `acceptance` 必须使用同一轮的 Reference Meta、Capture、Preflight、Geometry、Structure Map、Diff。Reference 尺寸不匹配、Capture 哈希不一致、结构映射不完整或有效比较像素为 0 时停止接受。

## 运行状态

Capture 分开记录：

- `requested`：Config 与 Fixture 请求；
- `applied`：捕获器执行结果；
- `observed`：页面观测结果；
- `unsupported`：捕获器能力缺口。

`freezeTime`、API Fixture、Seed 等请求缺少捕获器实现时，捕获停止。`stateEntry` 指向状态注入入口。

## Readiness

`config.readiness` 控制：

- `requireFonts`；
- `requireImages`；
- `failOnPageErrors`；
- `networkIdle`；
- `timeoutMs`；
- `allowSuspiciousScreenshot`。

捕获门检查目标 URL、登录页、空白页、Ready Selector、字体、图片、页面错误、视口和截图尺寸。登录页 URL 检查不依赖登录状态请求。

## 几何与坐标

`geometry_probe.py` 输出 `css-viewport-px`。`normalize_geometry.py` 使用 Capture、Config、DPR、滚动与 Clip 生成 `render-image-px` Structure Map。

Structure Map 记录 Capture 哈希、Geometry 哈希、Regions 哈希和视口 ID。Compare 接受哈希一致的映射。

## 图层检查

- 图片边框与图表圆环不能重叠。
- 图片文字与 DOM 文字不能显示两份。
- CSS 阴影与图片光晕不能形成重复边缘。
- Canvas 或 WebGL 主画面不能叠加 CSS 假图表。
- 旧资源引用进入检查记录。

## 失败状态

登录页、空白页、Ready Selector 失败、字体缺失、图片未完成、页面错误、截图尺寸错误、目标 URL、实际 DPR、滚动或截图哈希不一致进入 `blocked`。结构选择器缺失进入 `unresolved`。
