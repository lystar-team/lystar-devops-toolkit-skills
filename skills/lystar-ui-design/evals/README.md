# UI design evals

## 目标

这里保存用于维护 `lystar-ui-design` 的固定场景定义和运行记录约定。它验证的是设计指导对第一次 UI 输出的影响，不替代项目测试，也不把完整 eval 变成普通 UI 任务的默认门禁。

## 最小起步方式

1. 选择获准的真实页面任务；场景数、模型调用和代理派发按本次授权，不因修改 Skill 默认启动评测。
2. 冻结 prompt、输入、起始代码、agent / model、生成配置、工具、视口、主题、状态和其他加载规则。
3. 修订已有 Skill 时，baseline 使用旧版本；对照设计合同本身时，baseline 使用原设计上下文。保存独立初始会话中的第一次交付，Harness 失败单独标记。
4. guided 使用候选规则或设计合同，其余条件不变；不向该会话提供 baseline 失败、人工改稿答案或评审结论。
5. 打乱两组产物，按 `references/review-rubric.md` 盲评；保留源码、截图和相关交互证据，截图不能替代运行路径。
6. 把有证据的纠正写成候选规则，选择最窄的落点，只重跑获准的受影响场景。
7. 保留未参与规则调节的 holdout，并检查必要帮助、多表对照、长流程与局部修改等反例；没有足够场景时记录为未验证。

先手工完成一次比较，再增加自动截图、确定性检查或模型 judge。工具只能加速已理解的判断，不能替代人工决定。

## 目录约定

```text
evals/
├── README.md
├── fixtures.schema.json
├── results.schema.json
└── scenarios/
    └── <scenario-id>/
        ├── fixture.json
        ├── baseline/
        ├── guided/
        └── review.md
```

`fixture.json` 描述固定任务和渲染条件；baseline / guided 保存第一次输出的证据；`review.md` 保存盲评和规则决定。当前没有真实项目产物时，不预填虚构场景、数据或截图。

## 运行范围

- 普通 `build` / `modify`：沿用 `lystar-ui-design` 的真实页面和目标视口验收。
- Skill 维护、重复失败验证或设计合同变更：需要行为验证时使用固定 scenario 和 rubric；规则文本与引用检查不算行为评测。
- 不默认执行 E2E、跨平台完整测试、全仓构建或大规模多模型 round；E2E 授权遵循 `yean-develop-style` 的“验证”一节。
- 确定性检查只能报告它能够稳定识别的失败；主观构图、读者任务和证据解释保留人工评审。

## 证据记录

每次运行至少能回指：scenario、prompt、输入、生成配置、视口、主题、guidance version、源码、截图、相关交互证据、已知失败、reviewer 和决定。实际加载文件与版本、未执行项记录在既有 `review.md` 中，区分未加载、规则缺口与未遵循，不为记录扩展 Schema。

`../../yean-develop-style/references/trigger-tests.md` 提供预期行为样例，可用于选择问题类型；它们不是运行结果，也不能作为未见过的泛化测试。未实际执行的运行不要写入结果目录，不在交付中写成已验证。
