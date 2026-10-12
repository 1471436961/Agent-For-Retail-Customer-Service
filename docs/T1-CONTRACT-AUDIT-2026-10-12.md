# t1 客户查询契约审计

审计日期：2026-10-12（Asia/Shanghai）。审计基线：`76301f14fdf6c3883824a426e2a6417323f7041a`。
后续已按本报告实施修复，见[T1修复说明](T1-CONTRACT-REPAIR-2026-10-12.md)；下文反例描述的是上述基线。
本次检查的是当前提交的 Agent 在 t1 上的契约符合性，并检查影响这条路径的公共组件。
没有将 t1 的一个公开客户查询扩展为 t2 的134项业务需求，也没有把离线结果当作平台评分。

## 结论

当前正常执行路径具备合规完成 t1 的结构：模型解释客户输入，工具先独立搜索，
比对声明 ID，成功后读取详情，把真实回执交回模型，再回答邮箱。
未发现该正常路径存在“先 GET 档案，再用档案中的邮箱证明身份”的顺序错误。
模型网关、工具注册、REST 路径以及工具返回消息的当前接法也符合所核对契约。

但是，不能由此宣称当前 Agent 已完整满足 t1。审计复现了两项依赖模型遵守提示词的
关键缺口，以及两项状态/工具目录一致性问题。平台实际最近一次评测仍未通过。
这些问题与第一次模型调用返回429是不同的执行阶段，不能互相当作根因证据。

## 1. 原始依据及优先级

| 来源 | 本轮核对内容 | 得到的约束 |
|---|---|---|
| [项目入口契约](../AGENT-CONTRACT.md) | 工厂、消息、工具、网关、提交范围 | 实现 Agent；宿主执行工具；走注入网关 |
| [U1–U6](../INTERFACE-CLARIFICATIONS.md:15) | U1接口、U2模型/预算、U3字段 | Python参数为 actions；固定参数由网关注入；运行时名单优先 |
| [原始 agent_contract](E:/Agent-For-Retail-Customer-Service-Materials/materials/framework/agent_contract.md) | 上游英文原件，与仓库中文副本对照 | 保存 context/gateway；接收 UserMessage/MultiToolMessage；回复文字或工具调用 |
| [原始 client_api_contract](E:/Agent-For-Retail-Customer-Service-Materials/materials/framework/client_api_contract.md) | 上游英文原件、工具与REST的映射、会话重放 | 一个工具可组合多个REST请求；同会话可保存确定性工具状态 |
| [原始 intake](E:/Agent-For-Retail-Customer-Service-Materials/materials/uploaded_materials/intake_form.md:182) | entry、lookup、verification、profile access、confirmed decisions | 先身份；ID只是候选；邮箱或完整姓名＋邮编独立核验；之后加载完整档案 |
| [身份核验段](E:/Agent-For-Retail-Customer-Service-Materials/materials/uploaded_materials/intake_form.md:268) | ID路径的独立所有权核验 | 不能靠知道ID、订单、商品或卡后四位替代核验 |
| [档案访问段](E:/Agent-For-Retail-Customer-Service-Materials/materials/uploaded_materials/intake_form.md:311) | 候选定位与full profile分离 | 验证后才能加载订单、地址和支付信息 |
| [会议03](E:/Agent-For-Retail-Customer-Service-Materials/materials/uploaded_materials/meeting_transcript_03.vtt:353) | 失败处理最终裁决 | 不泄露账户是否存在；不设flag/hold/lockout；指向认可的标识及自助恢复 |
| [案例01](E:/Agent-For-Retail-Customer-Service-Materials/materials/uploaded_materials/case_file_01.md:15) | 已批准服务轨迹 | 邮箱匹配、身份verified后profile opened |
| 原始 system_export.zip / policy_contracts.json / CONTRACT-1001 | 信息访问权限 | authenticated customer's own profile；不能跨账户 |
| 原始 api_contract.zip / record_contract_notes.md | 历史业务记录模型 | 历史 user_id、payment_methods映射等不能覆盖当前教学REST字段 |
| [当前OpenAPI](../materials/client_api/openapi.yaml) | 搜索、客户详情、响应schema | POST /v1/customers/search；GET /v1/customers/{customer_id} |
| [案例目录](E:/Agent-For-Retail-Customer-Service-Materials/test-cases-3d81f316-16c1-452d-939f-48776870bd23-zh.md:5) | 开头范围声明 | 完整案例目录不代表下一次push全部运行；t1是接入检查 |

来源边界：本次逐项读取的是与 t1 相关的条款、原始ZIP中的政策/记录说明、相关会议和案例。
没有把此前撰写的项目分析文档当作原始规范，也不声称重新逐张审阅了与t1无关的全部截图或所有业务案例。

原始资料完整性对照：仓库的两个框架文档是中文翻译，原始包为英文；OpenAPI只增加说明和课堂注记，
deployment_manifest只增加x-classroom-notes。移除说明字段后，两者结构与原件一致；公开development_seed完全一致。
原始模型清单同样含openai/enterprise-haiku及max_tokens=1024，未发现另一个应由Agent自行改用的模型配置。
原件哈希与比对结果保存在[审计JSON](../evaluation-results/t1-contract-audit-20261012/report.json)。

## 2. t1 应有的最小执行过程

1. `create_agent()`保存注入的context/model_gateway。
2. 模型根据完整客户消息理解邮箱和声明ID，产生结构化工具参数。
3. 宿主执行`verify_customer(email=..., claimed_customer_id=...)`。
4. 工具调用`POST /v1/customers/search`，body只包含独立邮箱；搜索返回customer_id。
5. 工具比较搜索结果与声明ID；不匹配时终止，不发任何客户详情GET。
6. 匹配后调用`GET /v1/customers/{verified_id}`；邮箱事实来自这次响应。
7. 宿主返回匹配tool_call_id的工具消息；Agent将MultiToolMessage展开为ToolMessage交回模型。
8. 模型根据实际回执回答所请求的邮箱。

两个容易混淆的点：

- “身份先于详情”约束的是业务访问顺序。先调用模型理解输入并不违反它。
- “独立核验”不是要求两个不同的LLM工具调用。Client API契约明确允许一个工具组合多个REST请求，
  因此verify_customer内部先search再GET是允许的。独立性在于用客户另行提供的标识搜索，
  而不是先凭ID读取档案，再把读出的邮箱当作验证依据。

## 3. 当前正常路径逐项核验

| 检查点 | 代码 | 判断 |
|---|---|---|
| 工厂内取得context | [agent.py](../agent/agent.py:7) | 符合 |
| 运行时允许模型、显式generate | [gateway.py](../agent/support_agent/gateway.py:40) | 符合；实际选择enterprise-haiku，无自选固定参数 |
| tau2消息、actions工具对象 | [gateway.py](../agent/support_agent/gateway.py:83) | 符合；无自带HTTP客户端或凭证 |
| 模型理解身份字段 | [prompts.py](../agent/support_agent/prompts.py:3) | 有明确指令；代码未用my name is等句式正则提取客户身份 |
| 结构化selector | [identity.py](../agent/support_agent/domain/identity.py:30) | 邮箱单独，或完整姓名＋邮编；不是自然语言规则解析 |
| 独立搜索与比对 | [customer_tools.py](../agent/support_agent/adapters/customer_tools.py:51) | 参数包含声明ID时，先search、比对后读详情 |
| 兼容lookup_customer | [customer_tools.py](../agent/support_agent/adapters/customer_tools.py:83) | 转调用相同验证路径；ID单独不能读取 |
| URL和字段 | [read_api.py](../agent/support_agent/adapters/read_api.py:78) | 正确REST路径、body、URL编码；读取回执customer_id还要与请求ID相同 |
| 未验证详情读取 | [customer_tools.py](../agent/support_agent/adapters/customer_tools.py:37) | 工具门禁拒绝；不访问API |
| 同一客户会话 | [customer_tools.py](../agent/support_agent/adapters/customer_tools.py:65) | 验证后锁定该客户，禁止切换 |
| 工具回执与调用ID | [gateway.py](../agent/support_agent/gateway.py:90)、[state.py](../agent/support_agent/state.py:59) | 展开MultiToolMessage，保留ID对应，先处理error |
| 文字与工具半双工 | [application.py](../agent/support_agent/application.py:71) | 实时工具调用去除附带文字；符合原始agent_contract |

原始框架没有要求新增OTP登录、短信验证或对只读查询增加客户确认。当前t1正常路径也没有这些额外步骤。
本地128次模型调用/16轮工具调用是工程保护值，不是平台配额；正常t1只需2次模型调用、1个工具批次，未触及这些值。

## 4. 实际复现出的缺口

### A. 用户声明ID的完整传递只靠模型遵守提示词

位置：[customer_tools.py:52](../agent/support_agent/adapters/customer_tools.py:52)、[prompts.py:8](../agent/support_agent/prompts.py:8)。

`claimed_customer_id`默认为空；工具只能比较它实际收到的ID，不能读取完整用户对话。
探针让客户声明`developer_delivered_9002`、提供另一个账户的邮箱，故意模拟模型只调用
`verify_customer(email=...)`。当前程序搜索后读取了`developer_pending_9001`档案，没有比较被遗漏的声明ID。

这是一个可复现的条件性缺口：如果模型漏参，就不满足“匹配用户声明ID”的完整要求。
它仍进行了邮箱独立核验，不是“任何人凭ID直接读取”，也不是对真实平台模型漏参的观察。
当前公开t1中的邮箱和声明ID本来匹配；这一反例不解释已有首轮429。

建议：让模型对“是否存在声明ID”作显式结构化选择，使claimed ID字段不可默默省略，
并强化真实模型对遗漏/冲突/更正的行为验证。字段必填只能降低漏参风险，不能证明模型理解一定正确。
不能通过扫描用户自然语言的正则来补这个缺口，也不能只凭模型说“已核验”就授权。

### B. “邮箱来自真实API”在最终文字上没有本地证据约束

位置：[state.py:119](../agent/support_agent/state.py:119)、[application.py:80](../agent/support_agent/application.py:80)。

正常路径会把真实profile交给模型；但record_reply也接受未经工具调用的普通文本。
探针模拟模型在首轮直接回复输入里的邮箱，当前Agent将它原样返回：API调用0次、verified_customer_id为空、halt_reason为空。
这种实际行为若出现在t1中，就违反“调用API、根据真实记录回答”的要求。

普通问候或追问信息当然需要在验证前发出，不能粗暴禁止所有未验证文本。
应由模型承担语义理解，以结构化的任务/回答类型和真实工具证据支持业务答案；
再验证事实性回答的依据。不要加入邮箱句式正则或固定客户答案表。

现有正常路径测试中，模型工具选择与最终邮箱文字多为预设，无法发现“模型根本不查就回答”的倾向。
本次反例证明的是当前代码没有该防护，不证明真实模型一定会这么做。

### C. 搜索成功但详情失败时，验证状态不一致

位置：[customer_tools.py:79](../agent/support_agent/adapters/customer_tools.py:79)、[state.py:78](../agent/support_agent/state.py:78)、[gateway.py:58](../agent/support_agent/gateway.py:58)。

工具在search成功并比对ID后先锁定身份，这本身正确；但后续GET失败会使整个verify工具返回error。
Agent只在非error的完整验证回执中设置verified_customer_id，因此此时工具集已验证、Agent仍认为未验证。
本次模拟GET503已复现：工具集ID非空，Agent ID为空，下一轮仍只提供verify、lookup、transfer三个动作。

影响：恢复详情读取的能力未明确暴露，模型容易重复验证，不能清楚区分“身份已核实”和“详情服务暂时失败”。
该问题不会导致未验证越权读取，但属于错误恢复设计缺陷。建议把身份结果与详情加载结果分开表达，
用真实工具回执同步这两个状态，保留已核验客户锁定；不放宽错误检查。

### D. 当前轮工具集与回复验证使用的目录不同

位置：[application.py:54](../agent/support_agent/application.py:54)、[application.py:80](../agent/support_agent/application.py:80)。

请求模型时身份未验证只传3个工具，但record_reply验证的是完整27工具映射。
探针模拟模型返回未提供的get_customer_summary，Agent仍将其转发给宿主。
之后工具自身的身份门禁拒绝，API调用0次，所以没有形成身份绕过。

这是目录一致性问题。实时回复应按本轮实际提供的actions验证；历史恢复则可使用完整合法目录，
不能为修复实时问题误伤已有合法历史。

## 5. 复杂度与验证策略问题

本地真实SDK/REST正常链路测得：

| 模型请求 | 消息 | 工具 | 候选可见JSON大小 |
|---|---:|---:|---:|
| 初次识别身份 | 2 | 3 | 4079字节 |
| 已返回profile，只需回答邮箱 | 4 | 27 | 32179字节 |

第二轮携带全部P7写操作工具和完整政策没有违反网关契约，但对于当前查询属于额外负担。
可以由模型选择当前任务所需能力，并保持查询的工具集合更聚焦；不应按t1案例ID切特殊答案路径。
请求大小不是已证明的429原因，不能把此优化描述为已修复平台配额错误。

当前网关诊断还调用validate_request并产生请求统计，这是额外实现，不是U1强制要求。
实际SDK中它是本地约束检查，不进行模型推理；目前没有证据表明重复validate_request会消耗模型配额。
诊断文字直接展示给客户也不是理想产品交互，后续应使用宿主支持的诊断记录能力保留证据，客户只接收可理解的失败说明。

## 6. P1、当前代码和平台429不能混为一谈

此前[本地真实模型记录](LOCAL-REAL-MODEL-2026-10-10.md)验证过P1的真实缺陷：
没有把MultiToolMessage展开为ToolMessage，第二轮provider请求缺少tool_call_id对应返回，真实服务返回400。
当前gateway.project_history已经展开并保留对应ID；本次SDK回归和正常探针也验证了它。

最新已保存平台结果：Job `cb98f7e7-c942-47a5-b13b-ff36605646e2`，提交76301f1，0/1。
报告显示第1次模型调用即失败：enterprise-haiku、max_tokens=1024、3工具、2消息，
宿主RateLimitError/429，detail包含model/quota。该轨迹没有执行验证或详情工具。

因此identity_verified_before_details=False在该轨迹中表示要求未完成，不能解读为“已经先读了详情”。
RuntimeError来自SDK候选broker对宿主失败的包装，不是触发429的业务核验规则。
本次确认的A–D问题仍值得修复，但不能用它们解释这条尚未返回首个模型回复的轨迹。
课程Mentor诊断请求5b9f97c0在本次只读查询时仍无回复。

## 7. 本轮实测证据及后续顺序

- [probe.py](../evaluation-results/t1-contract-audit-20261012/probe.py)：六组契约探针；真实本地tau2 REST与公开种子，模型输出受控。
- [report.json](../evaluation-results/t1-contract-audit-20261012/report.json)：实际API顺序、工具结果、两次模型请求统计、反例状态与原件比对。
- 相关回归：test_customer、test_read_scope、test_gateway_wire、test_agent_protocol、test_reference_runtime、test_sandbox_dialogue，90项通过，6.085秒。
- 本轮没有真实模型调用；没有更改生产Agent、平台绑定、评测范围，未提交或推送。

推荐实施顺序：

1. 先修复已能确定的实时工具目录一致性、身份/详情状态表达；确保故障可恢复且不放宽身份门禁。
2. 强化模型声明ID参数与基于工具证据的事实回答，保持LLM负责自然语言理解，不增加用户句式解析器。
3. 以现有公开接口做正常、缺信息、错ID、模型漏参、未查询回答、详情失败等行为测试。
   受控模型测试验证防护；真实允许模型测试才验证模型如何选择工具和回答，二者分别记证据。
4. 宿主模型调用可用后，再以原绑定t1验证完整真实对话；通过后再推进业务评测。

本报告判断限于t1相关契约与执行组件，不声称完成了所有P7业务功能的重新验收。
