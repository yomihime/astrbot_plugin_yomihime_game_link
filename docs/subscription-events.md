# 持久订阅事件与公开事件合并

Core 负责采集调度、订阅权限、持久状态、事件去重和投递；模块负责规范化来源输入、采集公开数据、纯评估及纯展示组合。模块不自行启动后台轮询，也不直接操作 Host 消息客户端或 Core 数据库。

## 创建与采集合同

`SubscriptionRequest.creation_key` 是可选的语义创建键。Core 在当前受信任用户、完整 `ConversationRef`、订阅类型和规范化 `CollectionKey` 内原子创建或返回已有有效订阅；重复创建不清空评估状态或增加订阅修订。省略该键保留普通创建行为。取消后再创建获得新的订阅身份。

用户和投递目标由受信任命令上下文确定。查看、修改和取消均复核当前用户、完整路由和有效授权；不能从网页或模型参数构造命令身份。`SubscriptionView.evaluation_state` 仅在经过这些检查的当前会话列表中返回；创建、修订及评估器输入不借此公开原始状态。

规范化的公开 `CollectionKey` 可在多个会话间共享采集；各订阅的评估状态与投递身份仍独立。私有范围与授权不能通过公开事件合并扩散。

调度器读取当前配置中的间隔，下次到期采集后按新值安排后续任务，不立即提前已登记的到期时间。当前间隔枚举为 **60 / 300 / 900 / 3600 / 21600 / 43200 / 86400 秒**。来源的每小时预算独立于采集间隔：滚动 60 分钟按实际外部请求计数，缓存命中不计数，重试计数；请求预算和冷却持久保存。`SourceHttpError.next_available_at` 是带时区的 UTC 时间，调度器据此延后任务并释放租约，不把限流当成失败观测提交。

`SubscriptionDescriptor.enabled_config_key` 可指向模块声明的普通布尔配置，其默认值必须为 `true`。该开关控制本模块订阅采集及投递，不授予模块启用或内置模块的可信权限。暂停保留原订阅，查看和取消仍可用；恢复继续使用持久状态。已有显式关闭或非法配置不会因初始化被覆盖为开启。

## 评估与持久事件

`SubscriptionEvaluator.evaluate` 是同步纯函数，不执行网络、存储或模型调用。`EvaluationDecision.events` 可返回最多 3 个独立 `EvaluationEvent`，每个携带业务 `event_key`、版本和确定性 `display_data`；不能同时使用旧版单事件字段。旧版单事件合同继续有效。

Core 将评估状态与事件原子提交到 outbox，再执行展示和投递。重试、重启或多次观察同一事件复用持久事件身份，不依赖内存去重。

Dota 2 的具体策略是：首次完整成功采样只建立基线；后续未就绪比赛先持久待处理，恢复窗口为 **24 小时**。每次完整恢复采样仅保留时间最新的 **3 场**候选（包含已有待处理比赛），较旧积压写入已见集合并累计省略数量，供订阅列表显示。每场单独建事件；不按最高比赛编号推断全部历史，也不承诺对未来任意延迟发现的数据实现全局恰好三场。这是模块恢复策略，不是所有 Core 订阅的固定保留期限。

## 同路由公开事件合并

模块可在事件中附加 `PublicEventContribution(composer_id, contribution_key, payload)`，并在 `ModuleHandlers.event_composers` 注册同步纯 `PublicEventComposer`。组合器接收贡献集合，返回 `ComposedPublicEvent(display_data, commentary=None)`。Core 不解释比赛编号、玩家身份或业务事实。

合并仅适用于无私有授权的 PUBLIC 数据。合并身份包含模块、业务事件键及版本、组合器，以及完整 `ConversationRef`：适配器、群组或私聊类型、会话编号和投递路由。不同路由独立处理，同一公开采集结果不会把消息投递到其他会话。

首个贡献开启 **30 秒**收集窗口，窗口独立于日历 digest，不改变原有每日摘要规则。窗口最多保留 100 个订阅成员，原始每订阅事件、幂等键和投递关系继续保留。领取发送任务时封存成员集合（sealed）。已封存或达到成员上限后的晚到贡献持久记录 `sealed_membership` 或 `member_limit` 抑制原因，原事件置为 CANCELLED，不假记为 SENT，也不再为同一封存事件追加重复通知。诊断应同时查看抑制记录和原始成员事件。

30 秒只能覆盖一次相近的采集波次；任意延迟入库或不同轮询时刻发现的贡献不保证全部进入同一条消息。已经开始发送的消息不能追加成员。

组合前、模型或渲染之后及最终 Host 发送前，Core 复核成员订阅、修订、权限、完整路由和开关。已取消或失效的成员被移除，并据剩余成员重新组合；没有有效成员时不发送。

## 可选无状态短评

确定性事件或组合结果可附加 `CommentaryRequest`。评估器只声明请求，Core 在事件持久化之后、发送之前调用 Host 模型适配器。请求有模板身份、版本、超时与输出上限；当前通用上限为 10 秒、1000 字符，Dota 默认使用 8 秒、400 字符。

Host 适配器选用已配置模型，以新临时 session、空历史、无工具和 `persist=False` 调用 `text_chat`，不读取群组会话、私人记忆或凭据。模型调用按事件、模板及成员集合保存一次尝试标记；进程中断后不重复消耗已开始的尝试。异常、超时、空响应及超长响应均回退到确定性事实消息，短评失败不阻断战报。

## UNKNOWN 与恢复边界

只有确认送达才能记为 SENT。Host 已开始发送后出现超时、连接中断或无法确认持久提交结果时，事件为 **UNKNOWN**，含义是“可能已经送达，当前无法确认”；不会自动当作失败重发。

冷启动会恢复上一进程遗留的全部领取与发送中记录：尚未开始 Host 发送的 CLAIMED 任务可回到待处理；SENDING 任务转为 UNKNOWN，即使原租约尚未到期。运行中的普通租约恢复仍按到期时间处理。原始成员事件保留对应状态与证据；需要显式核对投递记录才能处理 UNKNOWN。持久幂等和恢复机制不提供跨外部消息平台的端到端恰好一次保证。

## 实现与行为参考

主要入口为 [SDK 订阅类型](../yomihime_game_link_sdk/subscriptions.py)、[订阅服务](../services/subscriptions.py)、[调度器](../services/scheduler.py)、[投递服务](../services/delivery.py)、[SQLite 持久实现](../infrastructure/sqlite/repositories_subscriptions.py)及 [Host 短评适配器](../adapters/astrbot/subscription_commentary.py)。

| 固定参考版本与路径 | 采用方式 | Core 落点 |
| --- | --- | --- |
| [`mml-hrb/astrbot-plugin-dota2matches@703a5dcf2d501a2a232fd44e4eaecf395ed293a6/main.py`](https://github.com/mml-hrb/astrbot-plugin-dota2matches/blob/703a5dcf2d501a2a232fd44e4eaecf395ed293a6/main.py) | 共享轮询、延迟比赛跟踪、同目标同场合并及可选短评的行为参考；独立实现 | 公开 CollectionKey 调度、持久事件窗口、投递及短评适配器 |
| [`同版本 dota_store.py`](https://github.com/mml-hrb/astrbot-plugin-dota2matches/blob/703a5dcf2d501a2a232fd44e4eaecf395ed293a6/dota_store.py) | 订阅与重启后保留状态的行为参考；独立实现 | 订阅创建键、评估状态和 outbox |

未直接复制参考项目代码或实质性文本。参考项目为 [MIT License](https://github.com/mml-hrb/astrbot-plugin-dota2matches/blob/703a5dcf2d501a2a232fd44e4eaecf395ed293a6/LICENSE)，`Copyright (c) 2026 马铭亮`；如后续直接复用，应保留原版权声明及完整 MIT 许可文本。
