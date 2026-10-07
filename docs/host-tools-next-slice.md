# 普通聊天工具入口：下一片合同

本文件记录 R5 最小工具切片合同。固定宿主 AstrBot 4.28.2 / `3c7adafa1397e182d60b1016bf88759265113c8a`；SDK 为 1.6.0。固定源码隔离合同、合成行情源和受控模型 stub 分别验证工具链与追问续接，不代表真实模型调用或真实 IM 聊天验收。

## 最小路径

1. SDK 1.6 已引入版本化的入口集合，保留旧 `invocation_policy` 的等效映射和 1.0–1.5 包兼容。COMMAND、WEB_PUBLIC、LLM_TOOL 的声明分别校验，网页部署 opt-in、工具公共只读限制和入口证明继续生效；不能把市场策略单独切到 natural language 而使网页失效。旧 1.5 包不能声明新版字段。
2. 可信 Host 工具 publisher 从活跃 Registry 生成 `FunctionTool` 与闭合参数 schema，通过宿主 `Context.add_llm_tools` 注册。注册前拒绝他者同名；保存 owner、module epoch、exact object 和 revoked 句柄。
3. `call(ContextWrapper, **业务参数)` 从宿主 `context.context.event` 获取正式事件，内层 Host Context 必须是本 runtime 持有的 exact Context，通过入口绑定的内部 seal 形成 LLM_TOOL ingress。模型参数不得声明 actor、会话、权限、事件或模块 epoch。候选的随机 generation 是业务票据，不能用作模块 epoch。
4. 复用 `Core.invoke_tool` 和经 OutputService 校验的 `ToolOutput`；只向模型返回有界公开事实，包括状态、覆盖范围、来源与年龄。歧义继续由模块候选服务保存原查询上下文，不能由模型猜选物品。
5. 停用先撤销句柄并关闭 Core 准入，再按 exact object/owner 移除宿主工具。旧 ToolSet 仍可能持有对象，调用时必须拒绝；撤销列表不能替代执行围栏。部分注册失败撤销本批对象，清理失败保留 ownership/pending，不删除他者工具。

## 当前工具与用户确认

`ff14_market_query` 只接受 Q1 的 `query`、`server`/`dc`/`region`、`quality`（all/nq/hq）、`intent`（overview/min/listings）。它直接进入现有 MarketQueryHandler、Q1、S1；不向模型暴露 JSON/命令 wrapper。用户直接以 ID 查询时应明确说“物品ID 44091”或“item ID 44091”，或使用整条数字查价句（纯 ID、ID 后接“多少钱”/“什么价”，可前接“查价”/“查一下”/“查询”）；范围、数量及没有 ID 标签的“物品44091个”不是物品 ID 依据。

名称 query 必须在本条真实用户消息中有明确名称依据，只规范大小写和连续空白。当前候选的名称、ID，以及包含候选的确认、疑问、否定或多名回复不能改走 query 重算范围，较短名称子串也不能绕过；原 query 重试保留原批次和上下文，真正无关的新物品查询仍使旧批次失效。没有有效候选时，明确确认模板拒绝续接；裸完整名称与独立新查询无法可靠区分，此时只作为有本条名称依据的新查询，不恢复已失效上下文。

歧义返回至多六个候选，模型展示名称并追问唯一完整名称。支持“犎牛牛排”“犎牛牛排那个。”及闭合肯定模板“选/选择/就/就选 + 完整名称”，允许末尾句号或感叹号，大小写和连续空白规范化；旧“选择物品 <ID>”兼容。`ff14_market_select` 仍只接收 batch_id/generation/item_id。Runtime 在已验证 ingress 后把真实 event/text/owner 任务局部绑定到 exact 当前 handler，模块先独立确定唯一 ID 再核对模型参数，finally 清除绑定；原始歧义事件不能自动选择。疑问、否定、多名、同名、简称、数量及无有效批次均拒绝且不消费。序号没有可信展示顺序绑定，首版不支持“第一/第二个”，应追问完整名称；“刚才那个”、翻译名和模型自声明确认也不支持。原范围、品质、意图、配置 revision 和候选名称由模块保存；TTL、单次消费、新查询失效及迟到围栏仍生效。COMMAND、WEB_PUBLIC、LLM_TOOL 的候选命名空间隔离。

工具结果只读取 OutputService 的 ToolOutput。公开结构化事实来自 MarketExecution typed 数据，区分 NQ/HQ、Gil 每件、服务器 ID、指标缺值、至多五条挂牌、来源时间、获取时间、观察时间、年龄、缓存、partial 和 truncation。minimum 只限成功返回范围，挂牌是有限样本；不保证完整 World 覆盖或实时可买。缺值保持 null，来源未来时间如实标记，不补零或从展示文案反解价格。网页仍沿用原 query/coverage 白名单投影。

LLM_TOOL 只使用 PUBLIC cache，USER/AUTHORIZED cache 拒绝；不为模型或候选创建用户 principal，COMMAND 的既有默认 USER cache 保持。初始化出版失败先封闭准入并清理本批对象和原 Core；工具清理失败仍独立尝试 Core 关闭，Core 关闭成功后继续关闭 transport，保留失败 publisher exact owner 供 terminate 重试并报告 cleanup_pending。Core 自身尚未静止时继续保留它及其所需资源的既有安全清理语义。宿主 permission guard 保持逐调用有效，不通过 deactivate 改写全局用户工具偏好。

续接拒绝条件在有效、已消费、缺失或过期批次前统一执行：阿拉伯数字或常用中文数字的序数（含零、非法数值与超界序号）、闭合位置指代、裸“不是/不要/不选”和带名称否定均不能改为新 query。闭合指代包括“最后一个/最后那个/前一个/后一个/前者/后者/前面那个/后面那个/上一个/下一个/第几个/刚才那个/就那个/那个/这个/这一个/那一个”。序数按整句或完整 query 词匹配，不把候选上限六当作识别上限；模型 query 本身为这些闭合标记或以否定标记开头时，即使真实消息更长也拒绝。“第七个勇士徽章”“第七天堂牛排”“最后一个传说牛排”等完整新物品名称不因正文含序数词被拒。若名称本身恰好只有“第7个”等位置表达，无法可靠区分，应追问明确物品 ID，不猜序号或创建展示顺序绑定。这是上述有限形态的守卫，不宣称理解所有复杂续接语句。

已消费选择在价格请求执行期间，现有有界 pending 记录短暂保留 consumed 候选与真实确认事件，仅用于拒绝 query 绕过和重放，不重新呈现可选择批次。成功、异常与取消退出清除本 flight 的记录；新查询只失效相同 owner 的旧 generation，旧任务不能删除新批次。TTL 和模块关闭也清除记录。

## 固定宿主证据

- [Context.add_llm_tools](https://github.com/AstrBotDevs/AstrBot/blob/3c7adafa1397e182d60b1016bf88759265113c8a/astrbot/core/star/context.py#L679)：正式动态注册按名称覆盖，需要适配器提前检查归属。
- [FunctionTool 与 ToolSet](https://github.com/AstrBotDevs/AstrBot/blob/3c7adafa1397e182d60b1016bf88759265113c8a/astrbot/core/agent/tool.py#L40)：调用入口、schema、同名覆盖与列表行为。
- [权限包装及撤销](https://github.com/AstrBotDevs/AstrBot/blob/3c7adafa1397e182d60b1016bf88759265113c8a/astrbot/core/provider/func_tool_manager.py#L214)：正式 permission guard 逐调用读取宿主 event；async deactivate 会修改用户全局工具停用偏好，不能用作每次模块卸载的无副作用清理。
- [宿主 agent 上下文](https://github.com/AstrBotDevs/AstrBot/blob/3c7adafa1397e182d60b1016bf88759265113c8a/astrbot/core/astr_agent_context.py#L9)：可信包装的 context/event 来源。

## 验收与边界

隔离合同首先覆盖：旧策略等效；新三入口合法/冲突/未知值；伪造身份、COMMAND proof 换 TOOL、API key/asset token 换管理/工具权限拒绝；宿主 admin-only 工具对 member 拒绝；同名/部分注册失败/旧对象被替换；停用和迟到调用；私有输出拒绝；候选用户与会话隔离；显式命令 provider 调用计数为零。

本批已经实现两项 FF14 工具、Host 工具 ingress 和 SDK 1.6 三入口合同，并以固定宿主源码隔离执行、合成行情源和受控模型回放验证；完整真实宿主进程中的工具调用与真实 LLM 回复仍需分别验收。没有这两层真实证据，不称真实普通聊天闭环通过。不得新增无差别消息监听、独立模型会话或凭据服务。

## R5 市场回答事实投影 v2（离线实现）

市场工具的 `market.fact_projection_version=2` 只版本化 FF14 模块投影，SDK 的开放 `FactDocument` 合同不变。`market.minimums` 和 `market.listings` 仍是直接回答的报价行，原价格、品质、区域、World ID 和时间路径保留。同一行增加 `world_name`、`world_name_state`、`source` 和有限覆盖/非实时 `limitation`。名称只取执行时已有的已验证目录，要求准确 World ID、区域和原 scope 一致；缺目录或不一致为 null/unknown，不增加来源请求或猜测映射。

`coverage[].quotes[]` 的最低价三字段 `minimum`、`minimum_world_id`、`minimum_time`，仅在与同品质同区域的 canonical minimums 完全一致时改为 `minimum_ref="market.minimums[N]"`。消费者遇到该引用应分别读取目标行的 `price_per_unit`、`world_id`、`time`；没有引用时继续读取原三字段。非冠军区域报价以及最近成交、均价、成交量、缺项、失败/空区域、缓存和警告保留。仓内实际报价消费者使用 minimums；原命令/页面事实白名单不扩展。不得忽略版本而假定每条 coverage quote 都仍有旧三字段。

价格的 `time.age_seconds` 与 `source.fetched_age_seconds` 分开，缓存保留原获取时间；null 不补零，不设置新的“陈旧”阈值。`coverage_complete` 仅说明本次计划返回范围，不代表全部 World 上传或实时可买。`answer_guidance` 不重复报价，要求来源/限定紧邻价格，成功或可用 partial 已足够时直接回答，没有新用户范围时不补查服务器，不推断 HQ 用途、补货或购买建议。

候选 `user_confirmation` 现在主提示唯一完整名称或“完整名称那个”；原 `name_confirmation` 保留，ID 兼容提示迁移到 `id_confirmation`。确认只选择物品，保留原 scope、quality、intent 和配置 revisions；不改变事件、身份、TTL 或候选验证链。工具描述对“最低价/哪里最便宜”优先 min，overview 仍用于一般行情。错误恢复等待用户明确新指令，不诱导工具自行改 query 绕过确认。

SDK 1.6 错误事实仍保持精确 `{status, error: {code, message}}`（市场执行错误可保留 market），恢复指引附正式 `ErrorDetail.message`，不增加 error 子字段或修改 SDK/Core。

真实证据的原 query 显式 `cn/all/overview`；其名称 select 续接已通过。随后两个服务器 query 来自同一模型批次，不归因为收到错误后的自动重试。最终坏措辞结论来自真实验收报告。历史数值仅用于离线回放；受控模型 stub 和离线测试不能证明模型遵循描述或真实聊天验收通过。

## 2026-10-07 阶段验收与剩余待办

R5 工具链已在真实原生 WebChat 的一次两轮对话中通过：用户先问“牛排国服哪里最便宜？”，模型选择 query 的 `min` 意图，列出歧义候选并追问；用户以新的真实消息回复“犎牛牛排那个。”，模型调用 select，保留 `China/all/min` 与原配置上下文。两轮只有 query、select 各一次，没有额外服务器补查。取得的公开行情包含正确关联的 NQ/HQ 价格、可信服务器名称和各自来源时间；回复注明 Universalis、非缓存、数据年龄及非完整覆盖／非实时限制。这不代表模型永远遵循描述，也不代表全功能验收或正式发布。

最终模型回复仅部分通过，以下两项仍待后续单独授权修复；本次阶段提交只登记，不修改产品逻辑或工具描述：

- [ ] 消除“完整挂单列表”的超范围承诺。当前挂牌明细最多五条，是本次返回的有界样本，不是完整列表；后续验收需检查真实最终回答，不能只看工具输出正确。
- [ ] 收紧“国服最低价”的标题和表头。应紧邻价格说明是“本次返回数据中的最低挂牌”；本次正文已经说明覆盖有限、非全部 World、非实时可买，不把标题问题错误归因为整篇没有限定。

该真实样本显式传入 `region=cn`，省略地区读取 Core 默认值的路径未因此通过。global 四区、真实部分失败／缺值／缓存场景及其它平台仍按原未验记录保留。

复用的离线证据分开记录：此前最终全量为 1319 项运行、1303 通过、0 失败、2 项 Windows ACL 环境错误、14 项既有跳过；后续事实与描述修复的受影响回归为 111/111，通过最终冻结独审（0 Blocker、0 Important、1 项非阻塞测试增强建议）。两者不是同一轮全量，不能称“全部测试通过”。既有 8 项基线格式问题及 18 条 EOF 提示继续保留；本次 Git 交付不重跑全量或为消除提示改动冻结资源及第三方许可证。
