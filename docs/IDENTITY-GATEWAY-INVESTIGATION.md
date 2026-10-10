# 身份验证与模型链路排查

2026-10-10。在 P7 快照上修复，不改变网页绑定、评测范围或业务授权。

## 原始要求

`materials/client_api/openapi.yaml` 的 `searchCustomers` / `getCustomer` 与
`INTERFACE-CLARIFICATIONS.md` U1/U3 共同约束当前实现。原始
`intake_form.md:266` 确认账号 ID 不能独立证明身份，必须匹配账户邮箱或完整姓名和邮编。
材料目录位于仓库外。模型理解客户自然语言，工具只处理结构化参数：独立搜索 → 核对
声明 ID → 读取匹配客户的资料。失败或不匹配时不得读取资料。

## 远程证据及其边界

- 对照历史提交 `269d9c7` 的 CustomerAgent，P0 是直接发 lookup_customer 的确定性模板，
  不调用 context.model_gateway。P1 `dc4dd74` 才改为模型决策。
  因而 P0 通过不能证明平台模型网关已打通，更不能证明模型自然语言理解已验证。
- P0 的 t1：run `38022286570`，Job `319dbc29-a203-40d7-80b4-31a5e9a9b4bf`，1/1 通过。
  实际工具结果的 GiftCard.balance 为字符串 `"500.0"`。
- P7 的 t1：run `38023329509`，Job `7ad55dbd-52d7-472b-8490-21fc3f0c6aac`，0/1 通过。
  轨迹只有用户消息及带 `Reason: gateway_failure` 的停止回复，没有任何工具调用。
- 这证明本次失败发生在首个模型决策阶段，不能由身份检查为 false 推断先读了详情。
  原报告没有异常类型或 HTTP 状态，尚不能确定远程模型服务/接口失败的具体原因。

2026-10-10 根据用户补充再次逐份核对报告和历史源码：

| 推送快照 | GitHub run | 实际轨迹及能确定的结论 |
|---|---|---|
| P1 | 38022437725 | 用户后直接通用停止回复，零工具调用；回复没有原因码 |
| P2 | 38022727341 | 同上 |
| P3 | 38022909013 | 同上 |
| P4 | 38023015270 | 同上 |
| P5 | 38023119439 | 同上 |
| P6 | 38023233199 | 同上 |
| P7 | 38023329509 | 同样停止，完整 JSON 另外含 Reason: gateway_failure，零工具调用 |

P1 的 `_stop` 对 invalid_host_sequence、model_call_limit、gateway_failure、
tool_round_limit、invalid_model_reply 都返回相同文案；不能仅凭旧文案给 P1–P6 的
失败逐一确定分类。P7 原因码则明确来自捕获 gateway.generate 的异常。
网关内部还包括请求验证、消息/schema 转换、服务调用、响应转换及用量记录，
gateway_failure 不能直接等同 HTTP 服务错误或“平台坏了”。

共同的未验证链路是从 P1 开始启用的模型入口，应优先取得该链路的脱敏异常证据。
批量工具结果和余额兼容缺陷都发生在工具执行后，不能解释本次首轮零工具调用。
这些证据更新没有触发新的在线评测。

## 已确认的实现缺陷及修复

1. **工具批次未进入模型请求。** `GatewayAdapter.project_history` 原来保留
   MultiToolMessage；本地固定 tau2 源码 `utils/llm_utils.py::to_litellm_messages`
   只转换单条 ToolMessage。现在将批次展开为单条结果，保留调用 ID、成功/错误标记，
   原始批次仍保留在可序列化状态中。这是确定的缺陷，但不是已证明的远程首次调用失败根因。
2. **已观察到的余额类型差异。** 仅在 getCustomer 的礼品卡余额字段兼容有限 JSON
   数字字符串，转为数值后仍通过完整严格模型校验。保留 response_warnings 中的字段路径
   与原余额值；不修改后端响应。非法值、布尔/空值、非有限值、溢出和非零下溢均失败，
   不默认为零。其他金额字段及正常 OpenAPI 模型保持严格。U3 明确允许这样的有证据兼容。
3. **诊断被丢弃。** 首次网关失败仅保留允许名单中的异常类型和合法整数 HTTP 状态。
   不保留异常原文、参数、响应体、URL、密钥或未识别的类名。失败仍终止，不重试，
   不运行身份解析后备规则；状态恢复保留同一脱敏诊断。

## 测试缺口及本次验证

旧测试模拟 `tau2.hyper.agent_context.generate`，未经过模型服务请求序列化，因而漏掉
MultiToolMessage 的转换问题。新增测试仅在最终 `tau2.utils.llm_utils.completion`
边界模拟服务，真实执行入口、网关约束、tau2 消息/schema 转换、宿主工具调用和回复。

身份链路首轮修复新增 6 项网关链路/诊断测试及 3 项余额兼容/身份边界测试，当时 120 项独立离线测试全部通过，
包括匹配前只搜索、ID 不匹配零详情读取、多个乱序工具结果和错误标记进入真实模型请求、
原始批次保留、异常敏感文本不写入回复/状态，以及失败不重试。

测试使用合成账户与模拟模型决策，不证明自然语言理解或远程接入已通过。没有把公开案例的
客户 ID、邮箱或预期答案写入运行时代码或提示词；离线测试只使用既有合成账户。尚未推送本次修复，
尚未运行新的在线评测。需在当前 t1 绑定上用新诊断验证远程失败原因与修复效果。

用户随后要求全面检查，新增发现、修复和最新验证数字另见 [全面复核记录](COMPREHENSIVE-REVIEW.md)。

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
```

## 118e46b 首轮失败的继续排查

2026-10-10，已推送 `118e46b7930aa68c38f1ae27409beac4b62d7851`。
GitHub run `38028056501`，Job `aff86df4-a8de-4204-9b6f-d0f0ad8dae1e`，
t1 通过 0/1。提交和报告保存步骤成功；实际轨迹首轮返回
`gateway_failure / RuntimeError`，零工具调用。

检查该任务的 GitHub 日志及附件：只有排队记录、最终报告及 report.json/report.md，
没有模型调用的 traceback、错误响应或 HTTP 状态。经用户明确授权向 Parallight Lab
发送三个相关源码文件，查询同一 Job 的本人记录。该接口返回相同 0/1 摘要，明确
不提供原始响应或完整调用轨迹。公开接口澄清 U1/U2 版本
`d58e1786ae00` 与 Agent contract `b491f1496768` 支持当前 Python
`model/messages/actions` 调用方式及省略固定参数的做法；当前公开资料版本不证明
与本次运行环境完全一致。

已定位的证据丢失链路（本地固定 tau2 源码）：

1. `sandbox/sealed_runner.py::CandidateProcess._model_response` 将宿主异常转为
   `{type, message}` 错误响应。
2. `sandbox/candidate_server.py::_brokered_generate` 只用 message 构造 RuntimeError，
   丢失原异常类型和 status_code 属性。请求参数错误、模型认证错误或响应解析错误
   均可能成为同一个外层 RuntimeError。
3. 当前提交的 `diagnose_gateway_failure` 又丢弃 message，只留下外层类型。
   因而当前报告无法区分这些原因。另一个实现问题是 project_history 与 generate
   在同一个 try 中，历史转换异常也被标成 gateway_generate。

本次本地改动将历史转换与模型调用分开；只计数实际网关调用。诊断根据真实 SDK
固定错误格式保留有限原因码、可信模块/函数定位、消息前缀报告的允许异常类型和
HTTP 状态。`reported_exception_type` 表示错误文本中报告的类型，不声称已恢复
原始异常对象。不记录任意错误原文、变量名、模型名、URL、客户资料或凭证。
未识别的 broker 错误明确标为 `unclassified_broker_error`，不擅自推断原因。
失败继续终止，不自动重试，不替换平台模型或绕过中介。

新增测试经过真实 candidate broker 和宿主错误响应转换，验证宿主参数错误、
LiteLLM BadRequestError/400、缺失服务端模型配置、未知错误，以及敏感内容过滤、
状态恢复不重试和历史转换不调用模型。测试错误来自合成宿主，不代表远程已经
发生其中某一种错误。诊断改动尚未提交/推送，不能把本地测试当成在线评测通过。

本次新增 7 个测试，完整离线测试 138 项通过；提交包审计无错误。在线根因确认
还需要将诊断版本推送到同一 main/t1 绑定后，读取同一推送任务的实际错误分类。
