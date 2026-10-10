# 平台首轮模型调用与 tau2-bench 对照

基线：9d253dcd1cca9e77fbd8fa8d8392f59429634773。
目标保持 main / t1 / retail_plus / python / 原绑定范围。

官方参考：
- https://github.com/sierra-research/tau2-bench/blob/main/src/tau2/agent/llm_agent.py
- https://github.com/sierra-research/tau2-bench/blob/main/examples/agents/minimal_text_agent.py

核心循环保持 tau2 消息历史、展开 MultiToolMessage、保存对应调用 ID、返回
AssistantMessage；使用项目要求的 context.model_gateway.generate(actions=...)。
不使用官方示例的直接 provider generate() 来绕过项目网关。

已通过 Parallight Lab 查询本人 Job 8b9d2b5a-95d5-460b-9dbf-0a61046a57e6。
公开 U1 契约路径 integrations/enterprise-ai-starters/common/INTERFACE-CLARIFICATIONS.md，
版本 d58e1786ae008e15f7bafe3ba51f215cfe18415a3c55b532839a113724b8a6ae。
接口与本地一致。工具只返回公开契约和本人评测摘要，不含原始 provider 响应。

原首轮发送 27 个动作的 schema，JSON 约 22,313 字节，系统提示 7,478 字符。
验证前无需暴露业务写工具和完整计划 schema。现在按实际成功验证回执切换
动作与提示；身份语义、选择器和客户意图仍由模型理解，不解析用户措辞。
失败/错误回执不开放业务能力；工具自身身份、归属和确认授权检查保持不变。
验证后开放全部业务能力，不改变案例范围或模型固定参数。

失败报告增加模型名、调用序号、消息数、动作数、候选请求 JSON 字节数、
教师 token 固定设置。只保存长度和固定标签，不保存请求内容或任意异常原文。
429 中仅保留已知 provider 子码与固定分类；无法识别时保持空值。
没有自行调整 token 设置、更换模型、读取凭证、追加本地付费调用或自动重试。

163 项本地测试通过（17.588 秒）：实际 SDK wire 验证首轮请求小于 5 KB，
真实验证回执后开放完整工具；失败验证不开放业务工具；同一 broker 的已知
429 子码分类和敏感文本不外泄。本地测试不证明 provider 配额或平台通过。

请求缩减是否解除平台失败，由本次提交后的同一自动评测结果判定。
