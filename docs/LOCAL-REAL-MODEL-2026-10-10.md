# 本地真实 Kimi 模型调用记录

2026-10-10。用户授权最多 16 次调用、费用上限人民币 10 元。本次实际调用 14 次：
13 次 HTTP 200，1 次 HTTP 400；遇到错误后停止，未耗尽预算。

## 调用配置与边界

- 服务：Moonshot 中国官方 API，https://api.moonshot.cn/v1。
- 请求模型：kimi-k2.6；本地 tau2/LiteLLM 标识 openai/kimi-k2.6。
- 输出上限：保持项目模型配置快照的 1024 tokens。
- 本地宿主读取仓库外的独立密钥文件。未打印密钥，未将其传入 Agent context 或提交包。
- Agent 仍调用 context.model_gateway；使用真实 tau2 candidate broker、宿主 ModelGateway、
  LiteLLM 和 OpenAI HTTP 客户端。completion 观察器只转发真实请求，不返回预设响应。
- 客户与业务 API 是既有合成测试数据；没有访问生产客户、平台隐藏案例或参考答案。
- 不修改生产 Agent、平台模型配置、绑定、任务或评测范围，不提交、推送或启动平台评测。

关闭 SDK/HTTP 自动重试和空响应重试；实际 HTTP transport 计数，每次发送前预留
整个模型上下文窗口及最大输出的最坏费用，超出 16 次或 10 元即阻止发送。
原始错误由本地宿主记录，经密钥过滤后保存。报告没有 Authorization 请求头。

入口：[scripts/test-live-model.py](../scripts/test-live-model.py)。
报告：[report.json](../evaluation-results/kimi-local-20261010T063129426665Z/report.json)。
报告及提取的历史提交在 E 盘 evaluation-results/ 下，该目录被 Git 忽略。

## 实际结果

| 测试对话 | HTTP 调用次数 | 实际 API 顺序 | 结果 |
|---|---:|---|---|
| 当前代码：邮箱与声明 ID，默认思考 | 2 | search → profile | verify_customer 后根据结果回复邮箱 |
| 当前代码：同一请求，关闭思考 | 2 | search → profile | 完成 |
| 当前代码：不同措辞的姓名与邮编验证 | 2 | search → profile | 模型理解 Alex Li/10001 并调用 verify_customer，回复邮箱 |
| 当前代码：邮箱与声明 ID 不匹配 | 2 | 只有 search | 工具拒绝，没有读取任何客户详情；模型要求核对验证信息 |
| 当前代码：中文问候 → 仅 ID → 补充邮箱 | 4 | 前两轮零 API；补充邮箱后 search → profile | 模型先询问验证信息，后执行验证并回答 |
| P1 原始 dc4dd74：邮箱与声明 ID，关闭思考 | 2 | search → profile | 第二次模型请求 HTTP 400，gateway_failure |

前五组完成了所检查的对话/身份路径；这不是平台评分或全面业务验收。默认开启思考
的 K2.6 在 1024 输出 token 配置下也成功，本次没有复现空响应或 token 耗尽。

## P1 的真实模型服务错误

第一轮模型实际选择 lookup_customer，工具正确完成 search/profile。第二轮发送给
服务的 messages 只有 system、user、assistant，没有工具返回消息。

Moonshot 实际返回 HTTP 400；宿主通过 LiteLLM 抛出 BadRequestError：

```text
Invalid request: an assistant message with 'tool_calls' must be followed by
tool messages responding to each 'tool_call_id'. The following tool_call_ids
did not have response messages: lookup_customer:0
```

P1 随后捕获中介异常，回复固定的 service or protocol limit 文案。此次错误是服务
真实返回，不是本地模拟校验主动抛出的。原因是 P1 没有展开 MultiToolMessage，
而 tau2 的 provider 转换层忽略了批次；当前代码展开为 ToolMessage 后，同类请求
正常完成。它验证了 P1 第二轮缺陷，不能据此改写先前平台零工具调用的首轮轨迹。

## 用量与费用

成功请求返回合计输入 43,873 tokens、输出 804 tokens。按官方国内价格估算：
输入缓存命中每百万 ¥1.10、未命中 ¥6.50、输出 ¥27.00。

- 计入返回的缓存命中数，成功请求费用估算 ¥0.2073497。
- 所有成功请求输入都按未命中计费，上界估算 ¥0.3068825。
- 最后一次 HTTP 400 未返回 usage。测试账本保留了该次最大费用预留，整体保守
  上界为 ¥2.0384665，低于授权的 10 元。这不是服务商实际账单金额。

官方来源：
[调用地址和模型参数](https://platform.kimi.com/docs/guide/kimi-k2-6-quickstart)，
[人民币定价](https://platform.kimi.com/docs/pricing/chat)。价格从官方 Markdown
文档取得，核实日期 2026-10-10。

## 观察到的后续改进

中文多轮场景的最终回复在客户仅请求邮箱时，还附带了账户姓名和配送地址。
身份及访问权限路径正确，但回复应更聚焦用户请求。此次只记录观察，不修改业务实现。
真实模型测试覆盖六组具体对话，不能替代后续业务任务的真实模型验证。

## 后续修正与离线验证

2026-10-10，在上述真实调用之后继续修正，未追加模型调用：

- 历史恢复接受合法的 assistant 文字加工具调用，保留文字、参数和调用 ID，
  不修改调用方传入的历史。实时返回给半双工宿主的消息仍只包含工具调用，
  符合 tau2 orchestrator 的要求。
- 历史中的附带文字不会被记作已展示的变更确认，也不会提前清除待展示的操作回执。
- 异常回复分别说明模型请求失败、历史准备失败、无效消息、本地模型调用上限和
  本地工具轮次上限，移除统一的 service or protocol limit 文案。
- 提示词要求只回答本次请求所需信息，不附带无关的姓名、地址或支付信息。
  该提示词调整尚未追加真实模型验证。

实际执行 `python -m unittest discover -s tests -q`：143 项通过，0 失败、0 错误。
新增恢复测试覆盖实际 tau2 provider 消息转换、匹配的 tool_call_id、乱序多工具
结果与错误标记、输入历史不被修改，以及工具附带文字不能提前满足变更确认要求。
错误文案验证还覆盖异常停止后不重试、保留已发生操作的回执。
提交包审计：24 个文件，94,389 字节，0 问题；未提交或推送。
本轮离线测试使用模拟模型响应和合成业务 API，不作为新的真实模型或平台评测结果。

## 第二轮真实模型测试（修复后）

2026-10-10 15:37 开始（中国标准时间）。用户另行授权本轮最多 16 次、10 元。
入口使用 `--suite post-fix --run`，未运行旧 P1。实际发出 16 次请求，全部 HTTP 200；
没有第 17 次实际网络调用。按所有输入未命中缓存计算，用量费用上界估算 ¥0.4059705，
不是服务商账单金额。合计输入 58,569 tokens、输出 936 tokens。

原始报告：[report.json](../evaluation-results/kimi-local-20261010T073709167837Z/report.json)。
核对记录：[analysis.json](../evaluation-results/kimi-local-20261010T073709167837Z/analysis.json)。

| 场景 | 网络请求数 | 实际结果 |
|---|---:|---|
| 恢复待完成的带文字工具调用 | 2 | 完成，保留待完成 ID，接收真实工具结果后回答邮箱 |
| 恢复已完成的带文字工具调用 | 2 | 完成，待完成 ID 清空，基于已有结果回答，无重复客户查询 |
| 默认思考模式的邮箱与声明 ID | 2 | 完成，search → profile → 邮箱回答 |
| 姓名加邮编的不同措辞 | 2 | 完成，模型选择 verify_customer，search → profile |
| 邮箱与声明 ID 不匹配 | 2 | 完成拒绝验证，仅 search，不读取客户详情 |
| 中文问候、仅 ID、补充邮箱 | 4 | 验证与工具链完成；最终回复仍附带未请求的姓名和地址 |
| 客户详情 API 返回合成 503 | 2 | 未完成；模型重复验证一次后耗尽整轮 16 次预算 |

历史恢复场景的工具选择、ID、参数和业务结果来自实际模型与工具执行；由于模型在
这些调用中没有附带文字，驱动在历史 fixture 中明确补入一条测试用的调用说明，
用来检验合法的文字加调用形状。没有伪造模型的邮箱回答。
核对所有 16 次真实请求中的消息，检查了 7 条工具结果关联，缺失或不匹配 ID 为 0。

故障场景的第一次 getCustomer 返回合成 503，模型在第二次真实 HTTP 200 回复中
选择再次 verify_customer。工具再次失败后，需要第三次生成来收尾，但整轮已有
16 次请求，BudgetTransport 在发送前拦截。LiteLLM 将这个本地预算异常包装成
InternalServerError / Connection error，Agent 随后给出 gateway_failure。
这次收尾失败源于本地授权调用数耗尽，不能报告为 Moonshot HTTP 失败或网络断开。
原始报告保留实际宿主包装错误；analysis.json 单独记录预算归因及证据。

本轮后继续改进：测试驱动在预算拦截前记录明确的 budget_stop，避免下次只保留
SDK 包装错误；将数据最小披露要求移到主提示词开头，明确任何语言的邮箱查询
不得附带未请求的姓名、地址、邮编或支付信息。后一次提示词调整尚未再用真实
模型验证。本轮未提交、推送或启动平台评测，未超过授权额度。
