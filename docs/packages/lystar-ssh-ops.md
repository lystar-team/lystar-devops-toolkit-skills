# lystar-ssh-ops

面向 OpenCode、Codex、Claude Code、Pi 等 Agent Harness 的 SSH/SFTP 与服务器资料管理 Skill，附带 `sshx` 命令。服务器资料直接作为 Skill 的 `servers/` 子目录使用，不再需要独立的 server Skill。

## 安装

```bash
./install.sh
```

安装器默认探测当前用户已安装的 Harness。也可以明确选择一个或多个：

```bash
./install.sh --harness claude
./install.sh --harness opencode,codex,pi
./install.sh --skills-home /path/to/compatible/skills
./install.sh --server-home /private/server-list
```

需要 Unix 兼容系统、Python 3.11+ 和联网安装 Paramiko。SSH 建连、同步远程操作和普通远程命令默认超时为 120 秒。

多级跳板机通过 `--jump` 绑定，跳板别名可以递归引用更前一级跳板：

```bash
sshx open server-1 ops@bastion.example
sshx open server-2 ops@10.0.0.2 --jump server-1
sshx open server-3 root@10.0.0.3 --jump server-2
sshx server-3 "hostname"
```

## 更新

`sshx` 每 24 小时最多自动检查一次，有新版时校验 SHA-256 后更新。也可手工运行：

```bash
lystar-skill-update check lystar-ssh-ops
lystar-skill-update update lystar-ssh-ops
```

## 长任务与端口转发

```bash
sshx run prod "long-running-command"
sshx jobs prod
sshx job prod <job_id>
sshx cancel prod <job_id>
sshx wait prod <job_id> --follow

sshx forward open prod 15432 db.internal 5432
sshx forward list prod
sshx forward status prod <forward_id>
sshx forward close prod <forward_id>
```

任务列表和单任务查询沿用远端 `~/.agent-ops/jobs/<job_id>`；`--follow` 按日志偏移增量输出 stdout/stderr 和状态变化。端口转发由已有 SSH daemon 管理，关闭 profile 时一并回收。
