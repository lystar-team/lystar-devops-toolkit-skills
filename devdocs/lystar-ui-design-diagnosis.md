# lystar-ui-design 不好用的根因分析

Yean，这个 Skill 不是某一环坏了，是定位写错了。它写成了一套评审治理框架，而不是一个能帮你干活的执行 Skill。下面是依据和结论。

## 1. 先看事实

Skill 本体很小，治理附件很大：

- `skills/lystar-ui-design/SKILL.md` 197 行，其中能直接指导写代码的只有「映射到实现」一节，而那一节几乎全是「去读别的 Skill」。
- `references/` 四个文件 321 行：`design-contract.md`（72 行，15 字段合同模板）、`imagegen-ui.md`（50 行，生图提示词）、`review-rubric.md`（91 行，双会话盲评流程）、`rule-catalog.md`（108 行，规则采纳流程）。
- `evals/` 下只有 `README.md` + 两个 schema，`scenarios/` 目录根本不存在，一条真实验证记录都没有。
- 安装侧：`requirements/` 下没有它的依赖文件，`bin/` 下没有它的命令，`docs/packages/lystar-ui-design.md` 自己写「不增加 PATH 命令；图像生成和页面检查使用当前 Harness 已提供能力」。即零工具抓手。

真正干活的步骤全部外包：

| 环节 | 实际接手者 | ui-design 自己留下什么 |
|---|---|---|
| 组件实现、a11y、响应式、状态 | `frontend-ui-engineering`（有完整代码示例） | 一句话「按需读取」 |
| 文案怎么写 | `shuorenhua` | 「内容准入由本 Skill 判断」，但判断完还是交给它写 |
| 页面截图、交互验证 | `agent-browser` | 「使用它」，无自己的检查手段 |
| 公开页抓取 | `lystar-web-restore` | 「按需使用」 |
| 强视觉方向 | `design-taste-frontend` | 「先读取它」 |
| 外部规范审查 | `web-design-guidelines` | 「需要时读取」 |
| 拆分、验证、E2E 边界 | `yean-develop-style` | 「遵循它」 |
| 生图 | `imagegen` | 写简报、调它、回读 |

一个 UI 任务按它的写法要加载 5～7 个 Skill，每个又有自己的 contract/rubric。上下文先爆炸，Agent 大概率只读第一个，行为退化成凭感觉写，Skill 等于没加载。

## 2. 根因一：描述和路由把什么都往里装

`lystar-ui-design` 的 description 是「设计、修改、视觉生成、代码还原与评审总入口」，范围 cover 了至少四个邻居：

- `frontend-ui-engineering`：「Builds production-quality … user-facing UIs」
- `lystar-ui-restore`：「证据驱动的 UI 视觉还原与验收」
- `web-design-guidelines`：「review my UI / audit design」
- `anti-ai-slop`：「页面文案与 UI 复核」

`AGENTS.md` 路由只有一句话「可见 UI …：lystar-ui-design」，没写这五个什么时候用哪个。Harness 靠 description 做隐式路由，`agents/openai.yaml` 还开了 `allow_implicit_invocation: true`，结果必然是：简单调个间距也被拉起整套合同+生图简报；真要做还原时不知道走 ui-design 还是 ui-restore。误触发和漏触发同时存在。

## 3. 根因二：生图链路成本高、收益低

`build` / `major-redesign` 默认要走：写合同 → 写生图简报 → 调 imagegen → 回读 → 提取视觉合同 → 再写代码，至少 5 步才碰代码。

但 Skill 自己又规定：生成图里文字尽量少；文案、数据、状态、权限、业务动作一律不信图；虚构指标功能导航一律不实现；UI Kit 图还要再「落实为项目原生 token」。那这张图到底决定什么？只剩构图、密度、颜色。这些用 CSS/token 直接试更快。

而「默认不生成」的四种例外（局部调整、已有稿、只读 review、生成图不改变判断）恰恰是日常 80% 的任务。等于日常任务走完合同就结束，没有增量价值；重任务又被生图链路拖慢。这就是你「没起实质作用」体感的主要来源。

## 4. 根因三：合同重、验证零

- 15 字段合同 + 6 层优先级 + 6 问回读。局部修改说「不填完整合同」，但「结构性还是局部」这个判断本身又要读合同，先有鸡还是先有蛋。
- `rule-catalog.md` 自己承认：两条核心规则 `ui/content/task-relevance`、`ui/composition/task-boundaries` 状态都是 `existing`，但 `Last validated: unvalidated`，Evidence 写「未复现历史页面，未运行模型对照」。
- `review-rubric.md` 要求 baseline/guided 双隔离会话 + 打乱盲评 + holdout，普通 UI 任务永远不会跑。治理框架先行，证据为零，自己写了自己做不到的流程。

## 5. 根因四：好判断没有配好代码

「内容准入」「任务与承载位置」（Dialog vs Drawer vs Tabs vs 主从）这两节是全 Skill 最有价值的部分，但只有表格和「不要」，没有一段能抄的 props/路由/状态归属示例。对比 `frontend-ui-engineering` 里现成的 `TaskListContainer` / `EmptyState` / `Skeleton`，Agent 有样学样；ui-design 没给样子，Agent 学不会。

## 6. 实质性结论

一句话：**ui-design 把自己写成了「总入口+评审委员会」，但执行层是真空。**

- 日常小改：合同太重，生图不触发，读完等于没读。
- 新建大改：链路太长，生图不可信，代码还要去别的 Skill 找。
- 评审任务：rubric 太重，一次都跑不起来。
- 路由层面：和四个邻居职责重叠，没有分工说明，误触发是必然的。

所以修法不是加更多规则，是收缩和下沉。

## 7. 整改建议（按顺序做）

P0，改两处文字就能止血：

1. 收窄 description，去掉「代码还原与评审总入口」。改成只做三件事：任务/主次判断、内容准入、承载位置选择。还原明确交给 `lystar-ui-restore`，实现明确交给 `frontend-ui-engineering`，复核明确交给 `anti-ai-slop`。同步改 `AGENTS.md` 路由，加一句话分工。
2. 生图从「默认生成」改「按需生成」：只有新建 Landing/品牌页且用户无稿时才生成；后台、Dashboard、表单、表格默认不生成，直接给布局代码。`openai.yaml` 的隐式调用至少排除 `modify` / `review`。

P1，让 Skill 能抄：

3. 每个承载位置配一段最小代码正例（Dialog 短操作、Drawer 轻编辑、Tabs 互斥视图、主从对照、空状态），复用项目已有组件，不发明新 API。
4. 合同压成短版：`build` 填 7 字段（Scope / Reader job / Primary / Secondary+位置 / Allowed fields / States+窄屏 / Verification），局部修改只填 3 字段（范围 / 字段变化 / 验证目标）。

P2，补一次真实验证再谈规则：

5. 在 `evals/scenarios/` 下先建 1 个真实场景（建议用你反复抱怨过的「多余 description」和「列表+编辑堆一页」两类），手工跑通一次 baseline/guided 盲评，再决定 `task-relevance` / `task-boundaries` 是否进 `accepted`。没跑通之前不新增任何全局规则。

## 8. 验收标准

- 简单改样式任务不再触发合同+生图简报。
- 新建后台页不经过生图也能拿到布局代码。
- 还原类任务明确走到 `lystar-ui-restore` 而不是 ui-design。
- `evals/scenarios/` 下有 1 个带源码+截图+review.md 的真实记录。

## 附：本次核查用到的位置

- `skills/lystar-ui-design/SKILL.md`、「映射到实现」「视觉生成」「评估与维护」节
- `skills/lystar-ui-design/references/design-contract.md`、`rule-catalog.md`（`Last validated: unvalidated`）
- `skills/lystar-ui-design/evals/README.md`（`scenarios/` 缺失）
- `skills/lystar-ui-design/agents/openai.yaml`（`allow_implicit_invocation: true`）
- `~/.agents/skills/frontend-ui-engineering/SKILL.md`（有代码正例，可对比）
- `install-lystar-ui-design.sh`、`docs/packages/lystar-ui-design.md`、`requirements/`（无工具抓手）
- 本轮未改任何 Skill 源码，只输出本报告。动手改之前先跟你对一遍第 7 节的范围。
