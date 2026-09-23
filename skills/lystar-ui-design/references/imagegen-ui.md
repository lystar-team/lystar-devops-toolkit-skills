# Imagegen UI 提示词

只在 `lystar-ui-design` 已决定需要图像生成时读取。每次调用只生成一种资产。

## 页面 Mockup

```text
Use case: production UI mockup for code implementation
Asset type: full-page [desktop/mobile] interface reference
Primary request: design [page and core task]
Input images: [current screenshot/user reference and its role]
Product and audience: [real product, role, frequency]
Real content: [exact sections, labels, data types, primary actions]
Visual direction: [product character, density, typography, color discipline]
Signature element: [one identifiable treatment]
Composition/framing: [viewport, grid, navigation, content priority]
Components and states: [real controls and required states]
Constraints: preserve [business structure/brand/interaction]; implementation-ready; readable text hierarchy
Avoid: invented features or metrics, decorative card grids, generic AI gradients, illegible text, device frames, watermarks
```

## UI Kit 视觉板

```text
Use case: visual UI Kit reference for implementation
Asset type: organized component and token board, not a marketing poster
Primary request: show typography roles, color roles, spacing rhythm, buttons, inputs, selects, tables/cards where relevant, navigation, feedback and empty/loading/error/disabled states
Product context: [type and users]
Visual direction: [same as approved page mockup]
Composition: neutral labeled board with stable rows and columns
Constraints: components needed by the current product only; consistent geometry and state differences
Avoid: fake brand copy, device mockups, decorative showcases, components outside scope
```

## 插图或纹理资产

```text
Use case: runtime asset for [exact location]
Asset type: [illustration/texture/hero image]
Primary request: [subject and purpose]
Style/medium: [match approved interface]
Composition/framing: [aspect ratio, crop, negative space]
Lighting/mood: [specific]
Constraints: no embedded UI, no logos, no text unless verbatim text is supplied
Avoid: watermarks, stock-photo look, unrelated objects, details that fail at final size
```

## 回读

每张结果检查：产品语境、构图、状态、文字干扰、禁用内容和最终尺寸。只针对一个明确问题迭代一次。
