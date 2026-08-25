# LYStar 服务器资料库

这是 `${LYSTAR_SERVER_HOME:-$LYSTAR_HOME/servers}` 的服务器事实目录，也是 `lystar-ssh-ops` 的唯一资料入口。标准安装会在 `$HOME/.lystar/servers` 创建真实目录；已有 Git 资料库只能通过显式迁移命令接入。

## 标准目录

```text
servers/
├── README.md                         # 服务器索引，不写服务细节
├── _templates/
│   ├── server.md
│   ├── service.md
│   └── topic.md
└── <server-id>/                      # 稳定 ASCII ID：lower-kebab-case
    ├── README.md                     # 主机事实、连接命名、主题索引
    ├── services/
    │   └── <service-id>.md            # 一个可独立运维的服务
    └── topics/
        └── <topic-id>.md              # 网络、证书、路由等独立主题
```

服务器目录、服务文件和主题文件都使用稳定 ID，不用显示名称或临时 IP 作为路径。显示名称、别名和历史名称写在文档内部及根索引中。

## SSH 连接命名

服务器 `README.md` 与 `sshx` 使用同一个稳定 profile 名称：

```text
默认连接：<server-id>
用途连接：<server-id>-<purpose>
跳板连接：<server-id>-via-<jump-id>
```

profile 只允许小写 ASCII、数字和 `-`，同一主机不同入口必须写清用途、地址、端口、用户和跳板链。修改 profile 时同步服务器 `README.md`，不要在服务文档里重复保存另一份连接事实。

## 文档责任

- 根 `README.md`：服务器 ID、显示名称、别名、主要角色和链接。
- 服务器 `README.md`：主机事实、SSH 用户名/密码、profile、整机约束和服务/主题索引。
- `services/<service-id>.md`：服务状态、入口、配置/数据路径、依赖、检查、变更和回退。
- `topics/<topic-id>.md`：网络、证书、路由、代理等独立运维主题。
- Git：保存资料仓库时间线；不另建重复的总变更数据库。

## 安全边界

这里可能保存明文 SSH 或数据库凭据。只放在本地受控目录或私有仓库，不要提交到公开仓库，不要在普通回复、日志和命令输出中展示凭据。
