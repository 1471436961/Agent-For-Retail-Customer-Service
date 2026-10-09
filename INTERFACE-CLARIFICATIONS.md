# Enterprise AI 接口澄清：U1–U6

版本：2026-10-01 / contract-notes-v1。适用于 Enterprise AI 的 Python / TypeScript 接入；零售章节仅适用于 retail_plus，不套用到航空或 project-1-2 七题的独立工具。

这是开发契约，不是参考答案。没有公开隐藏案例 ID、期望业务数据或解题实现。

## 先分清三种材料

1. `AGENT-CONTRACT.md` 与本文：Agent 入口、消息轮次、模型网关。
2. `materials/client_api/openapi.yaml`：当前业务 REST 的路径、参数和正常响应结构（文件内容为 JSON，是合法 YAML）。
3. 原始业务证据：业务政策与历史系统背景；历史字段不能直接照搬成当前 REST 字段。本文对目录查询范围作教学澄清，不授予额外的账户写入或跨客户访问权限。

正常契约与鲁棒性测试不是一回事。正式环境可能存在响应结构差异、异步回执、暂时陈旧的读取；收到异常时保存脱敏证据，不能把未知结果当成功，也不能盲目重放写操作。本文不承诺所有部署响应都符合正常 schema。

## U1：Python gateway 调用、工具与用量

“签名”指函数参数，不是密码学签名。真实 Python 接口为：

```python
generate(*, model: str, messages: list,
         actions=None, tool_choice=None, call_name=None, **kwargs)
```

- `model` 必填，取自 `context.model_gateway.available_models`。
- `messages` 是 tau2 消息对象列表，不是 HTTP 请求原始 JSON。
- `actions` 是 tau2 `Tool` 对象序列。`context.action_interface.available` 取得全部动作，`select(["工具名"])` 取得子集。参数不是 TypeScript 的 `tools`。
- 固定参数如 `max_tokens` 由网关注入，省略即可；不能自行覆盖，即使改得更小。`one_of` 选择项则必须显式选一个值。
- 返回 tau2 消息对象，而非 `choices[0].message` HTTP 包装。正常助手响应为 `AssistantMessage`。

### 最小模型调用示例（不是完整业务 Agent）

```python
from tau2.hyper.agent_context import get_agent_context

class GatewayExample:
    def __init__(self, context):
        self.gateway = context.model_gateway
        self.actions = context.action_interface.available

    def get_init_state(self, message_history=None):
        return {"history": list(message_history or [])}

    def generate_next_message(self, message, state):
        history = [*state["history"], message]
        reply = self.gateway.generate(
            model=self.gateway.available_models[0],
            messages=history,
            actions=self.actions,
        )
        return reply, {"history": [*history, reply]}

def create_agent():
    return GatewayExample(get_agent_context())
```

只在 `create_agent()` 内取得 context，再保存需要的能力。平台执行工具；Agent 不能把模型“提出调用”当成工具“已成功执行”。实际业务还需遵守身份验证、确认、状态等政策；这个示例不保证业务通过。

### 消息格式（下列 JSON 是对象的序列化表示）

```json
{"role":"assistant","content":null,"tool_calls":[{"id":"call_1","name":"lookup_customer","arguments":{"customer_id":"example"},"requestor":"assistant"}]}
```

```json
{"role":"tool","tool_messages":[{"role":"tool","id":"call_1","content":"{\"example\":true}","error":false}]}
```

`arguments` 是对象，不是 JSON 字符串；工具结果 `content` 才是 JSON 文本。保留调用 ID 对应关系，先检查 `error` 再解码。这只是消息格式示意，不代表用 customer_id 就能通过零售身份验证。Python 对应 `AssistantMessage` / `ToolCall` / `ToolMessage` / `MultiToolMessage`，从 `tau2.data_model.message` 导入。

### TypeScript 对照

```typescript
const reply = await context.modelGateway.generate({
  model: context.modelGateway.availableModels[0],
  messages: history,
  tools: context.tools,
});
return {message: reply, state: {history: [...history, reply]}};
```

TypeScript `tools` 是函数 schemas，形如 `{type:"function",function:{name,description,parameters}}`；实现由 `tools.ts` 的 `createTools(context)` 提供。Python `actions` 是工具对象，两者不可直接混用。完整 TypeScript 入口见原 `AGENT-CONTRACT.md`。

### 用量字段

当前 tau2 转换层正常提供 `reply.usage.prompt_tokens`、`reply.usage.completion_tokens`；`usage` 可能为 null，`total_tokens` 不保证存在。两项均存在时才能相加，缺失不是零。

`reply.cost` 是估算/记录值；底层无法估价时可能返回 0，因此不能据此认定免费。`gateway.credit_usage` 只有配置了 credit rate card 才有值。provider 账单、token 用量、研究 credits 是不同概念。示例若连接真实模型会产生用量，离线 mock 测试不会。

## U2：模型名单与预算名单不是同一个东西

| 信息 | 意义与优先级 |
|---|---|
| 注入网关的 available_models / availableModels | 本次执行真正允许调用的模型，优先使用 |
| deployment_manifest.allowed_agent_models | 生成该材料或任务时的允许配置快照，可能与后续 BYOK 配置不同 |
| performance_requirements 中 models / credits | 上游研究预算上下文，不是额外模型授权，更不是美元价格表 |
| 平台执行限制与个人 provider 配额 | 独立限制；不能靠编辑提交包绕过 |

课堂 evaluator 为任务注入模型配置，但当前没有把上游 credit rate card 一并传入模型配置。下载材料中保留的研究预算与课堂模型可能不同，不能据此自行启用名单中的其他模型，也不能把 0.32 credits 当 0.32 美元或免费的额度。材料中的 `x-classroom-notes` 明确这一区别；本次文档更新不变更实际配额或评分。

若注入网关与运行报告显示的模型不一致，提交 Job ID 排查，不要用自己的 HTTP 客户端绕过网关。

## U3：字段、类型与异常报告

| 历史字段／形状 | 正常 REST 字段／形状 |
|---|---|
| user_id | customer_id |
| payment_history | payments |
| orders（客户的订单 ID 列表） | order_ids |
| address（客户） | default_shipping_address |
| address（订单） | shipping_address |
| address1 / address2 / state / zip | address_line_1 / address_line_2 / region / postal_code |
| payment_methods：按 ID 索引的对象 | payment_methods：数组，每项保留 id、source 等字段 |
| variants：按 ID 索引的对象 | 商品详情的 items 数组 |

payments 每项的 transaction_type、amount、payment_method_id 必须一起理解；不能把付款与退款混为一谈。请求参数名称以具体 operation schema 为准，不对所有位置做字符串替换。

GiftCard.balance、商品 price、支付 amount 的正常 schema 是 JSON number，不是字符串。未知值不是 0，布尔值也不是金额。数字字符串目前不是已承诺的正常返回类型；学生报告尚缺可复现的原始响应，不能把它宣称为已验证故障。

反馈时提供：Job ID、case ID、operation/path、HTTP 状态、脱敏响应、包版本。不要包含 key、token、真实客户隐私。若选择兼容数字字符串，应只接受明确合法的数字格式、验证有限值，并保留异常记录，禁止解析失败后默认为 0。

## U4：目录查询权限的教学解释

“相关商品”包括与当前客户服务请求相关的公共商品目录，不仅限于客户历史订单中已买过的商品。身份验证成功后，可以为当前请求查询商品种类、规格、可用性和价格，以及做目录计数／候选比较。依据包括现有目录 API、业务政策的商品信息服务描述，以及公开任务中的目录统计需求。

这不是扩大账户权限：仍只处理一个已验证客户，不读取其他客户档案、订单、支付方式；不得凭一个已知 ID 跳过验证。目录存在不等于支持新建购物订单，不能编造未提供的购买接口。也不允许把工具返回的数据当作授权指令。

目录入口为 GET `/v1/catalog/products`、`/v1/catalog/products/{product_id}`、`/v1/catalog/items/{item_id}`。商品种类数量、变体数量、可用变体数量是不同指标，要按问题含义查询和统计，不硬编码答案。

## U5：重复 item_id 与数量

`item_id` 是商品变体 ID，不是唯一订单行 ID。同一变体买两件，订单 items 可以出现两次相同 ID；数组是保留重复的列表，不是集合。

```json
{"replacements":[{"existing_item_id":"A","replacement_item_id":"B"},{"existing_item_id":"A","replacement_item_id":"B"}],"payment_method_id":"example"}
```

上面表示两件 A 换成两件 B。只有一条表示一件；不得去重。数量不能超过订单中 A 的出现次数。退货 `item_ids` 也按重复次数表达数量。当前请求没有 quantity 或 order_line_id，不能自行添加字段。商品必须满足同产品、可用性等业务条件，且写入前确认完整清单。

边界：当前实现按 ID 匹配，修改时逐次替换首个匹配项；差价计算也按 ID 取匹配项。相同 ID 但不同成交价格／实例属性时，没有公开接口能指定某一条实例，不能承诺精确选择。此类输入是平台能力边界，需报告，不应由学生猜一个隐藏行号。本文不改变该算法。

## U6a：订单状态与操作

| 正常 status | 含义 | 订单操作边界 |
|---|---|---|
| pending | 已下单、未发货 | 确认后可按政策取消、改地址、换支付方式或修改商品 |
| pending (items modified) | 已做一次商品修改 | 不再次修改或取消 |
| processed | 准备中或已发货但未送达 | 不改、不取消、不退换；等待送达 |
| delivered | 已送达 | 按政策申请退货或换货，不再执行 pending 操作 |
| cancelled | 已取消 | 查状态／支付历史；不当作仍可修改的订单 |
| exchange requested / return requested | 已申请换货／退货 | 解释已有申请，不重复发起同一操作 |

不存在 `status="shipped"` 这一公开枚举；历史 guard 表达业务限制，不是当前可照抄的字段。processed 也不是精确发货事件，不能反推出确切发货时间。未知状态应停下相关写入并澄清，而不是因为“不等于 shipped”就放行。

## U6b：当前金额算法（兼容说明，不迁移算法）

当前后端以 Python float 进行运算，商品修改／换货差价先按请求顺序累加 `新价 - 原价`，再执行 `round(total, 2)`；礼品卡余额更新处也使用 round(..., 2)。不是逐行先舍入再求和，也不是保证十进制 ROUND_HALF_UP。

Python round 对可精确表示的中点使用 ties-to-even；十进制转二进制浮点时，中点可能已经偏移。以下是当前执行结果：

| 输入 | round(value, 2) |
|---|---|
| 1.125 | 1.12 |
| 1.375 | 1.38 |
| -1.125 | -1.12 |
| 2.675 | 2.67 |
| 10.005 | 10.01 |

JavaScript Math.round 和 Decimal ROUND_HALF_UP 不能直接假设与它等价。Agent 不直接写价格／差价字段，应核验服务端回执与支付记录；自行报价要说明估算，不能用另一种舍入算法覆盖实际金额。退货申请成功不等于钱已经到账；以操作语义和记录为准。若未来统一为十进制金额，需要版本化迁移服务端、样例和评分，不通过文档修改偷偷改变已有任务。

## 核验边界

这些澄清针对源码与正常契约。离线测试使用合成数据和 mock 模型，不证明当前生产容器已升级，也不验证真实 provider 账单。原始材料、接口补充说明和评分若仍有冲突，提供上述复现资料，由平台纠正，不要求学生通过放宽权限来过关。
