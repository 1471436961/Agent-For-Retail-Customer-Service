# P0：API 与运行契约基线

本表从当前 OpenAPI 提取；不是新增接口。业务资格另见 [政策登记](POLICY-REGISTER.md)，类型合法不等于业务允许。

## 固定接入范围

| 项目 | 基线 |
|---|---|
| repository | `1471436961/agent-for-retail-customer-service` |
| branch | `main` |
| binding | `3d81f316-16c1-452d-939f-48776870bd23` |
| domain / language | `retail_plus` / `python` |
| task / case_ids | `t1` / `null` |

原始资料位于仓库外；本地虚拟环境不上传。P0 不修改上述配置。

## OpenAPI 快照

- 来源：[openapi.yaml](../materials/client_api/openapi.yaml)。版本 `3.1.0`，OpenAPI `3.1.0`。
- 文件 SHA-256：`f2268b325c442206b1234c4a16dbd5a7f4ff81a925be56d9fb0be59cf8a56594`。
- 14 个 operation：6 个读取、7 个业务写入、1 个转接。分类由 x-api-mutates-state 和实际用途决定，不能只看 HTTP 方法。
- 全部操作：strong 正常一致性；无分页；query/body 合计 1,048,576 字节，响应正文 4,194,304 字节。
- 读取：safe / automatic-retries allowed；写入和转接：not_guaranteed / automatic-retries forbidden。

| operationId | 方法与路径 | 类别 | 正常状态 |
|---|---|---|---|
| `searchCustomers` | `POST /v1/customers/search` | 读取 | 200 |
| `getCustomer` | `GET /v1/customers/{customer_id}` | 读取 | 200 |
| `replaceCustomerDefaultShippingAddress` | `PUT /v1/customers/{customer_id}/default-shipping-address` | 业务写入 | 200 |
| `listProducts` | `GET /v1/catalog/products` | 读取 | 200 |
| `getProduct` | `GET /v1/catalog/products/{product_id}` | 读取 | 200 |
| `getCatalogItem` | `GET /v1/catalog/items/{item_id}` | 读取 | 200 |
| `getOrder` | `GET /v1/orders/{order_id}` | 读取 | 200 |
| `replaceOrderShippingAddress` | `PUT /v1/orders/{order_id}/shipping-address` | 业务写入 | 200 |
| `replaceOrderPaymentMethod` | `PUT /v1/orders/{order_id}/payment-method` | 业务写入 | 200 |
| `createOrderCancellation` | `POST /v1/orders/{order_id}/cancellations` | 业务写入 | 200 |
| `createOrderItemModification` | `POST /v1/orders/{order_id}/item-modifications` | 业务写入 | 200 |
| `createOrderReturn` | `POST /v1/orders/{order_id}/returns` | 业务写入 | 200 |
| `createOrderExchange` | `POST /v1/orders/{order_id}/exchanges` | 业务写入 | 200 |
| `createConversationTransfer` | `POST /v1/conversations/{conversation_id}/transfers` | 转接 | 201 |

## 每个 operation 的结构

字段表中的“必需”来自 schema.required；nullable、默认值不自动授予业务权限。路径参数逐个编码，尤其 # 编码为 %23。GET 不增加文档未提供的 body、分页或 filter。

### `searchCustomers`

`POST /v1/customers/search`

请求正文：

| 字段 | 类型 | 必需 |
|---|---|---|
| `email` | `string \| null` | 否 |
| `first_name` | `string \| null` | 否 |
| `last_name` | `string \| null` | 否 |
| `postal_code` | `string \| null` | 否 |

Provide email alone or a complete name-and-postal-code combination.

正常响应：

HTTP 200，`CustomerLookupResult`：

| 字段 | 类型 | 必需 |
|---|---|---|
| `customer_id` | `string` | 是 |

错误响应：400 / 404 / 405 / 409 / 413 / 422 / 502，共享 APIError。

### `getCustomer`

`GET /v1/customers/{customer_id}`

无请求正文。

正常响应：

HTTP 200，`Customer`：

| 字段 | 类型 | 必需 |
|---|---|---|
| `customer_id` | `string` | 是 |
| `name` | `CustomerName` | 是 |
| `default_shipping_address` | `Address` | 是 |
| `email` | `string` | 是 |
| `payment_methods` | `list[CreditCard \| GiftCard \| Paypal]` | 是 |
| `order_ids` | `list[string]` | 是 |

错误响应：400 / 404 / 405 / 409 / 413 / 422 / 502，共享 APIError。

### `replaceCustomerDefaultShippingAddress`

`PUT /v1/customers/{customer_id}/default-shipping-address`

请求正文：

| 字段 | 类型 | 必需 |
|---|---|---|
| `address_line_1` | `string` | 是 |
| `address_line_2` | `string \| null` | 否 |
| `city` | `string` | 是 |
| `region` | `string` | 是 |
| `country` | `string` | 是 |
| `postal_code` | `string` | 是 |

正常响应：

HTTP 200，`CustomerDefaultShippingAddress`：

| 字段 | 类型 | 必需 |
|---|---|---|
| `customer_id` | `string` | 是 |
| `default_shipping_address` | `Address` | 是 |

错误响应：400 / 404 / 405 / 409 / 413 / 422 / 502，共享 APIError。

### `listProducts`

`GET /v1/catalog/products`

无请求正文。

正常响应：

HTTP 200，`ProductCollection`：

| 字段 | 类型 | 必需 |
|---|---|---|
| `products` | `list[ProductSummary]` | 是 |

错误响应：400 / 404 / 405 / 409 / 413 / 422 / 502，共享 APIError。

### `getProduct`

`GET /v1/catalog/products/{product_id}`

无请求正文。

正常响应：

HTTP 200，`Product`：

| 字段 | 类型 | 必需 |
|---|---|---|
| `name` | `string` | 是 |
| `product_id` | `string` | 是 |
| `items` | `list[CatalogItem]` | 是 |

错误响应：400 / 404 / 405 / 409 / 413 / 422 / 502，共享 APIError。

### `getCatalogItem`

`GET /v1/catalog/items/{item_id}`

无请求正文。

正常响应：

HTTP 200，`CatalogItem`：

| 字段 | 类型 | 必需 |
|---|---|---|
| `item_id` | `string` | 是 |
| `options` | `map[str,string]` | 是 |
| `available` | `boolean` | 是 |
| `price` | `number` | 是 |

错误响应：400 / 404 / 405 / 409 / 413 / 422 / 502，共享 APIError。

### `getOrder`

`GET /v1/orders/{order_id}`

无请求正文。

正常响应：

HTTP 200，`Order`：

| 字段 | 类型 | 必需 |
|---|---|---|
| `order_id` | `string` | 是 |
| `customer_id` | `string` | 是 |
| `shipping_address` | `Address` | 是 |
| `items` | `list[OrderItem]` | 是 |
| `status` | `processed / pending / pending (items modified) / delivered / cancelled / exchange requested / return requested` | 是 |
| `fulfillments` | `list[OrderFulfillment]` | 是 |
| `payments` | `list[OrderPayment]` | 是 |
| `cancellation` | `OrderCancellation \| null` | 否 |
| `exchange` | `OrderExchange \| null` | 否 |
| `return_request` | `OrderReturn \| null` | 否 |

错误响应：400 / 404 / 405 / 409 / 413 / 422 / 502，共享 APIError。

### `replaceOrderShippingAddress`

`PUT /v1/orders/{order_id}/shipping-address`

请求正文：

| 字段 | 类型 | 必需 |
|---|---|---|
| `address_line_1` | `string` | 是 |
| `address_line_2` | `string \| null` | 否 |
| `city` | `string` | 是 |
| `region` | `string` | 是 |
| `country` | `string` | 是 |
| `postal_code` | `string` | 是 |

正常响应：

HTTP 200，`OrderShippingAddressResult`：

| 字段 | 类型 | 必需 |
|---|---|---|
| `order_id` | `string` | 是 |
| `shipping_address` | `Address` | 是 |

错误响应：400 / 404 / 405 / 409 / 413 / 422 / 502，共享 APIError。

### `replaceOrderPaymentMethod`

`PUT /v1/orders/{order_id}/payment-method`

请求正文：

| 字段 | 类型 | 必需 |
|---|---|---|
| `payment_method_id` | `string` | 是 |

正常响应：

HTTP 200，`OrderPaymentMethodResult`：

| 字段 | 类型 | 必需 |
|---|---|---|
| `order_id` | `string` | 是 |
| `payments` | `list[OrderPayment]` | 是 |

错误响应：400 / 404 / 405 / 409 / 413 / 422 / 502，共享 APIError。

### `createOrderCancellation`

`POST /v1/orders/{order_id}/cancellations`

请求正文：

| 字段 | 类型 | 必需 |
|---|---|---|
| `reason` | `string` | 是 |

正常响应：

HTTP 200，`OrderCancellationResult`：

| 字段 | 类型 | 必需 |
|---|---|---|
| `order_id` | `string` | 是 |
| `status` | `'cancelled'` | 是 |
| `cancellation` | `OrderCancellation` | 是 |
| `payments` | `list[OrderPayment]` | 是 |

错误响应：400 / 404 / 405 / 409 / 413 / 422 / 502，共享 APIError。

### `createOrderItemModification`

`POST /v1/orders/{order_id}/item-modifications`

请求正文：

| 字段 | 类型 | 必需 |
|---|---|---|
| `replacements` | `list[ItemReplacement]` | 是 |
| `payment_method_id` | `string` | 是 |

正常响应：

HTTP 200，`OrderItemModificationResult`：

| 字段 | 类型 | 必需 |
|---|---|---|
| `order_id` | `string` | 是 |
| `status` | `'pending (items modified)'` | 是 |
| `items` | `list[OrderItem]` | 是 |
| `payments` | `list[OrderPayment]` | 是 |

错误响应：400 / 404 / 405 / 409 / 413 / 422 / 502，共享 APIError。

### `createOrderReturn`

`POST /v1/orders/{order_id}/returns`

请求正文：

| 字段 | 类型 | 必需 |
|---|---|---|
| `item_ids` | `list[string]` | 是 |
| `refund_payment_method_id` | `string` | 是 |

正常响应：

HTTP 200，`OrderReturnResult`：

| 字段 | 类型 | 必需 |
|---|---|---|
| `order_id` | `string` | 是 |
| `status` | `'return requested'` | 是 |
| `return_request` | `OrderReturn` | 是 |

错误响应：400 / 404 / 405 / 409 / 413 / 422 / 502，共享 APIError。

### `createOrderExchange`

`POST /v1/orders/{order_id}/exchanges`

请求正文：

| 字段 | 类型 | 必需 |
|---|---|---|
| `replacements` | `list[ItemReplacement]` | 是 |
| `payment_method_id` | `string` | 是 |

正常响应：

HTTP 200，`OrderExchangeResult`：

| 字段 | 类型 | 必需 |
|---|---|---|
| `order_id` | `string` | 是 |
| `status` | `'exchange requested'` | 是 |
| `exchange` | `OrderExchange` | 是 |

错误响应：400 / 404 / 405 / 409 / 413 / 422 / 502，共享 APIError。

### `createConversationTransfer`

`POST /v1/conversations/{conversation_id}/transfers`

请求正文：

| 字段 | 类型 | 必需 |
|---|---|---|
| `summary` | `string` | 是 |

Request to transfer an active conversation to a human agent.

正常响应：

HTTP 201，`ConversationTransferReceipt`：

| 字段 | 类型 | 必需 |
|---|---|---|
| `transfer_id` | `string` | 是 |
| `status` | `'accepted'` | 是 |

错误响应：400 / 404 / 405 / 409 / 413 / 422 / 502，共享 APIError。

## 共享数据模型

| schema | 必需字段及类型 |
|---|---|
| `APIError` | `error: object` |
| `Address` | `address_line_1: string`；`city: string`；`region: string`；`country: string`；`postal_code: string` |
| `CreditCard` | `source: 'credit_card'`；`id: string`；`brand: string`；`last_four: string` |
| `CustomerName` | `first_name: string`；`last_name: string` |
| `GiftCard` | `source: 'gift_card'`；`id: string`；`balance: number` |
| `Paypal` | `source: 'paypal'`；`id: string` |
| `ProductSummary` | `product_id: string`；`name: string` |
| `CatalogItem` | `item_id: string`；`options: map[str,string]`；`available: boolean`；`price: number` |
| `ItemReplacement` | `existing_item_id: string`；`replacement_item_id: string` |
| `OrderCancellation` | `reason: no longer needed / ordered by mistake` |
| `OrderExchange` | `replacements: list[ItemReplacement]`；`payment_method_id: string`；`price_difference: number` |
| `OrderFulfillment` | `tracking_id: list[string]`；`item_ids: list[string]` |
| `OrderItem` | `name: string`；`product_id: string`；`item_id: string`；`price: number`；`options: map[str,string]` |
| `OrderPayment` | `transaction_type: payment / refund`；`amount: number`；`payment_method_id: string` |
| `OrderReturn` | `item_ids: list[string]`；`refund_payment_method_id: string` |

注意：

- Address 不允许额外字段；address_line_2 可缺省或 null，其他五个地址字段必需。
- CreditCard/Paypal/GiftCard 的 source 是 const；正常金额是 number，bool 不是金额。
- ItemReplacement 一项是一件；replacements 非空，保留顺序及重复。没有 quantity/order_line_id。
- CatalogItem 没有 product_id；所属商品从 Product.items 或订单 product_id 确定。
- OrderPayment.transaction_type 仅 payment/refund；不能将所有流水不分方向相加。
- OrderFulfillment.tracking_id 与 item_ids 都是数组。

## Schema 无法单独表达的业务条件

- search 请求各字段都可选/nullable，但描述要求 email 单独或完整 first/last/postal 三项；必须做业务 selector 校验。
- cancellation 请求 reason 是非空 string；当前允许的两理由来自政策及 OrderCancellation 枚举，不能把任意合法 string 当可执行理由。
- saved payment、余额、本人订单、同产品/available、一次性清单、状态和确认不由 JSON Schema 单独证明。
- 相同 item_id 但不同实例属性没有公开精确定位字段；不补造选择器。

## 错误与鲁棒性

| HTTP | 处理要求 |
|---|---|
| 400 / 405 | 请求结构或方法错误；修正调用，不当成身份不存在 |
| 404 | 引用资源不存在；身份失败响应不泄露账户存在性 |
| 409 | 当前状态冲突；重查并说明，不绕过状态 |
| 422 | 结构合法但业务限制失败；解释实际限制，不改政策 |
| 413 / 502 response_too_large | 容量限制；不裁剪/拆写/重试写入绕过 |
| 超时、畸形回执、异步/旧读 | 记录为结果未知或仅受理；只读核查，禁止重放写入 |

APIError.error 要有 code/message；details 可选。只使用有文档依据的错误码，不能把文本摘要当私有业务规则。异常响应是待核查的偏离，不修改正常 schema 使它看起来合法。

## Agent 协议与本地 SDK

- 工厂取得 context，实例保留 gateway/actions/resources/runtime_config；后续轮次不重新取工厂 context。
- 输入 UserMessage/MultiToolMessage；返回 AssistantMessage 与 JSON 可恢复 state；text 与 tool_calls 互斥。
- ToolCall.arguments 是对象；ToolMessage.content 是 JSON 文本；按 id 对应、先处理 error。
- Python gateway 使用 model/messages/actions，实际模型来自 available_models；固定参数省略，one_of 显式选择。
- 本地源码与七项接口验证记录：[LOCAL-DEVELOPMENT.md](LOCAL-DEVELOPMENT.md)。本地 SDK 安装不授予额外课堂能力。
- 工具重放按本项目 client_api_contract：行为由后端、参数及此前会话工具调用决定。

来源：[AGENT-CONTRACT](../AGENT-CONTRACT.md)、[U1–U6](../INTERFACE-CLARIFICATIONS.md)、[Client API 契约](../materials/framework/client_api_contract.md)。
