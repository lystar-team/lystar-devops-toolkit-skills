# 配色方法与本地基线

定新配色、修配色或项目缺少颜色体系时读取。已存在且满足本次要求的项目 token 直接复用，不重新挑主色。本文件只解决配色与跨页一致性，不要求研究一套参考图库。

## 选择与迁移

| 项目事实 | 使用方式 |
| --- | --- |
| 有 DESIGN.md / 品牌 / 现有主题 | 读取相关颜色角色、组件和当前主题；实现仍引用项目唯一 token。规范与实现冲突时核对作用域、当前要求与实际页面 |
| 已有 Ant Design | 复用 `ConfigProvider` 与组件的语义 token、已配置的算法；颜色取运行主题。已有 `@ant-design/colors` 才用其色阶工具，不为普通配色安装它 |
| 已有 TDesign | 复用 `--td-*` 语义变量和浅深主题；颜色带有透明度时在实际表面检查 |
| 已有 Arco | 复用项目 `--primary-*`、`--color-text-*`、`--color-bg-*` 与主题机制；不要拼入另一系统的背景或灰阶 |
| 已有 WeUI | 复用 `--weui-*` 与项目组件。品牌绿、文本、背景、链接是不同角色；微信平台不意味着必须采用微信品牌绿 |
| 无系统的普通操作页面 | 可选择本地 `td-light` 作有依据的起点；需要暗色时用配套 `td-dark`。这是可调整的设计判断，不是“中国产品默认蓝” |
| 明确需要微信服务视觉、且没有已有系统 | 可选择 `weui-light` 适配基线；不覆盖既有 WeUI 组件、不照搬消费运营页 |
| 品牌/行业已有独特色彩 | 保留给定品牌。用该项目或系统色阶映射操作、文字、浅背景与状态；不要因对比度问题重做全部品牌 |

不为了颜色迁移安装整套 UI 框架。以上系统的 token 名称与 API 以项目安装版本为准；下面基线是固定日期的可离线使用材料，不称外部最新版本。

## 角色与状态决定值

| 角色 | 决定什么 | 常见错配 |
| --- | --- | --- |
| page / surface / surface-subtle | 页面、容器和嵌套表面的关系 | 每个模块换一种浅彩色，或全白后无层级 |
| text / text-secondary | 正文与必须读取的辅助信息 | 将40%黑或35%白的占位/禁用等级用于业务正文 |
| brand / action / on-action | 品牌识别、主操作底色、其上文字 | 默认认为品牌色适合小号白字 |
| action-hover / active / disabled | 同一动作的实际状态 | 只验默认，hover变亮后文字不清；禁用仍像可执行 |
| link / selected / on-selected | 导航入口与当前选择 | 所有链接、按钮、标签套同一个高饱和底色 |
| border / control-border / focus | 装饰分隔、必要控件边界和焦点 | 为所有分割线强制3:1，或焦点太浅无法识别 |
| success/warning/error text与bg | 状态文字、图形与可选浅背景 | 把用于色块或图形的橙/绿原值直接用于小文字 |

统一定义实际需要的角色，组件引用它们；不要在每张卡片里再挑 hex。不同语义可以同色，语义相同应一致。品牌色可以保留，主操作使用同色系更适合文字的等级；状态仍用文字、图形或标签说明，颜色不能成为唯一线索。核心的装饰边界继续适用，不要求给状态增加底色容器。

## 可直接使用的三个基线

具体值的唯一来源是 [color-palettes.json](color-palettes.json)。它包含来源与本地适配说明；脚本从该文件输出，避免 Markdown、CSS 和检查脚本各维护一份数值。

| 基线 | 来源与本地调整 | 适用条件 |
| --- | --- | --- |
| `td-light` | TDesign浅色灰阶、brand-7/8/9；可读辅助文字用font-gray-2，状态文字用较深等级，必要输入边界用gray-8 | 无现有系统的日常操作界面；既有TDesign项目优先原组件 |
| `td-dark` | TDesign暗色表面和色阶；保留brand-8作品牌，白字操作底用brand-7/6/5，链接与选择文字用brand-9，状态文字用各色阶8 | 任务确需暗色；不是把浅主题取反 |
| `weui-light` | 保留WeUI品牌绿、背景、FG-0/1及链接色；操作/状态文字和部分浅背景是经过配对检查的本地适配，不冒充官方值 | 无系统且明确选择微信服务视觉；不自动覆盖WeUI主题 |

在 Skill 目录执行；复制时映射到项目现有 token 名称，不重复新建平行主题：

```bash
python3 scripts/color_tools.py palette td-light --css
python3 scripts/color_tools.py check --preset td-light
```

脚本仅用 Python 标准库，默认只读，不搜网页、不安装包、不修改项目。已有项目 token 就检查实际值；没必要先导出基线。`palette` 输出 `--color-*` 是参考命名，不要求项目改名。切换主题时须由项目状态或作用域承载，两个 `:root` 导出不可无条件叠加。

## 验证实际搭配

普通文字按 WCAG 2.2 AA 至少4.5:1，大字至少3:1。大字通常是至少24 CSS px，或至少约18.67 CSS px且加粗；不能把普通14px标签按大字豁免。识别控件或状态所需的图形/边界至少3:1；装饰分割、不可操作的禁用控件与标识有不同适用条件。不要用禁用豁免解释必须读取的说明。

按实际背景合成 alpha 再计算 sRGB 相对亮度；亮度通道转线性后用 `(L亮+0.05)/(L暗+0.05)`。不能直接相除RGB值、把透明色视为不透明或把4.499四舍五入当4.5。渐变、背景图、多层透明、遮罩及色域差异需在实际页面验证，静态色对检查不能替代。

本次实测的常见错配（小号白字、白底普通文字）：WeUI品牌绿 `#07c160` 配白字约2.38:1；Ant默认蓝 `#1677ff` 配白字约4.10:1；TDesign暗色brand-8 `#4582e6` 配白字约3.76:1；TDesign浅色hover `#366ef4` 配白字约4.47:1；40%黑字配白底约2.85:1。它们不满足上述普通文字标准，但这不等于整个设计系统无效；用途、文字大小、状态和背景分别判断。

先改本次的角色映射：保留品牌，选择适合文字的同色阶或前景组合；使用不透明色或适当文字 token，检查 default、hover、active、focus、selected 和当前主题。已有项目组件要在共同主题/组件责任点调整，不另贴零散覆盖。如果明确要求保留亮品牌底，可选择经检查的深色前景，而非强行白字。

颜色对 JSON 示例：

```json
[
  {"name":"正文","foreground":"rgba(0,0,0,0.6)","background":"#ffffff"},
  {"name":"焦点","foreground":"#0052d9","background":"#ffffff","minimum":3},
  {"name":"半透明表面上的字","foreground":"#ffffff","background":"rgba(0,0,0,0.6)","canvas":"#ffffff"}
]
```

```bash
python3 scripts/color_tools.py check --pairs /绝对路径/实际颜色对.json
```

默认门槛4.5，`minimum` 由实际角色决定；禁用/装饰对不拿普通文字门槛乱验。退出0表示这些配对通过，1表示至少一对不足，2表示输入无法计算。不支持的 `var()`、hsl、oklch先由浏览器解析为真实 sRGB；透明背景缺少 canvas 时明确报错，不偷用白底。页面上还须核对实际字体、状态、背景、焦点和整体视觉，不能把脚本通过写成全站无障碍认证。

## DESIGN.md 怎么帮助日常任务

借鉴 `google-labs-code/design.md` 的职责：精确 token 与使用理由相连，组件用引用连接前景/背景，跨页复用同一系统。该公开格式处于alpha，本 Skill 不强制安装其CLI、不强制改文件结构。

已有 DESIGN.md：优先读颜色与相关组件章节，再核对真实 CSS/主题值。建立新系统且需要跨页复用时，在项目现有规范中简写来源、颜色角色/引用、实际组件状态和适用主题；只有当前授权确需新规范才写 DESIGN.md。不要从不相关的示例抄品牌、布局或“全页只能一次主色”等普遍禁令，也不每次生成一份长文档。

## 已核对的事实源

核对日期：2026-10-03；以下GitHub链接固定到当时读取的commit，不依赖浮动分支。取值与完整主题可在源文件回查。

- [Google DESIGN.md格式](https://github.com/google-labs-code/design.md/blob/9bf8eae67128b6cc55ad9bf86665767deb4c11cd/docs/spec.md)：token、语义引用、使用说明及alpha边界。
- [Ant Design色彩说明](https://github.com/ant-design/ant-design/blob/5a4a6aa9d4e54ac2ffa64fee8244005867d5ceee/docs/spec/colors.zh-CN.md) / [中性色与色阶映射](https://github.com/ant-design/ant-design/blob/5a4a6aa9d4e54ac2ffa64fee8244005867d5ceee/components/theme/themes/default/colors.ts)：系统色与产品色、功能色一致性、透明中性色。
- [TDesign浅主题](https://github.com/Tencent/tdesign-common/blob/0c9b38898608d9f558d4e85a38843bd85db241e8/style/web/theme/_light.less) / [深主题](https://github.com/Tencent/tdesign-common/blob/0c9b38898608d9f558d4e85a38843bd85db241e8/style/web/theme/_dark.less)：本地蓝灰基线的色阶和表面来源。
- [WeUI浅主题](https://github.com/weui/weui/blob/7a6f8ad7700d0d6f1361dbe5e2942b4b129e7a7e/src/style/base/theme/vars/light.less)：品牌、文字、背景和链接的分离。
- [Arco语义变量](https://github.com/arco-design/arco-design/blob/c2b050d9c7ce94bebba94f616a0721344231caac/components/style/theme/global.less)：按角色与状态读取项目CSS变量，不混搭别的系统默认值。
- W3C [文字对比度](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html) / [非文字对比度](https://www.w3.org/WAI/WCAG22/Understanding/non-text-contrast.html)：阈值、适用条件与例外。
