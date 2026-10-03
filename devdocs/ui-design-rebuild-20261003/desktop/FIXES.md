# 桌面原型修正记录

## F-01（冻结 first/ 之前，冒烟阶段）
- 现象：查看模式下调拨说明的编辑表单同时显示，页面出现重复内容。
- 证据：evidence/desktop/desktop-smoke-1440.png（首次运行画面）。
- 原因：`.note-form { display: block; }` 覆盖了 `hidden` 属性的 `display: none`。
- 修法：全局规则 `[hidden] { display: none !important; }`。
- 范围：仅 styles.css 一处新增规则；first/ 快照为修正后的首版，冒烟截图保留原状。

## F-02（首次测试后定点修正）
- 现象：辅助文字使用 --ink-3 #7d8895，12–13px 下对白底对比度 3.6:1，对页面灰底 3.3:1，低于正文常用 4.5:1。
- 证据：evidence/desktop/interaction-log.md 中 D13 对比度测量；影响物料编号、仓库角色、页头字段名、字数计数。
- 修法：--ink-3 调整为 #626d7a（白底 5.27:1，灰底约 4.84:1）；其余 token 不动。
- 回查：重测 D13 相关元素与截图 D2-final/D1-final。
