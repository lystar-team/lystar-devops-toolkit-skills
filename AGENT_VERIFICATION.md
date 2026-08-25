# Verification

- 环境：Python 3.11+，Unix shell，zip/unzip，sha256sum。
- 静态检查：`sh -n install*.sh scripts/*.sh`，`python3 -m py_compile scripts/*.py runtime/agent-ops/scripts/*.py runtime/agent-ops/tests/*.py`。
- 运行时测试：`python3 -m unittest discover runtime/agent-ops/tests`。
- 注册表专项测试：`python3 -m unittest runtime.agent-ops.tests.test_registry_store`，覆盖 ops.toml CRUD、revision 恢复、未知字段和旧配置兼容。
- SSH/DB 引用专项测试：`python3 -m unittest runtime.agent-ops.tests.test_agent_ops.RegistryReferenceRemovalTest`，覆盖同 profile alias/source 同步、删除前影响清单、`--confirm` orphaned 标记和项目默认 source/binding 清理。
- hostx fake-sshx 测试：`python3 -m unittest runtime.agent-ops.tests.test_host_ops`，覆盖 facts/service/process/logs/ports/disk/health 和 `service inspect` 的 unit、drop-in、进程、端口、路径事实。
- deployx fake-sshx/hostx 测试：`python3 -m unittest runtime.agent-ops.tests.test_deploy_ops`，覆盖 plan/status/history、apply 阶段、断点续传参数、上传/暂存失败收尾、健康失败回退、rollback，既有服务 inspect/adopt、legacy 单目录迁移 plan、systemd/路径/端口 drift，以及 `doctor` 的备份覆盖缺失/通过。
- deployx 注册与新服务 draft 测试：同一专项测试覆盖项目/服务/环境注册、内置/托管 recipe、只读 service plan、复杂注册表 TOML 落盘、draft create、既有目录阻断和 adopted 基线。
- backupx fake-dbx/sshx/hostx 测试：`python3 -m unittest discover runtime/agent-ops/tests -p 'test_backup_ops.py'`，覆盖仓库/默认仓库、数据库/文件资产绑定与 manifest、数据库/文件 create、list/inspect/verify、restore、restore-verify、远端任务/下载/归档失败和 prune。
- incidentx fake CLI/注册表测试：`python3 -m unittest discover runtime/agent-ops/tests -p 'test_incident_ops.py'`，覆盖 host/db/deploy/backup/SSH 与全局注册表对象/revision/relation 证据采集、部分失败、`--require`、离线 show/timeline/verify、注册表元数据一致性和证据篡改。
- 安装测试：`sh tests/test_install.sh`。
- 打包测试：`sh tests/test_packages.sh`。
- 更新测试：`sh tests/test_update.sh`。
- 迁移验证：在临时 HOME 中准备旧 `agent-ops` 配置、状态和服务器资料，运行 `lystar-migrate`，确认只复制缺失目标、旧目录保留，`--server-link` 只创建显式服务器资料软链接。
- 构建：`./scripts/build-packages.sh`，产物位于 `dist/`。
- 已知限制：测试使用本地 Mock SSH 服务、fake sshx 和伪造 Release，不连接真实服务器或数据库；安装/打包测试会清空外部 Harness 路径变量以避免污染。
