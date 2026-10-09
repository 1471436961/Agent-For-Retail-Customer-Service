> **教学环境说明：** 下文保留上游研究框架资料。实际命令以项目根目录 README 和 materials/CLASSROOM.md 为准。本项目没有 run_local_test 或 Client 接口申请工具；使用本地单元测试及远程 t1/p1/t2。当前 OpenAPI 中的退货／取消能力已经启用。

客户场景契约

本文档说明你可以为客户模拟编写的 JSON 文件。场景不是单元测试，而是描述客户带着某种情况、目标及其掌握的事实前来咨询。模拟器利用该场景与你的智能体对话。

要运行场景，请从构建环境调用 run_local_test 工具：

run_local_test(task_path="workspace/my_customer_scenario.json")


这只会使用你提供的场景文件或目录来测试提交的助手工具集。对于客户 API 任务，本地测试使用实现以下内容的沙箱服务： client_api/openapi.yaml。只有文档中记录的 REST 接口及其响应属于开发者契约；本地测试行为不会扩展或覆盖该契约。不会加载保留场景。

开发者编写的场景是本地行为探测。它们不会成为最终评测套件，也不定义评测的请求分布；仅通过这些场景并不能证明智能体整体质量。最终质量在更广泛的未见评测案例中衡量。

最小场景

{
  "id": "my_customer_scenario_001",
  "user_scenario": {
    "persona": "A concise customer who answers follow-up questions directly.",
    "instructions": "I am calling because I need help with <situation>. I know <facts the customer knows>. I want the agent to <customer goal>."
  },
  "evaluation_criteria": {
    "nl_assertions": [
      "The agent resolved the customer's request or clearly explained why it could not be resolved.",
      "The agent followed the domain policy in the provided materials."
    ],
    "reward_basis": ["NL_ASSERTION"]
  }
}


设置 reward_basis 请显式指定。如果省略，后端默认值为
["DB", "COMMUNICATE"]，因此自然语言断言会被评估，但不会影响报告的奖励。

后端生命周期

当 run_local_test 运行场景时，后端会执行以下操作：


将 JSON 文件加载到 Task 数据模型。

加载 workspace/tools.py, workspace/agent.py，以及套件中存在的任何其他适用工作区文件。

安装可用操作、通用套件资源访问、受限模型网关和运行时配置，然后调用 create_agent()。工厂函数可通过以下方式读取这些能力： get_agent_context().

使用标准本地场景运行时创建模拟客户。

如果领域包含客户侧运行时，则通过所提供的客户侧运行时路由客户侧工具调用。

转换 user_scenario 转换为文本，使用 str(task.user_scenario) ，并将该文本作为模拟客户的私有指令。

如果场景包含 user_tools，则将可用客户侧工具过滤为该列表。如果省略 user_tools ，且领域提供客户侧工具，则使用该领域默认的客户侧工具。

在客户、智能体和环境之间逐轮运行会话。

根据以下内容评估最终对话记录及环境状态：
evaluation_criteria.reward_basis.

在以下目录写入带时间戳的产物： simulations/ ，包含对话记录、工具调用、奖励详情，以及构建执行框架可以访问的序列化结果数据。


模拟路径让你可以使用与提交后相同形态的客户/用户运行时运行自己的场景，无需自行实现客户侧电话或设备工具。

对于 REST 套件，每个场景必须且只能选择一种客户 API 模式。如果省略
client_api 字段，则模式为 seeded：场景从以下文件中列出的合成记录的全新副本开始：
client_api/development_seed.json。这些公开标识符有意保持稳定，供本地测试使用，不属于最终评测。记录使用与领域常规数据相同的标识符约定和资源结构。有些领域还会列出具名的 fixtures ，用于将本地客户/设备运行时置于文档中记录的状态。

一个 mock 模式场景的形式如下：

{
  "id": "declining_service_001",
  "client_api": {
    "mode": "mock",
    "module": "workspace/mock_client_api.py",
    "config": {"account_id": "acct_test", "failures_before_success": 1}
  },
  "user_scenario": {
    "instructions": "I need help with account acct_test."
  },
  "evaluation_criteria": {
    "nl_assertions": ["The agent handled the changing service response."],
    "reward_basis": ["NL_ASSERTION"]
  }
}


该模块只在隔离的候选沙箱内运行，且必须定义
create_mock_client_api(config)。每个全新的场景实例都会调用一次工厂函数。它返回一个可调用对象，或带有以下方法的对象：
request(payload)，其中 payload 仅包含公开的 method, path,
query, body，以及 headers。返回常规客户 API 响应封装： status_code, body，以及可选的 headers 和
elapsed_seconds.

class MockClientAPI:
    def __init__(self, config):
        self.calls = 0
        self.failures_before_success = config["failures_before_success"]

    def request(self, request):
        self.calls += 1
        if request["path"] == "/v1/example":
            status = 503 if self.calls <= self.failures_before_success else 200
            return {"status_code": status, "body": {"call": self.calls}}
        return {
            "status_code": 404,
            "body": {"error": {"message": "Not mocked"}},
        }

    def verify(self):
        assert self.calls >= 2, "expected the example operation to be retried"


def create_mock_client_api(config):
    return MockClientAPI(config)


状态属于返回的对象，因此相同请求可能随时间产生不同响应。可选的 verify() 钩子在会话后运行；抛出异常或断言即可让本地场景失败。带时间戳的模拟产物记录每个模拟请求和响应，以及验证结果。回调异常会报告为本地测试失败。

模拟代码与候选运行时的其他部分受到相同的离线、工作区只读、时间、请求数量、JSON 封装和载荷大小限制。它无法调用或检查真实客户环境。执行框架有意不依据以下内容验证模拟操作的结构： client_api/openapi.yaml；模拟结果是否代表可能的客户响应由开发者负责。

seeded 和 mock 互斥。模拟场景不能选择
development_fixture 或使用 DB 评分，因为没有真实客户数据库可供比较。请将 reward_basis 显式设置为对话记录、响应、操作或环境断言。若领域包含客户侧模拟器工具，它们仍由宿主提供，但其私有运行时不会与模拟回调共享；客户需要了解的任何模拟客户系统状态，请在场景指令中描述。

采用模拟后端的本地测试运行在当前构建运行时镜像上。这不会改变任务为已提交或保留评测会话选择的版本化镜像。

客户指令

user_scenario 是提供给模拟客户的信息，而非提供给智能体的信息。智能体只能看到客户在会话中说出的内容。

user_scenario.persona：可选。客户的一般沟通风格或背景。请将其与任务特定情况分开。

user_scenario.instructions：客户的处境、掌握的信息及其试图达成的目标。它可以是普通字符串，也可以是具有以下字段的结构化对象：

{
  "domain": "example_support",
  "reason_for_call": "Why the customer contacted support.",
  "known_info": "Facts the customer knows and may provide.",
  "unknown_info": "Facts the customer does not know and should not invent.",
  "task_instructions": "What the customer is trying to accomplish."
}


结构化形式会渲染为带标签的文本章节。 known_info 应包含客户可以透露的事实。 unknown_info 应包含客户不应编造的事实；如果智能体询问，客户应表示自己不知道，或询问如何找到相关信息。

奖励依据

evaluation_criteria.reward_basis 控制哪些检查影响数值奖励。可用值如下：




值
检查内容
典型本地用途





NL_ASSERTION
使用 LLM 评判器判断对话记录是否满足以下位置的每个字符串： evaluation_criteria.nl_assertions.
客户模拟的最佳默认选项，因为它检查行为，无需精确工具名称。



RESPONSE_ASSERTION
检查以下位置的确定性助手响应措辞限制： evaluation_criteria.response_assertions.
适用于精确风格限制，如禁用词或短语出现次数上限。



COMMUNICATE
检查以下位置的每个字符串是否 evaluation_criteria.communicate_info 出现在助手文本回复中。
适用于简单、精确的沟通检查；它基于子字符串匹配，灵活性低于 NL_ASSERTION.



ACTION
检查对话记录中的工具调用是否按名称和选定参数匹配 evaluation_criteria.actions 。
适用于有意要求特定工具接口的情况。它可能过度限制其他实现。



DB
将最终环境状态与通过以下内容预期的状态比较： evaluation_criteria.actions.
适用于能通过操作指定预期状态时的精确状态变更检查。如果未提供操作或环境断言，则不存在有意义的状态检查。



ENV_ASSERTION
针对环境调用以下位置列出的函数： evaluation_criteria.env_assertions ，并将其布尔结果与以下值比较： assert_value.
仅在环境中存在相关断言辅助函数时有用。




多个奖励依据会相乘。任何纳入的检查得到 0，整体奖励就变为 0。

评估标准字段

nl_assertions：关于会话应满足条件的自然语言陈述。仅在以下情况下计入奖励： reward_basis 包括
"NL_ASSERTION".

response_assertions：针对助手面向客户的消息的确定性断言。仅在以下情况下计入奖励： reward_basis 包括
"RESPONSE_ASSERTION".

communicate_info：应出现在助手消息中的精确字符串或事实。仅在以下情况下计入奖励： reward_basis 包括
"COMMUNICATE".

actions：预期的助手或客户工具调用。每个操作包含：

{
  "action_id": "unique_action_name",
  "requestor": "assistant",
  "name": "tool_name",
  "arguments": {"arg_name": "arg_value"},
  "compare_args": ["arg_name"]
}


requestor 可以是 "assistant" 或 "user". compare_args 控制比较哪些参数。如果省略 compare_args ，则检查所有提供的参数。

env_assertions：环境函数检查。每个断言包含：

{
  "env_type": "assistant",
  "func_name": "assertion_or_tool_function_name",
  "arguments": {},
  "assert_value": true,
  "message": "Optional failure message."
}


env_type 可以是 "assistant" 或 "user"。仅当相关环境中存在目标函数时使用。

可选场景字段

description：可选的自用备注，说明场景目的、相关政策或不常见的边界情况。如提供，必须是 JSON 对象；可使用如下字段： purpose 和 notes。例如：

{
  "purpose": "Exercise a repeat caller asking about a pending request.",
  "notes": "REQ-2041 is created and left pending during setup."
}


initial_state：可选的会话或消息历史设置。大多数本地场景不需要此项。在 REST 套件中，它可包含：

{
  "development_fixture": ["service_paused", "alerts_muted"],
  "initialization_actions": [
    {
      "env_type": "assistant",
      "func_name": "tool_or_setup_function",
      "arguments": {}
    }
  ],
  "message_history": []
}


在 REST 模式下，开发者编写的场景不能使用 initialization_data：这会描述私有客户存储，而非公开 API。也不能使用用户初始化操作。允许助手 initialization_actions ，但其名称只解析到开发者在以下位置定义的助手工具： workspace/tools.py。这些操作在全新的客户 API 上下文安装后运行，因此包装工具可以通过常规且有文档记录的 REST 调用准备案例。它们不会解析到私有客户函数。

在种子模式下， development_fixture 可以是该领域在以下文件中发布的固定配置 ID： client_api/development_seed.json，也可以是这些 ID 的列表，例如
"service_paused" 或 ["service_paused", "autopay_off", "alerts_muted"]。它仅用于本地测试，选择有文档记录且由宿主管理的状态；它不是数据库载荷或私有函数调用。列表按顺序应用，因此两个配置涉及相同设置时，后一个生效；每个配置最多列出一次。未知配置 ID 会被拒绝。如果领域未列出任何配置，或基线连接状态已适用，请省略该字段。

message_history 预加载已有会话。旧版非 REST 套件保留现有的基于数据库的初始化行为。

user_tools：可选的客户侧工具名称列表，指定本场景中用户模拟器可用的工具。省略此字段即可让客户侧运行时选择默认工具。只有希望模拟客户完全使用文本时，才使用空列表。

required_documents：可选的文档标题列表，列出预计在知识密集型领域中有用的文档。它是供分析和调试使用的元数据，不会自动为智能体检索文档。