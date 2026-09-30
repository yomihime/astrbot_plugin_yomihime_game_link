# FF14 物品来源约定（M1）

> 适用范围更新（2026-09-29）：本文件保留来源协议和分阶段证据，下方 M2 预核验的“schema 未确认/无实调”等表述属于当时基线。后来 CN 角色 API 已有成功证据，见 [获授权的实测](../../.architecture-refactor/ff14-m2-authorized-live-evidence.md)；宿主物品/日历失败尚未归因。后续按 [新计划](../next-stage-plan.md) 同环境对照塔塔露。物品主源是 XIVAPI-compatible，Garland 是补充详情，不能冒称自动回退；日历主备来源单独核验。统计网页 output 已移出本期，403 不阻塞角色 Logs；本文不派发任务。

状态：来源字段基于文档、锁定参考实现和一次有限 live probe 核对；这不保证服务持续在线、配额、所有字段稳定或真实宿主端到端可用。源模型只覆盖一期物品查询，不是完整 FF14 数据模型。

## 来源职责

| source ID | 声明 host | 用途 | 一期是否阻断能力 |
| --- | --- | --- | --- |
| `xivapi_items` | `xivapi-v2.xivcdn.com` | 中文名称候选、物品 ID 基础详情 | 是；物品名称与规范详情的主来源 |
| `garland_items` | `garlandtools.cn` | 获取途径和关联详情补充 | 否；失败时保留 XIVAPI-compatible 基础详情并返回部分结果 |

远端不可达由本次请求结果表达。Core 的 source readiness 只表示声明 host、受信任的模块包和本地受控 transport 已接通，不表示远端在线。

## XIVAPI-compatible 中文服务

`https://xivapi-v2.xivcdn.com/api` 是自称 XIVAPI **部分兼容实现**的服务，不称为 XIVAPI 原站。已核对请求形状如下：

- 名称查询：`GET /api/search`，结构化参数 `sheets=Item`、`query=Name~"铁矿"`、`fields=Name`、`language=chs`、`limit=3`。`query` 的值是查询语言表达式；SDK/Core 对结构化 query 做一次 URI 编码。模块不拼接 URL，不手工 percent-encode。
- 结果候选取自 `results[].row_id` 和 `results[].fields.Name`。多候选必须展示 ID 并让用户另行查询，不自动挑第一项。
- 详情：`GET /api/sheet/Item/{id}`，字段白名单为 `Name,Description,LevelItem,LevelEquip`，语言 `chs`。`LevelItem` 是关系对象，提取数值前读取并验证其 `value`，不把整个对象当整数。
- 页面携带不透明 `next` 时，仅在相同固定 host/path 上用该值作为结构化 `cursor` 参数续页。cursor 不是 URL，也不能改变 host/path。最多取两页、每页最多三项；仍有下一页时显式提示候选未穷尽。分页上限内无候选不能证明物品不存在。
- 名称输入拒绝控制字符、双引号和反斜线；当前没有在 reviewed query DSL 文档中确认引号/反斜线转义规则。含空格、Unicode、`%` 等普通名称内容保留为结构化 query 值交由 Core 编码。

## Garland Tools CN 获取途径补充

- 数据请求固定为 `GET /db/doc/item/chs/3/{id}.json`；用户详情链接为 `https://garlandtools.cn/db/#item/{id}`。
- 主对象为 `item`，关联对象在 `partials`。外部关系只允许依据 `(partial.type, partial.id)` 精确关联：nodes→`node`，fishingSpots→`fishing`，vendors→`npc`，drops→`mob`，instances→`instance`，quests→`quest`。相同 ID 的其它类型不能替代。
- `craft`、`tradeCurrency`、`tradeShops` 是 `item` 内嵌组；材料/货币只有在匹配 `(item, ID)` partial 后才展示关联名称。未解析时仍可显示明确 ID 和数量并标为部分信息，不猜名称。
- 一期识别以上九类字段，每组最多展示五条、单条最多八项成本、总途径最多二十四条。字段或关联缺失、列表截断、来源请求失败时标部分并给 Garland 链接；无记录不等于游戏内不可获得。

## 错误与展示规则

- XIVAPI-compatible 为名称搜索和基础详情主源。HTTP 429 映射为限流，数字 ID 详情 404 映射为未找到，其它来源错误映射为上游不可用；搜索端点 404 不伪装成“没有匹配项”。
- 直接数字 ID 查询不经过名称搜索。XIVAPI-compatible 详情成功而 Garland 失败时仍回基础物品资料，标为 `PARTIAL_SUCCESS`；主来源失败则不拿 Garland 单独响应冒充完整物品详情。
- 不要求图片；不返回原始 JSON，不缓存或打包上游物品数据。用户结果带来源链接，缺少字段采用“未提供”或部分结果描述。

## 证据、许可和边界

有限探测记录见 [`.architecture-refactor/ff14-m1-source-evidence.json`](../../.architecture-refactor/ff14-m1-source-evidence.json)：2026-09-28 一次中文名称搜索、XIVAPI-compatible 数字 ID 详情和 Garland 同 ID 详情均返回 HTTP 200。它只证明当时三个请求可访问及所记少量字段形状；不是生产运行时、宿主链路、稳定性或长期可用性验收。合成 fixture `tests/fixtures/ff14/items.json` 不含真实玩家数据、完整上游条目或图像。

Garland 页面/API 与项目代码、XIVAPI-compatible 服务及游戏数据库/图像各自的许可范围不能互相推定。本模块当前仅随包分发自有解析代码、来源链接和合成 fixture，不随包分发来源数据库或图标。锁定参考仓库 [jawwe/astrbot_plugin_tataru `704578d`](https://github.com/jawwe/astrbot_plugin_tataru/tree/704578d8479105e016de0474eaa72395d0806de8) 用于辨认字段线索，未移植其代码、字典、线上数据或图片；若以后复制或实质改写其代码，必须在产物保留该 commit 的完整 MIT license 和 copyright notice。参考许可证：[MIT LICENSE](https://github.com/jawwe/astrbot_plugin_tataru/blob/704578d8479105e016de0474eaa72395d0806de8/LICENSE)。

## 参考文档

- [XIVAPI-compatible 欢迎与差异说明](https://xivapi-v2.xivcdn.com/zh-cn/docs/welcome/)
- [XIVAPI-compatible 搜索、cursor 与 URI 编码说明](https://xivapi-v2.xivcdn.com/zh-cn/docs/guides/search/)
- [XIVAPI 原站搜索指南](https://v2.xivapi.com/docs/guides/search/)
- [Garland Tools CN](https://garlandtools.cn/)
- [GarlandTools-CN 源码仓库](https://github.com/ClayLivince/GarlandTools-CN)

## FFLogs 来源（M2 预核验）

本节是 FFLogs 实现前置，不表示 FFLogs 能力已启用。2026-09-28 对 FFLogs 官方文档的直接打开仍返回 403；下列 schema 结论来自搜索索引呈现的同一官方页面，属于**官方文档索引证据**，不是对实时 API、凭据、响应或国服覆盖的验证。每项链接均指向对应官方页面。独立调查记录见 [M2 来源交接](../../.architecture-refactor/ff14-m2-source-handoff.md) 和 [M2 合同提案](../../.architecture-refactor/ff14-m2-source-contract.md)。

### 两条数据路径

| 能力 | 来源与已知合同 | 限制 |
| --- | --- | --- |
| 公开角色战绩 | [FFLogs API 文档](https://www.fflogs.com/api/docs)索引说明客户端凭据使用 OAuth `client_credentials`：HTTP Basic 换 token，再用 Bearer 调用 `/api/v2/client`。公开角色通过 [`characterData.character(name, serverSlug, serverRegion)`](https://www.fflogs.com/v2-api-docs/ff/characterdata.doc.html)查找；[`Character.zoneRankings(...)`](https://www.fflogs.com/v2-api-docs/ff/character.doc.html)接受 metric、zone、difficulty、partition 等筛选，并返回未冻结的 JSON。 | 官方页面直开 403，无凭据实调；`zoneRankings` JSON 被文档标为可变化，精确响应字段仍 `NOT VERIFIED`。不能把搜索摘要当成功查询。省略 zone/difficulty/partition 时文档描述的 latest/highest 默认也须在实际行为验收；不得请求 `includePrivateLogs` 或 user endpoint。 |
| 输出分位统计 | 锁定 Tataru 参考在单独的公开 HTML `/zone/statistics/{quest}` 与 `/zone/statistics/table/...` 页面取数；它按 `dataset` 请求不同分位，并可传 `dpstype`。这不是 `zoneRankings` 的 GraphQL JSON。 | 当前 HTML DOM、脚本格式、周期、地区筛选、样本计数及列定义均未验证。旧参考中的启发式解析器不能作为本模块实现或启用依据。markup 未核验时返回不可用/降级，不显示猜测的数值。 |

### 区域、服务器与动态元数据

- [WorldData](https://www.fflogs.com/v2-api-docs/ff/worlddata.doc.html)文档索引公开 `regions`、`region(id)`、`server(id, region, slug)` 和 `zones(expansion_id)`。[`Region`](https://www.fflogs.com/v2-api-docs/ff/region.doc.html)的 `id/name/compactName/slug` 与 `servers(limit,page)` 已见于索引；servers 每页参数范围为 1 到 5000。返回的 [`Server`](https://www.fflogs.com/v2-api-docs/ff/server.doc.html)有 `id/name/normalizedName/slug/region/subregion`。地区及服务器名须由这些目录记录映射，不能本地改写 slug。
- 官方索引没有给出 `ServerPagination` 的字段列表。不得猜 `data/hasMorePages/total` 等成员，也不得把一页长度小于 5000 当作全集；owner 可用受控 GraphQL `__type` introspection 或真实响应确认其字段和分页终止规则。确认前离线候选 parser/合成测试可继续，但目录内容必须呈为 `UNPARSED/UNAVAILABLE`，不能据单页自动映射服务器或声称目录完整。
- 官方索引可确认 `WorldData.zones(expansion_id)` 入口，但没有给出 FF 专属 `Zone`、`Difficulty`、`GameClass`/job 字段和实际响应。未从 FF 专属页面核实职业目录查询；其他游戏页面的字段不能外推到 FF。允许按已固定的 FF schema 事实制作候选 parser 与合成样本；未核验字段保持 `UNPARSED/UNAVAILABLE`。运行时必须从经核验的 FFLogs 元数据选择有效 ID/slug；不打包硬编码副本、难度、职业目录，也不把名称翻译表当 API ID。
- 用户显式选择“国服/国际服”和可读服务器名。仅当目标 host 的授权、目录里确有相符地区/服务器，且能无歧义映射成 API 所需 `serverRegion/serverSlug` 时查询；国服 API host/region 支持、国际服 region 值、大小写和规范名对照都要真实核验。不得跨 host 回退、猜 region 或捏造候选。

### metric 与启用边界

[CharacterPageRankingMetricType](https://www.fflogs.com/v2-api-docs/ff/characterpagerankingmetrictype.doc.html)官方索引枚举包含 `rdps`、`ndps`、`cdps`、`dps`、`hps`、boss 口径及组合排名等值；这只说明 schema 有这些枚举，不保证每个副本/分区支持它们。FFXIV rDPS/nDPS/cDPS 的解释见 [FFLogs metric 说明](https://www.fflogs.com/help/rdps)。M2 规划默认建议 rDPS，属于未由用户确认的产品默认；实际查询必须校验目标内容是否支持，失败时明确报告不支持，不能换另一指标后仍标作 rDPS。

角色战绩和输出分位是两个可独立降级的能力。可先按固定官方 schema/上游行为做候选 parser 与合成测试；未知 JSON 字段或 HTML markup 必须显示 `UNPARSED/UNAVAILABLE`，不得猜数。公开 GraphQL token、角色查找/服务器完整目录映射与实响应结构通过后，才启用角色战绩；统计页需额外通过当前 HTML markup、分位列与周期/地区口径的真实验收，才启用输出分位。Core 的受控 HTTP、secret reference 与 OAuth token 交换/刷新提供传输和凭据机制；FF14 模块只处理公开 GraphQL/统计页领域参数，不直连网络、不读取 secret、不调用私密 user API。候选实现或合成 fixture 不算生产 PASS。

离线 fixture `tests/fixtures/ff14/fflogs.json` 全部为合成值；测试只守住“证据标记、查询边界、未核实字段保持空缺”的约定，不测试 FFLogs 的真实 schema、认证或数据。
