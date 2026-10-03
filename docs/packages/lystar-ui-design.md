# lystar-ui-design

从使用者的进入原因、业务对象与状态出发，推导任务路径、信息优先级和页面结构，再建立构图、排版、比例、密度与颜色一致的视觉系统。按要求交付方案、实现或评审，分别核对任务、视觉和运行结果。核心直接包含内容准入和明确视觉边界，不依赖后置参考恰好被加载；局部修改保持原范围，不启动完整研究。视觉还原使用 `lystar-ui-restore`，公开网页提取使用 `lystar-web-restore`。

## 安装

从独立 Release 包解压后运行：

```bash
./install.sh --harness codex
```

从源码仓库运行：

```bash
./install-lystar-ui-design.sh --harness codex
```

安装后由 Harness 按 Skill 名称 `lystar-ui-design` 加载，不增加 PATH 命令。核心方法、短设计记录、任务推演、平台适配、内容与视觉校准及评审说明随包安装；配套文件不再维护第二套核心规则或历史通过状态。国内参考保留十组、十五张本地原图，浏览入口为 `references/examples/index.html`。按当前设计问题选取相关说明与实际原图，不按“同为小程序”照搬品牌或运营页面，也不要求每次加载全部图片。

## 实际加载与验证

源码更新后运行对应安装器，再比较源码与 `$HOME/.agents/skills/lystar-ui-design` 的 SKILL.md、引用文件和图像资源。Pi 与 Codex 统一从该目录读取；已有会话是否缓存 Skill 列表取决于 Harness，新会话或显式重新读取可以确认加载内容。安装成功、文件相同与实际行为改善分别验证。

本地浏览图文参考可直接打开 `references/examples/index.html`。`evals/scenarios/ui-cn-inspection-report/guided/index.html` 是历史巡检 Web 样例，使用给定字段和本地模拟提交，不作为重构后的质量结论或小程序真机证据。维护测试按实际授权执行，不强制双会话对照；生成者自检和单个智能体的连续场景不能称为独立抽样。格式、安装和少量样例通过都不能单独证明稳定、效率提升或资深设计师水准。

## 环境能力

页面检查使用环境已有的浏览器能力；生图任务使用已有图像生成能力。公开资料可按需查询站酷、UI 中国、UI Notes、设计方原站、国内官方页面，以及 Mobbin、Refero、Component Gallery；未连接的付费 MCP 不能写成已使用。`frontend-ui-engineering`、`shuorenhua` 等外部 Skill 不随独立包安装，只在当前任务需要且环境可用时读取。缺少可选能力时执行可完成部分，说明未执行项，不把安装成功当成页面或行为验证通过。
