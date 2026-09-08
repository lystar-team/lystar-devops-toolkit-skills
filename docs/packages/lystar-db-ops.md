# lystar-db-ops

面向 OpenCode、Codex、Claude Code、Pi 等 Agent Harness 的多数据库操作 Skill，附带 `dbx` 命令。支持 MySQL、MariaDB 和 PostgreSQL 的数据源发现、查询、执行、导入和导出。

## 安装

```bash
./install.sh
```

安装器默认探测当前用户已安装的 Harness。也可以明确选择一个或多个：

```bash
./install.sh --harness codex
./install.sh --harness codex,pi
./install.sh --skills-home /path/to/compatible/skills
```

需要 Unix 兼容系统、Python 3.11+ 和联网安装数据库驱动。

## 更新

`dbx` 每 24 小时最多自动检查一次，有新版时校验 SHA-256 后更新。也可手工运行：

```bash
lystar-skill-update check lystar-db-ops
lystar-skill-update update lystar-db-ops
```

## 事务与数据源版本

```bash
dbx source show prod
dbx exec "UPDATE demo SET enabled = 1" --transaction commit
dbx exec "UPDATE demo SET enabled = 1" --transaction rollback
dbx import backup.sql --transaction commit
dbx query --source admin-source --database target_db --json "SELECT 1"
dbx exec --source admin-source --database target_db "CREATE TABLE ..."
dbx import --source admin-source --database target_db backup.sql --transaction commit
```

数据源统一使用 `db_type`（`mysql`、`mariadb`、`postgresql`），并保留 `engine` 兼容字段。`source show` 和实际连接会探测服务端 `version` 与 `version_parts`；连接失败、版本未探测和版本查询失败分别返回明确状态。`exec`/`import` 默认提交，`rollback` 只作用于当前命令的单次事务。

`--database` 只覆盖当前命令的连接目标，不修改已保存 profile，适合使用管理员连接创建或初始化指定项目库。

`dbx source remove <alias|profile> --json` 会先检查全局注册表和项目默认 source。profile 仍有其它 alias 时，deployment 和数据库 backup asset 会同步到存活 alias；删除最后一个 alias/profile 时必须显式使用 `--confirm`，确认后相关 deployment/backup asset 标记为 `orphaned`，失效的项目默认 source/binding 会被清理。旧 `databases.toml`、密码和备份文件不会被静默改写或删除。
