# P2 实施进度

状态：已完成（本地离线验收，2026-10-09）。P1 已提交 `dc4dd74`；当前绑定仍为 main / retail_plus / python / t1 / case_ids:null。

## 实际交付

- 模型根据完整对话组织 email，或完整 first_name + last_name + postal_code。`identity_selector` 只检查结构化 selector 的完整性与互斥性，不识别自然语言、拆名字或套姓名句式。缺失、混合 selector 和只报 ID 都不调用后端。
- `verify_customer` 先 POST search，再核对 optional claimed_customer_id 和当前会话绑定，最后 GET profile。不存在、claimed ID 不匹配及试图换客户返回相同脱敏失败，不暴露搜索得到的客户 ID、原始错误文案或资料。
- 身份绑定在 toolkit 实例内，不从 Agent state 或模型的 `verified=true` 取得。后端独立搜索匹配后立即绑定，在详情请求失败时仍保留绑定，避免切换账户；可以重新读取同一客户。可信 conversation_id 变化时清空绑定，新 toolkit 也没有旧身份。
- 兼容旧的 `lookup_customer` 通过同一验证方法执行，没有旁路。身份调用独立一批，不能与其他验证或依赖其结果的读取同批；工具实例内的锁保护完整验证/读取。
- `get_customer_summary` 和 `list_customer_orders` 不接受 customer_id，客户只来自已绑定身份。`get_owned_order` 先刷新本人 profile 的 order_ids，未列出的 ID 不请求订单；实际回执还要核对客户归属，再返回资料。
- 目录读取需要已验证身份，可查询公共目录中未买过的商品，不拓展其他账户权限。`get_catalog_summary` 读取完整产品清单和每个产品，分别计算商品种类、变体总数和 available=true 的变体数；失败不返回部分数量。计数字段是我们自己的工具输出，不是新增 REST 字段。
- 所有路径标识符作为一个不透明段编码，保留原值，不猜前缀或隐藏行 ID。GET 不发未声明的 body、过滤或分页。API 只调用六个现有只读 operation，包含语义上只读的 POST search。
- 正常数据使用 Pydantic 严格模型；六个响应模型的展开结构已与当前 OpenAPI 全量比较一致。金额还遵守 U3/U6 的有限值约束；拒绝 bool、数字字符串、null、NaN/Infinity、未知状态、额外字段和错误资源 ID。返回原始记录的独立副本，不重写 optional 字段、浮点值、列表顺序或重复项。
- HTTP 状态与已知公开错误码保留用于判断；不转发 backend message/details、响应原文或传输异常字符串。未知错误码也不输出任意后端字符串。本阶段没有自动重试；容量错误不裁剪或拆分。

## 工具与 API 对应

| 工具 | API 来源 | 边界 |
|---|---|---|
| verify_customer / lookup_customer | searchCustomers + getCustomer | 两种 selector、claimed ID、单客户绑定；兼容别名也走相同 guard |
| get_customer_summary | getCustomer | 参数中无客户 ID，只返回本人 |
| list_customer_orders | getCustomer.order_ids | 本人当前完整订单 ID 列表，不推断日期 |
| get_owned_order | getCustomer + getOrder | 先清单、后实际归属；重复项和付款/物流数组原样保留 |
| list_products | listProducts | 完整公共商品种类清单 |
| get_product_variants | getProduct | 全变体、options、available 和当前 price |
| get_catalog_item | getCatalogItem | 不增加 product_id；所属商品从产品详情确定 |
| get_catalog_summary | listProducts + 每个 getProduct | 分类计数及来源记录，不新增比较/写入接口 |

源码见 [customer_tools.py](../agent/support_agent/adapters/customer_tools.py)、[read_api.py](../agent/support_agent/adapters/read_api.py)、[read_models.py](../agent/support_agent/adapters/read_models.py) 和 [identity.py](../agent/support_agent/domain/identity.py)。出处为 [政策 AUTH/FACT](POLICY-REGISTER.md)、[接口基线](API-BASELINE.md) 及 [U1–U6](../INTERFACE-CLARIFICATIONS.md)。

## 实际验证

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe .\.venv\check_tau2_install.py
```

实际通过61/61：35项P2权限/读取/契约测试、22项P1协议测试和4项既有API/状态测试。SDK兼容探针更新为当前九个工具后通过7/7。没有安装新依赖，运行代码只使用 Python 标准库和 tau2 已使用的 Pydantic。

身份正反例验证了 search 在 profile 前、selector 不完整时零调用、claimed ID 不匹配时零详情读取、失败后纠正、不增三次锁定、跨账户请求零他人 profile 输出。订单测试验证本人清单和实际归属双重检查、错误 ID 不请求订单、返回值不能篡改绑定、重复实例不同原价和 payment/refund 方向不丢失。目录测试验证完整计数、可用性变化、空目录与畸形缺失的区别、单变体无 product_id、失败无部分计数。

错误测试覆盖400/404/405/409/413/422/502/503的分类与脱敏、不重试、畸形数据和错误资源重定向。容量测试调用实际 ClientAPI：超限 search 正文在后端前被413拒绝；超限响应返回502，不裁剪或重复查询。路径测试覆盖 #、/、?、%、空格作为不透明 ID 的编码。

合成轨迹在全新后端/工具实例按相同顺序重演，输出、错误、API请求序列和最终身份相同。此测试验证本项目公开的“全部工具调用按顺序重演”契约；不将上游通用 ToolKit 的只重演 mutating 调用机制冒充本项目宿主实现，也不擅自把 READ 标成 WRITE。真实平台重放兼容性仍需后续授权运行核对。

姓名多轮闭环使用真实 tau2 类型和 gateway，只有 provider 回复及后端数据为模拟。用户原文直接进入模型上下文，宿主执行真实工具；该测试证明协议和权限链路，不证明真实模型已理解中文、改口或其他表达。合成后端的路径/记录匹配仅供测试，不进入 agent/ 运行包。

摘要及本轮源码 hash 见 [P2-VALIDATION.json](P2-VALIDATION.json)。未运行真实模型或远程评测，没有修改绑定、任务范围或工作流。

## 边界与下一阶段

P3 尚未开始。结构化候选比较、calculator、变更方案，以及所有业务写入和转人工仍未实现；不承诺完整业务评测通过。本记录生成时 P2 尚未提交或推送；后续提交与推送结果以 Git 历史及对应工作流报告为准。

本轮没有触及五小时限额，P2 完成后停用条件续做安排；没有使用额度重置券。
