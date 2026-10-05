# T-05：Core 区域配置与 FF14 市场查询设计

> 2026-10-05：C0/C1 及 T-05-Q1 解析片已离线交付并独立审查；T-05-S1 客户端已完成离线、最终独立审查及定点公开源验证，正式 Host / 命令 / 页面接线及真实迁移仍单独验收。本版替代此前“仅显式 ID、必须单服务器、缺范围追问”的设计。策略默认值已确认，不代表性能、完整覆盖或实例更新已验收。

## 1. 已确认需求与配置归属

名称为主入口、ID 兼容，复用精确匹配和歧义候选。范围优先级：**本次显式范围 > Core 默认区域的全服**；省略范围不追问。cn 使用 Universalis China，global 汇总官方支持国际区域，不能向上游传虚构 global。页面、命令、确定性自然语言和后续工具共用参数解析器；候选续接保留范围、品质与意图。

手动查询、有限结果，复用当前 HTTP、SDK 结果合同与页面组件；不做账号默认、市场订阅、自动刷新、任意来源、逐服务器无界调用或复杂图片。C0/C1 及 Q1 解析片离线完成，本批 S1 仅目录 / 市场客户端与相应验证，不迁移真实实例，正式入口后续单列。

| 配置 | 定义 / 默认 / 校验归属 | 存储与呈现 | 生效含义 |
| --- | --- | --- | --- |
| default_region：cn/global，建议初始 cn | game-link Core，严格枚举 | 现有 Core 配置库的保留 Core 命名空间；授权管理入口“Core / 游戏区域”分组 | 游戏服务区域，与语言、时区、代理独立；不是 AstrBot 宿主全局配置 |
| ff14_calendar_default_days：7，整数 1–30，拒绝 bool | FF14 模块 | 同一配置库、ff14/ff14 命名空间；FF14 配置分组 | 省略 days 的新查询 |
| ff14_calendar_default_timezone：Asia/Shanghai，有效 IANA 时区 | FF14 模块 | 同上 | 新查询 / 显式新建订阅的缺省时区，不随区域切换 |
| ff14_calendar_default_delivery_time：08:00，严格 HH:MM | FF14 模块 | 同上 | 显式新建订阅的缺省摘要时间，不自动创建订阅 |
| 订阅开关 / FFLogs 敏感字段 | FF14 模块，保留现有定义 | 原模块配置 / SecretStore | 本批不扩展功能 |
| web_public_origin / 宿主接线 | AstrBot 适配器 | 现有宿主插件配置 | 不参与游戏区域默认规则，保持原鉴权 |

FF14 不再存第二个区域开关。其它游戏自行解释通用 cn/global，不继承 FF14 World/DC 编号。Core 统一存取、命名空间隔离；不另建配置存储。配置读写沿用合法管理链；Dashboard 登录、chat scope、查询 proof 不等于 Core 管理 grant。公开查询页保持业务只读。

### 实施前基线与调整清单

| 源码证据 | 当前实现 | 必要调整 |
| --- | --- | --- |
| _conf_schema.json:2–30；config_adapter.py；modules/ff14/config.py:11–81 | 四项宿主默认值，区域只提示；无效普通配置暂停插件 | 旧四项作为迁移输入；区域归 Core，三项日历字段归模块 schema |
| adapters/astrbot/runtime.py:119–122,329–342,471–473,782–783 | 启动冻结 / 注入四项，settings、帮助读旧 snapshot | 成功迁移后读取新权威；帮助不再要求市场显式区域 |
| services/module_services.py:1264–1280,1398–1416 | Host 与模块声明 / Core 值重名即拒绝 | 三字段 manifest 声明与停止旧 Host 注入同片切换，不能只改名 |
| services/configuration.py:706–714；yomihime_sdk/api/manifests.py:598–618 | 隔离仓储 / 白名单 / CAS 已有，ConfigField 缺类型 / 枚举 / 范围 metadata | 补必要 schema 声明与写前校验，不凭 default 猜类型，不重建公共层 |
| services/admin_operations.py:515–539 | 已有 UPDATE_CONFIG grant、generation、revision；目标只接受已有模块 | 新 Core 保留目标尚无管理接线，补薄入口，不能伪装已可用或冒充 FF14 配置 |
| modules/ff14/module.py:52–55；calendar.py:347–369；calendar_subscriptions.py:74–98 | 工厂冻结默认；日历 / 新订阅仍要求显式 region，记录保存 region/timezone/time | 三日历默认逐次读取；既有日历、Logs、新订阅显式区域合同不顺带扩展 |
| pages/ff14/app.js:15,161,411,767 | 验证 / 显示旧区域，自行填 Logs / 日历默认 | 默认提示读取 Core；市场范围由服务端解析，JS 不自定规则 |
| features/items.py:38–97,148–169 | 精确优先、完整空结果才模糊，歧义给文字 ID | 最小共享解析，市场候选增加结构化上下文，不解析展示文字来恢复意图 |

**C0 合同**：复用 config_state/config_entries 与 ConfigTarget，保留 Core 目标 game_link/core，禁止第三方模块注册 / 写入。模块只读配置视图在模块字段校验后由 Core 组合 core_defaults，包含 default_region、独立 revision 及来源 / 冲突诊断；不复制到模块配置行、不由 FF14 声明、不接受模块 patch。Core / 模块 revision 分开，不伪造统一原子版本。ConfigField/manifest 增加兼容可选 value_schema，复用既有 SDK schema 校验及授权协调器 / 仓储。SDK 1.4 结果、ErrorCode 保持不变；不另建数据库、平行模块注册表或通用配置框架。实际接线与剩余管理读取缺口见 §7。

## 2. 四字段迁移与回退（规则已确认，C1 离线完成，实例操作另行授权）

旧实际权威是宿主 snapshot，不能假定已持久化到 Core。只盘点四普通字段和目标命名空间，区分未保存与宿主自动补默认，不读取秘密。

1. **冲突**：有效新 Core 值优先；新值不存在导入有效旧 ff14_default_region；两边确实缺失才初始化 cn。双方有效但不同保留新值并报告来源 / 冲突；新值无效不覆盖、不回落 cn，旧值无效且无新值同样暂停迁移、保留原值。三日历字段逐字段采用相同优先级和模块校验，不给 FF14 再存区域。
2. **原子 / 幂等**：在新模块装配前执行迁移，现有库短事务检查 Core / 模块 revision 和迁移版本，记录字段来源与完成标记；四项全部成功、完成标记有效后才停止 Host 注入并启用新配置视图。CAS / 事务失败全部回滚、保留旧值与旧数据权威；不得装配“新 manifest 三字段声明 + 旧 Host 同名注入”的混合视图。失败返回可恢复配置错误并保留诊断 / 授权修复及旧包回退路径，不宣称新包仍能正常查询。库事务不覆盖代码装配切换，重启须先核完成标记再装配。不能半迁移、在查询中偷偷迁移或清空无效值。
3. **唯一写入**：成功后仅授权管理链写新配置。旧宿主四值保留作只读兼容 / 回退材料；旧齿轮变化不覆盖 Core，不长期双写。保存重载也仅读标记与新权威；旧开关撤下或标明已迁移，不能继续表现为有效管理入口。
4. **回退**：升级前保存代码、四字段和一致性配置备份到私有位置。未发生新配置写入可回退旧包 / 旧值；发生写入后先确认四字段反向导出，不回滚整库业务数据。失败迁移原权威不变。本设计不授权操作实例。
5. **影响**：新查询捕获当前 Core / 模块 revision；进行中请求、候选续接保持原规范范围，不中途改向。既有订阅 collector region 和 filters timezone/time 不改；新订阅仍显式 region，仅缺省时区 / 时间读模块默认。缓存键含模块、接口语义、规范范围、ID、品质、限制、解析版本；先解析再缓存，不跨区域复用，不清空全库。生命周期 fence 与迟到失效不变。

当前 UI-C 管理身份仍未闭合，公开页不能写配置。迁移和授权服务可先离线验证；实际管理分组须证明合法 principal / grant / revision 链。无权限只读并给恢复说明，不报告配置管理完成，不凭网页登录获取管理权限。

## 3. 单一解析流程与交互（参数形式为建议）

命令例：
- /ygl ff14 market 犎牛牛排
- /ygl ff14 market 44091 server=紫水栈桥 quality=hq
- /ygl ff14 market 牛排 region=global intent=min
- /ygl ff14 market 44091 dc=猫小胖 intent=listings

建议 query 必需；server/dc/region 可选。用户已确认首版 quality=all、intent=overview；可选品质 all/nq/hq、意图 overview/min/listings。region 为 cn/global 或已验证标准国际区域；保留现有 ID、长度与控制字符校验。页面市场区提供名称 / ID、可空范围、品质、意图，再显式查询；复用正式只读 bridge 与结果组件，不自动请求。确定性自然语言只把明确语法转成同参；后续工具也使用此解析器，本批不注册工具或意外调用 LLM。

共享 FF14 MarketQueryResolver（暂名）输出不可变规范查询与请求计划；入口只转写语法 / 鉴权：

1. 在现有受控读取边界内捕获一次 Core / FF14 快照及各自 revision，避免读取过程中配置写入形成混合默认值；校验 query、品质、意图和范围。名称精确优先，完整空结果才模糊；非法输入不当缺参。
2. World/DC 使用官方 worlds/data-centers 精确匹配名称或 ID，验证归属。多个显式范围相互包含时按 World > DC > region 确定单一 scope，不能把指定 World 扩成全区；归属矛盾 PARAMETER_ERROR。重名要求明确 ID，范围省略不追问。目录失败不能报“服务器不存在”。
3. 显式范围决定区域；无范围才读 Core：cn→China 全服，global→标准国际四区。显式国际 World 可以覆盖 cn 默认，反之亦然；Core 不解释 World/DC。
4. 歧义 NEEDS_SELECTION，尚不发市场请求。复用 SDK FactDocument 承载，新增市场白名单候选 ID 和 market_query_context（规范范围 / 品质 / 意图 / 配置 revision / 解析版本）；公开 DTO 投影与页面校验须新增这些明确白名单，当前文本候选未提供此能力。document 同时给完整 ID 续查命令。选择仅替换 query 为 ID，再交同一解析器；不从中文提示反推。上下文是公开查询参数，不是授权凭据；不得带任意 URL、Host、权限、原始配置或身份绑定，不长存会话。
5. 新提交重新解析；候选续接把原规范范围作为显式范围，保留品质 / 意图，不再套中途修改的默认值。重复提交抑制、停止展示、迟到失效和后台取消语义不变。
6. 结果显示规范区域 / 范围与来源（显式 / 默认）、请求覆盖、失败区域、品质、意图、源时间 / 获取时间 / 年龄。四区响应成功不等于全部 World 上传或实时可买。

## 4. 官方数据源、接口与合并语义

起点：[Universalis 当前文档](https://docs.universalis.app/)。实际源码固定 **e0d1acf5543a27feab56f24dc6e242e31b87a591**；线上部署 commit 未知。前次 44091/1043 NQ 直接样本三挂牌：1998/2050/2080，各数量 3，上传毫秒 / 挂牌来源秒已核；不是 Host / 页面通过。旧 HQ+ID 的 HTTPError 未保存具体状态，仍未归因；S1 对完全相同参数补采 HTTP 200 / JSON，不能以当前成功反推旧错误原因；IsUntradable 只核 false。无参考源码迁入。

设计调查阶段唯一 Universalis 实时 GET 为 [data-centers](https://universalis.app/api/v2/data-centers)，2026-10-04T14:05:27.510Z，HTTP 200、18 DC。文档标准区域为 North-America、Europe、Japan、Oceania、China/中国；**cn 使用 China；global 按用户确认覆盖前四个标准国际区，结果明确四区范围**。目录另有 NA-Cloud-DC Beta、한국、繁中服。文档称韩国不支持，另两类未有标准支持合同；[解析源码](https://github.com/Universalis-FFXIV/Universalis/blob/e0d1acf5543a27feab56f24dc6e242e31b87a591/src/Universalis.Application/Common/WorldDcRegion.cs#L42-L94)动态接受目录 region，并非硬编码枚举。路由认可不代表正式市场支持 / 覆盖，文档与目录口径差异保留待证；不能把四区称为所有目录地区。纳入额外地区须补正式支持证据并确认，不静默扩大。

| 意图 | 官方接口 / 字段 | 合并与覆盖 |
| --- | --- | --- |
| overview：价格概览 | GET /api/v2/aggregated/{worldDcRegion}/{itemIds}；results 每 item 含 itemId、nq/hq、worldUploadTimes；品质下 minListing、recentPurchase、averageSalePrice、dailySaleVelocity 含 world/dc/region 项 | 取对应 scope 层，逐区分列品质 / 指标；不平均地区均价或中位数。文档均价 / 日销量速度基于最近四天，展示口径 |
| min：最低挂牌 | 同 aggregated 的 minListing.price 与对应 worldId；cn 一次 China、global 四次 | 各品质比较有效区域最低单价，保留地区 / 获胜 World / 年龄。缺指标不补 0；部分覆盖只称“已覆盖区域最低价” |
| listings：有限挂牌明细 | GET /api/v2/{worldDcRegion}/{itemIds}；listings=6、entries=0；hq=true/false 在上游 Take 前过滤，all 省略 hq；白名单 pricePerUnit/quantity/hq/lastReviewTime/worldID 等 | 单 scope 有限挂牌；global 每区取有限前缀，再同口径排序合并最多 5 条，稳定 tie-break、World / 区域及截断提示；不是全服库存 / 成交统计 |

[聚合路由 / 分层取数](https://github.com/Universalis-FFXIV/Universalis/blob/e0d1acf5543a27feab56f24dc6e242e31b87a591/src/Universalis.Application/Controllers/V2/AggregatedMarketBoardDataController.cs#L22-L258)、[DTO](https://github.com/Universalis-FFXIV/Universalis/blob/e0d1acf5543a27feab56f24dc6e242e31b87a591/src/Universalis.Application/Views/V2/AggregatedMarketBoardData.cs#L1-L81)、[挂牌过滤](https://github.com/Universalis-FFXIV/Universalis/blob/e0d1acf5543a27feab56f24dc6e242e31b87a591/src/Universalis.Application/Controllers/CurrentlyShownControllerBase.cs)。aggregated 每次一个 scope、最多 100 item ID；首版一个物品，同执行重复意图可合并。没有 global / all-region 单次 API。

- NQ/HQ 以来源标记为准，缺失不补 false/0；all 概览分列。minListing 没有挂牌数量 / 该条发布时间；recentPurchase 是成交价，不能替代挂牌价。DTO 声明 medianListing，但本 commit 初始化器未赋，首版不承诺中位数。
- pricePerUnit 每单位 Gil；quantity 是该条堆叠数量，total 是堆叠价。v2 无 noGst 参数，不发送、不擅算含税价。限量 listingsCount/unitsForSale 是返回子集，不是全服库存。
- aggregated recentPurchase.timestamp 与 worldUploadTimes.timestamp 为 Unix 毫秒；后者只查询相关最低价来源 World，**不是整个区域所有服务器更新时间**，也不是最低挂牌发布时间。没有聚合级 hasData / scope lastUploadTime；空指标 null / 省略的线上形态待证。
- currently shown lastUploadTime 毫秒、lastReviewTime 秒；后者称“挂牌来源时间”，不当整个来源更新时间。单 World 与区域多 World 时间分开说明，scope 时间不能证明全部挂牌新鲜。
- 插件记录获取时间；源更新时间 / 年龄未知写未知，未来写源时钟异常，不能显示“刚刚”或保证实时。旧数据保留并提示年龄；不设未经确认的硬过期阈值。异步聚合 / 缓存不保证挂牌仍可买。

**已确认初始策略**：挂牌最多展示 5 条，聚合并发 2、统一总期限 30 秒；这些不代表性能或完整覆盖已验收，也不混入面向用户的价格语义。建议最多四区域市场请求、每请求至多 10 秒且服从剩余期限；页面 35 秒。名称 / 目录也占总期限，不各区重开 30 秒。只调用当前意图接口；overview 已含 min 时不重复请求，相同 scope/item/intent 合并，聚合 NQ/HQ 复用响应。沿用 SourceHttp 配额、体积及现有缓存 / 任务服务；失败不拆 World、无自动 retry。近期限保留已完成区并及时组装 partial，不留后台续查。跨查询合并仅在 scope / 取消所有权安全时采用，不新建共享后台任务。挂牌品质必须先筛再截断，禁止先取混合前 N 再筛 HQ。

**状态**：参数 PARAMETER_ERROR；确认物品不存在 NOT_FOUND、不可交易 UNSUPPORTED；404 不能单独区分二者。确认该范围 / 品质无挂牌 NO_RECORDS，不说游戏里绝对无货。未上传 / 所有报价指标空也可 NO_RECORDS，但明说“来源暂无可用市场记录，无法判断是否无挂牌”；有其它可用指标而报价缺项则 partial，不能补零。aggregated failedItems / 全无结果 400 结合物品与解析证据归因，不直接判不可交易。429 / 本地配额 RATE_LIMITED；超时 / 5xx UPSTREAM_ERROR；格式 UNPARSED；鉴权 AUTH_REQUIRED/AUTH_EXPIRED。保留各区阶段 / 原因；部分覆盖有可用结果 PARTIAL_SUCCESS，全失败 ERROR，成功空区 / 无上传 / 请求失败分别记录。HTTP 200 或四区都响应不等于完整最低价。

## 5. T-05 最小任务拆分

| 任务 | 交付与验收 | 最小范围 / 前提 |
| --- | --- | --- |
| T-05-C0 | Core 保留目标、只读默认视图、schema / 授权 / revision 合同 | services/configuration、module_services、admin_operations、core_runtime；必要 ConfigField / manifest 扩展，结果合同不扩展 |
| T-05-C1 | 四字段原子迁移 / 冲突 / 幂等 / 回退，Core 唯一区域权威，FF14 三默认逐次读取及配置分组 | modules/ff14/config、module、manifest；Host config_adapter/runtime/_conf_schema；配置库目标行及必要迁移标记，管理写入依赖合法 principal/grant |
| T-05-Q1 | 共用解析器、名称 / 精确 / 歧义与续接；跨入口同参同规则 | 新 modules/ff14/query_resolution.py（暂名），items/item_sources 最小抽取，manifest 命令 / 明确 NL 转写；市场不取完整 Garland 获取途径 |
| T-05-S1 | Universalis 目录 / 客户端、三意图、有限合并 / partial / 年龄 | 新 features/market_sources.py、market.py，module/manifest capability/source，Host 必要域名 / 来源标签；复用 HTTP |
| T-05-P1 | 正式只读市场接口、页面组件、候选按 ID 重查与覆盖提示 | web_public/ff14_pages/runtime、pages/ff14/app.js；市场候选 / context 公共 DTO 白名单投影和校验，CSS 必要增量；不改 proof/origin/鉴权 / 后台取消 |
| T-05-V1 | 最终版本独立审查与分层验收 | tests/modules/ff14、services、infrastructure/sqlite、host、pages/ff14；实例更新另确认候选授权 |

C0→C1→Q1→S1→P1→V1。C0/C1 及 Q1 解析片已离线交付，记录见 §8 / §9；本批授权 S1 客户端与验证，范围见 §10。不迁移真实实例数据，不修改已有订阅范围，正式入口及后续切片待确认。唯一实现者 + 独立审查，不并写共享代码 / 迁移。每片固定四项报告：改动、验收证据、失败 / 未验证、下一最小片及前提；至多两轮集中修复，不以全仓历史问题清理为前置。

## 6. 验收、真实条件与待决项

- 查询：cn/global 默认全服；显式 World/DC/region 覆盖、冲突 / 重名；ID、精确名、歧义、截断候选、无结果；候选保留范围 / 品质 / 意图，中途改配置、新提交、迟到隔离；页面 / 命令 / 确定性 NL / 未来工具合同同解析器。
- 数据：四区全成功、单区超时 / 429 / 解析失败、三失败一成功、全失败；成功空区 / 未上传 / 不可交易分开；品质先筛后限量、跨区排序 / tie-break；缺值不补 0、秒 / 毫秒、未来 / 未知 / 旧年龄；不平均均价 / 中位数；名称 / 目录 / 并发 / 请求数 / 体积 / 32 blocks 都有界。
- 配置：旧 / 新 / 相同 / 冲突 / 无效 / 部分存在、宿主补默认；事务中断 / CAS / 重启幂等；旧齿轮不能覆盖新值；回退四值、订阅不变；跨模块隔离、禁止模块写保留目标；未授权拒绝、合法 grant/schema/revision 校验；区域不改时区 / 代理 / 其它游戏服务器解释。
- 后续命令：隔离 Python 定点 unittest（既有 tests.modules.ff14.test_config/test_items/test_calendar 与 services config/admin、Host 合同，加 market/resolver/migration 测试）；node --test tests/pages/ff14/test_ui_a.mjs tests/pages/ff14/test_item_display.mjs 加市场用例；隔离 Ruff、JS syntax、现有构建 / 包成员校验。最终市场批次全量保留完整失败清单，区分环境 / 历史 / 本批。C0 仅运行相关配置、授权、Host/bootstrap 与构建检查，不重验历史页面或业务。
- 真实条件：先低频公开源白名单响应 / 状态 / 时间，再真实 Host 配置 / 执行 / SDK 投影，最后正常登录页面真实 bridge 的名称 / 候选 / 默认范围。管理写入另需合法 Core 管理身份，网页登录不足。故障 / 大结果可以离线夹具，不制造限流或网络故障；候选 / 安装 / 重载另确认。市场 Host / 页面全部未验证。

**确认与剩余前提**：all/overview、5 条、并发 2 / 总期限 30 秒及新值优先迁移规则已确认；global 首版就是标准国际四区，映射由 FF14 模块维护，帮助和结果须明确实际覆盖，不写入 Core。每区取 6 判截断已按 S1 用户要求采用；Core 管理分组的 Host/UI-C 合法身份接线仍须证明，不新增授权机制。额外地区官方支持待证，不自动纳入；暂不设硬过期阈值。C1 仅离线验证完成；Q1 当时未请求网络；当前 S1 只允许定点公开源验证，真实迁移 / 更新及正式入口切片仍须另确认。

保留 WebChat 8/8、44091 正式页面闭环与局部美化历史证据，不算 T-05 回归。日历活动内容延期、零 LLM / 原生 DTO-block 未验证；NapCat、QQ 官方真实消息 / 群私身份仍待具备环境后验收。

## 7. T-05-C0 交付时状态与 C1 准确前提

C0 建立 `game_link/core` 唯一保留配置目标及 `default_region` 严格枚举，当前声明默认 cn。`CoreDefaultsView.current()` 根据原始持久化键的实际存在情况选择有效新值、有效旧值或默认值；非法新值报稳定字段错误，不回落、改写或删除旧值。冲突诊断只含字段、来源和存在 / 冲突标志，不含原始拒绝值。Host 适配器只映射实际存在的旧区域字段，Core 不认识 FF14 目录或旧字段名。

模块读取 `services.config.current().values["core_defaults"]`，其中 Core revision 与模块 snapshot revision 分开。在共用 mutation gate 内捕获两份快照，返回不可变嵌套视图；模块不能声明或 patch 保留字段。配置行仍使用同一 ConfigRepository / principal 命名空间。模块定义 `CALENDAR_CONFIG_FIELDS` 及 IANA / HH:MM 语义 validators，公共 Coordinator 提供声明校验和可选语义校验入口。纯选择与回退计划不执行迁移、写库或整库恢复。

授权写入复用 `AdminFacade.update_config(..., "game_link/core", ..., authorization=...)` 的既有 UPDATE_CONFIG grant、generation、revision/CAS 链；公开页面未增加写接口。当前 SDK 没有正式的 Core 管理读取 / revision 投影，Host/UI-C 合法 principal 接线也未完成；模块只读 revision 不能用来替代管理授权。该缺口须在 C1 管理接线中补明确投影，不能冒充模块管理快照或扩大权限。

非法存量读取仍拒绝；合法管理员可以显式 REPLACE 为有效值或 CLEAR 修正该字段。只有此次覆盖的普通旧值可以免于旧值 schema 校验，未触及 / KEEP 的非法值、白名单、秘密 metadata、新值校验和授权 / CAS 保持严格。此缺陷经先红测、最小修复、最终相关复测及独立审查关闭，未增加授权。C0 合同交付完成；实际迁移、配置管理页面、市场业务和实例验收尚未完成。

**C1 前置清单**：

1. 确认新的实施范围，完成四字段原子选择 / 写入 / CAS / 完成标记及可回退设计；区分原始宿主保存值与宿主自动默认，不先注入默认再决定迁移。迁移失败保持旧权威，不半装配。
2. 三个日历字段 manifest 激活、Coordinator 管理写入 / 模块读取语义 validators 装配、停止旧 Host 同名注入须同片完成；日历默认改为逐次读取。C0 尚未激活这三个字段，FF14 当前业务仍读旧 snapshot，不宣称完成切换。
3. 完成标记有效后停止 legacy fallback，并说明新键显式清除后的默认规则，防止旧影子值复活；保留旧原始值作兼容 / 回退材料，不双写。
4. 合法 Core 管理读取 / revision 投影与 Host/UI-C principal 链必须先明确；若不能提供，管理界面仍只读并准确报告缺口。不得从 Dashboard 登录、公开查询 proof 或模块读取取得管理权。
5. 验证新查询、候选续接、进行中请求的快照生效规则，既有订阅 region/timezone/time 不变；保留数据与代码回退材料。此条不授权操作真实实例。
6. SDK 仍为 1.4.0，但可选 metadata 改变 wheel 字节，源码与 wheel 的严格固定值已同步。未来候选更新须重新核实加载隔离，不能假定驻留旧 SDK 支持新 ConfigField；本批只离线构建，不安装或重启。

品质 / 意图、5 条、并发 2 / 30 秒及四区范围是已确认的后续策略，本批没有市场实现、性能或完整覆盖证据。

## 8. T-05-C1 已确认实施边界

本批只实现四字段迁移 / 完成标记、日历语义校验与逐次读取、旧 Host 业务注入切换、合法 Core 管理读取 / revision 合同，执行离线测试和独立审查。真实实例数据迁移、安装、部署及市场 / 页面功能不在授权内。

### 原始输入与唯一权威

固定 [AstrBot 配置源码](https://github.com/AstrBotDevs/AstrBot/blob/3c7adafa1397e182d60b1016bf88759265113c8a/astrbot/core/config/astrbot_config.py) 在加载时补缺失键、替换 null 并回写；构造函数传入的配置及回写后的文件不能证明此次加载前的字段存在情况。因此实际迁移前须显式准备仅含四个普通字段的可信原始快照，保留缺失 / null / 非法值及来源，不把已经注入的默认值当作原始显式值。历史文件若早已被宿主标准化，不能恢复或猜测其更早的用户输入；必要时须使用可信的升级前备份。

`scripts/prepare_ff14_config_migration.py --source <指定旧JSON> --output <新准备文件>` 只接受明确指定的自身插件旧配置和输出路径，严格拒绝重复 JSON 键与覆盖已有准备材料；加载阶段再校验插件绑定和字段摘要。不保存其它字段、凭据或业务数据。运行时使用插件数据目录中的 `ff14-config-migration-v1.json`。原始快照是可恢复的迁移输入，**新配置及完成标记的权威是同一 Core SQLite 库**。四字段和 `ordinary_config_migrations` 标记同事务提交，不声称文件和数据库跨存储单事务。事务内写入 / 标记 / 提交失败及非法输入不生成有效标记；缺可信输入且无有效标记时明确拒绝启动迁移。

调用者取消与事务失败须区分：现有 SQLite executor 会排空已运行的工作线程，调用者收到取消时，事务可能已完整提交。此时提交结果先记为未知，恢复后读取版本 / 目标 / 快照绑定有效的标记确定权威；不能因取消异常就断言未提交，也不能仅凭请求结束宣称业务可用。离线真实临时 SQLite 已覆盖完整提交后的取消恢复及事务内故障回滚。

完成标记有效后不再读取旧输入或回落旧值；重复执行 / 重启不能恢复已废弃值。原 Host 四键只保留为兼容 / 回退材料并标明业务已停用，不能继续作为新默认配置的写入权威。两目标 revision 在提交内检查，管理员并发修改导致冲突安全退出，不采用最后写入覆盖。

### 失败恢复、回退与管理

保存本次四字段限定的前后快照和来源，回退仅恢复这些字段及其完成标记，使用 revision / 受影响值校验；无法证明未发生后续冲突时拒绝回退并要求先导出核对。不恢复整库、无关字段或既有订阅。三日历声明、语义 validators 在管理与模块读取链装配、旧 Host 注入撤下同时完成；新查询 / 新订阅缺省逐次读取，显式参数和已有订阅范围保持。

`AdminFacade.config_snapshot(..., "game_link/core", authorization=...)` 返回 SDK `CoreConfigSummary`，管理读取复用既有 MODULE_SNAPSHOT grant / generation 边界，非法存量可显示安全字段状态和 revision 以进行合法修复，不投影非法原值或秘密。写入仍使用现有 UPDATE_CONFIG 授权 / CAS。公开查询页面不获得管理权限。AstrBot 当前管理上下文验证仍拒绝所有未核验上下文；新增正式 Core 读取方法不等于真实 Host/UI-C 管理入口可用，后续可信身份桥接须另行明确，不能凭网页登录放行。

至少覆盖 raw 新旧组合、写入 / 提交 / 标记故障、幂等 / 重启恢复、revision 并发冲突、完成后忽略旧值、日历默认与显式覆盖、授权拒绝 / 修复、限定回退和无关数据保护。保留 C0 修复回归测试与历史 ACL 失败，最终冻结后重跑相关测试、Ruff、构建及成员校验，不重验全部历史工作。

### 本片离线验证及后续依赖

最终相关测试 264 项，262 通过，2 项为既有 Windows ACL 环境错误，0 新失败；Ruff、构建与包校验通过。独立审查发现页面错误状态会阻断合法修复后的命令，已用两项 red→green 回归作局部修复：页面当前配置错误只影响该次投影，启动失败仍严格拒绝；当前配置非法时帮助安全报错，合法 REPLACE/CLEAR 后下一条命令直接读取当前值恢复，无需重访页面或重启。使用一轮集中修复，最终独立审查 0 Blocker / 0 Important / 0 Polish。

来源证据保留于限定迁移快照的 before / legacy / after，可按固定版本重建来源与冲突。迁移返回的脱敏 diagnostic 尚未另存为冲突日志或提供运行时查询接口，不能将完成后的 current-view 来源当作迁移时来源，也不能宣称已有额外持久化脱敏日志。

真实实例未迁移、未安装、未验收。首次加载前需核实原始四字段准备材料、SQLite 一致备份、候选及旧 SDK 进程隔离，更新 / 启动操作另确认；不能将离线测试代替实际迁移。真实 Host/UI-C 合法管理身份桥仍是明确缺口，公开查询页继续只读。C1 收尾时的下一最小代码片为 Q1；其当前授权与快照规则见 §9，仍不实现市场来源或页面。

## 9. T-05-Q1 当前实施合同与发布前置条件

Q1 原定义是共用参数解析、名称 / 精确 / 歧义与续接，包含后续命令 manifest / 明确自然语言转写。本次授权收窄到确定性解析与对应测试：提供命令、结构化页面参数、确定性自然表达、后续只读工具可复用的转写 / 解析函数；不注册市场能力、无差别消息监听、新 LLM 会话或工具系统，不提前接入行情查询。正式页面候选 DTO / bridge 接线仍归 P1，网络目录 / 市场客户端仍归 S1。

物品名为主入口，数字 ID 兼容，复用已有严格输入校验与精确匹配；仅完整空精确结果允许模糊候选。统一输出不可变物品、规范范围、品质、意图和两配置 revision。省略范围读取单次 Core/module 组合快照：cn 为 China 全服，global 为 North-America / Europe / Japan / Oceania 标准四区；明确 World/DC/区域优先、相容取最窄，矛盾或不确定不猜测。默认 all / overview，结构化关键参数未知、非法或显式 null 不得当作缺省。

World/DC/别名由 FF14 的只读目录提供，解析器不访问网络，也不把合成测试目录当作完整正式目录。名称中的空格、范围词和品质词需保留；文字转写仅识别明确边界，必要时要求引用物品名或显式参数。自然表达覆盖“查一下紫水栈桥的××多少钱”“陆行鸟大区×× HQ 什么价”“××国服哪里最便宜”；不确定时给提示 / 选择，不静默拆错物品或丢弃范围。

候选等待期间**固定原查询上下文**：原规范范围、品质、意图、Core/module revision 都不重读默认；选择只替换物品 ID。新查询重新捕获默认，同时使旧候选失效，晚到结果不得恢复旧批次。候选状态短期、有界、仅内存；有效期 / 容量是初始实现策略，不代表运行态验收。绑定由受信调用方提供的用户 / 会话 / 接入上下文，不能接收普通查询参数伪造身份，不扩跨平台身份迁移。缺可靠绑定时不承诺用户隔离，也不启用有状态续接；正式匿名页面如何取得受信会话绑定在 P1 明确，不能冒充已有身份能力。

**发布 / 真实迁移硬前置条件**：必须先明确新配置的合法管理或修复入口，包括可信 principal、授权 grant、revision/CAS 和非法存量修复步骤。C1 离线通过不关闭 Host/UI-C 身份接线缺口，不以 Dashboard 登录或公开查询权限替代，不扩展成通用管理后台。真实迁移、安装和运行态验收仍需另行授权。

### Q1 可复用接口与后续接线

| 本片接口 | 合同 | 后续调用方责任 |
| --- | --- | --- |
| `MarketQueryResolver.parse(parameters, defaults)` | 单一 query/server/dc/region/quality/intent 校验，返回不可变 `MarketQuery` | 提供 FF14 只读 World/DC/别名目录；不自行填另一套默认值 |
| `command_parameters` / `natural_parameters` / `structured_parameters` | 命令参数尾、明确自然语法、页面及工具结构化参数转写，之后共用同一解析器 | 命令注册、正常授权与公共 DTO 接线另片完成；不监听所有消息 |
| `QueryDefaults.capture(services)` | 只读一次现有组合配置快照，保留 Core 与模块各自 revision | 仍由合法配置服务提供权威；不读旧 Host 默认值 |
| `QueryCoordinator.resolve` / `choose` | 先使旧批次失效，再转写 / 捕获配置 / 精确优先查名；选择只能取当前候选 ID | 从受信任调用上下文提供 `TrustedOwner`，不能从 query JSON 构造身份 |
| `request_plan` | 已解析 ID 的有界接口 / 范围参数描述；不发 HTTP，不把 global 当上游参数 | S1 验证真实目录、接口、响应、总期限、并发与合并语义后才执行 |

候选策略初值为 5 分钟有效期、最多 128 个所有者、每批最多 6 个候选，仅内存保存；重启后失效，不增加数据库或身份模型。无效选择不消耗合法批次；成功选择仅一次，重放、越权、过期及旧查询迟到结果拒绝。该资源边界仅离线验证，不表示运行态吞吐或性能已验收。自然语法只在引用外识别分隔，未引用且疑似品质 / 范围修饰的输入要求明确边界，避免把关键参数静默变成名称；完整引用或结构化 query 保留名称内的空格及语法词。

### Q1 解析片离线收尾与下一依赖

最终相关 39 项全部通过（23 Q1、13 既有物品、2 展示、1 固定 Host 合同），Ruff / 格式检查 / SDK 与插件包构建通过；发布包测试 10 项通过，1 项因既有 Windows 符号链接创建权限不足而未验证。单一实现者完成源码，独立最终审查 0 Blocker / Important；独立探测 10/10 及相关 39/39 通过。用一轮修复关闭引用内“的/大区”误拆及中文邻接 HQ/NQ 静默丢失问题，没有减少断言或改系统权限。

Q1 收尾时提出的下一片 S1 已获授权，当前边界见 §10：提供真实目录到 `ScopeCatalog` 的规范化合同，接现有 HTTP 与市场来源、SDK 状态和有限结果；正式 command/capability 接线单列。P1 仍需正式公共 DTO、真实 bridge、可信候选 owner/session 绑定；不能把函数转写测试或合成目录当作这些入口的运行态验收。C1 原 262 通过、2 历史 ACL 错误保留，不重算为本批；真实迁移及上述管理/修复前置缺口仍未关闭，历史物品页面/WebChat 不自动追认为本批真实回归。

## 10. T-05-S1 当前实施边界与目录事实

用户已授权真实目录 / 市场客户端，先离线合同验证，随后必要低频官方公开源定点请求；不安装、迁移真实实例或开放临时接口。正式 Host / 命令 / 页面接线与运行验收分别记录，不能用直接源请求、客户端或 SDK 投影替代。

当前官方文档从首页实际脚本定位到 [OpenAPI v2 描述](https://docs.universalis.app/api/schema/v2)，版本 OpenAPI 3.0.1 / API v2。官方源码仍定点读取 §4 固定 commit，未改为未经记录的最新版，也不声称线上部署 commit 已知。官方目录提供 128 World / 18 DC，每个 World 唯一归属，额外地区不纳入标准 global 四区。

[World 目录](https://universalis.app/api/v2/worlds) 有数字 ID 和名称；[DC 目录](https://universalis.app/api/v2/data-centers) 只有名称、区域和 World 列表，**没有数值 DC ID**。因此只扩展 Q1 目录标识兼容：官方 DC 名称作为 DC 及 World 父归属的规范标识，World 数字 ID 保持严格校验，不生成 hash / 索引假 ID；既有解析和候选续接规则保持。DC 请求使用已校验官方名称，China 接受官方中国目录映射；区域缺项、未知归属、目录失败不构造假映射或缩减 global。

统一总期限初值 30 秒，包括目录、缓存、等待槽和同次 Q1 准备过程；同一客户端官方请求并发最多 2，无自动重试、不逐 World 枚举或创建跨查询后台合并。三意图仍按 §4 分离；挂牌每范围 6 条仅为 5 条展示与截断哨兵，有限样本不定义完整最低价或排行榜。只白名单保存价格、品质、数量、World / 区域、时间与来源诊断，公开结果不带卖家或原始异常正文。

### S1 客户端收尾、可用性与未完成接线

最终相关71/71通过（32S1与39既有Q1/物品/展示/固定Host合同），Ruff及实际构建通过；发布包10项通过、1项既有Windows符号链接权限未验证。独立8/8探测、71/71复测与包核验通过，0 Blocker / Important / Polish。使用1/2轮集中修复：合法 requested failedItems 是来源未完成 UPSTREAM_ERROR，不能推断不可交易；公开来源HTTP401/403是上游拒绝，不冒充用户凭据缺失。本地授权/凭据错误保留现有类别，真正格式/身份错误仍UNPARSED。

实采目录为128World/18DC，低频单服HQ、大区概览、China最低价及标准国际四区概览均取得200/JSON并可由开发版客户端解析。该阶段没有冻结源码SHA，不能作为最终全矩阵回归；最终代码对相同安全样本的离线重放通过，明确是离线。最终冻结版另一次44091/server1043/hq/listings客户端查询成功：目录及行情3个请求均200/JSON，SDK12blocks，5条展示、第6条判截断，来源/品质/上传与挂牌时间/数据年龄均保留。源此刻可用不等于实时、完整World上传或未来可用，旧HQ HTTPError仍未归因。

本片是源客户端与SDK结果合同，不注册正式市场入口。下一最小片是原S1任务的正式接线余项：FF14工厂/handler、只读capability和显式命令、Host必要来源标签与健康名单；不重做Q1/HTTP公共层。调用开始建立统一deadline，目录/配置快照/名称解析通过同一session.run；共享source gate，每次start_bound使用当前合法HTTP/cache服务，不复用旧请求授权。候选等待不保留后台请求，选择固定原上下文并使用新的有界执行。

P1再接正式公共DTO、真实bridge、页面市场结果与可信owner/session候选续接；不得从任意JSON伪造身份，不借临时公开接口完成验收。市场Host、命令、页面、真实Core缓存身份与运行态均未验证。实际迁移前必须有合法Host/UI-C管理/修复入口，该缺口不因C1/S1离线通过关闭。没有安装或真实数据迁移；历史ACL问题、日历内容延期、零LLM/原生运行DTO未验证和NapCat/QQ官方待环境验收保持。

## 11. 连续正式接线与 P1 集成批次（已授权）

本批在 C0/C1/Q1/S1 上继续正式查询、市场页面、必要配置管理调查、隔离集成回归及最终独立审查；不逐片等待确认。只操作隔离环境和测试数据库，不安装或修改现有实例、真实配置数据、账号或平台连接，不提交 / 发布。

市场能力与显式命令贯通同一 Q1/S1 服务，源 / 缓存仍现有授权机制；公共查询 route、JWT / origin、SDK状态和结果投影保持原边界。页面只有新 query 或结构化 item selection，默认范围由 Core 快照决定，选择固定原规范参数 / revision。只读公开 page 不承担管理权。

已确认管理硬缺口：当前 Host validator 没有独立 Core 管理凭据验证后的可信 session / generation 桥，FF14 三配置管理读取及限定回退也没有正式 Host 路由。本批按用户条件停止受影响写入口实现，不以 Dashboard身份、event角色或市场会话代替，不扩大 CLI / 管理后台。服务层合法测试与 Host拒绝分别记录，真实管理演练和迁移前提仍未满足。

候选连续性只复用现有 WEB_PUBLIC owned proof 的 bearer key，作为内部描述性 public session 由 ContextIssuer 传给模块；不从 query JSON 建身份、不公开该值、不增加权限。同一 bearer 会话共享候选空间，换 token / 重启失效，不能宣称独立浏览器标签或跨端身份。SDK可选描述字段将重新构建核对源码与 wheel pin。

最多两轮集中修复和复核，完成最终同版相关测试 / 静态检查 / 构建 / 包校验、一次全量测试及隔离 Playwright 渲染 / 交互后交付待验收候选；模拟 bridge、实采回放、隔离 Host 与真实实例证据严格分开。未越过配置管理及运行态门槛不得称可发布。

### 连续批次收尾与验收分层

正式市场 handler、read_only 能力、显式命令和 P1 页面完成；全部参数进入原 Q1/S1，名称优先、默认Core区域全服、候选固定上下文和有界 source/cache 保持。市场仅手动查询，未新增自由监听、LLM、自动刷新或管理写页面。SDK1.4 新可选会话描述实际构建，严格入口文件 pin 与 wheel 一致。UI-C 可信身份桥仍按本节条件硬阻塞，真实迁移未准入。

最终相关168个不同Python用例通过，页面/隔离Host合同、Ruff/format/实际build/包校验通过；独立审查经1轮集中修复后0 Blocker/Important/Polish。最终一次标准全量在修复R0回归后必要重跑：1217项，10失败、6错误、14既有平台跳过，保留全部16失败而不全仓清历史。前批配置/schema/固定pin与fixture错位、历史ACL、scheduler/订阅归因缺口分别记录，不称全量绿。

最终ZIP完整SHA256 `ee9b6ca030ebad308bbe092119ead242f75209af49a525faf56a5e9e0bffe91e`，159成员；SDK wheel `083f0354fe91416e871bb4833305e56d9ea8143c3ab720425aac4e51815423e7`。master/HEAD732af43e，未提交，未安装/部署或迁移真实数据，产物只称待验收候选。

最终源码用模拟事件执行一条正式44091/World1043/HQ/listings，生产source链3个公开GET取得200/JSON、SDKsuccess12blocks、5挂牌+截断；仅隔离Runtime/Core/临时DB，不是完整AstrBot或真实聊天。Playwright桌面/窄屏亮暗关键状态、键盘/重复提交/停止展示/迟到/手动重试，以及实采DTO回放通过；bridge为模拟。实际实例新版本WEB_PUBLIC/真页面/IM、零LLM/原生DTO、日历内容和两种QQ延期仍未验，旧版本成功不转算新版本。

下一最小前置是限定合法管理/修复/回退入口的可信Core凭据→Host会话证明，再授权指定实例停机一致备份、候选冷加载、四字段revision/marker和受限回退，以及真页面单次查询。当前不要直接安装到既有实例；旧包/schema兼容及后续用户写入保护须先核实，不自动整库恢复或schema降级。

## 12. 集成收尾与有限管理桥重新核定（2026-10-05）

本批只收敛上一批全量的 16 项失败/错误、只读授权审计和升级方案校正，不增加市场能力，不安装、迁移或访问真实实例。前节数字是原批历史结果；本批最终计数另行记录，不能将它们合并为同一次测试。

### 测试合同与基线归因

ModuleServices 工厂夹具提供 C1 要求的最小合法配置，另保留缺失/非法配置拒绝。安装样例 SDK pin 必须固定到实际构建的受支持 SDK1.4 wheel，不能运行时信任工作树。完整最新迁移的测试核对 0100；刻意的历史 0080→0090 测试使用该时点迁移集合，再核对后续完整升级、数据保持、完成标记和幂等，不能统一替换所有 schema 数字。

三项 FF14 订阅与 B03 scheduler 在独立目录中重建保存的批前源快照，使用相同依赖复现。正式订阅 gate 的来源、声明和 binding 是实际授权条件，合法夹具必须构造这些条件；缺 gate、伪造管理上下文、gate 关闭后失效继续拒绝。调度仓库缺 binding 时拒绝 claim 是正式 fence 行为，不能靠取消该检查或恒 True validator 让测试通过。两项 Windows ACL 错误保留为明确的维护入口环境限制，不新增 skip、不更改系统权限或删除安全断言。

### 管理身份、逐操作授权与唯一待决

`requirements.md` 要求沿用 AstrBot 账号/管理权限、不另建管理员账号；`basic-design.md` 与 SDK 的已记录合同另要求独立 Core 管理能力凭据及可信 request/session、generation、到期和撤销检查。本次核查确认其为明确文档合同，但没有据此追认未核到的原始用户批准，也不把它变成新增实施授权。

固定 AstrBot v4.28.2 / `3c7adafa1397e182d60b1016bf88759265113c8a` 有可信签名 username 和服务端 API scopes。真实 PluginRequest 能取得请求内 username，但没有普通凭据验证后的 Core 管理证明，也未公开 verified via/exp；客户端 JSON、页面上下文、asset token、public query proof、Host JWT wildcard 都不能铸造 Core 管理权。当前 Host 管理 validator 仍失败关闭。

| 有限操作 | 已有合同 | 仍需补齐的最小边界 |
| --- | --- | --- |
| Core 区域管理读取 | MODULE_SNAPSHOT、CoreConfigSummary、revision、合法值/安全 invalid 状态 | 可信、活跃、未过期且未撤销的 Core request proof |
| FF14 三字段管理读取 | MODULE_SNAPSHOT 与模块 snapshot | 仅三普通字段的安全实际值/状态及 config revision；不投影任意模块配置 |
| 四字段修改与非法存量修复 | UPDATE_CONFIG、目标白名单、新值校验、各目标 revision/CAS；显式 REPLACE/CLEAR | 每操作和效果前的 request lifetime/generation fence；未触及非法值仍拒绝 |
| 本次迁移限定回退 | 内部仓储两目标 revision、after value/行 revision、marker 同事务保护 | 正式有限 rollback operation、可信授权及同事务效果 fence；不能直接暴露内部方法 |

现 Core grant 的 generation 检查不能独自证明请求仍活跃/未过期。适配必须由服务端创建并拥有不可伪造的证明，在读取/写入效果前重验；不得把 Dashboard 登录、role 字符串或客户端 grant 当作充分条件。

只有一个待决：**A：落实已记录的独立能力凭据合同，按请求证明（建议）；或 B：明确授权四字段范围的 Host 有限委托，改变该范围的独立凭据要求。** A 沿用 Host 账号、Core verifier 与本地维护生命周期，不创建账号/凭据服务器或自动生成 key；须补有界验证、受审 transport、request-owned proof、期限/撤销/事务 fence。B 仅委托四字段操作，仍须 server-owned 身份、期限、CAS 与有限撤销；Host 账号/JWT 被盗即能操作这四字段，必须明确接受这一信任变化，不能以 JWT wildcard 放行其它管理权。当前任何方案均只到设计，未开管理路由或修改授权。

### 首次启动失败恢复是迁移硬前置

非法四字段、缺迁移输入或迁移故障发生在 Core.start 恢复模块/pump 之前；Host 会关闭 Core，配置错误还会阻止后续 initialize。已运行 Core 的合法 REPLACE/CLEAR 测试不证明首次启动失败可以修复。现维护 CLI 只管理 credential lifecycle，没有配置修复能力。

A/B 任一方向都须在迁移前补足受控恢复：或者在业务入口、模块和 pump 关闭时保留严格限定的管理基础；或者明确设计受 OS 维护 guard 保护的四字段修复入口。不能重开 cleanup-pending Core、自动扩维护 CLI 或把预检当故障恢复。限定回退须保护后续管理员同值写入、无关配置和既有订阅，不能自动整库恢复或 schema 降级。**这些路径尚未实现，真实迁移仍未准入。**

### 安装顺序校正与生命周期风险

正式 upload API 必须在 Host 运行时调用；停止整个实例后不能向它上传。固定宿主会 staging 解压并检查/安装依赖，然后尝试 terminate 旧插件、替换代码并立即 load/initialize。terminate 异常可以被宿主记录 warning 后继续；插件 namespace 清理不保证清除全局 SDK 或存活任务。安装失败恢复的是代码，不保证恢复数据库、完成标记或依赖。

本插件 initialize 当场执行 schema/四字段迁移，随后恢复业务任务；上传后冷重启不能追溯消除已经发生的热迁移或旧 SDK 混用。没有核实既有正式离线安装流程，不能虚构“停止 Host 后调用 install_plugin_from_file”。

后续方案必须先闭合管理/修复/受限回退、旧任务静止和 SDK 加载风险，才可形成可执行流程：授权指定目标的一致备份与原始四字段准备 → 按已冻结的静止方案运行 Host → 正式 upload 并处理即时生命周期/失败恢复 → 真正停止旧进程并启动新进程 → 核对 PID、代码/SDK 字节、schema/revision/marker → 分层管理与真实页面验收。停机备份与运行时上传之间如何不恢复旧任务、热 initialize 如何安全，目前仍未闭合；该流程骨架不等于安装准入，不能笼统说 disable→upload→restart 已安全。

安装前须比较指定实例的实际文件及 SDK 字节，不能只靠版本号或 HEAD。源目录替换不主动删除独立插件数据目录，但代码目录内数据会被替换；宿主代码备份不是 SQLite 一致备份。迁移后存在任何后续写入时只可按限定 CAS 回退，不自动用整库备份覆盖新数据。

### 本批冻结验收结果

本批正式增量为 10 个测试文件和本节文档，生产代码、页面、SDK 字节均未改变。原 14 项非 ACL 失败/错误在冻结全量中逐 ID 通过；SDK pin 原先挡住的 CoreHardening 类四个方法实际执行通过。新增三个负例保留缺失/非法配置、缺 gate、伪造/缺失管理证据的拒绝。历史迁移及原失败恢复、数据保持、幂等断言仍在完整发现中执行。

冻结后仅运行一次完整 Python 发现：**1224 项，1208 通过、0 失败、2 错误、14 跳过**。两错误均为 Windows 维护 guard 在祖先权限检查报 `untrusted_access`，不能验证到目标 CLI 分支；保留完整 trace，未更改 ACL。14 个 skip 与原全量逐 ID 相同，未新增跳过。用例总数变化来自解除类级阻塞后执行四个方法及三个新增负例，不是删失败或缩减发现范围。

相关定点用例联合去重为 173 通过、2 既有 skip；最终独立探测 8/8 通过。Ruff/format、实际构建及包校验通过；包用例 11 通过、1 既有 Windows 符号链接权限 skip。重建 ZIP 为 159 成员，完整 SHA256 仍为 `ee9b6ca030ebad308bbe092119ead242f75209af49a525faf56a5e9e0bffe91e`，与前候选逐字节相同；SDK wheel 仍为 `083f0354fe91416e871bb4833305e56d9ea8143c3ab720425aac4e51815423e7`。验证修订不虚构新的运行功能或版本。

HEAD 仍为 `732af43e29444c3ea68a629b3cb037aed497b9f5`，暂存区为空，全部用户/前批改动保留；未安装、迁移、登录、重启、部署或提交。管理 A/B 决定及有限读取/修复/回退、首次启动失败恢复、热上传风险仍是硬前置，产物继续称为待验收候选。本轮没有新增真实 Host/WEB_PUBLIC/页面/IM 验收；历史 WebChat 8/8 与物品真页面结果保留为历史证据，日历内容和两种 QQ 接入端继续延期。

## 13. 用户确认的 Core/Host 管理合同修订（2026-10-05）

用户已明确结束 §12 的 A/B 待决：采用 **Core 通用授权合同＋受信任 Host 管理身份适配**。AstrBot 正式登录并获准后台管理后无需额外 Core 凭据；不是原 A 流程，不自动把 JWT wildcard 或客户端身份当 Core 管理。Core 独立/其它 Host 仍用各自认证入口，原生凭据仅为可选来源。共享最小合同见 [host-management-authorization-contract.md](host-management-authorization-contract.md)，明确取代旧通用独立凭据前置。

本批只实现四字段安全读取、修改/非法修复、限定回退及启动失败的受控管理/显式恢复，保留操作/资源/lifetime/失效/CAS/事务效果验证。普通查询页继续只读；若需要 UI 使用独立有限管理入口，不能借 Host scope 扩权。缺具体 Host seam 报具体证据，不再请求另一套凭据。

真实实例不安装、不迁移、不启用委托或创建凭据；隔离离线证据不等于实际登录页面或运行态通过。部署热生命周期风险及真实迁移准入仍单独验收。旧 §12 报告数字是历史证据，本批结果将单列，不混算。
