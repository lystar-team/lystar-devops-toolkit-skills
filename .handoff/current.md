---
schema_version: 1
status: done
coverage: complete
updated_at: 2026-08-26T03:36:57.664793Z
repo: lystar-devops-toolkit-skills
branch: main
head: 7f6bb77b2a5a6c9aba61d005a61678f5129d517e
---

# HANDOFF

## Goal

完成独立 /tmp/e2exmc 项目的 Codeup、Git、流水线、Redis、数据库、服务器配置、部署和验收闭环，不修改参考工作区。

## Stop Point

E2E XMC 项目建设和纠偏已完成并通过最终核验：manifest 现在强制要求英文项目名 `E2E XMC` 和中文项目名“兴码程项目管理系统”；两个 Codeup 仓库真实 ID 为 API `7379776`、Admin `7379777`，默认分支均为 `develop`，自带 `master` 已删除；流水线 `5225656`、`5225666` 已按中文项目名改为“兴码程项目管理系统-后端API-天翼云”和“兴码程项目管理系统-后台管理系统-天翼云”；Redis 预留已从 DB `1` 迁移到空闲 DB `40`，DB `1` 的 35 个 key 保持不变，应用已重启并使用 DB `40`；远端 API 已切换到 HTTPS `18443`，Admin 保持 HTTP `8280`。XMC 的私用源码链现已完整位于 `/home/yean/projectWorkspace/yean-research/xmc-platform-bootstrap-yean`，公开 toolkit 不再包含 XMC runtime、入口、测试、依赖、安装脚本或打包包；已有 Codex、Pi、Claude、OpenCode 和 `$HOME/.lystar` 安装副本按要求保留。本 handoff 已完成。

## Decisions And Constraints

- `accepted` 项目 manifest 同时保存英文项目名 `display_name_en` 和中文项目名 `display_name_zh`；流水线显示名使用 `<中文项目名>-后端API/后台管理系统-<云名称>`。
- `accepted` Codeup 仓库统一以 `develop` 为默认分支；只在真实分支列表确认存在时删除 `main` 或 `master`，不猜测分支名。
- `accepted` 本次真实 E2E 仓库 ID 以实时 Codeup 查询为准：API `7379776`、Admin `7379777`；摘要中的 `7221012/7221015` 是原始参考仓库，不用于本项目。
- `accepted` API HTTPS 默认端口为 `18443`；Admin 继续 HTTP-only `8280`，不使用不匹配的证书。
- `accepted` Redis 只选择从 `database_start=40` 开始、实际为空且未本地预留的 DB；迁移只更新本地预留记录，不执行 `FLUSHDB` 或 `FLUSHALL`，旧 DB `1` 数据保持不变。
- `accepted` 受保护配置继续使用既有凭证设计；本轮只修改 Redis database 索引，不新增环境变量、密码字段或凭证抽象。
- `constraint` 外部查询需要 `--live`，外部写入需要 `--confirm`；Codeup 操作使用官方 Alibaba Cloud CLI 通过 `codeupx`，不直接拼接云效 HTTP 请求。
- `constraint` 保留用户既有工作树修改，不执行 reset、checkout 或清理；参考工作区 `/home/yean/projectWorkspace/xmc-project-platform` 不修改。

## Verified

- `pass` `codeupx doctor --live --json`：Codeup 连接和持久化认证可用。
- `pass` `xmcx pipeline run --component api --confirm --watch`：最终 API run 4 成功，提交 2b130e0，release 4。
- `pass` `codeupx pipeline run --pipeline-id 5225666 --branch develop --watch --json`：最终 Admin run 3 成功，提交 ca215b5，release 3。
- `pass` `dbx query --source db_c70bdccc73fa --database e2exmc_platform_db`：MariaDB 12.3.2，103 张表，估算约 83617 行，连接正常。
- `pass` `git grep credential scan on both e2exmc repositories`：未发现旧数据库/Redis/Druid/default login/Camunda environment credential。
- `pass` Codeup 仓库 branch policy dry-run：E2E API/Admin 均仅有 `develop`，`master` 已删除；默认分支设置成功。
- `pass` Codeup 流水线：`5225656`=`兴码程项目管理系统-后端API-天翼云`，`5225666`=`兴码程项目管理系统-后台管理系统-天翼云`；按规范化 Git URL 和 develop 分支均唯一定位。
- `pass` Redis DB 迁移：目标 DB `40` 迁移前 `0` key 且未预留；本地预留已从 DB `1` 改为 DB `40`。
- `pass` Redis 数据保留：应用重启后 DB `1` 仍为 `35` key，DB `40` 已由应用使用并为 `35` key；未执行清库操作。
- `pass` 远端配置：`/etc/e2exmc/application-secrets.yml` 的 Redis database 为 `40`；`/etc/nginx/conf.d/e2exmc-api.conf` 监听 `18443 ssl`；Nginx 配置校验成功并已 reload。
- `pass` 远端服务：`e2exmc-api.service` enabled/active；API 本机 `13000` 返回 HTTP 200，API Nginx `18443` 返回 HTTP 200，Admin `8280` 返回 HTTP 200；远端监听已无 `8443`。
- `pass` 数据库：目标 `e2exmc_platform_db` 保持已初始化状态，103 张表；manifest 直接引用 `/home/yean/projectWorkspace/xmc-project-platform/xmc-project-platform-api/sql/xmc_platform_db.sql`，基线文件此前已验证字节一致。
- `pass` 定向测试：XMC `16` 个、Codeup `10` 个、Redis `2` 个均通过；相关 Python 文件编译通过。
- `pass` 最终 Python 回归：`120` 个测试通过；XMC 定向 `16` 个、Codeup 定向 `10` 个、Redis 定向 `2` 个均通过。
- `pass` 最终安装、打包、更新、Shell 语法和 `git diff --check`：全部通过；缓存和系统文件未进入 package archives。
- `pass` 公网健康：`https://e2exmc.api.csxmc.cn:18443/actuator/health` HTTP 200，Admin `http://e2exmc.admin.csxmc.cn:8280/` HTTP 200；API 证书 CN/SAN 为 `*.api.csxmc.cn`，SNI 校验通过。
- `pass` 仓库凭证扫描：两个 E2E 仓库未发现 Redis/数据库实际凭证或旧默认登录凭证。
- `pass` 项目状态和配置：`/home/yean/.lystar/state/xmc/projects/e2exmc.json`、Redis reservation、manifest 和 schema JSON 均有效且互相一致。
- `pass` XMC 私用源码链已完整迁移：`SKILL.md`、`AGENTS.md`、Schema、示例、`xmc_bootstrap.py`、`xmcx`、XMC 单测、依赖文件、私用安装器和操作文档均位于 `/home/yean/projectWorkspace/yean-research/xmc-platform-bootstrap-yean`。
- `pass` 私用 XMC 安装器临时 `$LYSTAR_HOME` 验证：入口加载 `$LYSTAR_HOME/runtime/xmc/xmc_bootstrap.py`，共享 Codeup/Redis runtime 从公共 runtime 读取，目标 Harness Skill 文件已安装。
- `pass` 公开 toolkit 清理：公开 `install.sh`、打包脚本、README、安装测试和打包测试均不再包含 XMC runtime、入口、测试或独立包；公开 package archives 未发现 XMC 文件名或入口。
- `pass` 私用 XMC 定向测试：迁移后的 `16` 个单测通过；私用 runtime 与迁移前 `$HOME/.lystar/runtime/scripts/xmc_bootstrap.py` SHA-256 一致。

## Next Action

无。后续新项目应复制当前 manifest 约束：同时配置 `display_name_en`/`display_name_zh`，流水线使用中文项目名规范，仓库使用 `develop` 并通过 branch policy 清理真实存在的 `main/master`。

## Files

- `/tmp/e2exmc/project-config.json`：E2E 项目 manifest。
- `/tmp/e2exmc/secrets-application.yml`：本地权限 600 的 API 受保护配置。
- `/tmp/e2exmc/redis-source.yml`：本地权限 600 的 Redis profile 配置。
- `/home/yean/.lystar/state/xmc/projects/e2exmc.json`：最终组件、Redis、端口和流水线状态。
- `/home/yean/projectWorkspace/yean-research/xmc-platform-bootstrap-yean/SKILL.md`：仅供 Yean 自用的 XMC Skill 源码。
- `/home/yean/projectWorkspace/yean-research/xmc-platform-bootstrap-yean/AGENTS.md`：XMC 私用源码维护规范。
- `/home/yean/projectWorkspace/yean-research/xmc-platform-bootstrap-yean/runtime/agent-ops/scripts/xmc_bootstrap.py`：XMC 私用 runtime。
- `/home/yean/projectWorkspace/yean-research/xmc-platform-bootstrap-yean/bin/xmcx`：私用命令入口。
- `/home/yean/projectWorkspace/yean-research/xmc-platform-bootstrap-yean/install.sh`：私用安装器。
- `/home/yean/projectWorkspace/yean-research/xmc-platform-bootstrap-yean/references/config-schema.json`：XMC 项目配置 schema。

## Risks And Unknowns

- `medium` Admin 没有可验证的匹配 HTTPS 证书，当前继续只提供 HTTP `8280`；这不影响本次 API/Admin 既定验收。
- `low` Codeup 原始参考仓库 `7221012/7221015` 与本次 E2E 仓库 `7379776/7379777` 不同；后续操作仍必须以实时 URL/ID 查询为准。

## Git State

- Root: `.`
- Branch: `main`
- HEAD: `7f6bb77b2a5a6c9aba61d005a61678f5129d517e`
- Working tree: `dirty`
- Dirty paths: `28`
