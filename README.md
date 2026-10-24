# 零售客服 Agent

Python Agent 通过平台注入的模型网关理解对话、选择工具并生成参数。业务数据由平台 REST API 提供；模型提出工具调用后，宿主执行工具，再把真实结果交回 Agent。

业务政策来自原始材料及 [政策出处索引](docs/POLICY-REGISTER.md)。接口以 [Agent 契约](AGENT-CONTRACT.md)、[接口澄清](INTERFACE-CLARIFICATIONS.md) 和 [业务 OpenAPI](materials/client_api/openapi.yaml) 为准。工程职责和会话状态见 [PROJECT.md](PROJECT.md)。

## 本地验证

在项目根目录的 PowerShell 执行：

```powershell
.\.venv\Scripts\python.exe scripts/verify-offline.py
```

它运行离线测试和提交包检查，结果写入已忽略的 `evaluation-results/offline/report.json`。测试覆盖消息协议、权限边界、实际 SDK 读写及合成数据；受控模型决策的通过不代表真实模型或平台业务评测通过。

需要单独检查时：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe scripts/check-submission.py
```

## 提交评测

平台只上传 `agent/`。仓库、分支、场景和案例范围由网页绑定决定，不能通过本地文档或测试数量推断。

检查本次修改，只暂存相关文件并创建提交，再正常推送 `main`。已有工作流会触发一次评测，不需要另外提交 `evaluate`。如需在自己的终端等待对应评测，可运行：

```powershell
python scripts/git-evaluate.py push --set-upstream origin main
```

可选的 PowerShell 集成：

```powershell
. .\scripts\git-evaluate.ps1
```

新开终端需要重新加载。Python 推送命令不依赖加载脚本；不要覆盖已有 Git alias/function，也不要为此修改全局 Git 配置或 PowerShell 执行策略。

凭证和原始资料保存在仓库外。测试输出保存在 `evaluation-results/`；不要把凭证、资料包、虚拟环境或测试报告加入提交。当前项目不启用提交脚本的个人凭证读取功能。
