# 命令展示设计

## 范围与状态

本设计将人类命令展示与 LLM 事实输出分开，同时保留现有 Core、模块、presentation、Host 边界。PR 起始交付为文档和 `examples/command_cards/` 离线原型；后续生产接入按[实施计划](implementation-plan.md)推进，已完成与未验范围以[当前检查点](README.md)为准。

SDK `0.1.0a6`、现有结果契约、授权边界与生命周期不在此阶段升级。样图只证明排版方向，不能证明查询、资源注册、权限或消息投递可用。

## 一 管理页先修紧贴位置

采用紧凑节奏：提示与关联动作之间约 8px，独立内容组之间约 12px。优先在明确的容器边界加间距，不给所有按钮套全局 margin，也不借此调整导航或页面职责。

静态代码定位到三处需核验的位置：

1. `module-management.ts:79–82` 的提示 Alert 与后续模块操作按钮
2. `main.ts:97` 的 `.module-dashboard` 内，模块区域与配置维护按钮
3. `main.ts:97–98` 的 dashboard 与维护区域边界

这些是源码审查线索，不是实际黏连截图的复现结论。[源码定位：模块管理][ui-module]、[主页面][ui-main]

设置页已有约 8–12px 的间距处理。实施前须结合实际宿主页面确认问题位置；不能把尚未看到的截图解释成全页都缺间距。

必须保持：

- 滚动位置、键盘焦点、未提交草稿及已有切换行为
- CAS 注册版本校验、重复点击保护、旧请求结果隔离
- 模块启停、解除挂载、清理等待、重开页面的生命周期边界
- 亮暗主题、窄屏换行、按钮禁用态及可访问性

## 二 同一事实 两种输出

### 人类命令

普通功能标题使用 `游戏连结 · 模块展示名 · 功能`；总帮助标题使用完整的 `如月怜的游戏连结`。模块展示名来自已注册声明，不从内部 module ID 猜测。

正文先给结果，再给必要限制与真实可执行的下一步。可以有一句短开场和有限随机短句池，但不改变事实、错误状态、指标或命令；同一帮助事件的重绘、重试准备与文本降级保持同一句话。

不得虚构“已排队”“稍后自动重试”“马上会发给你”等状态。只有真实工作已进入相应状态时才能这样描述。错误用安全、可理解的提示；不透传异常堆栈、源站错误正文或凭据内容。

沿用已有“总帮助 → 模块帮助 → 功能帮助”层级，不另造命令入口体系。[帮助分派现状][help-bridge]

### LLM 工具事实

facts 不包含品牌标题、人格设定、装饰短句、随机选择、图片或需要 OCR 才能读取的数据。字段保持紧凑、稳定、可计算：

- 数值是语义数值，单位单独声明；不要传带逗号的数字字符串
- 未知值用 `null`，并说明覆盖范围或缺失原因；未知不等于 0
- 标明查询地区、指标、数据时间或适用范围，避免把局部结果说成完整结果
- 来源保留可核验的非敏感标识或链接；下一步只有在确实适用时出现
- 人类展示中的整数、百分位格式化不回写原始 facts

复用模块内的事实构造逻辑，让命令和工具从同一领域结果生成输出。FF14 领域知识、目录分类与跨 tier 聚合留在 FF14 模块；Core/SDK 不增加游戏专用规则。

### 必须遵守的现有契约

Core 的错误 facts 顶层只允许 `status/error` 或 `status/error/supplement`。`error` 必须且只能含 `code`、`message`，并与 `ErrorDetail` 完全一致；补充信息放进 `supplement`，不能顺手加顶层 `source` 或 `next_action`。[错误校验][error-contract]

继续遵守 512 节点、深度 16、单字符串 4096 的公共 facts 上限；节点包含键和值，不能只数业务行数。原有字节预算也保持。完整 20 行样例及未来增长都需要真实计数测试，优先提取公共字段、减少重复结构，不静默删事实或调大契约上限。[预算实现][fact-budget]

Host 当前只序列化 `ToolOutput.facts.facts`，不会自动带上 `FactDocument.sources`。需要交给 LLM 的来源必须也存在实际 facts 中；错误来源则遵循 `supplement` 结构。[Host 序列化][tool-facts]

Core 的安全通用英文错误可以经过明确映射变成中文；不能以“改善文案”为理由把原始 `exception.message` 交给用户或 LLM。

## 三 统一图片语言

### 视觉约束

- 使用 Python + Pillow 绘制最终位图，不用 AI 生图或浏览器截图代替渲染
- 默认使用暖桃色纸感版式；淡紫、薄荷可作为可选预设，不做不受控随机配色
- 字号、留白、圆角、标题、来源脚注与警告层级由通用 presentation 统一
- 所有命令图片共享此风格；各功能保留适合信息内容的版式
- 物品卡尚无已确认版式，不能把市场卡或现有物品展示直接宣布为最终设计
- 图片与文本降级使用同一事实来源，不能因视觉简化改变查询范围

现有 [市场、帮助与 Logs 原型](../../examples/command_cards/README.md) 是可运行的布局参照，不是插件里的新功能。不得导入真实玩家截图、个人成绩或私人环境路径充当公开测试夹具。

原型对极端长名称仍可能缩字或省略，这只是已披露的演示限制。生产接入需完整换行或发送前完整文本降级；原型长名测试不等于生产无信息丢失验收。

### 总帮助

先展示所有 active 且实际可用模块的展示名与入口，再从这个集合随机选择一项，写成“从 XX 开始吧”。推荐项只是附加引导，不能代替完整模块列表。

同一次帮助事件固定选择；新事件可以重新选择。零模块时显示简洁空态与确实可用的下一步，不抽取空集合、不推荐禁用模块、不编造安装或恢复结果。原型只有一个模块，不能用它证明多模块布局已经完成。

### 模块图标与资产

每个模块拥有自己的可选 logo；通用样式不依赖 FF14。默认接受已审查的模块内单色 SVG，颜色由 `currentColor` 表达，缺失或处理失败时保留文字安全降级。

FF14 原型图标参考经典陨石意象进行绘制；它不是官方矢量原件，也不构成官方授权声明。保留参考来源、作者说明与适用许可，生产打包前核验资源和字体的再分发条件。

不要将正则过滤任意 SVG 当作安全边界。生产路径只处理可信模块资产白名单，限制字节数、尺寸、路径与解码开销，禁止外部资源和网络解析。转换器应选可部署、可测试的实现；不能因为原型环境有 Inkscape 就新增生产硬依赖。

中文字体必须覆盖长名称、中文标点与数字，来源和许可可审计。是否随包提供字体、采用哪种 SVG 转换器，是 P3 实现前要记录的依赖决定。

### P3 首个纯绘图切片的边界

先提供不接 Host 的通用 PNG 绘图基础，处理合成文本、行与表格；完整换行，超尺寸、像素或字节预算返回明确失败，不裁字或删行。Pillow 懒加载为可选依赖，字体由可信调用方显式注入，不捆绑或下载字体。首片不处理 SVG、不迁入原型图标，不实施物品卡或 Logs 业务布局。

绘图测试拟固定 [Pillow 12.3.0](https://pypi.org/project/pillow/12.3.0/)，其声明支持 Python 3.10 及以上；这不提高 SDK 最低版本，也不把测试库加入插件根运行依赖。独立测试依赖须纳入完整测试环境准备，缺库不能 skip 后记绿；字体加载和有限样张检查不能外推为任意字形覆盖或字体再分发许可。版本选择依据[官方稳定版发布说明](https://pillow.readthedocs.io/en/stable/releasenotes/)，实际验证环境和工件另行记录。

## 四 Logs 一张完整长卡

### 展示范围

一张长卡仅有一次主标题和角色概要，下面分成“绝境战”与“零式”两组：

- 绝境战：查询地区已经发布的全部绝境战
- 零式：查询地区当前资料片已经发布的所有零式 tier，不只最新 tier
- 只有当前资料片零式尚未发布时，才回退上一资料片的零式集合
- 角色没有记录、隐私隐藏、查询失败或部分数据缺失，都不能触发资料片回退

合成样图使用 7 行绝境战 + 13 行零式，共 20 行，目的是检验长卡排版。这不是运行时目录快照，也不是未来固定数量。新增内容应自然增加高度，不能靠缩小字号或删行塞进固定画布。

高度、像素总量、文件字节和平台投递限制必须有上限。正常范围保持一张卡；超出支持范围时在发送前选用清楚标明原因的完整文本降级，或提出新的版式决策，不静默截掉尾部副本。

### 数值与列含义

- rDPS 显示整数并使用千位逗号；原始数值保留在领域结果和 facts
- `ROUND_HALF_UP` 是建议的显示舍入方式，需在实现审查中确定并测试 `.5` 边界；不能把它写成已经确认的用户要求
- 百分位固定一位小数，整数补 `.0`；缺失显示未知符号，不能补成 `0.0`
- “最高 rDPS”与“最佳百分位”是各自独立的个人最佳，可能来自不同场次
- 列头或脚注明确上述独立含义，不能把两个峰值拼成同一场表现
- 原型的职业和版本归属于最佳百分位记录；生产必须核验两项最佳是否限制在同一职业/版本范围。若跨职业或分区汇总，应明确最高 rDPS 的职业与来源，不能在单一职业标签旁静默混用
- 保留真实覆盖范围和已有警告，不能因套用模板掩盖部分成功

### 低维护目录与名称

不建立需要随补丁更新的副本短名表或中文翻译表。优先使用 FFLogs 的本地化 `Encounter.name`；v2 怎样选择中文尚未验证，不能假设 CN 源或 `Accept-Language` 一定返回中文。[FFLogs Encounter 文档][fflogs-encounter]

中文不可得时保留上游原名，允许换行。只有已验证的共享游戏 ID 才能连接另一个中文游戏数据源；禁止按相似名字、猜测 ID 或 LLM 翻译连接。XIVAPI 兼容中文数据源的存在，不代表 FFLogs encounter ID 与游戏数据 ID 相同。

分类、资料片归属、区域发布状态、顺序及历史目录去重，必须先做[只读元数据探查](fflogs-discovery.md)。既有目录查询只有 id、name、difficulties、encounters；这些不足以证明已经具备全自动目录。[现有目录查询][catalog-query]

如果上游缺少足够字段，应报告“自动目录规则尚未闭合”及影响，不能默认引入人工分类表，也不能声称已经做到零维护。CN/global 源、地区验证范围和已有凭据边界继续分开。

## 五 图片生产链路

### 当前并未接通

1. runtime 创建 `GenericDisplayRenderer` 时没有 `asset_reader` 或 `image_backend`，图片读取与尺寸预算均为 1；这不是可用图片配置。[runtime][runtime-image]
2. `GenericDisplayRenderer` 将通用 DTO 转成文本与已有资源引用，不是 Pillow 卡片生成器。[renderer][generic-renderer]
3. `AstrBotMessagePort` 当前拒绝非空 `resource_ids`，只构造 Plain 消息。[message port][message-port]
4. 帮助链路经 TextPresenter 返回字符串；普通业务结果走 OutputService → MessagePort。只改 `main.py` 的 `plain_result` 不能接通业务图片。[help bridge][help-bridge]
5. 公开资源注册接口要求调用方提供与暂存内容一致的 asset ID；ID 的生成依赖内部 owner scope。模块不应复制私有 CoreOwnerScope 哈希算法。[资源注册][resource-register]

### SDK 不变的接入方向

模块提供领域事实和展示结构；通用 presentation 提供纯绘图能力并返回有界位图；Host 负责能力选择、资源注册、授权读取、投递和回收。模块 logo 作为可选资产输入，不把 FF14 逻辑塞入绘图基础设施。

在现有 SDK 下，优先评估 Host 内部受控注册路径：由可信调用上下文完成暂存与 asset ID 生成，再复用现有授权、TTL、文件生命周期与资源引用校验。该路径必须审查，不能绕过 ownership/grant 检查，也不能以全局公共文件路径替代授权资源。

P3 需一并明确 owner、audience、TTL、撤权、取消、清理失败、缓存复用以及发送时再次校验；`PRIVATE` 展示标签本身不是授权。

固定 AstrBot 源码已核实 [`Image.fromBytes`](https://github.com/AstrBotDevs/AstrBot/blob/3c7adafa1397e182d60b1016bf88759265113c8a/astrbot/core/message/components.py#L528) 同步持有编码后的图片内容，可优先评估以减少源文件路径存活期依赖；仍须先授权和验证 PNG，并计入 base64 约 4/3 的额外内存。`fromFileSystem` 仅保存路径，不提供本插件的资源授权或图片验证；不要把它当成全局读取入口。组件可构造与 `send_message` 的 await 返回均不能证明图片最终可见、用户收到或平台临时资源已回收。

### 降级和投递语义

图片不支持、依赖缺失或渲染失败时，在任何发送开始前选择文本降级，并保证事实一致。发送一旦返回 `UNKNOWN`，可能已经到达平台，不能自动补发文本造成重复消息。[发送状态][message-port]

不得绕过现有 OutputService 的投递边界；预发送失败与发送后状态不确定必须分别测试。LLM 工具始终直接接收 facts，不接收图片后再 OCR。

当前通用文本渲染也受字符、行数、表格行数和 blocks 预算约束，不能假定现有 `render.text` 总能提供完整降级。未来接线须逐项证明文本投影完整；图片与完整文本同时超限时，明确提示超限并请用户缩小查询范围。这是发送前的单次响应，不默认引入分页、多消息或额外补发。

## 六 明确留到其他批次

- Logs 的 `result.warnings` 未随当前命令 document 渲染的问题，留到下一 BUG 批；本轮不得删除或进一步吞掉已有警告
- Core 取消 pump、digest 分页、public-web eligibility、多模块 assembly 等问题另行处理
- `partition=-1` 与正整数校验冲突目前是静态疑点，要独立复现，不能混入本设计修复
- Dota2、DLC、CI 改造、真实实例安装与本地清理不属于本次交接交付

## 代码与文档依据

以下源码链接均固定到审查基线；续作开始时还需核对分支最新实现。

[ui-module]: https://github.com/yomihime/astrbot_plugin_yomihime_game_link/blob/8251a52580b8839964f8f232d45706a98c1fd02d/pages/frontend/src/module-management.ts#L79-L82
[ui-main]: https://github.com/yomihime/astrbot_plugin_yomihime_game_link/blob/8251a52580b8839964f8f232d45706a98c1fd02d/pages/frontend/src/main.ts#L97-L98
[help-bridge]: https://github.com/yomihime/astrbot_plugin_yomihime_game_link/blob/8251a52580b8839964f8f232d45706a98c1fd02d/adapters/astrbot/command_bridge.py#L109-L171
[error-contract]: https://github.com/yomihime/astrbot_plugin_yomihime_game_link/blob/8251a52580b8839964f8f232d45706a98c1fd02d/core/public_result.py#L134-L155
[fact-budget]: https://github.com/yomihime/astrbot_plugin_yomihime_game_link/blob/8251a52580b8839964f8f232d45706a98c1fd02d/core/public_result.py#L10-L32
[tool-facts]: https://github.com/yomihime/astrbot_plugin_yomihime_game_link/blob/8251a52580b8839964f8f232d45706a98c1fd02d/adapters/astrbot/tool_publisher.py#L226-L230
[catalog-query]: https://github.com/yomihime/astrbot_plugin_yomihime_game_link/blob/8251a52580b8839964f8f232d45706a98c1fd02d/modules/ff14/features/fflogs_catalog.py#L88-L93
[runtime-image]: https://github.com/yomihime/astrbot_plugin_yomihime_game_link/blob/8251a52580b8839964f8f232d45706a98c1fd02d/adapters/astrbot/runtime.py#L845-L868
[generic-renderer]: https://github.com/yomihime/astrbot_plugin_yomihime_game_link/blob/8251a52580b8839964f8f232d45706a98c1fd02d/presentation/rendering.py#L122-L170
[message-port]: https://github.com/yomihime/astrbot_plugin_yomihime_game_link/blob/8251a52580b8839964f8f232d45706a98c1fd02d/adapters/astrbot/message_port.py#L29-L76
[resource-register]: https://github.com/yomihime/astrbot_plugin_yomihime_game_link/blob/8251a52580b8839964f8f232d45706a98c1fd02d/services/resources.py#L365-L456
[fflogs-encounter]: https://www.fflogs.com/v2-api-docs/ff/encounter.doc.html
