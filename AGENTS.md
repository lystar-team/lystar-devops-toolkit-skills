# LYStar DevOps Toolkit Skills Agent Guide

## 先确认事实源

- 本仓库是 Skill 源码和安装器仓库，不保存用户服务器、SSH 密码、数据库密码、私钥、Token、Cookie 或运行结果。
- 用户本地运行时的唯一根目录是 `$HOME/.lystar`；除 Harness 要求的 Skill 发现目录外，不要另建 `~/.local/share/agent-ops`、`~/.config/agent-ops` 或 `~/.local/state/agent-ops` 作为新路径。
- 服务器资料唯一入口是 `${LYSTAR_SERVER_HOME:-$LYSTAR_HOME/servers}`。先读根 `README.md`，再读服务器 `README.md`，最后只读当前任务相关的 `services/<service-id>.md` 或 `topics/<topic-id>.md`。
- Yean 本机可以让 `$HOME/.lystar/servers` 软链接到已有 Git 服务器资料库；普通用户使用安装器创建的真实目录。不要在各 Harness 的 Skill 目录创建服务器资料软链接。

## 安装指令

全量安装当前仓库的六个 Skill：

```bash
./install.sh --harness auto
```

固定安装一个 Skill：

```bash
./install-lystar-ssh-ops.sh --harness codex
```

其中 `ssh`、`db`、`host`、`deploy`、`backup`、`incident` 必须与对应安装脚本和 Skill 名称保持一致。安装完成后验证 `$HOME/.lystar/bin` 下的命令和 `$HOME/.lystar/servers/README.md`。

## 目录规则

标准布局如下：

```text
$HOME/.lystar/
├── bin/
├── runtime/
├── config/
├── state/
├── data/recipes/
└── servers/
    ├── README.md
    ├── _templates/
    └── <server-id>/
        ├── README.md
        ├── services/<service-id>.md
        └── topics/<topic-id>.md
```

服务器 ID、服务 ID、主题 ID 和 SSH profile 使用稳定的小写 ASCII 命名。默认 SSH profile 是 `<server-id>`，用途连接使用 `<server-id>-<purpose>`，跳板链使用 `<server-id>-via-<jump-id>`。文档中的 profile 必须和 `sshx` 中的 alias 一致。

## 修改和验证

- 修改路径规则时同时检查 Shell 入口、Python 运行时、更新器、独立安装包和 README。
- 修改服务器资料模板时检查必需标题、目录链接、profile 命名和凭据边界；不要批量覆盖已有 Git 资料的用户修改。
- 最小验证：`sh -n install*.sh scripts/*.sh bin/*.sh`、`python3 -m py_compile scripts/*.py runtime/agent-ops/scripts/*.py`、相关单测和安装/打包/更新测试。
- 旧数据迁移只复制缺失目标并保留旧目录。需要接入 Git 服务器资料时使用：

```bash
lystar-migrate --server-link /绝对路径/服务器资料仓库
```
