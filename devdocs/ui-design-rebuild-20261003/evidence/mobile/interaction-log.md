## M1 目标视口与长中文（first 版）
```
"{\n \"viewport\": \"390x844\",\n \"hOverflow\": false,\n \"titles\": [\n  {\n   \"text\": \"界面设计的秩序\",\n   \"cw\": 273,\n   \"sw\": 273,\n   \"ch\": 22,\n   \"sh\": 22,\n   \"overX\": false,\n   \"overY\": false\n  },\n  {\n   \"text\": \"信息架构实践：从用户任务到跨平台产品\",\n   \"cw\": 267,\n   \"sw\": 267,\n   \"ch\": 44,\n   \"sh\": 44,\n   \"overX\": false,\n   \"overY\": false\n  },\n  {\n   \"text\": \"城市与日常生活\",\n   \"cw\": 274,\n   \"sw\": 274,\n   \"ch\": 22,\n   \"sh\": 22,\n   \"overX\": false,\n   \"overY\": false\n  },\n  {\n   \"text\": \"看见数据\",\n   \"cw\": 273,\n   \"sw\": 273,\n   \"ch\": 22,\n   \"sh\": 22,\n   \"overX\": false,\n   \"overY\": false\n  },\n  {\n   \"text\": \"纸上的建筑\",\n   \"cw\": 273,\n   \"sw\": 273,\n   \"ch\": 22,\n   \"sh\": 22,\n   \"overX\": false,\n   \"overY\": false\n  },\n  {\n   \"text\": \"植物观察笔记\",\n   \"cw\": 273,\n   \"sw\": 273,\n   \"ch\": 22,\n   \"sh\": 22,\n   \"overX\": false,\n   \"overY\": false\n  }\n ],\n \"locs\": [\n  {\n   \"text\": \"馆藏 主馆三层设计书区A12排\",\n   \"cw\": 273,\n   \"sw\": 273,\n   \"ch\": 19,\n   \"sh\": 19,\n   \"overX\": false,\n   \"overY\": false\n  },\n  {\n   \"text\": \"馆藏 东区分馆二层数字产品与信息技术\",\n   \"cw\": 267,\n   \"sw\": 267,\n   \"ch\": 37,\n   \"sh\": 37,\n   \"overX\": false,\n   \"overY\": false\n  },\n  {\n   \"text\": \"馆藏 主馆二层人文书区C05排\",\n   \"cw\": 274,\n   \"sw\": 274,\n   \"ch\": 19,\n   \"sh\": 19,\n   \"overX\": false,\n   \"overY\": false\n  },\n  {\n   \"text\": \"馆藏 主馆三层技术书区A06排\",\n   \"cw\": 273,\n   \"sw\": 273,\n   \"ch\": 19,\n   \"sh\": 19,\n   \"overX\": false,\n   \"overY\": false\n  },\n  {\n   \"text\": \"馆藏 主馆三层艺术书区D02排\",\n   \"cw\": 273,\n   \"sw\": 273,\n   \"ch\": 19,\n   \"sh\": 19,\n   \"overX\": false,\n   \"overY\": false\n  },\n  {\n   \"text\": \"馆藏 西区分馆一层自然科学书区A03\",\n   \"cw\": 273,\n   \"sw\": 273,\n   \"ch\": 19,\n   \"sh\": 19,\n   \"overX\": false,\n   \"overY\": false\n  }\n ],\n \"tabbarVisible\": true,\n \"badge\": \"1\",\n \"badgeHidden\": false\n}"
```
"{\"viewport\":\"360x800\",\"hOverflow\":false,\"titleCount\":6}"
```

## M2 搜索与空结果（first 版）
```
"{\"query\":\"建筑\",\"rows\":1,\"first\":\"纸上的建筑\",\"clearVisible\":true}"
"{\"query\":\"沈一衡\",\"rows\":1,\"first\":\"看见数据\"}"
"{\"rows\":0,\"emptyText\":\"没有找到与“历史”相关的书目\",\"emptyButton\":\"清除搜索\"}"
"{\"query\":\"\",\"rows\":6,\"active\":\"bookSearch\"}"
```

## M3 详情进入与返回（first 版，360x800）
```
"{\"hash\":\"#/book/B06\",\"navTitle\":\"青禾学院图书馆\",\"backHidden\":true,\"tabbarHidden\":false,\"detailTitle\":null,\"action\":null,\"infoRows\":[]}"
"{\"hash\":\"\",\"scrollY\":45,\"booksVisible\":true,\"tabbarHidden\":false,\"activeTab\":\"找书\"}"
```

## M3b 详情进入与返回（等待渲染后复核，360x800）
```
"{\"beforeClickScrollY\":45}"
"{\"hash\":\"#/book/B06\",\"navTitle\":\"书目详情\",\"backHidden\":false,\"tabbarHidden\":true,\"detailTitle\":\"植物观察笔记\",\"action\":\"预约这本书\"}"
"{\"hash\":\"\",\"searchValue\":\"\",\"rows\":6,\"scrollY\":45,\"navTitle\":\"青禾学院图书馆\",\"backHidden\":true,\"tabbarHidden\":false}"
```

## M3c 带搜索条件进入详情并返回（fresh refs，390x844）
```
"{\"afterFill\":\"\",\"rows\":6}"
"{\"route\":\"#/book/B06\",\"detailTitle\":\"植物观察笔记\"}"
"{\"route\":\"\",\"searchValue\":\"\",\"rows\":6,\"firstRow\":\"界面设计的秩序\",\"clearVisible\":false}"
```

## M3d 带搜索条件进入详情并返回（ref=@e5，390x844）
```
✓ Done
"{\"afterFill\":\"植物\",\"rows\":1}"
"{\"route\":\"#/book/B06\",\"detailTitle\":\"植物观察笔记\",\"tabbarHidden\":true}"
"{\"route\":\"\",\"searchValue\":\"植物\",\"rows\":1,\"firstRow\":\"植物观察笔记\"}"
```

## M4 已有一本待取时的限制（B02，first 版）
```
"{\"title\":\"信息架构实践：从用户任务到跨平台产品设计与维护\",\"info\":[\"分类 设计\",\"可约册数 1 册\",\"馆藏位置 东区分馆二层数字产品与信息技术专业书区B08排\",\"取书地点 主馆一层服务台\",\"取书期限 预约提交后两天内，当日 18:00 前\"],\"notice\":\"你已有这本书的待取预约，取书后可再次预约。\",\"actionText\":\"暂不可预约\",\"actionDisabled\":true}"
```

## M5 预约成功流程与数据一致性（B01，first 版）
```
"{\"sheetVisible\":true,\"sheetTitle\":\"确认预约\",\"sheetBody\":\"《界面设计的秩序》 取书地点 主馆一层服务台 取书期限 10月5日 18:00 前 提交后为你保留该册，请在取书期限内到服务台取书。\",\"primary\":\"提交预约\",\"secondary\":\"再想想\"}"
"{\"sheetHidden\":true,\"toast\":\"预约成功\",\"banner\":\"预约成功，请于 10月5日 18:00 前到主馆一层服务台取书。 查看我的预约\",\"info\":[\"分类 设计\",\"可约册数 2 册\",\"馆藏位置 主馆三层设计书区A12排\",\"取书地点 主馆一层服务台\",\"取书期限 预约提交后两天内，当日 18:00 前\"],\"actionText\":\"暂不可预约\",\"actionDisabled\":true}"
"{\n \"b01Available\": 2,\n \"reservationCount\": 3,\n \"b01Reservations\": [\n  {\n   \"id\": \"YY20261003002\",\n   \"bookId\": \"B01\",\n   \"status\": \"ready\",\n   \"created\": \"2026-10-03 13:30\",\n   \"pickupUntil\": \"2026-10-05 18:00\"\n  }\n ],\n \"readyTotal\": 2\n}"
"{\"hash\":\"#/reservations\",\"navTitle\":\"我的预约\",\"cardCount\":3,\"groups\":[\"待取书\",\"已结束\"],\"firstCard\":\"界面设计的秩序 待取书 程言 预约编号 YY20261003002 取书地点 主馆一层服务台 取书期限 10月5日 18:00 前 预约时间 2026-10-03 13:30 取消预约\"}"
"{\"b01Row\":\"界面设计的秩序 程言 馆藏 主馆三层设计书区A12排 可约 2 册 已有待取\",\"badge\":\"2\"}"
```

## M6 提交中状态与阻止重复（slow 适配器，first 版）
```
"{\"during\":\"提交中…\",\"primaryDisabled\":true,\"secondaryDisabled\":true,\"calls\":1,\"sheetVisible\":true}"
"double-clicked-while-disabled"
"{\"calls\":1,\"b01Available\":2,\"b01ReservationCount\":1,\"toast\":\"预约成功\",\"sheetHidden\":true}"
```

## M7 预约失败保留与重试（fail-once 适配器，first 版）
```
"{\"error\":\"提交失败，请重试。\",\"errorHidden\":false,\"primary\":\"重试预约\",\"secondaryEnabled\":true,\"sheetVisible\":true,\"bookStillOnScreen\":\"《界面设计的秩序》\",\"calls\":1,\"b01Available\":3,\"reservationCount\":2}"
"{\"calls\":2,\"b01Available\":2,\"b01ReservationCount\":1,\"lastReservation\":{\"id\":\"YY20261003002\",\"bookId\":\"B01\",\"status\":\"ready\",\"created\":\"2026-10-03 13:32\",\"pickupUntil\":\"2026-10-05 18:00\"}}"
```

## M8 取消预约流程与状态一致性（B02，first 版）
```
"{\"sheetTitle\":\"取消预约\",\"sheetBook\":\"《信息架构实践：从用户任务到跨平台产品设计与维护》\",\"sheetInfo\":[\"预约编号 YY20261003001\",\"取书期限 10月5日 18:00 前\"],\"note\":\"取消后该预约结束，该册释放回可预约数量。\",\"primary\":\"确认取消预约\",\"primaryClass\":\"btn btn-danger\"}"
"{\"sheetHidden\":true,\"b02Available\":1,\"b02Status\":\"ready\"}"
"{\n \"b02Available\": 2,\n \"b02Status\": \"cancelled\",\n \"groups\": [\n  \"待取书\",\n  \"已结束\"\n ],\n \"emptyHint\": \"当前没有待取书的预约。\",\n \"cards\": [\n  \"信息架构实践：从用户任务到跨平台产品设计与维护 已取消 许文川 预约编号 YY20261003001 预约时间 2026-10-03 08:\",\n  \"看见数据 已取书 沈一衡 预约编号 YY20261002006 预约时间 2026-10-02 10:20 原取书期限 10月4日 18:0\"\n ],\n \"badgeHidden\": true,\n \"toast\": \"已取消预约\"\n}"
```

## M8b 取消后可再次预约（first 版）
```
"{\"b02Row\":\"信息架构实践：从用户任务到跨平台产品设计与维护 许文川 馆藏 东区分馆二层数字产品与信息技术专业书区B08排 可约 2 册\"}"
"{\"notice\":null,\"actionText\":\"预约这本书\",\"actionDisabled\":false}"
```

## M9 取消失败保留与重试（fail-once 适配器，first 版）
```
"{\"error\":\"取消失败，请重试。\",\"primary\":\"重试取消\",\"sheetVisible\":true,\"calls\":1,\"b02Status\":\"ready\",\"b02Available\":1}"
"{\"calls\":2,\"b02Status\":\"cancelled\",\"b02Available\":2,\"cancelledCount\":1}"
```

## M10 焦点、禁用原因与未要求能力的文本检查（first 版）
```
"tab is-active|\n      \n        \n        1\n      \n      我的预约\n    "
"{\"sheetVisible\":true,\"activeElement\":\"BUTTON.btn btn-secondary|取消预约\"}"
"{\"afterEscapeSheetHidden\":false}"
"{\"forbiddenTerms\":[]}"
"{\"b03Info\":[\"分类 人文\",\"可约册数 暂无可约\",\"馆藏位置 主馆二层人文书区C05排\",\"取书地点 主馆一层服务台\",\"取书期限 预约提交后两天内，当日 18:00 前\"],\"b03Notice\":\"这本书当前没有可预约册数。\",\"b03Action\":\"暂不可预约\",\"b03Disabled\":true}"
```
"{\"b04Notice\":null,\"b04Action\":\"预约这本书\",\"b04Disabled\":false}"
```

## M10b 焦点管理复测（F-M01 后）
```
"{\"activeAfterOpen\":\"sheetPrimary|确认取消预约\",\"sheetVisible\":true}"
"{\"afterTab\":\"|\\n\\n  \\n    \\n      \\n    \\n    我的预约\\n    \\n      \\n      \\n      \\n    \\n  \\n\\n  \\n    \\n      \\n        \\n          \\n          \\n          清除\\n        \\n      \\n      界面设计的秩序程言馆藏 主馆三层设计书区A12排可约 3 册信息架构实践：从用户任务到跨平台产品设计与维护许文川馆藏 东区分馆二层数字产品与信息技术专业书区B08排可约 1 册已有待取城市与日常生活江映澄馆藏 主馆二层人文书区C05排暂无可约看见数据沈一衡馆藏 主馆三层技术书区A06排可约 2 册纸上的建筑顾景行馆藏 主馆三层艺术书区D02排可约 4 册植物观察笔记赵雨禾馆藏 西区分馆一层自然科学书区A03排可约 1 册\\n    \\n\\n    \\n      \\n    \\n\\n    \\n      \\n        林予宁\\n        R2026032\\n      \\n      待取书信息架构实践：从用户任务到跨平台产品设计与维护待取书许文川预约编号YY20261003001取书地点主馆一层服务台取书期限10月5日 18:00 前预约时间2026-10-03 08:30取消预约已结束看见数据已取书沈一衡预约编号YY20261002006预约时间2026-10-02 10:20原取书期限10月4日 18:00 前\\n    \\n  \\n\\n  \\n    \\n      \\n        \\n      \\n      找书\\n    \\n    \\n      \\n        \\n        1\\n      \\n      我的预约\\n    \\n  \\n\\n  \\n    \\n    \\n      取消预约\\n      《信息架构实践：从用户任务到跨平台产品设计与维护》预约编号YY20261003001取书期限10月5日 18:00 前取消后该预约结束，该册释放回可预约数量。\\n      \\n      \\n        再想想\\n        确认取消预约\\n      \\n    \\n  \\n\\n  \\n\\n\\n\\n\\n\\n\"}"
"{\"afterTabLoop\":\"sheetPrimary|确认取消预约\"}"
"{\"afterShiftTab\":\"sheetSecondary|再想想\"}"
"{\"afterEscapeHidden\":true,\"focusRestored\":\"取消预约\"}"
```

## M10c 焦点循环复测（DOM 顺序修正后）
```
"{\"open\":\"sheetPrimary\"}"
"{\"tab1\":\"sheetSecondary\"}"
"{\"tab2\":\"sheetPrimary\"}"
"{\"shiftTab\":\"sheetSecondary\"}"
"{\"escClosed\":true,\"focusBack\":\"取消预约\"}"
```

## M10d 提交成功后的焦点与流程回归（F-M01 后）
```
"{\"focusAfterReserve\":\"查看我的预约\",\"banner\":\"预约成功，请于 10月5日 18:00 前到主馆一层服务台取书。 查看我的预约\",\"b01Available\":2}"
"{\"focusAfterCancel\":\"screen\",\"b02Status\":\"cancelled\",\"b02Available\":2}"
```

## M11 对比度与触控尺寸（first 版 + F-M01）
```
"{\n \"location\": {\n  \"sample\": \"馆藏 主馆三层设计书\",\n  \"size\": \"12px\",\n  \"ratio\": 4.97\n },\n \"author\": {\n  \"sample\": \"程言\",\n  \"size\": \"13px\",\n  \"ratio\": 7.24\n },\n \"tabInactive\": {\n  \"sample\": \"找书\",\n  \"size\": \"11px\",\n  \"ratio\": 7.85\n },\n \"readerId\": {\n  \"sample\": \"R2026032\",\n  \"size\": \"12px\",\n  \"ratio\": 4.53\n },\n \"avail\": {\n  \"sample\": \"可约 3 册\",\n  \"size\": \"13px\",\n  \"ratio\": 15.92\n },\n \"rowState\": {\n  \"sample\": \"已有待取\",\n  \"size\": \"12px\",\n  \"ratio\": 5.54\n },\n \"targets\": {\n  \"bookRowMin\": 95,\n  \"tab\": 54,\n  \"searchInput\": 40,\n  \"navBack\": 0\n }\n}"
"{\"cancelButton\":\"36x86\",\"resCardStatus\":\"待取书\"}"
```

## M12 详情页与面板的目标尺寸、360 宽长文本（复测）
```
"{\n \"viewport\": \"360x800\",\n \"hOverflow\": false,\n \"navBackBox\": {\n  \"h\": 40,\n  \"w\": 40,\n  \"cw\": 40,\n  \"sw\": 40,\n  \"overX\": false\n },\n \"actionButton\": {\n  \"h\": 46,\n  \"w\": 336,\n  \"cw\": 334,\n  \"sw\": 334,\n  \"overX\": false\n },\n \"detailTitle\": {\n  \"h\": 53,\n  \"w\": 304,\n  \"cw\": 304,\n  \"sw\": 304,\n  \"overX\": false\n },\n \"infoValues\": [\n  {\n   \"h\": 22,\n   \"w\": 28,\n   \"cw\": 28,\n   \"sw\": 28,\n   \"overX\": false\n  },\n  {\n   \"h\": 22,\n   \"w\": 25,\n   \"cw\": 25,\n   \"sw\": 25,\n   \"overX\": false\n  },\n  {\n   \"h\": 43,\n   \"w\": 218,\n   \"cw\": 218,\n   \"sw\": 218,\n   \"overX\": false\n  },\n  {\n   \"h\": 22,\n   \"w\": 98,\n   \"cw\": 98,\n   \"sw\": 98,\n   \"overX\": false\n  },\n  {\n   \"h\": 22,\n   \"w\": 209,\n   \"cw\": 209,\n   \"sw\": 209,\n   \"overX\": false\n  }\n ]\n}"
```

## M12b 面板 360 宽度量与长文本（预约 B01）
```
"{\"sheetOpen\":true,\"sheetBox\":{\"h\":282,\"w\":360,\"overX\":false},\"sheetBook\":{\"h\":23,\"w\":328,\"overX\":false},\"primary\":{\"h\":46,\"w\":159,\"overX\":false},\"secondary\":{\"h\":46,\"w\":159,\"overX\":false},\"hOverflow\":false,\"viewport\":\"360x800\"}"
```
