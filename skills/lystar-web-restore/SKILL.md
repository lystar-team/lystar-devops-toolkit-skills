---
name: lystar-web-restore
description: 从任意公开网页、组件预览、iframe、Bundle、CSS、源码和资源生成可直接打开的 HTML/CSS UI 镜像，并按需提取运行时样式、主题、状态、动画、字体和资源；源码不可得时根据公开运行时结果重建结构并标注证据。
---

# LYStar Web UI Restore

这个 Skill 用于把公开网页中的 UI 还原成可直接打开和检查的本地镜像，再迁移到目标项目。它以页面真实运行时的 DOM、CSSOM、CSS 变量、字体、资源和动画为依据，不把截图或通用 Tailwind 字典当作唯一证据。

## 适用范围

适用于：

- 普通公开网页、设计画廊和灵感站；
- 公开组件库、公开 Demo、Preview 和 CDN Bundle；
- iframe 内的页面、嵌套预览和客户端渲染页面；
- SVG、CSS 动效、部分 Canvas/WebGL/3D/Rive/Lottie 页面；
- v0、21st.dev 等有明确公开入口的特殊平台。

只给截图或设计稿、没有公开页面地址时，交给 `lystar-ui-restore`。需要根据需求重新设计界面时使用 `lystar-ui-design`。桌面应用、登录后页面、被阻断资源和只能运行不能读取的画面要如实标记，不强行生成完整 HTML/CSS。

## 先记住五件事

1. 先读取目标页面自己的 CSS、DOM 和运行时结果，再解释 Tailwind class。
2. 先找真实渲染入口：页面、iframe、Preview 或公开 Bundle 可能不是同一个地址。
3. 保留 CSS 原文值和浏览器最终计算值，不能只保存一个猜出来的像素或颜色。
4. 主题、状态、响应式和动画按页面实际证据采集；没有证据时记录 unavailable，不主动伪造。
5. 镜像是视觉和 DOM 基准，不等同于原作者 React/Vue/TSX 源码，也不自动包含业务事件、接口和数据。

## 工作流程

### 1. 读取项目并形成当前任务 Contract

先确认目标项目技术栈、样式入口、组件目录、目标选择器和目标视口。当前任务只处理目标 UI 以及直接影响它的祖先上下文、子元素、资源和状态；不自动扩展到部署、业务接口或无关页面。

### 2. 读取公开页面

使用 `agent-reach` 读取公开网页，使用 `agent-browser` 开启独立 session 检查真实运行时页面。只使用公开页面、公开 Preview、公开 Bundle、公开 CSS、源码和资源。

发现来源时按以下顺序判断：

- 当前页面是否已经包含目标 DOM；
- 是否存在 iframe 或嵌套 frame；
- 是否存在公开 Preview 或 CDN Bundle；
- 是否能读取页面 CSSOM、内联样式、脚本生成的样式和网络资源；
- 是否属于 SVG、Canvas、WebGL、3D 或其他只能观察运行时的渲染模式。

原始源码不可取时，可以根据公开 Bundle 和运行时 DOM 还原结构，但必须标记为 `reconstructed`，不能声称拿到了原作者源码。

### 3. 来源路由

默认使用通用来源处理器，不按网站清单逐个写适配器。只有页面行为确实不同才使用平台适配器：

- `GenericWebAdapter`：普通页面、SPA、组件库和公开网站；
- `EmbeddedFrameAdapter`：iframe、嵌套 Preview 和跨 frame 页面；
- `PublicBundleAdapter`：公开 CDN/Bundle 页面；
- `V0Adapter`：v0 Preview 的 frame、主题和公开文件；
- `TwentyFirstAdapter`：21st.dev Bundle、主题 URL 和详情页；
- `RuntimeVisualAdapter`：SVG、Canvas、WebGL、Rive、Lottie、3D 等运行时画面。

平台识别只决定特殊入口，不决定 Skill 的适用范围。

### 4. 提取 DOM、CSS 和资源

必须尽量从目标页面的 `document.styleSheets`、CSSOM 和运行时 DOM 读取：

- 目标元素、祖先上下文和子元素的 HTML、class、属性和内联 style；
- 完整选择器、伪类、伪元素、父选择器和实际生效条件；
- `@media`、`@supports`、`@layer`、`@container`、CSS nesting 和嵌套声明；
- CSS 变量的来源、继承、局部覆盖和最终值；
- 布局、尺寸、字体、颜色、边框、圆角、阴影、滤镜、遮罩和变换；
- `@font-face`、`document.fonts`、图片、SVG、视频和背景资源；
- `@keyframes`、animation shorthand、transition 和 Web Animations API。

CSS 原文与 computed style 同时保留。例如：

```text
原始值：gap: var(--gap)
变量值：--gap: 2rem
当前计算值：gap: 32px
```

### 5. 主题、状态和响应式

主题入口自动从以下位置发现：

- `html.dark`、`html.light` 或命名主题 class；
- `data-theme`、`data-mode`、`data-color-scheme` 等属性；
- `prefers-color-scheme`；
- URL 的 `theme` 参数；
- 页面中可访问的 Dark、Light、System 控制项；
- 页面主题变量和切换前后的 CSSOM 差异。

按实际页面能力采集：

- `base`；
- 目标元素存在对应规则时的 `hover`、`focus`、`active`；
- 页面存在展开、打开、关闭、禁用或加载状态时再采集这些状态；
- 页面存在响应式规则时采集桌面、平板或移动视口；
- 需要时采集 `prefers-reduced-motion`。

每个主题和状态单独记录媒体条件、控制策略、是否生效和失败原因。不要把当前浏览器不支持的条件写成已经验证。

### 6. 处理特殊渲染模式

按能力分级交付：

- `dom-css`：可以生成完整 HTML/CSS 镜像；
- `dom-css-svg`：在 HTML/CSS 外保留 SVG 结构和资源；
- `iframe` / `public-bundle`：记录真实入口并生成对应镜像；
- `canvas` / `webgl` / `three-dimensional`：保留 DOM 外壳、公开资源和运行时证据，不伪造 CSS 关键帧；
- `hybrid`：可还原 DOM 外壳，但画面仍由运行时脚本生成；
- `unavailable`：公开内容不足以还原，写清对象和原因。

## 默认交付

默认生成：

```text
ui-mirror/
├── component.html
├── component.css
└── manifest.json
```

需要逐条审计时再生成 `style-report.json`。原始 HTML、CSS、Bundle、字体和临时网络资料放在 `/tmp/` 或项目已有验收目录，不写入 Skill 目录。

`manifest.json` 至少要记录：

- 页面地址、frame 链、公开入口和来源类型；
- 目标选择器、视口、主题、状态和媒体条件；
- CSS 规则数量、class 匹配情况和未匹配 token；
- 字体、图片、SVG、视频、背景和其他资源；
- 动画、关键帧和运行时观察结果；
- `renderMode`、主题控制策略和证据状态。

证据状态只使用：

- `verified`：直接从公开页面、CSSOM、Bundle 或浏览器运行时读到；
- `reconstructed`：根据公开编译结果和运行时结果拼回结构；
- `unavailable`：公开页面没有提供，不能继续猜。

## 运行命令

先将 `LYSTAR_WEB_RESTORE_ROOT` 设为当前安装目录（包含 `SKILL.md`），并把 LYStar 运行依赖目录加入 `PYTHONPATH`：

```bash
export LYSTAR_WEB_RESTORE_ROOT="<lystar-web-restore 安装目录>"
export PYTHONPATH="${LYSTAR_HOME:-$HOME/.lystar}/runtime/vendor${PYTHONPATH:+:$PYTHONPATH}"
```

镜像：

```bash
python3 "$LYSTAR_WEB_RESTORE_ROOT/scripts/build_local_mirror.py" \
  --cdp-url http://127.0.0.1:9222 \
  --page-url 'https://example.com/page' \
  --selector 'main .component' \
  --source auto \
  --states auto \
  --themes auto \
  --viewports desktop,mobile \
  --out /tmp/ui-mirror
```

样式审计：

```bash
python3 "$LYSTAR_WEB_RESTORE_ROOT/scripts/extract_rendered_styles.py" \
  --cdp-url http://127.0.0.1:9222 \
  --page-url 'https://example.com/page' \
  --selector 'main .component' \
  --states auto \
  --themes auto \
  --out /tmp/ui-style-report.json
```

只有排查全部 computed 属性时才加 `--full-computed`。如果页面有 iframe，使用 `--frame-url` 指定真实 frame；否则脚本先按目标选择器和页面内容选择候选 frame。

## 验收重点

- 本地 `component.html` 是否能够在临时静态服务器中打开；
- CSS 规则、变量、字体、伪元素、主题和动画是否来自目标页面证据；
- 白天/夜间、hover/focus、桌面/移动和 reduced-motion 是否按实际条件分别验证；
- iframe、Bundle、Canvas/WebGL 页面是否正确标记渲染能力；
- 原始源码不可得时，交付说明是否明确区分 verified、reconstructed 和 unavailable。

截图只用于最后视觉验收。它能说明最终外观，不能替代 CSS、字体、动画和响应式条件的提取。

使用 `shuorenhua` 处理还原页面里的普通中文文案；不要改写代码中的 class、属性名、接口名和动画名。

## 参考文件

- [references/extraction-workflow.md](references/extraction-workflow.md)：公开页面、frame、Bundle 和来源发现；
- [references/css-parser.md](references/css-parser.md)：CSSOM、Tailwind nesting、变量和动画解析；
- [references/mirror-workflow.md](references/mirror-workflow.md)：本地镜像输出和资源处理；
- [references/output-schema.md](references/output-schema.md)：manifest 和证据字段；
- [references/catalog-testing.md](references/catalog-testing.md)：公开站点目录的批量 smoke 测试。
