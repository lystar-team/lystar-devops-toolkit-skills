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

需要 Unix 兼容系统、Python 3.11+ 和联网安装 Paramiko。SSH 建连和普通远程命令默认超时为 120 秒。

## 更新

`sshx` 每 24 小时最多自动检查一次，有新版时校验 SHA-256 后更新。也可手工运行：

```bash
lystar-skill-update check lystar-ssh-ops
lystar-skill-update update lystar-ssh-ops
```
