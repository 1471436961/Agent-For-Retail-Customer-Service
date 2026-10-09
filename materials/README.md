> **教学环境说明：** 下文保留上游研究框架资料。实际命令以项目根目录 README 和 materials/CLASSROOM.md 为准。本项目没有 run_local_test 或 Client 接口申请工具；使用本地单元测试及远程 t1/p1/t2。当前 OpenAPI 中的退货／取消能力已经启用。

学员试点覆盖说明： 请先阅读项目根目录的 README。 workspace/ 表示 agent/ 位于你的仓库中。本试点提供远程 t1/t2 提交，而非上游的 run_local_test 工具。除非明确选择子集，否则 t2 会运行所选场景中的所有案例；这并不是完整的多领域基准测试，也不是上游的额度预算评分。允许使用的模型由导出的部署清单与讲师服务决定。


构建客户服务智能体：retail_plus

你的任务是利用此套件中的领域材料构建可靠的客户服务智能体。具体资料集因领域而异；请通过文件树和文件级文档了解内容，再将这些材料转化为可运行的实现。

成功标准

智能体质量以其通过评测案例的比例衡量。评测案例不会提前公开，可能覆盖所提供材料代表的全部行为范围：常规请求、复杂的多步骤或多意图请求、不常见的边界情况、不完整或变化的信息，以及必须拒绝或引导至其他渠道的请求。

只有当智能体正确处理底层操作、使系统处于正确状态、遵守领域规则，并向客户传达正确的信息时，案例才算成功。在满足框架契约的前提下，不要求采用特定架构或开发流程。

如何使用此套件

将套件目录视为可用资料清单。来源信息可能分散在政策文档、API 契约、知识库文件、培训记录或其他客户提供的资料中。请根据邻近的文件头、目录索引及文件名理解各份资料。

workspace/ 已包含组织现有的实现，按原样继承。 framework/ 说明你的实现必须满足的契约。 simulations/ 存放仅针对候选实现的本地模拟运行所写入的产物。

必需输出

评测器会将这些文件作为稳定入口导入。你可以按需要添加辅助模块、检索层、规划器、验证场景或任何其他支持架构；以下文件只是集成接口。



workspace/tools.py ——一个 ClientAPIToolKitBase 子类，包含使用 @is_tool装饰的智能体操作，实现客户所提供材料中描述的全部操作。每个方法使用注入的 self.client_api。参见 framework/client_api_contract.md 和 client_api/openapi.yaml.




workspace/agent.py ——智能体实现。它可以读取提供的任何套件资料，并可以在以下位置添加辅助、提示词、规则、索引或其他证据文件： workspace/ ，组织方式由你自行决定。参见 framework/agent_contract.md.




模拟环境

你可以使用模拟环境，在其中创建模拟客户场景，并让这些客户与你的客户服务智能体交互。不会提供示例场景；请基于客户材料中的场景自行编写 JSON 文件。

参见 framework/scenario_contract.md 了解客户场景格式。

要运行仅针对候选实现的端到端模拟，请调用 run_local_test 工具并传入场景路径，例如 run_local_test(task_path="workspace/my_customer_scenario.json")。该工具仅使用你自己编写的场景文件测试你自己的助手工具集。若领域包含客户侧运行时，它会通过提供的客户侧运行时路由客户/用户工具调用。每次运行都会在以下目录写入带时间戳的 JSON 产物： simulations/ ，便于日后查看历史对话记录与奖励。
进行黑盒行为检查时，请使用自然语言断言，并检查返回的对话记录，不要依赖内部函数名称。

性能要求


agent_credit_budget: gpt-5.6-sol, anthropic/claude-opus-5, google/gemini-3.1-pro-preview, moonshotai/kimi-k3, deepseek/deepseek-v4-flash, gpt-5.6-terra, gpt-5.6-luna, anthropic/claude-haiku-4-5, google/gemini-3-flash-preview, qwen/qwen3.8-27b, google/gemma-4-31b-it, gpt-5.4-nano, qwen/qwen3-30b-a3b-instruct-2507, google/gemma-4-26b-a4b-it, gpt-4.1-mini, moonshotai/kimi-k2.6, gpt-4o-mini, anthropic/claude-sonnet-5, gpt-5-mini, google/gemini-3.1-flash-lite 共享每次会话 0.3200 额度的预算。通过模型网关调用这些模型所产生的每个输入和输出 token 都计入预算；推理 token 计作输出。


使用 run_local_test 在迭代过程中查看测得的性能。额度超支属于软惩罚：最终得分为平均任务奖励减去每次会话超预算比例的平均值，最低为零。延迟要求仍是硬性门槛。

重要


允许使用的智能体模型及其推理限制由以下文件固定： framework/deployment_manifest.json。你的实现可以从中选择。

开发者编写的场景是本地探测，并非最终评测分布。最终评估取决于智能体在更广泛的未见客户请求中的行为。