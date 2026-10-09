# 项目分析与证据结论

分析日期：2026-10-09。本文记录已提供材料能支持的结论；实现选择放在配套 [实施规划](IMPLEMENTATION-PLAN.md)。本次只做分析与规划，未实现业务、调用模型或更改评测范围。

## 1. 项目目标与当前基线

目标是构建消费零售客服 Agent：理解客户的自然语言和多轮意图，以真实记录为依据，选择工具完成查询、修改、取消、退换及必要的人工转接。模型负责语义理解、澄清、依据客户明确表达的偏好筛选和任务编排；工具负责身份与归属检查、精确计算、业务前置条件和正式 API 操作。

这不是扩展现有正则提取器，也不是为公开案例逐题编写回答。业务约束可以确定性校验，但不能用大量关键词分支替代 Agent 的理解和决策。

| 项目项 | 已确认基线 |
|---|---|
| 仓库 | `1471436961/Agent-For-Retail-Customer-Service` |
| 分支 | `main` |
| binding | `3d81f316-16c1-452d-939f-48776870bd23` |
| domain / language | `retail_plus` / `python` |
| 当前任务 / 案例范围 | `t1` / `case_ids: null`；本次不更改 |
| 项目绝对路径 | `E:\Agent-For-Retail-Customer-Service` |
| 原始资料目录 | `E:\Agent-For-Retail-Customer-Service-Materials`，位于仓库外 |
| 现有业务能力 | 确定性的只读客户查询示例 |
| 已有验证 | 先前接入提交的 t1 为 1/1 通过；不代表业务评测通过 |
| 本机运行条件 | Python 3.12.13；后续已在项目 `.venv/` 安装官方 Hyper-tau 的 `tau2==1.0.1`，远程环境仍由平台提供框架 |

现有代码可保留的部分是薄入口、分层目录、独立验证后读取客户、URL 编码与 HTTP 状态检查的思路。需要替换的是业务主体：

| 文件 | 当前行为 | 实现缺口 |
|---|---|---|
| [agent.py](../agent/agent.py) | 直接创建 `CustomerAgent()` | 未在工厂取得并保存注入 context |
| [application.py](../agent/support_agent/application.py) | deterministic t1 example；固定文本流程 | 没有网关决策、完整历史、多个工具结果、多请求编排 |
| [state.py](../agent/support_agent/state.py) | turn、单个 pending call、customer_id | 缺验证状态、完整消息、待确认方案和执行记录 |
| [customer.py](../agent/support_agent/domain/customer.py) | 示例 ID/邮件提取及固定回复 | 不能扩展为规则式业务解析器；错误不能全部概括为客户不存在 |
| [customer_tools.py](../agent/support_agent/adapters/customer_tools.py) | 一个 `lookup_customer` 工具 | 缺其他身份路径、订单、目录、变更、人工转接 |
| [customer_api.py](../agent/support_agent/adapters/customer_api.py) | 邮件搜索→核对 ID→读资料 | 需扩展契约适配，同时保留验证在前的顺序 |
| [test_customer.py](../tests/test_customer.py) | 六项基础测试 | 无真实写入防重、模型循环、完整对话或业务覆盖 |

尤其现有 `test_transport_propagates_errors_without_retrying_writes` 实际调用 GET，不能作为写入不重试的证据。

## 2. 分析范围与材料使用方法

已核查仓库入口、全部现有 Agent 代码和测试、项目/框架契约、接口澄清、OpenAPI 的全部 14 个 operation、提交和评测脚本。业务材料包括 intake、reference、14 封邮件、5 份会议记录、Slack 导出（76 次调用、460 条去重消息）、48 份历史案例、134 项公开任务说明、25 页培训 PDF、5 张流程图；60 张界面截图完成用途浏览，并对关键商品、订单、物流和退换界面放大核对。

原始业务材料统一位于：`E:\Agent-For-Retail-Customer-Service-Materials\materials\uploaded_materials`。公开任务说明位于：`E:\Agent-For-Retail-Customer-Service-Materials\test-cases-3d81f316-16c1-452d-939f-48776870bd23-zh.md`。

解释材料时需要区分以下权威边界：

1. **运行接口**由 `AGENT-CONTRACT.md`、`INTERFACE-CLARIFICATIONS.md` 和当前框架契约规定。
2. **REST 参数与正常字段**以当前 OpenAPI 为准。历史工单或 UI 中存在的字段不等于当前 API 提供它。
3. **业务政策**取已批准、适用于 consumer Care、仍有效的决定；同一问题按最新有效的有日期裁决解释。不是简单按整个文件更新时间排序。
4. **intake 中 Proposed / TBD / 空白**不是批准政策；后续已签署会议可补足原先 TBD。
5. **历史案例**用于理解行为；`QA approved` 不意味着一定提交变更。很多是查询、草稿、纠正、暂停片段。
6. **公开案例**用于建立能力覆盖，不能变成运行时题号、客户 ID、商品金额到答案的映射。
7. **截图**辅助理解业务语境，不能用截图里的商品总数、余额或 UI 功能代替实时 API。

证据入口：[intake:18](E:/Agent-For-Retail-Customer-Service-Materials/materials/uploaded_materials/intake_form.md:18)、[reference:5](E:/Agent-For-Retail-Customer-Service-Materials/materials/uploaded_materials/reference_document.md:5)、[契约分工](../INTERFACE-CLARIFICATIONS.md)、[当前 Care schema 会议](E:/Agent-For-Retail-Customer-Service-Materials/materials/uploaded_materials/meeting_transcript_02.vtt:665)。`reference_document.md` 只是简短封面，不能声称其中包含完整运营手册。

### 培训材料的有效性

培训 PDF 的有效操作卡包括 C2、C5、A3、I6：先收集有效取消理由；取消前完整回顾及确认；完整新地址回读及纠错后重新确认；一次性商品修改前收齐清单、最后询问有无补充并等客户回答。

G1、C8、G4、A6、G7、I9 的附加评分要求属于 calibration-only；R1/R3 已退休；W2/W5 属于 wholesale，不能迁移到 consumer。卡片降级不等于撤销会议已签署的 grounding、澄清、完整 recap、语义确认、一次性完整收集和 calculator 等通用控制。

来源：[slide_deck.pdf](E:/Agent-For-Retail-Customer-Service-Materials/materials/uploaded_materials/slide_deck.pdf) 第 3、5、8、10、13、25 页；[全局控制最终确认](E:/Agent-For-Retail-Customer-Service-Materials/materials/uploaded_materials/meeting_transcript_01.vtt:1165)。

流程图文件名与板上 Frame 编号不一致，引用时需使用对应关系：

| 文件 | 内容 |
|---|---|
| `process_map_02.png` | Frame 1：状态资格入口，当前 v0.9 |
| `process_map_03.png` | Frame 2：取消与退款 |
| `process_map_05.png` | Frame 3：订单地址与付款方式 |
| `process_map_01.png` | Frame 4：一次性 pending 商品修改 |
| `process_map_04.png` | Frame 5：停车场、历史裁决；未批准提议不能生效 |

## 3. 平台接口：必须照约定实现

### 3.1 Agent 与模型循环

- `agent/agent.py` 导出 `create_agent()`，只在这里调用 `tau2.hyper.agent_context.get_agent_context()`，将需要的 context 能力保存在实例。
- 实例实现 `get_init_state(message_history=None)` 和 `generate_next_message(message, state)`；后者返回 `(AssistantMessage, next_state)`。
- 输入可能是 `UserMessage` 或 `MultiToolMessage`。必须保留历史、处理多个工具结果、按调用 ID 关联；先处理 `error`，再解析 JSON 内容。
- `ToolCall.arguments` 是对象，`ToolMessage.content` 才是 JSON 文本；不能混淆。
- assistant 一次输出文字或 tool calls，不能同时输出两者。无工具调用代表本次回复完成，不等于整个会话终止。
- Python 网关签名：`generate(*, model, messages, actions=None, tool_choice=None, call_name=None, **kwargs)`。
- `messages` 是 tau2 消息对象；`actions` 是 `context.action_interface.available` 或 `.select([...])` 提供的 tau2 工具对象。不能传 TypeScript 的 `tools`，不能使用 OpenAI HTTP 的 `choices` 解包方式。
- 模型可提出工具调用，宿主执行后回传真实结果。提出调用本身不代表操作完成。

来源：[入口契约](../AGENT-CONTRACT.md)、[U1](../INTERFACE-CLARIFICATIONS.md)、[框架 Agent 契约](../materials/framework/agent_contract.md)。

### 3.2 模型、资源与打包

- 允许模型取运行时 `available_models`；部署 manifest 只是快照。模型固定参数由网关注入，不能覆盖，较小的 `max_tokens` 也不例外；`one_of` 参数按实际允许选项选择。
- `usage` 可能为空；缺失不是零。`cost=0` 不证明免费，研究 credits 不等于美元、当前账户额度或费用授权。
- 只递归提交 `agent/`：最多 128 文件、每文件 256 KiB、合计 2 MiB。根目录 docs、tests、外部原始资料不会自动进入 Agent 运行包。
- 项目要求 Python 3.12+；运行环境无任意联网、文件系统只读、非 root，不能临时 pip 安装依赖。
- 必要的精简政策需放入 `agent/` 可提交包，或者使用经验证的注入资源接口。不得猜 `resources` 的未公开读取方法。

来源：[PROJECT](../PROJECT.md)、[提交约束](../AGENT-CONTRACT.md)、[模型与预算澄清](../INTERFACE-CLARIFICATIONS.md)。

### 3.3 完整 REST 能力

当前共 14 个 operation：6 个读取、7 个业务写入、1 个转人工。路径参数全部 URL 编码，含 `#` 的订单号编码为 `%23`。

| 类别 | 方法与路径 | 关键请求 |
|---|---|---|
| 读取 | `POST /v1/customers/search` | email，或 first_name + last_name + postal_code |
| 读取 | `GET /v1/customers/{customer_id}` | 已验证客户 |
| 读取 | `GET /v1/catalog/products` | 无臆造的分页/筛选参数 |
| 读取 | `GET /v1/catalog/products/{product_id}` | 商品及其 items |
| 读取 | `GET /v1/catalog/items/{item_id}` | 单个变体 |
| 读取 | `GET /v1/orders/{order_id}` | 自有订单 |
| 写入 | `PUT /v1/customers/{customer_id}/default-shipping-address` | 完整 Address |
| 写入 | `PUT /v1/orders/{order_id}/shipping-address` | 完整 Address |
| 写入 | `PUT /v1/orders/{order_id}/payment-method` | payment_method_id |
| 写入 | `POST /v1/orders/{order_id}/cancellations` | reason |
| 写入 | `POST /v1/orders/{order_id}/item-modifications` | replacements + payment_method_id |
| 写入 | `POST /v1/orders/{order_id}/returns` | item_ids + refund_payment_method_id |
| 写入 | `POST /v1/orders/{order_id}/exchanges` | replacements + payment_method_id |
| 转接 | `POST /v1/conversations/{conversation_id}/transfers` | summary |

来源：[当前 OpenAPI](../materials/client_api/openapi.yaml)。不要按 HTTP 方法猜副作用：search 虽然是 POST，但明确属于可重试的读取。

工具继承 `ClientAPIToolKitBase`，使用 `@is_tool(ToolType.READ/WRITE/GENERIC)` 和注入的 `client_api.request(method, path, query=..., body=...)`。可以把多个读取组合成一个有业务含义的工具；不提供任意 method/path/body 的自由 HTTP 工具。

### 3.4 数据语义与执行边界

| 易错点 | 正确处理 |
|---|---|
| 地址 | `address_line_1/city/region/country/postal_code` 必需；`address_line_2` 可缺省或 null。完整 PUT，不能直接发局部更新；没有 recipient name 字段 |
| 数量 | `item_id` 是变体 ID，重复数组项表示多件；保留重复和顺序，不用 set 去重，不发明 quantity/order_line_id |
| 相同 ID 的不同实例 | 不同成交价等实例区分没有精确定位接口；不能捏造行 ID 或猜后端顺序 |
| 商品归属 | 单个 CatalogItem 没有 product_id；通过商品的 items 或订单的 product_id 校验同产品 |
| 金额 | 正常是 JSON number；bool、null、非有限数、非法数不能默认为 0 |
| 差价 | 当前后端按请求顺序 float 累加新价减原价，最后 `round(total, 2)`；不是逐行舍入或 Decimal HALF_UP |
| 支付流水 | 联读 transaction_type、amount、payment_method_id；不能只把 amount 全部相加 |
| 物流 | fulfillments 的 tracking_id 和 item_ids 都是数组，保留对应分组，不臆造单一 tracking 字段 |
| 状态 | 只有契约列出的七种正常状态，没有 `shipped`；未知状态阻止相关写入 |
| 退款事实 | 申请成功、退款已发起、资金已到账是不同事实 |

来源：[U3–U6](../INTERFACE-CLARIFICATIONS.md)。

所有业务写入和转接都不保证幂等，禁止自动重试，包括 PUT。读取可按契约做有限重试。400、404、409、422、413、502 应保留差异；不能全部解释为“没找到”。query/body 合计最大 1 MiB，响应正文最大 4 MiB；不能裁剪或拆分写入绕过大小限制。

正常写入响应按强一致契约处理；同时需要应对澄清中明确的正式环境结构差异、异步回执、暂时旧读。完成、仅受理、明确失败、结果未知要分开：结果未知时保留脱敏证据并只读核查，不重放写入，不捏造异步状态 API。

### 3.5 工具重放是架构约束

评分会在新的 toolkit 和后端上按原顺序重放已记录调用。行为只能依赖后端状态、参数及本会话之前的工具调用。不能依赖实际时钟、随机数、其他会话或不可重建的 Agent 私有内存。

Agent state 与 toolkit state 必须分别设计。toolkit 可保存由先前工具调用确定性建立的已验证身份、准备方案和回执；不能假设它能读取 Agent 的完整对话。当前可信 `client_api.context` 明确公开的是 conversation_id，转接必须从这里取得，不能让模型或客户传入。

来源：[Client API 契约：上下文、错误、重试、重放](../materials/framework/client_api_contract.md)。

## 4. 当前有效业务政策

### 4.1 身份、范围与事实依据

一次会话只处理一个已验证客户本人账户，支持该客户多个连续请求。唯一账户 ID 只是候选标识；ID 单独不够，必须匹配 profile email 或完整 first + last + ZIP。查找与 full profile 读取分离，验证成功后才读取账户、订单、地址、支付方式。电话、订单号、熟悉商品、家人关系都不是替代验证。

验证失败不能泄露账户存在与否、订单或地址；请求受认可的身份信息，无法提供时引导网站自助恢复。不设材料未授权的 flag、hold、固定失败次数或锁定时间。转人工明确请求按转接政策处理，不强行增加身份或排障前置条件。

所有订单访问与写入都检查归属；提供另一个客户订单号不获得其访问权。客户自述“刚收到”“取消这件”不能覆盖后端状态，Agent 应先确认记录，再解释正确业务路径。

来源：[intake:133](E:/Agent-For-Retail-Customer-Service-Materials/materials/uploaded_materials/intake_form.md:133)、[intake:266](E:/Agent-For-Retail-Customer-Service-Materials/materials/uploaded_materials/intake_form.md:266)、[5 月 28 日验证失败裁决](E:/Agent-For-Retail-Customer-Service-Materials/materials/uploaded_materials/meeting_transcript_03.vtt:341)。

### 4.2 状态决定可做操作

| 实际订单状态 | 允许方向与限制 |
|---|---|
| pending | 完整确认后整单取消、改订单地址、换支付方式、一次性修改同产品规格 |
| pending (items modified) | 商品修改已占用一次机会；不能当普通 pending 再取消、改地址/支付或追加商品修改 |
| processed | 查询、解释限制；不能修改、取消或提前退换；客户要求升级时可转人工 |
| delivered | 按政策一次性申请退货或同产品换货；不再走 pending 修改/取消 |
| cancelled | 不恢复，不重复取消 |
| exchange requested | 不追加第二次换货，不承诺修改既有请求 |
| return requested | 不追加第二次退货，不承诺修改既有请求 |

默认地址是独立客户资料更新，不受某个订单状态决定；不能自动连带修改任何订单地址。每个新请求和紧邻写入时重新检查必要状态，未知状态停止相关写入。

来源：[状态澄清](../INTERFACE-CLARIFICATIONS.md)、[Frame 1](E:/Agent-For-Retail-Customer-Service-Materials/materials/uploaded_materials/process_map_02.png)、[Frame 4](E:/Agent-For-Retail-Customer-Service-Materials/materials/uploaded_materials/process_map_01.png)。

### 4.3 回顾、确认、一次性收集

每个账户/订单记录变更之前，先给出完整相关 recap：哪个记录、做什么、全部受影响商品/数量，以及相关金额、付款/退款去向或完整新地址。随后收到明确指向当前方案的确认才执行。只读查询无需形式确认。

确认是语义问题：“yes”可能是在确认邮件、回答金额问题，也可能是在批准操作；不能用肯定词白名单或“最近出现过 yes”判断。新增商品、改地址、改支付、改操作种类、暂停或撤回都会改变原方案的可执行性。变化后重新准备与回顾，不能复用旧确认。

pending 商品修改、delivered 换货及退货需要一次收齐同一订单的完整清单。商品修改应明确最后询问是否还有补充，并等待回答；不能在发问的同一轮提交，也没有 60 秒或 15 分钟补改特例。

来源：[确认与语义](E:/Agent-For-Retail-Customer-Service-Materials/materials/uploaded_materials/meeting_transcript_01.vtt:301)、[不能按位置推定同意](E:/Agent-For-Retail-Customer-Service-Materials/materials/uploaded_materials/meeting_transcript_01.vtt:593)、[完整收集](E:/Agent-For-Retail-Customer-Service-Materials/materials/uploaded_materials/meeting_transcript_01.vtt:901)、[地址纠正实例](E:/Agent-For-Retail-Customer-Service-Materials/materials/uploaded_materials/case_file_46.md:38)、[退货界面](E:/Agent-For-Retail-Customer-Service-Materials/materials/uploaded_materials/screenshot_46.png)。

### 4.4 七类变更的精确边界

| 业务 | 当前要求 |
|---|---|
| 默认地址 | 本人账户、完整地址、单独明确授权；不连带改订单 |
| 订单地址 | 仅 pending；回读完整新地址，纠正后重新完整回读和确认；不自动修改默认地址 |
| 付款方式 | 仅 pending；换成一个不同的已保存方法，不添加新方法、不拆付；礼品卡覆盖整单金额。新方法全额 charge 成功后原方法全额 refund；decline 保持原状；成功仍 pending |
| 取消 | 仅 pending 整单；先收理由。仅 `no longer needed` / `ordered by mistake`；接受清楚同义表达，不能把价格投诉硬映射为不需要。完整回顾订单、金额、原退款去向后确认 |
| pending 商品修改 | 同产品、现有可用变体；不改数量；一个完整 replacements 列表一次提交；一个付款方法结算差价，礼品卡需覆盖全部正差价；成功变为 pending (items modified) |
| delivered 退货 | 收齐本单所有退货 item_ids；按原成交记录处理；目的地只能原支付方式或请求打开前已存在的 saved gift card，同时可选时让客户决定；不能借另一订单支付方式或会话中新增卡扩大资格 |
| delivered 换货 | 同产品可用变体；完整 replacements 一次提交，保留重复原 item；一个已保存付款方法处理价差，退款可到非原支付的已保存方法；提交后不可改目的地或追加 |

取消理由来源：[4 月 29 日裁决](E:/Agent-For-Retail-Customer-Service-Materials/materials/uploaded_materials/workspace_export.json:1447)。退货/换货目的地来源：[5 月 4 日 RD5](E:/Agent-For-Retail-Customer-Service-Materials/materials/uploaded_materials/workspace_export.json:1507)。支付和地址来源：[Frame 3](E:/Agent-For-Retail-Customer-Service-Materials/materials/uploaded_materials/process_map_05.png)。

### 4.5 金额规则不能合并为一种退款逻辑

- **取消**：业务取消接口原子处理状态和退款；多笔原 charge / top-up 逐笔原金额原路退款，即使同一张卡也不能净额合并。不另造 refund API，也不自行追加退款。
- **换支付方式**：整单全额新扣款与整额旧退款，不是新旧价差。
- **退货**：原成交价与符合资格的退款目的地；请求受理不等于款项到账。
- **修改/换货**：按有序 replacements 计算差额，正负方向明确；执行后以正式回执为最终事实。
- **计算**：新合计、退款合计、差价等非平凡推导调用 calculator；复述 API 已给出的数字不算推导。不存在“小于 10 元可以心算”豁免。
- **时间**：取消及换支付方式产生的退款，当前 card / PayPal 为 3–6 个工作日，gift card 即时；取消时主动说明相关时间。旧取消 6–9 个工作日和 3 月净额取消规则已被替代。退货/换货不能仅凭这两类操作的时限作同样承诺，需以适用材料或正式回执为依据。

来源：[取消原子语义](E:/Agent-For-Retail-Customer-Service-Materials/materials/uploaded_materials/email_06.eml:125)、[逐笔退款新裁决](E:/Agent-For-Retail-Customer-Service-Materials/materials/uploaded_materials/email_11.eml:62)、[退款时间更新](E:/Agent-For-Retail-Customer-Service-Materials/materials/uploaded_materials/email_12.eml:77)、[支付替换](E:/Agent-For-Retail-Customer-Service-Materials/materials/uploaded_materials/email_09.eml:92)、[换支付退款时间](E:/Agent-For-Retail-Customer-Service-Materials/materials/uploaded_materials/email_07.eml:101)、[calculator](E:/Agent-For-Retail-Customer-Service-Materials/materials/uploaded_materials/meeting_transcript_01.vtt:1113)。邮件原文件采用 quoted-printable 编码，分析按解码后的正文及其日期解释。

### 4.6 不提供的能力与人工转接

不能改 quantity、拆 shipment、改账户 email、创建付款工具、发放新礼品卡、下新订单、部分取消、增加自定义客户字段、把 address2 当备注、建立跨会话隐藏客户记忆。可以解释 cancel/reorder 方案，但这不授权自动整单取消或代下单。历史案例中的收据标题、邮件、完整地址簿等不等于 REST 具备相应操作。

人工转接仅有三类业务依据：客户明确要求人；不支持的请求在说明后客户要求升级；事实已明确但当前政策确实没有处理规则。复杂、多步骤、愤怒、低信心、工具慢本身不是额外触发条件。请求人工无需强迫客户先说明理由或完成排障，不能承诺人工突破政策。客户明确请求人工后，暂停尚未执行的不可逆操作；只有客户明确要求同时完成该操作且已经确认时才可继续，不能等转接成功后才停止原写入计划。

转接接口成功返回 `201 / accepted` 后停止后续客户 API 调用；固定客户文案为：

> YOU ARE BEING TRANSFERRED TO A HUMAN AGENT. PLEASE HOLD ON.

不自行翻译、改写或追加等待时间。实际 active config 如提供更精确版本则按该版本核实使用。失败时不能宣布已经成功转接。

来源：[封闭 schema](E:/Agent-For-Retail-Customer-Service-Materials/materials/uploaded_materials/meeting_transcript_02.vtt:321)、[转接三类依据](E:/Agent-For-Retail-Customer-Service-Materials/materials/uploaded_materials/meeting_transcript_04.vtt:289)、[请求人工后的写入暂停](E:/Agent-For-Retail-Customer-Service-Materials/materials/uploaded_materials/meeting_transcript_04.vtt:489)、[转接固定文案](E:/Agent-For-Retail-Customer-Service-Materials/materials/uploaded_materials/meeting_transcript_04.vtt:801)、[API 转接契约](../materials/framework/client_api_contract.md)。

## 5. 公开需求揭示的核心工程难点

134 项任务描述的能力标签统计如下；分类非互斥，共 176 个标签，不是 176 个案例，更不是当前 t1 范围。

| 能力 | 涉及案例数 | 代表 ID，仅用于验收设计 |
|---|---:|---|
| pending 商品修改 | 38 | 3、4、21、41、71、119、125 |
| delivered 退货 | 34 | 2、5、10、19、28、84、108 |
| delivered 换货 | 32 | 0、1、6、18、58、95、105 |
| 整单取消 | 20 | 16、30、38、59、76、113、127 |
| 订单地址 | 20 | 17、22、41、71、103、111 |
| 默认地址 | 11 | 22、33、39、43、87、109、128 |
| 支付方式 | 4 | 40、114、123、129 |
| 人工转接 | 5 | 10、12、26、50、130 |
| 明确不得发生未授权变更 | 12 | 24、25、57、62、65、67、68、115、121、126、132、133 |

能力之间的组合比单独 API 更重要：

1. **查找目标**：客户可能报错订单号、混淆 product/item，或只说“另一单”“贵一点的”。Agent 用本人订单、目录和澄清确定目标，不按数字格式猜 ID 类型。
2. **按偏好选变体**：预算、规格优先级、同分最低价、未要求改变的属性都要保留；无货时按客户接受的替代条件重新选择。
3. **请求动态变化**：确认时又改商品、付款或从换货变退货；当前完整方案才是执行依据。
4. **合法操作顺序**：同单改地址再改商品可以有顺序依赖；商品修改后状态锁定，不能机械按用户叙述顺序执行。多个写入并非原子事务。
5. **重复变体**：ID 105 要把两个相同原变体换成两个不同目标；不能去重。ID 133 只想取消其中一件时不能整单取消来“近似完成”。
6. **有条件的授权**：如只接受取消退款到礼品卡，而该取消必须原路退款，就应保持零写入；不能忽略条件。
7. **回答也属于结果**：价格、余额、物流、退款时间等用户问题必须回答，不能只看数据库状态正确。

来源：[公开目录](E:/Agent-For-Retail-Customer-Service-Materials/test-cases-3d81f316-16c1-452d-939f-48776870bd23-zh.md)、[相同原变体案例 105](E:/Agent-For-Retail-Customer-Service-Materials/test-cases-3d81f316-16c1-452d-939f-48776870bd23-zh.md:1602)、[未授权部分取消案例 133](E:/Agent-For-Retail-Customer-Service-Materials/test-cases-3d81f316-16c1-452d-939f-48776870bd23-zh.md:2017)。

## 6. 已明确的实现方向与待核实运行事实

业务规则和正常接口已有足够证据，无需自行补造制度。设计应采用：**LLM 对话编排 + 有类型的领域工具 + 明确的变更准备/确认边界 + 精确 REST 适配 + 可重放的会话状态**。

以下仍需在实现阶段核实，不能因材料足够就假装已运行验证：

- 后续安装已完成：本地真实 tau2 的消息构造/序列化、工具发现、schema、context、资源和网关参数共七项检查通过。具体接口及固定源码提交见 [本地开发环境](LOCAL-DEVELOPMENT.md)。这消除了“本机未安装框架”的待核实点；课堂部署版本和真正 Agent 多轮流程仍须在实现后验证。
- 本地源码已确认 `resources.path/read_text`、`gateway.models` 中的配置及 `validate_request`；按实际签名实现，不再猜 helper。正常消息与网关调用仍以本项目契约为准；不能把上游完整 benchmark 的额外运行能力推定为课堂可用接口。
- 实际 allowed model、参数选择、费用和在线任务范围以未来运行时和用户授权为准。快照不是永久运行配置。
- 同变体不同实例的精确定位、异常写回执是否完成等，是公开接口能力边界；保留未知并查证，不编造补偿 API。

业务实现与新远程评测尚未开展；后续环境安装已运行七项框架兼容检查及既有六项本地测试，均通过，见 [LOCAL-DEVELOPMENT.md](LOCAL-DEVELOPMENT.md)。未读取权限凭证，未修改 Agent、绑定、工作流或安装脚本。接下来实施顺序和每阶段验收见 [IMPLEMENTATION-PLAN.md](IMPLEMENTATION-PLAN.md)。
