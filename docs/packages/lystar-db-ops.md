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
