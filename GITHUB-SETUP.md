# 安装完整 Enterprise AI 项目

绑定：1471436961/agent-for-retail-customer-service / main；场景：retail_plus；语言：python；任务：t1。

## 按这个顺序操作

1. 获取权限中心的 Enterprise-AI.json，保存在独立私人目录，不放入资料文件夹或 Git 仓库。
2. 用网页命令下载完整原始 Package 和测试案例说明。在新建的普通资料文件夹运行，无需 Git 仓库，也不用先 clone；需 Python 3.9+ 和 curl。
3. clone 绑定的仓库，进入仓库根目录。
4. 安装项目文件。
5. 启用终端评测。
6. 检查、commit、push，查看结果。

## 命令安装（推荐）

第二步同时下载完整原始 Package 和测试案例说明。在网页此仓库的指导中复制命令：先用 curl（Windows 用 curl.exe）下载脚本，再用 --mode materials 和个人 Enterprise-AI.json 运行。命令下载全部原始 ZIP 分卷和 test-cases-<绑定编号>-<语言>.md，逐个显示字节进度并校验 SHA-256；重试跳过已校验文件，同名不同内容不会覆盖。下载后将所有 ZIP 分卷解压到同一资料目录。不会安装项目或发起评测。Markdown 包含绑定场景的全部公开案例，不等于下一次 push 的选定范围。旧的 --mode cases 仍只下载 Markdown。

先安装 Git、Python 3.9+ 和 GitHub CLI（gh）；TypeScript 本地测试另需 Node 24+。clone 自己的仓库，在绑定分支的项目根目录打开终端。在 Agentist → 我的学习 → 实验管理 → 权限中心下载个人 Enterprise-AI.json，保存在仓库外，禁止提交凭证。

macOS / Linux（bash 或 zsh）：

~~~bash
curl -fL 'https://agentist.org/lab/enterprise-ai/install.py' -o install-enterprise-ai.py
# 查看下载的脚本后执行：
python3 install-enterprise-ai.py --binding 3d81f316-16c1-452d-939f-48776870bd23 --config "/绝对路径/Enterprise-AI.json" --mode new
~~~

Windows PowerShell（不需要 bash）：

~~~powershell
Invoke-WebRequest -Uri 'https://agentist.org/lab/enterprise-ai/install.py' -OutFile install-enterprise-ai.py
# 查看下载的脚本后执行：
py -3 install-enterprise-ai.py --binding 3d81f316-16c1-452d-939f-48776870bd23 --config "C:\Users\你的用户名\Enterprise-AI.json" --mode new
~~~

全新项目用 --mode new：安装完整包，包括 agent/、scripts/、tests/、材料和文档，并自动放好 .github/workflows/hyper-lab.yml。已有 Agent 项目用 --mode existing：只安装工作流、终端辅助脚本和本说明，不修改 agent/、package.json、测试和材料。安装器先核对仓库、分支，以及已有 Agent 的场景与语言；遇到不同内容的同名文件会列出冲突并停止，不自动覆盖。先自行备份/移走冲突文件再重试。相同内容可重复安装。七题作业需先 clone 作业，用 --mode existing；不会安装答案。

## 手动下载 ZIP 的替代方式

全新项目要复制解压包里的全部内容，不是只复制 hyper-lab.yml。macOS/Linux 执行 mkdir -p .github/workflows，再执行 cp -i hyper-lab.yml .github/workflows/hyper-lab.yml。Windows PowerShell 执行 New-Item -ItemType Directory -Force .github/workflows，再执行 Copy-Item hyper-lab.yml .github/workflows/hyper-lab.yml -Confirm。仅放在根目录的 YAML 不会触发 Actions。已有项目建议用安装器 --mode existing，保护自己的源码。

## 让 git push 在终端显示评测

首次先运行 gh auth login。每次新开 bash/zsh 终端执行：

~~~bash
source scripts/git-evaluate.sh
~~~

每次新开 Windows PowerShell 执行：

~~~powershell
. .\scripts\git-evaluate.ps1
~~~

若执行策略阻止 PowerShell 脚本，不要降低系统策略，直接运行 py -3 scripts/git-evaluate.py push；macOS/Linux 对应 python3 scripts/git-evaluate.py push。已有 git alias/function 不会覆盖。Windows cmd.exe 用 Python 命令，不使用 source 或 PowerShell 脚本。

出现“已有 git alias/function，未覆盖”不是安装失败。保留原别名，使用下面的 Python 命令即可，不需要反复 source。

### 首次提交并推送（新空仓库必做）

安装不会自动创建 commit。先用 git status / git diff 检查文件，确保 Enterprise-AI.json 和密钥保存在仓库外。新项目需提交完整工程，包括 agent/、scripts/ 和 .github/workflows/hyper-lab.yml。先暂存，再检查暂存区：

~~~bash
git add .
git diff --cached
~~~

确认没有凭证后，创建首次提交：

~~~bash
git commit -m "Initialize Enterprise AI project"
~~~

若 Git 提示缺少 user.name / user.email，请配置自己的提交身份后重试。commit 成功后才继续。macOS/Linux 用下面的命令首次推送，自动设置到绑定分支的上游关联并等待评测：

~~~bash
python3 scripts/git-evaluate.py push --set-upstream origin 'main'
~~~

Windows PowerShell（不需要 source）：

~~~powershell
py -3 scripts/git-evaluate.py push --set-upstream origin 'main'
~~~

出现 has no upstream branch 时也使用上面这条命令；cmd.exe 中请将分支名外的单引号换成双引号。以后每次修改后先检查、git add、git commit，再运行 python3 scripts/git-evaluate.py push（Windows：py -3 scripts/git-evaluate.py push）。若终端脚本已成功加载，也可直接 git push。

终端先显示阶段与等待时间，报告完成后显示逐案例结果、总数和网页链接；只是等待 GitHub 的同一任务，不会重复评测。没有新提交不会产生新评测；变更还需匹配工作流路径。Ctrl-C 只停止本地等待，不取消远程任务。逐案例明细在报告完成后展示，不伪造实时进度。

网页是同一轮运行的可视化视图。GitHub Actions 权限/计费限制可能导致任务未启动；completed 不代表通过。初始模板只针对 t1 接入检查，不是完整业务答案。安装不会修改计费配置、发起评测或购买模型额度。
