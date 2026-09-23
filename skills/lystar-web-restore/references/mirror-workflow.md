# 本地 HTML/CSS 镜像

## 默认目录

```text
ui-mirror/
├── component.html
├── component.css
└── manifest.json
```

`component.html` 保留目标 DOM、class、属性、内联样式和必要祖先上下文。`component.css` 保留目标元素、祖先、子元素、伪元素、字体、变量、动画和实际条件。`manifest.json` 记录来源、覆盖范围、资源、状态、主题和证据。

## CSS 筛选

默认只保留：

- 目标元素、祖先上下文和子元素实际出现的 class 规则；
- `:root`、`html`、`body`、通用重置和目标属性选择器；
- 目标元素和祖先的 `::before`、`::after`；
- 规则所在的 `@layer`、`@media`、`@supports`、`@container` 条件；
- 页面中的 `@keyframes`、`@font-face` 和目标资源；
- 目标元素依赖的 CSS 变量和内联声明。

页面依赖难以判断的全局规则时使用 `--all-css`。跨域 stylesheet 的 CSSOM 不可读时，镜像会保留公开 stylesheet 的 `@import`，再从可读规则中筛选目标内容。不要先把变量、媒体条件或选择器压成猜出来的固定值。

## 主题镜像

如果至少采集到 light 和 dark：

- 保留页面原有 class、data 属性和媒体规则；
- 额外生成 `html[data-mirror-theme="light"]` 和 `html[data-mirror-theme="dark"]` 覆盖；
- 对 `prefers-color-scheme` 生成手动切换桥接规则；
- HTML 暴露 `window.setMirrorTheme('light'|'dark')`；
- 默认跟随系统主题，验收时手动切换两次。

如果页面只有当前主题，manifest 记录 `current`，不强行生成另一套主题。

## 渲染模式

manifest 的 `renderMode` 表示交付能力：

- `dom-css`：HTML/CSS 可直接还原；
- `dom-css-svg`：包含可复用 SVG；
- `iframe` / `public-bundle`：入口来自子 frame 或公开 Bundle；
- `canvas` / `webgl` / `three-dimensional`：画面由运行时绘制；
- `hybrid`：DOM 外壳可还原，画面仍依赖运行时；
- `unavailable`：公开证据不足。

Canvas、WebGL、3D、Rive 和 Lottie 页面不能只靠截图生成假的 CSS。镜像可以保留 DOM 外壳、资源地址和运行时观察结果，并在 manifest 中标注限制。

## 打开和验收

普通 HTML 可以直接打开。资源或字体受本地文件限制时，使用临时静态服务器：

```bash
cd /tmp/ui-mirror
python3 -m http.server 4173
```

验收顺序：

1. 页面是否能打开，DOM 层级是否完整；
2. 背景、文字、边框、阴影、圆角、字体和尺寸；
3. 伪元素、变量、媒体查询和动画；
4. 主题、hover/focus、桌面/移动和 reduced-motion；
5. Canvas/WebGL/3D 页面是否正确显示为 hybrid 或 runtime 证据。

镜像是迁移到项目时的视觉基准，不是原作者 React/Vue 源码，也不自动包含业务事件、接口和真实数据。
