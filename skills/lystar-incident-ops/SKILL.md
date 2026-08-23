---
name: lystar-incident-ops
description: 通过现有 hostx、sshx、dbx、deployx、backupx 和全局注册表采集只读事件证据，生成可校验诊断包和时间线。
---

# LYStar Incident Ops

统一使用 PATH 中的 `incidentx`。它只按用户明确给出的范围采集证据，不修复故障、不重启服务、不切换发布版本、不恢复备份，也不把采集顺序推断成因果关系。

`incidentx` 通过外部 CLI 的结构化结果和已安装运行时中的 `registry_store.py` 工作，不复制 `sshx`、`dbx`、`hostx`、`deployx` 或 `backupx` 的内部连接和业务逻辑。使用前只安装本 Skill 不够，实际采集哪个组件就要确保对应 CLI 已安装并可用；注册表采集使用全局 `ops.toml`，不读取 SSH/数据库凭据。

## 命令

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
  --log-source nginx \
  --since '2 hours ago' \
  --require host,db,deploy,registry

incidentx show ./incident-bundles/case-20260823
incidentx timeline ./incident-bundles/case-20260823
incidentx verify ./incident-bundles/case-20260823
```

`collect` 至少需要提供 `--host`、`--source`、`--app`、`--backup-id` 或注册表对象选择之一。参数与采集范围对应：

- `--host` 采集 `hostx health` 和 `sshx status`；`--service`、`--log-source`、`--since` 可补充主机服务/日志事实。
- `--source` 只读调用 `dbx source list`，按 alias 或 profile 选择目标，不执行 SQL，不探测或修改数据库。
- `--app` 必须同时提供 `--host` 和显式绝对 `--release-root`，只调用 `deployx status`。
- `--backup-id` 必须同时提供 `--repository`，只调用 `backupx inspect` 和 `backupx verify --read-only`。
- `--project-id`、`--service-id`、`--deployment-id` 和 `--backup-asset-id` 触发 `registry` collector；它读取注册表 revision、请求对象及其 project/service/deployment/backup asset/relation 关系。`--service-id` 是注册表服务 ID，和主机检查用的 `--service` unit 参数不同。

`--require` 可重复使用，也可以写成逗号分隔的 `host`、`ssh`、`db`、`deploy`、`backup`、`registry` 或 `all`。非必需 collector 失败时 bundle 仍会完成并返回 `partial`；必需 collector 失败时返回 `failed`，但仍保留已采集证据。

## Bundle 布局

```text
<bundle>/
├── manifest.json
├── checksums.sha256
├── timeline.jsonl
└── evidence/
    ├── host/host-1.json
    ├── ssh/ssh-1.json
    ├── db/db-1.json
    ├── deploy/deploy-1.json
    ├── backup/backup-1.json
    └── registry/registry-1.json
```

按请求组件生成证据目录；使用注册表对象选择时才会出现 `registry/registry-1.json`。每个证据文件都保留 collector 状态、目标、操作、采集时间、来源和结构化 JSON。注册表证据额外保留 `registry_revision`、更新时间、对象计数和 relation 结果；`manifest.json` 同步记录 revision、选择范围和 registry 证据 ID。`timeline.jsonl` 会保留对应 revision 和选择范围。所有证据文件都记录大小与 SHA-256，采集失败也会落一条证据状态，不静默丢失。

## 时间线与校验

`timeline` 同时保留：

- `event_time`：原始结果明确提供的业务/操作时间；没有就为空。
- `observed_at`：incidentx 读取外部结果的时间。
- `time_basis`：明确当前排序使用 `event_time` 还是 `observed_at`。
- `source`、`kind`、`summary` 和 `evidence_id`：可回到原始证据文件。

没有原始事件时间时不会把采集时间伪装成事件发生时间。`verify` 完全离线检查 manifest、证据文件、timeline 和 `checksums.sha256`，不要求现场主机、数据库或备份仓库仍在线。

## 采集边界

- 不扫描 home 目录，不读取凭据文件，不把任意远端目录打包进 bundle。
- 注册表采集只读取全局 `${XDG_CONFIG_HOME:-~/.config}/agent-ops/ops.toml` 及其恢复逻辑，不修改 `ops.toml`、旧 SSH/数据库配置或密码；注册表不可读或请求对象不存在时保留明确失败状态。
- 不调用 `deployx apply`/`rollback`、`backupx restore`/`prune`、服务重启或 SQL 写操作。
- 依赖不可用、连接失败、返回非 JSON 和部分 collector 失败都会保留明确状态。
- bundle 输出目录必须为空或不存在，避免覆盖已有诊断现场。

验证使用临时目录和 fake CLI，不连接真实服务器、数据库或生产备份仓库。
