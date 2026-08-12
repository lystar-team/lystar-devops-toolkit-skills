# LYStar 服务器资料库

这是 `lystar-ssh-ops` 使用的服务器事实目录。安装器会把它链接到每个已选 Harness 的 `lystar-ssh-ops/servers`。

## 目录约定

```text
servers/
├── README.md
├── _templates/
│   ├── server.md
│   └── topic.md
└── <server-name>/
    ├── README.md
    └── <service-or-topic>.md
```

根 `README.md` 维护服务器索引；每台服务器的 `README.md` 维护整机事实、SSH profile 和主题索引；服务文档维护当前状态、日常命令和变更记录。

## 安全边界

这里可能保存明文 SSH 或数据库凭据。只放在本地受控目录或私有仓库，不要提交到本公开仓库，不要在普通回复和日志中展示凭据。
