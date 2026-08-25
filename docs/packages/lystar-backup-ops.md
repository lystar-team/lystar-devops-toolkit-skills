# lystar-backup-ops

面向 OpenCode、Codex、Claude Code、Pi 等 Agent Harness 的本地数据库与文件备份恢复 Skill，附带 `backupx` 命令。

## 安装

数据库备份依赖 `dbx`，文件备份依赖 `sshx`；建议先安装对应 Skill：

```bash
./install-lystar-db-ops.sh --harness codex
./install-lystar-ssh-ops.sh --harness codex
./install-lystar-backup-ops.sh --harness codex
```

本包安装器只安装 `backupx`，不会复制或替换 `dbx`、`sshx`。

## 使用

```bash
backupx db create --source prod --repository ./backups
backupx file create prod /srv/web/uploads --repository ./backups
backupx list --repository ./backups --json
backupx verify db-20260823T120000Z-ab12cd34 --repository ./backups
backupx verify db-20260823T120000Z-ab12cd34 --repository ./backups --read-only
backupx restore db-20260823T120000Z-ab12cd34 --repository ./backups --source staging
backupx restore file-20260823T120000Z-ab12cd34 --repository ./backups \
  --alias staging --target-dir /srv/web/uploads
backupx prune --repository ./backups --keep-last 5 --dry-run
```

数据库 create 调用 `dbx export`，并把数据库类型、版本、来源、大小和 SHA-256 写入 manifest。文件 create 通过 `sshx run/wait` 在远端生成 tar.gz，再用 `sshx get` 下载。恢复前自动校验本地备份；数据库恢复明确提交事务，文件恢复明确写入用户指定的绝对目录。`verify --read-only` 只返回校验结果，不更新 manifest 状态，供只读诊断采集使用。

仓库和备份资产统一登记在 `${LYSTAR_HOME:-$HOME/.lystar}/config/ops.toml`。省略 `--repository` 时使用唯一登记的默认仓库；旧的显式 `--repository` 参数继续有效：

```bash
backupx repository register local-prod --path /var/lib/agent-ops/backups --default
backupx asset register mall-db --kind db --project mall-admin --service api \
  --environment prod --repository local-prod --source prod-db
backupx db create --asset-id mall-db
```

资产只保存 project/service/environment、来源和仓库引用，不复制 SSH 或数据库密码；带资产创建的 manifest 会记录资产 ID。

`restore-verify` 只接受用户提供的只读 SQL、文件路径或 `hostx health` 检查，不猜测表名、文件名或业务健康口径。`prune` 默认 dry-run，真正删除必须传入 `--confirm`。
