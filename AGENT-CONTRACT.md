# Agent 接口契约：Python 与 TypeScript

你需要实现 Agent，而不是业务服务器。只提交 `agent/`：最多 128 个文件，合计 2 MiB，单个文件不超过 256 KiB。平台为每次评测创建全新的隔离环境，提供工具与模型调用中介，并根据真实业务状态评分。

不要包含凭证、依赖、`node_modules`、虚拟环境或隐藏测试。上传器和 GitHub runner 不执行提交的源码；源码以数据形式传入封闭 Docker 环境执行，该环境只读、无网络、非 root，并限制 CPU、内存和运行时间。

## 共同的轮次协议

环境发送用户消息或多个工具的返回消息。你返回一条 assistant 消息和下一份状态。消息使用 `role`、`content` 和可选的 `tool_calls`。每个工具调用包含 `id`、`name`、`arguments`（对象）以及 `requestor: "assistant"`。多个工具返回的消息使用 `role: "tool"`，并在 `tool_messages` 数组中包含各工具输出：`content` 是 JSON 文本，`error` 表示失败。宿主执行所请求的工具，并将结果交给你的下一轮调用。assistant 消息不含工具调用时，表示本次回复结束。

Agent 可以通过注入的网关调用模型，不能使用自带凭证的外部 HTTP 客户端。可选模型受教师配置限制。网关调用次数和资源有边界；模型服务商异常不代表通过评测。

## Python

`agent.py` 导出 `create_agent()`，返回的对象实现：

```python
def get_init_state(self, message_history=None): ...
def generate_next_message(self, message, state):
    return AssistantMessage(role="assistant", content="..."), next_state
```

从 `tau2.data_model.message` 导入 `AssistantMessage`、`ToolCall` 和 `MultiToolMessage`。`tools.py` 定义 `ClientAPIToolKitBase` 的子类，使用 `@is_tool(ToolType.READ)` / `@is_tool(ToolType.WRITE)` 声明函数。`self.client_api.request(method, path, query=..., body=...)` 返回的响应具有 `status_code`、`body` 和 `raise_for_status()`。

在 `create_agent()` 内调用 `tau2.hyper.agent_context` 的 `get_agent_context()`，保存返回的上下文。模型网关为 `context.model_gateway`，使用它提供的 `available_models` 和 `generate()`。保留该上下文供后续轮次使用，不要在工厂函数之外重新查找上下文。

## TypeScript

`agent.ts` 导出 `createAgent(context)`，返回：

```ts
export function createAgent(context: AgentContext) {
  return {
    getInitState(messageHistory: Message[] = []) { return {}; },
    async generateNextMessage(message: Message, state: object) {
      const reply = await context.modelGateway.generate({
        model: context.modelGateway.availableModels[0],
        messages: [{role: "user", content: message.content || ""}],
        tools: context.tools,
      });
      return {message: reply, state};
    },
  };
}
```

这只是接口示例，不是完整的对话／历史管理策略，也不是 benchmark 答案。方法可以同步或异步实现。可选的 `isStop`、`setSeed` 和 `stop` 钩子对应 Python 的可选钩子。

`tools.ts` 导出 `createTools(context)`，返回由以下对象组成的数组：

```ts
{
  type: "read", // read | write | think | generic
  schema: {type: "function", function: {
    name: "lookup_customer", description: "Read a customer",
    parameters: {type: "object", properties: {customer_id: {type: "string"}}, required: ["customer_id"]}
  }},
  async execute({customer_id}) {
    const response = await context.clientApi.request("GET", `/v1/customers/${encodeURIComponent(customer_id)}`);
    response.raiseForStatus();
    return response.body;
  }
}
```

`context.clientApi.request(method, path, {query, body, headers})` 返回 `{status_code, body, headers, raiseForStatus()}`。`context.tools` 包含工具 schema，`context.modelGateway.availableModels` 包含允许使用的模型名称，`context.clientApi.context` 是当前对话上下文。

模型选项采用 snake_case（例如 `max_tokens`），与代理服务契约一致。不要传入教师固定的选项，网关会提供配置值。这些值是固定设置，不是上限；覆盖其中任一设置，即使改成更小的值，也会导致候选 Agent 验证错误。不要假设某个固定的 token 上限。

Node 24 使用原生类型擦除执行 TypeScript，**不是完整的 TypeScript 构建**。请使用显式 `.ts` 导入和 `import type`；不支持 enum、参数属性、JSX/TSX、装饰器或 tsconfig 路径别名。评测时不会执行 npm install 或任意构建步骤。请使用本地模块与 Node 内置模块，并在本地运行类型检查。模板包含 `contract.ts` 类型定义。

## GitHub 接入与证据

在工作台绑定仓库 URL、精确分支、编程语言和任务。按绑定后的安装引导操作：新项目安装完整包，已有项目安装工作流和终端辅助脚本；安装器自动放好 .github/workflows/hyper-lab.yml。然后 commit、push。CI 客户端由统一 v1 工作流提供；scripts/lab_eval.py 仅用于可选的本地提交。工作流会检出提交、上传源码、轮询远程任务、写入 Job Summary；即使评分失败，也会上传 JSON/Markdown 附件。退出码为 0（通过）、1（业务未通过）或 2（运行时／基础设施异常）。

GitHub OIDC 证明仓库身份、分支、工作流和提交声明。SHA-256 标识提交的文件字节。服务器**不会**独立拉取私有仓库，也不会声称已独立验证这些字节与 commit 一致；修改上传器的仓库所有者可以控制所提交的字节。这是教学反馈闭环，不是防篡改考试系统。

只有仓库 URL 不代表授权安装。仓库所有者需要自行安装一次生成的工作流；本版本不包含 OAuth/GitHub App 自动安装。学生仓库中不保存长期 GitHub token 或平台模型凭证。

官方参考：[GitHub OIDC](https://docs.github.com/en/actions/reference/security/oidc)、[Node TypeScript](https://nodejs.org/api/typescript.html)。


<!-- contract-notes-v1 -->
See project-root INTERFACE-CLARIFICATIONS.md for U1–U6 gateway, budget, field, catalog, quantity, state and money semantics. It clarifies compatibility without changing grading.
