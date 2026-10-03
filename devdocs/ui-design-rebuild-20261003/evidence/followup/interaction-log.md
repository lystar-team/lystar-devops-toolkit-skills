# 定点复测交互记录（第二轮，F-M02 后）

## T1 B02 详情：已有待取时的实际期限与关联预约入口
```
"{\"route\":\"#/book/B02\",\"detailTitle\":\"信息架构实践：从用户任务到跨平台产品设计与维护\",\"infoRows\":[\"分类 设计\",\"可约册数 1 册\",\"馆藏位置 东区分馆二层数字产品与信息技术专业书区B08排\",\"取书地点 主馆一层服务台\",\"取书期限 10月5日 18:00 前\"],\"noticeText\":\"你已有这本书的待取预约，取书后可再次预约。 查看该预约\",\"noticeLink\":\"查看该预约\",\"actionText\":\"暂不可预约\",\"actionDisabled\":true,\"hOverflow\":false}"
```

### T1b 关联预约入口 → 我的预约
```
"{\"route\":\"#/reservations\",\"navTitle\":\"我的预约\",\"b02Card\":\"信息架构实践：从用户任务到跨平台产品设计与维护 待取书 许文川 预约编号 YY20261003001 取书地点 主馆一层服务台 取书期限 10月5日 18:00 前 预约时间 2026-10-03 08:30 取消预约\"}"
"{\"route\":\"#/books\",\"booksVisible\":true,\"navTitle\":\"青禾学院图书馆\",\"activeTab\":\"找书\"}"
```

## T2 B01 分支：可约状态 → 预约后同一本书的已有待取状态
```
"{\"stage\":\"before-reserve\",\"notice\":null,\"noticeLink\":false,\"deadlineRow\":\"取书期限 预约提交后两天内，当日 18:00 前\",\"actionText\":\"预约这本书\",\"actionDisabled\":false}"
"{\"stage\":\"after-reserve\",\"notice\":\"你已有这本书的待取预约，取书后可再次预约。 查看该预约\",\"noticeLink\":\"查看该预约\",\"deadlineRow\":\"取书期限 10月5日 18:00 前\",\"actionText\":\"暂不可预约\",\"actionDisabled\":true,\"b01Available\":2}"
```

## T3 B03 分支：无可约册数且无本人预约
```
"{\"route\":\"#/book/B03\",\"notice\":\"这本书当前没有可预约册数。\",\"noticeLink\":false,\"deadlineRow\":\"取书期限 预约提交后两天内，当日 18:00 前\",\"actionText\":\"暂不可预约\",\"actionDisabled\":true}"
```

## T4 B04 分支：已有已取书记录的可约状态
```
"{\"route\":\"#/book/B04\",\"notice\":null,\"noticeLink\":false,\"deadlineRow\":\"取书期限 预约提交后两天内，当日 18:00 前\",\"actionText\":\"预约这本书\",\"actionDisabled\":false}"
```

## T5 变更区域的 a11y 复核（B02 详情，检查变更组件，不扩展审计范围）
```
axe 4.12.1 violations: 0 passes: 29 incomplete: 0
```

## T6 B02 详情在 360 宽的变更显示
```
"{\"viewport\":\"360x800\",\"hOverflow\":false,\"notice\":{\"h\":40,\"w\":304,\"overX\":false},\"link\":{\"h\":20,\"w\":65,\"overX\":false},\"deadlineRow\":\"取书期限 10月5日 18:00 前\"}"
```
