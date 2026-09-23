# manifest 和样式报告字段

## manifest v3

```json
{
  "schemaVersion": "3.0",
  "mode": "mirror",
  "renderMode": "dom-css",
  "source": {
    "platform": "unknown | v0 | 21st",
    "sourceType": "page | iframe | public-bundle | canvas | webgl | three-dimensional",
    "pageUrl": "",
    "frameUrl": "",
    "selector": ""
  },
  "capture": {
    "time": "ISO 8601",
    "source": "auto | generic | v0 | 21st | runtime",
    "themes": ["light", "dark"],
    "requestedThemes": ["auto"],
    "states": ["base", "hover"],
    "viewports": []
  },
  "discovery": {
    "url": "",
    "readyState": "complete",
    "frames": [],
    "scripts": [],
    "stylesheets": [],
    "svgCount": 0,
    "canvasCount": 0,
    "webgl": false
  },
  "coverage": {
    "classTokenCount": 0,
    "matchedRuleCount": 0,
    "unmatchedTokens": []
  },
  "themes": [],
  "states": [],
  "assets": [],
  "fonts": [],
  "fontFaces": [],
  "evidence": []
}
```

## 样式报告

详细审计模式可以额外保留：

- CSS class token 对应的完整选择器、声明、条件和 stylesheet 来源；
- 内联 style、CSS 变量、继承值和 computed style；
- 元素的 DOM path、尺寸、字体、颜色、布局、伪元素和动画；
- `@keyframes`、Web Animations 的 timing 和 keyframes；
- `@font-face`、document.fonts、图片、视频、SVG 和背景资源；
- 每个主题、视口和状态的媒体条件与采集结果。

## 主题记录

主题记录至少包含：

```json
{
  "name": "dark",
  "captured": true,
  "controller": {
    "strategy": "preview-html-class | public-bundle-query | generic-discovery",
    "mechanisms": ["class", "data-attribute", "media-query"],
    "discovered": {}
  },
  "states": []
}
```

## 证据状态

只使用三种状态：

- `verified`：页面、CSSOM、Bundle 或浏览器运行时直接读到；
- `reconstructed`：根据公开编译结果和运行时结果拼回结构；
- `unavailable`：公开页面没有提供，不能继续猜。

地址被发现不等于文件已下载。资源记录可继续区分 `discovered`、`downloaded` 和 `unavailable`。
