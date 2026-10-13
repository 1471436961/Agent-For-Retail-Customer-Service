# tau2 零售 Agent 全面复核与修复

日期：2026-10-12。基线：`803d1c72649d91093db770a6423f5f6694db98d8`，`main`。
本文记录本地修改；不表示已经提交或获得新的平台分数。

## 结论与之前验证的问题

之前的实现和验证确实有遗漏，不能用“本地能回答 t1”证明整个零售 Agent 正确。
最新四段失败对话都已完成独立身份核验，随后在 `getOrder` 响应适配中失败。
这是业务读取阶段，和早期首次模型调用的 429 是不同问题。把错误包装或改写提示语不能解决适配错误。

本轮以原始材料及当前 REST 契约为执行依据，使用官方 tau2 Agent、政策和本地真实运行时作交叉检查。
检查对象覆盖入口、模型网关、消息与历史、身份、全部 14 个 REST operation、七类变化、金额、回执、计划及人工转接。
没有为具体客户话术写正则，没有把公开测试输入/答案加入提交包。

旧测试存在两个严重不足：部分模拟模型输出被预先包装成自定义回复格式；七类写入成功测试主要检验回执，没有逐类独立比较原生工具产生的完整数据库状态。
本轮更换原生文本测试，并补充完整 Agent/宿主流程及数据库哈希对照。

## 已发现并修正的问题

| 问题 | 原实现的影响 | 当前修正及验证 |
|---|---|---|
| 普通 JSON 回复被当作私有协议 | 以 `{` 开头的正常文本被强制解析成 ReplyIntent；异常产生额外模型纠正甚至停止 | 完全移除 `kind/text/evidence_call_ids/customer_fields` 回复协议。自然语言、Markdown、JSON 及以大括号开头的解释均原样返回；真伪由模型依据真实工具结果判断 |
| 实时多工具读批次 | 允许模型同时读取多张订单，与上游零售每次最多一个工具的政策不符 | 提示词与实时消息校验均限制一个调用，先等真实结果再继续；旧历史批次仍能恢复、展开并保留每个 ID 与错误标记。平台支持多结果的传输协议不等于零售政策允许实时并行 |
| 自设每请求 16 个工具轮次 | 多订单串行读取及准备流程可能提前触发本地停止 | 默认与 128 次会话模型调用上限对齐，不再设置较小的 16 轮门槛；宿主显式注入的上限仍生效。18 次连续读取及最终正常回复通过 |
| 返回值标量兼容不完整 | 只兼容礼品卡余额，订单价格、支付金额、商品 availability 和换货差价仍可能在严格类型校验失败 | 对已知 schema 叶子按有限 JSON 数字/明确布尔编码兼容，保存原值警告，随后执行原严格模型校验。`False` 不会被当作真；ID、规格、地址、枚举、未知字段不做自动转换 |
| 读取错误缺少定位 | 所有 schema 问题只有 invalid_read_response，无法知道是字段缺失、类型异常还是返回 ID 错误 | 返回有限、脱敏的字段路径和校验类型；不包含输入值、任意异常正文或未知字段名。订单目标 ID 不一致单独标记；保留 `#` 并由工具 URL 编码 |
| 多商品退货回执顺序 | 原生工具排序保存 item_ids，原严格数组相等会把合法受理误报 unknown | 核验相同退款目的地及完整 ID 多重集，重复次数不能丢失；仅回执排序不同可接受并记警告，提交请求保持原确认顺序 |
| 多商品换货回执的原生存储差异 | 原生工具分别排序旧、新 ID；REST adapter 再 zip，可能改变配对展示，原严格比较会误报 unknown | 同时核验目标、状态、结算方法、精确差价；接受原配对的重排，或精确符合公开原生实现的独立排序编码。其他配对变化拒绝。保留 pairing_lost 警告与提交时 requested_replacements，不把该回执配对当作实际履约证明 |
| 测试覆盖了私有格式而非正常 Agent 对话 | 固定 JSON fixtures 给出虚假的格式正确感 | 协议、wire、隔离中介和业务循环均改用原生 AssistantMessage 文本；不再测试或声称程序能够通过自然语言格式验证模型事实真实性 |

数字字符串解释的证据边界：实际 `Environment.to_json_str` 会把数字和布尔值转成字符串，本地对真实运行时订单应用该序列化后可以复现旧严格模型的失败。
四段远程对话没有原始 `getOrder` 响应，因此这证明一个真实的兼容缺口，而不证明远程这四次一定全部是价格字符串造成。
现在失败会留下具体字段路径，可继续精确诊断，而不是猜测字段并随意放宽模型。

回执排序问题则已直接在未修改的本地 `RetailPlusTools` 与 `ClientAPIRuntime` 上复现；不需要注入标量故障。
跨两个商品类型的换货测试特意让旧、新 ID 排序方向不同，证明 pair-loss 分支确实被执行。

## 对照检查结果

| 领域 | tau2 标准/项目要求 | 当前实现与证据 |
|---|---|---|
| Agent 入口 | 工厂取得可信上下文；后续返回消息及可序列化状态 | create_agent 内取得并保存 context；跨实例恢复与会话隔离测试覆盖 |
| 模型调用 | 使用注入 gateway 与允许配置，固定参数不得覆盖 | available_models、validate_request、one_of 显式选择；使用 Python actions 和消息对象，无候选 HTTP 客户端或凭证。当前 t1 的远程基线已有实际成功调用 |
| 对话历史 | 用户消息保留；工具结果与请求 ID 关联 | 历史不解析用户话术；MultiToolMessage 展开为独立 ToolMessage，真实 LiteLLM wire 转换核对 tool_call_id；错误不作为成功证据 |
| 文本/工具半双工 | 工具或文本二选一、零售一次一个调用 | 实时调用去除伴随旁白并校验一个工具；工具执行由宿主完成；没有将调用意图伪造为成功结果 |
| 身份 | 邮箱或完整姓名邮编独立查询，即便已给 ID | searchCustomers → 比较 claimed_customer_id → getCustomer；匹配失败不加载详情；缺少资料由模型询问，未对用户话术做匹配 |
| 身份读取异常 | 成功搜索不等于详情加载成功 | 身份保留但不虚构邮箱；get_customer_summary 恢复；失败返回不解锁新客户 |
| 本人范围 | 一次会话一个客户、可处理多项请求 | toolkit 锁定身份；读取本人订单清单后 getOrder 并核对 customer_id；会话上下文改变时清除本地授权 |
| 不透明 ID | 不能靠拆解 ID 推断关系 | 原 ID 原样保留，含 `#`；URL quote；商品关系由订单/product records 判断 |
| 公共目录 | 种类、变体、可用数不同；规格和价格来自工具 | U4 允许验证后查请求相关的公共商品，get_catalog_summary 实际读取计数；没有硬编码上游 50 或公开案例数据 |
| 偏好理解 | LLM 从整个对话理解，保留未修改规格 | 比较工具只处理结构化 catalog 选项；规格单位 codec 不读取用户语言；不猜尺寸顺序或缺失偏好 |
| 变更确认 | 先列明具体动作和资料，再获明确同意 | prepare → review → 真正展示完整固定事实 → 后续用户轮次 → 模型决定是否执行。用户轮次只是必要条件，是否同意仍是模型语义判断；没有 yes 正则 |
| 改口与撤回 | 旧确认不能用于新方案 | 同目标新方案撤销旧确认；invalidate/withdrawn/stale 阻止旧执行；后台关键事实变化重新确认 |
| 状态边界 | pending 可改/取消；delivered 可退换；一次商品修改后锁定 | U6 规范状态枚举，processed 不推断已送达；不存在臆造 shipped 字段；ineligible_order_status 零写入 |
| 取消 | 整单、两种允许理由、原 charge 分别退款 | 模型语义映射理由；工具严格业务枚举；不把价格投诉改标为允许理由；核验完整资金流水和退款时限来源 |
| 地址/支付 | 默认地址与订单地址独立；单个已保存支付方式 | 两类地址不同 endpoint，完整字段；换支付新方法全额扣款、旧款退回；余额校验，不创建新支付方式 |
| 商品修改 | 同产品可用新变体，完整一次清单 | 整个 replacements 一次提交，重复 ID 和顺序保留；正差额资金充足；修改后状态锁定 |
| 退货 | delivered、确认所有 item_ids、原支付或原有 gift card | 数量多重集与资格校验；真实受理后解释申请和后续邮件，不声称到账 |
| 换货 | delivered、同产品可用变体、单一结算方法、一次完整清单 | 保留替换对应关系及次数；引用实际差价；受理不当作退款/发货完成；处理上游回执排序并保存异常证据 |
| 金额 | 当前后端依顺序 float 累加后 round(total,2) | U6 相同算法，不用 Decimal HALF_UP 替代；bool/NaN/无限值拒绝；同时核验实际金融回执 |
| 多请求 | 依赖顺序、分步确认、部分成功 | 同单地址/支付先于一次商品修改；逐步确认/执行，未宣称原子事务；失败/未知不重复已完成项 |
| 不确定写入 | 未知不是失败后可重放 | write 前 attempted；unknown 只做授权读取观察，不能重新准备同目标写入；缓存结果杜绝第二次远程执行 |
| 人工转接 | 当前获批准 pilot 触发及固定 notice | 使用真实 conversation_id 和真实转接 endpoint；accepted/unknown 后终止自动客户 API；不伪造转接，不由愤怒关键词触发 |
| 提交范围 | agent 包大小/导入/凭证约束，绑定不变 | 本轮不改 binding、task、case_ids、工作流或业务服务器；安装脚本、资料包、凭证不进入 agent |

## 为什么不能直接把 upstream retail 的常量搬过来

本项目是 `retail_plus` REST Agent。上游政策与当前课程的批准材料共同说明业务模式，但存在明确版本差异：

* 上游取消/换支付的 card 退款时间是 5–7 工作日，当前材料批准的是 3–6；保持项目值。
* 原生工具字段是 user_id/address/payment_history，REST 字段是 customer_id/shipping_address/payments；候选必须调用公开 REST。
* 当前 U6 使用 `pending (items modified)`；不能把上游文字中的拼写差异或历史 shipped 守卫复制成另一个枚举。
* 当前 pilot 的转人工触发与一般 upstream 的 unsupported 自动转接表述不同，按原材料获批准裁决执行。
* 当前 one_of/固定模型选项、无网络沙箱和网关中介是宿主约束，不能把直接调用自带 key 的标准 LLMAgent 复制进提交包。

因此检查目标是保持标准 LLM Agent 的消息/工具循环和业务状态语义，同时准确实现本项目批准的策略与接口。
直接运行 vanilla retail CLI 并不能验证这个 REST 候选包；本地 SDK 也没有安装默认 retail/retail_plus 数据目录。
本轮没有读取上游任务答案或 ground-truth Agent，没有改正式评测任务/范围来获得分数。

## 实际验证方式和边界

1. 六个读取及全部 14 个 operation 的响应模型与项目 OpenAPI 展开 schema 对照。
2. 七类操作分别走真实 `ClientAPIRuntime`/`RetailPlusTools`，每次仅一次业务写入，重复执行返回缓存。
3. 七类操作还走 create_agent → 模型中介 → 宿主 get_response → prepare/review → 实际用户确认 → execute → 最终原生文本循环。确认前数据库哈希不变。
4. 每类操作在另一个全新数据库直接执行 tau2 原生工具，比较完整 get_db_hash，不只看目标订单或 accepted。
5. 另行测试多商品退货、跨两产品的换货排序、重复商品、序列化标量、坏类型、未知字段与未知写入零重试。
6. 模型输出在这些回归中受控，实际工具、消息模型、网关转换与数据库后端运行。模型理解、偏好抽取和条件同意需要真实模型/模拟客户评测，不能由这些断言替代。

官方评分文档明确区分最终数据库状态、沟通和可选的动作轨迹检查；因此本轮补充最终状态比较，而不是把“工具序列与一份参考脚本一致”当作业务正确。
完整平台 t2 仍是多轮语义验收，当前没有新的 t2 通过结果；本轮没有发起付费本地模型调用或新远程评测。

最终回归与包审计结果见本轮会话输出和 `.git/tau2-audit-tests.log` / `.git/tau2-package-audit.json`；这些文件不进入提交包。

全量回归：**197 个测试通过**，失败、错误、跳过均为 0，耗时 22.587 秒。
提交包审计：**27 个文件、127,227 字节，错误 0**；`git diff --check` 通过。
随后为回执排序的兼容分支补充了错误数量、错误结算方法和错误差价的拒绝子断言，单独重跑真实运行时组验证；不将子断言重复计为独立测试。

## 参考

* [官方 retail policy](https://github.com/sierra-research/tau2-bench/blob/main/data/tau2/domains/retail/policy.md)：身份、半双工、一次调用、一次清单等。
* [官方 LLMAgent](https://github.com/sierra-research/tau2-bench/blob/main/src/tau2/agent/llm_agent.py)：完整消息循环、MultiToolMessage 展开、原生模型输出。
* [官方 evaluation](https://github.com/sierra-research/tau2-bench/blob/main/docs/evaluation.md)：最终状态与沟通评分、参考 actions 的含义。
* 仓库 AGENT-CONTRACT.md、INTERFACE-CLARIFICATIONS.md U1–U6、materials/client_api/openapi.yaml、framework/client_api_contract.md。
* 原始材料 `E:\Agent-For-Retail-Customer-Service-Materials\materials\uploaded_materials`；出处索引见 POLICY-REGISTER.md（特别是身份、语义确认、一次清单、退款时限和转人工裁决）。
* 已安装 SDK：tau2/domains/retail/tools.py、retail_plus/tools.py、hyper/client_api/catalogs/retail.py、environment/environment.py。

本文替代旧回复格式测试和旧“单笔回执足够证明七类业务正确”的判断；历史验证文档保留当时事实，不用于证明本轮或完整 benchmark 已通过。
