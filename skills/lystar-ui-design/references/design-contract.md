# UI Design Contract

## 用途

设计合同把一次 UI 任务中的读者任务、证据、构图和实现边界写成可回读的短记录。它服务于 `build`、`major-redesign`、结构性 `modify` 和完整 `review`；普通局部样式修改只保留必要字段。

设计合同是任务上下文，不是全局品牌规范。项目已有权威文档、组件 API、业务需求和 `AGENTS.md` 时，合同必须服从它们。

## 合同模板

```text
Scope：
Reader：
Reader job：
Primary decision or action：
Secondary tasks / entry / surface：
Visible content / allowed fields：
Required help / action consequences：
Strongest supported answer or relationship：
Evidence and source：
Caveat / uncertainty：
Composition hypothesis：
Available primitives：
Named anti-patterns to avoid：
Required states and responsive changes：
Verification target：
Open decisions：
```

只填写本次任务需要的字段；合同属于任务上下文，不作为页面说明展示。局部修改沿用既有范围，不为补齐模板推测字段或重做页面。

## 填写顺序

1. **Scope**：写清本轮页面、组件、状态、视口和输出形式。URL、截图或组件名只确定范围，不自动授权修改。
2. **Reader / Reader job**：说明谁在什么上下文中，要理解、比较、决定、输入还是连续操作什么。
3. **Primary decision or action / Secondary tasks**：确定首屏主任务，记录次级任务的入口与承载位置，按 `../SKILL.md` 的“任务与承载位置”判断。功能有入口不等于必须常驻展开；多表需要说明共同任务，不能只写组件名称。
4. **Evidence and source / Visible content / Required help**：从需求、项目约定和本次涉及的既有内容提取允许展示内容与字段，列出必需输入限制、动作后果和错误帮助的依据；区分事实、推导、建议和未决项。按 `../SKILL.md` 的“内容准入”检查，不把接口字段或实现说明自动转成 UI 文案。
5. **Caveat / uncertainty**：保留会改变解释的条件、范围、单位、时间、对象和不确定性；缺口会改变展示范围或操作流程时向用户确认。
6. **Composition hypothesis**：先选信息拓扑，再选组件。结构性任务最多比较两种明显不同的构图假设，比较首屏路径、证据位置和密度，不只换颜色。
7. **Available primitives**：记录项目已有的布局、字体、token、组件、图标、图表和状态 API。没有适用 primitive 时，才定义页面自己的最小局部结构。
8. **States and responsive changes**：只写真实可达的 loading、empty、error、disabled、selected、permission、long-content 和窄屏变化。
9. **Verification target**：写明真实页面、目标视口、相关状态与交互路径，以及各项证据能证明什么；不用截图证明操作完成，不把自然度、表格数或文件行数设为硬门禁。

## 信息优先级

按以下顺序处理冲突：

1. 用户本轮明确要求和范围。
2. 需求、业务事实、接口状态和真实内容。
3. 项目 `AGENTS.md`、组件 API、设计系统和已有权威文档。
4. 已采纳的项目级设计合同和可回溯的设计决定。
5. 当前 Skill 的通用判断。
6. 一般界面经验。

合同不能发明业务状态、指标、品牌信息、用户意图、权限、结论或承诺。

## `DESIGN.md` 的边界

- 已有项目级 `DESIGN.md`：读取、遵循，并只在当前任务授权范围内修改。
- 没有项目级文件：先使用任务内合同，不自动创建长期文件。
- 只有用户明确要求交付设计规范，或已确认要维护一个反复使用的项目界面规范时，才建立或修订项目级文件。
- 项目级文件只保存该项目可复用的设计事实、判断、primitives、例外和来源；不复制全局 Skill，也不替代需求和组件 API。
- 多个项目存在不同品牌或技术栈时，分别维护各自合同，不建立一个跨项目的通用视觉 token 层。

## 完成前回读

- 读者能否用标题、首屏证据和主要动作说明页面要解决什么？次级任务是否有可发现的入口与合适的承载位置？
- 可见文案与字段是否处于允许范围，必需帮助是否保留，是否把合同或实现说明写进页面？
- 主结论、证据、限制和来源是否仍然对应真实材料？
- 构图是否服务读者任务，而不是套用页面类型模板？
- 组件和 token 是否来自项目事实，生成图是否被当成参考而非业务事实？
- 必要状态、窄屏行为、文案长度和验证目标是否已写清？
