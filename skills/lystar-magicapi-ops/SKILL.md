---
name: lystar-magicapi-ops
description: 通过 magicx 管理 Magic-API WebIDE 和运行接口。用于 Magic-API 登录、资源列表、资源详情、接口保存/修改、运行查询接口、请求头集合和多环境 Profile；不用于直接数据库或 SSH 操作。
---

# LYStar Magic-API Ops

统一使用 PATH 中的 `magicx`。不同 Agent Harness 共用 `$LYSTAR_HOME` 下的 Magic-API Profile、登录态和结果快照。

启动 `magicx` 时会按 Skill 更新器的 24 小时检查间隔自动检查上游更新；检查或安装失败不阻断当前 Magic-API 操作：

```bash
lystar-skill-update auto lystar-magicapi-ops --quiet || true
```

## 本地配置与登录态

配置文件：

```text
$LYSTAR_HOME/config/magicapi.toml
```

默认 `LYSTAR_HOME` 为 `$HOME/.lystar`。登录态保存于：

```text
$LYSTAR_HOME/state/magicapi/<profile>.json
```

接口保存前的资源快照保存于：

```text
$LYSTAR_HOME/state/magicapi/backups/<profile>/
```

配置和状态可能包含密码、Cookie、`magic-token` 或业务请求头 Token，只能保存在本机受控目录或私有仓库。命令输出、日志、Skill 文档和公开安装包不得显示这些值。

## Profile

每个 Magic-API 环境使用一个别名；存在多个 Profile 时不能静默选择第一个：

```bash
magicx profile add prod-cron \
  --base-url 'http://example.com:31289' \
  --username yean \
  --password-stdin
magicx profile add test-cron \
  --base-url 'http://test.example.com:31289' \
  --username yean \
  --password-stdin
magicx profile list
magicx profile show prod-cron
magicx profile use prod-cron
```

Profile 可以覆盖不同部署的路径：

```bash
magicx profile add prod-cron --update \
  --base-url 'http://example.com:31289' \
  --username yean \
  --webide-base-path /cron/cron-center/liteasy/webide \
  --runtime-base-path /cron/cron-center
```

请求头集合用于接口自身的业务 Header，不和登录态混用：

```bash
magicx profile header-set prod-cron query \
  --header-file ./query-headers.json
magicx profile show prod-cron
```

`query-headers.json` 只能保存在本地受控目录，不提交到仓库。

## 登录

```bash
magicx login prod-cron
magicx status prod-cron
magicx logout prod-cron
```

`magicx` 保存 Magic-API 登录响应中的 Cookie 和 `magic-token`。运行请求遇到 HTTP 401/403 或 Magic-API 401/403 时，会重新登录并重试一次；其他错误保留原始 HTTP 状态、Magic-API 返回码和消息。

## WebIDE 资源

查询资源列表：

```bash
magicx resource list prod-cron
magicx resource list prod-cron --body resource-filter.json
```

查询详情：

```bash
magicx resource get prod-cron --id <resource-id>
magicx resource get prod-cron --path /yean/queryPortraitMissing202607
```

保存或修改接口：

```bash
magicx resource save prod-cron \
  --id <resource-id> \
  --script ./query.js \
  --confirm
magicx resource save prod-cron \
  --file ./resource-detail.json \
  --confirm
```

保存前会获取远端详情并保存本地快照。保存需要 `--confirm`；不把配置写操作伪装成普通查询。修改现有资源时优先获取详情后合并，避免丢失分组、参数、请求头和其他资源字段。

## 运行接口

GET 接口可以直接运行：

```bash
magicx run prod-cron \
  --path /yean/queryPortraitMissing202607 \
  --header-set query
```

POST、PUT、DELETE 需要显式确认：

```bash
magicx run prod-cron \
  --path /yean/some-api \
  --method POST \
  --body request.json \
  --header-set query \
  --confirm
```

请求头可以临时追加：

```bash
magicx run prod-cron \
  --path /yean/queryPortraitMissing202607 \
  --header 'X-Custom-Header=...' \
  --confirm
```

请求头值不写入命令结果快照；包含 Token 的文件不提交仓库。

## 结果

每次操作返回 JSON，区分 HTTP 状态和 Magic-API `code`。当前会话有会话 ID 时可以读取上次结果：

```bash
magicx last --summary
magicx last
magicx last --clear
```

## 边界

- Magic-API 资源保存是远端配置写操作，必须得到用户明确要求并使用 `--confirm`。
- Magic-API 接口运行可能修改业务数据；POST、PUT、DELETE 必须使用 `--confirm`。
- 不能把运行接口称为只读查询，除非方法和接口契约已经确认。
- 数据库查询、SQL 执行和真实数据库核验使用 `lystar-db-ops`。
- SSH、服务器、远程日志和文件操作使用 `lystar-ssh-ops`。
- 本 Skill 不执行浏览器操作；登录接口或资源接口不兼容时，说明具体契约差异，不猜测替代路径。

## 参考资料

- Profile 字段和本地文件边界：`references/profile-schema.md`
- Magic-API 请求契约和响应处理：`references/magicapi-contract.md`
