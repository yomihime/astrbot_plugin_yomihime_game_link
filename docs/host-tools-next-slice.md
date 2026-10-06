# 普通聊天工具入口：下一片合同

本文件是 R5 方案，不是已实现功能。固定宿主 AstrBot 4.28.2 / `3c7adafa1397e182d60b1016bf88759265113c8a`。本批没有真实模型调用或真实聊天验收。

## 最小路径

1. 在后续 SDK 版本引入版本化的入口集合，保留旧 `invocation_policy` 的等效映射。COMMAND、WEB_PUBLIC、LLM_TOOL 的声明分别校验，网页部署 opt-in、工具公共只读限制和入口证明继续生效；不能把市场策略单独切到 natural language 而使网页失效。本次 1.5 页面/命令合同不隐式包含此项。
2. 可信 Host 工具 publisher 从活跃 Registry 生成 `FunctionTool` 与闭合参数 schema，通过宿主 `Context.add_llm_tools` 注册。注册前拒绝他者同名；保存 owner、module epoch、exact object 和 revoked 句柄。
3. `call(ContextWrapper, **业务参数)` 从宿主 `context.context.event` 获取正式事件，通过入口绑定的内部 seal 形成 LLM_TOOL ingress。模型参数不得声明 actor、会话、权限、事件或 generation。
4. 复用 `Core.invoke_tool` 和经 OutputService 校验的 `ToolOutput`；只向模型返回有界公开事实，包括状态、覆盖范围、来源与年龄。歧义继续由模块候选服务保存原查询上下文，不能由模型猜选物品。
5. 停用先撤销句柄并关闭 Core 准入，再按 exact object/owner 移除宿主工具。旧 ToolSet 仍可能持有对象，调用时必须拒绝；撤销列表不能替代执行围栏。部分注册失败撤销本批对象，清理失败保留 ownership/pending，不删除他者工具。

## 固定宿主证据

- [Context.add_llm_tools](https://github.com/AstrBotDevs/AstrBot/blob/3c7adafa1397e182d60b1016bf88759265113c8a/astrbot/core/star/context.py#L679)：正式动态注册按名称覆盖，需要适配器提前检查归属。
- [FunctionTool 与 ToolSet](https://github.com/AstrBotDevs/AstrBot/blob/3c7adafa1397e182d60b1016bf88759265113c8a/astrbot/core/agent/tool.py#L40)：调用入口、schema、同名覆盖与列表行为。
- [权限包装及撤销](https://github.com/AstrBotDevs/AstrBot/blob/3c7adafa1397e182d60b1016bf88759265113c8a/astrbot/core/provider/func_tool_manager.py#L214)：正式 permission guard 逐调用读取宿主 event；async deactivate 会修改用户全局工具停用偏好，不能用作每次模块卸载的无副作用清理。
- [宿主 agent 上下文](https://github.com/AstrBotDevs/AstrBot/blob/3c7adafa1397e182d60b1016bf88759265113c8a/astrbot/core/astr_agent_context.py#L9)：可信包装的 context/event 来源。

## 验收与边界

隔离合同首先覆盖：旧策略等效；新三入口合法/冲突/未知值；伪造身份、COMMAND proof 换 TOOL、API key/asset token 换管理/工具权限拒绝；宿主 admin-only 工具对 member 拒绝；同名/部分注册失败/旧对象被替换；停用和迟到调用；私有输出拒绝；候选用户与会话隔离；显式命令 provider 调用计数为零。

随后才进行固定宿主真实工具调用和真实 LLM 回复，分开报告。没有这两层证据，不称普通聊天闭环通过。不得新增无差别消息监听、独立模型会话或凭据服务。FF14 工具当前为空，Host 工具 ingress 与三入口合同尚需下一片实现。
