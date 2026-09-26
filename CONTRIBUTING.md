# 小组协作指南

[首页](README.md) · [24 项认领表](docs/features/README.md) · [排期](docs/04-功能排期.md)

## 一项功能的完整流程

1. 打开功能页，了解代码基础、依赖和验收点；在 GitHub Issues 选择“功能认领”模板。
2. 按四周排期领取自己板块的功能；在功能总表登记对应 Issue/PR 和状态。负责人及交叉审核人已列明，先查是否已有实施者，尤其 F04 / card-24。
3. 从最新 main 建分支，例如 `feature/f01-card-query`。一项功能一个 PR；公共机制改动单独说明受影响功能。
4. 修改共享文件前，按四周排期联系本周集成人员，在 Issue 约定范围和合并顺序。业务改动先补测试，再实现并回归。
5. 使用 PR 模板提供真实验证输出和演示；审核通过、合并且验收证据齐全后，更新认领表为“已验收”。

状态只维护在功能总表，Issue/PR 作为讨论与证据。每周日总结只记录当周快照。组员 GitHub 写权限由仓库管理员在 Settings → Collaborators 中配置。

```bash
git switch main
git pull --ff-only
git switch -c feature/f01-card-query
# 修改、验证后，只暂存本次需要的文件
git add <本次修改的文件>
git commit -m "F01: 完善卡片查询"
git push -u origin feature/f01-card-query
```

历史 card-* 提交沿用旧命名；本轮业务提交统一用 Fxx。文档与公共基础提交可用 `docs:` / `infra:`。不直接向 main 推送，不强推他人分支。

## 共享文件与接口

四周集成人员依次为莫、黄、汤、舒，周总结页已列明。多人改 `agent/classifier.py`、`agent/read_routes.py`、`agent/write_flow.py`、`agent/templates.py`、`tools/schemas.py`、`data/dao.py` 时，先约定责任区；同域 `tools/card.py` 等文件也需要协调。

保留 interfaces → agent → guard/tools → data 的既有分层约束；界面不直连业务工具或数据库。具体调用边界以 [CLAUDE.md](CLAUDE.md) 为准。

接口名称、字段、权限与数据结构以[冻结规格](docs/01-接口规格.md)为准。需要调整先由队长确认，在单独的 `SPEC-CHANGE` 提交中说明兼容影响；不能为了通过测试删掉权限、确认、审计或用户隔离。新增依赖同样先确认。

## 首次运行

需要 Python 3.11、Git、uv；Windows 运行 `.sh` 使用 Git Bash。所有需要下载或安装的内容放在 D 盘新建项目文件夹中；不要在包含他人改动的目录直接覆盖克隆。

PowerShell 示例（已有相应软件时，无需重复安装）：

```powershell
New-Item -ItemType Directory -Force D:\Projects\bank-agent | Out-Null
Set-Location D:\Projects\bank-agent
$env:UV_CACHE_DIR = 'D:\Projects\bank-agent\cache\uv'
$env:UV_PYTHON_INSTALL_DIR = 'D:\Projects\bank-agent\runtimes'
$env:TEMP = 'D:\Projects\bank-agent\temp'
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Force $env:TEMP | Out-Null
git clone https://github.com/huangzhaokai2007-lang/git.git repo
Set-Location repo
uv sync --frozen
uv run python -m data.seed --reset
Copy-Item .env.example .env
uv run streamlit run interfaces/web/app.py
```

以上环境变量只影响当前终端；新终端安装/更新依赖前重新设置。`.venv` 位于 D 盘仓库内。`--reset` 重建本地合成演示库，仅在首次初始化或确认需要重置时执行；已有 `.env` 时不重复复制覆盖。

模型配置填写自己的 `.env`，不要提交密钥。没有模型配置可以运行 `uv run python scripts/demo.py --quick` 查看离线演示；它用替身分类器，不代表真模型识别效果。完整运行说明见 [运行与评测](docs/03-运行与评测.md)。

## 完成的标准

- 功能页列出的正常、边界、失败路径有验证，用户可见行为可演示。
- 涉及金额、权限和写入时，确认/OTP、幂等、用户隔离与审计有相应证据。
- `uv run pytest -q` 与 `bash scripts/verify.sh` 通过，保留实际输出；记录本次使用真实模型还是替身。
- 另一名组员审核通过；已知限制没有被包装成成功。新功能未被旧测试覆盖时必须补对应测试。

仅文档整理可检查链接、编号、路径与内容一致性；不要为了文档变更编写无意义的业务测试。周日项目收束仍执行完整门禁。

## 周日收束

进入[本周总结页](docs/weekly/README.md)，填写交付与 PR、测试输出、未完成原因、下周依赖和负责人。10 月 25 日额外核对全部 24 项验收证据及跨功能流程。
