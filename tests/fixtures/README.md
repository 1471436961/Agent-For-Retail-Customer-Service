# P0 合成开发资料

P2新增 [p2_backend.py](p2_backend.py)：根据下述合成记录提供六个只读REST操作，故意不代替toolkit做权限检查，用于证明Agent工具自身会阻止未验证/跨账户读取。该后端只匹配结构化HTTP路径和selector，不解析客户自然语言，也不进入agent运行包。

`p0_baseline.json` 用于本地契约检查和后续工具/Agent 测试设计，不上传到 Agent 运行包。所有记录、地址、姓名、邮箱、价格及回执均为合成数据，不包含凭证或公开评测答案。

## 内容与解释

| 字段 | 用途 |
|---|---|
| records.customers / products / orders | 当前正常 schema 的快照；包含第二客户用于越权负例 |
| wire_examples | 每个 operation 一个正常请求/响应；14项分别独立，不构成连续执行日志 |
| additional_wire_examples | 负差价换货请求及回执，使用另一个 delivered 订单 |
| request_variants | 完整姓名+邮编的身份选择器；默认 search 例子使用 email |
| negative_schema_examples | 七个故意违反正常 schema 的输入/响应，预期 schema 拒绝 |
| schema_valid_policy_counterexamples | 五个 schema 接受但业务层必须阻止的请求；不是可执行操作 |
| identity_dialogue_blueprints | 不同措辞、多轮、纠正、中英文、ID单独、ID不匹配；留给后续语义测试 |
| error_examples | 合成 APIError 封装，用于后续错误处理，文案不是线上真实响应 |

订单快照保留重复 item_id；`#P0-AMBIGUOUS` 的重复变体具有不同成交价，用于验证公开接口不能精确指定某一实例。`#P0-NEGATIVE` 提供更便宜规格的换货场景。订单原价可与当前目录价格不同，验证器不得把二者强制相等。

后续测试会验证：声明客户 A 不可读 B 详情；只报 ID 不算认证；模型根据完整对话提交结构化身份字段；后端查找结果才构成匹配依据。不得把这些语言蓝图编译成运行时关键词或正则路由。

## 当前可运行的检查

在仓库根目录执行：

```powershell
.\.venv\Scripts\python.exe scripts\check-p0-baseline.py --report evaluation-results\baseline.json
```

检查正常 schema、负例确实被拒绝、operation 完整性、来源 hash、fixture 的引用和货币类型一致性。依赖 jsonschema，已随本地 tau2 核心依赖安装。检查不运行 LLM、业务写入或远程评测；不能作为 Agent 行为通过的证明。
