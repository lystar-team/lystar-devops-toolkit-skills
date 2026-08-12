# Verification

- 环境：Python 3.11+，Unix shell，zip/unzip，sha256sum。
- 静态检查：`sh -n install*.sh scripts/*.sh`，`python3 -m py_compile scripts/*.py runtime/agent-ops/scripts/*.py`。
- 运行时测试：`python3 -m unittest discover runtime/agent-ops/tests`。
- 安装测试：`sh tests/test_install.sh`。
- 打包测试：`sh tests/test_packages.sh`。
- 更新测试：`sh tests/test_update.sh`。
- 构建：`./scripts/build-packages.sh`，产物位于 `dist/`。
- 已知限制：测试使用本地 Mock SSH 服务和伪造 Release，不连接真实服务器或数据库。
