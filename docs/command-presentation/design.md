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
- 暖桃色纸感保留为初版与可选预设；最终默认主题待选，不把离线脚本的默认参数当作用户确认，不做不受控随机配色
- 字号、留白、圆角、标题、来源脚注与警告层级由通用 presentation 统一
- 所有命令图片共享此风格；各功能保留适合信息内容的版式
- 物品卡尚无已确认版式，不能把市场卡或现有物品展示直接宣布为最终设计
- 图片与文本降级使用同一事实来源，不能因视觉简化改变查询范围

现有 [市场、帮助与 Logs 原型](../../examples/command_cards/README.md) 是可运行的布局参照，不是插件里的新功能。不得导入真实玩家截图、个人成绩或私人环境路径充当公开测试夹具。

原型对极端长名称仍可能缩字或省略，这只是已披露的演示限制。生产接入需完整换行或发送前完整文本降级；原型长名测试不等于生产无信息丢失验收。

### 模块富文本与语义色续作

最新要求以 [模块开发者展示样式 Spec](module-display-style-spec.md) 为准：Core 统一渲染、主题与受限富文本样式，模块定义评价范围、映射及业务布局；独立 Python helper 是离线参考，不是 SDK 升级或生产调用入口。FFLogs 七级字面色在浅深主题保持一致，先按原始百分位分级再显示一位小数；非百分位值必须有模块可解释的比较依据，不能任意归一化 DPS 或价格。

B「居中柔影」仅在字面与实际背景对比度小于 1.5 时启用，原始字形蒙版、零偏移、无扩张或硬描边；阴影配色保留已选原型的字面色与反色背景中点，最后合成清晰字面。A「贴字柔光」是独立的真实金色满分装饰，不应用到其他等级。主题选择仍待确认；该样式参考未接入 `presentation/card_bitmap.py` 或 Host。

### 总帮助

先展示所有 active 且实际可用模块的展示名与入口，再从这个集合随机选择一项，写成“从 XX 开始吧”。推荐项只是附加引导，不能代替完整模块列表。

同一次帮助事件固定选择；新事件可以重新选择。零模块时显示简洁空态与确实可用的下一步，不抽取空集合、不推荐禁用模块、不编造安装或恢复结果。原型只有一个模块，不能用它证明多模块布局已经完成。

### 模块图标与资产

每个模块拥有自己的可选 logo；通用样式不依赖 FF14。默认接受已审查的模块内单色 SVG，颜色由 `currentColor` 表达，缺失或处理失败时保留文字安全降级。

FF14 原型图标参考经典陨石意象进行绘制；它不是官方矢量原件，也不构成官方授权声明。保留参考来源、作者说明与适用许可，生产打包前核验资源和字体的再分发条件。

不要将正则过滤任意 SVG 当作安全边界。生产路径只处理可信模块资产白名单，限制字节数、尺寸、路径与解码开销，禁止外部资源和网络解析。转换器应选可部署、可测试的实现；不能因为原型环境有 Inkscape 就新增生产硬依赖。

中文字体必须覆盖长名称、中文标点与数字，来源和许可可审计。是否随包提供字体、采用哪种 SVG 转换器，是 P3 实现前要记录的依赖决定。

### P3 首个纯绘图切片的边界

`presentation/card_bitmap.py` 提供不接 Host 的通用 PNG 绘图基础。调用方传入不可变 `CardSpec`、`Section`、`Row`、`BitmapBounds` 与可信 `FontProvider`，`render_card` 返回 PNG 字节、宽高与媒体类型；完整换行，超尺寸、像素或字节预算抛出限定原因码的 `BitmapUnavailable`，不裁字或删行。Pillow 懒加载为可选依赖，字体由可信调用方显式注入，渲染器不自行发现或下载字体。首片不处理 SVG、不迁入原型图标，不实施物品卡或 Logs 业务布局。

绘图测试固定 [Pillow 12.3.0](https://pypi.org/project/pillow/12.3.0/)，其声明支持 Python 3.10 及以上；这不提高 SDK 最低版本，也不把测试库加入插件根运行依赖。`requirements-test.txt` 包含独立的 `requirements-presentation-test.txt`，缺库不能 skip 后记绿。版本选择依据[官方稳定版发布说明](https://pillow.readthedocs.io/en/stable/releasenotes/)，实际验证环境和工件另行记录。

纯绘图硬预算为宽 2048、高 8192、总像素 8,000,000、PNG 1 MiB，以及独立的输入、行列和测量次数上限；调用方只能降低。它们不是平台发送上限，也不扩大 SDK facts、32 blocks 或现有 Host 文本/资源预算。先测量完整布局，再分配画布；固定输入、字体和预设可重复输出。

`FontProvider.supports` 是可信调用方针对确切字体和 face 的覆盖声明，渲染器逐字符检查声明，但不能证明任意 provider 的声明为真。依赖与字体缺失时只返回明确的不可用原因，不自动扫描路径、下载字体或进入发送链。

### 固定字体与许可

`presentation/bundled_font.py` 提供显式构造的 `BundledFontProvider`。采用未修改的 Noto Sans CJK SC Regular 2.004、face 0，固定[上游版本](https://github.com/notofonts/noto-cjk/tree/523d033d6cb47f4a80c58a35753646f5c3608a78)；原版 OTF 为 16,437,364 bytes，SHA256 `2c76254f6fc379fddfce0a7e84fb5385bb135d3e399294f6eeb6680d0365b74b`。原 [OFL 1.1 许可](../../presentation/assets/fonts/OFL.txt)与[来源说明](../../presentation/assets/fonts/NOTICE.md)随包保留，不改字体或保留名称，不推定其他素材的授权。

导入不读取资产或加载可选库；显式构造时有界读取精确 package 资产、核对长度与摘要，再从同一份不可变字节加载指定 face，避免校验后重开路径。没有路径参数、系统字体扫描、网络下载或全局缓存。每次渲染持有四个字号的字体对象，Pillow 可能保留副本；该边界不等于精确 heap/RSS 预算或内存擦除。

`_font_coverage.py` 由该 face 的 `getBestCmap` 排除 `.notdef`/glyph 0 后生成：44,810 码点、2,032 个连续范围。测试使用独立固定的 fontTools 4.67.0 重建并逐项核对；fontTools 只作测试依赖，不进入插件运行依赖。这是字体 cmap 清单，不是游戏名称映射，也不保证任意 Unicode、字体塑形或每个字形的目视正确。有限样图另验中文标点、数字、长名与罕见字。

打包只允许字体、OFL、NOTICE 三个精确资产，复用既有路径、链接、打开句柄和快照校验，再核资产长度与摘要；不放宽通用文件后缀。缺失或损坏即构建失败。加入原版字体后候选包体约 15 MB，不能沿用早期不含字体的候选大小；具体字节与摘要见当前检查点。字体 provider 尚未由生产命令或 Host 图片出口调用。

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
- 百分位右对齐、垂直居中，各等级使用相同背景；错误显示红色 `ERR`，缺失显示主题色 `—`，有效零与不可评价原值分别保留。每 Boss 的 `totalKills` 必须是 FFLogs 记录到的非负整数，不能由排名条数替代；实际响应字段仍待本地真实 fixture 核验
- 图片底部只显示实际查询地区的 `数据源：cn.fflogs.com` 或 `数据源：www.fflogs.com`，不同时列两者，不加汇总记录数或长统计口径脚注；完整语义与边界见模块展示 Spec

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

在现有 SDK 下，`services/generated_png.py` 提供 Core 私有、有界的内存生成 PNG 注册：只服务当前根命令、当前结果，不落文件或跨请求缓存。上下文从真实 OutputService 的 issuer、admission、principal、owner proof/Grant 和可信 route 派生，不能接受模块自报 owner。该服务不替代模块已有 ResourceAccessService，也不暴露全局 asset ID 或路径读取入口；尚未接入运行时或真实发送。

每个上下文只注册一张图片、取得一次 pin、读取一次；读取本身不授予发送许可。普通操作自行进入真实 mutation gate，不能把子任务继承的 ContextVar 当作持门身份。单图沿用 1 MiB、2048×8192、8,000,000 像素上限；资源总量额外限制为 8 个上下文或保留项、4 MiB 原始 PNG、16 MiB 编码转换预留，解码并发为 1、RGBA 像素暂存预算 32 MiB。TTL 默认 30 秒、最多 60 秒，其余上限只能降低。这些是数据预算，不是 Python heap、RSS 或 CPU 硬沙箱。

`presentation/png_validation.py` 只接受生成用的 8-bit RGB/RGBA、非交错、无附加 metadata 的 PNG；核对容器、CRC、连续 IDAT、有界 deflate 流、扫描行长度和 filter 枚举，再由 Pillow verify/load。不依赖宿主允许截断图片的设置，也不修改 Pillow 全局配置。过期、撤权、取消或 close 后不能晚注册；尚在 await 或被 pin 持有的字节继续计量，实际释放后才扣除。

不落文件的范围内无需新增文件 ledger、GC 或数据库迁移；若以后引入文件或持久缓存，必须重新审查独占资源和物理回收。内存中已交出的 bytes/base64 副本也不能承诺清零或撤回。随机临时 handle 不进入现有 `resource_ids` 或持久化 fingerprint；稳定内容摘要、发送许可和真实任务 pin 的组合留到发送切片单独验证。

P3 需一并明确 owner、audience、TTL、撤权、取消、清理失败、缓存复用以及发送时再次校验；`PRIVATE` 展示标签本身不是授权。

固定 AstrBot 源码已核实 [`Image.fromBytes`](https://github.com/AstrBotDevs/AstrBot/blob/3c7adafa1397e182d60b1016bf88759265113c8a/astrbot/core/message/components.py#L528) 同步持有编码后的图片内容，可优先评估以减少源文件路径存活期依赖；仍须先授权和验证 PNG，并计入 base64 约 4/3 的额外内存。`fromFileSystem` 仅保存路径，不提供本插件的资源授权或图片验证；不要把它当成全局读取入口。组件可构造与 `send_message` 的 await 返回均不能证明图片最终可见、用户收到或平台临时资源已回收。

### 首轮 Host 目标：AstrBot WebChat

首轮目标已选 WebChat；其他适配器不能据此推定支持。固定版本的 [WebChat adapter](https://github.com/AstrBotDevs/AstrBot/blob/3c7adafa1397e182d60b1016bf88759265113c8a/astrbot/core/platform/sources/webchat/webchat_adapter.py) 可按会话发送，但会向该会话的多个活跃 request IDs 投递。新图片出口须绑定可信的触发 event/request，不能仅凭会话名或全局可变的“当前事件”保证一次投递。

[WebChat event](https://github.com/AstrBotDevs/AstrBot/blob/3c7adafa1397e182d60b1016bf88759265113c8a/astrbot/core/platform/sources/webchat/webchat_event.py) 先写 Host 附件，再尝试放入请求队列；队列关闭可能未接收，而该路径仍返回 `None`。因此 Core 内存资源不落文件，不等于 Host 没有附件副本；后续须证明附件归属、关闭或取消时回收、队列接受状态及受权文件读取。源码中的 SSE/WS 和 Dashboard 图片展示路径，仍需真实 Host 与浏览器验收，不能作为已经投递的证据。

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
