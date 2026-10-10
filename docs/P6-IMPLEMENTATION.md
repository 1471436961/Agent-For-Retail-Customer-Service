# P6：串行计划、未决结果与人工转接

2026-10-10 完成。依据 ARCH-03/04、API-02/03、HUMAN-01–03、REPLAY-01 和 Client API 契约。

plan_changes 只接受明确的 kind/parameters，并按订单依赖安排地址→付款→一次性商品修改；互斥取消/退换需澄清，不默默删步骤。prepare_plan_step 只准备下一项，每项仍实际回顾并获得后续批准。实际 completed/accepted 才推进；failed/unknown/paused 阻止后续盲目执行。abandon_plan 只暂停未尝试步骤，get_service_status 保留各项真实回执与部分成功，不补偿/回滚/重做已完成步骤。

inspect_change_result 只用既有 profile/order API，每方案最多3次。旧读或当前状态并不能证明 unknown 请求身份，故保留原未知结果，禁止借新方案再次写入。容量错误不裁剪、拆写或重试。模型协议不合法仍保守停止并保留证据；不对失败输出伪造工具结果。

transfer_to_human 为 GENERIC，依据只允许明确要人、解释不支持后的请求升级、事实清楚但政策无规则三类。语义依据由模型判断，工具不假装通过枚举证明客户说了什么。路径使用可信 conversation_id，暂停业务写入后只尝试一次；accepted 由独立输出路径发出逐字 notice，unknown 停止自动操作但不宣称转接成功。toolkit 和 Agent 均阻止 accepted/unknown 后客户 API。失败仍暂停业务，不自动重试或擅自恢复。

固定 notice 来源：材料 meeting_transcript_04.vtt:1025。无附加承诺。

实际独立 unittest：102/102，通过新增11项计划/人工/恢复测试。同单依赖、部分成功、改目标暂停、三次只读上限、三种转接依据、未验证直接人工、路径编码、转接错误/未知、成功后零 API、多会话重置、完整新 toolkit/后端重放均有证据。重放测试按本项目全部工具轨迹重演，而非宣称上游只重演 WRITE 即等价。真实平台重放和模型语义仍待验证。
