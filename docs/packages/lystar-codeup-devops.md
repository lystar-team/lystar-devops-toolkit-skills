# LYStar Codeup DevOps

`lystar-codeup-devops` 提供 `codeupx`，通过官方 `aliyun devops` CLI 操作云效 Codeup/Flow。

## 安装后路径

- 命令：`${LYSTAR_HOME:-$HOME/.lystar}/bin/codeupx`
- 配置：`${LYSTAR_HOME:-$HOME/.lystar}/config/codeup-devops.toml`
- 运行结果：复用 Lystar 的 `${LYSTAR_HOME:-$HOME/.lystar}/state/results/`
- Skill：安装到目标 Harness 的标准 Skills 目录

## 依赖

依赖插件安装到 Lystar 运行时目录 `${LYSTAR_HOME:-$HOME/.lystar}/runtime/aliyun/plugins`，不会把插件状态写入 Skill 目录。若需手动安装：

```bash
export ALIBABA_CLOUD_CLI_PLUGINS_DIR="${LYSTAR_HOME:-$HOME/.lystar}/runtime/aliyun/plugins"
aliyun plugin install --names aliyun-cli-devops
aliyun devops version
```

当前 Lystar 环境也支持把 `aliyun` 放在 `${LYSTAR_HOME:-$HOME/.lystar}/bin/aliyun`。

## 配置

云效 Token 和流水线映射统一持久化在 `${LYSTAR_HOME:-$HOME/.lystar}/config/codeup-devops.toml`。流水线映射按“组织 key + 规范化 Codeup 仓库地址 + 分支”登记，不写入项目仓库；项目文档只保留历史发布记录。

保存云效 Token：

```bash
codeupx auth save --org main --token '<personal-access-token>' --json
codeupx auth show --json
```

登记流水线映射并查看统一配置：

```bash
codeupx pipeline register --org main \
  --pipeline-id '<pipeline-id>' \
  --repo-url 'https://codeup.aliyun.com/org/project/api.git' \
  --branch develop --json
codeupx pipeline registry --org main --json
```

首次查找时可使用 `codeupx pipeline find --save` 自动登记。以后运行直接按仓库和分支复用：

```bash
codeupx pipeline run --org main \
  --repo 'https://codeup.aliyun.com/org/project/api.git' \
  --branch develop --watch --json
```

`auth save` 会通过官方插件查询 HTTPS 克隆用户名，并将凭证写入
`${LYSTAR_HOME:-$HOME/.lystar}/config/codeup-devops.toml`，文件权限为 `0600`。
Git HTTPS clone/push 使用临时 `GIT_ASKPASS`，不会把 Token 写进仓库 URL；普通输出只报告是否已配置。

中心版组织：

```bash
codeupx org register main --name '主组织' --edition central \\
  --organization-id '<organization-id>' --default --json
```

Region 版组织：

```bash
codeupx org register test-region --name '测试组织' --edition region \\
  --api-base-url '<api-base-url>' --json
```

## 常用命令

```bash
codeupx doctor --live --json
codeupx org list --save --json
codeupx pipeline list --org main --all --json
codeupx pipeline find --org main \
  --repo-url 'https://codeup.aliyun.com/org/project/api.git' \
  --branch develop --json
codeupx pipeline register --org main --pipeline-id '<pipeline-id>' \
  --repo-url 'https://codeup.aliyun.com/org/project/api.git' --branch develop --json
codeupx pipeline registry --org main --json
codeupx pipeline run --org main \
  --repo 'https://codeup.aliyun.com/org/project/api.git' --branch develop --watch --json
codeupx pipeline get --org main --pipeline-id 5224875 --yaml-out ./pipeline.yaml --json
codeupx pipeline apply --org main --create --name '新流水线' --yaml ./pipeline.yaml --run-after --watch --json
codeupx pipeline apply --org main --pipeline-id 5224875 --name '已有流水线' --yaml ./pipeline.yaml --run-after --watch --json
codeupx repository list --org main --namespace-id 2049451 --json
codeupx repository create --org main --namespace-id 2049451 \
  --name 'demo-api' --path 'demo-api' --confirm --json
codeupx repository branch-policy --org main --repository-id '<verified-repository-id>' \
  --branch develop --confirm --json
```

具体行为和组织选择规则见 Skill 的 `SKILL.md`。
