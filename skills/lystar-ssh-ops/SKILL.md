---
name: lystar-ssh-ops
description: 连接 SSH、执行远程命令、通过 SFTP 传输文件、运行远程长任务，并查询、维护服务器资料。用户提到服务器、IP、域名、端口、Nginx、代理、隧道、部署、日志或远程故障排查时使用。
---

# LYStar SSH Ops

统一使用 PATH 中的 `sshx`。不同 Agent Harness 共用全局 host profile、连接状态、远程任务和服务器资料。

每次加载本 Skill，先执行下面命令；它每 24 小时最多联网一次，失败不阻断当前任务：

```bash
lystar-skill-update auto lystar-ssh-ops --quiet || true
```

`sshx` 启动时也会执行同样的检查。手工检查或更新：

```bash
lystar-skill-update check lystar-ssh-ops
lystar-skill-update update lystar-ssh-ops
```

## 服务器资料

本 Skill 目录下的 `servers/` 指向服务器资料根目录。首次安装只有空索引和模板。

1. 读取 `servers/README.md`，按名称、别名、IP、域名、端口、用途或 `sshx profile` 定位服务器。
2. 读取目标服务器的 `README.md`，再读取当前任务相关的服务文档。
3. 找不到资料时在 `servers/` 内使用 `rg`；仍找不到就标记为未记录，不猜测。
4. 地址、端口、进程、防火墙、证书、DNS 和版本依赖当前状态时，用 `sshx` 做只读核验。
5. 文档与实机不一致时以实机为准，并在完成远程变更后同步文档的当前状态和变更记录。

新增服务器或主题时使用 `servers/_templates/`。资料可能包含明文凭据，只能保存在本地受控目录或私有仓库；不要在普通回复、日志或命令输出中重复展示。

## SSH 与 SFTP

```bash
sshx open <别名> <user@host[:port]> [密码]
sshx <别名> "<命令>"
sshx exec --json <别名> "<命令>"
sshx ls <别名> <远端目录>
sshx cat <别名> <远端文件>
sshx put <别名> <本地路径> <远端路径>
sshx get <别名> <远端路径> <本地路径>
sshx run <别名> "<长任务命令>"
sshx job <别名> <job_id>
sshx wait <别名> <job_id>
sshx status [别名]
sshx close <别名>
sshx forget <别名>
sshx last [--summary|--json|--clear]
```

SSH 建连和普通远程命令默认超时均为 120 秒，可用 `--timeout` 覆盖。普通命令会按已保存 profile 自动建立或恢复连接；同一 `host + port + user` 的别名共用连接。

## 执行规则

- `exec` 和 `cat` 默认输出原文；`ls`、`status` 默认输出 CSV；需要完整结构时使用 `--json`。
- 用户给出服务器和凭据后可直接 `open`，密码允许明文保存在全局配置。
- 删除、覆盖、重启服务、安装脚本等远端写操作必须来自用户明确要求。
- 变更前核验目标服务、配置路径、监听端口、依赖、进程和健康状态，准备备份、语法检查、回退命令和验证入口。
- 长任务使用 `run`；日志优先用 `tail -n`、`journalctl -n --no-pager`、`rg` 或 `jq` 缩小结果。
- 上传前确认本地路径，下载后核对目标路径和返回的校验值。
- 完成后报告目标别名、动作、退出码、关键输出和实际验证；服务器事实变化时同步 `servers/` 文档。
