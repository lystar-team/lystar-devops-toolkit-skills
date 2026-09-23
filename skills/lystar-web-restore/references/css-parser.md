# CSS 和 Tailwind 解析规则

## 目标

报告要保留两种值：

1. CSS 原文值，例如 `gap: var(--gap)`、`animation: rotate-gradient var(--speed) linear infinite`；
2. 浏览器当前值，例如 `gap: 22.4px`、`animation-duration: 4s`。

原文值解释组件怎么写，当前值解释这一刻浏览器怎么显示。两者不能互相替代。

## 不要只查 class 字典

相同的 Tailwind class 会受到 Tailwind 版本、主题 token、自定义 CSS、父元素和当前视口影响。必须从目标页面自己的 `document.styleSheets` 或公开 CSS/Bundle 中读取规则。

例如：

```text
gap-gap
→ gap: var(--gap)
→ 当前页面的 --gap: 2rem
→ 当前计算值可能是 22.4px
```

## 展开 CSSOM

遍历每个可读的 stylesheet 和它的 `cssRules`。至少处理：

- `CSSStyleRule`；
- `CSSMediaRule`，保留 `@media` 条件；
- `CSSSupportsRule`，保留 `@supports` 条件；
- `CSSLayerBlockRule`，保留 `@layer` 名字；
- CSS nesting；
- `CSSNestedDeclarations`；
- `CSSKeyframesRule`；
- `CSSFontFaceRule`。

Tailwind v4 可能把声明放在嵌套的 `CSSNestedDeclarations` 中。下面这个规则如果只看外层 `CSSStyleRule.style.cssText`，会被误判为空：

```css
.lg\:grid-cols-12 {
  @media (width >= 64rem) {
    grid-template-columns: repeat(12, minmax(0, 1fr));
  }
}
```

正确报告应该是：

```text
class: lg:grid-cols-12
selector: .lg\:grid-cols-12
condition: @media (width >= 64rem)
property: grid-template-columns
value: repeat(12, minmax(0, 1fr))
```

## 合并嵌套选择器

如果子规则使用 `&`，用父选择器替换 `&`：

```css
.hover\:scale-105 {
  &:hover {
    scale: 105%;
  }
}
```

应还原为：

```text
.hover\:scale-105:hover { scale: 105%; }
```

如果子规则没有 `&`，按 CSS nesting 规则拼成父子选择器。多个选择器要逐个组合，不能只拼第一个。

## 精确匹配 class

不能用简单的 `selector.includes(".grid")`。它会把 `.grid-cols-1` 错配给 `grid`，也会把 `.bg-sidebar-accent` 错配给 `bg-sidebar`。

做法：

1. 对 class token 使用 `CSS.escape()`；
2. 查找 `.` 加转义 token；
3. 检查 token 后面不是 CSS 标识符字符、连字符或转义起始符；
4. 保留完整选择器、伪类和父选择器。

有些 token 是 `group/sidebar-wrapper`、`recharts-wrapper` 这类命名空间或第三方类，找不到 Tailwind 规则是正常结果，不能补猜一个 CSS。

## 变量、内联值和层叠

对每个目标元素记录：

- class 属性；
- `style` 属性和 DOM style 对象；
- 元素自己和父级继承下来的自定义属性；
- 匹配规则的声明和条件；
- `getComputedStyle()` 的最终值；
- 当前视口、主题、`matchMedia` 结果和字体状态。

脚本输出里，`cssRules[].source` 是规则来自哪个 stylesheet，`conditions` 只放
`@layer`、`@media`、`@supports` 等条件。元素的 `inlineDeclarations` 是浏览器读到的
内联声明；`customProperties` 在根元素保存完整变量，在子元素只保存相对父元素发生
变化的变量，`inlineCustomProperties` 仍保留子元素自己写在 `style` 里的变量。这样报告
不会因为继承变量重复出现而膨胀，但不会丢掉局部覆盖。

内联 style 的优先级可能覆盖 class。例子：

```text
class: animate-marquee-pulse
class 规则: animation: 1s linear infinite marquee-pulse
运行时内联值: animation-duration: 3000ms; animation-delay: 950ms
最终值: 3s，并带有该元素自己的延迟
```

不要把 `var(--color)` 直接改成固定颜色后丢掉变量来源；报告同时给原表达式和当前解析值。

## 动画链

按下面的链路输出动画：

```text
class 或 inline style
→ animation 属性
→ animation-name
→ duration / timing-function / delay / iteration-count / play-state
→ @keyframes
→ 每个关键帧的 transform / opacity / color / background 等属性
```

浏览器里优先调用：

```js
element.getAnimations()
animation.effect.getTiming()
animation.effect.getKeyframes()
```

CSSOM 中的 `@keyframes` 也要保存。`getComputedStyle()` 读到的 `transform` 和 `opacity` 会随时间变化，不能把某一个时刻的值当成完整动画。

如果看到 `requestAnimationFrame` 不断改内联 style、Canvas、WebGL 或第三方动画库，报告“运行时观察到”，并记录采样结果；没有 CSS keyframes 时不要伪造 keyframes。

## 字体和资源

字体至少从三处交叉检查：

- `@font-face` 的 family、src、weight、style、unicode-range；
- 元素最终的 `font-family`、`font-size`、`font-weight`、`line-height`；
- `document.fonts` 的加载状态。

图片、SVG、视频和 `background-image` 要记录最终 URL、元素属性和使用位置。公开地址可能是短期地址，报告里区分“地址已发现”和“文件已下载”。

## 浏览器条件

伪类和媒体条件不是普通 class：

- `hover:` 需要真正进入 hover 状态，而且要检查 `(hover: hover)`；
- `focus:` 需要让元素获得焦点；
- `active:` 需要在鼠标按下或键盘激活时采集；
- `data-*`、`aria-*` 和 `group-*` 要记录触发它们的属性和父元素；
- 响应式要改变真实视口，不要只改报告里的数字；
- 主题和 reduced-motion 要记录 `matchMedia` 结果。

当前条件不满足时，仍然输出规则本身，但把最终值标成“本次未生效”。
