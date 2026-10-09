> **教学环境说明：** 下文保留上游研究框架资料。实际命令以项目根目录 README 和 materials/CLASSROOM.md 为准。本项目没有 run_local_test 或 Client 接口申请工具；使用本地单元测试及远程 t1/p1/t2。当前 OpenAPI 中的退货／取消能力已经启用。

客户 API 契约

将工具实现为 ClientAPIToolKitBase 的子类，放在
workspace/tools.py中。运行时使用 ClientAPI构造工具集，可通过 self.client_api访问。使用 @is_tool 装饰的方法会公开给客户服务智能体，并可发起一次或多次客户 API 请求。

from urllib.parse import quote

from tau2.environment.toolkit import ToolType, is_tool
from tau2.hyper.client_api import ClientAPIToolKitBase


class Tools(ClientAPIToolKitBase):
    @is_tool(ToolType.READ)
    def get_order(self, order_id: str) -> dict:
        """Get an order by its customer-facing identifier."""
        response = self.client_api.request(
            "GET",
            f"/v1/orders/{quote(order_id, safe='')}",
        )
        response.raise_for_status()
        return response.body


REST 接口由 client_api/openapi.yaml定义。响应包含
status_code, body, headers，以及 elapsed_seconds。请显式检查状态与正文； raise_for_status() 可在需要将非 2xx 响应转为工具错误时使用。

会话上下文

self.client_api.context 包含当前活跃会话可信且只读的上下文。目前仅暴露 conversation_id。对于 OpenAPI 路径包含 {conversation_id}的操作，请使用此值；像其他路径标识符一样对它进行 URL 编码。不要要求客户或模型提供该值。

例如，实时转接工具寻址客户侧的会话资源：

class Tools(ClientAPIToolKitBase):
    @is_tool(ToolType.GENERIC)
    def transfer_to_human_agents(self, summary: str) -> dict:
        conversation_id = quote(
            self.client_api.context.conversation_id,
            safe="",
        )
        response = self.client_api.request(
            "POST",
            f"/v1/conversations/{conversation_id}/transfers",
            body={"summary": summary},
        )
        response.raise_for_status()
        return response.body


客户系统会将已认证会话的记录和路由上下文关联到转接。智能体只需提供问题摘要。成功响应表示实时转接已受理；自动重试并不安全，而且该会话之后的客户 API 操作会被拒绝。

载荷结构以 OpenAPI 文档为准。尤其是：


路径中的资源标识符必须进行 URL 编码。对于包含保留字符的标识符，这一点尤为重要——开头的 #
编码为 %23.

enum 的取值已经穷尽。不要编造或规范化出额外取值。

字段接受 JSON null 仅当其结构包含一个 null 分支。

每个操作都通过以下内容记录其是否更改状态、重复调用是否安全、是否允许自动重试、一致性行为及是否分页： x-api-* 字段。

错误响应使用共享的 APIError 封装。 400 表示请求不符合所记录的路径、查询参数或正文结构； 404
表示未找到引用的资源； 409 表示资源当前状态阻止操作； 422 表示结构合法的请求违反业务限制。状态码与错误码构成公开契约；消息是稳定摘要，不包含私有业务规则和实现细节。


读取操作可安全重复，并允许自动重试。写入操作不保证幂等性，因此在超时或传输结果不明确之后，绝不能自动重试写入。成功写入具有强一致性：同一会话中的后续读取会观察到已完成的更改。
标记为 x-api-pagination: none 的操作返回完整结果；不要发送文档中未记录的分页参数。

每个操作还会发布 x-api-request-body-max-bytes 和
x-api-response-body-max-bytes。大小按紧凑 JSON 序列化后的 UTF-8 字节数衡量。契约允许请求最大为 1,048,576 字节（查询参数与正文合计），响应正文最大为 4,194,304 字节。超大的请求返回 413 request_too_large；结果无法容纳的操作返回 502 response_too_large。请像其他已记录错误一样处理这两种响应，不要通过裁剪、拆分或重试写入来规避。

契约有意将两种接口分开：


面向智能体的工具名称、参数、返回类型和描述编写在 workspace/tools.py.

面向客户系统的 HTTP 方法、路径、请求结构、响应结构和错误编写在 client_api/openapi.yaml.


映射不必一一对应。一个智能体工具可以组合多个客户请求，多个智能体工具也可以共用一个客户操作。开发者本地的确定性辅助工具不代表客户资源，可以无需发起客户 API 请求而实现。

工具集实例可在同一会话的多次调用之间保存内存会话状态。评分时，会话中记录的工具调用会按顺序在全新的工具集实例和后端上重新执行，因此每个工具的行为必须是后端状态、其参数及同一会话此前调用的确定性函数。依赖其他因素——实际时间、随机性或会话外带入的状态——的行为可能在重新执行时发生偏差，导致该会话失败。

本地场景后端

run_local_test 为每个开发者编写的场景支持两种互斥后端：全新的确定性开发种子，或开发者编写、在隔离的候选沙箱中运行的 Python 模拟后端。后端只影响本地测试。已提交及保留评测会话始终使用真实客户 API 运行时。

参见 framework/scenario_contract.md 了解 client_api 场景字段、模拟工厂函数契约、生命周期、追踪、验证钩子及评分限制。