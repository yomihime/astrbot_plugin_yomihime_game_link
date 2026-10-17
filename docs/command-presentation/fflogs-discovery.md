# FFLogs 目录与中文名称探查

## 当前策略：标准显式历史分区与精确整行选优

在此前单正分区样本之外，本轮固定同一角色、地区、zone、难度、rDPS、`Historical` 和省略职业筛选，仅变化六个目录确认的标准 partition。六次均成功，但仍只有一个 partition 有百分位，因此没有发送以多正样本为前提的额外 `-1` 对照。两个单正样本不能证明 `-1` 在多个正 partition 中选择最高原始百分位、仅包含标准组成，或能给出每行可靠记录时期。

当前使用必要的标准显式历史 partition，不同时重复默认与 `-1` 查询。保留所有规范历史 zone / difficulty，按来源完整名归组、原始百分位精确择一整行；比较使用 `Decimal.copy_negate()`，不转 float、不先格式化，也不修改全局 Decimal context。真正同分仍按可靠时期或原稳定次序，资料片名称仅说明来源时期，不把 selection / partition ID 冒充 patch。职业、rDPS 和次数沿用选中范围，不跨期相加或宣称各字段必然同场。

请求身份包含 zone、difficulty、requested partition、metric、timeframe、职业筛选，并由已验证角色、地区、source 与目录指纹隔离。省略 partition、`-1` 和正 partition 不互抵完成状态；`-1` 允许上游实际回显正 partition，显式正 partition 仍严格匹配。未知标准标签保留未查询，不用默认零式响应覆盖绝境占位。零式仍仅来源目标资料片的八人零式，目录没有合格零式才回退，角色没有成绩不触发回退。

通过现有受管缓存复用完整已验证范围，动态小组仍是结构化对象，沿用 512 节点 / 64KiB、900 秒及每范围获取时间。目录决定稳定分组，不用响应偶然行数或获取时间改变键；当前 owner / grant、源、身份与生产 parser 继续校验。缓存预读、剩余读取、写入和取消均有界，实际请求才推进游标。每次 48 新范围、每批 12、HTTP 并发 2、排名 15 秒 / 外层 30 秒不变，未发范围不判为无成绩。

当前目录的离线快照为 108 个绝境标准历史范围与 3 个零式范围，分为 15 个合法缓存条目；这些计数不是生产名单或固定上限。受控真实 CacheAccessService / SQLite 验证跨调用 48 / 48 / 15 后复用全部范围且不再发排名请求。正式 Host 的两张修后新图则仍是部分范围：最终图片的已验证缓存与本次数据中包含旧时期成绩，同时有未覆盖和未保存提示。真实保存失败原因未知；不能将已发出请求、当前有效缓存、当次成功、当次无记录和未查询混写成累计完成。未保存范围需要后续重新取得，当前选优不宣称包括所有先前但已不在本次有效证据中的结果。

**跨调用不保证保留全部已查高分，展示值可能下降。** 这次真实前后图片已观察到先前较高结果未进入下一次有效比较；该范围未保存或不可用的具体原因缺少逐范围遥测，仍未知。仅当范围重新取得或有效缓存可读时才能恢复其比较，不能把两次曾发出的范围总数当作最后图片覆盖数。

源码与受控回放、真实 API 语义、Host 发送、图片逐项内容以及完整历史覆盖是不同证据。最终 source / test-only 门槛独立 132P、包审通过；旧失败、原首图历史推进失败和有限真实范围继续保留。[当前交接](README.md) 记录固定候选、真实用量和未完成边界。以下是此前的策略与失败，不覆盖本节。

## 历史查询语义与选定方案（2026-10-17，接续 b30986c）

`zoneRankings(partition: -1)` 的官方范围是所选 zone 的全部 partition；它不自动跨越其他 zone。省略 partition 的默认范围、显式正 partition 和 `-1` 必须区分。`Historical` 是排名比较基准，与 partition 覆盖范围分开；本次受管 schema 返回 `Today` / `Historical`，默认 `Historical`。[Character 文档](https://www.fflogs.com/v2-api-docs/ff/character.doc.html)

一个已知旧来源的有限对照固定角色、地区、zone、难度、rDPS、全职业和 `Historical`，仅改变 partition。修后四次受管调用成功：`-1` 与唯一有百分位的显式历史 partition 整行一致，其他两个范围返回空百分位及来源字面零值。本次同时证实 `-1` 请求可返回正数 `selection.partition`；不能要求 JSON 回显 `-1`。请求与响应范围分别保存，显式正 partition 仍须严格匹配；zone、难度、指标、身份、职业及输入预算校验不放宽。初次解析拒绝及补验分别保留，真实数据只留本地。

选用最小方案：每个目录确认的独立历史 zone / 难度请求一次 `-1`，跨来源按原始百分位选择整行，表述为“已查询来源返回的最高百分位”。不同时展开该来源的默认与全部显式 partition，不加逐版本 ID 名单。零式仍为目标资料片的八人零式、默认 partition 范围；与绝境的 `-1` 请求不等价。完成状态按完整计划请求及其结果记录，不能以“该 zone 已请求”或实际返回分区代替另一项请求完成。

单样本只有一个正 partition，**未证明**上游面对多个正 partition 时始终取最大值、`-1` 仅含标准组成，或 `selection.partition` 属于每行最佳字段。行时期只使用可靠的来源资料片，不由响应 partition 推 patch；最佳百分位、rDPS、职业与次数仍按来源范围保留，不宣称同场或跨期累加。客户端原值择优的受控测试与上游算法保证是两层证据。

语义与参数校验修正已完成真实 API 对照，并在指定实例保持启用。A 的离线实现、请求身份 / 覆盖回归及生产 PNG 检查已完成，但最终独审有 1 Important：Decimal 取负排序会在默认 28 位精度下舍入，违反解析器允许范围内的原值择优要求。这是继承的比较器问题，不能因真实样本精度较短而放行。返修额度耗尽，A 冻结、不安装；正式 Logs 命令和实际图片验收未执行。原始拒收源码、反例和离线图只留本地，公开交付仅保留已审参数修正。

既有 marker 不记录 timeframe，固定 `Historical` 由冻结 query 与受控 wire 测试证明，不冒充运行时 marker 独立观测。下面 R1 / R2 覆盖记录属于上一轮，不能当作新方案已交付。下一步只建议在新增授权后采用不受上下文精度影响的比较，并保留整行及稳定同分回归；不改变输入精度预算或转浮点绕过。

## 本轮选择合同与实际边界（2026-10-17，接续 dd9f804）

用户已明确采用来源命名和人数规则恢复实用聚合：zone/encounter 完整名中的“绝境”/Ultimate 加规范归属生成绝境候选；有限 Unicode/空白规范化全名归组，保留所有独立 ID，不使用模糊匹配、简称或逐版本名单。每组查询已识别历史时期，以未截断原百分位选一整份结果并标来源时期；失败或未查询不能冒充完整历史最佳。

零式采用 source 默认 zone 所属资料片及其来源名，以真实零式 difficulty、目录人数包含八人和响应 selection 难度/人数复验共同过滤。普通、四人、四十八人不展示；角色无零式成绩不能用普通补位。只有目标资料片目录无合格零式时才按已核来源资料片顺序回退，不要求先证明独立地区游戏开放状态，也不外推这个状态。保留来源目录顺序和不同 encounter 标识的门神/本体。

本轮正常受管命令已取得 `Difficulty.sizes`、`Zone.frozen`、`Zone.partitions` 及 Partition 名称/默认标记的实际值；不再只凭 schema 存在推断值。74 个 zone / 5 个 expansion 的最小匿名投影及分批成绩完整重组，目录人数、实际 selection 和规范归属分别核对；八人零式保留，四人、四十八人及普通范围排除。闭合诊断只扩已证字段、分批安全数值筛选及本层 fetch 汇总，不增加认证、任意 query 或管理代理，也不外推地区完整开放、游戏 patch 或底层请求数。

R1 实际长图与源数据对照通过，但查询仅 48/219 个目标，171 未查；绝境只覆盖 3/10 个来源 zone、7/17 个独立 encounter ID。整行择优只代表已查询范围。R2 独立来源优先优化在实际目录正例通过，混合类别共用同一 zone/difficulty 的受控负例却会遗漏默认零式目标；最后独审 1 Important、两轮额度耗尽，候选未安装，保留 R1 源码与运行现场。修复标准必须按完整 `(zone,difficulty,partition)` 分辨目标是否覆盖，不能仅凭合并后的来源覆盖桶。详情见[当前交接](README.md)。

下方“主目标阻塞、暂停聚合”是前一批按当时产品要求的结论，本轮按本节明确约定实施和分层验收；历史原始失败不改写。

## 2026-10-16 实际受管响应结论

一次已授权的正常两参数 Logs 命令取得 CN source 的 schema、74 个 zone / 5 个 expansion、默认成绩及旧选择的别名成绩。通过固定白名单记录操作、非敏感筛选、时间、模块缓存标记、目录和最小成绩投影；无认证、角色身份、服务器或报告 URL。完整脱敏 JSON、逐项选择/排除表和原始成绩只留本地，不提交。这里只公布结构与结论。

- 实际 `Zone` 有 `id/name/expansion/difficulties/encounters/partitions/frozen/brackets`，`Encounter` 有 `id/name/zone/characterRankings/fightRankings`。已查字段没有内容类别、共享游戏副本 ID、地区发布状态或可靠排序键。类型存在不等于已取到实际值，未知嵌套字段不声称缺失。
- 相关绝境目录的难度为 `100 / 普通`，旧 Ultimate/绝境战难度精确匹配因此全部排除。本次旧选择只有七个目标，缺组并非十二目标截断造成。
- 同默认 zone 的 expansion 加 Savage 标签不足分类，实际会纳入四人和四十八人内容。默认 zone 所属资料片也不能证明查询地区当前开放资料片。不能用最大 ID、名字关键词或逐版本人工表修补。
- `Difficulty.sizes` 的 schema 可见，但人数只能排除部分非目标，仍不能证明绝境类别、地区当前资料片及发布回退；因此没有为试探人数再消耗一轮请求。
- 实际来源返回中文名称，只能证明此次响应的名称；未证明通用 locale 选择机制。版本字段和两个最佳指标的记录关联仍不能凭目录或 partition 名推定，过本次数只指来源对应范围。

**P2/P4 仍阻塞**：需要可信机器可读内容类别及稳定关联、地区开放依据和排序/规范去重证据。默认命令停止不可靠自动扩展，保留来源本次真实返回范围及明确不完整提示；不假造两大类或未知目录空行。此前下方“未取得真实响应”的描述仅是历史时点，不覆盖本节。

## 目标

用已有授权的只读 API 证明一个低维护目录规则：全部已发布绝境战、查询地区当前资料片所有已发布零式 tier，以及仅在当前资料片零式未发布时的上一资料片回退。

先核验再实现。不能把最新 zone 当成当前资料片全部内容，也不能因角色无记录、隐藏或查询失败改选旧资料片。

## 已知与未知

- 官方 `WorldData.zones(expansion_id: Int)` 可按资料片查询；省略参数取得全部资料片的 zone。这只是目录入口，不自动解决分类、发布状态或排序。[WorldData 文档](https://ja.fflogs.com/v2-api-docs/ff/worlddata.doc.html)
- 官方 `Encounter.name` 被描述为本地化名称；这并不说明本项目 v2 请求如何选择中文。[Encounter 文档](https://www.fflogs.com/v2-api-docs/ff/encounter.doc.html)
- 现有目录实现会先检查 schema，再查询 `zones { id name difficulties { id name } encounters { id name } }`；可以复用这种有界、先验形状校验的方法。[目录源码](https://github.com/yomihime/astrbot_plugin_yomihime_game_link/blob/8251a52580b8839964f8f232d45706a98c1fd02d/modules/ff14/features/fflogs_catalog.py#L40-L93)
- 现有角色查询是默认或指定一个 zone 的 `zoneRankings`，不是“所有目标副本聚合”。[角色查询](https://github.com/yomihime/astrbot_plugin_yomihime_game_link/blob/8251a52580b8839964f8f232d45706a98c1fd02d/modules/ff14/features/fflogs.py#L56-L82)
- `fflogs_public_cn` 与 `fflogs_public_global` 已分开声明；现有线上验证范围不能直接外推到其他地区。[源与地区常量](https://github.com/yomihime/astrbot_plugin_yomihime_game_link/blob/8251a52580b8839964f8f232d45706a98c1fd02d/modules/ff14/features/fflogs_catalog.py#L26-L37)
- 历史检查提示 Ultimates/Legacy 中同一逻辑副本可能对应不同 zone/encounter ID，zone 43/59 是需复核的回归样本，不是新的硬编码分类规则。

尚未确认：可用的内容分类字段、资料片与发布时间字段、地区发布状态、可信顺序、重复目录的规范来源，以及 v2 中文选择机制。官方文档说明应与实际认证响应一起验证，不能只凭网页文案作生产保证。

## 认证与数据保护

- 仅使用已有 Host source 和既有授权上下文，不新建 API 应用、持久凭据或扩大 grant；不导出或记录 Host 内部 token
- 不把凭据放进聊天、命令参数、源码、夹具、截图、请求日志或异常文本
- 公共成果仅保存 schema 名称、字段类型、非敏感目录 ID 和最小脱敏响应
- 本阶段不需要真实角色查询；不得采集个人战绩来证明目录结构
- 认证不可用时记录具体能力缺口，暂停在线探查，继续可独立完成的离线工作
- 只做已支持的读取；不要为了中文试探额外域名、改认证区域或扩大权限

## 探查步骤

### 1 确认 schema

- [ ] 在相应 source 上确认 `WorldData`、`Expansion`、`Zone`、`Difficulty`、`Encounter` 的实际字段与参数
- [ ] 查明资料片归属、内容类型、发布状态或时间、稳定排序是否存在机器可读字段
- [ ] 记录字段的 nullability、枚举值与列表顺序是否有明确保证
- [ ] 检查 `zoneRankings` 的参数与返回结构；请求次数、响应字节、别名数量和时间预算均有上限

只查询 schema 已证明存在的字段。不要把“想要的字段”直接放进生产 query，也不要用异常回退掩盖 schema 不匹配。

### 2 查询目录 验证通用规则

- [ ] 获取必要资料片与 zone 元数据，保留 source 和地区信息
- [ ] 核验绝境战/零式分类是否可以由明确字段推导，而非名称包含某个词
- [ ] 核验当前资料片在查询地区的发布状态；未来公布但未开放的内容不得混入
- [ ] 覆盖同资料片多个 tier，证明规则不会只选最新 tier
- [ ] 复核 zone 43/59 等历史样本，通过 schema 与真实目录证明逻辑副本的关联及去重规则；不假设跨版本 encounter ID 相同
- [ ] 证明排序不依赖猜测 ID 大小、英文名称字母序或 API 未承诺的返回顺序

如果不能从已验证字段形成通用规则，输出缺口与影响，暂停 P4。不得悄悄加入随补丁维护的分类表，也不得用“全量 zones 已拿到”宣称自动目录已完成。

### 3 核验中文名称

- [ ] 使用已有 source 查询同一 encounter，记录实际名称与明确支持的 locale 参数或机制
- [ ] 不假设 CN source、网页语言或 `Accept-Language` 就决定 GraphQL 的名称语言
- [ ] 没有经过验证的中文机制时保留原名，并测试长英文名换行
- [ ] 如果考虑其他中文游戏数据源，先证明共享稳定游戏 ID 与覆盖范围

FFCafe/XIVAPI 兼容中文源只能作为候选。它有中文游戏数据不等于能直接用 FFLogs encounter ID 查询；没有可靠 ID 关联就不接入。禁止相似名称匹配、LLM 翻译和长期人工短名/翻译表。

### 4 核验聚合与预算

- [ ] 确认“最高 rDPS”和“最佳百分位”在响应中的真实含义，以及两者可能不属于同一场
- [ ] 核验两项最佳的职业/版本/分区范围；决定同职业约束或分别标注来源，避免职业标签错误归属
- [ ] 验证空记录、隐藏、部分错误、请求失败与未发布的区别
- [ ] 先筛选有限目标 zone，再使用批量别名或有界并发；禁止遍历全部历史 zone 发起无界请求
- [ ] 定义元数据与角色结果的缓存键、时效和失败行为，保留地区与指标隔离
- [ ] 整体查询遵守有界 30 秒目标；超时如实给出失败或部分成功，不伪装完整
- [ ] 用合成 20 行及增长夹具实算 facts 512 节点、渲染 32 blocks 与图片大小预算

## 探查交付格式

提交一份短结论，包含：

1. 日期、代码 commit、实际核验的 source/地区、只读操作范围
2. 已验证字段、规则和最小脱敏证据；每条结论的适用范围
3. 中文机制、分类、发布状态、排序、去重分别是已验证、缺失还是未验证
4. 拟采用的聚合与缓存边界，接口/配额/时间上的风险
5. P4 可以开始，或必须先解决的具体决策缺口

这里的 schema、目录和聚合检查项全部待执行。2026-10-15 续作只完成技术入口与宿主只读前置核验，当时未取得可用的正常受管 Host/source 上下文，没有执行 FFLogs 上游请求。

### 2026-10-16：正常管理入口与纯元数据合同仍分离

正常 Dashboard 登录、插件元数据读取和默认 monitor 页面已取得证据。这不能证明 FFLogs source 调用权限或 OAuth 可用。当前管理操作的凭据状态仅返回配置 revision 和 configured / unset / unusable 元数据；`READ_CONFIG` 不能自动授权上游请求，本批未额外执行该状态读取。

现有 `FFLogsCatalog.load_output_metadata` 经受管 SourceHttp 检查固定 schema 后取得目录，但只有输出百分位业务调用它。该业务还会抓取七组统计页，且健康状态标为不可用；不能通过刻意构造不匹配名称来借用业务错误路径探查目录。当前没有单独的纯元数据正式入口，也没有通用 SourceHttp / GraphQL 管理代理。[现有目录实现](https://github.com/yomihime/astrbot_plugin_yomihime_game_link/blob/4fc65beeef0c5b7adf23d24acb34a0a2edebb55d/modules/ff14/features/fflogs_catalog.py)，[业务调用者](https://github.com/yomihime/astrbot_plugin_yomihime_game_link/blob/4fc65beeef0c5b7adf23d24acb34a0a2edebb55d/modules/ff14/features/fflogs.py)，[管理操作](https://github.com/yomihime/astrbot_plugin_yomihime_game_link/blob/4fc65beeef0c5b7adf23d24acb34a0a2edebb55d/adapters/astrbot/admin_pages.py)。

后续最小内部合同须先独立审定：由 FF14 拥有固定地区和 schema / catalog 阶段，不接受任意 URL、header 或 query；通过真实 module invocation 与既有 source 绑定执行，不能从管理处理器直接取得私有凭据服务。先使用已审固定 schema，证实字段后才增加固定投影；核清整体 deadline、内部 OAuth / 重授权请求预算、取消与关闭围栏。输出保留现有 facts 512 节点和 32 blocks 上限，超限如实处理，不默认引入无限分页。每个顶层元数据执行都计入获准的真实能力预算，不能藏作配置读取。此提案未实现，也未更改 SDK 公共合同。

现有角色解析没有已验证的 `totalKills` 合同，`size` 不能解释为过本次数；JSON scalar 内部字段也不能仅靠 GraphQL 类型名证明。分类、区域发布、中文机制、排序、去重、过本次数及两个最佳指标范围仍未验证，P4 继续阻塞。本批没有 FFLogs 上游请求、真实角色查询、凭据导出或权限扩大。

公开文档与原型不能替代认证元数据探查；不把私人认证响应原样提交到仓库。
