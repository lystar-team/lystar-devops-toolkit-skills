---
name: lystar-db-ops
description: 发现、选择、创建和使用 MySQL、MariaDB、PostgreSQL 数据源，执行查询、SQL、导入导出或核验真实数据库时使用。
---

# LYStar DB Ops

统一使用 PATH 中的 `dbx`。当前目录默认作为项目根目录，不同 Agent Harness 共用全局数据库 profile 与项目默认选择。

每次加载本 Skill，先执行下面命令；它每 24 小时最多联网一次，失败不阻断当前任务：

```bash
lystar-skill-update auto lystar-db-ops --quiet || true
```

`dbx` 启动时也会执行同样的检查。手工检查或更新：

```bash
lystar-skill-update check lystar-db-ops
lystar-skill-update update lystar-db-ops
```

## 数据源

```bash
dbx sources
dbx use <别名|profile|发现来源>
dbx use --clear
dbx source add <别名> --type <mysql|mariadb|postgresql> --host <host> --port <port> --database <库名> --user <用户> --password <密码>
dbx source add <别名> --url "<数据库URL>"
dbx source list
dbx source show <别名|profile>
dbx source remove <别名|profile> [--confirm] [--json]
```

同一 `db_type + host + port + database + user` 只保存一个 profile，多个别名可以复用。`db_type` 规范化为 `mysql`、`mariadb` 或 `postgresql`；旧 profile 的 `engine` 字段继续兼容。自动发现的数据源会写入全局配置，项目保存发现来源绑定和默认 profile。

`source remove` 删除 alias/profile 前会检查全局注册表和项目默认 source。profile 仍有其它 alias 时，会把 deployment 的数据库引用和数据库 backup asset 同步到存活 alias；删除最后一个 alias/profile 时，如果存在 deployment、service、backup asset 或项目绑定，默认阻断并返回影响清单，只有显式 `--confirm` 才会把 deployment/backup asset 标记为 `orphaned`，同时清理已失效的项目默认 source/binding。不会删除备份文件，不改其它 source 的密码或旧配置字段。

`source show` 会在成功连接后执行对应数据库的版本查询，并保存 `version`、可比较的 `version_parts`、`version_status` 和 `connection_status`。发现尚未连接的数据源时，版本状态为 `not_probed`；连接失败为 `connection_status=unavailable`，版本查询失败为 `version_status=query_failed`，不伪造版本号。

## 操作

```bash
dbx query "<只读SQL>"
dbx query --source <数据源> "<只读SQL>"
dbx query --json "<需要脚本解析或严格类型的只读SQL>"
dbx exec "<DDL或DML>" [--transaction commit|rollback]
dbx import <SQL文件> [--transaction commit|rollback]
dbx export <输出文件>
dbx last [--summary|--json|--clear]
```

当前项目发现一个数据源时直接使用；发现多个且没有默认值时，先执行 `dbx sources`，再执行一次 `dbx use`。禁止静默选择第一个数据源。

## 规则

- 查询成功默认输出 CSV；需要脚本解析或严格类型时使用 `--json`。
- `exec` 和 `import` 默认单次事务自动提交；`--transaction rollback` 会执行 SQL 后明确回滚。结果包含 `transaction.state`（`committed`、`rolled_back`、`failed` 或 `rollback_failed`）及当前事务状态。本阶段不建立跨命令持久事务会话。
- 查询、执行、导入、导出结果统一提供 `datasource.db_type`、`datasource.engine`（兼容字段）、`datasource.version`、`datasource.version_parts` 和连接/版本状态。
- 用户问上次结果时，当前上下文有完整结果就直接使用；缺少细节时调用 `last --summary`，确需正文再调用 `last`。
- `query` 用于只读查询，默认返回 20 行；需要精确总数时单独执行 `COUNT(*)`。
- `exec`、`import` 和数据库写入必须来自用户明确要求；执行后使用 `query` 回查。
- 删除、批量更新、资金、权限和历史数据修正先说明影响与回滚方式，再执行。
- 结果区分只读查询、SQL 编写、SQL 审查和已实际执行，未执行不能宣称已落库。
