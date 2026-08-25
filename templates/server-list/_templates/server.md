# <服务器显示名称>

- **server_id**：`<server-id>`
- **状态**：在线 / 停用 / 未核验
- **最后核验**：YYYY-MM-DD HH:mm CST
- **主机名**：`hostname`
- **操作系统**：
- **角色**：
- **别名**：

## SSH 连接与命名

profile 名称必须与 `sshx` 中的 alias 一致；默认连接使用 `<server-id>`，同一主机的其它入口使用 `<server-id>-<purpose>`。

### 可恢复凭据

```text
username=
password=
```

| 用途 | sshx profile | 地址 | 用户 | 端口 | 跳板 profile | 状态 |
| --- | --- | --- | --- | ---: | --- | --- |
| 默认连接 | `<server-id>` | `host` | `root` | `22` | 无 | 未核验 |

服务器文档和 `sshx` 必须同步维护用户名、密码、地址、端口和跳板链。

## 网络信息

| 网络 | 地址 | 网卡/接口 | 说明 |
| --- | --- | --- | --- |
| 管理网 |  |  |  |

## 整机运维约束

-

## 服务索引

| service/topic ID | 类型 | 当前状态 | 文档 |
| --- | --- | --- | --- |
| `<service-id>` | service | 未核验 | [服务名称](services/<service-id>.md) |
| `<topic-id>` | topic | 未核验 | [主题名称](topics/<topic-id>.md) |

## 依赖关系

- 上游：
- 下游：
- 网络、证书或跳板依赖：

## 核验记录

### YYYY-MM-DD

- 核验范围：
- 核验结果：
- 未核验项：
