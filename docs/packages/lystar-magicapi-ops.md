# LYStar Magic-API Ops

通过 `magicx` 直接管理 Magic-API WebIDE 和运行接口。

## 能力

- 多 Magic-API Base URL 和账号 Profile
- 密码、Cookie、`magic-token` 本地状态管理
- WebIDE 资源列表、资源详情和资源保存
- Magic-API 运行接口执行
- 请求头集合，例如 `X-Custom-Header`
- 登录态失效后自动重新登录一次
- 接口保存前的本地资源快照

## 安装

```bash
./install.sh --harness auto
```

或单独安装：

```bash
./install.sh --harness codex
```

安装后把 `$HOME/.lystar/bin` 加入 PATH。

## Profile

```bash
magicx profile add prod-cron \
  --base-url 'http://example.com:31289' \
  --username yean \
  --password-stdin
magicx profile list
magicx login prod-cron
```

配置文件位于：

```text
$HOME/.lystar/config/magicapi.toml
```

不要把这个文件提交到仓库。

## 资源与接口

```bash
magicx resource list prod-cron
magicx resource get prod-cron --id <resource-id>
magicx resource get prod-cron --path /yean/queryPortraitMissing202607
magicx resource save prod-cron --id <resource-id> --script ./query.js --confirm
magicx run prod-cron --path /yean/queryPortraitMissing202607
```

POST、PUT、DELETE 执行和资源保存需要 `--confirm`。
