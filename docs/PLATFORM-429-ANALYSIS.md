# 本地真实模型成功与平台 t1 429 的调用链分析

2026-10-10。分析已有记录，不新增模型调用、评测、提交或推送。

## 本次运行身份

- 仓库：1471436961/agent-for-retail-customer-service；main。
- Commit：24511a7678133da5f5ea0f95ddc0b4e96a1ad2c1。
- Binding：3d81f316-16c1-452d-939f-48776870bd23。
- GitHub run：38035706842。
- 平台 Job：c6465c7c-8e13-4383-b9dd-1a0acee50f4b。
- t1 / retail_plus / python；integration-check，0/1。
- Snapshot：8f575f69de2eb7a35841c0278c6de1b53c64755ff053514b77355eca822a2001。
- Environment：hyper-lab-v1-90c863ee6bf1ee8c9b72c6a58a6066d8730c713b6cbd7d0f785cf1e158bc6f4f。
- Evaluator/client revision：70c12bdf3d52666106dba3f22f78a74bbd1a18dd。

## 直接失败原因和实际消息来源

首轮模型决策未产生有效模型响应：宿主错误经 broker 传回时包含
RateLimitError 类型前缀和 Error code: 429。Agent 收到外层 RuntimeError 后
保存 provider_rate_limit / 429 / model_broker / gateway_generate 的脱敏诊断，
随后主动返回英文停止消息。英文消息由 application.py::_stop 生成，不是模型生成。

在本地固定 SDK 源码中，CandidateProcess._model_response 将宿主异常序列化为
error.type/error.message；_brokered_generate 只用 message 构造 RuntimeError。
diagnose_gateway_failure 从 broker 的固定异常前缀识别报告类型，只有在识别成功时
才从 Error code: NNN 提取 HTTP 状态。普通 RuntimeError 不会默认变成 429。
本次报告保留的是 SDK 文本中报告的类型/状态，不包含原始 HTTP 响应体或请求头。

实际顺序：客户消息 → Agent 调用注入的 ModelGateway → 宿主推理路径返回
RateLimitError/429 → broker 包装为 RuntimeError → Agent 返回停止文字 → t1
断言未完成。轨迹没有任何工具调用；identity_verified_before_details=false
表示所需验证步骤未发生，不能由它推断执行过 getCustomer 或验证顺序颠倒。
completed_variants=1 和 status=done 表示评测已结束，不表示业务成功。

## 本地与平台不是同一条模型服务路径

| 项目 | 本地真实模型测试 | 平台 t1 |
|---|---|---|
| 模型 | 自建宿主配置 openai/kimi-k2.6，HTTP model=kimi-k2.6 | context.model_gateway 注入；该报告不含实际别名/上游型号 |
| 模型服务 | Moonshot 中国官方 API | 平台配置的服务路由；该报告不含实际路由 |
| 密钥 | 仓库外、用户独立授权的模型密钥，由本地测试宿主读取 | 平台宿主提供；Agent 不读取/替换 |
| 模型执行 | 真实 SDK broker/宿主函数和真实 HTTP；本地进程内驱动 | 平台密封执行环境和平台模型服务 |
| 业务 API | 本地合成客户记录与注入的 ClientAPI | 评测环境提供的 ClientAPI |
| 重试 | OpenAI max_retries=0、宿主 num_retries=0、空回复尝试=1 | 报告未给出有效设置；本地 SDK 默认 num_retries=3 |
| 本轮结果 | 16 次实际模型 HTTP 请求全为 200 | 首轮模型决策报告 RateLimitError/429 |

本地第一组首次真实请求包含 27 个工具 schema，返回 usage.prompt_tokens=3534、
completion_tokens=33。这个用量是 Kimi tokenizer 的结果，不是平台 token 计量。
本地成功证明所测试输入下 Agent 可以通过 LLM 选择工具、执行独立搜索和 ID 匹配、
接收带 ID 的结果并回答；不验证平台服务的凭证额度、路由或限流设置。

安装时公开资料 materials/framework/deployment_manifest.json 给出的允许别名是
openai/enterprise-haiku、max_tokens=1024。但其 classroom notes 要求最终以注入的
available_models 为准；不能把安装快照当作此运行的实际模型服务记录，也不能由
openai/ 前缀推断服务一定由 OpenAI 官方承载。SDK model_routing 支持改写上游型号
和 provider endpoint。此次报告缺少该运行的实际解析结果。

## 排除的混淆和仍需的服务端数据

1. Agent 自己的 128 次模型调用/16 轮工具限制分别走 model_call_limit 和
   tool_round_limit。本次走 gateway_failure，不是这些阈值触发。
2. 本地测试脚本的 16 次/10 元上限不在 Agent 提交包里，不参与平台运行。
   上次本地故障组被该预算拦截并包装为连接错误，与本次平台报告的 429 分开。
3. deployment_manifest 的 0.32 research credits 不是美元额度/课堂模型允许列表。
   本地 SDK CreditLedger 在成功生成之后记录 usage；该路径不主动构造 HTTP 429。
   平台服务本身是否另有额度限制，必须看服务端记录。
4. SDK 错误分类不是客户语言解析，不读取用户名字/邮箱来判断限流。
5. 一次 Agent 网关调用可能包含 SDK/客户端的多次 HTTP 尝试。此次执行耗时约
   33.59 秒，不能据此推算重试次数，也不能宣称底层只发出过一个 HTTP 请求。
6. workflow_verified=true 与 commit_verified=false 含义不同。前者验证 GitHub
   工作流身份，后者没有证明平台独立 fetch 仓库并核对源码；报告给出了提交 SHA
   与上传快照 hash。新错误文案/诊断也出现在轨迹，不能把该字段当成旧代码证据。

本次已定位失败边界到平台宿主的模型调用。要区分 429 是请求频率/并发、token
吞吐、账户/项目额度，还是模型路由对应网关的拒绝，需要该 Job 同一请求的：

- 脱敏 error.code/type/message 与响应来源层；
- upstream request ID、Retry-After、相关限流响应头；
- 请求别名 → 实际 provider/model 的解析结果；
- 实际输入/输出 token 上限、发送时间与 HTTP 尝试次数；
- 平台配置的有效重试设置与该模型/项目的限额状态。

这不是请求替换模型、添加业务硬编码或重跑。可凭上述 Job、commit 和 run 在
平台宿主日志中定位同一失败请求，无须提供模型密钥或 Enterprise 权限文件。

本轮经已有授权用 Parallight Lab 查询该 Job 的公开资料/本人诊断，接口返回 HTTP
404，没有返回上述字段；未改用独立凭证重试。内置浏览器转到平台登录页，未取得
额外模型诊断，也未代替用户登录或改变设置。已有 GitHub artifact 和用户提供的
JSON 均只有脱敏类型/状态，不能把某一种 429 子原因写成已确认事实。

本地源码依据：support_agent/application.py、gateway.py、domain/gateway_failure.py；
本地固定 tau2 SDK commit 6e9f34c685d40fa7a9f5935d8970af6fd9d5f118 的
hyper/agent_context.py、hyper/sandbox/candidate_server.py、sealed_runner.py、
utils/llm_utils.py 和 model_routing.py。远程 environment hash 不等于已公开的 SDK
commit，不能声称本地源码版本已独立匹配平台镜像内部实现。
