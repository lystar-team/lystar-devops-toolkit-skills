# lystar-ui-design 实际执行测试与规则审计报告

- 测试目录：/tmp/lystar-ui-design-eval-20261003（全部产物、证据、方案只写在此目录；未修改仓库、已安装 Skill、全局配置或生产数据）
- 执行方式：单执行会话（模式：执行，不派发下级会话）；本报告的页面回读由本会话自检，不是独立盲评
- 指导版本：已安装 /home/yean/.agents/skills/lystar-ui-design，VERSION 0.4.2；实际读取内容快照见 guidance/NOTES.md
- Skill 测试日期：2026-10-03
- 报告版本：本报告含两轮。第一轮为初始完整测试（冻结稿 report-before-followup.md）；第二轮为暴露问题后的定点复测（见第 10 节）。两轮都只写入本测试目录。

## 0. 结论摘要

- 两个实现请求均已完成并可运行：PC 调拨核对（desktop/）、微信语境图书预约（mobile/）。两套首次完整实现分别保存在 desktop/first/ 与 mobile/first/，未被覆盖；后续修正只在工作副本，全部留了差异记录。
- 测试中发现并修正 3 类实现缺陷：桌面首版隐藏表单未生效（F-01，冻结前修正）、桌面辅助文字对比度不足（F-02）、移动端确认面板焦点管理（F-M01，含一次 DOM 顺序回退修正）。均保留首次证据、定点修正并复测。
- 三个附加请求按序完成：培训报名长流程只出方案；取消按钮改返回只改一处并用 diff、盒子尺寸与行为核验证明范围；摄影阅读方案如实说明未提供图片与字体的资产缺口，未声称查看图像或运行页面。
- 规则审计在本次范围内未发现文档之间的直接冲突；53 个相对引用全部可解析，15 张参考图与 sources.json 记录一致，独立安装包含 47 个技能文件且校验通过，两个 fixture 的必要约束通过。审计发现以“注意项”为主，见第 6 节。
- 不能由本次测试得出的结论：真实微信/真机通过、真实接口行为、稳定性和效率提升、资深设计师级别。浏览器原型和本地模拟只证明本轮覆盖。

## 1. 运行方式、入口与产物

本地静态服务（空闲端口 8801，仅监听 127.0.0.1）：

```bash
cd /tmp/lystar-ui-design-eval-20261003
python3 -m http.server 8801 --bind 127.0.0.1
```

浏览器会话（agent-browser 0.34.0，Chrome；两个独立命名 session）：

```bash
agent-browser --session pi-ui-eval-desktop-01 set viewport 1440 1000
agent-browser --session pi-ui-eval-desktop-01 open http://127.0.0.1:8801/desktop/index.html

agent-browser --session pi-ui-eval-mobile-01 set viewport 390 844
agent-browser --session pi-ui-eval-mobile-01 open http://127.0.0.1:8801/mobile/index.html
```

失败/延迟模拟（测试适配器，不在产品页面引用；通过页面正常数据入口操作，未改 DOM 造通过）：

```bash
# 页面地址带参数：desktop/index.html?save=fail-once|fail-always|slow
# mobile/index.html?reserve=fail-once|fail-always|slow&cancel=fail-once|slow
agent-browser --session <SESSION> eval --stdin <<'EOF'
(async () => {
  await new Promise((res, rej) => {
    const s = document.createElement('script');
    s.src = 'test-adapter.js'; s.onload = res; s.onerror = rej;
    document.head.appendChild(s);
  });
  return window.__testAdapter.mode || window.__testAdapter.reserveMode;
})()
EOF
```

产物与首次/最终路径：

| 项 | 首次完整实现 | 当前可运行版本 | 首次后修改 |
| --- | --- | --- | --- |
| PC 调拨核对 | desktop/first/ | desktop/ | styles.css 对比度 token；index.html 取消→返回（附加请求二） |
| 微信语境图书预约 | mobile/first/ | mobile/ | app.js 与 styles.css 的确认面板焦点管理 |

| 入口 | 地址 |
| --- | --- |
| 桌面原型 | http://127.0.0.1:8801/desktop/index.html |
| 移动原型 | http://127.0.0.1:8801/mobile/index.html |
| 浏览器证据截图 | evidence/desktop/、evidence/mobile/、evidence/additional/ |
| 交互原始记录（JSON 输出） | evidence/desktop/interaction-log.md、evidence/mobile/interaction-log.md |
| 修正记录 | desktop/FIXES.md、mobile/FIXES.md、evidence/additional/B-local-change.md |
| 指导快照 | guidance/NOTES.md 与 guidance/ 下的副本 |

测试环境备注（事实）：修改文件后首次打开时 Chrome 命中本地缓存，仍显示旧文案；改用带查询参数的地址重新加载后确认（见 B-local-change.md）。属本地静态服务缓存现象，不是页面缺陷。

## 2. 实际读取清单（详见 guidance/NOTES.md）

- 执行前读取：SKILL.md；references/reference-map.md、domestic-ui-examples.md、china-product-baseline.md、mobile-product-examples.md（只读文字）、design-examples.md、anti-noise.md、design-contract.md。
- 执行时实际查看的参考原图 5 张：ant-query.png、arco-query.png（桌面），tdesign-publish.png、tdesign-orders.png、tdesign-order-detail.png（移动）。未查看该库其余 10 张与本任务无关的产品画面。
- 审计阶段补充读取：imagegen-ui.md、review-rubric.md、rule-catalog.md、evals/README.md、两个 schema、两个场景 fixture、巡检场景的 review.md/checks.json/result.json、examples/sources.json、examples/index.html、agents/openai.yaml、VERSION。
- guidance/ 快照与安装目录当前文件 md5 一致；VERSION 0.4.2 只是包标签，实际加载内容以快照为准。

## 3. PC 调拨核对（desktop/）

### 3.1 任务到结构

进入原因：园区物料管理员核对一张具体调拨单（待核对），逐项比较调出与调入记录，并查看或编辑调拨说明。

| 区域 | 服务的用户问题 | 默认信息与动作 |
| --- | --- | --- |
| 页头 | 这是哪张调拨单、哪个方向 | 单号、仓库路线（调出→调入，带角色标签）、创建时间、经办人、状态 |
| 明细区 | 每项物料是否一致 | 按物料 ID 匹配的对照表：物料/规格、单位、调出数量、调入数量、差异（调入减调出）；表上方查找框与“共 5 项，3 项数量有差异” |
| 说明区 | 差异原因如何记录 | 查看态显示已保存说明；编辑态可改并保存；1200px 以上为右侧栏，窄于 1200px 堆叠到表格下方 |

设计判断（非业务事实）：将说明放在右侧栏，使核对与书写可同时进行；未添加采购、财务、审批或库存动作（规则明确本轮不做）；未做跨单位总量合计，只对条目计数。

### 3.2 视觉决定与参考迁移

- 方向：中性纸面 + 墨色主操作 + 琥珀色差异提示；不套用主色品牌。差异用文字（调入短少/多出/相符）加色点，不用彩色侧边条或徽章。
- 迁移关系：ant-query.png / arco-query.png → 筛选与结果分区、数字与单位对齐、状态文字加色点；未采用演示数据、整套皮肤、重复标题、装饰背景与浮钮（见 guidance/NOTES.md）。
- 字体为系统中文字体；数字使用 tabular-nums；长名称允许换行，行高随内容增长。

自检观察（据运行截图，不是独立评审）：信息角色为调拨单身份（页头）> 逐项对照（表）> 差异原因（说明）；表内数量与差异用右对齐和字重承担主次，规格与编号降为辅助灰；构图为对照表为主体、说明为右侧栏，1440 下比例约 7:3，1024 下上下堆叠；排版使用 20/16/15/14/13/12px 的角色分级；行高由内容决定（单行约 62px，M102 两行 76px）；细节上圆角、边框、状态点与焦点环在各区一致。保留的首版截图见 D1/D2。

### 3.3 首次输出与修正

| 编号 | 位置/输入状态 | 观察 | 用户后果 | 依据 | 最小修法 | 回查 |
| --- | --- | --- | --- | --- | --- | --- |
| F-01 | 说明区查看态（first 冻结前） | 编辑表单与查看文本同时显示 | 同一内容重复，用户无法判断当前状态 | evidence/desktop/desktop-smoke-1440.png（首次运行画面） | `[hidden] { display: none !important; }` 一处 CSS | 冻结 first/ 后复查，查看态只显示文本 |
| F-02 | 物料编号、仓库角色、页头字段名、字数计数（12–13px 辅助文字） | 对白底 3.6:1、对灰底 3.3:1，低于 4.5:1 | 低视力或弱光下辅助信息可读性差 | evidence/desktop/interaction-log.md 的 D13 测量 | `--ink-3` 改为 #626d7a（白底 5.27:1、灰底 4.82:1） | 复测四个元素比值 4.82–5.27 |

### 3.4 测试结果（first 版 + 上述修正）

| 组 | 输入/状态 | 观察 | 证据 |
| --- | --- | --- | --- |
| D1/D3 | 1440×1000 默认 | 5 行数据与差异 −2/0/−3/0/−100、单位与数量正确；“共 5 项物料，其中 3 项数量有差异”；无横向溢出 | D1-first-1440.png；interaction-log D1 JSON |
| D2/D4 | 1024×800 默认；M102 长名称与长规格 | 页面重排为单列、说明区在表格下方；长名称/规格/说明无裁切、无横向溢出；M102 行高 76px | D2-first-1024.png；interaction-log D2/D4 JSON |
| D5 | 搜索“电缆”“RS485”“M10”“打印机” | 分别命中 1（M105）、1（M102）、5、0 条；空结果给出查询词与“清除搜索”，清除后恢复 5 行且焦点回输入框；“清除”链接状态正确 | D5-empty-search.png；interaction-log D5/D5b |
| D6 | 编辑说明后点“取消” | 表单关闭，说明视图与 store 内容保持原文，焦点回“编辑说明”（当时按钮文案为“取消”，附加请求二后为“返回”，行为一致） | interaction-log D6 |
| D7 | 编辑说明后保存 | “保存中…”后成功；视图显示保存后内容，反馈“已保存 13:15”，store 一致，焦点回“编辑说明” | D7-save-success.png；interaction-log D7 |
| D8 | slow 适配器：点保存后快速再点两次 | 提交中按钮禁用、取消禁用；重复点击不产生第二次保存（saveCalls=1） | interaction-log D8 |
| D9 | fail-once 适配器：保存失败后重试 | 失败时输入保留、显示“保存失败，内容未丢失，请重试。”、按钮变“重试保存”；重试成功且内容只出现一次（saveCalls=2） | D9-save-failed.png；interaction-log D9 |
| D10 | fail-always：连续失败后取消 | 内容持续保留；取消后视图与 store 仍为原保存内容，草稿不落库 | interaction-log D10 |
| D11 | 输入 1200 字 | 输入被限制为 1000 字，计数“1000 / 1000”；文本域高度封顶 338px、内部滚动可用 | D11-long-note.png；interaction-log D11 |
| D12 | 结构/可访问性 | 搜索框有可访问名称、表格有 caption、反馈 aria-live=polite、错误 role=alert；页面无“总计/合计/总量”和 185/180 等跨单位合计 | interaction-log D12 |
| D13 | 对比度测量 | 修复前 3.3–3.6:1，修复后 4.82–5.27:1 | interaction-log D13 |

### 3.5 限制

- 说明保存在内存中（本地模拟），刷新即回到给定初始值；本轮未要求持久化。
- 未做 390 宽等手机视口的桌面页检查（任务只要求 1440×1000 与 1024×800）。

## 4. 微信语境图书预约（mobile/）

### 4.1 任务到结构

进入原因：读者找书、判断能否预约并提交；需要时查看已有预约与取书状态、取消尚未结束的预约。

| 页面/区域 | 服务的用户问题 | 默认信息与动作 | 进入与返回 |
| --- | --- | --- | --- |
| 找书（tab） | 有哪些书、我能不能约 | 搜索书名或作者；书目行显示标题、作者、馆藏位置、可约册数；被自己的待取预约挡住时显示“已有待取” | 点行进入详情；返回保留搜索词与滚动位置 |
| 书目详情 | 这本书的完整条件 | 分类、可约册数、馆藏位置、取书地点、取书期限规则；不满足条件时显示原因并禁用动作 | 确认面板提交；成功后显示成功提示与取书期限 |
| 我的预约（tab） | 我有哪些预约、下一步做什么 | 读者身份；待取书组显示编号、取书地点、期限、预约时间与取消；已结束组显示已取书/已取消与原取书期限；待取书为空时给出说明 | 取消走确认面板 |
| 微信导航（模拟） | 返回与页面身份 | 顶栏标题、返回箭头（详情页）、右侧胶囊；底部两个 tab 与待取角标 | 均为浏览器模拟，不是微信运行 |

设计判断：新预约只允许 available>0 且没有该书 ready 预约（B02 因此被挡）；取消只对 ready 开放，已取书/已取消无取消入口；取书期限按“提交日期 +2 天 18:00”在提交时计算；读者已有已取书记录的同一本书（B04）仍可再约——这直接来自给定规则，未加额外限制。

### 4.2 视觉决定与参考迁移

- 方向：暖纸白底 + 墨绿主操作 + 琥珀待取提示；状态用文字加色点；待取角标用真实数量。
- 迁移关系：tdesign-publish.png → 输入顺序、长文字增长、底部操作的权重；tdesign-orders.png / tdesign-order-detail.png → 列表保留对象摘要与当前动作、详情展开依据、返回保留列表上下文；未采用零售金额、优惠、红色主题、示例地址与图片像素尺寸。china-product-baseline.md 用于小程序返回、安全区与操作区约束。

自检观察（据运行截图，不是独立评审）：信息角色为书目标题 > 可约/状态 > 作者与馆藏位置；待取期限在预约卡中用琥珀色强调，已结束项降为中性；构图为单列列表与单列详情，长内容自然增长，底部固定动作避开安全区；排版使用 17/15/14/13/12/11px 分级；触控目标为书目行 ≥95px、面板按钮 46px、tab 54px；列表、详情、面板与预约卡的圆角、间距（12px 页边距）、状态点颜色保持一致。保留的首版截图见 M-smoke/M1。

### 4.3 首次输出与修正

| 编号 | 位置/输入状态 | 观察 | 用户后果 | 依据 | 最小修法 | 回查 |
| --- | --- | --- | --- | --- | --- | --- |
| F-M01 | 确认面板打开、Escape、Tab | 焦点仍在背后的触发按钮；Escape 不关闭；Tab 会离开面板进入背景 | 键盘与辅助技术用户可能操作到被遮挡内容，也无法用键盘退出 | evidence/mobile/interaction-log.md 的 M10 | 打开聚焦主按钮、关闭回触发元素、Escape 关闭、面板内 Tab 循环、成功后焦点到成功提示或主区域 | 复测 M10b/M10c/M10d：焦点进入、循环、Escape 关闭并回到触发元素；预约成功焦点在“查看我的预约”，取消成功焦点在主区域 |
| F-M02（第二轮暴露） | 移动端书目详情，存在本人 ready 预约（B02；B01 预约成功后同状态） | 被自己的待取预约挡住时仍显示新建预约的通用期限，且没有关联预约入口 | 用户看不到实际取书期限，不知去哪里查看已有预约 | 已安装 SKILL 第 1 节新增段落；parent-review/ 复核 | 期限读取当前记录 pickupUntil；notice 增加“查看该预约”入口；无本人记录时不加入口 | 第 10 节 T1–T6；mobile/FIXES.md |

F-M01 的第一次修复把面板内顺序写成“主按钮在前”，实测 Tab 从主按钮走到背景；核对 DOM 实际顺序（次要按钮在前）后交换循环首尾变量，复测通过。该回退过程保留在 M10b 与 M10c 记录中。

### 4.4 测试结果（first 版 + F-M01）

| 组 | 输入/状态 | 观察 | 证据 |
| --- | --- | --- | --- |
| M1 | 390×844、360×800 默认 | 无横向溢出；B02 长标题两行、长馆藏位置两行不裁切；tab 与角标“1”正确 | M1-first-390.png、M1-first-360.png；interaction-log M1 |
| M2 | 搜索“建筑”“沈一衡”“历史” | 分别按书名、作者命中 1 条；无结果时给出查询词与“清除搜索”，清除后 6 条、焦点回输入框 | M2-empty-search.png；interaction-log M2 |
| M3/M3d | 搜索“植物”后进入详情再返回 | 返回后仍为搜索态（1 条）；另测无搜索时滚动 45px 后进入详情，返回滚动恢复 | interaction-log M3/M3b/M3c/M3d |
| M4 | B02 详情 | 显示“你已有这本书的待取预约，取书后可再次预约。”，动作禁用为“暂不可预约” | M4-blocked-b02.png；interaction-log M4 |
| M5 | B01 预约成功 | 确认面板显示取书地点与“10月5日 18:00 前”（提交日 +2 天 18:00）；成功后提示、期限与取书地点、可约 3→2、B01 转为“已有待取”、新预约 YY20261003002 进入待取书组、角标 2 | M5-reserve-sheet.png、M5-reserve-success.png、M5-reservations.png；interaction-log M5 |
| M6 | slow 适配器：提交中双击与点“再想想” | 按钮“提交中…”禁用；额外点击不产生第二次创建（reserveCalls=1，B01 只有 1 条新预约） | M6-submitting.png；interaction-log M6 |
| M7 | fail-once：预约失败后重试 | 面板保留书名与条件、显示“提交失败，请重试。”、按钮变“重试预约”；重试成功且只创建一次（可用数 3→2，B01 预约数 1） | M7-reserve-failed.png；interaction-log M7 |
| M8 | B02 取消：先“再想想”，再确认 | 放弃不改变状态（available 1、ready）；确认后 available 1→2、状态 cancelled；待取书组显示空说明；角标消失；B02 详情恢复可约、列表行“可约 2 册”且无“已有待取” | M8-cancel-sheet.png、M8-after-cancel.png；interaction-log M8/M8b |
| M9 | cancel fail-once：取消失败后重试 | 失败保留面板与上下文、状态不变；重试成功且 cancelled 只 1 条 | interaction-log M9 |
| M10 | B03 无可约、B04 已有已取书记录 | B03 显示原因并禁用；B04 可约（规则只挡 ready 预约） | interaction-log M10 |
| M11 | 对比度与触控尺寸 | 辅助文字 4.53–5.54:1；书目行高 ≥95px、tab 54px、搜索框 40px、取消按钮 36×86、返回 40×40、面板按钮 46×159；找书页文本扫描未发现禁用词（登录/推荐/费用/逾期/罚款/自动保存/分享） | interaction-log M11、M12、M12b |
| M12 | 360 宽详情与面板 | 标题两行、长馆藏位置两行、面板 360 宽无溢出 | M12-sheet-360.png（B02 禁用态尝试）、M12b-sheet-360.png |

### 4.5 限制

- 浏览器原型不证明微信运行或真机通过；顶栏胶囊是浏览器模拟。
- 未接微信 API、登录、真实接口；可约数与预约由本地状态读写。软键盘遮挡未验证。

## 5. 附加请求

### 5.1 培训报名长流程（只出方案）

产物：additional/plan-training-registration.md。内容：五阶段结构（选择课程 → 资格核验 → 填写与附件 → 负责人确认 → 最终提交）、阶段导航与返回继续路径、各阶段状态（加载/空/失败、资格四态、附件状态、确认四态、提交四态）、视觉决定（阶段用文字位置表达、表单宽度、附件限制写在输入处、手机单列）、接口待确认项（资格规则、附件上下限、负责人机制、提交前置条件）。按请求未新增自动保存：离开未保存即丢弃，取消时明确告知。→ 定点复测轮按新版 SKILL 第 1 节校准了“保存能力”的待确认依据（保存机制标为方案判断、待项目确认），见第 10 节与 additional/plan-training-registration.md。

### 5.2 局部修改：取消 → 返回

产物：evidence/additional/B-local-change.md 与 B-before/（修改前基线）、B-after-return-label.png。diff -r 证明只有 desktop/index.html 第 92 行文案从“取消”变为“返回”，styles.css、app.js、test-adapter.js 无差异。运行核验：按钮 62×36 位于 x=1173,y=397，与修改前一致；保存按钮 90×36、操作行 291×36 一致；点击“返回”后表单关闭、说明与 store 保持原文、焦点回“编辑说明”。观察项（按请求未改行为）：按钮语义变为“返回”，但仍会丢弃未保存草稿，文案与行为存在理解落差。

### 5.3 摄影作品阅读页（只出方案）

产物：additional/plan-photography-reading.md。明确资产证据缺口：请求所述真实图片与品牌字体未提供，未查看任何作品图像、未运行页面。可确定部分：主题→作品→详情的阅读结构、返回保留位置、状态集（加载/失败/空主题/长名称）与验证计划；素材相关部分（首屏画面、裁切比例、色彩、字体参数、加载策略）保持待定并列出所需材料。→ 定点复测轮把本方案收窄为“本次素材缺口”说明，删除未验证的结构与视觉决定，见第 10 节与 additional/plan-photography-reading.md。

## 6. 规则审计

审计范围：SKILL.md、7 份 references、evals 文档与 schema、两个 fixture、examples 入口与来源记录、agents 与安装/打包链路。父会话已执行的 quick_validate、引用/元数据/15 图尺寸检查、tests/test_install.sh、tests/test_packages.sh 未重跑；本节补的是行为与内容核对。

### 6.1 已核对项与结果

| 检查项 | 方法/证据 | 结果 |
| --- | --- | --- |
| 核心与参考一致性（装饰边界、参考成本、局部范围、长流程归属） | 通读 SKILL 与 anti-noise、design-examples、design-contract、review-rubric 对照 | 无冲突；anti-noise 明确把拒绝清单的最终依据指回核心 |
| 参考与工具路由 | reference-map 表格 ↔ domestic-ui-examples、mobile-product-examples 的案例 ID；53 个相对链接解析 | 路由项全部有对应案例；0 断链 |
| 局部/方案/评审范围 | SKILL 范围与执行方式 ↔ design-examples 局部修改段 ↔ design-contract 局部段 ↔ review-rubric 局部限制 | 三处口径一致：局部只查受影响区域，方案不擅自实现，评审只给问题与修法 |
| 条件例外 | imagegen、anti-noise、rule-catalog、review-rubric、evals README 的读取条件 | 都是“需要时读取”，未出现必须常驻读取的表述；与本次按需选择一致 |
| 参考资源真实性 | 15 张图片存在性 + sources.json 尺寸逐项比对；本次实际查看的 5 张 | 15/15 存在且尺寸一致；5 张已实际查看原图 |
| 来源与限制声明 | sources.json 的类型/核验日期/限制 ↔ 两份参考文档的“证据/限制”段 | 一致（如 tdesign 为官方模板截图、宇舶为设计方项目图、未做真机） |
| eval/schema 一致性 | 两个 fixture 对照 fixtures.schema.json 的必要约束；巡检 result.json 对照 results.schema.json 字段 | fixture 必要约束通过；巡检记录为历史 single-guided 自评，表述与 schema 的 variant/decision 取值一致 |
| 独立安装可用性 | install-lystar-ui-design.sh、install-lib.sh 复制逻辑、dist/lystar-ui-design.zip 内容与 SHA256、tests 覆盖读取 | zip 内 47 个技能文件与仓库一致，包含安装机制，校验通过；测试覆盖临时 HOME 安装 SKILL.md 与 agents/openai.yaml |
| VERSION 来源 | install-lib.sh 第 293 行从包根写入技能目录 | 技能源码目录无 VERSION；0.4.2 为包级版本，快照哈希才是内容依据 |
| 未验证声明排查 | review.md/reference 文档逐段标注 | 文档把截图类型、未做真机/未做旧版对照写清；未发现把未执行写成通过 |

### 6.2 审计发现（位置、输入/状态、观察、后果、依据、最小修法）

| 编号 | 位置 | 输入/状态 | 观察 | 用户后果 | 依据 | 最小修法 |
| --- | --- | --- | --- | --- | --- | --- |
| A1（注意项） | references/reference-map.md 路由表 | 移动端服务类检索与预约（搜索→判断→提交→记录取消） | 表中有“小程序输入、提交与安全区”和“移动记录、详情与返回”两行，但没有直接覆盖“服务类检索与预约”的组合行 | 执行者需要自行组合两行与平台基线；本轮组合可行，未造成错误路由 | reference-map.md 表格；本轮 mobile 执行记录 | 如需，可增加一行“移动服务检索与状态操作”，引用 tdesign-order 与平台基线；非必须 |
| A2（注意项） | references/domestic-ui-examples.md 的实测数值 | 14 CSS px / 47 px / 43 px / 32 px | 数值来自更早的原站捕获，本轮未再访问原站复核 | 无直接用户后果；依赖这些数值的人需知道它们绑定当时版本 | 文档自身已声明“这个版本的测量值”“目标项目以既有 token 为准” | 保持现状即可；引用时带版本与日期 |
| A3（注意项） | eval fixture ui-cn-stock-transfer | 视口为 desktop 1440×1000 + mobile 390×844 | 本轮任务只要求桌面 1440×1000 与 1024×800，桌面原型未测 390 | 该 fixture 未来执行时，390 宽路径尚未验证 | fixture.json 与本轮 inputs.json 差异 | 若执行该 fixture，补 390 宽检查；本轮不扩范围 |
| A4（注意项） | 数量表述 | “原六组/十组十五张”与 sources.json assets 计数 | 6 组 8 张 + 4 组 7 张 = 10 组 15 张，一致 | 无 | 脚本核对 | 无 |
| A5（事实） | 本轮执行差异 | 任务要求“普通原型生成按核心选择相关参考及实际原图，不默认读取所有资源” | 实际读取 7 份参考文档、查看 5 张图；与 SKILL 的按缺口读取口径一致 | 无 | guidance/NOTES.md | 无 |

本轮未发现：文档间的规则冲突、把可选能力写成前提、把未执行写成通过、参考资源与记录不符。以上均为本轮审计范围结论，不是对全部文档的稳定性结论。

### 6.3 未复现/无法在本轮验证

- 文档中记录的 Ant/Arco 原站交互与像素测量：本轮未访问原站，未复现。
- 巡检场景的 26 项历史检查与截图：只读取记录，未重跑页面。
- 站点级键盘流程、屏幕阅读器与 axe 全面审计、软键盘：未执行。

## 7. 未验证项与限制（总）

1. 未在微信开发者工具或真机运行；浏览器原型不证明微信运行、真机布局与授权行为。
2. 未接真实接口、真实上传或真实保存服务；失败与延迟由测试适配器模拟，数据为本地内存。
3. 未做屏幕阅读器/axe 全面检查；只做了可访问名称、角色、aria-live、错误角色、对比度、焦点路径与 Tab 循环。
4. 未测试软键盘遮挡、触控手势与真机滚动。
5. 桌面原型未测 390 宽；移动原型未测桌面宽（任务未要求）。
6. 本次是单执行会话自检，不是独立盲评；不能声明稳定、效率提升或资深设计师级别结论。
7. 附加请求中的两个方案未实现、未运行；摄影方案没有素材，无法核对其视觉决定。

## 8. 修正成本与工具次数可得信息

- 修正项：3 类（F-01、F-02、F-M01），其中 F-M01 含一次基于 DOM 实际顺序的回退修正；共 5 次编辑操作，涉及 3 个文件（desktop/styles.css、mobile/app.js、mobile/styles.css），另有附加请求二的 1 处文案改动（desktop/index.html）。没有重做整页，没有新增依赖或测试框架。
- 测试组：桌面 13 组、移动 13 组、附加请求 2 份方案与 1 组局部修改核验；每组约 3–8 条浏览器命令。精确的工具调用次数未逐条记录，量级为百次左右；本报告不据此推断效率。
- 证据数量：截图 28 张（desktop 11、mobile 16、additional 1）、交互原始记录 2 份、审计检查 1 份、方案 2 份、修正记录 3 份。

## 9. 证据索引

- guidance/NOTES.md：读取清单、快照一致性、参考迁移与非采用部分。
- evidence/desktop/interaction-log.md：D1–D13 原始输出。
- evidence/mobile/interaction-log.md：M1–M12b 原始输出。
- evidence/audit/audit-checks.md：链接、图片、压缩包、fixture 检查结果。
- evidence/additional/B-local-change.md：diff、盒子尺寸、行为核验、缓存备注。
- desktop/FIXES.md、mobile/FIXES.md：首次之后每个修正的说明与回查。
- additional/plan-training-registration.md、additional/plan-photography-reading.md：两个只出方案的交付（已在第二轮校准，见第 10 节）。

## 10. 定点复测（暴露问题后，第二轮）

### 10.1 触发与依据

- 已安装 SKILL.md 第 1 节新增两段，实际读取内容保存于 guidance-followup/SKILL.md 与 guidance-followup/NOTES.md：
  - “区分用户需要的 UX 能力与系统已经具备的机制：阶段保存、持久化和返回继续必须有项目依据；提出新机制时标为方案判断或待确认，不能因为流程需要就声称系统已有。”
  - “先按当前状态选择有意义的下一步，再决定按钮，而不是固定一个新建/编辑动作，条件不符时只禁用并加提示。有关联的已有记录、未完成工作或可恢复步骤时，在该对象上下文提供相应入口；不让用户退出后重新查找。没有合法下一步就呈现结果或等待条件，不制造动作。涉及期限、资格或结果时读取当前记录，不能套新建流程的通用说明。”
- 父会话复核材料：parent-review/ 下有 axe 证据（desktop/mobile 均为 0 violations）、桌面首版写入来源记录（desktop-first-provenance.json）与最初写入副本（original-desktop/）。本轮只读取，未修改这些文件。

### 10.2 复测边界（三类，互不替代）

1. **初始完整测试（第一轮）**：范围为桌面 D1–D13 与移动 M1–M12b，证据在 evidence/desktop 与 evidence/mobile；第一轮冻结稿为 report-before-followup.md。其结论只覆盖当时检查。
2. **冻结前源代码恢复**：F-01 在冻结 first/ 之前修正，因此 desktop/first/ 是“修正后的首版”；父会话从首次写入的工具参数恢复了最初写入内容到 parent-review/original-desktop/，并在 desktop-first-provenance.json 标明这不是渲染前冻结、冒烟截图才是首次渲染证据。本轮未改动 desktop/first/、mobile/first/ 与 parent-review/ 的任何文件。
3. **暴露问题后的定点复测（第二轮）**：只做 B02 的进入/返回、关联预约入口，以及 B01/B03/B04 的必要分支；只按新版 SKILL 依据校准两份方案；不重跑完整套件、不新增审计项、不做全包 hash。

### 10.3 本轮修正

| 编号 | 位置/输入状态 | 观察 | 用户后果 | 依据 | 最小修法 | 回查 |
| --- | --- | --- | --- | --- | --- | --- |
| F-M02 | 移动端书目详情，存在本人 ready 预约（B02；B01 预约成功后同状态） | 被自己的待取预约挡住时仍显示新建预约的通用期限，且没有关联预约入口 | 用户看不到实际取书期限，不知去哪里查看已有预约 | SKILL 第 1 节新增段落；parent-review/ 复核 | 期限读取当前记录 pickupUntil；notice 增加“查看该预约”入口；无本人记录时不加入口 | T1–T6（见 10.4） |
| P-01 | additional/plan-training-registration.md | 方案把“显式保存、离开即丢弃”写成既定行为 | 实施者可能据此新增或假设系统没有的保存机制 | SKILL 第 1 节“阶段保存、持久化和返回继续必须有项目依据” | 标为方案判断待项目确认；补充保存能力待确认项；验证点改为机制确认后执行 | evidence/followup/diffs.txt |
| P-02 | additional/plan-photography-reading.md | 无素材却给出结构与视觉决定 | 未验证假设可能被误当方案结论 | 素材证据缺口 | 只保留本次素材缺口与所需材料，删除未验证的视觉决定 | evidence/followup/diffs.txt |

### 10.4 复测记录与证据

- 记录：evidence/followup/interaction-log.md（T1–T6 原始输出）；差异：evidence/followup/diffs.txt；修改前快照：evidence/followup/before/。
- 截图：T1-b02-detail.png（B02 实际期限与入口）、T1b-reservations-entry.png（入口落地）、T2-b01-after-reserve.png（B01 预约后同状态）、T4-b04-detail.png、T6-b02-360.png。
- 结果摘要：
  - T1/T1b：B02 详情取书期限显示“10月5日 18:00 前”（来自记录）；notice 出现“查看该预约”；点击进入我的预约，B02 卡片信息一致；返回列表正常。
  - T2：B01 预约前无 notice、可约；预约成功后显示同一形态的实际期限与入口，动作禁用。
  - T3：B03 无可约且无本人记录 → 原因提示、无入口、动作禁用。
  - T4：B04 有已取书记录仍可约 → 无 notice、通用期限、动作可用。
  - T5：变更后 B02 详情 axe 0 violations（只复核变更组件，不扩展审计范围）。
  - T6：360 宽无横向溢出，notice 40px、链接 65×20。
  - console/errors：无输出。

### 10.5 本轮修正成本

- 4 次编辑操作：mobile/app.js、mobile/styles.css、培训方案、摄影方案；本轮未改桌面、未改 first/、未新增依赖。

### 10.6 本轮未验证项

- 未在微信/真机复测；仅浏览器 390 与 360 视口。
- B02 关联入口只验证到“我的预约”列表；取消流程本轮未重跑（首轮已验证）。
- 培训方案与摄影方案未实现、未运行。
- 本轮仍是同一执行会话自检，不是独立盲评。
