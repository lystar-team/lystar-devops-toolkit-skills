# screenshot-only 参考流程

适用：截图、设计稿、人工裁剪图。

## 证据边界

截图证明尺寸、像素位置、可见边界、颜色、重复关系、文字形状、行数、对齐、间距、图片显示比例、静态图表外观。

截图不能证明 DOM 层级、组件来源、接口、权限、字体文件、原始资产、交互、动画、响应式和跨平台表现。无法辨认的文字与图标进入 `unavailable`，生产页面使用项目事实源。

截图还原使用 `iteration` 或 `acceptance`：`iteration` 输出差异与未决项；`acceptance` 需要 Reference Meta、同轮 Capture、Preflight、Regions、关键区域检查和 Diff。缺少运行环境证据时不写 `accepted`。

## 场景初始化

```bash
python3 "$LYSTAR_UI_RESTORE_ROOT/scripts/ui_restore.py" init \
  --root . \
  --scenario-id <scenario> \
  --reference-mode screenshot \
  --target web
```

填写 `decisions.json` Contract。人工区域至少包含：

- `id`、`role`、`bbox`、`parent`；
- `visualOwner`、`allowedLayers`；
- `evidenceIds`；
- `allowedProperties`、`forbiddenChanges`；
- 文字行数、对齐、间距、阴影状态；
- 图片比例、裁切、背景、透明状态。

## 参考检查

```bash
python3 "$LYSTAR_UI_RESTORE_ROOT/scripts/ui_restore.py" inspect \
  --reference reference.png \
  --scenario-dir .visual-restore/scenarios/<scenario> \
  --scenario-id <scenario> \
  --regions manual-regions.json
```

检查元数据与裁剪写入 `inspection/`；Regions、Evidence、Content、Assets、Tokens、Decisions 写入场景目录。

## 图表与图片

- 数据图表使用项目图表组件或正式图表组件。
- 截图裁片不能冒充透明资产。
- 图片中的文字、数字、Label 和图表不能改写成业务数据。
- 圆形、圆环、人物、地图、图标记录原始比例与显示比例。
- imagegen 输出检查文字残留、方形背景、重复轮廓、边缘和最终尺寸。

## 无结论项

单视口截图不能证明响应式。静态截图不能证明状态变化。结构证据需要运行页 Geometry 与 Structure Map。
