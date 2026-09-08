# Magic-API Contract

本 Skill 通过 HTTP 直接访问 Magic-API WebIDE 和运行接口，不依赖浏览器页面。

## 默认路径

Profile 可以覆盖所有路径。默认组合如下：

```text
POST <base_url><webide_base_path>/login
POST <base_url><webide_base_path>/resource
GET  <base_url><webide_base_path>/resource/file/{id}
POST <base_url><webide_base_path>/resource/file/api/save?auto=0
<method> <base_url><runtime_base_path><api-path>
```

本 Skill 适配的典型部署可能使用：

```text
WebIDE：/cron/cron-center/liteasy/webide
Runtime：/cron/cron-center
```

不要把某一个环境的前缀写入 Skill 源码；使用 Profile 配置。

## 登录

请求体默认是 JSON：

```json
{
  "username": "...",
  "password": "..."
}
```

登录响应中读取：

- 响应 Header 的 `magic-token`、`magicToken` 或 `token`；
- JSON 响应中递归查找 `magic-token`、`magicToken`、`token` 或 `accessToken`；
- `Set-Cookie` 中的 Cookie。

后续请求会带上保存的 `magic-token` 和 Cookie。

## 资源保存

保存接口使用资源详情 JSON 作为请求体。修改已有资源时先获取详情，再只覆盖用户指定字段。`auto` 默认使用 `0`。

保存前在本机保存详情快照，远端资源不自动删除、不自动发布、不自动执行。

## 运行接口

运行结果保留两层状态：

```json
{
  "http_status": 200,
  "magic_code": 200,
  "message": "success",
  "response": {
    "code": 200,
    "message": "success",
    "data": {}
  }
}
```

HTTP 401/403 或 Magic-API `code` 为 401/403 时自动重新登录并重试一次。HTTP 错误或 Magic-API `code >= 400` 会失败并保留响应内容；接口返回的业务 `code` 需要结合接口契约判断，不能统一称为查询成功。

## 请求头

登录态 Header 与业务 Header 分开保存。业务 Header 使用：

```bash
magicx profile header-set <alias> <set-name> --header-file headers.json
magicx run <alias> --path /example --header-set <set-name>
```

请求结果只展示 Header 名称，不展示 Header 值。Token 文件不提交到仓库。

## 错误定位

遇到 Magic-API 500：

1. 先查看 `http_status`、`magic_code`、`message` 和 `response`。
2. 用 `resource get` 确认远端脚本和接口请求头已经保存。
3. 把脚本缩小到 `return 1`，确认运行契约。
4. 再按 SQL、请求对象、参数和响应结构分段恢复。

不要把页面编辑器错误、接口执行错误和业务 SQL 错误混成一个原因。
