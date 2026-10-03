# 本次实际加载的指导内容快照

- 安装位置：/home/yean/.agents/skills/lystar-ui-design
- 执行时读取时间：2026-10-03 13:07–13:40（CST）
- VERSION 文件内容：0.4.2
- 说明：本目录保存的是执行时实际读取的文件副本，不是用历史 VERSION 编号代替当前稿；下表的校验和用于确认快照与安装目录内容一致。

## 执行前读取（核心与按任务选择的参考）

| 文件 | 读取用途 | 与安装目录 md5 一致 |
| --- | --- | --- |
| SKILL.md | 核心方法、范围、视觉边界与交付要求 | 一致 |
| references/reference-map.md | 参考与工具路由 | 一致 |
| references/domestic-ui-examples.md | 后台对照、筛选结果与移动记录/输入案例 | 一致 |
| references/china-product-baseline.md | PC 后台与微信小程序平台基线 | 一致 |
| references/mobile-product-examples.md | 移动案例适用条件（本任务只读文字，未查看其图片） | 一致 |
| references/design-examples.md | 数据核对与长流程的结构推导 | 一致 |
| references/anti-noise.md | 内容准入与视觉校准 | 一致 |
| references/design-contract.md | 方案类交付的记录结构 | 一致 |

## 实际查看的参考原图（执行时查看，不是缩略图）

| 图片 | 迁移的关系 | 未采用部分 |
| --- | --- | --- |
| ant-query.png | 筛选与结果分区、状态文字加色点、数字与行对齐 | 演示数据、整套皮肤、重复标题、装饰背景、配置浮钮 |
| arco-query.png | 筛选网格与结果工具栏的关系、行对齐支持扫描 | 英文枚举、随机数据、整套导航 |
| tdesign-publish.png | 输入顺序、长文字增长、底部操作权重 | 图片像素尺寸、标签/位置示例、具体限制数值 |
| tdesign-orders.png | 状态列表保留对象摘要与当前动作 | 零售金额、优惠、红色主题、示例地址 |
| tdesign-order-detail.png | 同一对象从列表到详情的信息展开方式 | 费用组成、复制编号业务、支付按钮 |

## 审计阶段补充读取（实现冻结后）

- references/imagegen-ui.md、references/review-rubric.md、references/rule-catalog.md
- evals/README.md、evals/fixtures.schema.json、evals/results.schema.json
- evals/scenarios/ui-cn-inspection-report/{fixture.json,review.md,guided/checks.json,guided/result.json}
- evals/scenarios/ui-cn-stock-transfer/fixture.json
- references/examples/sources.json、references/examples/index.html
- agents/openai.yaml、VERSION

## 未读取项

- references/examples 中与本任务无关的 10 张移动产品画面未打开（本任务不是内容消费或品牌阅读场景）。
- ui-cn-inspection-report/guided/index.html 与 first-output.html 未逐行阅读（审计只需要记录与断言清单）。
