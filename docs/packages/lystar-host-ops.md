# lystar-host-ops

面向 OpenCode、Codex、Claude Code、Pi 等 Agent Harness 的只读主机事实与健康检查 Skill，附带 `hostx` 命令。

## 安装

`hostx` 依赖已安装的 `sshx`，建议先安装 SSH Skill，再安装本包：

```bash
./install.sh --harness codex
```

本包安装器不会复制或替换 `sshx`；如果从源码仓库安装，可先执行 `./install-lystar-ssh-ops.sh --harness codex`，再执行 `./install-lystar-host-ops.sh --harness codex`。

## 使用

```bash
hostx facts prod
hostx service prod status nginx
hostx service prod inspect nginx.service
hostx process prod list --pattern python
hostx logs prod nginx --lines 100
hostx logs prod /var/log/example.log --lines 50
hostx ports prod
hostx disk prod /
hostx health prod --check facts,disk:/,service:nginx
hostx facts prod --json
```

所有采集通过现有 `sshx exec --json` 完成，不执行远端写操作。连接失败返回 `connection_status=unavailable`；这与主机健康状态分开表达。

`service inspect` 用于既有服务接管前的只读事实采集：返回 systemd unit/drop-in 内容、启动命令、运行用户、工作目录、环境文件、依赖、主进程和启动命令、按服务进程过滤的监听端口，以及从 unit、启动命令和 systemd 目录属性中明确观察到的 release/config/data/log 路径。无法确认的事实会返回 warning，不会把目录名称猜成版本或环境。
