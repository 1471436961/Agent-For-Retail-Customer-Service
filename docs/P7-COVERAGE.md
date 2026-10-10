# P7：离线能力覆盖与在线边界

全部134条公开需求已逐项人工审阅并映射到 CV 能力，见 [P7-REQUIREMENTS.json](P7-REQUIREMENTS.json)。映射包括源文件与章节 hash/行号，**不包含客户、订单、商品、金额答案，也不进入 agent/**。它证明需求整理完整，不证明134个公开案例已执行或通过。该目录含客户不对 Agent 透露的偏好，运行时仍须澄清，不能读取目录取得答案。

下表的“离线通过”仅指结构、工具/编排边界。用户话语是否被理解、确认是否真正成立、解释是否全面仍需真实模型验证。全部真实公开业务案例状态为 not_run；范围仍是 t1/case_ids:null。

| 能力 | 实际离线证据（tests/ 中的方法） | 结果与边界 |
|---|---|---|
| CV-01 协议 | test_agent_protocol；test_write_batches_and_wrong_executor_are_rejected；test_transfer_cannot_share_a_batch_with_other_actions | 通过；单写/独立转接、关联 ID、历史恢复、边界上限 |
| CV-02 契约 | test_all_fourteen_response_models_match_expanded_normal_contract；test_read_models_match_reviewed_openapi_shapes；容量与路径编码测试 | 通过；14响应模型全量结构比对，异常不放宽正常schema |
| CV-03 身份 | test_full_name_model_host_loop_receives_unparsed_multi_turn_context；test_incomplete_mixed_or_id_only_selectors_never_contact_backend | 工具通过；真实姓名/邮箱语义未验证 |
| CV-04 权限 | test_claim_mismatch_never_reads_any_profile；test_actual_owner_is_checked_even_if_profile_list_is_wrong；test_untrusted_catalog_text_does_not_grant_identity_or_other_order_access | 通过；零越权读取/写入 |
| CV-05 事实读取 | test_owned_order_preserves_repetition_shipping_and_payment_direction；test_order_ids_are_full_list_without_invented_dates | 通过；最近期望和物流解释由模型澄清，真实回复未验证 |
| CV-06 商品 | test_comparison_numeric_units_preservation_ties_and_missing；test_duplicates_and_different_targets_are_not_deduplicated；test_specific_duplicate_instance_is_unaddressable | 工具通过；偏好优先级/单位同义换算语义未验证 |
| CV-07 确认 | test_pause_revise_question_and_conditional_decisions_do_not_write；test_replacement_proposal_cannot_use_old_review；test_multi_turn_revised_complete_list_replaces_confirmation_and_scope | 编排通过；语义决定为 mock，不能机械证明真实同意 |
| CV-08 状态 | test_all_ineligible_states_and_foreign_order；test_changed_backend_facts_or_status_require_new_confirmation；test_one_time_requests_cannot_append_after_success | 通过；未知状态拒绝正常解析及相关写入 |
| CV-09 金额 | test_calculator_rounding_sign_order_and_invalid_numbers；test_cross_order_positive_negative_net_and_actual_customer_reply；test_wrong_financial_or_replacement_receipt_remains_unknown | 通过；合成原价/现价/净額与正式算法一致，实际支付到账未验证 |
| CV-10 地址 | test_actual_recap_then_new_user_allows_exact_default_address_once；test_order_address_does_not_change_default；test_complete_address_and_no_extra_fields | 通过；实际固定完整 recap，修改范围独立 |
| CV-11 付款 | test_whole_payment_balance_and_positive_difference_balance；test_four_complete_business_slices_and_repeat_call；test_each_kind_rejection_unknown_and_capacity_never_retries | 通过；decline不虚报，保持原后端记录 |
| CV-12 取消 | test_invalid_reason_or_partial_scope_does_not_expand_to_whole_order；test_cancel_whole_order_and_keep_individual_original_refunds | 工具通过；同义理由/有条件批准与实际退款时限沟通待真实模型 |
| CV-13 商品修改 | test_submission_is_one_full_list_with_preserved_duplicate_targets；test_price_availability_payment_or_target_changes_need_new_review | 通过；完整性问题实际展示，后续用户事件必需 |
| CV-14 退货 | test_return_destination_and_original_prices；test_four_complete_business_slices_and_repeat_call；test_one_time_requests_cannot_append_after_success | 通过；原支付/首次验证时保存gift、accepted非到账 |
| CV-15 换货 | test_negative_difference_keeps_direction_and_other_saved_method；test_cross_product_unavailable_unchanged_and_preserved_options；test_duplicates_and_different_targets_are_not_deduplicated | 通过；目标与单一saved去向重新核实 |
| CV-16 复合计划 | test_same_order_dependencies_execute_serially_using_current_facts；test_partial_success_failed_second_step_does_not_redo_first；test_cross_order_positive_negative_net_and_actual_customer_reply | 通过；单步确认、部分成功与跨单各自回执 |
| CV-17 异常 | test_rejection_timeout_malformed_receipt_and_no_repeated_write；test_each_kind_rejection_unknown_and_capacity_never_retries；test_unknown_write_observations_never_clear_unknown_or_allow_retry | 通过；不重写、不拆写，最多3次只读观察 |
| CV-18 重放/隔离 | test_full_tool_replay_rebuilds_identity_proposals_plans_and_receipts；test_context_reset_discards_all_service_state_and_requires_new_identity；test_json_resume_restores_display_evidence | 离线通过；正式宿主全工具重放仍未验证 |
| CV-19 人工/限制 | test_all_transfer_bases_before_identity_and_invalid_basis_zero_requests；test_transfers_use_trusted_context_exact_notice_and_terminal_automation；test_failed_or_unknown_handoff_never_emits_success_or_resumes_writes | 工具通过；真实模型对三类语义依据判断未验证 |
| CV-20 沟通 | test_actual_cancellation_reply_preserves_original_refund_source_and_timing；test_cross_order_positive_negative_net_and_actual_customer_reply；test_transfers_use_trusted_context_exact_notice_and_terminal_automation | 真实工具回执生成必要事实块与固定notice；完整模型沟通未验证 |
| CV-21 语义泛化 | test_arbitrary_language_is_forwarded_without_parsing；test_full_name_model_host_loop_receives_unparsed_multi_turn_context；中文改口轨迹 | 真实gateway路径、原文转交通过；理解能力及误判率未验证 |
| CV-22 包/范围 | test_final_submission_package_scope_and_134_requirement_index；test_submission_audit_rejects_embedded_credential_without_reading_it；scripts/check-submission.py | 通过；agent/大小/依赖/源码hash及固定范围，在线报告另计 |

模型与后端均为合成，三个维度观察包括最终记录、正式形状 API 轨迹、模拟回复中的必要事实。Mock 回复检验消息链路，不能证明 provider 会生成这些事实。SDK探针独立使用本地真实 tau2。

最新在线证据：恢复推送的 P7 t1 run `38023329509` 为0/1，首次模型决策失败并显示 gateway_failure，没有工具调用；不能断言实际发生身份工具失败或先读详情。本次本地修复增加允许名单内的异常类型/合法整数 HTTP 状态，仍不输出异常原文或凭证。另新增真实 SDK 消息转换边界测试，详见 [全面复核](COMPREHENSIVE-REVIEW.md)。这些本地修复尚未提交、推送或重新在线评测；模型配置、预算和费用由平台负责。

工程限制：事实快照保守比较，任何变化可能要求重准备；每步单独回顾/批准；回顾事实块为结构化JSON并使用英文确认问句；语义与本地化沟通由模型继续完善。未知写入不会仅凭读记录解除，也不提供跨进程 exactly-once；没有真实原子事务或虚构异步状态接口。
