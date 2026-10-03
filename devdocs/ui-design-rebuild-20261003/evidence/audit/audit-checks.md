# 审计检查记录（补充父会话已跑的格式/安装/打包测试）

## 1. 相对引用完整性（本 Skill 目录内 Markdown）
```
checked: 53, broken: 0
```

## 2. 参考图片资源真实性
```
cases: 10 assets: 15
missing: []
dimension mismatch: []
used-this-run dims: [('ant-query.png', (1440, 1000)), ('arco-query.png', (1440, 1000)), ('tdesign-publish.png', (750, 1624)), ('tdesign-orders.png', (437, 914)), ('tdesign-order-detail.png', (437, 914))]
```

## 3. 独立安装包内容与校验
```
zip 内 skill 文件数与仓库一致：47 / 47
zip 包含安装机制：3 项关键文件
lystar-ui-design.zip: 成功
```

## 4. 固定场景 fixture 对照 fixtures.schema.json 的必要约束（本机手工校验）
```
evals/scenarios/ui-cn-inspection-report/fixture.json: OK
evals/scenarios/ui-cn-stock-transfer/fixture.json: OK
```
