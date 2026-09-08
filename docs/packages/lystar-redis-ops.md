# LYStar Redis Ops

`lystar-redis-ops` 提供 `redisx`，使用 Redis profile 或业务运行时连接参数检查 Redis、扫描空闲 DB、登记 DB 预留和执行验证。

## 安装后路径

- 命令：`${LYSTAR_HOME:-$HOME/.lystar}/bin/redisx`
- Profile：`${LYSTAR_HOME:-$HOME/.lystar}/config/redis-ops.toml`
- 预留：`${LYSTAR_HOME:-$HOME/.lystar}/config/redis-reservations.toml`
- 结果：复用 Lystar 的 session snapshot

## 凭据

Redis profile 可以直接保存 username/password，配置文件保持 `0600`；命令输出、预留文件、日志和制品不输出 password。

## 命令

```bash
redisx doctor --profile project-redis --json
redisx db-list --profile project-redis --json
redisx db-find-free --profile project-redis --json
redisx db-reserve --profile project-redis --project-id demo --json
redisx db-reassign --profile project-redis --project-id demo --start 40 --confirm --json
redisx db-verify --profile project-redis --database 3 --json
```

空闲 DB 必须同时满足 `DBSIZE = 0` 且没有本地预留记录。`db-reassign` 只迁移本地预留指针，目标 DB 必须重新检查为空；旧 DB 的数据不清理。`redisx` 不执行 `FLUSHDB` 或 `FLUSHALL`。
