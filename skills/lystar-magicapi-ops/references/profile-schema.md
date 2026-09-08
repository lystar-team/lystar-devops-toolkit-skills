# Profile Schema

`lystar-magicapi-ops` 的全局配置文件是：

```text
$LYSTAR_HOME/config/magicapi.toml
```

## 结构

```toml
default_profile = "prod-cron"

[profiles.prod-cron]
base_url = "http://example.com:31289"
webide_base_path = "/cron/cron-center/liteasy/webide"
runtime_base_path = "/cron/cron-center"
login_path = "/login"
resource_path = "/resource"
resource_file_path = "/resource/file"
resource_save_path = "/resource/file/api/save"
username = "yean"
password = "本机受控配置中的密码"
verify_tls = false
default_group = "/yean"

[profiles.prod-cron.header_sets.query]
"X-Custom-Header" = "本机受控配置中的接口 Token"
```

字段说明：

- `base_url`：协议、主机、端口，可带部署根路径，但不带具体登录或运行接口路径。
- `webide_base_path`：WebIDE 基础路径。
- `runtime_base_path`：运行接口基础路径。
- `login_path`：相对 `webide_base_path` 的登录路径。
- `resource_path`：资源列表路径。
- `resource_file_path`：资源详情路径前缀。
- `resource_save_path`：资源保存路径。
- `username`、`password`：Magic-API 登录凭据。
- `verify_tls`：HTTPS 证书校验开关，默认 `true`。
- `default_group`：资源查询的默认分组提示，不自动限制远端结果。
- `header_sets`：接口运行使用的本地请求头集合。

## 文件边界

- 配置文件权限为 `0600`。
- 登录态文件位于 `$LYSTAR_HOME/state/magicapi/`，权限为 `0600`。
- 接口保存前的远端详情快照位于 `$LYSTAR_HOME/state/magicapi/backups/`，权限为 `0600`。
- 仓库源码、独立安装包和 Skill 文档不保存实际 Profile、密码、Cookie、Token 或运行结果。
- Profile 别名使用小写或大写字母、数字、点、下划线和短横线。

## 维护命令

```bash
magicx profile add <alias> --base-url <url> --username <user> --password-stdin
magicx profile list
magicx profile show <alias>
magicx profile use <alias>
magicx profile header-set <alias> <name> --header-file <file>
magicx profile remove <alias> --confirm
```

删除 Profile 只删除本地 Profile 和登录态，不删除远端 Magic-API 资源。
