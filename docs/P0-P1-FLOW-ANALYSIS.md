# P0 → P1：身份查询与模型调用断点分析

2026-10-10。只分析和本地重放；没有提交、推送、调用真实模型、修改绑定或评测范围。
保留先前尚未提交的诊断改动。本次不修改 Agent 运行时代码。

## 比较的版本与要求

- P0：`269d9c7`。实际成功评测的 `1543566` 仅有入口说明文字差异。
- P1：`dc4dd74`。实际失败评测的 `ae55232` 的 agent/ 与其一致。
- 当前已提交版本：`118e46b`；重放此提交，不混入未提交的诊断补丁。

用户截图要求：模型理解邮箱和声明 ID → 请求 verify_customer → 工具独立搜索邮箱并
核对声明 ID → 匹配后读详情 → 根据实际工具结果回答。工具负责结构化权限判断，
模型负责自然语言理解；不恢复 P0 的客户 ID/邮箱正则。

P1 尚未命名 verify_customer；其 lookup_customer 具有相同的邮箱搜索、声明 ID
核对和详情读取顺序。工具名称差异不是这里发现的接口错误。

## P0 与 P1 实际改变了什么

P0 的 CustomerAgent 不访问 model_gateway。它用固定格式解析输入，直接返回
lookup_customer 工具调用，收到结果后用 customer_reply 从返回 JSON 中取 email。
其身份工具调用 POST /v1/customers/search，比较找到的 customer_id 与声明 ID，
然后才调用 GET /v1/customers/{id}。P0 的在线成功验证了业务 API 可用和这个顺序，
没有验证模型服务链路。

P1 删除上述自然语言正则，使用注入的 ModelGateway。它先把 system/user 历史和
lookup_customer schema 发给模型，再把模型提出的调用交给宿主执行，之后再次
请求模型根据工具结果作答。这是所需的模型 Agent 方向，但实现的消息适配有错误。

## 本地重放方法及边界

脚本位于 E 盘 `.git/enterprise-diagnostics/compare_p0_p1.py`。使用 git archive
提取各提交的 agent/ 到独立目录，分别启动 Python 进程，避免模块缓存和当前改动污染。
使用本地已固定 tau2 的真实 ModelGateway、candidate broker、宿主
CandidateProcess._model_response、消息转换、Tools 和 ClientAPI。只在最终模型
completion 边界提供合成响应；业务数据使用既有合成账户。

因此它可以证明实际发送的消息结构、工具执行顺序和 Agent 自己的协议处理。
它不证明真实模型理解能力，不重现平台的真实服务配置、凭证或原始异常。
在检查缺失工具结果时，模拟服务主动进行严格协议校验；该 ValueError 是本地
校验错误，不能作为线上宿主确实返回过该错误的证据。

## 已复现缺陷一：工具结果没有进入第二次模型请求

P1 GatewayAdapter.project_history 保留 MultiToolMessage，未将批次展开。
固定 tau2 的 utils/llm_utils.py::to_litellm_messages 只处理 UserMessage、
AssistantMessage、ToolMessage 和 SystemMessage，忽略 MultiToolMessage。

| 提交 | 第一次请求 roles | 第二次请求 roles | API 路径顺序 | 重放结果 |
|---|---|---|---|---|
| P0 | 无模型请求 | 无模型请求 | search → profile | 从工具返回 email 作答 |
| P1 | system,user | system,user,assistant | search → profile | 缺失 tool 结果，严格校验拒绝，gateway_failure |
| 当前 118e46b | system,user | system,user,assistant,tool | search → profile | 根据工具结果作答 |

P1 第二次请求保留了 assistant.tool_calls，却丢失对应 tool_call_id 的工具结果。
即使第一次模型正确选择工具、身份 API 已成功执行，模型也收不到事实，因此无法
可靠完成截图第 4 步。当前 118e46b 已将批次展开，这一具体问题已修复。

## 已复现缺陷二：自行拒绝 SDK 接受的文字加工具调用

P1 state.record_reply 使用 `has_text == bool(calls)` 拒绝同时存在的 content 与
tool_calls。SDK 的 AssistantMessage 支持这两个字段，原 AGENT-CONTRACT 没有
规定互斥；该限制是本地实现自行添加的。

重放让模型返回说明文字和正确 lookup_customer 调用：tau2 宿主正常返回，
host_errors 为空；P1 随即判为 invalid_model_reply，第一条工具调用未交给宿主，
API 请求为零。当前 118e46b 保留工具调用、去除执行前说明，完成 search/profile
和最终回答。P1–P6 的通用停止文案不能区分这种本地拒绝和模型请求异常。

## 这些缺陷与最新线上首轮失败的区别

P0 run 38022286570 通过 1/1。P1 run 38022437725 通过 0/1，实际轨迹没有工具调用，
只有通用停止回复；原回复没有标记停在哪个原因分支。

118e46b run 38028056501，Job aff86df4-a8de-4204-9b6f-d0f0ad8dae1e，仍为 0/1。
轨迹首轮为 gateway_failure / RuntimeError，零工具调用。上述三个报告的
environment_version 相同，为 hyper-lab-v1-90c863ee6bf1ee8c9b72c6a58a6066d8730c713b6cbd7d0f785cf1e158bc6f4f。

这条最新线上失败发生在身份工具执行前：不是 search 的失败，不是声明 ID 比较
的失败，也不是详情响应 schema 失败，更不是第二轮缺失工具结果。这些操作尚未发生。
当前代码在普通和混合模型输出两种本地重放中都能完成四步，说明上述两处 P1
缺陷不足以解释最新的线上首轮错误。

P1 的首轮 model/messages/actions/call_name 用法符合本地公开 U1 契约。模型取自
注入的 available_models；固定 max_tokens 由网关注入；发送给宿主的 kwargs 仅有
max_tokens 和 tool_choice。首轮经本地真实中介及消息/schema 转换正常到达模拟
服务。没有找到可以据此断言线上一定错误的首轮参数差异。

异常包装与诊断丢失只解释为何看不到原始错误，不解释为何产生错误。RuntimeError
还可能来自 broker 的响应 ID 不匹配，不能只凭这个外层类型认定宿主服务已抛出
某一具体错误。需要第一轮实际 broker 的 ok/error 响应或宿主 traceback 才能确定。

## P1 验收失误与下一步取证目标

P1 当时的测试替换 tau2.hyper.agent_context.generate，直接返回合成 tau2 消息，
跳过 candidate broker、宿主转换和最终模型请求。测试甚至断言保留的
MultiToolMessage 内有正确结果，却没有检查转换后结果已全部消失。这使有缺陷
的消息闭环被报告为本地完成。

下一次在线取证应只针对首次模型请求：区分本地历史转换、broker 响应关联、宿主
参数/消息/schema 校验、模型路由及服务响应、响应转换和网关用量记账。必须取得
该次实际错误的安全分类/HTTP 状态，不能把本地人为制造的错误指认为平台根因。
先前准备的诊断补丁仅是取证手段，不能作为宿主错误根因的修复结论。

当前用户要求暂不提交，因此没有启动新的在线运行。现有本人报告、GitHub 日志
和已查询的 Parallight 摘要都未提供这条首次错误的原始响应。不会据此猜测认证、
配额或参数问题，也不修改平台配置来试错。

随后用户授权本地使用独立 Moonshot API key，最多 16 次、人民币 10 元。
真实模型复现了 P1 第二轮缺失工具结果导致的 HTTP 400 / BadRequestError；
当前 Agent 的五组身份对话完成。详见
[真实模型调用记录](LOCAL-REAL-MODEL-2026-10-10.md)。此后续证据来自真实服务，
与上文的合成协议校验分开记录；没有提交、推送或重新运行平台评测。
