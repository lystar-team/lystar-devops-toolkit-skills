# native-runtime 运行流程

适用：原生 App、设备、模拟器。

原生任务需要项目 CaptureAdapter。Adapter 负责设备控制、页面入口、状态应用、截图、诊断和关闭，并输出统一 `capture.json`。

Adapter 提供：

- 设备型号、系统版本、屏幕尺寸；
- DPR、安全区域、系统导航；
- App 版本、构建来源、页面入口；
- 页面状态、Fixture、截图模式；
- 字体、原生组件、系统控件、资源状态；
- 运行限制与未验证项。

浏览器 CSS 像素、字体与 WebView 结果不能代替设备结果。无法读取几何时，Structure Map 使用 `unavailable`，报告保留未决项。

登录页、错误页、空白页、资源失败、截图尺寸错误进入 `blocked`。圆形、圆环、文字、设备图片检查自然比例与显示比例。

`iteration` 用于定位单个状态或区域；`acceptance` 需要同一轮设备、系统、窗口、状态、截图、区域和差异证据。
