# P1 实施进度

本页记录P1时的实现与验证快照；P1提交为 `dc4dd74`。后续身份与读取能力、当前代码和测试状态见 [P2记录](P2-IMPLEMENTATION.md)。

状态：已完成（本地离线协议验收，2026-10-09）。P0 本地提交为 `269d9c7`。范围保持 main / retail_plus / python / t1 / case_ids:null。

## 实际实现

- `create_agent()` 仅在工厂内取得 context，实例保存 gateway/actions/resources/runtime_config；后续消息不再查找活动 context。
- `RetailAgent` 每个宿主轮次调用一次注入 gateway。模型负责理解用户与选择工具；已删除原来的邮箱/ID 正则和固定成功回复模块，不添加自然语言关键词分支。
- 使用当次 `available_models` 的第一个模型；固定参数由网关提供，Agent 不传。`one_of` 显式选该配置的首个合法值。相同模型有多个配置时停止构造，不能靠覆盖固定参数消除歧义。这是明确的工程选择，不声称为平台新增约定。
- 只暴露现有 READ `lookup_customer`。Agent 返回真实 `AssistantMessage` / `ToolCall`；由宿主执行，Agent 不直接调用 Tool 或 ClientAPI。
- JSON 状态保存原始历史、所有未完成调用 ID、已用 ID、模型尝试次数与工具轮次。多个结果可乱序，必须完整且匹配当前批次；缺失、重复、陌生 ID 或错误 requestor 不交给模型续写。
- 传入的历史可重建 pending 批次；历史中的逐条 ToolMessage 可合并为一批。JSON state 可交给新的 Agent 实例继续；不修改调用方历史/状态，不在模块全局保存会话资料。
- 文本与工具调用互斥，检查真实工具参数类型、额外参数、名称和唯一 ID。非法模型结果不交给宿主执行。
- 原始工具结果保持不变；模型看到的是带 `ok/result` 或 `ok/error` 的内容投影。先检查 error，再解码 JSON；错误内容即使长得像有效客户记录也不会投影成成功。畸形 JSON 不当成成功数据。
- provider 异常不重试、不切换模型；只保存脱敏原因，返回未完成说明。错误字符串不复制进状态或客户回复。

## 工程调用边界

默认每会话最多128次模型尝试，每次用户请求最多16批工具调用。新用户请求重置后者；失败的模型尝试也计数，历史恢复保守地按历史 assistant 数计数。达到工具批次上限后仍允许模型给出最终文字，但不再向宿主返回新工具批次。耗尽或协议错误时保留原始证据和 pending，状态终止，不能宣称任务完成。

这些是当前工程默认值，不是平台限额、美元预算或费用授权。可通过我们自己的 `runtime_config.agent_limits` 配置两个正整数，不修改平台模型固定参数。后续正式运行应结合实际平台限制及用户费用授权核对。

## 实际验证

命令在仓库根目录执行：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

本轮26/26通过：22项P1协议测试、4项既有API/状态测试。测试使用实际安装的 tau2 消息、context、gateway、Tool 与 ClientAPI，只有 provider 的生成函数和业务传输被模拟。完整闭环测试由模拟宿主执行真实 `lookup_customer`，验证 search 先于 profile；Agent 发出调用时后端零访问。

覆盖实际模型网关调用、固定参数注入与 one_of、多个乱序结果与错误标记、JSON 状态跨实例恢复、历史输入不被修改、会话隔离、非法 assistant/调用参数、调用 ID 复用、模型失败及两个调用上限。不同语言样本只证明原文进入模型上下文，不证明真实模型理解它们。

检查摘要及P1验证时源码 hash 见 [P1-VALIDATION.json](P1-VALIDATION.json)。原模板的两项正则/固定回复测试随旧模块移除；四项 API/状态测试仍保留。缺失 `.env`、上游业务数据和 voice 扩展的提示不影响本轮离线测试，不为消除提示创建凭证或额外下载数据。

## 当前边界与下一步

未运行真实模型、远程评测或 push。P1 仅接既有 email lookup；姓名+ZIP、toolkit 单客户绑定及其权限测试属于 P2。当前 prompt 的单客户约束尚不构成 toolkit 级安全实现，P2 必须补齐；不将 P1 骨架作为完整业务 Agent 部署。P2 尚未开始。

本轮未达到五小时限额；条件续做安排在 P1 完成后停用，不消耗重置券、不继续扩展到 P2。
