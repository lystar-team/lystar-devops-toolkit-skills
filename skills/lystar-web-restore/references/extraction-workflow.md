# 公开页面提取流程

这份参考文件只讲公开入口和来源发现。CSSOM 解析看 `css-parser.md`，本地镜像输出看 `mirror-workflow.md`，批量目录验证看 `catalog-testing.md`。

## 1. 页面入口

打开目标地址后，按下面顺序找真实渲染入口：

1. 当前页面是否已经包含目标元素；
2. 是否存在 iframe 或嵌套 frame；
3. frame 是否指向公开 Preview、Bundle 或 CDN HTML；
4. 页面是否通过脚本延迟生成 DOM、样式或画布；
5. 是否存在公开 CSS、source map、RSC/HTML 文件数据或资源地址。

默认使用独立的 `agent-browser` session。页面导航、点击或重新加载后，重新获取 snapshot 和 frame 引用。

如果用户给了 `--frame-url`，优先使用该 frame。没有指定时，按目标选择器和 frame 内容选择候选；目标为 `body` 且页面包含多个 frame 时，优先让用户提供 `--frame-url`，否则记录选择依据。

## 2. 通用来源类型

### 普通页面或 SPA

直接读取目标 DOM、CSSOM、内联样式、运行时变量、字体、资源和动画。等待页面达到可观察状态后再采集；需要时使用 `--wait-ms`。

### iframe 或嵌套 Preview

不要把展示页的 DOM 当作 Preview 的 DOM。进入实际 frame 后再读取目标元素、样式表、资源和状态。跨域不妨碍通过 CDP 读取已经打开的子 frame；如果 frame 没有公开内容，记录 unavailable。

### 公开 Bundle

Bundle 可以支持视觉还原，但不代表原始组件源码公开。保留 Bundle 地址、页面 DOM、CSSOM、主题参数和证据状态。对 v0 和 21st 使用专门主题入口；其他站点只有在页面实际使用 `theme` 参数时才改写 URL。

### SVG、Canvas、WebGL 和 3D

先读取 DOM、SVG 节点、canvas 数量、WebGL 上下文、脚本和资源。CSSOM 没有描述画面时，不能根据截图伪造 CSS。输出 `canvas`、`webgl`、`three-dimensional` 或 `hybrid`，并在 manifest 中写明运行时依赖。

## 3. 页面和资源发现

记录：

- 页面 URL、最终 URL、frame 链和来源类型；
- 页面 title、readyState、脚本和样式表地址；
- iframe 的 src、图片、视频、SVG 和背景资源；
- 可读与不可读的 stylesheet，以及跨域读取失败原因；
- 页面是否存在 Canvas、WebGL、SVG、Web Animations 或持续脚本更新。

地址被发现不等于文件已经下载。manifest 中分别记录 `discovered`、`downloaded` 和 `unavailable`。

## 4. v0 和 21st 的特殊入口

- v0：公开模板页常把 Preview 放在 iframe 中。读取 Preview frame 的 DOM 和 CSS，不把展示页当作组件页。
- 21st.dev：详情页常把 Demo 放在 iframe 中，Bundle 可通过 `?theme=light`、`?theme=dark` 重新打开。`Component.tsx` 显示 `Unlock code` 时，只能说 Bundle 和运行时结果可读。

两者只是适配器，不应影响 Generic Web 路径。

## 5. 失败说明

失败时具体记录：

```text
已验证：页面存在 iframe，目标元素在子 frame 中。
已验证：CSSOM 中存在 hover 规则。
未验证：当前浏览器不支持目标媒体条件。
无法确认：页面只公开了运行时画面，没有公开 CSS 或源码。
```

不要把页面打不开、需要登录、资源过期、跨域不可读或源码锁定写成“已还原”。
