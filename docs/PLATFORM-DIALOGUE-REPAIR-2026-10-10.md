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

## 5be0938 实际平台结果

Job bffe1dd9-79e3-4b95-8404-ea49686aeb55 / GitHub run 38041022408，t1 0/1。
首轮实际选择 openai/enterprise-haiku，2 条消息、3 个动作、4,079 字节，
教师 max_tokens=1024；第 1 次模型调用仍返回 broker RateLimitError/429。
没有工具轨迹。请求缩减没有解除此次失败；不是业务身份校验拒绝了模型调用。
公开摘要和 GitHub 客户端日志均没有原始 provider 响应。Chrome 用户已经登录，
但浏览器控制连接读标签/新建标签请求失败，暂未取得工作台接入状态。

继续补充自定义网关错误的固定概念词汇（额度、余额、限流、并发等），只从
识别为 429 的基础设施异常提取，不解析客户文本；不保留任意异常原文。
记录允许模型数量和所选择 option 的名称，用来核对是否只有该默认别名、
是否选择过可选配置。防止历史投影失败错误显示上一轮的请求摘要。
164 项回归测试通过（15.592 秒），不额外进行本地付费调用。

## 6edf1fe 实际平台结果及后续诊断

Job 467315b7-afa0-4512-ac4a-f48df165885e / GitHub run 38041779447，t1 0/1。
仍为第 1 次 openai/enterprise-haiku 调用，2 消息、3 动作、4,079 字节，
教师 max_tokens=1024。允许模型数量确实为 1，one_of 选择参数为空。
429 原始异常识别出的固定概念为 quota、model；没有业务工具轨迹。
用户工作台未显示此绑定的配额详情，Chrome 控制重连后仍无法读取。

继续支持网关将固定错误码放在 error/detail 的形状，包括
model_quota_exceeded、model_quota_not_configured、budget_exceeded 等。
普通 requests/tokens 文字不能当作机器错误码；固定 Model quota exceeded
文字只分类为模型配额，不虚构 provider_error_code。只输出闭合标签。
完整回归 165 项通过（13.269 秒），最终诊断针对测试 11 项通过（0.668 秒）。
模型名始终取自注入网关，未修改配额、token 固定值、绑定或评测范围。

## b495d6e 实际结果与配额错误证据

Job 5f0296f4-756a-4c6b-8bbf-8a3e9e2c1916 / GitHub run 38042398816，t1 0/1。
相同首请求仍收到 RateLimitError/429，原文包含 quota、model，但不包含
上一版支持的固定错误码或 Model quota exceeded 等完整固定短语。
因此不能把这个结果写成已确认 model_quota_exceeded。

再次按 quota/429 精确问题查询 Parallight Lab，返回的仍为公开 U1、U2
（INTERFACE-CLARIFICATIONS.md / d58e1786ae00）与本人摘要，未提供
原始响应、配额检查条件或恢复入口。U2 明确可用模型与配额为独立限制。

为了区分否定、缺失配置与耗尽，新增仅在已识别 429 中的有限词汇序列。
保留 no/not/available/configured/exceeded 等固定词及顺序；未知完整 token
替换成 redacted，不拆开邮箱、URL、账号、数值或 key。该序列只作诊断，
不影响用户语义、模型调用、工具行为或限额，不据此猜测错误码。
新增实际 SDK broker 测试验证词序及私密标识不外泄，12 项诊断测试通过。

## e7696c8 实际结果

Job 6e775518-48d9-4981-be83-413bab9f1faa / GitHub run 38042892154，t1 0/1。
429 带 detail 字段，但它的内容被有限英文词序诊断整体隐藏。
进一步支持 CJK 中文（含 JSON Unicode 转义）中的固定基础设施词，以及
每个分量均在词汇表中的 quota 下划线/连字符标识；其他标识仍整体隐藏。
映射出的词只用于诊断，不据此填写未经原文证明的 provider_error_code。
实际 broker 离线测试 13 项通过（1.074 秒）；没有新增外部模型调用。
