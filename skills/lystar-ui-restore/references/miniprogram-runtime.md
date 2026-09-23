# miniprogram-runtime 运行流程

适用：微信、支付宝、百度、头条和其他小程序。

小程序任务需要项目 CaptureAdapter。Adapter 对接平台开发者工具、真机或模拟器，并输出统一 `capture.json`。

Adapter 提供：

- 平台与工具版本；
- 页面入口、页面状态、Fixture；
- 设备型号、屏幕尺寸、DPR、安全区域；
- 原生组件与平台限制；
- 截图、诊断、运行状态；
- 区域几何或 `unavailable` 说明。

浏览器 DOM、CSSOM、WebView、`@eN` 不能替代小程序运行结果。每个平台保存独立基线、Structure Map 与报告。

缺少 CaptureAdapter、页面进入登录页、错误页、空白页、资源失败、截图尺寸错误时，状态使用 `blocked`。平台间证据禁止混用。

`iteration` 用于局部调试；`acceptance` 需要同一轮平台截图、运行状态、区域证据、参考元数据和差异报告。
