# Enterprise AI Environment 学生项目

**接口补充说明：**先阅读 [INTERFACE-CLARIFICATIONS.md](INTERFACE-CLARIFICATIONS.md)。它统一说明模型网关、用量与预算、历史字段映射、目录查询权限、重复商品数量、订单状态和当前金额舍入行为（U1–U6）。正常 schema 与正式环境鲁棒性测试的区别也在其中；不包含作业答案。

## 统一远程评测

登录 Agentist → 我的学习 → 实验管理 → 权限中心，生成“统一评测配置”，下载后保存在仓库外。

```bash
export HYPER_LAB_CONFIG="/绝对路径/Enterprise-AI.json"
npm run evaluate
# 默认免费 t1 接入检查；开放正式评测后可指定 -- --task t2
```

本地提交与 GitHub push 使用同一任务队列。主机休眠时自动启动，不需复制 Daytona 临时地址；终端显示进度、逐案例结果、汇总和网页链接。
结果保存在 Supabase，可在“我的学习 → 实验管理 → 我的实验测评记录”查看。任务提交成功后，查询同一个 Job ID 不会重复执行。
临时测试受教师开放时段限制；正式课程按服务配置和费用额度开放。90 天个人凭证不代表无限模型预算。

```bash
npm run evaluate -- --job-id <任务编号>
# 上传超时时：保持源码不变，使用终端打印的编号
npm run evaluate -- --retry-key <原提交编号>
```

GitHub push 需先在工作台绑定仓库并安装生成的工作流；GitHub 使用短期 OIDC 身份，不上传个人 JSON 凭证。

这是多模块工程模板，不是完整业务评测答案。Python 和 TypeScript 使用相同的轮次接口，航空和零售均可接入。

1. 在 Agentist 工作台选择场景和语言，下载模板及业务材料。
2. 阅读 `PROJECT.md`（结构与扩展流程）和 `AGENT-CONTRACT.md`（接口规则）。所有远程运行代码放在 `agent/`，入口保持不变。
3. 运行本地测试：Python 使用 `python3 -m unittest discover -s tests -v`；TypeScript 使用 Node 24 和 `npm test`，无需安装 npm 依赖。
4. 在网页绑定自己的 GitHub 仓库、分支、**业务场景**、语言和评测任务。展开“查看本次测试内容与判定依据”确认范围。保存后展开该仓库的“安装完整项目并启用终端评测”，复制对应操作系统的命令；详细命令也在接入包的 `GITHUB-SETUP.md`。全新项目用 `--mode new` 安装完整包，包括 agent/、scripts/、tests/ 和材料；已有 Agent 项目用 `--mode existing` 保护源码。安装器自动放好 `.github/workflows/hyper-lab.yml`，不同内容的同名文件会停止而非覆盖。手动解压的新项目必须复制全部包内容，不能只复制 YAML。停用重复的旧评测工作流。
5. 推送后，在 GitHub Actions 和平台“运行与反馈”查看同一运行的报告。也可以通过网页或已登录的 `/lab-evaluate` 主动提交。

模板只实现只读客户查询，用于 t1 接入检查。取消、退款、订单处理等 p1/t2 业务仍需你根据材料实现。单元测试通过不代表正式案例通过。

## 在 git push 的终端直接看评测

### 首次提交：新仓库不能直接 push

安装器只放入文件，不会自动创建 commit 或设置上游分支。先运行 `gh auth login`，并在仓库根目录用 `git status` / `git diff` 检查文件。确认个人 `Enterprise-AI.json` 和密钥均保存在仓库外，再暂存并审阅：

```bash
git add .
git diff --cached
```

确认暂存内容没有凭证后创建提交：

```bash
git commit -m "Initialize Enterprise AI project"
```

若提示缺少 `user.name` / `user.email`，先配置自己的 Git 提交身份再重试。commit 成功后才推送。以下以绑定分支 `main` 为例；其他分支请替换为网页绑定的名称。

macOS/Linux：

```bash
python3 scripts/git-evaluate.py push --set-upstream origin main
```

Windows PowerShell / cmd.exe：

```powershell
py -3 scripts/git-evaluate.py push --set-upstream origin main
```

这条命令完成首次推送、设置上游和等待评测；出现 `has no upstream branch` 时也这样处理。Python 入口不需要先 `source`。以后每次修改后先检查、`git add`、`git commit`，再运行 `python3 scripts/git-evaluate.py push`（Windows 用 `py -3`）。没有新 commit 不会产生新评测。

### 可选：让普通 git push 也显示结果

先完成仓库绑定和工作流安装。安装 Python 3 和 [GitHub CLI](https://cli.github.com/)，然后在项目根目录的 bash/zsh 中运行：

```bash
gh auth login
source scripts/git-evaluate.sh
```

Windows PowerShell 不使用 `source`，在项目根目录运行：

```powershell
gh auth login
. .\scripts\git-evaluate.ps1
```

如果 PowerShell 执行策略阻止脚本，或使用 cmd.exe，直接运行 `py -3 scripts/git-evaluate.py push`，不要修改系统执行策略。每次新开终端需重新加载；PowerShell 用 `Remove-Item Function:\git` 关闭集成。需 Python 3.9+、Git、GitHub CLI；未安装 Python Launcher 时将 `py -3` 换成可用的 `python` 命令。这些脚本不会自动提交代码，push 前仍需检查并 commit。

当前终端后续的 `git push` 会先正常推送，再等待这次 commit、分支对应的 `hyper-lab.yml` 工作流，显示执行阶段、逐条 PASS/FAIL、总数、判定依据和结果网页链接。它只读取 GitHub 已触发的任务，不会再次提交评测。私有仓库需要当前 GitHub 账号有读取 Actions 和 artifacts 的权限。

七题作业的模拟交互：设置 `HYPER_LAB_CONFIG` 为仓库外的个人 Enterprise-AI.json 路径后，终端每约 5 秒读取同一任务的交互记录，显示模拟用户、授权负责人、工具调用、Agent 实际回复或返回 JSON。macOS/Linux 使用 `export HYPER_LAB_CONFIG="/绝对路径/Enterprise-AI.json"`；Windows PowerShell 使用 `$env:HYPER_LAB_CONFIG="C:\\私人目录\\Enterprise-AI.json"`。不设置时仍可查看 GitHub 进度和最终报告，但对话只能在完成后回放，不能称为实时。该凭证只发送到配置中的 Agentist 平台，不上传 GitHub。

新增模拟场景属于 `project-1-2` 的观察项，不计入七题成绩；不是 t1/p1/t2 的新增测试。旧服务器没有交互事件时不会生成虚构对话。安装器不自动覆盖旧辅助脚本；更新时请先备份旧脚本再按接入引导安装。

- 每次打开新终端重新 `source`；不会自动修改你的 shell 配置或全局 Git hooks。`unset -f git` 关闭集成；`command git push` 仅推送。
- 提示“已有 git alias/function，未覆盖”不是安装失败，不必删除原别名或重复 source。直接使用 Python 入口；首次推送用上面的 `--set-upstream origin main`，后续用 `python3 scripts/git-evaluate.py push`。
- 只修改文档、没有新 commit、dry-run、删除分支或推 tag 不一定触发评测；没有匹配运行时会说明原因，不会显示虚假的通过。
- 默认最多等待 30 分钟。Ctrl-C 只停止本地等待，不取消远程任务。网页“运行与反馈”和 GitHub Actions 仍可查看结果。
- 完整逐案例明细在远程报告生成后显示；等待期间显示工作流阶段和已等待秒数，并非虚构逐案例进度。
- 退出码 0 表示通过或无需等待；1 表示业务未通过；2 表示评测未确认。评测失败不会撤销已经成功的 push。报告存入 Git 元数据目录的 `enterprise-evaluations/<run-id>/report.json`，不进入学生提交源码。

已有项目需要工作流以及终端辅助脚本：`scripts/git-evaluate.py`、`scripts/git-evaluate.sh`、`scripts/git-evaluate.ps1`，不要覆盖自己的 `agent/`。安装命令默认拒绝冲突；明确备份旧接入文件后再安装新版。工作流在 GitHub 启动失败或没有生成报告时，终端说明“未评分”和失败原因（可获取时），不把它误报为 Agent 业务失败。

## 教学环境与上游材料的区别

- 零售模板先使用用户提供的邮箱搜索，再核对用户声称的客户 ID，成功后才读取档案。仅给 ID 时会追问邮箱，不会直接返回档案信息。正式业务还可以按政策实现“姓名＋邮编”验证；航空遵循它自己的政策，不照搬零售验证规则。
- 教学平台已预先启用零售退货和航空取消能力。以下载包 `materials/client_api/openapi.yaml` 为当前接口目录，不需要调用上游 Client 来申请接口。旧下载包缺少这些接口时，请重新下载材料，不要覆盖自己的 `agent/`。
- `materials/CLASSROOM.md` 说明教学适配边界。上游材料中的 `run_local_test` 指研究框架工具，本项目没有这个工具；请执行第 3 步的本地测试，然后提交远程 t1/p1/t2。接口仍可能返回异步状态或部署差异，需要按真实响应处理。
- 证据材料中的 `user_id` 对应业务 API 的 `customer_id`，`payment_history` 对应 `payments`；支付记录保留 `transaction_type`、`amount`、`payment_method_id`，不能把所有金额都当成退款。

本项目与七案例作业共用提交队列、按需执行器和个人历史，但评测器不同：t2 是完整业务对话与最终状态判定，不能用七题成绩替代。正常课程按需服务无需手动启动主机；管理员暂停服务或临时验收期限到期时，会拒绝新任务。本地测试和已有在线历史仍可使用。

平台只上传 `agent/`：本地 `tests/`、依赖目录和材料不会提交。远程不执行任意安装或构建脚本；新增第三方依赖必须先确认评测环境支持。不要提交任何凭证。已有项目无需替换自己的 Agent，本模板更新不会覆盖学生代码。
