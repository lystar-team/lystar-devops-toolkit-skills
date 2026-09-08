---
name: lystar-redis-ops
description: 使用 redisx 检查 Redis 连通性、扫描空闲 DB 索引、登记项目占用并验证项目 Redis DB。涉及 Redis 数据源、DB 分配、Redis 健康检查或 XMC 项目初始化时使用。
---

# LYStar Redis Ops

使用统一命令 `redisx`，不要在 Skill 目录、项目目录或日志中保存 Redis 密码。

## 配置

Redis profile 位于 `$HOME/.lystar/config/redis-ops.toml`，可直接保存主机、端口、username/password、TLS 和 DB 数量；配置文件保持 `0600`，运行结果和 snapshot 不输出 password：

```toml
schema_version = 1
default_profile = "project-redis"

[profiles.project-redis]
host = "redis.example.internal"
port = 6379
username = "default"
password = "<stored-local-redis-password>"
tls = false
database_count = 16
```

## 常用命令

```bash
redisx doctor --profile project-redis --json
redisx db-list --profile project-redis --json
redisx db-find-free --profile project-redis --json
redisx db-reserve --profile project-redis --project-id demo --json
redisx db-reassign --profile project-redis --project-id demo --start 40 --confirm --json
redisx db-verify --profile project-redis --database 3 --json
```

## 规则

- 空闲 DB 必须同时满足实际 `DBSIZE = 0` 且没有本地项目预留记录。
- `db-reserve` 只写 Lystar 的受锁定预留文件，不向 Redis 写标记。
- `db-reassign` 只允许把已有项目预留迁到真实为空且未被预留的 DB；旧 DB 不执行清理，旧数据保持不变。
- 不默认执行 `FLUSHDB`、`FLUSHALL`、`KEYS` 或删除其它项目数据。
- Redis `CONFIG GET databases` 不可用时使用 profile 中的 `database_count`，并在结果中保留 warning。
- 业务 Skill 可以把脚手架中的连接信息或本地 profile 的 username/password 传给 `redisx`；命令输出、预留文件、日志和制品不得输出 password。
- 连接、扫描、预留和验证结果都使用 Lystar session snapshot 保存，不另建状态目录。
