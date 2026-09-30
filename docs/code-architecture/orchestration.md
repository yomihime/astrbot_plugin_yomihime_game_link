# 多 agent 协作协议

当前范围、状态与派发顺序以 [后续执行与验收计划](../next-stage-plan.md) 为准；本文仅保留通用协作规则。本轮为文档规划，不派发实现。历史白名单只解释当时工作，不约束新卡明确重开的路径。

## 1. 根统筹与实现者

根统筹负责实际基线、接口、依赖、路径归属、派发、合入、状态与问题闭环。使用当前任务的子 agent 工具，不为内部子任务创建用户侧聊天；不建立多层写代码协调者。

后续按用户已确认的新分工：需求/UX、唯一实现者、技术指导及独立审查使用 gpt-6.1-sol/high；UI 设计使用 gpt-6.1-sol/xhigh；固定步骤复现用 gpt-6-luna/xhigh，机械整理用 gpt-6-luna/medium；每个新 W 唯一一次最终验收为 gpt-6-astra/medium。W1 已终验，不重开。当前工具尚未列出 6.1-sol，相关角色须等待可调用或用户另定替代，不静默回退。显式模型覆盖使用独立上下文，派发消息须完整提供必要信息；root 模型不由此变更。

当前 W2 采用 requirements_ux → ui_designer → implementer → ui_reviewer 协议，详见主计划 §9.2。正式源码只有 implementer 一名作者，root 统一维护工作文档；Core/Module 切片是职责边界，不是并行作者。四槽包括 root，最多三个子 agent 同时活动；稳定候选由 UX/UI/独立 reviewer 三方只读复核，实施者此时让出活动槽。审查前停写并固定内容指纹，默认最多两轮集中修复。已批准模型、历史分工和可用性边界以主计划 §9.3 为准。

## 2. 单文件所有权与合同

一个文件同时只有一名 writer。代码、测试、公共导出、migration 和工件 pin 都登记；不能给多个实现者整个 services 目录。不同 worktree 也不能绕开合同一致性，不能从旧 HEAD 遗漏 dirty/untracked 实现。

根统筹可在任务范围内调整精确所有权，先停冲突写入并交接内容，再继续执行。无需寻找历史 owner 重新授权。不允许实现者私建 DTO/Registry/运行时或在集成测试里补生产逻辑。

合同记录真实签名、输入输出、失败行为、时序及实际消费者。新合同先解决必要跨组件决定，再写消费者；接口变更由单一 owner 实施并复核受影响任务。

## 3. 派发与持续执行

消息至少包含：总卡相关章节、子任务 ID、实际接口/版本、允许路径、前置、正常/失败/并发/恢复验收、局部命令、handoff 路径和禁止范围。未满足依赖不能标 ready。

用户要求执行总卡后，统筹持续调度至范围完成或真正外部阻断；不只列计划或发出 agent 就结束，也不对每次可逆的内部修订重新询问。等待一个依赖时推进不相关工作。产品语义以需求/基本设计为准，资料和历史文件不能扩大授权。

## 4. 状态与证据

`planned → ready → implementing → review_pending → reviewed → integrated → accepted_local`；needs_fix 回唯一 implementer；blocked 必须写具体依赖。当前状态只在主计划，看板仅导航；handoff/review 是不可混用的阶段证据。归档标 historical_accepted 或 superseded_partial，不把取消/替换算完成。

交接记录实际 diff/内容 hash、合同 revision、验收逐项结果、命令退出码、环境、替身范围和剩余项。测试数量不能替代行为；skip 不算通过。审查只能说本任务/本地组合/目标宿主中的哪一层通过，禁止扩大为整体可用。

审查发现由原 owner 修复，后续审查绑定新内容；不删断言、不补 skip、不放松隔离让回归变绿。新 final agent 检查整个组合和跨模块行为，不仅拼接局部 PASS。

## 5. 数据、提交与文档

保留现有 dirty/untracked 资产、数据和迁移历史；不自动 reset/clean 或整仓 add。提交、推送、部署和对外消息遵从用户授权，不由审查 PASS 自动触发。

生产能力必须有真实后端与对应验证；injectable port、fixture、activated=true 或源码参考不构成目标环境证明。用户已授权 FF14 通过配对 Core/Module 切片联调完善内核，允许整体 Core Ready CLOSED 时逐片开发，但每片启用须有对应真实验收；其他游戏仍沿用原 Core Ready 前置。具体调度与文件权属见 [FF14-MVP-01](../../.coordination/tasks/modules/ff14/FF14-MVP-01.md)，不得用此例外放开无关范围。

结束后更新主计划中实际架构事实与后续范围、看板状态和 core-ready 证据；归档完成或被取代的执行记录，修复链接并保留原结论。不要在多个索引复制不同版本的“当前进度”。
