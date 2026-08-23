# lystar-incident-ops

面向 OpenCode、Codex、Claude Code、Pi 等 Agent Harness 的只读事件证据与诊断包 Skill，附带 `incidentx` 命令。

## 安装

`incidentx` 通过外部 CLI 采集证据。按实际范围先安装对应 Skill，再安装本包：

```bash
./install-lystar-host-ops.sh --harness codex
./install-lystar-db-ops.sh --harness codex
./install-lystar-deploy-ops.sh --harness codex
./install-lystar-backup-ops.sh --harness codex
./install-lystar-incident-ops.sh --harness codex
```

本包安装器只安装 `incidentx`，不会复制或替换其它 Skill 的命令和运行时。

## 使用

```bash
incidentx collect case-20260823 \
  --out ./incident-bundles/case-20260823 \
  --host prod \
  --source prod-db \
  --app web \
  --release-root /srv/apps \
  --service web.service \
  --project-id mall-admin \
  --service-id api \
  --deployment-id mall-admin/api/prod \
  --backup-asset-id mall-db \
  --require host,db,deploy,registry

incidentx show ./incident-bundles/case-20260823
incidentx timeline ./incident-bundles/case-20260823
incidentx verify ./incident-bundles/case-20260823
```

`collect` 只执行 host/SSH/数据库/部署/备份和全局注册表的只读路径。数据库使用 `dbx source list`，部署使用 `deployx status`，备份使用 `backupx inspect` 和 `backupx verify --read-only`；提供 `--project-id`、`--service-id`、`--deployment-id` 或 `--backup-asset-id` 时，额外读取 `ops.toml` 中的注册表对象、关系和 revision。不会执行 SQL 写操作、服务重启、发布切换、备份恢复或保留清理。

bundle 可脱离现场使用，包含 `manifest.json`、按组件分目录的结构化证据、`timeline.jsonl` 和 `checksums.sha256`。注册表采集会把 `registry_revision`、对象选择和关系计数写入 manifest/timeline；`verify` 会发现证据缺失、文件大小变化、SHA-256 不匹配、注册表元数据不一致和校验清单不一致。
