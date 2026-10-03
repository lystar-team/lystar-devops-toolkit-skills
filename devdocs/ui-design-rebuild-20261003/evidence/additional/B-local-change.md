# 附加请求二 · 局部修改记录（取消 → 返回）

目标：桌面原型调拨说明编辑区的一个取消按钮，仅改文案，行为与布局不变。

## 修改前基线
- 基线文件：evidence/additional/B-before/desktop/（index.html、styles.css、app.js、test-adapter.js）
- 修改前按钮：cancelNoteBtn 文本“取消”，盒子 62×36，位于表单操作行（行宽 291，位置 x=1173,y=397）。

## 实际差异（diff -r 基线 与 当前）
```
diff -r '--exclude=first' '--exclude=assets' '--exclude=FIXES.md' evidence/additional/B-before/desktop/index.html desktop/index.html
92c92
<           <button id="cancelNoteBtn" class="btn btn-secondary" type="button">取消</button>
---
>           <button id="cancelNoteBtn" class="btn btn-secondary" type="button">返回</button>
```

只有 index.html 第 92 行一个按钮文案发生变化；styles.css、app.js、test-adapter.js 无差异。

## 修改后核验（运行页面）
```
按钮文案：返回
盒子：62×36 at x=1173,y=397（与修改前一致）
保存按钮：90×36（一致）；操作行：291×36（一致）
点击返回后：formHidden=true；说明视图与 store 内容保持原文；焦点回到“编辑说明”
```

## 受影响显示检查
- 按钮宽度由两个字与内边距决定，取消/返回同宽，操作行位置与尺寸未变。
- 编辑进出路径、取消丢弃草稿的行为与修改前一致（对照 D6 记录）。
- 行为提示：按钮语义从“取消”改为“返回”，但它仍会丢弃未保存的草稿；文案与行为存在理解落差，按本次要求未改行为，记录为观察项。

## 测试环境备注
- 修改文件后首次打开时 Chrome 命中本地缓存，仍显示旧文案；使用带查询参数的地址（index.html?verify=1）重新加载后确认。属于本地静态服务缓存现象，不是页面缺陷。
