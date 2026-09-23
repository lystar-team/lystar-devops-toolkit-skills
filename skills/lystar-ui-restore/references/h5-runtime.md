# h5-runtime 运行流程

适用：uni-app H5 与移动 Web 构建结果。

H5 使用 `agent-browser` CaptureAdapter。项目 Adapter 提供 H5 服务入口、页面路由、状态入口和运行环境。

`iteration` 只用于 H5 调试和局部差异定位；`acceptance` 需要同一轮 Reference Meta、Capture、Preflight、Geometry、Structure Map 和 Diff。H5 结论不代表原生 App。

记录内容：

- H5 构建入口；
- 浏览器或 WebView 信息；
- CSS 视口、DPR、截图尺寸；
- 字体、资源、主题、安全区域模拟方式；
- 页面滚动位置与截图模式；
- H5 与原生页面的状态差异；
- Capture、Geometry、Structure Map 路径。

Clip 坐标使用 `css-viewport-px`。`normalize_geometry.py` 负责 DPR 转换。圆形、圆环、地图、插图禁止独立横纵缩放。

H5 结论不代表原生 App。原生组件、系统导航栏、设备安全区域需要原生 CaptureAdapter 证据。
