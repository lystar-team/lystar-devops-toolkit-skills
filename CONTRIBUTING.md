# Contributing

## 开发环境

需要 Python 3.11+、`pip`、`zip`、`unzip` 和 `sha256sum`。

```bash
python3 -m pip install -r requirements/all.txt
```

## 修改原则

- `lystar-ssh-ops` 和 `lystar-db-ops` 必须保持可单独安装。
- 不提交真实服务器资料、数据库连接、密码、私钥、Token、Cookie 或运行结果。
- 用户配置和状态目录要向后兼容；改名不能清空现有 `agent-ops` 数据。
- 新增 Harness 时，先确认其官方全局 Skill 目录，再补探测、README 和安装测试。
- 修改 SSH 默认行为时，同时更新 CLI 测试和 Skill 文档。
- 修改更新协议时，同时更新 `tests/test_update.sh`。

## 验证

```bash
python3 -m unittest discover runtime/agent-ops/tests
sh tests/test_install.sh
sh tests/test_packages.sh
sh tests/test_update.sh
```

## 发布

1. 更新 `VERSION`，格式为 `MAJOR.MINOR.PATCH`。
2. 确认所有测试通过。
3. 提交并推送代码。
4. 创建并推送同版本 tag，例如 `v0.2.0`。
5. GitHub Actions 自动构建 Release 资产和 `SHA256SUMS`。

不要手工上传未经过工作流测试的 ZIP。
