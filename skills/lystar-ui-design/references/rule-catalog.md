# UI Rule Catalog

## 用途

规则目录记录可重复使用的 UI 判断、它们的证据和最窄责任位置。它不是把每次评审意见都变成全局规则的清单。

## 规则记录格式

```text
ID：ui/<surface>/<short-name>
Status：existing | proposed | accepted | rejected | superseded
Scope：
Rule：
Why：
Exceptions：
Source：
Evidence：
Bad example：
Good example：
Related eval：
Last validated：
```

### 字段要求

- **ID**：稳定、短、描述对象和问题，不随措辞润色改变。
- **Status**：`existing` 表示当前 Skill 已有的指导；`proposed` 等待证据和人工确认；`accepted` 才能作为已采纳的项目规则。
- **Scope**：写明适用的页面、组件、状态和技术边界。
- **Rule**：写成可观察的行为，避免“高级”“优雅”“更有质感”等不可检查形容词。
- **Why**：说明对读者任务、理解、操作或可信度的影响。
- **Exceptions**：记录何时不适用，避免规则被无条件套用。
- **Source / Evidence**：链接到需求、项目文件、组件 API、已发布页面、评审记录或 eval 运行；没有证据就保持 `proposed`。
- **Bad / Good example**：用最小代码、页面片段或截图说明差异，不复制整页。
- **Related eval**：关联能验证该规则的固定场景。
- **Last validated**：写实际验证日期或 `unvalidated`，不把计划写成结果。

## 规则落点判断

```text
需要读者、产品或项目上下文？
  └─ 是 → 设计指导或项目级参考
  └─ 否 → 能否稳定静态识别？
             └─ 否 → 保留为人工判断或 warning
             └─ 是 → 是否有明确、低误报的修复？
                        └─ 否 → warning / 人工判断
                        └─ 是 → lint / validator
```

以下问题属于不同责任位置：

- 读者任务、信息层级、构图、证据解释：`lystar-ui-design` 判断。
- 项目字体、颜色、token、组件 API、图标和交互状态：项目设计系统或组件库。
- accessible name、heading 顺序、溢出、任意 spacing 等可稳定识别问题：确定性检查候选。
- 截图采集、浏览器状态、console、a11y 和环境失败：`agent-browser` / eval harness。
- 一次出现且不能泛化的意见：当前任务记录，不升级规则。

## 当前已有指导的命名索引

以下条目索引 `lystar-ui-design` 的执行与视觉指导，状态为 `existing`，不代表已通过行为评测。规则正文保留在 `SKILL.md`；目录记录范围、例外与验证依据，不维护第二份执行规则。

| ID | 当前关注点 |
|---|---|
| `ui/composition/generic-hero` | 没有产品依据时，不把营销式 Hero 套到后台或工具页面 |
| `ui/composition/card-nesting` | 不用卡片套卡片修补层级问题 |
| `ui/visual/gradient-default` | 不用紫蓝渐变等生成式默认视觉替代真实品牌和内容 |
| `ui/visual/decorative-icon-box` | 不用装饰性图标盒填充信息空白 |
| `ui/content/fake-data` | 不从视觉参考图或生成图中制造业务指标、功能或品牌信息 |
| `ui/composition/multiple-signatures` | 一个页面保留一个主识别点，其余选择服务于它 |
| `ui/content/task-relevance` | 可见内容服务用户任务，不为 description 插槽补实现说明 |
| `ui/composition/task-boundaries` | 主次任务有承载位置；多表按共同任务判断，不按数量禁用 |

### `ui/content/task-relevance`

- **Status**：existing；行为效果未验证。
- **Scope**：本次新增或修改的 UI 内容，尤其是业务管理页面。
- **Rule**：见 `../SKILL.md` 的“内容准入”。
- **Why**：避免将实现过程当成用户需要的信息，或用同义改写保留无用途文案。
- **Exceptions**：有事实依据的输入限制、动作后果、错误帮助与可访问性描述保留；技术工具由读者任务和 Contract 明确允许内容。
- **Source**：Yean 确认的前端优化方案第 4.1 节；原始内部材料未随仓库分发。
- **Evidence**：Yean 关于多余 description 和底层逻辑上屏的重复反馈；未复现历史页面，未运行模型对照。
- **Bad / Good example**：编辑标题已说明用途却补“在此编辑信息” → 省略重复说明，保留真实输入限制。
- **Related eval**：`../../yean-develop-style/references/trigger-tests.md` 的 `copy-purpose`、`visible-boundary`、`technical-reader` 是预期样例，不是运行记录。
- **Last validated**：unvalidated。

### `ui/composition/task-boundaries`

- **Status**：existing；行为效果未验证。
- **Scope**：新建或结构性修改的业务管理页面；局部改字不触发结构重做。
- **Rule**：见 `../SKILL.md` 的“任务与承载位置”。
- **Why**：功能需要可用入口，不需要所有任务默认展开；拆文件不能解决产品层级问题。
- **Exceptions**：同时核对、主从编辑与明确工作台需求可以多表；长流程不强制使用 Dialog。
- **Source**：Yean 确认的前端优化方案第 4.2—4.4 节；原始内部材料未随仓库分发。
- **Evidence**：Yean 关于功能同页堆叠、不区分 Dialog 与多表展示的重复反馈；未复现历史页面，未运行模型对照。
- **Bad / Good example**：列表、短编辑与低频详情全部常驻 → 主列表保留入口，编辑与详情进入适用位置；对账数据仍可同时展示。
- **Related eval**：`../../yean-develop-style/references/trigger-tests.md` 的 `list-and-edit`、`long-workflow`、`compare-data`、`local-change` 是预期样例，不是运行记录。
- **Last validated**：unvalidated。

## 采纳条件

一条规则进入 `accepted` 前，至少要有：

1. 明确适用范围和例外。
2. 能回溯到真实材料、重复反馈、项目决定或固定 eval。
3. 有一个可以观察的 bad / good 差异。
4. 已判断它应该进入设计指导、项目 primitives、确定性检查还是验收工具。
5. 经过人工确认，并完成受影响场景的最小回归。

单个截图、单个模型的偶发失败或无法说明修复方式的偏好，不足以采纳为全局规则。
