# 目录站点批量测试

`catalog_smoke_test.py` 用于验证一个工具目录中的所有公开链接是否能被浏览器打开，以及页面属于哪种还原能力。默认读取 `designengineer.tools` 首页，不需要把站点清单复制到 Skill 中。

将 `LYSTAR_WEB_RESTORE_ROOT` 设为当前 Skill 安装目录后运行：

```bash
python3 "$LYSTAR_WEB_RESTORE_ROOT/scripts/catalog_smoke_test.py" \
  --catalog-url 'https://designengineer.tools/' \
  --out /tmp/lystar-web-restore-catalog-report.json
```

可选参数：

- `--concurrency`：并发页面数，默认 6；
- `--timeout-ms`：单页导航超时，默认 15000；
- `--wait-ms`：DOM 加载后等待时间，默认 800；
- `--executable-path`：Chrome/Chromium 路径，默认 `/usr/bin/google-chrome`；
- `--screenshots-dir`：保存每个站点首屏截图，默认不保存。

每个站点记录：

- HTTP 状态、最终 URL、页面标题和浏览器错误；
- DOM 元素数、文本长度、脚本、样式表和可读 CSSOM 数量；
- iframe、SVG、Canvas、WebGL 和主题入口；
- `full-dom-css`：可直接提取 DOM/CSS；
- `partial-dom`：页面可读，但 CSS 或资源证据不完整；
- `runtime-hybrid`：包含 iframe、Canvas、WebGL 或其他运行时画面；
- `blocked`：浏览器收到安全验证、访问拒绝或挑战页；
- `unavailable`：页面无法在当前环境建立可用浏览器上下文。

这个脚本是目录级 smoke 测试，不等同于对每个第三方站点做像素级视觉验收。像素级验收只对实际要迁移的目标组件执行；Canvas、WebGL、3D 和登录后页面按运行时证据和可用性报告验收。
