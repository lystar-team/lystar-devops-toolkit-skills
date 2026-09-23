# lystar-ui-restore

把截图、设计稿或运行页面还原成可检查的 UI，并记录几何、差异、补丁和验收证据。Web/H5 使用 Agent-Browser；小程序和原生 App 需要项目 CaptureAdapter。

## 安装

从独立 Release 包解压后运行：

```bash
./install.sh --harness codex
```

从源码仓库运行：

```bash
./install-lystar-ui-restore.sh --harness codex
```

安装器把 Skill 所需 Python 依赖放到 `$LYSTAR_HOME/runtime/vendor`。运行 Skill 内脚本前，按 `skills/lystar-ui-restore/SKILL.md` 设置 `PYTHONPATH` 和 `LYSTAR_UI_RESTORE_ROOT`。此 Skill 不增加 PATH 命令。
