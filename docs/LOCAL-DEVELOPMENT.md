# 本地 Python 开发环境

## 环境与来源

- 项目：`E:\Agent-For-Retail-Customer-Service`。
- 虚拟环境：项目内 `.venv/`，已由既有 `.gitignore` 忽略。
- Python：3.12.13；使用 `.venv\Scripts\python.exe`。
- tau2 来源：[Sierra 官方 Hyper-tau 仓库](https://github.com/sierra-research/hyper-tau-bench)，不是仅提供普通 tau-bench 接口的其他版本。
- 固定源码提交：`6e9f34c685d40fa7a9f5935d8970af6fd9d5f118`；包元数据版本为 `1.0.1`。
- 官方源码保存在 `.venv/src/hyper-tau-bench`，安装缓存与报告保存在 `.venv/`。
- 上游构建配置要求 Python `>=3.12,<3.14`，核心依赖包含 LiteLLM 等；安装遵守其 `openai<=2.20.0` 约束。

上游仓库部分材料文件名包含 Windows 不允许的字符，因此完整工作树无法检出。本次从固定提交仅提取 `src` 子树，以及 pyproject、README、LICENSE、AGENTS 构建/说明文件；未修改框架源码，未检出上游材料或评测任务数据。

## 使用方式

在项目根目录直接指定虚拟环境 Python，无需修改 PowerShell 安全策略或全局 PATH：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe .\.venv\check_tau2_install.py
```

如需激活自己的 PowerShell 终端，可运行已有的虚拟环境激活脚本：

```powershell
.\.venv\Scripts\Activate.ps1
```

若本机策略阻止激活，继续用完整 Python 路径即可；不需要修改执行策略。

用户已授权：后续本地开发确实需要的依赖可直接安装，优先放在本项目 `.venv/`，无需逐包重复询问。真实模型费用、评测范围、commit/push 仍按对应授权处理。可选 voice/gym 等扩展只在实际需要时安装。

## 契约使用原则

消息类型、工具 schema、context 和网关调用采用已安装 tau2 的实际公开接口，并以本项目的 `AGENT-CONTRACT.md`、`INTERFACE-CLARIFICATIONS.md` 和框架契约核对。模型、业务 REST 和课堂运行边界以项目提供的契约为准；不把上游完整 benchmark 的功能自动当成课堂可用能力。

本地安装只提供开发环境。远程评测仍由平台提供 tau2，不会执行项目的 pip 安装；虚拟环境与依赖不能放进 `agent/`。上游 CLI 的数据检查或独立 benchmark 运行不属于本次安装验证。

## 验证记录

2026-10-09 安装完成，实际安装包为 `tau2==1.0.1`，核心依赖包含 `litellm==1.82.6`、`openai==2.20.0`、`pydantic==2.14.0`。安装的 Python 可执行文件位于 `E:\Agent-For-Retail-Customer-Service\.venv\Scripts\python.exe`。

- `pip check`：`No broken requirements found`。
- 既有本地测试：6/6 通过。
- 本地框架兼容检查：7/7 通过；检查脚本位于已忽略的 `.venv/check_tau2_install.py`。

七项兼容检查实际验证：消息构造和 Pydantic JSON 往返；多个工具结果与 error 标记；本项目现有 toolkit 的真实发现与 schema；context 激活及作用域；action 选择和资源辅助方法；Python gateway 的 `actions` 参数及固定选项注入；固定参数拒绝覆盖和 `one_of` 显式选择要求。

已核实的具体接口包括 `model_dump_json()` / `model_validate_json()`、`TypeAdapter(Message)`、`toolkit.get_tools()`、`action_interface.select()`、`resources.path()` / `resources.read_text()`、`gateway.validate_request()`；本地网关配置在 `gateway.models` 的 `ModelConfig.constrained_args` 中。调用实际注入网关时仍使用其当次模型配置，不把本地测试模型或固定参数值带入正式 Agent。

Windows 下读取 UTF-8 政策文件时，应显式指定 UTF-8，或在 UTF-8 模式运行 Python；上游 `resources.read_text()` 没有编码参数，可通过已核实的 `resources.path(...).read_text(encoding="utf-8")` 明确处理编码。

框架导入会提示未找到 `.env`、上游数据目录和 voice 扩展。本次兼容检查不需要这些内容，导入和全部检查已成功；没有为消除提示而创建凭证或下载额外 benchmark 数据。

网关生成在检查中使用 mock，ClientAPI 传输被设置为禁止访问后端。本次没有调用真实模型、运行远程业务评测或验证业务完成率。
