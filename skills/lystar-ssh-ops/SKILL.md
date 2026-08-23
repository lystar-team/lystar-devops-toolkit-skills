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
sshx open <别名> <user@host[:port]> [密码] --jump <跳板别名> [--jump <更前一级跳板别名>]
sshx <别名> "<命令>"
sshx exec --json <别名> "<命令>"
sshx ls <别名> <远端目录>
sshx cat <别名> <远端文件>
sshx put <别名> <本地路径> <远端路径> [--resume] [--chunk-size <字节数>]
sshx get <别名> <远端路径> <本地路径>
sshx run <别名> "<长任务命令>"
sshx job <别名> <job_id>
sshx jobs <别名>
sshx cancel <别名> <job_id>
sshx wait <别名> <job_id>
sshx wait <别名> <job_id> --follow
sshx forward open <别名> <本地端口> <远端主机> <远端端口>
sshx forward list <别名>
sshx forward status <别名> <forward_id>
sshx forward close <别名> <forward_id>
sshx status [别名]
sshx close <别名>
sshx forget <别名> [--confirm] [--json]
sshx last [--summary|--json|--clear]
```

SSH 建连、同步远程操作和普通远程命令默认超时均为 120 秒，可用 `--timeout` 覆盖。普通命令会按已保存 profile 自动建立或恢复连接；同一 `host + port + user + 跳板链` 的别名共用连接。

`sshx put --resume` 只对单文件上传启用断点续传：如果远端已有同名部分文件，会从已有字节偏移继续；远端文件大小相同但 SHA-256 不一致时会重新上传。`--chunk-size` 控制单次 SFTP 写入大小，默认 4 MiB。目录上传保持原有递归上传行为。

`forget` 删除 alias 前会检查全局注册表 `ops.toml`。同一 profile 仍有其它 alias 时，会把 deployment 和 file backup asset 的引用同步到存活 alias；删除最后一个 alias/profile 时，如果存在 deployment、service 或 backup asset 影响，默认阻断并返回清单，只有显式 `--confirm` 才会把 deployment/backup asset 标记为 `orphaned`。该操作不删除远端服务、目录或备份文件，也不改 SSH 密码。

跳板机支持多级。每一级跳板先用 `sshx open` 保存自己的凭据，再通过 `--jump` 绑定；跳板别名可以继续带自己的 `--jump`：

```bash
sshx open server-1 ops@bastion.example
sshx open server-2 ops@10.0.0.2 --jump server-1
sshx open server-3 root@10.0.0.3 --jump server-2
sshx server-3 "hostname"
```

上例的实际链路是本机 → `server-1` → `server-2` → `server-3`。跳板机只转发 SSH 通道，命令和 SFTP 操作仍在最终目标机执行；每一跳必须允许 SSH TCP 转发，并能访问下一跳的 SSH 端口。

## 执行规则

- `exec` 和 `cat` 默认输出原文；`ls`、`status` 默认输出 CSV；需要完整结构时使用 `--json`。
- 用户给出服务器和凭据后可直接 `open`，密码允许明文保存在全局配置。
- 删除、覆盖、重启服务、安装脚本等远端写操作必须来自用户明确要求。
- 变更前核验目标服务、配置路径、监听端口、依赖、进程和健康状态，准备备份、语法检查、回退命令和验证入口。
- 长任务使用 `run`；日志优先用 `tail -n`、`journalctl -n --no-pager`、`rg` 或 `jq` 缩小结果。
- `jobs` 按 SSH profile 列出远端任务，展示任务 ID、命令、状态、PID、创建/结束时间和 stdout/stderr 日志位置；`job` 继续查看单个任务的日志尾部。`cancel` 对运行中的任务发送终止信号，并把远端状态标记为 `cancelled`；已结束、已取消和不存在的任务返回稳定状态，不会重建第二套任务目录。
- `wait --follow`（`job --follow` 也支持）按 stdout/stderr 文件偏移增量输出，状态变化使用 `# job=... state=...`，两个输出流分别使用 `# stdout` 和 `# stderr`；默认非 follow 模式仍一次性输出日志尾部。使用 `--json` 时 follow 输出为 JSON Lines 事件。
- `forward open` 在当前 SSH daemon 和多级跳板链上创建本地端口转发；不传本地端口时使用系统分配的空闲端口。`forward list/status/close` 查看、查询和关闭转发。连接异常会记录在转发状态中，关闭 profile 时所有转发随 daemon 一起回收。
- 上传前确认本地路径，下载后核对目标路径和返回的校验值。
- 完成后报告目标别名、动作、退出码、关键输出和实际验证；服务器事实变化时同步 `servers/` 文档。
