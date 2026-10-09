# 工程结构与开发方式

## Python

```text
agent/
  agent.py                  # 平台工厂入口
  tools.py                  # 平台工具发现入口
  agent.json                # 协议、语言、场景
  support_agent/            # 可安装的本地 Python package
    application.py          # 决定下一轮回答或工具调用
    state.py                # 每个会话独立的 JSON 状态
    domain/customer.py      # 不依赖运行环境的业务规则
    adapters/customer_api.py    # 业务 API 传输与错误处理
    adapters/customer_tools.py  # 工具声明与注册
tests/test_customer.py      # 无网络、无模型的单元测试
pyproject.toml             # 本地 package 元数据
```

## TypeScript

`agent.ts` 和 `tools.ts` 是同样的薄入口。`agent/support-agent/` 按 `application.ts`、`state.ts`、`domain/customer.ts`、`adapters/customer-api.ts` 和 `adapters/customer-tools.ts` 划分模块；`contract.ts` 提供协议类型。项目根目录的 `package.json` 提供 `npm test`，`tests/customer.test.ts` 验证完整的用户消息 → 工具调用 → API 结果 → 回答流程。

## 一个功能应该如何增加

1. 从业务材料提取规则，先在 `domain/` 增加纯函数和本地测试。
2. 在 `adapters/` 添加业务 API 适配器，检查状态码；在工具模块声明只读/写入属性。
3. 在 `application` 中连接对话逻辑。真正的会话数据保存在 state 中，不放在模块全局变量；根据调用 ID 对应工具结果。
4. 写入操作前实现业务规则与用户确认；不要盲目重试会造成重复退款等副作用的操作。
5. 运行本地测试，然后提交整个 `agent/`。先做 t1 接入，再做公开练习或所选正式案例。查看实际结果与期望结果的差异。

## 依赖与运行边界

- Python 需要 3.12+；纯业务规则测试只使用标准库。`tau2` 由远程环境提供；本地安装这个 package 不会自动安装评测框架。
- TypeScript 需要 Node 24，使用原生类型擦除和显式 `.ts` 相对导入。这里的多模块目录是真正运行的本地模块，不依赖构建别名或 npm workspace 链接。
- `pyproject.toml` / `package.json` 是开发配置，不是授权远程安装依赖。远程不会执行 `pip install`、`npm install` 或项目脚本。不得上传 `node_modules` 或虚拟环境。
- 仅递归提交 `agent/`，最多 128 文件、单文件 256 KiB、合计 2 MiB。放在它之外的共享代码不会运行；应将其移入该目录。
- 示例无模型调用，也没有真实客户凭证。本地测试使用合成数据；真正的 t1 通过环境 API 查询，而不是返回写死的客户信息。
- README 和本文件随所选中文/英文版本切换。代码标识符、测试断言及业务 API 不随阅读语言变化。
