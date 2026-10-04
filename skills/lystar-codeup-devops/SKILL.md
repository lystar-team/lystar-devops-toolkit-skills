---
name: lystar-codeup-devops
description: 通过本机官方 aliyun devops CLI 管理阿里云云效 Codeup/Flow，支持多云效组织上下文、组织发现、流水线 YAML 获取、创建、更新、自动运行、运行状态轮询和部署依赖检查。用户要求创建、复制、更新、运行或排查云效流水线时使用。
---

# LYStar Codeup DevOps

使用统一命令 `codeupx`，不要在 Skill 目录另建配置、状态或凭据目录。

## 运行前提

- 官方阿里云 CLI 位于 PATH，或位于 `$HOME/.lystar/bin/aliyun`。
- 已安装并加载 `aliyun-cli-devops` 插件：`aliyun devops version`。
- 云效个人访问令牌通过 `codeupx auth save --token` 持久化到 `$HOME/.lystar/config/codeup-devops.toml`；运行时只读取本地凭据配置。
- 多组织配置位于 `$HOME/.lystar/config/codeup-devops.toml`。
- 运行结果沿用 Lystar session snapshot，位置由 `codeupx` 返回。

CLI 缺失或插件未加载时先执行 `codeupx doctor --json`，不要绕过 `codeupx` 自己拼接另一套运行时。

保存凭证：

```bash
codeupx auth save --org main --token '<personal-access-token>' --json
codeupx auth show --json
```

`auth save` 使用官方 `base-get-user-by-token` 和
`codeup-get-member-https-clone-username` 查询 Git HTTPS 用户名。Token 保存在
`codeup-devops.toml` 的 `credentials` 表中，配置文件保持 `0600`；普通输出只报告是否已配置。

## 组织上下文

每次流水线操作都必须先解析组织。流水线 ID 不能单独决定组织。

流水线映射统一保存在 `${LYSTAR_HOME:-$HOME/.lystar}/config/codeup-devops.toml` 的 `pipelines` 表中。映射键由组织 key、规范化 Codeup 仓库地址和分支组成，不保存在项目仓库或项目 `docs/` 中。项目文档可以记录历史发布结果，但不是流水线 ID 的事实源。

中心版组织保存 `organization_id`；Region 版组织保存 `api_base_url`。用稳定的小写 ASCII key 作为 Agent 口令中的组织别名：

```bash
codeupx org list --save --json
codeupx org register main --name "主组织" --edition central \
  --organization-id '<organization-id>' --default --json
codeupx org register region-test --name "测试 Region" --edition region \
  --api-base-url '<api-base-url>' --json
codeupx org show --org main --json
```

用户未指定组织时：

1. 使用配置中的 `default_organization`。
2. 只有一个本地组织时使用它。
3. 多个组织且名称、别名或 ID 无法唯一匹配时，列出候选并要求选择。
4. 用户只给流水线 ID 时，使用 `pipeline locate` 跨可发现的中心版组织查找；Region 版必须先有本地域名配置。

## 流水线操作

读取已有流水线 YAML：

```bash
codeupx pipeline get --org <org-key> --pipeline-id <id> \
  --yaml-out ./pipeline.yaml --json
```

列出流水线：

```bash
codeupx pipeline list --org <org-key> --all --json
```

按 Codeup Git 地址匹配流水线：

```bash
codeupx pipeline find --org <org-key> \
  --repo-url 'https://codeup.aliyun.com/org/project/api.git' \
  --branch develop --json
```

`pipeline find` 会读取流水线 YAML 的 `sources.*.endpoint`，标准化仓库地址后返回 `located`、`not_found` 或 `ambiguous`；默认只读，不会根据流水线名称猜测，也不会修改流水线。需要把唯一结果登记到统一配置时显式加 `--save`。

## 统一流水线映射

首次接管或迁移项目时，先登记并校验映射：

```bash
codeupx pipeline register --org main \
  --pipeline-id '<pipeline-id>' \
  --repo-url 'https://codeup.aliyun.com/org/project/api.git' \
  --branch develop --json
```

查看统一登记的映射：

```bash
codeupx pipeline registry --org main --json
```

也可以在首次按仓库查找时自动保存唯一结果：

```bash
codeupx pipeline find --org main \
  --repo-url 'https://codeup.aliyun.com/org/project/api.git' \
  --branch develop --save --json
```

运行已有项目时优先复用统一映射，省略流水线 ID：

```bash
codeupx pipeline run --org main \
  --repo 'https://codeup.aliyun.com/org/project/api.git' \
  --branch develop --watch --json
```

`pipeline run` 会先按组织、仓库和分支读取本地映射；只有没有登记时才执行一次 `pipeline find` 并保存唯一结果。映射失效时，使用 `pipeline register` 覆盖，或使用 `pipeline find --save` 重新登记。需要临时运行未登记流水线时，仍可直接提供 `--pipeline-id`。

创建并自动运行：

```bash
codeupx pipeline apply --org <org-key> --create \
  --name '<pipeline-name>' --yaml ./pipeline.yaml \
  --run-after --watch --json
```

更新并自动运行：

```bash
codeupx pipeline apply --org <org-key> \
  --pipeline-id '<pipeline-id>' --name '<pipeline-name>' \
  --yaml ./pipeline.yaml --run-after --watch --json
```

列出代码组仓库：

```bash
codeupx repository list --org <org-key> --namespace-id <group-id> --json
```

创建远程代码库。`--namespace-id` 必须使用官方查询得到的真实代码组 ID：

```bash
codeupx repository create --org <org-key> --namespace-id <group-id> \
  --name '<repository-name>' --path '<repository-path>' \
  --visibility private --confirm --json
```

统一仓库分支策略：先确认 `develop` 存在并设为默认分支，再只删除真实分支列表中存在的 `main` 或 `master`：

```bash
codeupx repository branch-policy --org <org-key> \
  --repository-id <repository-id> --branch develop --confirm --json
```

单独运行：

```bash
codeupx pipeline run --org <org-key> --pipeline-id '<pipeline-id>' \
  --repo '<repo-url>' --branch develop \
  --env KEY=VALUE --watch --json
```

省略 `--pipeline-id` 时，必须提供 `--repo` 和 `--branch`，由统一流水线映射解析 ID。

运行参数复杂时写 JSON 文件并使用 `--params-file`。已有流水线的 YAML、服务连接、主机组和变量组应优先复用，不要凭空生成内部 ID。

## 运行流程

1. 解析组织并验证 CLI 上下文。
2. 从统一流水线映射读取组织、仓库、分支和流水线 ID；已有映射时直接运行，不重复 `pipeline find`。
3. 只有映射缺失时，才按仓库地址和分支查找；唯一匹配后写回统一配置。
4. 读取 YAML，确认代码源、服务连接、构建集群、制品和部署组件。
3. 对创建/更新动作先使用 `--dry-run` 或先读取现有流水线比对。
4. 创建或更新后，若用户要求自动部署，必须继续调用 `--run-after`，不能只报告创建成功。
5. 运行后使用 `--watch` 轮询；报告流水线 ID、运行 ID、最终状态和失败阶段。
6. 失败时保留 `codeupx` 返回的原始运行详情，不把失败猜成部署成功。

`pipeline create` 只创建，`pipeline update` 只更新；需要“变更后立即运行”时使用 `pipeline apply --run-after`。

## 部署依赖

流水线 YAML 可能引用以下已有资源：

- Codeup、ACR、ACK、ECS 等服务连接
- 主机组
- 变量组
- 构建集群和制品

依赖检查优先调用官方 CLI 的 `flow-list-service-connections`、`flow-list-host-groups` 和 `flow-list-variable-groups`。资源存在性是前置检查，资源创建不属于流水线 YAML 创建的隐式步骤；只有用户明确要求时才扩展资源管理。

## 输出和边界

- Agent 优先使用 `--json`。
- `codeupx` 会把最后一次结果保存到 Lystar 的 session snapshot；不要自行把结果写到 Skill 目录。
- 流水线映射必须保存在 `${LYSTAR_HOME:-$HOME/.lystar}/config/codeup-devops.toml`，由 `pipeline register`、`pipeline find --save` 或缺失映射时的 `pipeline run` 维护。
- 发版完成后报告流水线 ID、运行 ID、提交号和最终状态；运行 ID用于追溯，不替代全局流水线映射。
- 不要把组织 ID、流水线 ID 和服务连接 ID 混用。
- 不要猜测 Codeup 代码组或代码库内部 ID；创建前先用 `repository list` 或官方 CLI 查询。
- 不要把流水线名称当作唯一 ID；同名流水线必须结合组织和显式选择策略。
- 生产流水线只有在用户明确给出组织、流水线或创建目标、运行分支/Tag 和运行意图时才执行 `--run-after`。
