# t2 运行 dc330ede 的证据复核

日期：2026-10-15。运行提交 `2d00f5854a46c00a21c900f4e602587261db7220`。
源码与 `6e001b6` 相同，运行编号 `38671334344`，Job `dc330ede-097d-4e52-bc24-330f2f2e61bd`。

## 输入证据

* GitHub 附件 `.git/enterprise-evaluations/38671334344/report.json`。
* 用户提供的 `D:\download\hyper-tau-dc330ede-097d-4e52-bc24-330f2f2e61bd.json`，96,817 字节。
* 用户提供的前三个案例完整对话及第四个案例截图。
* 已登录 Chrome 的本人评测页面和 Enterprise AI 工作台，读取当前运行的对话、API 事件及案例反馈。

该 JSON 的根结构是 `receipt / selected_case / activity`，不是直接以 report 为根。
`selected_case` 是案例 0，包含实际对话；`receipt.report.cases` 保存四个案例的状态摘要。
`activity.events` 只有 method/path/status_code/changes/time，没有 response.body。
`activity.before/after` 是 candidate-visible-records 的 synthetic 业务快照，不能当作候选 ClientAPI 收到的原始响应。
特别是快照中的 status=delivered，不能据此向实际缺字段的响应补 status。

## 实际状态

| 案例 ID | 记录结果 | 具体证据 |
|---|---|---|
| 0 | failed | 姓名邮编核验成功；两次 getOrder 均 HTTP 200，适配报 status:missing,*:extra_forbidden；没有换货写入 |
| 1 | failed | 相同订单读取错误；模型之后可以继续读取键盘与温控器目录，说明不是完全不能调用模型或业务 API |
| 2 | failed | 有两处错误：把 12 个总变体回答成当前可用数量，其中两项不可用；读取订单再次报相同 status 缺失/额外字段错误 |
| 3 | infrastructure_error | reward=null，仅有 Runtime/provider error 摘要；截图亦无业务快照、对话、异常类、HTTP 状态或堆栈 |

总范围 134；记录 3 个业务失败和 1 个运行时异常，130 个未运行，无正式总分。
不能把 130 个未运行说成失败；也不能用总 verdict=infrastructure_error 抹掉前三个业务失败记录。

## 已证实的订单阻断原因与未获得的数据

阻断发生在我们 `_read()` 中的 `READ_MODELS['getOrder'].model_validate(body)`，即响应适配，不是身份核验或最初模型调用。
当前模型按 OpenAPI 要求必填 status 并禁止未知字段，实际评测响应同时缺少该字段并出现至少一个额外字段。
当前材料和本地 SDK 的正常 schema 都使用 status；这份导出没有提供额外字段名/值，不能确定它是否为状态的别名，或只是另一个无关字段。

需要进一步取得：本次 getOrder 的脱敏顶层字段名、状态字段的实际名称/枚举值，以及对应运行时 API schema/version。
拿到可证实的映射后才能补兼容并对 pending/processed/delivered 等状态做回归。
不能将未知字段按值猜成状态，不能从客户声称“收到订单”补成 delivered，不能去掉状态检查来放行退换货。

此前数字字符串兼容是真实的兼容措施，但不是本次报错的原因；这次诊断已明确指向字段缺失/额外字段。
反复读同一订单不会改变稳定的结构不匹配，继续读取其他目录也无法替代订单的状态、原商品和成交价证据。

## 已完成的本地修正：目录计数

`get_product_variants` 现在除经校验的商品记录外，返回三个派生字段：

* variant_count：全部变体数；
* available_variant_count：available 为真者的数量；
* unavailable_variant_count：其余数量。

这些值实际计算，不硬编码产品名称、产品 ID 或 10/12 等案例答案；没有改变 REST schema。
提示词及工具说明明确：当前可用/可选数量使用 available_variant_count；总变体数仅作为另一个指标说明。
单商品计数使用该商品详情，不为一个产品无谓扫描整个商店。

新增测试使用 7 个合成变体，其中 4 个可用、3 个不可用，同时覆盖原生 bool 及 SDK 序列化的 True/False 字符串。
全量回归：198 个测试通过，20.329 秒；`git diff --check` 通过。
这验证工具计算和协议，不宣称已用真实模型重跑当前对话或通过正式 t2。

## 第四个案例的归因边界

目前没有具体异常可用于把 case 3 归因到 Agent、模拟客户、模型服务商或评分器。
本地 SDK 的基础设施分类会覆盖多种提供方/网络错误，但不能拿本地分类代码推断线上具体异常。
本机后来下载 GitHub 日志遇到 EOF/TLS timeout，也不能当作发生在评测主机中的证据。

下一步查询应同时索取 case 3 的脱敏异常类型、发生阶段与堆栈/调用摘要，以及案例 0 的响应字段。
目前未向课程服务发送新的诊断内容，未修改绑定/评测范围，未提交或启动新评测。

结果链接：https://agentist.org/lab/enterprise-ai?run=dc330ede-097d-4e52-bc24-330f2f2e61bd
