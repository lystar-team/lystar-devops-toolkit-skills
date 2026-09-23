# lystar-web-restore

从公开网页、Preview、iframe、Bundle、CSS、源码和资源提取运行时证据，生成可打开的 HTML/CSS 镜像。只处理公开内容，不把镜像等同于原作者源码或业务功能。

## 安装

从独立 Release 包解压后运行：

```bash
./install.sh --harness codex
```

从源码仓库运行：

```bash
./install-lystar-web-restore.sh --harness codex
```

安装器把 Playwright 放到 `$LYSTAR_HOME/runtime/vendor`。运行 Skill 内脚本前，按 `skills/lystar-web-restore/SKILL.md` 设置 `PYTHONPATH` 和 `LYSTAR_WEB_RESTORE_ROOT`。浏览器需提供可连接的 CDP 地址；此 Skill 不增加 PATH 命令。
