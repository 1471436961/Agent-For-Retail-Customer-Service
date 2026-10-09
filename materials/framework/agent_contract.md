智能体契约

你的 agent.py 文件必须导出一个工厂函数，供评测器调用以构建内循环智能体。


工厂函数

from tau2.hyper.agent_context import get_agent_context

def create_agent():
    """
    Returns:
        An agent instance with get_init_state() and generate_next_message()
    """
    context = get_agent_context()
    ...  # Any agent logic goes here.


get_agent_context() 在工厂函数运行时可用，返回四项运行时能力：


action_interface：完整操作目录、其元数据，以及按规范名称选择操作的辅助工具。目录不强制任何分组或排序。

resources：套件根目录、相对文件清单，以及用于解析或读取任何已提供资料的辅助工具。

model_gateway：访问允许的模型及其强制限制。

runtime_config：领域名称等运行时元数据。


这些输入是能力和资源，并不要求特定组织方式。工厂函数自行决定如何使用以及是否使用它们。

模型网关是生成的智能体代码受支持的推理路径。每次推理调用都明确指定模型。如果允许列表中包含该模型的多个配置，请传入足够的受限参数以唯一确定其中一个；网关会拒绝含糊、不被允许或相互冲突的请求。如果返回的智能体或其组件需要在以下操作之后进行推理，请在其中保留网关对象： create_agent() 返回。

值为 {"one_of": [...]} 的限制是留给你选择的选项，而非固定设置。例如：

{"model": "gpt-5.6-sol", "constraints": {"reasoning_effort": {"one_of": ["high", "medium"]}}}


允许调用传入 reasoning_effort="high" 或 "medium" ，并拒绝其他任何值。调用省略固定限制时，系统会自动补充；但可选限制没有默认值：省略它会报错，因此每次调用都必须传入一个选项。各次调用可以选择不同的值。


智能体接口

你的智能体必须实现两个方法：

get_init_state(message_history=None) -> state

在会话开始时调用一次。返回一个不透明状态对象，该对象会贯穿每一轮交互。

generate_next_message(message, state) -> (AssistantMessage, state)

每一轮调用。接收 UserMessage （来自客户的文本）或 MultiToolMessage （智能体上一轮工具调用的结果）。返回智能体响应与更新后的状态。

该 AssistantMessage 可以包含：


文本内容 ——发送给客户的消息

工具调用 ——要调用的一个或多个工具


智能体应选择以文本回复或进行工具调用，不能同时执行两者。

可选钩子

运行时还识别三个可选方法；智能体未提供这些方法时，使用框架默认实现：


is_stop(message) -> bool ——助手消息是否结束会话。默认：智能体从不主动发出停止信号；会话由客户侧结束或达到轮次上限时结束。

set_seed(seed: int) ——为内部随机性设置种子。默认：无操作。

stop(message, state) ——会话结束后的清理。默认：无操作。