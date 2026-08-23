---
name: lystar-host-ops
description: 通过现有 sshx 采集主机事实、服务、进程、日志、监听端口、磁盘和只读健康状态。
---

# LYStar Host Ops

统一使用 PATH 中的 `hostx`。`hostx` 不保存 SSH 凭据、不建立第二套连接，也不执行重启、杀进程、删日志或修改配置；它通过已有 `sshx exec --json` 复用 SSH profile 和多级跳板链。

使用前需要先安装并配置 `lystar-ssh-ops`，确认 `sshx` 可以连接目标主机：

```bash
sshx status
sshx open prod root@example.com
```

## 命令

```bash
hostx facts prod
hostx service prod status nginx
hostx service prod inspect nginx.service
hostx process prod list
hostx process prod list --pattern python
hostx process prod show 1234
hostx logs prod nginx --lines 100
hostx logs prod /var/log/example.log --lines 50
hostx logs prod nginx --lines 50 --since '1 hour ago'
hostx ports prod
hostx disk prod /
hostx disk prod / /var
hostx health prod
hostx health prod --check facts,disk:/,ports
hostx health prod --check service:nginx --check process:nginx
```

所有命令支持 `--json`。JSON 结果统一包含 `schema_version`、`kind`、`target.alias`、`collected_at`、`status` 和 `connection_status`。`health` 额外返回 `started_at`、`finished_at`、`checks`，整体状态只有 `pass`、`warn`、`fail`、`unknown`。

状态含义：

- `ok`：远端命令执行成功，结果完整。
- `partial`：主机可连接，但输出被 `sshx` 截断或部分字段无法解析。
- `failed`：主机可连接，但采集命令失败或目标服务/进程不存在。
- `unknown`：目标命令缺失，无法判断该项。
- `unavailable`：SSH 连接、`sshx` 命令或调用过程不可用；这不等于主机不健康。

`health` 将连接不可用转换为 `unknown`，不会把 SSH 故障伪装成主机 `fail`。默认只检查 `facts` 和根文件系统 `disk:/`；服务、日志和进程必须通过 `--check` 明确指定。

`service inspect` 是只读的接管基线，采集 systemd unit 文件和 drop-in、`ExecStart`、`User`、`WorkingDirectory`、`EnvironmentFile`、依赖、主进程和启动命令、按服务进程过滤的监听端口，以及能够从这些事实明确观察到的 release/config/data/log 路径。unit 或 drop-in 内容无法读取、`ss` 不可用或端口无法按服务过滤时，结果会保留 warning 和明确的 partial/unknown 状态，不会推断未验证的路径。

## 采集边界

- `facts` 使用 `hostname`、`uname`、`/proc/uptime` 和 `/proc/loadavg`；缺失 `/proc` 数据会保留已采集事实并给出 warning。
- `service` 第一版使用目标机的 `systemctl`；没有该命令时返回能力缺失，不猜测其它服务管理器。
- `process` 使用 `ps`，`ports` 使用 `ss -H -ltnup`；命令不可用会明确返回 `unknown`。
- 绝对路径日志使用 `tail`，其它来源按 systemd unit 使用 `journalctl`。
- `disk` 使用 POSIX 风格的 `df -Pk`，返回字节数和使用百分比。
- `service inspect` 只使用 `systemctl show`、unit/drop-in 文件、`ps`、`/proc`、cgroup 和 `ss` 采集事实，不执行重启、写配置、切换版本或修改远端文件。

结果只描述采集时观察到的事实，不把采集时间当成业务事件发生时间。真实服务器的日志、端口、服务状态和进程结果需要由 `hostx --json` 或人类输出现场核验。
绝对路径日志支持 `--lines`；`--since` 只适用于 systemd unit 日志，文件日志不会猜测时间格式。
