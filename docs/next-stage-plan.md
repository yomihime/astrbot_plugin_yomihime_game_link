# 如月怜的游戏连结：后续执行与验收计划（FF14/配置/页面）

> 本文档是 W1 后续工作的唯一新入口和执行计划，不代表后续功能已经实施或验收。初始状态基线截至 2026-09-29；最新执行记录更新至 2026-10-03，见 §14.11。实现前先复核本文列出的代码、宿主和来源事实；不要重开已验收的 W1 Astra 审查。

**W2 当前状态：EXECUTING / B2 候选404c已通过HTTP API安装到本机19266实例；安装与公开查询入口已联调，物品部分成功、日历成功，角色Logs因实例未配置凭据而未完成；视觉/交互未验。** 用户于 2026-10-01 明确要求开始执行本计划，基线为干净工作树 `0a20110`。D0隔离宿主、B0b Core、B0c Host和B2最小链路局部验收记录保留；755c旧包已由404c/SDK1.4替换。同包隔离TC06-B的历史凭据验证不等于当前用户实例已配置。GPT-6.1-Sol按§9.3和四角色协议逐片执行；W2 Astra最终验收仍0/1。仅对本仓库和获准本地测试宿主实施，不改Host源码/共享环境，不部署用户远端实例；凭据、运行数据与临时证据不提交。最新执行调整和证据见§14.11；旧章节状态是历史快照，不代表当前全部功能已通过。

导航：[需求与 UX](#requirements-ux) · [配置设计](#config-design) · [页面设计](#page-design) · [测试设计](#test-design) · [阶段与调度](#execution-stages) · [协作协议](#collaboration-protocol) · [模型分工](#agent-models) · [文档接续](#document-inventory)。

## 1. 目标与范围

在现有 FF14-W1 本地实现基础上，按一个完整、可复核的真实宿主会话补齐长路径、物品、公开角色 FFLogs、日历与本人订阅的目标验收；解决由这些验收直接证明的缺陷。同步确定 AstrBot 插件配置项和插件页面的设计与可实现边界。

本轮计划中的 FFLogs 产品范围只有公开角色 Logs：角色查找、公开战斗/副本排名，以及来源实际提供的 rDPS 数值与排名百分位。当前角色样本为国服 **如月怜 @ 潮风亭**。角色结果使用 FFLogs GraphQL/角色资料路径；不请求、不解析、不回退到 `statistics/table` 或统计 HTML 页面，也不开放人口统计 `output` 能力。截图中的绝本和零式排名用于确认展示类别；数值按目标查询时返回的新鲜数据核对，不要求跨时间快照数字不变。

同一后续 wave 覆盖物品精确检索、活动日历查询、显式本人订阅 CRUD 与推送链路、真实 AstrBot 4.28.0 Windows 长路径安装/重启检查。UI 先冻结信息架构、字段、权限和实现路线；原生配置表单可以按确认后的宿主 schema 实施，专用管理页必须先通过 §6 所列的宿主 API 与授权门槛。

<a id="requirements-ux"></a>

### 1.1 需求与 UX 基线

目标用户分为聊天查询/订阅用户、插件运维者和 Dashboard 浏览者；第三类身份不能自动获得前两类的个人数据或管理权限。核心任务是查公开角色、找物品、查看活动、管理本人订阅以及配置/诊断插件。§5–6 的具体布局与普通字段是设计建议，由 root 在既定范围内冻结，不冒充用户逐项批准的功能。

以下编号稳定保留。P0 是主能力或安全验收前提，P1 是本轮配置/页面设计及后续交付要求；P1 不代表可以免验。新增/修订能力的当前执行状态见 §14，历史 W1 证据仍只覆盖原工件。§8 的 TC 是详细步骤，本表不另建重复测试体系。

| 编号 / 优先级 | 来源与要求 | 依赖 / 验收条件 | 计划实现位置 → 验证 |
| --- | --- | --- | --- |
| REQ-01 / P0 | 用户要求：CN 公开角色 Logs，样例如月怜@潮风亭；统计网页不做 | 凭据经 Core 正式路径；显示来源实际提供的副本/职业/rDPS/排名口径和时间，缺值不编造；直连成功不能替代插件通过 | FF14 `features/fflogs.py`、Core 来源服务 → TC06–07 |
| REQ-02 / P0 | 用户要求物品查询；ID 44091 来自给定样例。中文主源与 Garland 补充是现有来源合同 | 名称/ID 一致，多候选人工选择；主/补字段标来源，图标失败仍有文字；失败不显示成零结果 | FF14 `features/items.py`、`item_sources.py` → TC04–05 |
| REQ-03 / P0 | 用户要求活动日历与推送；用户指出备用源不可靠 | 查询窗口/时区/采集时间明确；来源分别核验，无活动、部分、过期、失败可区分；未验证备用源不自动启用 | FF14 `features/calendar*.py`（职责描述，非扩大的写入白名单）→ TC08–09 |
| REQ-04 / P0 | 日历推送功能来自用户需求；收件目标来自可信私聊 actor/conversation，页面不管理个人订阅沿用当前权限边界 | 可信 actor/owner、CAS、重启、去重、取消及 UNKNOWN 不重发；实收证据单列；不接受任意收件人 | FF14 `calendar_subscriptions.py`、既有 Core 调度/投递 → TC10–11 |
| REQ-05 / P0 | 用户安装诉求与 LongPathsEnabled 报告；现有长根失败证据 | 新进程、历史长根、固定包完成安装/可信加载/重启/help；静默无模块应有明确诊断 | Windows scanner、AstrBot adapters → TC01–03 |
| REQ-06 / P1 | 用户要求配置设计；唯一存储权威/加密引用是当前架构约束 | 普通配置走原生表单保存/重载；每区一个敏感 alias；授权与 CAS、KEEP/REPLACE/CLEAR、失败恢复有证据 | 拟议 schema/config adapter、既有 Core 配置服务 → TC12–13 |
| REQ-07 / P1 | 用户要求页面方案；五页 IA 为本计划设计建议 | 正式 bridge、最小公共 DTO；网页查询/管理入口各自获准才启用。未接通时指引聊天，不计页面查询通过 | 拟议 `pages/ff14/`、宿主 page adapter → TC14，按 UI-A/B/C 分判 |
| REQ-08 / P1 | 用户本轮协议要求状态、主题、窄屏、语言和键盘；文本优先为现有设计原则 | 简体中文、320px、亮暗主题、键盘焦点/label、长文本、图标失败文本降级；页面主题不改变聊天输出 | 页面组件、既有 Core presentation → TC14–15 |
| REQ-09 / 可选边界 | Global 可选沿用当前范围，不阻断 CN；统计页、私人报告、其它游戏不做 | 未验证能力明确标注，不能以空结果表示已支持；不扩大数据/权限范围 | 清单、入口与页面能力状态 → TC07–10/14 |
| REQ-10 / P0 准入 | 待验证假设登记：公开 Web 准入、管理 principal 映射、原生配置跳转、来源映射 | W2-0 逐项列现有接口/缺口/负例；缺失保持 BLOCKED 或指引，不伪造 COMMAND/owner/admin | Core/host 合同和 §12 → TC06-B/08/13/14 |

信息架构沿用 §6 五页文字线框：概览回答“能否使用/下一步”；Logs、物品、日历各完成一个查询任务；设置区分普通项、受保护凭据与危险清除。首次进入只呈现已知状态和必要引导，不自动扫描外网。常用查询先于高级诊断，危险操作不与主按钮同等突出。

关键流程：①入口→选区服/输入角色→必要时消歧→查询→看口径/来源→调整或重试；②名称/ID或日历窗口→候选/筛选→详情→明确空、部分或来源失败→调整参数/重试；③私聊本人订阅→查看当前 revision→变更/取消→回执，而运维在原生表单修改普通设置或经独立授权替换/清除凭据。凭据失败清空敏感输入，普通非敏感草稿可保留；冲突先读新 revision，绝不自动覆盖。

最小假设：简体中文优先，UI 字体沿宿主，五页和布局可逆；无需逐项向用户确认。权限、隐私与迁移缺口不能用最小假设补齐。需求变更由 root 记录原因、REQ 编号、影响用例与切片版本，不为适配已有实现降低验收标准。

## 2. 非目标

- 不重开 W1 的 Astra 审查，也不因 FF14 后续验收把 Core Ready 标为 OPEN；全局状态继续由其正式合同单独管理。
- 不做 FFLogs 统计网页 `output`、职业/副本人口分位对照、私人报告、用户 OAuth、角色绑定。
- 不实现市场、招募、石之家、其它游戏或通用扩展更新/卸载能力。
- 不把参考插件行为、静态检查、HTTP 200、插件 `activated=true`、模块目录存在或来源声明当作功能通过。
- 不把页面权限等同于 AstrBot 管理员身份，不允许页面任意指定聊天接收人；本人订阅页面管理保持只读/不可用，直到主体提供经审查的可信 actor 与私有读写合同。
- 用户已明确授权，root 已将 FFLogs Client ID/Secret 保存在未提交的 `.architecture-refactor/local-secrets/fflogs.credentials.json`；root 已核验 git ignore 与 Windows ACL。文件只作为本机测试输入，不属于插件配置，不随 ZIP/构建/测试归档复制，不提交、不打印、不回显，也不由插件自动发现或加载；本计划编写者不读取或修改该凭据文件，root 不得要求用户重复提供。
- FFLogs access token、完整私人战绩或宿主登录资料不得写入仓库、普通配置、日志、测试 fixture、截图或提交。不得自动 commit、push、tag 或部署到用户目标。

## 3. 已确认决策与执行不变量

| 决策 | 对计划的约束 |
| --- | --- |
| Windows `LongPathsEnabled` 已由用户报告开启 | 这是用户报告，root 尚需在新进程/新 AstrBot 实例中复核。Windows 长路径能力还受进程 `longPathAware` manifest 影响；修改注册表或 manifest 后重启目标进程再测，见 [Microsoft 路径长度限制说明](https://learn.microsoft.com/en-us/windows/win32/fileio/maximum-file-path-limitation)。以历史失败过的 273/284 字符根验证，不因开关已开启就宣称修复；先区分 OS/进程能力、Python 文件访问、插件扫描与宿主静默信任降级。 |
| W1 本地 Core/Module 和一次 Astra 审查已 PASS；W1 状态为 `accepted_local`，Core Ready 仍 CLOSED | 复用当前产物和证据；任何修复按新变更局部审查，不再次消耗一次整 W Astra，也不升级总体 Core Ready 状态。 |
| 用 FFLogs 角色接口验证 `如月怜 @ 潮风亭`；角色 Logs 需要呈现截图可见的绝本/零式排名和 rDPS 语义 | 以来源响应中的字段和查询时间为准；界面标明 region、服务器、角色、指标、排名口径和来源。无数据、隐藏角色或不支持口径须清楚说明，不转换成统计页结论。 |
| 统计网页 `output` 本期不做 | 禁止新增、调用或开放统计页查询；角色 GraphQL 成功和 HTML 403 分开记录。 |
| item 样本以 ID `44091` 为准 | 中文名称取执行时同一已核验来源返回的规范名称，再与截图核对；截图“犎牛牛排”的字体转录歧义不阻塞 ID 验收，不要求复制截图背景或装饰。 |
| 物品以 XIVAPI-compatible 中文来源为主；Garland Tools CN 仅作补充详情/交叉核验，用户给定页面为 `https://garlandtools.cn/db/#item/44091`，对应资料接口路径 `/db/doc/item/chs/3/44091.json`。它不是已验证的完整备用源 | W2-0 冻结具体 source map 前不把 Garland 标为自动 fallback。每个来源分别记录 URL/source ID、区域覆盖、字段范围、freshness、许可和状态；无法证明时返回明确 unavailable/partial，不用旧缓存伪装新鲜。日历备用源同样须独立核验。 |
| 测试凭据文件与生产密钥存储分离 | root 已按授权创建 `.architecture-refactor/local-secrets/fflogs.credentials.json`，并核验 git ignore 与 Windows ACL；仅 root 的测试器显式读取。它不提交、不进 ZIP、无插件自动加载。产品运行时凭据仍经 Core `SourceCredentialService` 与 SecretStore 路径配置，页面写入需独立 AdminOperations 授权接线。 |
| 插件配置项目和 Plugin Page UI 必须一起设计 | AstrBot 4.28.0 已核验原生 `_conf_schema.json` 和 Plugin Pages API。非秘密选项由 AstrBot 配置作为唯一权威；密钥不得用 `secret:true` 冒充加密，只能进 Core SecretStore。页面后端须验证 Core 授权，Dashboard 登录身份不等同调用者、owner 或 Core admin。 |
| 角色/订阅权限按本人可信上下文 | 命令 CRUD 必须来源于 Core 解析的 actor/conversation；页面不得传入任意 platform、conversation、target 或 owner 字段。订阅操作只改本人记录并校验 `expected_revision`；`UNKNOWN` 投递按 Core receipt 处理，模块不自动重发。 |

## 4. 当前状态、证据层与目标状态

### 4.1 已有证据

- W1 已有 FF14 模块、物品/日历/角色查询及订阅命令、本地 Core 能力和固定扩展工件；本地验收并不等于用户目标安装或 IM 推送通过。参见 [FF14-MVP-01](../.coordination/tasks/modules/ff14/FF14-MVP-01.md)、[W1 验收证据](../.architecture-refactor/ff14-w1-acceptance.md)及 [W1 执行记录](../.coordination/runs/FF14-W1-execution.md)。
- 长根下曾出现 `bundled_extensions` 空目录、模块未受信任并静默降级成“无模块”；短根测试包则完成 AstrBot 4.28.0 安装、激活、重启、FF14 help/status。该差异指向长路径安装缺陷，不能由用户开启注册表选项的报告自动关闭。精确产物和过程见 [真实安装指挥记录](../.architecture-refactor/ff14-live-install-director.md)。
- 短根真实 WebChat 中，item 与 calendar 查询都返回 `source temporarily unavailable`，尚未归因；无凭据 Logs 返回 `authentication required`；订阅清单为本人 0 条。此前未创建订阅、未做实际 source 数据校验和定时推送验收。
- 此前获准的一次国服 FFLogs OAuth/GraphQL 角色 handler 成功，统计网页当前返回 403。塔塔露对照和另一轮角色 API 查询均证明这两条路径不能混为一谈。日志/角色样本来源和输出口径见 [FFLogs 对照记录](../.architecture-refactor/ff14-tataru-fflogs-comparison.md)。
- 最初正式 ZIP 的 SHA-256 为 `582857923426fdfa1207a56e115b555c0610c3c4145302a30a1a4ee5d157d466`，不含两处 Windows 扫描器与 LLM 阶段修复；后续 live-test ZIP `dist/ff14-live-test/astrbot_plugin_yomihime_game_link-v0.1.2-live-test.zip` 的 SHA-256 为 `493a98ee6bae62bf3e021f114a65e7879657f9f8514f61a90d8487d7387552c0`。运行验收先确认选用当前工作树构建的包是否仍含必要修复，不复用旧正式包作为默认输入。测试包构建必须使用 allowlist，明确排除 `.architecture-refactor/local-secrets/**`。

### 4.2 AstrBot 4.28.0 配置与页面事实

以下内容已由 root 对照本机 AstrBot 4.28.0 官方文档与官方文档页核对。执行时若目标版本不同，重新核验。

- 插件配置文档：[官方 plugin-config 指南](https://docs.astrbot.app/en/dev/star/guides/plugin-config.html)。插件根目录 `_conf_schema.json` 描述字段；AstrBot 自动生成并持久化 `data/config/<plugin_name>_config.json`，配置注入插件 constructor。字段类型包括 `string`、`text`、`int`、`float`、`bool`、`object`、`list` 等。`secret: true` 只遮罩 UI 输入，不提供加密存储，不能用于 FFLogs client secret。`ConfigItemRenderer.vue:327-332` 只实现遮罩；`astrbot_config.py:338-344` 负责 JSON 配置持久化。
- Plugin Pages 文档：[官方 plugin-pages 指南](https://docs.astrbot.app/en/dev/star/guides/plugin-pages.html)，与本机 `docs/en/dev/star/guides/plugin-pages.md` 一致。`plugin_page_service.py:498-535` 自动发现插件根目录 `pages/<page_name>/index.html`，无需 `register_page`。AstrBot 插件详情页可打开受限 iframe；前端支持 `window.AstrBotPluginPage.ready()`、`apiGet()`、`apiPost()`、`onContext()`，相对静态资源和 hash 路由；前端不得读写 cookie、LocalStorage 或访问 parent DOM。
- 后端可用 `context.register_web_api('/' + PLUGIN_NAME + '/...', handler, methods, desc)` 注册路由；推荐 `astrbot.api.web.request`、`json_response`、`error_response`。`context.py:705` 确有此方法。`plugins.py:166-221,379-419,866-909` 中的 plugin scope/auth 是页面/插件 API 范围校验，不等于 Core 管理权限、聊天 actor/owner 或订阅授权。
- 配置保存路径已核验：`dashboard/services/config_service.py:998-1027` 执行 `validate_config → metadata.config.save_config → plugin_manager.reload`；没有通用热更新 hook 或配置 CAS。`plugins.py` API bridge 支持受控 GET/POST；不能假设存在 bridge PATCH。
- 当前本插件仓库没有 `_conf_schema.json` 或 `pages/`。`main.py` 只将插件 config 交给父类，没有把它接入 CoreRuntime；目前 runtime 只读取宿主 `http_proxy`。新增 schema 字段仅会显示/保存，不会自动改变模块行为；W2-2 必须完成运行时 snapshot 接线和重载验收。

### 4.3 分层验收状态

| 层 | 当前判断 | 后续关闭证据 |
| --- | --- | --- |
| 包/依赖 | 曾有 CRC、137 entries、固定 SHA 的本地包检查 | 本轮构建包的成员、hash、依赖 pin、可加载 manifest 和文件路径长度报告 |
| 宿主安装/注册 | 短根 4.28.0 安装激活通过；长根降级失败 | 长根新进程标准安装；插件数据中模块被信任；AstrBot 重启后模块仍注册 |
| 命令/业务 | help/status 有真实 WebChat 回复；source 数据未通过 | item、角色、日历明确响应内容及来源/时间，不用只看 HTTP 层 |
| 私人订阅/投递 | 仅测本人空列表；未建订阅 | 私聊本人 create/list/update/cancel、重启持久性、投递去重/UNKNOWN/拒绝越权 |
| 自定义页面 | 未验收 | 宿主页面注册、可信读上下文、响应 DTO、错误/权限路径、可视布局与键盘检查；或按决策明确留在设计阶段 |
| 远端用户实例 | 未验收 | 用户明确授权该目标后单独证明插件列表、模块、可见 UI/IM 回复、数据保护；隔离本机成功不代替该证据 |

<a id="config-design"></a>

## 5. 配置权威、配置字段与生效规则

### 5.1 唯一权威与读写路径

| 数据 | 唯一持久化权威 | 写入口 | Runtime 读路径 |
| --- | --- | --- | --- |
| 基础普通 FF14 配置 | AstrBot 生成的 `data/config/<plugin_name>_config.json` | AstrBot 原生插件配置表单；Plugin Page 不另存同一字段 | 插件 constructor 注入 config 经 adapter 做类型/范围校验后转成不可变 `FF14ConfigSnapshot`，交给 CoreRuntime/ModuleServices 只读配置视图；模块不直接读宿主 JSON |
| Core FF14 feature gate 与生产 FFLogs credential fields | Core ConfigRepository 是普通 Core 字段权威，既有 `_ConfigurationCoordinator` 管理校验/revision；secret bytes 由 Core SecretStore 保存，`SourceCredentialService` 为模块凭据使用路径。`ConfigurationService` 仅作为模块读取 facade；不得另建协调器；普通 `_conf_schema.json` 不写这些字段 | Host adapter 经受支持 `AdminOperations.module_snapshot` / `update_config` / `set_enabled` 等操作调用；具体 `ConfigPatch`/revision 与授权上下文由 W2-0 对照现有 API 冻结 | 配置 revision 与 SecretTransition 由既有服务执行；模块只通过受限 SourceHttp/credential scope 请求，不读 secret bytes；不把私有 Core classes 暴露给页面或 SDK |
| 本机真实验收输入 | 用户已授权且 root 已创建、检查 git ignore/Windows ACL 的 `.architecture-refactor/local-secrets/fflogs.credentials.json` | 仅 root 的受控 controller 显式读取一次；经 W2-0 已审查的管理操作写入隔离宿主 Core SecretStore | controller 的内存态仅用于同一隔离 root 的受控写入/重启/清理；正式插件查询必须自行经 `SourceCredentialService` 与 SourceHttp 使用宿主内凭据，不得直接注入 OAuth token |

普通设置禁止复制进 Core ConfigRepository/SQLite 形成同字段双写。constructor config 是基础项的唯一权威；adapter 生成 `FF14ConfigSnapshot` 并向 CoreRuntime/ModuleServices 暴露只读视图。当前 `main.py` 未把父类 config 接入 Runtime，W2-2 修正此接线。配置保存已核验按 `validate_config → metadata.config.save_config → plugin_manager.reload` 生效，不设计热更新 hook。正在运行的 invocation 使用启动时快照，保存后的下一批 invocation 在宿主 reload 后用新快照。宿主保存接口没有 CAS；多标签/并发编辑可能以旧表单覆盖新值，因此要求编辑前刷新，只保留原生表单一个 writer；页面对普通项只读并跳转原生表单。

原生配置表单保存普通设置并校验 schema 范围。当前确认的宿主 save 会 reload plugin；W2-2 应在 UI 明确提示保存后由 AstrBot 重载。配置切换不改写已有订阅、不改变在途调用，也不重发消息。invalid/corrupt snapshot 时 Runtime 保留当前健康快照并报告配置无效；新进程无法读取有效配置时只允许无害默认查询，订阅/秘密能力 fail closed。

AstrBot config 更新没有 CAS，多标签旧表单可能覆盖新值。首版仅 native form 写基础字段，Plugin Page 显示只读 snapshot 与“保存会重载”提示，不为避免 CAS 问题而自行创建第二持久化副本。若未来页面需要写基础字段，必须调用 AstrBot 已支持的 save API，并增加 stale revision 检查；无法验证 revision 就维持页面只读。

Core 已有 `services/configuration.py` 中的 `_ConfigurationCoordinator`、`ConfigurationService`，并已有 `services/source_credentials.py` 的 `SourceCredentialService`，以及 `services/admin_authorization.py`、`services/admin_facade.py`、`services/admin_operations.py`。W2 复用这些内部能力：Plugin Page 只调用经审查的宿主适配器/受支持 `AdminOperations` 合同；不能把私有服务类型直接暴露给页面或 SDK，也不能另建第二套配置协调器。W2-0 先锁定现有方法、CAS/revision、SecretStore transition、授权 principal 与错误合同；如果功能缺口确实需要扩展，必须围绕既有服务单点扩展并列明精确文件/事务语义。任何字段只能有一个持久化 authority，禁止同字段双写 AstrBot JSON 与 Core 表。跨存储逻辑无法原子提交时，拆成两个用户可见动作，或记录 pending/补偿状态，不宣称为原子事务。

FFLogs 每个 region 使用一个既有敏感配置 alias：`credential_fflogs_cn` 或 `credential_fflogs_global`，各自指向一个 SecretRef。`client_id` / `client_secret` 是同一 JSON secret material 内的属性，也是页面成对输入的内容，不是两个独立的 Core 配置字段。复用 manifest、source policy 与 `SourceCredentialService` 的现有消费合同，协调 reference、revision 与 SecretStore secret bytes；任何字段迁移必须先在 W2-0 明确冻结。按既有 `SecretTransition`/receipt 合同检查跨存储事务；若不能原子完成，应使用其受支持的补偿状态表达，不自造并行 generation store。`REPLACE` 只有在完整新凭据可用且 revision 更新成功后才切换；失败保留原活动引用并补偿暂存 secret；`CLEAR` 先让 active reference 不可用，再按服务合同清理 secret，失败需报告待清理状态且不得继续用已清除凭据。

宿主 Page route 使用单调递增 `config_revision`/credential revision 做 CAS；底层必须映射到 Core 既有 revision/SecretTransition 合同。API 草案如下（AstrBot bridge 支持 GET/POST，不依赖 PATCH）：

```text
GET  /<plugin_name>/ff14/admin-state
     -> { module_enabled, subscription_gate_enabled,
          module_revision, config_revision }
POST /<plugin_name>/ff14/module-lifecycle
     request: { enabled, expected_registry_revision }
     -> { module_enabled, module_revision }
POST /<plugin_name>/ff14/subscription-gate
     request: { enabled, expected_config_revision }
     -> { subscription_gate_enabled, config_revision }

GET  /<plugin_name>/fflogs/credential-status
     -> { configured, state, config_revision,
          last_check_at?, last_error_code? }

POST /<plugin_name>/fflogs/credential
     request: { expected_revision, mode: "KEEP" | "REPLACE" | "CLEAR",
                client_id?, client_secret? }
     -> { configured, state, config_revision, reload_required }
```

上述 path/DTO 是本计划的 adapter 草案，不代表已经存在的插件 API。W2-0 必须将字段映射到已有 `AdminOperations`/health DTO；若当前 DTO 没有最近检查时间或错误类别，就省略相应 optional 字段，不新增平行 Core 状态表或未授权读口。

路由以 `context.register_web_api('/' + PLUGIN_NAME + '/...', handler, methods, desc)` 注册。模块/订阅 gate 路由分别映射既有 `AdminOperations.module_snapshot`、`set_enabled` 和 `update_config`；credential GET 从 module snapshot/config summary 读无明文状态，POST 将 `KEEP`/`REPLACE`/`CLEAR` 转为 Core 现有 `ConfigPatch` 能表达的更新，经 `AdminOperations.update_config` + `AdminAuthorizationService` 执行并使用返回 revision。W2-0 必须核对现有接口是否覆盖敏感字段/清空/expected revision 语义，不能在 route 绕过服务直接读写数据库/SecretStore。AstrBot `require_plugin_scope` 只证明当前请求具备插件 API scope，不证明 Core admin、聊天 actor 或订阅 owner。Host adapter 必须从真实 Dashboard request/session 形成受审 principal mapping，并把每个写操作交给既有 `AdminAuthorizationService`/受支持 `AdminOperations` 检查；不得把 dashboard username、asset token、onContext 内容直接铸造成 Core 管理上下文。若 mapping 或现有 AdminOperations 无法提供本页所需的操作授权/revision 合同，页面可展示经许可的全局健康汇总，credential/gate mutation 与个人订阅详情端点必须拒绝。

- `KEEP`：不带密钥正文，只读当前状态/保持当前 reference；用于未修改表单保存，不触发密钥轮换。
- `REPLACE`：client ID/secret 只在一次受控 Dashboard POST 请求的内存中传输，不放 query、hash、cookie、LocalStorage、日志或页面状态恢复存储。既有 Core 配置/授权/凭据服务校验大小/格式后，以新 secret reference 暂存，revision 切换活动引用成功后才清理旧项。新 secret 写入或 revision 任一步失败，按既有 transition 补偿暂存项、保留旧 reference、返回脱敏错误。
- `CLEAR`：二次确认后 CAS 移除 active reference；事务式 store 应原子移除，非事务 provider 先切换为无凭据状态、再清旧值并记录仅含 reference/generation 的清理工作。清除失败时维持可解释的 active state，不展示成功绿灯，不自动回退到旧密钥进行授权。
- 冲突：`expected_revision` 过期返回 409 和当前 revision/state，不回显任何 secret；用户刷新后重新操作。
- 页面关闭/请求中断：服务端按 request transaction 完成或回滚；前端清空输入框和内存字段。Core 既有凭据服务按 active reference 为后续调用提供凭据；若实现依赖 Runtime snapshot 刷新，响应须报告 `reload_required`，并由受支持的 AstrBot 流程显式 reload。不能假设存在配置热更新 hook。旧 OAuth access token 按短 TTL 作废/刷新，不持久进普通配置。

### 5.2 配置字段目录

下面的 internal key 是本阶段拟议名称，实际 schema JSON 形状按官方指南校验。不要增加任意 URL、鉴权 header、API endpoint 或收件人配置字段。

| Key（拟议） | 中文标签 / AstrBot 类型 | 默认 / 校验 | 生效范围 | 持久化/写入口 |
| --- | --- | --- | --- | --- |
| `ff14_default_region` | 默认 FF14 区域 / `string` | `cn`；只接受 `cn`、`global`。国服角色是必验样本，国际服是可选测试 | 仅为省略区域时的查询提示；显式输入总是优先，且当前支持范围有限时不伪装 Global 已验收 | AstrBot plugin config；保存后按上节 reload 边界生效 |
| `ff14_calendar_default_days` | 日历默认查询天数 / `int` | `7`；闭区间 1–30，非整数拒绝 | 只影响不带 days 的日历查询默认窗口，不改既有订阅窗口 | AstrBot plugin config |
| `ff14_calendar_default_timezone` | 日历默认时区 / `string` | `Asia/Shanghai`；要求 `zoneinfo` 可解析的 IANA timezone | 新查询/新订阅的默认值；更新既有 subscription 必须本人执行 CAS update | AstrBot plugin config |
| `ff14_calendar_default_delivery_time` | 新订阅每日摘要时间 / `string`/文本输入 | `08:00`；严格 `HH:MM`，24h，禁止无效分钟/小时 | 只在用户显式创建订阅时作为默认值，不自动创建或群发 | AstrBot plugin config |
| `web_public_origin`（B0c局部通过，用户实例待更新） | 网页访问地址（可选）/ `string` | 空；仅一个规范化http(s) origin，无用户信息、业务路径、query/fragment或通配符 | 缺失/空/非法仅关闭网页查询，不影响聊天和后台；保存重载生效，不从请求Host/Forwarded推断。它不是可选外部数据源URL | AstrBot plugin config唯一authority；Host网页配置单独校验/只读投影，不进入FF14ConfigSnapshot，不增加网页编辑存储 |
| `ff14_subscriptions_enabled` | FF14 Core module config field / `bool` | 初始 `true` 以保留 W1 已提供的本人订阅能力；Core migration 写入明确值后启用，缺失/损坏时 fail closed | `false` 时暂停所有本人订阅投递并拒绝新建；本人仍可 list/update/cancel，且不丢记录。重新启用只恢复之后的未来窗口，不补发暂停期积压 | Core ConfigRepository 是唯一 authority，既有 `_ConfigurationCoordinator` 校验/CAS、`AdminOperations.update_config` 写入、`ConfigurationService` 提供受限只读视图；不进 AstrBot普通 config。Settings 页未授权时只读，有管理授权才显示操作 |
| FF14 模块启停 | 不建立 `_conf_schema.json` bool；读取现有 Core module lifecycle | 默认遵循当前已注册/启用状态；不额外写第二个 `ff14_module_enabled` | 仅经 Core 现有 `AdminOperations` lifecycle 操作启停。禁用停止该模块命令与调度，保留订阅记录并暂停发送；管理员重新启用后本人可继续管理，调度只恢复未来窗口、不重放过去窗口 | 现有 Core lifecycle/管理存储是唯一 authority；管理身份由 `AdminAuthorizationService` 授权；Settings 未授权时只读 |
| 页面主题 | 不建立插件配置字段 | 优先跟随宿主注入的 `data-theme`/`isDark`，无宿主上下文时回退 `prefers-color-scheme` | 只改变受限 iframe 中的 CSS；不影响 QQ/AstrBot 聊天背景或消息输出 | 不持久化用户级主题偏好；首期无自定义主题开关 |
| 消息输出策略 | 不建立插件配置字段 | 固定 text-first；仅在 Core renderer 与目标平台能力允许时附图 | 图片失败回退文本/链接；不承诺截图装饰或所有平台图片 | Core Renderer 能力与来源许可是唯一 authority；首期无全局强制图片选项 |
| 物品/日历 source policy | 不建立用户可写字段 | 物品以已审核的 XIVAPI-compatible 中文主源为准；Garland 为补充详情/交叉核验；日历按 W2-0 冻结的 source map | 未审核来源不能作为自动 fallback；输出标来源、覆盖和 freshness | 受版本控制的来源 descriptor / Core 策略；不把 URL/endpoint 放入普通配置 |
| HTTP 代理 | 不建立插件代理字段 | 沿用宿主当前 `http_proxy`；凭据部分始终脱敏 | 当前 Core transport 继承宿主代理；保存后按宿主 reload 规则生效 | 宿主配置唯一 authority；本插件只读，不另存、不回显代理 URL/凭据 |
| 诊断/日志级别 | 不建立可切换 verbose 字段 | 固定脱敏的状态码/错误类别和必要计时，不记录 payload/secret/token | 只影响安全诊断输出；不因排障开关扩大个人数据日志 | Core 日志政策唯一 authority；首期不提供原始响应或 debug dump |
| `credential_fflogs_cn` / `credential_fflogs_global` | 沿用 manifest 的每区一个 sensitive alias，各自绑定一个 SecretRef；`client_id` / `client_secret` 仅为同一 secret JSON 的输入属性，不新增两个 Core 字段，也不得加进 AstrBot `_conf_schema.json` | 未配置为缺失；CN 是本轮必验，Global 可选；replace 必须成对提交非空 ID/secret，长度与控制字符按既有凭据解析合同校验；密钥不回显 | 仅通过 Core `AdminOperations.update_config` 的授权配置路径写入该 alias 对应的 SecretStore 引用；`SourceCredentialService` 解析同一 blob 中的两个属性 | Core ConfigRepository 保存每区 alias 的敏感引用/元数据；Core SecretStore 保存完整 JSON material 的加密值。Plugin Page GET 只显示 `configured/state`，POST 按 revision 经 AdminOperations 更新 |
| `ff14_source_urls` / `ff14_http_headers` / `ff14_target` / arbitrary timeout | 不向用户暴露 | 固定来源 descriptor、Core HTTP 安全上限与可信 invocation；禁止任意地址、header、recipient 或放宽 timeout | 由 Core/模块声明、宿主安全策略和可信 invocation 决定 | 不属于插件配置 |

schema 字段缺失时不暗中以插件默认覆盖已保存值；可接受字段必须通过边界验证并产生安全诊断。范围错误应由原生表单拦截；即便宿主绕过前端，Runtime adapter 仍作相同拒绝/裁剪并使能力 fail closed。

<a id="page-design"></a>

## 6. 五页 Plugin Page 设计与用户工作流

Plugin Page 是 restricted iframe 中“如月怜的游戏连结”的运行面板；页面顶栏和面包屑使用产品名，FF14 作为当前唯一已实现模块显示在模块导航/分组中。不得把未来可能添加的游戏或模块显示成已可用功能。AstrBot 原生配置表单仍是普通设置的编辑位置。五页使用中文文案、轻量信息层级和响应式卡片；不要把截图中的 QQ 聊天背景、聊天气泡、装饰或图片样式复制进 Dashboard，也不要让页面配色反向改变 AstrBot/QQ 聊天窗口。

页面路由建议：`#/overview`、`#/logs`、`#/items`、`#/calendar`、`#/settings`。根目录按 `pages/<page_name>/index.html` 自动发现；可用页面 bundle 文件结构建议为 `pages/ff14/index.html`、`pages/ff14/app.js`、`pages/ff14/styles.css`（后两者为相对静态资源，不允许从本机绝对路径加载）。前端在 iframe ready 后调用 `window.AstrBotPluginPage.ready()`，读取 host context 使用 `onContext()`，API 通过 `apiGet/apiPost`；所有请求只进入同插件命名空间的 `context.register_web_api`。前端禁止 cookie/LocalStorage/parent DOM，secret 输入只留在当前组件内存并在提交/取消后清空。

### 6.1 页面文字线框与状态

**页一：概览（`#/overview`）**

```text
如月怜的游戏连结                             [刷新状态]
模块：FF14（当前已实现）
模块  已加载 / 未注册               配置  已生效 / 需重载
FFLogs 凭据  未配置 / 可查询 / 验证失败 / 状态未知
来源状态
  物品：XIVAPI-compatible  最近成功 ... / 暂无记录
  物品详情：Garland Tools CN  最近成功 ... / 未验证
  国服日历：主源 ...   备用源 ...
[查看活动日历]                 [查询角色 Logs]
```

状态字段包括 loading skeleton、首次访问“尚未检查”、健康“最近成功：时间/来源”、partial、“暂不可用：安全错误分类”、模块未装载/需重启、权限不足、unknown。刷新状态只读取脱敏 DTO，不触发大批量外部请求；“验证连接”若未来添加须 Core admin 授权，显示来源与 request time。

**页二：角色 Logs（`#/logs`）**

```text
公开角色 Logs                                  来源：FFLogs
区域 [国服 v] 服务器 [输入/选择] 角色 [输入角色名]
指标 rDPS（本期固定）                         [查询]
状态：请选择区域/服务器/角色 → 正在查询 → 结果
角色：如月怜 @ 潮风亭（CN）     [打开 FFLogs 角色页]
数据更新时间：...    查询指标：rDPS    公开战斗排名
绝本  [副本名] [职业]  最佳 rDPS ...  排名百分位 ...
零式  [副本名] [职业]  最佳 rDPS ...  排名百分位 ...
无数据 / 角色隐藏 / 多个服务器候选 / 需要检查凭据 / 请求超时
```

服务器输入支持从已验证目录选择；存在同名歧义时出现带 region/world slug 的候选列表，必须点击一项才能查询。CN 是必验；Global 仅当当前日的目录、来源覆盖和查询都可验证时选做。错误状态分别包含参数缺失、候选待选、角色不存在/隐藏、无公开排名、认证失败、受限/限流和暂时不可用。结果逐条标注 source/更新时间/职业/指标；若排名 percentile 缺字段就显示“来源未提供”，不计算替代。角色名和报告详情仅呈现于当次响应，不写浏览历史。

**页三：物品（`#/items`）**

```text
物品查询
名称或 ID [搜索框：例 44091]                         [查找]
候选（多项时手动选择）
  名称 — ID — 地区/来源                               [查看]
物品详情：来源规范名 / ID / 已知详情字段 / 来源时间
主源：XIVAPI-compatible ...   补充详情/交叉核验：Garland Tools CN ...
```

本期稳定样本以 `44091` 为准；名称显示为 ID 同次、已核验来源返回的规范名称，并与截图核对。截图中的“犎牛牛排”字形可能受字体影响，不作为停止计划的前置，不把不确定的转录强行写入测试输入。中文查询主源为 XIVAPI-compatible；Garland Tools CN 参考 `https://garlandtools.cn/db/#item/44091` 与 `/db/doc/item/chs/3/44091.json` 仅供补充详情/交叉核验，不代表已接入整套 fallback。空输入提示、无结果、同名多候选、详情 partial、主源失败/补充字段缺失、超时/限速都有独立提示。图片图标仅为增强，图片失败不影响文本详情。

**页四：活动日历与订阅入口（`#/calendar`）**

```text
活动日历
区域 [国服 v] 日期范围 [7 天 v] 时区 [Asia/Shanghai v] [查询]
来源：主源 ... / 备用源 ...       收集时间：...
活动日期 | 时间 | 活动标题 | 活动区域 | 数据状态
本人订阅：该页面无法证明当前 AstrBot Dashboard 用户就是订阅 owner。
请在本人私聊使用：/ygl ff14 calendar subscriptions
创建/修改/取消通过本人命令和 expected_revision 完成。
```

状态包括 loading、空窗“当前窗口暂无活动”、完整数据、partial 数据及受影响事件、stale、source A failed/source B succeeded（明确标签）、双源不可用、窗口/时区错误。页面不显示其他人的订阅，不提供订阅写按钮，不把 dashboard current user 假装成聊天 actor。本人私聊命令是本期订阅 CRUD 和推送验收入口。

**页五：设置（`#/settings`）**

```text
查询默认值（由 AstrBot 插件配置表单编辑）         [打开插件设置]
默认区域：国服       日历范围：7 天       时区：Asia/Shanghai
新订阅默认时间：08:00
FF14 模块：已启用 / 已禁用      [启用 / 禁用]
本人订阅总开关：启用 / 暂停      [启用 / 暂停]
AstrBot 保存状态：已保存 / 校验失败 / 重载后生效

FFLogs 凭据（Core SecretStore；值不可读取）
状态：未配置 / 已设置（不可读取） / 验证失败 / 状态未知
Client ID [不回显]     Client Secret [提交替换值]
[保留当前凭据] [替换凭据] [清除凭据]
保存要求 Core 管理授权；Client Secret 只提交一次，之后不能读取。
```

模块禁用前显示确认：“FF14 命令与日历推送将暂停；订阅记录保留，重新启用后可由本人继续管理；暂停期间不补发。”订阅总开关的确认文案另说明：命令查询和本人 list/update/cancel 可用，仅新建与日历投递暂停。

普通设置值在 Plugin Page 只读呈现，编辑跳转 AstrBot 原生配置 UI；不能为了做漂亮页面而另建表/自定义文件。只有受审 Host principal 映射并通过 `AdminAuthorizationService` 授权的管理员会看到 Core gate 写按钮：订阅 gate 经 `AdminOperations.update_config` + revision 修改，模块启停经 `AdminOperations.set_enabled` + registry revision 修改；其他页面身份只看脱敏状态。Credential card 也仅在授权与既有 Core config revision/SecretTransition 通过审查后加载表单，route 经宿主 adapter 调用受支持的 `AdminOperations`。默认操作 `KEEP`；用户选择 replace 时空输入不等于清空，需两字段有效并二次提交；clear 单独按钮与二次确认；每次提交带 `expected_revision`。成功时显示新状态、revision 和是否需重载，绝不回显或保存 secret；失败状态区分 401/403/409/5xx 且清空输入。

### 6.2 中文、移动、暗色与图片

- 页面所有标签/帮助/错误以简体中文优先，时间/地区/指标口径写全；小屏使用单列卡片，窗口 320px 时无横向滚动；tab/输入/候选/确认可仅用键盘完成，焦点可见，表单有 label、读屏名称，状态使用文字/图标并用颜色增强而非单独依赖颜色。
- CSS 优先跟随宿主注入的 `data-theme`/`isDark`；无宿主上下文时才通过 `prefers-color-scheme` 适应系统暗色/亮色。主题只影响 restricted iframe 页面自己的 HTML/CSS。
- Chat output 是另一条通道：查询结果仍由 `DisplayDocument` → Core renderer → MessagePort 输出。短信/聊天是否附图片仅由后端来源许可、Core renderer 与目标平台能力决定；图片不可用时降级成文本/链接。Plugin Page 中的图片输出设计和聊天背景/气泡主题无关，首阶段不要求复制截图装饰或保证所有平台都发送图片。
- 图标/图片须有来源、再分发许可；未许可时用文字和可访问的 CSS/内置中性符号。图片缺失、格式不支持、消息平台不支持时保留文本查询结果。

### 6.3 页面读写和错误边界

只实现 UI fetch → AstrBotPluginPage.apiGet/apiPost → 插件 register_web_api → Host principal mapping → `AdminAuthorizationService`/受支持 `AdminOperations` → Core service → DTO。页面不触碰 ModuleServices、SQLite、业务 HTTP、SecretStore、AstrBot send API。Plugin scope 仅保证页面路由范围；全局健康汇总走显式 read policy，个人订阅详情不经 Dashboard 返回。Credential mutation 和 gate control 都需由 Core AdminAuthorizationService 对每次 operation 授权；Dashboard username、asset token、onContext 不能直接铸成 Core admin context。若现有合同不能安全映射 host principal，写操作保持不可用。页面 401/403 显示权限错误；409 提示刷新重试；request id 不含 query/name/secret。

### 6.4 页面实现合同与 API 草案

首版采用本地 HTML/CSS/ES modules，不引入前端框架、CDN、独立 Web 服务或新端口。所有静态资源随插件 ZIP 打包，路径相对；初始加载先 `bridge.ready()`，主题使用宿主注入的 `data-theme`/`isDark`，无宿主上下文时才回退系统主题。iframe 的五个 hash 路由共享导航、表单状态、错误横幅和结果组件。卸载时取消本页未完成请求/订阅；一份页面初始化不能注册多个刷新循环。

以下为未来插件本地 endpoint 草案，通过 `bridge.apiGet/apiPost` 调用，不是已存在接口。具体业务方法在 W2-0 冻结后才能实现：

| 方法 / 本地 endpoint | 输入与输出 | 权限、行为 |
| --- | --- | --- |
| GET `overview` | 模块版本/注册状态、配置生效状态、source 状态和最后检查时间 | 最小全局只读 DTO；无服务器路径、凭据内容、个人订阅、原始日志；普通刷新不访问外网 |
| GET `settings` | 已生效普通配置、模块/gate 状态、配置存储归属、是否需重载 | 只读；默认表单写入交原生配置界面；不假造 SDK snapshot 字段 |
| POST `queries/items` | `{query}` → 候选或物品 DTO、来源和数据状态 | 受控公开查询；不接受 source URL/header/actor/target |
| POST `queries/character` | `{region,server,character}` → 角色/副本/职业/rDPS/排名 DTO | 只查询公开角色，禁用统计/私人接口；内容不写 URL、访问日志或持久浏览历史 |
| POST `queries/calendar` | `{region,days,timezone}` → 活动 DTO、coverage/collected_at | 只读，不创建订阅；默认结果最多 30 天，仍受 Core 响应预算约束 |
| GET/POST `fflogs/credential-status` / `fflogs/credential` | 使用 §5 的状态和 KEEP/REPLACE/CLEAR 合同 | 每次写入经 Core 管理授权和 CAS；GET 无密钥；仅 page scope 不足以写入 |
| POST `modules/ff14/enabled`、`modules/ff14/subscriptions` | 显式 bool + 当前版本令牌 → 生效状态 | 拟议管理适配入口；复用 Core enable/config operation，不直接修改 DB 或广播消息 |

页面公开查询需要单独冻结“Dashboard 只读入口→Core 准入/能力策略→FF14 handler→公共结果 DTO”的通路。不能伪造 `COMMAND`/聊天 actor，不能凭 `request.username` 生成聊天 OWNER，不能绕过 Core 直接调用 handler 或抓取来源。现有 `InvocationOrigin` 没有可假定的 `WEB` 类型；优先复用经过核对的既有入口模型，确需合同扩展时由唯一 Core owner 写明 ABI/兼容消费者及测试后才增加。页面查询的结果只返回当前浏览器，不能触发 MessagePort 对外发送。该入口未通过时，查询按钮禁用并给出可复制的聊天命令，不能声称页面查询已交付；已获许可的概览/配置只读页面仍可独立验收。

非秘密普通设置的“打开插件设置”不硬编码一个未经验证的 Dashboard 路由。4.28.2 bridge 没有配置导航能力，显示“返回已安装插件列表 → 本插件卡片的配置齿轮 → 修改并保存重载”的操作说明。所有 POST 校验请求格式、字段白名单、大小和请求来源；Core 授权在服务端做，不以按钮是否隐藏判断。不得把反向代理/桥接机制当作已证明防 CSRF，TC14 单独验证跨来源请求、会话过期与权限撤销。

UI 默认禁止重复点击同一个未完成查询；切换输入或页签使旧响应失效，慢响应不能覆盖新查询。诊断性重试只针对明确可重试的只读失败，尊重服务端 Retry-After 与 Core 上限，不自行叠加无限重试。第一版不做定时自动外网轮询；概览刷新只读取 Core 最新状态。卡片状态统一为未检查、查询中、成功、部分结果、无数据、凭据缺失、受限、来源失败，不能以绿色“已配置”代替“连通成功”。

### 6.5 UI 规格与可运行切片

以下尺寸是可逆设计选择，不是宿主已有组件/API 的声明。复用 AstrBot 外壳、官方 bridge 和主题语义；iframe 内已有可复用页面组件尚不存在，只创建本切片需要的本地组件，不跨 iframe 导入宿主私有组件库，不增加框架/CDN/依赖。

| 规格 | 指导与验收（REQ-06–08） |
| --- | --- |
| 布局 | 宽屏约 200px 左导航+内容区；小于 720px 改有 label 的原生页面选择器；320px 表单/卡片/按钮单列，长中文、URL、错误码可换行，不裁切关键结果 |
| 主次 | 页标题、主要查询操作、结果为主；来源/口径/时间紧邻结果；诊断信息折叠；凭据清除单独危险区，二次确认。产品名使用“如月怜的游戏连结”，当前模块仅 FF14 |
| 组件 | 导航、带 label 查询表单、候选列表、状态提示、结果卡、来源标签、确认框共用；Logs 按副本类别组织，物品候选后详情，日历按事件时间排列，设置普通只读与受保护区分开 |
| 尺寸 | 间距 4/8/12/16/24/32px；区块间 24px，圆角 8px；标题 24/32px、分区 18/26px、正文 14/20px、辅助 12/18px；操作目标至少 40px，可见焦点，遵循宿主字体栈 |
| 颜色/主题 | 仅在 iframe 定义背景/卡片/边框/主次文字/accent/成功/警告/错误/禁用语义变量；优先已核实宿主变量，否则按 data-theme 映射；状态必须配文字或图标，不只靠颜色 |
| 交互 | 查询中防重复提交；换页/参数使旧请求失效；状态更新可被读屏获知，错误关联字段；确认框管理初始焦点、取消与返回焦点；不以动画承载必要信息 |

| 状态 | 呈现与恢复 |
| --- | --- |
| 首次/尚未检查/未配置 | 说明尚无检查或缺少哪类配置，给普通设置指引；“已配置”不表示来源可达 |
| 加载/保存中 | 文本+进度占位，禁用重复操作；界面其余导航仍可用；保存结果未确认前不显示成功 |
| 空数据 | 仅在业务成功但无结果时出现，提供调整参数；不从来源失败推断角色不存在/私密 |
| 部分/过期 | 标明缺字段/来源及时间；若保留旧结果必须显著标旧，不能作为本次查询成功 |
| 失败/无权限 | 显示安全分类、可用的下一步和重试条件；服务端同样拒绝，无权限不只隐藏按钮 |
| 保存完成/冲突 | 显示新状态与是否待重载；冲突刷新状态后显式重提。仅保留非敏感草稿，secret 在成功、失败、取消或离页后清空且不进持久存储 |
| 尚未接通 | 查询禁用并提供确认过语法的聊天命令；剪贴板 API 受限时显示可选取文本。原生设置导航未核实时给文字步骤，不假造 URL |

W2-5 分成依赖明确的三个小切片，不以整插件设计全部完成为开工前提：**UI-A** 概览/设置只读、五页导航和未接通指引；**UI-B** Core 公开 Web 入口通过后逐个启用 Logs/物品/日历；**UI-C** 可信 Core 管理授权通过后启用凭据与 gate。A 的页面可用不证明 B/C 已完成；B/C 缺前置必须列 BLOCKED。每片首版都要在稳定候选上检查实际浏览器渲染；当前全部为设计，**视觉 / 交互未验证**。

## 7. 架构边界与隐私

- 保持 Core 通用、FF14 领域留在 `modules/ff14/`。模块只通过唯一 `yomihime_sdk.api` 和受限 `ModuleServices` 使用 HTTP、凭据引用、持久记录、展示、订阅与投递。
- 不在 FF14 模块直接导入 AstrBot、SQL/SQLite、文件系统 secret、requests/aiohttp 或宿主发送 API；不增加第二套 registry/runtime、后台 scheduler、来源任意 URL 输入或页面私有协议。
- 主体接口缺口由 root 冻结公共 DTO/ABI 和白名单，交唯一 implementer 顺序完成 Core 与 FF14 切片；Core/Module 是职责边界，不是并行作者。`main.py`、共享 builder、manifest、SDK、schema 都由同一 implementer 修改。
- OAuth 使用 client-credentials 并只查公开角色资料，不调用私人/用户端点、不请求 `includePrivateLogs`。授权材料仅由批准的秘密桥接提供；错误只给脱敏类别，不记录令牌、Authorization header、原始 OAuth 响应或含个人信息的完整响应。
- 输入和展示限定在用户明确指定的服务器、角色、查询窗口和来源。角色查询结果应只保存必要的短期缓存；不要把用户示例角色、完整 ranking JSON 或私聊消息写入共享 fixture。
- 页面及命令对权限拒绝、来源失败和部分结果分开处理。对本人 subscription 改动必须校验 owner + CAS revision；取消保留 delivery history；`UNKNOWN` 不自动重发。
- 新建/更新测试宿主时只操作本轮建出的 workspace 隔离根和进程；先验证绝对根路径、进程树、端口与数据边界。远端安装、重启、凭据持久化或发 IM 需要单独按用户授权执行。

<a id="test-design"></a>

## 8. 详细测试设计

这些是后续执行的设计，不代表本轮已运行测试。所有真实来源结果记录区域、来源 ID、请求时间、HTTP/业务状态、数据时间和结果类别；不保存响应全文或凭据。统一使用 `PASS`、`FAIL`、`NOT RUN`、`INCONCLUSIVE`、`BLOCKED`，并附证据路径和该证据能证明的层。

| ID | 场景/目标 | 通过标准 | 不能据此声称 |
| --- | --- | --- | --- |
| T0 | 构建唯一测试工件 | manifest、模块、schema、依赖均在包内；记录 SHA-256、entry count、固定 SDK pin；两处既有修复在内；路径清单包含最长成员 | 文件打包成功不等于 AstrBot 信任/加载 |
| T1 | 新进程长路径验证 | 记录新进程读到的 LongPathsEnabled 值、AstrBot 进程 manifest 是否 `longPathAware` 和 Python/OS 版本；用历史失败的 273/284 字符根完成安全扫描、目录操作、安装解包和资源访问；不能静默得到空 extension root；重启后模块仍信任并注册 | 用户报告注册表已开、短根安装、一个 mkdir 探针不能证明目标长根修复 |
| T2 | AstrBot 标准安装与可见命令 | 隔离 AstrBot 4.28.0 的实际插件列表/日志、restart 后 activated 状态；新会话 `/ygl help` 和 `/ygl ff14 status` 得到业务成功 `plain` + 正常终结事件，正文列出 FF14 命令/真实模块状态；报告宿主版本、根路径、构建 hash | HTTP 200、JSON `status:error`、SSE `error`、activated 或 process readiness 均不能证明安装/命令成功；本地隔离实例不代表用户远端部署 |
| T3 | 物品中文名和 ID | ID `44091` 为稳定验收键；显示执行时同一已核验来源返回的规范名，并与截图核对；精确截图转录不阻断 ID 验收 | 成功访问一个来源不证明另一个来源、价格或全部详情可信 |
| T4 | 物品多候选/失败/降级 | 用一个可重复的歧义输入、ID miss、429/超时/部分字段受控情形；用户看到候选选择和明确 source error；XIVAPI-compatible 是中文主源，Garland 只提供经 W2-0 冻结字段范围内的补充/交叉核验，不启用整套自动 fallback | 缓存命中不能掩盖当前来源不可用；Garland 补充链接不能直接记为整套 fallback |
| T5 | FFLogs 公开角色 Logs | 用 `cn / 潮风亭 / 如月怜` 经 OAuth client-credentials 后调用角色 GraphQL；显示同一角色的公开排名结果、绝本与零式类别（源有数据时）、rDPS 与 percentile 的真实来源字段、时间和 profile URL；明确区分“百分位排名”与“统计网页人口分位” | OAuth 200 不等于角色查询成功；GraphQL 角色成功不等于统计 HTML 可用，也不允许访问私有报告 |
| T6 | FFLogs 失败边界 | 缺失/失效凭据、隐藏角色、角色/服务器歧义、空排名、unsupported metric、timeout、限流、GraphQL errors 均返回有区分的脱敏提示；确认未请求统计 HTML/table 和私人端点 | 不把所有失败统称“FFLogs 不通”；未提供凭据路径不能验证授权角色结果 |
| T7 | 日历双来源 | CN 必验、Global 可选；记录各 region 主源与备用源身份、状态码、覆盖窗口、完整性和 collected_at；验证主源/备源/双失败/partial | 当前备用源不可靠；代码尝试 URL 或旧缓存不构成来源可用证明 |
| T8 | 日历来源 freshness/recurrence | 验证活动窗口、时区与日期边界、取消/partial、重复规则、全天 DTEND；来源不完整不得推断活动取消；过期数据必须标 stale/unknown | 解析器单测不证明真实日历源新鲜、完整或许可可用 |
| T9 | 本人订阅 CRUD | 可信本人私聊 create → list → update with current revision → stale-revision conflict → cancel；他人 ID、群聊或任意 target 拒绝 | PRIVATE 回执文案不单独证明 owner 判定；群消息中成功也不是本人权限证明 |
| T10 | 重启/持久状态与推送 | AstrBot/插件重启后本人订阅保留且行为一致；测试日报时间窗口、重复事件 key、重复 collector、取消后不投递；对受控 send 结果包括 `UNKNOWN` 验证不自动重建/重发，并能由 Core receipt 作出可审计解释 | Scheduler fake time 通过不证明 AstrBot真实周期；排队不等于用户收到 |
| T11 | 配置权威与 Runtime 注入 | Native settings validate/save/reload 已核验；测试默认值、边界、错误类型及 main.py 到 Runtime snapshot；并发旧表单覆盖风险单列 | schema form 已保存不等于模块已读取新值 |
| T12 | Plugin Page UX 与权限 | 五 routes、loading/empty/error/partial、窄屏、键盘/焦点/暗亮模式；页面仅健康汇总，不显示个人订阅详情；secret editing 需独立 Core auth；API 成功须为 2xx 且业务 DTO 为成功状态 | onContext/Dashboard identity 不证明 owner/admin；HTTP 200 + JSON `status:error` 仍失败；截图美观不证明 API 安全 |
| T13 | 统一的完整运行记录 | 一次 controller/单一长 root/固定包覆盖安装、restart 和能力验收；进程/端口退出；local secret 不在包、日志、交付或提交 | 拼接多个 root/hash/cohort 的局部 PASS 不能冒充一次端到端 PASS |

执行详细用例前先冻结代码/package cohort、宿主版本、测试器和授权范围。控制器的 ready 必须确认 Popen 存活、listener 属于进程树、期望 root 生效、目标 AstrBot API 返回正确版本，不能只看任意 HTTP 200。用同一有效 controller 会话完成安装、重启和可归属该 root 的检查；不可恢复时封存当前 cohort 并另开新 cohort，不拼接证据。

### 8.1 可执行测试用例卡

所有用例记录 `run_id`、源码/worktree 摘要、ZIP SHA-256、AstrBot/Python/Windows 版本、进程树/listener、隔离根长度、实际请求命令、业务错误码和证据相对路径。真实响应只保留最小脱敏字段；任何凭据/token/header、完整 GraphQL 响应、无关聊天文本或完整私人资料都不记录。页面截图不得包含密码输入内容、真实会话列表或 QQ 背景。

#### TC01：测试 ZIP 内容与秘密排除

- 前置：冻结工作树与 builder 输入；授权凭据文件已由 root 创建，并核验 git ignore 与 ACL。该文件只由 root 显式读取，不进入 builder 输入目录。
- 输入：唯一候选 W2 ZIP、SDK 固定 wheel、扩展 manifest。
- 步骤：按 allowlist 构建；记录 ZIP hash/entries/pins/最长成员；对 archive 成员名与构建 staging 做只报告命中数的扫描；核实必要 Windows 修复在包内。
- 预期：测试 ZIP 含模块、manifest、页面和已批准依赖；不含 `.architecture-refactor/local-secrets/`、`fflogs.credentials.json` 或 secret 值；成员顺序/数量/hash 可复现。
- 证据与观测：build command、archive hash、成员清单、最大路径长度、secret-path 命中布尔值。不得输出被扫描的 secret 内容。
- 失败定位：秘密路径出现在包中即停止安装、修 allowlist；必要文件缺失或 hash 不同则为新 cohort。

#### TC02：新进程长路径扫描/安装

- 前置：隔离 AstrBot 4.28.0 root 为空；本轮新启动测试子进程；根绝对路径在 workspace 内。
- 输入：当前候选 ZIP、历史失败的 273/284 字符嵌套路径、用户报告已开启的 LongPathsEnabled。
- 步骤：先复核 registry/system 值、新建 AstrBot/Python 进程是否生效、AstrBot executable manifest 的 `longPathAware` 标记与 Python/OS build，不先改代码；重启目标进程后走标准解压、native scanner、manifest 读取与模块路径打开/关闭；试验 junction/reparse point 越界被拒。
- 预期：合法包被信任并列出 FF14 模块；系统/文件 API 错误不会静默转为空扩展根；错误路径有清楚诊断且 fail closed。
- 证据与观测：有效系统值、AstrBot manifest 标记、解释器、OS/Python、位数/GIL、绝对根/最长路径、Win32 errno、scan/trust result、extension count。
- 失败定位：注册表值没进入子进程为进程重启/环境问题；系统值有效且 manifest 缺标记时先定位宿主进程能力；两者有效但文件操作失败为 path abstraction；操作成功而 trust false 或静默空模块为验证规则/manifest/错误呈现问题；不可只凭开关值关闭缺陷。

#### TC03：真实 AstrBot 安装、重启和消息路由

- 前置：TC01–02 同一 cohort；controller 证明 AstrBot listener PID 属于它创建的进程树；安装前确认目标隔离 root、端口、版本和插件列表。
- 输入：固定 ZIP 和新聊天会话。
- 步骤：检查 AstrBot 4.28.0；只上传一次；分别检查 HTTP status 与安装 JSON 业务状态；重启同一 root；在 WebChat 发 `/ygl help` 与 `/ygl ff14 status`，核对 stream event sequence。
- 预期：真实标准上传成功且安装 JSON 明确业务成功，插件激活且 extension trusted；重启后命令仍可见；帮助正文有 FF14 命令，status 对应真实模块/来源限制，两个流均有 `plain` 和终止 `end` 且无 `error`。
- 证据与观测：上传 HTTP 与 JSON `status`/error code、插件 id/version/activated、trust id、日志异常类别、重启 PID tree、SSE event sequence、root/hash。
- 失败定位：HTTP 200 但 JSON `status:error` 是安装失败；上传失败查安装栈；无模块查 scan/trust；模块已载入但 help 空查 manifest/factory；SSE `error`、缺少终止 `end` 或消息正文空均为命令失败，再查 ingress/renderer/MessagePort。

#### TC04：物品 ID 44091 与规范名称

- 前置：TC03 PASS；根统筹已冻结 XIVAPI-compatible 中文主源和 Garland Tools CN 补充详情的 source map、许可、区域覆盖与字段可信度。
- 输入：命令查询 ID `44091`；Garland 用户给定参考页 `https://garlandtools.cn/db/#item/44091` 及资料接口 `/db/doc/item/chs/3/44091.json` 仅供补充交叉核验。名称输入取同一已核验来源返回的规范名称。截图“犎牛牛排”用于视觉比对，精确手工转录不是前置。
- 步骤：先通过真实 chat command 查询 ID；比较 ID/name/来源/时间；再用规范名搜索并明确选中 44091。Plugin Page 对同 ID 的结果比较延后到 TC14/W2-5。
- 预期：名称和 ID 在单个结果中一致；多候选由人选具体 ID；字段逐一注明来源；Garland 不可用时中文主源结果仍完整，或标出仅缺补充字段；不将该链接假定为完整自动 fallback。
- 证据与观测：查询 ID、来源返回的规范名、结果 ID、source id、链接 host、时间/业务状态、字段缺失。
- 失败定位：查询有 HTTP 响应但无模型为 parser/schema；结果 ID 错为搜索映射；页面和命令不一致分别定位 bridge/renderer；source map 未冻结时该 source 记 BLOCKED，不阻塞已验证 ID。

#### TC05：物品错误、多候选与补充来源

- 前置：TC04 有一条可重复查询；准备不含真实资料的受控错误响应。
- 输入：空输入、无效 ID、可重复歧义名称、429/timeout/partial；XIVAPI-compatible 中文主源失败但 Garland 补充资料仍可访问的组合。
- 步骤：依次触发参数错误、未命中、歧义和主源错误；在多候选场景选择第二项；单独观察 Garland 被配置为补充详情时的字段合并、来源标签及禁用缓存后的行为。
- 预期：参数、候选、无结果、限流、超时和 partial 有不同状态；不自动选首项；主源失败不能用 Garland 页面伪装成完整查询成功；只有 source map 明确允许的补充字段可展示并标注 Garland 来源；旧 cache 不冒充 current。
- 证据与观测：case id、candidate IDs、source attempt order/status、cache age、user-facing error/candidates、supplement-used/source attribution。
- 失败定位：默认首选看 module selection；来源标签丢失看 ItemRecord/DisplayDocument；旧数据新鲜度错误看 cache invalidation；不得将 Garland 补充资料上升为全量 fallback；mock 不升级为 live source PASS。

#### TC06：FFLogs 国服角色 Logs 与宿主凭据链路

- 前置：TC03 PASS；root 已创建授权凭据文件，git ignore 与 Windows ACL 已核验。测试按 `TC06-A`/`TC06-B` 两层单独判定；controller 运行于同一隔离宿主工作流，并将凭据留在进程内存直到本 cohort 的 restart/清理结束。
- 输入：CN、潮风亭、如月怜、公开角色 Logs、固定 rDPS；真实凭据只用于本机当前验收，不写入 fixture、日志或插件普通配置。
- 步骤 `TC06-A`（协议连通，不算插件通过）：root controller 显式读取文件、字段校验但不打印值；在单次直接 API probe 中执行 OAuth client-credentials + 角色 GraphQL，记录脱敏状态。此步骤只证明 FFLogs 协议/授权链可达。
- 步骤 `TC06-B`（产品路径，必须）：A 成功后，通过已审查的安全写入入口把 credential pair 写入隔离 AstrBot 对应的 Core ConfigRepository/SecretStore 加密引用；入口必须是 Windows ACL 约束并审查过的维护 CLI，或经独立 Core admin authorization 的 host `AdminOperations` bridge。启动插件后，经生产角色 handler → Core `SourceCredentialService` → SourceHttp 查询，再以真实 chat command 展示；同一 controller 重启该 root 后重复查询，最后用同一授权入口清除隔离 SecretStore credential 并确认状态变为未配置。插件不得读取本地文件，controller 不得把 OAuth access token 注入插件。
- 预期：只有 `TC06-B` 才能 PASS 角色产品验收。CN 角色来源有数据时显示绝本/零式类别、rDPS 与 ranking percentile、职业/副本、数据时间和官方 profile URL；缺字段如实显示。Global 可选，不阻断 CN。若安全写入入口、secret reference 或插件 SourceCredentialService 链路未完成，`TC06-B` 标记 `BLOCKED`，`TC06-A` 的 PASS 不可替代。
- 证据与观测：credential_loaded 布尔值、写入入口类型/授权结果、Core secret metadata state/reference generation（不记 value/hash/digest）、OAuth/GraphQL 分层状态、SourceCredentialService source id/status、CN/server/character、metric、字段 presence/列表数、time/link host、restart 前后 configured 状态和清理结果。禁止记录凭据 path 内容、secret 值/hash/token 或完整 rankings JSON。
- 失败定位：`TC06-A` OAuth fail 查本地输入字段/token route；A 成功而 B 写入失败查 AdminOperations/ACL/SecretTransition；Core configured 但插件查询失败查 SourceCredentialService/credential declaration/SourceHttp；GraphQL OK 结果错查 mapper/render；B 链路未接好不能用 direct `requests`、mock 或 stub 标产品 PASS。

#### TC07：FFLogs 凭据隔离、负例与 output 禁用

- 前置：准备不含凭据的独立 profile 和 synthetic invalid credential；不复用 TC06 token cache。
- 输入：无凭据、invalid synthetic credential、角色 Logs；网络观测器只记录脱敏 host/path/method。
- 步骤：启动插件但不显式传入本地文件；分别发起无凭据与 invalid credential 查询；扫描构建包、页面 storage、日志类别及实际 source path。
- 预期：无凭据显示 authentication required，invalid 显示脱敏认证失败；正常角色路径只走 OAuth + GraphQL；不请求 statistics/table、统计 HTML、私人/user endpoint；插件不自动发现本机凭据文件。
- 证据与观测：HTTP/business status、脱敏 host/path category、访问本地 secret path 布尔值、secret scanner 命中布尔值、页面 storage 检查。
- 失败定位：统计路径被调用查 capability/command registry；本地文件被打开为 secret-boundary bug；日志检测命中停止归档，执行泄露影响审查。

#### TC08：日历主源与备用源实测

- 前置：root 冻结 CN 主备 source ID、固定 URL、许可、区域覆盖和 freshness。CN 必验；Global 可选。测试没有外部接收人。
- 输入：相同 region、窗口、timezone；一次只读 live 查询及 primary/fallback success/failure/partial 的受控响应。
- 步骤：查询主源成功；分别触发主源失败+备源成功、双源失败、partial；重复相同窗口以检验缓存和版本；确认 UI/chat 输出来源字段。
- 预期：结果注明来源、窗口、collected_at；备源成功显示备源标签；双失败/partial 清楚降级且不清空持久状态，不说“暂无活动”；不使用 stale cache 伪装实时。
- 证据与观测：source ID、region/window/timezone、status、request/collect time、source version、coverage complete、event count、fallback used、错误码。
- 失败定位：source list未冻结停止网络动作；fallback未标为备源查 aggregation；双失败误判取消查 completeness/cancellation； live不可达记 BLOCKED，错误路径单独验。

#### TC09：日历 recurrence、时区与 partial

- 前置：calendar collector/evaluator 可在受控离线运行；fixture 为合成数据，不含真实订阅或用户日历。
- 输入：DST fold/gap、全天 exclusive DTEND、RRULE/RDATE/EXDATE/RECURRENCE-ID、partial fetch、取消和 stale 时间戳。
- 步骤：按 fixture 分别运行 normalize/evaluate；先完整覆盖窗口，再注入 partial/failure；对比本地窗口和发生时间。
- 预期：时区/全天窗口边界正确；只有覆盖窗口的完整 observation 可认定取消；partial/failure 不删除已有 occurrence；复杂度超过限制返回 partial/error；过期 cache 标 stale/unknown。
- 证据与观测：fixture ID、timezone/window、occurrence count、source completeness/version、decision code、stale age。
- 失败定位：纯时差错误看 timezone/DST；partial变取消看 evaluator predicate；合成通过不代表真实来源和许可通过。

#### TC10：本人订阅 CRUD、CAS 与越权拒绝

- 前置：可信本人私聊 invocation、目标 source可返回 observation；隔离数据已有备份，记录安全 test recipient。
- 输入：CN、Asia/Shanghai、08:00 的订阅；current revision 和 stale revision；另一主体的订阅 ID；群聊命令。
- 步骤：本人私聊 create/list/update/cancel；重复旧 revision；另一 actor 查询/修改同一 ID；在群聊执行管理操作；确认没有外发给任意指定 target。
- 预期：本人操作成功且 revision 递增；stale revision 返回 conflict 且不变更；跨 owner/群聊/伪造 target fail closed；只有可信上下文决定记录 owner/recipient。
- 证据与观测：actor 来源类别、private/direct 标志、operation、脱敏 subscription ID、before/after revision、错误码、外发次数。
- 失败定位：仅文案说 PRIVATE 不算 owner proof；stale 可写查 CAS；群聊或伪 target 成功立即阻断并审权限服务。

#### TC11：重启、日报投递和 UNKNOWN

- 前置：TC10 有本人订阅；受控真实 Scheduler/MessagePort，非真实群 recipient；controller 可安全重启同一隔离 AstrBot。
- 输入：完整 observation、重复 event key、due window、send SUCCESS/FAILED/UNKNOWN。
- 步骤：推进一次有效窗口；核查 event key/receipt；模拟 UNKNOWN 后重启；重复投递相同 observation；取消订阅后再推进。
- 预期：订阅跨重启保留；同 key 去重；UNKNOWN 仍按 Core receipt，不由模块创建新 event/盲重试；cancel 后不再出现在投递；真实用户收到消息是单独的可见性证据。
- 证据与观测：revision、observation/source version、event key hash、receipt state、attempt count、重启前后 owner record、真实 receive acknowledgement。
- 失败定位：重复发送查 idempotency/receipt；scheduler fake clock 不关真实宿主周期；queue accepted 不等于用户看见；未授权 recipient 不得测。

#### TC12：原生配置字段、唯一 authority 与 reload

- 前置：新 AstrBot 4.28.0 profile；拟议 schema/config adapter 已实现；没有同字段 Core DB 镜像。
- 输入：schema defaults、合法 region/days/timezone/time、边界外数值、错误类型；配置保存请求。
- 步骤：看原生表单默认和输入控件；保存有效项；测试 validation 拒绝非法项；按官方 service 路径确认保存触发 plugin reload；新 invocation、重启后检查 ModuleServices snapshot 和旧订阅。
- 预期：普通项只保存在 AstrBot 生成的 JSON，constructor snapshot 实际影响查询；非法配置不进入 Runtime；保存后重载提示准确；旧订阅与在途 operation不变；不添加 config DB双写。
- 证据与观测：schema hash/keys/types/default/range、redacted native values、save response、plugin reload generation、Runtime snapshot、existing subscription before/after。
- 失败定位：表单保存但Runtime旧值为 main/bootstrap 接线；第二份同字段为 authority bug；用旧多标签表单覆盖时记录 host no-CAS 风险。

#### TC13：既有 Core 配置、生命周期与凭据写入事务

- 前置：复用既有 `_ConfigurationCoordinator`/`ConfigurationService`、`SourceCredentialService`、`AdminAuthorizationService`/`AdminOperations` 与 Core SecretStore；W2-0 冻结真实调用合同和 Core owner 文件边界。测试只用 synthetic credentials，和用户本地文件分开；Host principal mapping 已审查。
- 输入：Core gate true/false、模块 lifecycle enable/disable、KEEP/REPLACE synthetic A→B/CLEAR、stale config revision、SecretStore transition fault；Dashboard plugin scope 与 Core `AdminAuthorizationService` grant 分别测试。
- 步骤：走受支持 AdminOperations 路径读取 module snapshot；通过 revisioned config update 暂停订阅 gate，确认不接新 create、不发生投递但本人可 list/update/cancel；恢复 gate 并确认仅调度未来窗口；单独调用 lifecycle set_enabled false/true，确认保存订阅而停止/恢复模块调度。再保持 KEEP、替换 credential、注入既有 transition/config revision/cleanup 故障、发旧 revision、CLEAR，并重启后经服务检查状态。
- 预期：API/页面/日志永不返回 secret；替换失败留住原 active pair，不激活半组值；stale返回409；CLEAR使凭据不可用；订阅 gate false 时无投递且查询/list/update/cancel 可用，重开不补发暂停期积压；模块 disabled 时命令/投递停、记录保留，管理员重新 enable 后本人可继续管理且不补发暂停期积压；Dashboard scope单独不足以 mutation。
- 证据与观测：config revision、registry revision/module state、gate mode、subscription operation/result、mode、secret reference metadata state、phase/error、reload required、permission decision；不记录 value/hash/digest。
- 失败定位：只遮罩 UI 不证明加密；半组激活查既有 transition/CAS 顺序；false gate 仍投递或无法本人 cancel 为功能缺陷；AdminOperations 或 principal 映射缺失就 BLOCKED，不绕过 AdminAuthorization 或放宽到 username/token。

#### TC14：五页 Plugin Page API 和 UX

- 前置：pages bundle入 ZIP；4.28.0官方接口与 route prefix经source冻结；测试账户能从插件详情页打开 restricted iframe。
- 输入：overview/logs/items/calendar/settings五路由；items 样本 ID `44091`；Logs 样本国服如月怜@潮风亭；无 Core admin grant 与有效 Core admin grant 两种会话；loading/empty/success/partial/unavailable/401/403/409；HTTP 200 但 JSON `status:error`；320px、亮色/暗色、键盘路径。
- 步骤：从插件详情打开页面；验证 ready/onContext/hash route/relative asset；item 页查询 44091 并检查 XIVAPI 主源/Garland 补充详情标签；Logs 页查询同一 CN 角色 DTO；逐页发 apiGet/apiPost 并分别核对 HTTP 与业务 DTO；将任一成功 HTTP 响应替换为 `status:error`、模拟慢网/断网/权限冲突；用键盘导航/移动窗口；尝试提交 actor/owner/target；检查无cookie/LocalStorage/parent DOM访问。
- 预期：五页状态和 §6.1 文案一致；只有 2xx 且业务 DTO 为成功状态才是 API PASS，HTTP 200 + JSON `status:error` 必须显示错误并判该请求 FAIL；item 命令/页面 ID 与规范名及来源标签一致；健康概览只返回允许的最小DTO；无 Core admin grant 时写按钮隐藏或禁用且 mutation 返回 401/403、Core revision/registry revision 不变；有效授权时才执行 mutation；用户不能伪造 actor；个人订阅详情缺失；普通设置跳原生表单；窄屏和两种主题可用。
- 证据与观测：AstrBot/plugin version、page route、API method/status、error code、响应 DTO keys、脱敏截图、viewport/theme/keyboard path、browser storage/parent access布尔检查。
- 失败定位：没有入口查 pages discovery/ZIP；404查 API prefix；HTTP 200 但 DTO `status:error` 是业务失败；403先分 plugin scope/Core policy；onContext被当权限是 fail；视觉通过不等于授权通过。

#### TC15：聊天图片输出与插件页主题隔离

- 前置：真实聊天 text output已通过；若测图片只用许可图片或 synthetic renderer input；不登录真实 QQ。
- 输入：同一 DisplayDocument 的 text-only 与可选 image block；Plugin Page亮/暗主题。
- 步骤：先改 Plugin Page theme，再通过受控 test chat请求同一 item/角色输出；关闭 renderer image capability复验文本 fallback。
- 预期：iframe theme只影响页面；聊天输出走 Core Renderer/MessagePort；无图仍有文本/链接；不复制截图的 QQ背景/气泡或装饰；页面截图不会转成聊天图片。
- 证据与观测：脱敏页面图、Document block kinds、MessagePort result、media type/license/bytes、文本 fallback。
- 失败定位：页面成功不等于 IM 图像送达；图片失败可按文本降级；样式污染聊天查 CSS/asset bundling 边界。

#### TC16：单一 cohort 收尾和凭据隔离

- 前置：TC01–TC15均通过或逐条标清状态；controller/run_id、root、ZIP hash、owner 已固定。
- 输入：同一隔离长 root、单次上传和 restart、脱敏证据索引。
- 步骤：关闭controller进程树；检查listener/child PID与端口退出；只清本轮建的root；生成最小脱敏记录；对照 builder、日志、临时目录和 git change 检查凭据文件排除规则。
- 预期：所有 PASS 属于同包/同 root/cohort；测试无宿主残留；指定凭据文件仍留本机且未复制进交付、ZIP、日志或提交。唯一允许读取它的是 TC06 中 root 的显式内存加载；计划不授权删除或改写该文件。
- 证据与观测：start/end PID/port、root/hash、evidence index、secret path exclusion布尔值。不得读取或记录 secret 文件内容。
- 失败定位：secret进入包/日志/git立即停止并处理泄露；不同 root/hash的结果拆成不同 cohort；清理目标不是本轮创建则不删除。

### 8.2 一次会话验收顺序

1. 冻结源码 commit/worktree diff、测试包 hash、Python/AstrBot/Windows 版本、LongPathsEnabled 新进程值、测试根路径长度和源配置状态。
2. 验证长根现场为空且位于 workspace 内，建立受控 AstrBot 进程树、唯一 listener 与日志/数据隔离；插件安装前读取版本和插件状态。
3. 单次上传固定包，检索真实安装日志和模块信任状态；重启后依次完成 help/status、item、角色 Logs、calendar、订阅 CRUD、投递边界、配置和页面检查。
4. 结束受控进程树，验证端口和子进程均退出；保留脱敏日志/摘要、最小 UI 截图与结果表，清理前逐项确认只删除本轮创建的隔离目录。不得删除原失败现场或用户数据。
5. 如果必须重新构建修改包，只对新 SHA 开启新的验收 cohort；其结果不能覆盖旧包的失败，也不能复用旧 host 激活状态。

### 8.3 来源诊断与塔塔露同环境对照

用户提供的截图证明塔塔露在其环境能返回物品和角色结果，不能继续以历史一次失败断言来源整体不可用。未来先以同输入、同时间窗、同网络出口对照，定位差异后才修解析或来源策略。本轮不执行下列请求。

| 来源 | 当前代码中的职责/定位 | 后续诊断重点 |
| --- | --- | --- |
| `xivapi_items` / `xivapi-v2.xivcdn.com` | 名称候选、ID 基础详情；请求构造见 `features/item_sources.py` 与来源文档 | 中文 language、fields、query 编码一次、直接 ID 是否绕开搜索、HTTP/JSON 字段形状、代理是否实际生效 |
| `garland_items` / `garlandtools.cn` | 获取方式补充；44091 页面和 JSON 接口见 TC04 | 与同 ID 基础记录合并的字段来源；补充失败保留基础结果，绝不冒充完整回退 |
| `ff14_calendar_primary` / `calendar.google.com` | CN/Global ICS 的固定 source variant，见 `features/calendar.py::_SOURCE_INFO` | 实际 region、日期窗口、HTTP 状态/content-type、ICS 完整性、新鲜度与解析层错误 |
| `ff14_calendar_fallback` / `p66-caldav.icloud.com` | 日历备用 ICS，用户已指出可用性差 | 与主源同窗口独立验证；默认建议采用“仅启用通过当前覆盖检查的来源”，没有合格备用就明确无回退，不无限等待备用 |
| FFLogs `cn.fflogs.com` | 客户端认证、国服公开角色 GraphQL | OAuth、GraphQL errors、服务器目录映射、角色映射和展示分层；不请求统计 HTML |

诊断顺序：①确认宿主 HTTP proxy 的实际配置来源和健康，不假定 7890 存在；②冻结塔塔露版本/输入参数，优先源码构造或独立受控实例，不把参考插件装入用户实例；③对照请求 host/path、查询参数、请求头类别、HTTP 状态、响应类型/大小/脱敏 schema；④同请求经项目 SourceHttp，再经模块 mapper，再经真实 AstrBot；⑤首个产生差异的层是修复候选。协议直连成功、塔塔露成功、SourceHttp 成功、消息成功分别记证据，不能相互替代。

只记录 `run_id,source_id,region,request_stage,proxy_mode,elapsed_ms,http_status,content_type,response_bytes,error_code,parser_stage,item_id/event_count,coverage,collected_at,cache_age` 等最小字段；密钥、header 值、代理口令、响应全文和私人数据不记录。DNS/TLS/连接/timeout/429/403/JSON/ICS/schema/映射/发送错误分别归类，不统一吞为 source unavailable 而失去内部诊断；用户只收到简洁中文与可重试建议。

未来自动离线回归用合成 fixture 覆盖故障、权限、CAS、ICS 和并发；在线检查明确 opt-in，不进入普通 CI；真实 UI/IM 验收独立记录。每次修复先运行受影响的既有测试，契约/打包或跨模块变更才扩到对应集成/全量，不为单行文档修改重复 930 项历史测试。定时推送至少验证一次真实受控接收；现有采集间隔默认/最小 900 秒，应记录到期至实际采集/送达延迟，不承诺 08:00 分钟级准点。虚拟时钟用于边界回归，不能顶替这次实收。

<a id="execution-stages"></a>

## 9. 有界阶段与准入

root 负责范围、依赖、基线和文档；按 §9.2 四角色顺序调度，正式源码由唯一 implementer 写入。用户已确认 §9.3 模型分工：主实现/技术指导/独立审查使用 GPT-6.1-Sol/High，UI 设计用 XHigh；可用性不足时不擅自回退。W2-0 至 W2-6 是同一个 W2 的阶段，不是七个各自终验的 W。最终候选冻结且必要问题关闭后，W2 仅安排一次独立 Astra/Medium 验收；finding 仅在 §9.2 剩余轮次内回 implementer 修复并由独立 reviewer 复核，预算已尽且仍有 Blocker/Important 时标 NOT ACCEPTED 并列阻塞，不重置轮次。W1 的 1/1 次数不变。本次只整理并提交已有工作，不调用 Astra 或派发 W2 实施。

阶段之间只有公共合同、文件路径、依赖和验收状态明确后才推进。任何阶段可记录 `blocked_external`，但不得用后续阶段或离线通过掩盖；与阻断无关的已冻结合同工作可独立推进，不能把国际服或备用来源未通过变成国服全部功能的串行锁。

| 阶段 | 目标和交付 | 验收/边界 | 进入下一阶段条件 |
| --- | --- | --- | --- |
| W2-0 接口分组与测试 cohort 冻结 | 核对 worktree、W1 证据与包；按 §4.2 冻结 AstrBot 普通 config、Core `ConfigurationService`/`SourceCredentialService`、`AdminAuthorizationService`/受支持 `AdminOperations` 的字段 authority、revision、事务和错误合同；root 冻结 calendar/item source map 与安全凭据写入路线（ACL 维护 CLI 或 Core-admin host operation），列明 owner/文件白名单 | 只读核对与任务卡；不重复研究 AstrBot schema/Pages；LongPathsEnabled 仍待新进程验证；本地授权文件已创建并由 root 管理，其他执行者不打开；不新建第二配置协调器 | 每字段唯一 authority；现有 Core 服务有明确接线和 revision合同；`TC06-B` 有经过审查的凭据导入/清理路由，否则角色产品验收保持 BLOCKED；ID 44091 可直接进入测试，不等截图字形 |
| W2-1 Windows 长路径与 package registration | 先用 Microsoft 长路径前置和历史 273/284 根诊断，再决定是否改插件 path handling；单独补充“空模块/不信任”清楚错误提示。若需修改 AstrBot 宿主或其进程 manifest 而超出仓库授权，记录 BLOCKED，不改 sibling 源码 | 记录 registry、重启后的 AstrBot/Python 子进程、宿主 executable `longPathAware` manifest、Python 文件访问与 native scanner 的分层结果；安全扫描不得跟随越界 junction/reparse point；真实 package 安装、重启与 FF14 help/status 同根通过；短根只作诊断 | 历史长根标准安装、信任与重启注册通过；否则定位根因/明确外部阻塞，不把 LongPathsEnabled 单值当修复 |
| W2-2 配置项与角色 Logs | 新增普通 `_conf_schema.json` 并将 constructor config 接入 Runtime snapshot；Core owner 复用既有 configuration/authorization/source credential services，通过安全 host `AdminOperations.update_config`/测试器路径写入隔离宿主 secret reference；root controller 显式读取已授权 credential file；CN 公开角色必须经实际插件 SourceCredentialService→SourceHttp→handler→chat 路径 | 普通基础项 AstrBot config 一处 authority；Core gate/secret 一处 Core authority；host 原生 save 后 reload；统计 output 保持关闭；`TC06-A` 仅协议验证、`TC06-B` 产品路径不可用直连 API 替代；Global 可选 | 普通设置实际影响 Runtime；CN 如月怜查询和 rDPS/排名含义经当前来源验证；同 root restart 后凭据仍由插件服务读取，最后安全清理；凭据不进普通 config/log/package |
| W2-3 物品与日历来源验收 | 用 ID 44091 与同次来源规范名验证物品候选/详情；逐地区验证主/备日历、时间窗和 freshness | 不要求按截图字形手动输入物品名；逐 source attribution；未验证备用源显式降级，不用 stale cache 兜底 | ID 与来源数据一致；至少一个 calendar source 被当前窗口实际验证，失败路径可辨识 |
| W2-4 本人订阅和真实推送 | 实测本人私聊 CRUD、CAS 冲突、重启恢复、采集摘要和 Core receipt/发送结果 | 不扩大到网页管理；不使用共享/PUBLIC 授权替本人可见性；目标推送收件人来自受信宿主上下文 | 本人回执、状态持久化、重复/UNKNOWN/取消行为有证据；接收端可见回复/推送单独记录 |
| W2-5 五页 Plugin Page | 交付五个 hash routes、受限 iframe 静态资源、read-only health DTO；credential editor/gate control 仅在 Host principal mapping + Core management auth + 既有 `AdminOperations` config/secret mutation revision 合同审查后启用；无个人订阅详情 | 官方 Plugin Pages GET/POST 接口；plugin scope 不等 Core 权限；亮暗/窄屏/键盘验收；HTTP success 与业务 DTO 分层判定；图片/聊天 UI 分离 | 页面真实从插件详情可见并符合 §6；未完成管理授权时仍可交付无个人详情的健康页，但所有受保护写操作显示不可用/拒绝 |
| W2-6 整片终检与用户试用 | 统一 controller 针对最终 SHA/单一长 root 完成 TC01–TC16；准备回滚包、脱敏证据索引和用户试用入口 | Sol 独立审查，root 对照真实宿主回包；用户试用后单列远端 UI/IM层 | 所有 required 项通过，或列明用户接受的 BLOCKED 边界；不改变 W1 Astra次数/全局 Core Ready |

### 9.1 失败、回滚和阶段重入

若安装器失败或功能回归，保留脱敏日志、数据库/插件配置的路径与摘要，先确认数据仍独立且可恢复，再使用 AstrBot 标准插件更新事务回到上一个可用包。不要通过手删 `sys.modules`、插件数据目录、secret 存储或另一个 AstrBot 进程规避错误。回滚后重启并证明模块/已有订阅未丢失；若状态不明，标为 `INCONCLUSIVE` 并停止可能覆盖的数据操作。

如果真实来源不可达，source 验收可记 `BLOCKED`，但必须证明失败提示清楚、不会静默用 stale 数据；该状态不能变成来源能力 PASS。W2-5 若 Host principal/Core admin mapping 未实现，仍交付五页布局和只读健康页；仅 credential editor/个人数据 API 标不可用，不把已核验的 AstrBot Plugin Page API 写成未验证。

<a id="collaboration-protocol"></a>

### 9.2 需求 / UX / UI / 实现 / 审查执行协议

本协议来自用户本轮明确指令，只改变协作与证据要求，不增加产品范围或实施权限。所有角色先读适用 AGENTS.md、本文相关 REQ/切片、项目地图 `docs/code-architecture/ff14.md` 与相关决策；root 派发精确源码/API/测试范围，避免四方重复扫描全仓。

| 角色 | 唯一职责与交付 | 权限 |
| --- | --- | --- |
| requirements_ux | 用户任务、来源/优先级、REQ 验收、信息架构/流程/状态；变化分析和运行后的覆盖复核 | 只读源码，返回精简基线；不定配色/CSS，不把优化建议升级为需求 |
| ui_designer | 基于已冻结 UX 给布局、视觉主次、组件、主题/窄屏/键盘和状态规格；首版运行后看实际渲染 | 可提前只读检查视觉资产；不另立产品流程，不修改正式源码 |
| implementer | 早期只读可行性；获实施授权后按切片统一写正式源码、测试和开发控制器，验证并交付证据 | 唯一源码写入者；复用技术栈和明确白名单，不静默删需求或增加依赖 |
| ui_reviewer | 对稳定候选独立核验需求、UX/UI、权限/数据/集成、回归与越界 | 不写源码，不替实现者修问题；必须给证据，不以完成声明作依据 |

root 统一维护本文的需求/UX、UI、审查摘要和状态，汇总冲突后给一份修复清单，不与 implementer 同时改源码。原“Core owner / Module owner”改为 implementer 所处理的不同职责切片，不再代表多个写代码 Agent；技术指导可以由另一只读 Sol 角色承担，但该角色若参与某候选的方案指导，就不能兼任该候选的独立 ui_reviewer。四职责必须实际派发；工具不可用时如实说明并顺序履职，不冒充独立审查。

执行顺序：

1. **A 基线**：requirements_ux 先形成 REQ/UX；implementer 可并行查技术约束，ui_designer 可并行查主题/素材。root 等必要上游结果，裁决常规可逆选择并冻结当前切片；权限/隐私/迁移依据不足的部分记 BLOCKED，不影响其它已获授权工作。
2. **B 设计/实现**：ui_designer 基于该 UX 提交规格；root 对齐后才交 implementer。实现中可研究下一片，不能静默改变正在实现的基线。2026-10-01 已获得实施授权，各片仍须先满足自己的接口/权限前置。
3. **C 稳定候选联合复核**：冻结工作树相关文件 hash/包 SHA、REQ 与规格 revision、宿主/浏览器版本、合成或真实环境、操作步骤、截图和测试结果；requirements_ux 查覆盖/流程，ui_designer 查真实渲染，ui_reviewer 独立查验收/风险，三者针对同一候选分别返回。无 commit 权限时用文件 hash，不为冻结候选自动提交。四槽包含 root：实现者交付后让出活动槽，三方可并行；不能四个子角色同时运行而超额。
4. **D 修复收敛**：root 去重并裁决建议，只派发已接受问题；implementer 统一修复，重跑受影响 REQ/状态/流程。默认每切片最多两轮集中修复与复核；计数不因更换模型或候选号重置。第二轮后仍有 Blocker/Important，标 NOT ACCEPTED 并列阻塞；非必要 Polish 留后续。最终 W2 的跨片问题也最多两轮，某问题沿用原轮次，不能挪到整体验收重新计数。

每个问题记录 `ID / Blocker|Important|Polish / REQ / 候选版本 / 证据或复现 / 影响 / 修复方向 / 裁决 / 轮次 / 状态`。Blocker 为核心不可用、严重错误或安全问题；Important 为明确违反验收或显著影响使用；Polish 不阻断本轮。不得用个人审美、降低验收或“文档写完”关闭功能问题。

浏览器验收由 implementer 实测、三方据同一候选核验：进入真实 AstrBot 插件详情→打开 iframe→关键操作及恢复→检查控制台/网络错误，记录 viewport/theme/keyboard path，保存必要脱敏截图。合成 DOM/API、真实宿主集成、实际视觉/交互和真实聊天/IM 分开判定；没有浏览器/截图证据就明确“视觉 / 交互未验证”。评审者有工具时独立复现关键步骤，受限时注明证据审阅而非亲测。本轮不启动浏览器或运行产品。

工作文档优先直接复用本文：§1.1 为 requirements-ux，§6 为 ui-spec，§13 为当期 review/status，不再建三份重复基线。未来截图/详细临时记录确需另存时，root 指定任务目录并在写入前加入 Git **本地 exclude**，如 `.codex/work/FF14-W2/`；不修改共享 `.gitignore`，不对已追踪文件取消追踪。目录写入/排除权限不可用时使用允许的既有本地位置或报告限制，不绕过权限。本轮未新建该目录、未修改 exclude，已有秘密文件及旧证据原样保留。未经用户另行授权不 stage/commit/push。

最终切片报告只写：完成的用户能力与 UX/UI 决策；`REQ → 实际实现位置 → 验证证据/结果 → 状态`；修复/剩余问题；未验证/阻塞/范围外建议。模型能力或文档评审通过不替代产品验收。

<a id="agent-models"></a>

### 9.3 子 Agent 模型分工（用户已确认）

**2026-09-30 用户同意，作为后续派发约束生效。** 下表保留原分工供辨识历史证据；新调用采用已批准列，已完成的旧模型工作不改写为新模型产出。未修改全局 Codex 配置；root 是主会话，不计子 Agent，不自动更改 root 模型。

2026-09-30 已核对 [GPT-6.1 Sol 官方模型页](https://developers.openai.com/api/docs/models/gpt-6.1-sol)：支持 low/medium/high/xhigh/max；官方将其用于复杂编码、电脑操作与专业工作。[模型选择指南](https://developers.openai.com/api/docs/guides/model-selection) 建议按具体任务比较质量/成本，并将 Sol 6.1 Medium 用于可迭代复杂工作、XHigh 用于视觉体系和冲突证据决策。以下是本项目的选型建议，不是已做项目实测的性能结论；不推算 ChatGPT 订阅扣量或承诺延迟。

| 子 Agent 类别 | 原约束 / 历史使用 | 已批准模型 |
| --- | --- | --- |
| requirements_ux：需求/信息架构 | 无单独长期指定；本轮复用主写 Luna/XHigh | **GPT-6.1-Sol / High**：处理需求来源、流程与权限冲突 |
| ui_designer：UI 规格/渲染复核 | 无单独长期指定；本轮 Luna/XHigh | **GPT-6.1-Sol / XHigh**：统一组件、各状态与视觉体系 |
| implementer：正式源码唯一作者 | **GPT-6-Luna / XHigh**，用户明确约束 | **GPT-6.1-Sol / High**：本轮含 Core/Host/UI/加密配置接线，建议统一由它实现 |
| ui_reviewer：独立质量审查 | **GPT-6-Sol / High**，沿用阶段审查约束 | **GPT-6.1-Sol / High**：独立上下文、只读复核同一候选 |
| 技术指挥/架构可行性（需要时） | **GPT-6-Sol / High** | **GPT-6.1-Sol / High**；root 仍负责最终调度，避免多层指挥 |
| 安装/浏览器操作与复现 | 原要求 Luna/XHigh 在 Sol/High 指导下操作 | **GPT-6-Luna / XHigh** 用于已冻结步骤的只读复现；探索性排障交 implementer Sol 6.1/High，不成为第二源码作者 |
| 定向资料整理/机械核对（按需） | 没有单独硬约束，原随主写 Luna/XHigh | **GPT-6-Luna / Medium**；复杂上下文整理才用 XHigh，不另开实现作者 |
| 每 W 最终独立验收 | **GPT-6-Astra / Medium**，仅一次 | **保持 GPT-6-Astra / Medium**；独立新上下文，W1 不重开 |

采用“Sol 6.1 负责核心设计/实现/审查，Luna 负责明确步骤与整理，Astra 保留单次终验”。不同角色可以同模型，但必须是独立职责和上下文，尤其 reviewer 不参与被审实现。实现者交接时先停旧 writer、传递同一冻结候选和白名单，不能增加并行作者；任何模型回退须由用户另行指定。

**可用性边界**：2026-10-01 已实际成功启动 GPT-6.1-Sol/High 的 requirements_ux、implementer 及 XHigh 的 ui_designer，先前 Unknown model 阻断解除。后续若调用再次不可用，应报告具体限制，不暗中以 6-Sol 替代；不自行修改客户端或全局模型配置。

本次提交前实测调用返回 `Unknown model gpt-6.1-sol`。用户另行明确允许 Luna/XHigh 作为临时唯一作者，仅修复 `tests/extensions/test_windows_fs.py` 的导入排序和 `tests/host/test_b05_sdk_bootstrap.py` 的 Ruff 排版；两项已完成并复查，不构成后续实现角色的长期回退授权。

## 10. 文件边界、自审查与提交分组

开始每个阶段前由 root 重新确认当前磁盘 ABI、SDK wheel 和任务卡路径。所有正式源码、测试和开发控制器由唯一 implementer 写入；root 只写工作文档并统筹获准的运行与凭据使用。默认模块切片限 `modules/ff14/` 与 `tests/modules/ff14/`；切换到 Core/Host/SDK 职责前必须冻结对应白名单，不因同一作者而放宽架构边界。W2-3/W2-4 重叠文件串行修改。测试夹具只能是合成/脱敏数据；真实响应只留下必要字段摘要。

### 10.1 W2 文件白名单

路径为该阶段的写入上限；标为“新增拟议”的文件目前尚不存在，创建前由 root 核对目录职责并把准确路径发给 owner。没有列出的共享 Core/SDK/host 文件一律不在授权白名单内。发现真实接口缺口时先暂停对应阶段、更新任务卡和唯一 writer，再实现。

| 阶段/owner | 白名单 | 明确排除 |
| --- | --- | --- |
| W2-0 root 计划/合同 | `docs/next-stage-plan.md`；由 root 另建的精确单片任务卡/来源资产映射（由 root 自行列路径）；现有 Core contracts 的只读核对 | 不改产品实现；不重复研究已核验的 AstrBot `_conf_schema`/Pages API |
| W2-1 Core Windows | `extensions/windows_fs.py`；`main.py`（只在模块信任失败诊断需宿主入口承载时，由 root 单独分配）；`tests/extensions/test_windows_fs.py`；`tests/extensions/test_discovery.py` | 不改 AstrBot sibling 源码、Python/AstrBot 二进制或其 manifest；不清理既有失败现场；不碰 module business logic。若根因超出仓库边界，记录 BLOCKED |
| W2-2 FF14 config/Logs | `modules/ff14/features/fflogs.py`；`modules/ff14/module.py`；`main.py`；`_conf_schema.json`（新增拟议）；`modules/ff14/config.py`（新增拟议）；`adapters/astrbot/ff14_config_adapter.py`（新增拟议）；`tests/modules/ff14/test_fflogs.py`；`tests/modules/ff14/test_config.py`（新增拟议）；`tests/adapters/astrbot/test_ff14_config_adapter.py`（新增拟议）。Core owner 经 W2-0 精确派发，可修改既有 `services/configuration.py`、`services/source_credentials.py`、`services/admin_authorization.py`、`services/admin_facade.py`、`services/admin_operations.py` 与对应现存 `tests/services/test_config_records.py`、`test_source_credentials.py`、`test_admin_authorization.py`、`test_admin_operations.py`；只有确需接线时才新增一个精确宿主 adapter 文件，不新增平行 coordinator | Module owner 不改 `services/`/`infrastructure/`/SDK；生产 secret 不进 `_conf_schema.json`/插件普通 config；本地凭据文件只由 root controller 显式读取且不属于写入产物；任何 Core service 改动必须经 W2-0 单一 owner/合同批准 |
| W2-3 Items/Calendar | `modules/ff14/features/items.py`；`modules/ff14/features/item_sources.py`；`modules/ff14/features/calendar.py`；`tests/modules/ff14/test_items.py`；`tests/modules/ff14/test_calendar.py`；`docs/modules/ff14-sources.md`（implementer 返回来源证据，root 更新文档） | 不改共享 HTTP/renderer/SDK；不把未经核验的 fallback URL加入生产清单 |
| W2-4 本人订阅 | `modules/ff14/features/calendar_subscriptions.py`；`modules/ff14/features/calendar.py`（日历 domain 修订需单一 owner）；`tests/modules/ff14/test_integration.py`；`tests/modules/ff14/test_calendar.py`；仅当 Core 缺陷由证据定位时，Core owner 可改 `services/scheduler.py`/`services/delivery.py` 和现存 `tests/services/test_scheduler.py`、`tests/services/test_delivery.py` | 模块 owner 不改 scheduler/delivery；不增加 Plugin Page subscription CRUD |
| W2-5 Plugin Page | `pages/ff14/index.html`（新增拟议）；`pages/ff14/app.js`（新增拟议）；`pages/ff14/styles.css`（新增拟议）；`adapters/astrbot/ff14_pages.py`（新增拟议）；`main.py`（只注册 adapter/routes）；`tests/adapters/astrbot/test_ff14_pages.py`（新增拟议）；`tests/pages/ff14/`（新增拟议，只放合成 DOM/API 状态测试） | 不访问数据库/业务 HTTP/secret bytes；不返回个人订阅清单；不写 `_conf_schema` 普通值；不使用跨域脚本/CDN或读取 parent DOM |
| W2-6 acceptance evidence | `.architecture-refactor/ff14-w2-acceptance.md`（新增拟议，root 维护）；`.architecture-refactor/ff14-w2-controller.py`（新增拟议，仅在无法复用时由 implementer 编写，用合成输入验证；真实凭据读取和运行由 root 获准后统筹，经已审 host operation 写入/清理，不向文件/日志打印） | 不把凭据、access token、完整响应、个人聊天/排名历史写入证据；不上传/安装到用户远端 |

表内 Core owner/Module owner 均指同一 implementer 在相应职责切片内的权限，不派发额外源码作者；文档项归 root。以下接线文件也由 root 冻结当前切片白名单后交 implementer：

- **W2-1 宿主信任/失败诊断**：`adapters/astrbot/bundled.py`、`adapters/astrbot/runtime.py`、`tests/host/test_bundled_extensions.py`、`tests/host/test_astrbot_runtime.py`。已有长路径失败发生在内置安装器并被 Runtime 降级，这两层必须进入诊断范围；不能只改扫描器而漏掉无模块提示。
- **W2-2 配置接线**：`adapters/astrbot/runtime.py`、`modules/ff14/yomihime.manifest.json`、`tests/host/test_astrbot_runtime.py`、`tests/contracts/test_manifests.py`。`ff14_config_adapter.py` 若保留，只能作为宿主的已知内置包装配，Core 不增加 FF14 字段分支；优先命名通用 `config_adapter.py` 并把模块字段说明留在模块清单。新增文件的最终名称在 W2-0 冻结。默认使用既有 `tests/host/` 布局，表中的 `tests/adapters/astrbot/` 属拟议，不为一个文件机械新建平行测试体系。
- **W2-2 Windows 安全维护路线（若采用）**：`scripts/admin_credentials.py`、`scripts/configure_source_credentials.py`、`tests/host/test_source_credentials_cli.py`。先明确 ACL、秘密文件与父目录的身份/链接检查及外部 key 生命周期，再接入受支持 AdminOperations；这与本轮已保存的本地输入文件是两件事。页面替代路线需要同等授权证明，不能直接调用私有 coordinator 绕过入口。
- **W2-5 打包/页面接线**：`scripts/build_dashboard_zip.py`、`scripts/build_release.py`、`tests/host/test_b05_sdk_bootstrap.py`、`tests/packaging/test_release_build.py`。当前 allowlist 没有 `_conf_schema.json`/`pages/`，未来必须显式列入已审页面文件并测试缺失/越界/密钥排除；不能笼统把全仓加入 ZIP。`_conf_schema.json` 由 W2-2 的同一 owner 持有；W2-5 不重复修改同一普通配置定义。

若必须新增公开 SDK 合同，先由 root 明确具体 `yomihime_sdk/api/<file>.py`、合同测试、版本/兼容消费者及 wheel 重建/pin 路径，独立审查后再改；当前白名单没有默认授权任意 SDK 修改。未新增公共合同则不无故重建 SDK。UI 示例/测试、开发控制器和本地凭据都不是产品运行包内容。

Core 配置 revision/SecretTransition 已由 `services/configuration.py` 提供协调层，生产来源凭据已由 `services/source_credentials.py` 提供读取服务；不得重建第二套配置协调器，也不得让 FF14 module 直接 SQL/SecretStore。所有宿主写操作须经既有 `AdminAuthorizationService`/受支持 `AdminOperations` 接线。若既有宿主管理合同缺少所需操作，先由 W2-0 定义安全最小 operation、单一 owner 与测试白名单，再扩展既有路径；不把 `_ConfigurationCoordinator` 等私有类暴露给 SDK 或页面。

每个提交组结束前检查：模块是否只导入 SDK；宿主类/网络库/SQL/Secret bytes 是否泄漏进模块；本地临时 controller/data 和人工测试 helper 是否有责任人/后续清理点；新增配置是否有 default、范围、迁移/重载说明；普通/AstrBot 与 Core ConfigurationService 是否对同字段形成双权威；既有 secret transition 跨 store 失败语义是否诚实；错误和状态是否可区分；对其他模块/Core Ready 是否产生无意行为变化；公开 role rank 的百分位语义是否准确；证据是否只覆盖声明层级。

提交分为四组，少于执行阶段数：

1. **宿主/路径与构建合同**：W2-0/W2-1；建议 `fix(windows): preserve trusted extension scan on long paths`；附版本矩阵、扫描器实证和隔离安装报告。
2. **配置与公开角色查询**：W2-2；建议 `feat(ff14): expose safe settings and public character logs`；附字段表、角色来源/时间和“无统计页请求”证据。
3. **物品/日历/本人投递**：W2-3/W2-4；建议分别或成对提交，仅在 Core/Module 文件归属允许时组合；附来源分状态、CRUD CAS 与重启/receipt evidence。
4. **页面/文档/验收材料**：W2-5/W2-6；建议 `feat(ui): add bounded FF14 plugin page` 或仅适用实际交付的文档提交；附宿主版本、授权调用路径和可访问性手工检查。

每组只在用户授权提交时提交；提交前确认工作区只有预期文件。禁止自动 commit、push、tag、release 或把测试包上传到远端实例。

2026-10-03 用户授权“先按规则提交一波”。本次仅本地阶段提交，不推送或发布。现有 Core/SDK/Host/页面、构建成员清单和测试相互依赖，按四组机械拆分整文件会产生缺文件或 SDK pin 不一致的中间版本；经独立提交审查，合为一个完整提交，正文按上述四个逻辑领域说明。公开文档及已追踪导航同提交；临时控制器、凭据、数据库、备份、测试日志及构建产物保持本地忽略。提交前仅整理格式与对应 SDK 字节校验，不趁机修改延期功能；404c既有安装证据不外推为格式后新工件已经部署。

## 11. 自审查、用户验收与结束条件

执行者进入新阶段先读取本计划、[FF14 架构](code-architecture/ff14.md)、[主体 Core 架构](code-architecture/core.md)、[Core Ready 合同](../.coordination/contracts/core-ready.md)、[SDK 导航](module-sdk.md)、`docs/modules/ff14-sources.md`（由 W2-3 来源 owner 建立或更新）、现行任务卡和当前源码。先核对上游/外部接口当前状态，再冻结具体任务白名单；本文中的接口假设不能替代当前实现。

阶段与候选冻结前审查由 §9.3 的 GPT-6.1-Sol/High 独立 reviewer 执行，核对模块边界、权限/秘密、Windows 长路径真实行为、来源 attribution/freshness、payload 大小/timeout、消息降级、日志和本地数据路径、测试器收尾、包 hash/依赖与用户可见文案。W2 最终候选只交一次独立 Astra/Medium，后续 finding 在剩余轮次内由 implementer 修复、reviewer 复核。缺少证据时降低状态，不引用 W1 PASS 推定通过。

结束条件按层记录：长根安装与重启、item/角色/calendar 命令回复、本人订阅及推送、配置保存、页面 UX、用户远端实例各自独立。用户本机隔离 AstrBot 的 UI/API 证据只关闭隔离宿主层；用户试用后仍须处理已知 bug 和必要 UI 调整，再进入独立收尾。全局 Core Ready 是否 OPEN 由其自身所有条件判断，本计划没有授权自动变更。

## 12. 待冻结的实现合同

1. 日历每个 region 的 primary/fallback source map、许可、覆盖窗口和 freshness 由 root 在派发 W2-3 时写入单片合同；备用源当前不可靠，未独立通过前仅可显示 unavailable/unverified。
2. W2-2 implementer 在源码修改前确认既有 `_ConfigurationCoordinator`、`ConfigurationService`、`SourceCredentialService` 与 `AdminAuthorizationService`/`AdminOperations` 的真实方法、revision/SecretTransition 行为和宿主写入入口；若 `AdminOperations` 缺少所需安全操作，按 W2-0 冻结的最小扩展路径补齐现有合同，不新建 coordinator、不使用泛路径派发。`TC06-B` 的本地凭据导入和清理入口未通过 ACL/权限审查前，角色产品验收保持 `BLOCKED`。
3. Host principal mapping 的具体证据/实现路径由 W2-0 任务卡冻结。插件 scope 只保护宿主 API 路由；不能凭 dashboard identity、username、asset token 或 `onContext()` 得出 Core admin、chat actor、subscription owner。
4. 公开角色、source 响应、FFLogs 当前 OAuth secret 是否有效会随时间变化；W2-2 当日分别记录 OAuth/GraphQL 结果。Global region 不是 CN 验收的阻断项。

执行前将本文件与实际实现、宿主、当前外部来源核对；若用户改变范围，更新决策、配置目录、测试矩阵和里程碑的权威章节后再派发。

<a id="document-inventory"></a>

## 13. 文档接续与规划阶段交付记录

本文件是唯一后续范围、决定、设计、测试和阶段状态入口。原件及 13 份整理前文档的 SHA-256 保存在 [归档索引](../.coordination/archive/2026-09-29-w2-plan/README.md) 和 [清单](../.coordination/archive/2026-09-29-w2-plan/manifest.json)，只归档明确列出的文档，没有复制凭据或运行数据。

公开仓库包含当前设计/计划与 [C00 归档索引](../.coordination/archive/C00-index.md)。本文件引用的 `.architecture-refactor/` 和其它 `.coordination/` 验收/任务/原件链接是本地工作区证据，不随本次文档提交公开；远端读者可能无法打开。其历史结论在 §4/§13 摘要中保留，不因本地附件缺失升级验收状态；源码以 `1648565` 及后续提交为准。

| 文档 | 整理后的定位 |
| --- | --- |
| `docs/development-execution.md`、`.coordination/board.md` | 导航到本文，不维护另一份当前状态 |
| `docs/development-plan.md`、`.coordination/contracts/core-ready.md` | 全局架构范围/门禁与历史证据，Core Ready 保持 CLOSED |
| `docs/module-development-plan.md`、`.coordination/tasks/modules/README.md` | 模块范围导航；Steam/HBR/Dota 未被本轮启动 |
| `.coordination/tasks/modules/ff14/README.md`、`FF14-MVP-01.md` | W1 历史摘要与本文跳转；原完整总卡已留档 |
| `.coordination/runs/FF14-W1-execution.md`、FF14 F01–F06 | 只作历史记录；旧中途状态、旧白名单不再派发 |
| `docs/code-architecture/ff14.md`、`work-packages.md`、`orchestration.md` | 当前源码职责/通用协作边界，不与本文争夺执行权威 |
| `docs/modules/ff14-sources.md` | 来源协议和分阶段证据；开头注明当期范围与旧基线边界 |
| `.architecture-refactor/ff14-*-handoff/review/acceptance` 与旧归档 | 原始证据保留，不改写旧 PASS/FAIL；未来新 W 另建证据 |
| 需求、基本设计、SDK、README/公开使用文档 | 稳定参考，不在规划时宣称页面/schema已实现；实际实现后同步用户文档和构建资源 |

| 本轮事项 | 状态 |
| --- | --- |
| 新计划、配置目录、五页 UI、TC01–TC16、W2 阶段与文件责任 | 文档已形成，非功能实现 |
| 旧执行入口与历史原件收束 | 已整理；原件哈希保留 |
| 上一版规划文档审查与静态检查 | Luna/XHigh 主写，Sol/High 审查已收口；旧审查记录：`.architecture-refactor/next-stage-plan-sol.md`。当时 15 份文档、95 个本地链接检查通过，13 份原件哈希验证通过；这些是上一版证据，不替代本次协议修订审查 |
| 本次四角色协议修订 | requirements_ux 复用 next_plan_writer（Luna/XHigh）；ui_designer、implementer（Luna/XHigh）分别完成 UI 规格与只读可行性；ui_reviewer（Sol/High）独立审查。root 整合本文；UX/UI 文档复核无实质问题，收件目标措辞已消歧。独立审查的 Important（Astra finding 与两轮预算关系）已修正并经 reviewer 关闭，无未关闭重要问题。本次两份修改文档的 21 个本地链接与 Git 空白检查通过。这是规格审查，不是实际页面联合验收 |
| 子 Agent 模型再设计 | 用户已批准 §9.3 新分工；本会话 6.1-Sol 调度可用性仍未满足，未修改全局配置或冒用替代模型。旧审查记录保留原模型 |
| `.architecture-refactor/local-secrets/fflogs.credentials.json` | 已按用户指令保存；Git 忽略已验证；ACL 限 workspace owner、SYSTEM 和本地执行账号；不写入文档、归档或构建输入。本轮只创建文件，未重新验证凭据有效性 |
| W2-0–W2-6、TC01–TC16 | 全部 NOT RUN；需要用户后续明确开始实施的指令 |
| 本次已有修改的提交/推送 | 已完成：`1648565` 推送至 `origin/master`，99 文件；本地任务卡/架构草稿/凭据/证据未提交。离线套件 932 项、14 skipped；全仓 Ruff check/format 通过；两处格式修复后的相关用例 38 项、1 skipped；构建通过且 ZIP 137 项内容/排除检查通过。不把这些结果当作 W2 真实宿主或页面验收 |
| W2 实施/发布/部署 | NOT RUN；本次提交授权不包括 W2 实施、tag、Release 或部署 |

后续每项测试填写下面的统一记录。无法满足某一前置时保留 NOT RUN/BLOCKED，明确阻断层，不把存在测试计划当作结果。

```text
case_id / run_id / wave / status:
source_worktree_or_commit / package_sha256 / sdk_pin:
AstrBot / Python / OS / target_root / process_identity:
preconditions / fixture_mode (synthetic | live | host | visual):
input (redacted) / observed_at / duration:
transport_status / business_status / visible_result:
expected / actual / evidence_path:
failure_layer / next_owner / retest_trigger:
```

页面、图片和推送属于不同可见性证据；协议直连、模块结果、真实宿主、实际浏览器/IM分别记录。若同一候选包修订，生成新 cohort；旧失败保留，未受影响离线证据可引用但不冒充在新包上重跑。验收后才处理开发临时文件与版本发布；原失败现场、用户数据和用户指定的凭据文件不属于自动清理范围。

<a id="current-execution"></a>

## 14. 当前执行记录（2026-10-03）

用户已要求调度执行全计划。起点为干净工作树 `0a20110`；正式源码、测试和开发控制器由单一 implementer 串行维护，当前接续者为 `w2_config_implementer`（GPT-6.1-Sol/High），root 统一维护文档。需求与 UX 最初由 `w2_requirements_ux`（High）复核，UI-A 规格由 `w2_ui_designer`（XHigh）形成；本轮分别由 `w2_ui_a_contract` 与 `w2_ui_designer_target` 同档模型接续，均为实际独立子 Agent，未写源码。当前只冻结已经具备上游依据的切片，不将其余合同中的假设当作实现事实。§13 的 NOT RUN/模型不可用仅描述 2026-09-30 规划交付时的状态，不再作为当前派发门禁。

| 切片 | 当前状态及准入 | 集中修复轮次 |
| --- | --- | --- |
| W2-0 | EXECUTING；REQ-01—10 不变，普通配置/秘密/页面入口技术合同核对中 | 0/2 |
| W2-1 | ACCEPTED_LOCAL；R1 独立复核关闭 R01/R02，新包真实长根安装/同根重启及 WebChat 正文通过；不代表浏览器或其它功能通过 | 1/2 |
| W2-2 | TC06-B ACCEPTED_ISOLATED：固定819d联合包真实SET→角色查询→同根重启再查→CLEAR→中文未配置提示全通过，自有进程全停。固定Local私有目录解除测试器Temp阻塞，原guard保持；不含W4后续工作树，不代表用户实例已配置或浏览器通过 | 2/2 |
| W2-3 | 日历 R0 已独立通过，作者55项、独立26项与16选择矩阵通过；联合包官方上传/配置保持/runtime ready通过。真实国服日历两次约20.5秒返回source temporarily unavailable；独立无代理直连主源20.4秒超时、备用源0.3秒HTTP503，与现象一致但不是逐源产品trace。当前来源环境不可用，宿主日历尚未验收 | 0/2 |
| W2-4 | A1/A2/B1/B2/C均已独立本地收束；C R2三方关闭I1/I2。联合755c包独立打包检查通过并已标准安装到19266，原配置保持，overview/settings schema2 ready/applied、gate可运行。真实IM/浏览器视觉交互未验证 | 2/2 |
| W2-5 UI-A | R1 三方复核关闭两项 Important；用户 4.28.2 页面发现/资产/两 GET、非法配置恢复及插件重载通过；视觉/交互未验证 | 1/2 |
| W2-5 UI-B / UI-C | SDK前置、B0b/B0c局部通过；B2最小查询链路局部通过，非Blocker延期；候选包未部署。B0b R2漏两类结果，root给予一次限定R3例外并保留失败历史，见§14.11。UI-C管理principal仍未闭合；不伪造聊天owner，不因拆片重置计数 | 3/2，R3限定例外已用，不自动追加 |
| W2-6 | NOT RUN；Astra/Medium 最终验收尚未调用（0/1） | 0/2 |

### 14.1 W2-1 当前证据与冻结边界

最初诊断：新进程读取 `LongPathsEnabled=1`，工作区 Python 3.12.10/x64 的 manifest 为 `longPathAware=true`。当前系统实际构建为 `10.0.26300`，旧 scanner 精确矩阵仅包含 `10.0.26200`，因此首先返回 `unsupported_environment`。无需扩展路径转换：修正精确矩阵后，273 字符目录/284 字符文件读写，以及 R1 固定包在真实 4.28.0 长根中的标准安装、help/status、同根重启和重复命令均已通过。该 R1 证据不替代 §14.4 新实例验证。

已解锁 `extensions/windows_fs.py`、`tests/extensions/test_windows_fs.py`、`tests/extensions/test_discovery.py`，仅新增当前构建并验证原生 identity/race/reparse/handle/budget；保留旧构建与 Python/GIL 限制。安装诊断范围按 §10.1 的 bundled/runtime 和对应 host 测试，不修改 sibling、宿主二进制或原失败现场。开发控制器为 `.architecture-refactor/ff14-w2-controller.py`；新运行根使用 `ff14-w2-run-20261001` 唯一目录，新构建输出 `dist/ff14-w2/`，历史包不覆盖。

### 14.2 UI-A 规格增量

沿用 §6 五路由。宽度 ≥720px 使用 200px 侧导航，窄屏改为有 label 的原生页面选择器；内容最大 1120px，桌面/窄屏留白 24/16px，操作目标至少 40px。本地系统字体，iframe 不继承父页面样式；通过 bridge 上下文更新自身 `data-theme`，没有宿主上下文时才采用系统主题。键盘焦点可见，跳至正文采用按钮而非占用业务 hash 的锚点。

概览/设置只读最小状态 DTO，不因刷新自动查询外网。Logs/物品/日历在公开入口接通前禁用查询，提供 §1.1 对应聊天命令；复制失败选择文本并提示手动复制。未配置、未检查、未知、失败和成功空数据分别表达，旧数据标旧。当前宿主 bridge 的 `apiGet/apiPost` 没有 AbortSignal/取消接口，错误仅含 message；应用需有界等待、忽略失效旧响应，不能假设 `error.status`。`ready/onContext` 不构成管理权限。

后续在同一冻结候选上分别完成 UX、实际渲染和独立质量复核。浏览器工具初始没有列出可用浏览器；待隔离宿主启动后尝试受支持入口，未取得实际截图前保持“视觉 / 交互未验证”。本轮实施授权允许受控隔离运行，§9 中原规划阶段“不启动产品”语句仅为历史约束。

实际调用内置入口 `getBrowser({id:'iab'})` 返回 `Browser is not available: iab`。不通过其它浏览器自动化技术绕过此工具限制；后续同候选的规格/协议测试可继续，但不能替代渲染检查。

新实例 4.28.2 已由 UI Designer 独立只读复核：页面发现、主题、sandbox 和五页规格沿用。`apiGet("overview", params)` 由 host 添加插件命名空间，后端注册 `/<PLUGIN_NAME>/overview`，前端不重复插件名前缀或手拼 query。成功返回会自动拆出 `.data`，失败仍只有 Error(message)。`onContext` 会立即回调已有上下文，避免与 `ready()` 结果触发重复首次请求；保留其解绑函数。实际导航指引按 §6 修正为插件列表配置齿轮，宿主内部 `open_config` query 不视为 iframe 可用 API。以上为源码核对，不是浏览器验收。

UI-A 经 `w2_ui_a_contract`（6.1-Sol/High，requirements_ux/只读可行性）与 `w2_ui_designer_target`（6.1-Sol/XHigh）接续冻结：

- 仅 GET overview/settings；Runtime 提供窄公共投影，page adapter 不读私有字段、ModuleServices、DB 或 SecretStore。运行状态 ready/not_ready/invalid_config；模块 registered/enabled 为 bool/null；普通配置 applied/valid_not_ready/invalid/unknown，值仅从有效四字段 snapshot 来，无效时 null 且清除旧值。Registry 缺失或读取失败为 unknown，不伪称未注册。
- CN/Global 凭据一律 configured=null/state=unknown，文案“当前页面无法读取凭据配置状态”。当前没有获审的纯元数据读接口，不能借解密来验证。gate 尚未实现，supported=false/enabled=null；不显示假开关。来源只有固定声明清单、declared 三态及 freshness=unknown/last_success_at=null，不把 transport 接线当网络健康。
- 概览顺序：运行、模块、普通配置、凭据、订阅控制、来源。设置用只读 dl 显示四普通值和原生表单齿轮步骤。网页查询按钮禁用，提供已审命令；日历本人订阅只指引私聊，不展示数据。页面标题 24/32px、卡片标题 18/26px、正文 14/20px、辅助文字 12/18px；长内容可换行，焦点 2px 外框/2px 偏移。亮色背景/卡片 #f8fafc/#ffffff，正文/辅助 #0f172a/#475569；暗色 #0f172a/#161f2f、#f1f5f9/#cbd5e1；成功/警告/错误均带文字，unknown 使用中性色。
- 显示读取中、固定错误与重试、超时、明确标旧的缓存；不回显原始 bridge 错误，不轮询、不伪称取消网络请求。ready/onContext 防止重复首请求，换页与超时后忽略旧响应，copy 失败选择文本供手工复制。
- Host plugin scope/JWT 仅保护公共状态读取。实际 JWT 校验没有明确排除 asset token type，不能声称 asset JWT 必定不能访问扩展 API；本片通过无秘密、无个人详情、无 mutation 及无身份推导保持范围。绝不把 username、API Key 或上下文映射为 Core admin。
- 精确源码白名单：新增 `pages/ff14/index.html`、`app.js`、`styles.css`、`adapters/astrbot/ff14_pages.py`、`tests/host/test_ff14_pages.py`、`tests/pages/ff14/test_ui_a.mjs`；接线仅 `main.py`、`adapters/astrbot/runtime.py`、`tests/host/test_astrbot_runtime.py`；打包仅 `scripts/build_dashboard_zip.py`、`tests/host/test_b05_sdk_bootstrap.py`、`tests/packaging/test_release_build.py`。不改 Core、SDK、module 或 schema。候选冻结后 UX/Designer/独立 reviewer 对同一版本复核；没有浏览器截图仍不计视觉通过。

UI-A 的必要 CI 扩项仅 `.github/workflows/build.yml`：verify job 用 Node 24 运行新增的零第三方依赖 JS 测试；沿用现有 Actions 主版本引用风格，`actions/setup-node@v7` 和关闭自动 package cache 已对照官方仓库说明。不新增 npm/package.json/框架或插件运行依赖，不改变 workflow 触发/发布权限。CONTRIBUTING 由 root 同步本地命令；本机 Node REPL 执行与未来 Actions 执行分开记录。

R0 三方均核对 13 个冻结文件与 ZIP `a4143b8e6d6091ed39c472d9ba111a919eca70ded51b6bb1f1abc8cc27f34a6c`。root 合并为 UI-A-I1（物品示例必须为 `/ygl ff14 item 44091`，测试不得固化多余地区参数）和 UI-A-I2（刷新/重试保留按钮节点与键盘焦点，请求结束不抢走已移开的焦点），派发集中修复 R1。无效配置措辞的 Polish 暂留后续。独立 reviewer 本轮实跑 7 host 与 19 合成 DOM 通过；额外 bootstrap 测试的 wheel 构建前置失败未计通过。候选尚未安装到用户实例；真实渲染、交互、Node CLI 与 Actions 均未验证。

R1 ZIP `1ed896aaa6719167dc818f0ba7d1c9b0e8be5d0139d5b10daf208b19432ffc06` 已经三方受影响复核，I1/I2 关闭。独立 reviewer 复跑 24 合成 DOM 和 1 真实 Registry/parser 通过，作者另有 2 打包检查通过；13 文件、144 ZIP 成员及字节/CRC 核对一致。root 标准上传到用户 4.28.2 返回 HTTP 200/business ok；页面发现、入口 HTML 和三项页面资源均成功，overview/settings 返回 ready/applied、FF14 已注册启用、凭据 unknown 和 gate 未接通。保存非法天数 0 后，两 GET 仍可用且不保留旧配置值；finally 还原原四值，状态恢复 ready/applied，显式插件 reload 后 GET 仍正常。没有重启整台宿主或修改其它插件。该证据是实际宿主 API/资源加载，不是浏览器视觉/交互或 UI-B 查询通过。

### 14.3 W2-2 分片合同

W2-2A 先处理普通配置接线，W2-2B 再处理安全凭据入口；二者仍属于同一 W2-2 修复预算，不通过拆分重置轮次。W2-1 已稳定审查，允许修改共享 runtime。订阅 gate 移至 W2-4 与调度/投递一起完成：当前 Core 没有配置 gate 的后台准入，单个 bool 无法防止关闭后迅速恢复时旧队列复活；必须先冻结持久化暂停边界，而非仅在模块 create/evaluator 加判断。

- 普通字段经 `main.py` constructor → `adapters/astrbot/config_adapter.py` → 不可变 host snapshot → `CoreRuntime.module_host_config_snapshots` → `ModuleServicesFactory` 的 SDK `ConfigView.current()` 只读视图。Core 映射按 module ID 通用处理，不出现 FF14 字段分支；host keys 与 manifest Core config_fields 冲突必须拒绝，绝不写 Core repository。
- 四个普通字段规格留在 `modules/ff14/config.py`。默认区域按 §5 仅产生查询提示，不改变现有位置参数语法；日历天数/时区实际影响省略参数的查询，时区/投递时刻实际影响新订阅。显式参数优先，已有订阅及在途调用不改写。根据宿主重载及后台准入缺口，收束原“无害查询默认值”设计：非法新启动配置在 Core/pump/模块启动前整体拒绝，help/status 提示去原生表单修正并保存重载，不能运行后台或使用秘密。有效运行中的快照不被非法输入原地改写，跨实例不伪造旧快照、不新增 last-good 存储副本。此为 REQ-06/10 实现前的安全恢复路径修订，不降低字段验证标准。
- 宿主 `validate_config` 只覆盖类型并可能转换 int，不保证 options/range/IANA/HH:MM 合法。因此 TC12 的非法范围用例按两层判定：原生保存可能成功；Runtime 必须在启动前拒绝，并给出修正与重载提示。不能因 HTTP 200 或配置文件保存成功就声称有效快照或功能恢复。有效值的原生保存→重载→实际消费仍必须通过。
- Core `ff14_subscriptions_enabled` 是独立 gate，仅声明在 manifest 并经既有受信默认值迁移；与普通字段无双写。关闭 gate 保留本人 list/update/cancel 和记录，禁用模块是不同动作；恢复不补发暂停窗口。
- 白名单在 §10.1 基础上增加 `services/core_runtime.py`、`services/module_services.py`、`tests/services/test_core_runtime.py`、`tests/integration/test_b03_services.py`、`modules/ff14/features/calendar.py`、`calendar_subscriptions.py` 与对应现有 FF14 tests。新增精确文件为 `_conf_schema.json`、`modules/ff14/config.py`、`adapters/astrbot/config_adapter.py`、`tests/modules/ff14/test_config.py`、`tests/host/test_config_adapter.py`；不另建 tests/adapters 平行目录。打包 schema 使用既有 §10.1 builder/tests 白名单。无 SDK ABI 修改或 wheel 重建需求。
- W2-2B 安全维护设计已按 §14.6 独立审查，等待 UI-A 收束后由同一 writer 实现：既有 `AdminOperations.module_snapshot/update_config` 和 ConfigPatch 足够表达每区域单个 secret alias。复用 stage→claim→config CAS→finalize→health publish→旧 secret 清理；失败可能需要恢复，不能笼统承诺所有阶段均回滚旧引用。CLI 维护入口还须通过 Windows 身份/ACL/路径正负例，并验证短期 session、独立 admin credential 和收尾。仓库祖先存在其它 writer ACL，不得为跑通测试修改共享祖先权限或静默扩大受信 SID；TC06-A 的内存读取不等于产品写入入口通过。

### 14.4 用户提供的本地测试实例

2026-10-01 用户提供 `E:/AI/Dev/AstrBot/Local_Test_Env.txt`，用于接续测试。root 已通过仅在内存读取的 API 凭据核对 `127.0.0.1:19266`：AstrBot/WebUI 4.28.2，插件列表接口成功，目标插件尚未安装。该实例与前述自建 4.28.0 长路径 cohort 独立，不能混用版本或替代已有重启证据。

允许在该测试实例安装已审固定包、进行目标插件配置/命令/页面验证。root 统筹读取环境文件并仅输出脱敏结果；不把凭据传给子 Agent、文档、日志或交付包。保留实例既有数据、其它插件和宿主设置，不使用会初始化账户、覆盖宿主配置或结束整个实例进程的旧隔离控制器。后续新候选在源码审查和构建验证后再更新到该实例。

首次上传已审 W2-1 R1 包返回 HTTP 200 / business error，未激活目标插件。实际阻断为宿主依赖保护：插件 `aiohttp==3.13.2` 与 4.28.2 的 `aiohttp==3.14.3` 约束冲突。初次状态为 BLOCKED_DEPENDENCY；后续仅修正插件侧声明，并用下面的新包成功复验，没有降级宿主或关闭保护。

只读元数据进一步确认目标为 Windows 10.0.26300 / CPython 3.12.14 / AMD64 64bit，已安装 aiohttp 3.14.3、yarl 1.25.1、cryptography 50.0.2、python-dateutil 2.9.0.post0、tzdata 2026.4；icalendar 尚缺。批准唯一实现者将 requirements 前三项调整为 `aiohttp>=3.13.2,<=3.14.3`、`yarl>=1.22.0,<=1.25.1`、`cryptography>=46.0.3,<=50.0.2`，其余固定值不变；新旧端点须通过 HTTP/AES-GCM 回归，不外推区间所有版本已实测。Windows scanner 仅增加精确 3.12.14 候选，必须使用该真实解释器取得 `WINDOWS_NTFS_QUALIFIED=True` 和实际 native 安全测试结果，不能用整类 skip 或模拟旧版本代替资格。

该兼容增量与 W2-2A 由同一 writer 串行维护。额外源码范围仅 `requirements.txt`、`extensions/windows_fs.py`；测试范围为既有 Windows/discovery、`tests/services/test_http_transport.py`、`tests/storage/test_secret_store_worker.py`、`tests/storage/test_auth_secret_repository.py`，优先直接运行已有有效覆盖。用户实例依赖只通过受支持插件安装处理，不手工修改其 venv 或 launcher 配置。

W2-2A 初候选已冻结：ZIP SHA-256 `f32bfe73c94fbda257001446986feacc99f03709ebf3426028377a7185b67d3a`，源码 23 文件清单在本地验收记录。普通配置回归 145 项通过，新增真实 Core 重载/已有订阅保留另 1 项通过；打包 20 通过、1 环境 skip。实际目标 Python 3.12.14 与新依赖在 E 盘资格为 True，93 项中 86 通过、7 skip，子进程实际退出码 0。C 盘夹具的 runtime/volume 检查成功，但当前沙箱 token 枚举 `C:/Users/eveni` 祖先被拒（Win32 5），资格为 False，不能把 66 通过/27 skip 宣称 C 盘原生安全通过，也不能推断运行宿主的用户 token 同样失败。保留扫描器拒绝行为，待独立审查后用用户实例标准安装与实际模块注册补充目标证据。

后续同 SHA 独立审查完成：23/23 文件及包内字节匹配，16 个关键用例独立通过，无 Blocker/Important/Polish。用户实例标准上传返回 HTTP 200 / business ok，列表 activated=true；进一步以独立的非管理员 Open API 测试身份取得完整 SSE 的 help/status 正文，确认 FF14 模块真实加载。普通配置改为 global/3/UTC/09:15 后，原生保存/重载和读回一致，help 显示 global；天数 0 虽由宿主保存成功，插件状态命令明确提示配置无效、尚未启动。最后还原原四项值并收到正常 FF14 状态正文。没有重启或修改整个用户实例，也没有读取其它聊天记录。独立测试会话记录保留；早期缺少 Open API username 的请求未计通过。此为 Open API/真实插件命令证据，不是 Dashboard 登录或可见 UI 验收；日历默认值的实际数据窗口和新订阅投递仍须后续业务链路验证。

root 已显式读取用户授权的本地 FFLogs 输入，仅内存使用；TC06-A 当日直连 OAuth HTTP 200，公开角色 GraphQL HTTP 200、无 errors、角色匹配且未隐藏，默认 rDPS 返回 5 项 rankings。未访问统计网页、未记录完整响应/令牌，也未向插件普通配置注入秘密。该结果证明凭据及公开角色协议可用，不替代尚未接通的 TC06-B Core 加密凭据产品链路。

### 14.5 W2-3 来源与状态合同

新实例物品 44091 实际链路成功，无已定位客户端缺陷，不为形成改动重写物品来源。日历四 variant 的资格由 root 独立核对参考 URL/区域及当日完整响应、普通/严格解析后冻结，见 `docs/modules/ff14-sources.md`；可以保留四个既有来源，未知 variant 不启用。网络可取不等于官方最新或全集，Google 的诊断代理不静默写入用户宿主。

只修正 `calendar.py` 和 `calendar_evaluator.py` 及现存对应测试；后两项作为 §10.1 精确扩白名单。Reader 与 evaluator 共享固定来源资格，旧持久 payload 不可自称 qualified 越过规则；无 SDK、Core、调度/投递改动。COMPLETE 保留“所选 feed 在计算窗口完整解析/展开”含义，source_observed_at=None 不自动判 stale；部分/失败继续不触发且保留旧 state。跨月仍进行中活动正常保留。

展示实际主/备用来源和获取时间；来源更新时间未知，说明“仅汇总所选公开日历来源，不保证覆盖全部活动”。主源请求失败与部分解析分开；合格备用成功明确已使用备用，未经资格则明确未启用。完整空窗口为“该公开来源在当前窗口未返回活动”；部分空窗口为“来源未完整解析，无法确认当前窗口是否无活动”，不能写“暂无活动”或降成完全失败；完全失败仍 ERROR。保留既有部分 covered_ids 和数据版本兼容，不为了说明字段强制失效全部旧 v1 观察或制造重复推送。

实际 OutputService 对成功/部分结果只渲染 document，不显示 `CapabilityResult.warnings`。因此上述说明必须由安全状态码白名单映射为 `build_calendar_document()` 的 TextBlock，位于来源/获取时间后、活动表前，query 和 evaluator 摘要都覆盖；warnings 元数据可以保留，但不能作为用户可见证据。测试使用现有真实 renderer 检查最终文本，不能只断言 warnings。此项不修改 Core output/MessagePort。

函数级可行性复核确认 evaluator 已有严格 variant/URL 校验，应集中复用为 reader/evaluator 共享资格，不增加 payload 自报 qualified。reader 优先完整来源，再选有活动的部分来源，最后保留已解析的部分空窗；不合并主备。查询的部分空窗可 PARTIAL_SUCCESS，但 collector 继续遵守 SDK 的“PARTIAL 无 covered_ids 则 FAILED”观察约束。旧 v1 没有 warnings 时按空值处理；选用 fallback 足以说明备用，但不能猜测旧主源失败原因。避免 `_attempt_warnings()` 默认制造 primary_source_partial。保持所有数据/key/event 版本、首次 baseline、日期去重与 checkpoint 行为不变；用现有两份 FF14 测试夹具及真实 renderer 覆盖最终文本。

### 14.6 W2-2B Windows 维护合同

只读可行性及独立设计复核已完成，UI-A R1 收束后由同一 writer 实现；R0 已冻结并交独立初审。作者真实 Windows suite 38 PASS/1 既有 symlink 权限 SKIP（其中 10 个新增维护 native 全通过），CLI 19、中文输出 1、打包 22/1 SKIP 及末次 helper 打包 2 项通过；分别保留原始证据，不合并成产品 PASS。ZIP SHA `acfd818aea802dde44662e7f74f02b6a79e13fc7356bdabd2a951eb821ab0b35`，145 成员；文件清单与证据见本地验收记录。复用现有维护入口/Core 管理，不增加依赖、SDK、第二套 coordinator 或产品运行锁。操作者先停止宿主仍是 CLI 前置，自有 controller 用自有进程退出证明；不对用户 launcher 做停机或环境改写。

- Windows guard 从真实 TokenUser 和线程 token 判断身份，拒绝 impersonation/未知矩阵/查询失败；不从用户名、参数、环境或 group 自动信任 SID。可信 writer 仅当前操作员、SYSTEM、Administrators；OS 祖先可识别 TrustedInstaller owner。复用 `extensions/windows_fs.py` 的 NTFS/路径/identity/reparse/handle seam，基于固定 handle 的 OWNER/DACL 授权，拒绝 NULL DACL、不能解释的 ACE、未知 writer、hardlink/别名/跨卷替换。安全描述符与 handle 必须收尾。
- 祖先禁止 outsider 修改 owner/DACL、删除所选祖先/child；只允许在固定链之外创建兄弟目录不等于可替换所选路径。私有代码/data 根内部禁止 outsider mutation，data/secret 还禁止 outsider 内容读取。区分 generic mapping 与 inherit-only，不能放宽仓库共享祖先 ACL。
- 代码只读 pin；固定句柄必须取得实际 READ_DATA（目录为同位 LIST_DIRECTORY）及 READ_CONTROL/attributes，不能只依赖 metadata-only 句柄的 share 标志。DB 以 share READ|WRITE 但不 DELETE 保持 identity，不挡住 SQLite 正常写和 sidecar。guard 在 `_configure()`/`admin_credentials._run()` 首个写入前获取并保持到全部资源关闭，不只在 main 检查。无 skip 参数或可伪造授权 DTO；隐藏输入不降级回显，受控内存调用同样经过 OS guard 和独立 Core admin 校验。
- 独立 session 保留对象 identity、5 分钟 TTL、generation 和两操作白名单/finally revoke；SET 验证外部 32-byte key 后再暂存，region 一 alias 的成对 JSON 与既有 SecretTransition 路径不变。CLEAR 不为展示状态解密。
- 隔离正例候选为当前操作员私有 Temp 下唯一根。root controller 在导入产品前校验同一份内存 ZIP 固定 SHA、封闭成员及复制后字节/祖先链；不从共享 workspace 导入。解释器/现有依赖沿用受信测试前提。key 只存 controller 内存并传入自有 child env，同根重启和 CLEAR 后清除，不写 key 文件或全局环境。不得复制用户实例 DB；首次 DB 由隔离宿主正常创建，维护不重建丢失 DB。
- 精确白名单：`scripts/admin_credentials.py`、`scripts/configure_source_credentials.py`、`extensions/windows_fs.py`、`tests/host/test_source_credentials_cli.py`、`tests/extensions/test_windows_fs.py`、`scripts/build_dashboard_zip.py`、`tests/packaging/test_release_build.py`。共享 helper 若确需新增，先报精确名称。自有 controller 同样由唯一 writer 修改，root 仅执行和记证据。真实 ACL/token/替换/SQLite 写/取消及 handle 收尾测试必须通过，模拟测试不代替 OS 正例。
- 用户实例 Logs 英文回复已归因于 `services/output.py` 静态映射覆盖模块中文 message。额外只准该文件及对应现有 output 测试修改 AUTH_REQUIRED/AUTH_EXPIRED 的固定中文恢复说明；不放开任意 module/upstream error.message，不让通用 Core 文案冒称所有授权失败都是 FFLogs。其它映射不在本片范围。

精确 helper 扩项已批准：`extensions/windows_maintenance.py`，两个 CLI 复用同一 TokenUser/OWNER/DACL/固定 handle guard。`windows_fs.py` 仅给现有底层打开 seam 增加保守默认 access/share 参数，原 scanner 行为和矩阵保持，回归要覆盖。builder/tests 显式纳入该 helper，不扩大成泛目录。后续隔离控制器候选为 `.architecture-refactor/ff14-w2-credentials-controller.py`，仍由唯一 writer 编写并先冻结合同；root 负责读取真实授权输入与执行，不运行在用户 launcher 数据根。

必要打包测试扩项：`tests/host/test_b05_sdk_bootstrap.py` 仅同步 synthetic repository 夹具及新 helper 的必需资产断言；builder 的 `MAINTENANCE_HELPER_FILES` 只列此精确 helper，不扩大目录/后缀规则。既有精确 ZIP 成员、SDK 固定字节检查继续保留。

当前 C 盘祖先枚举限制不能通过放宽 scanner 解决。guard 不枚举无关 siblings，但所有固定 volume/path/by-ID 句柄取得实际 READ_DATA/LIST_DIRECTORY 及 READ_CONTROL/attributes，且不 share DELETE；取得这些权限失败时拒绝，不能退回 metadata-only。逐级验证 OWNER/DACL 与所选 child 不可被 outsider 替换，再打开下一 child，核对 volume-GUID final path、file-ID、link count/reparse 后 by-ID 持有；全链保持到操作结束，私有树内仍严格枚举。代码根必须为实际加载的 `_PLUGIN_ROOT`，可传 plugin_root 不是授权依据。早期 metadata-only 方案已被原生 rename 反例否定，本段以新访问模式取代；实际 token 对应的私有 profile 可用于隔离 cohort，不要求绕过其它用户 profile 的权限限制。

为兼容既有 SecretStore 的原子替换/清理，不能把全部可变数据文件永久 noDELETE pin。代码与祖先使用 share READ；私有数据目录和 DB 全程 share READ|WRITE/noDELETE，沿 DB 链重复打开的数据目录必须保持同一模式；不能把普通祖先也扩成 data。其余已有可变 regular files 用短期只读 pin 排除已存在写句柄/映射，并严格核对 identity/reparse/link/OWNER/DACL 后释放，由私有 DACL 和固定父链禁止 outsider 后续写入。原生探针已定位数据目录 share READ 会阻挡合法 os.replace，改 share READ|WRITE 后 replace/unlink 可行；这不是放宽 DACL，也不允许 share DELETE。正式回归仍须证明代码、祖先、数据目录和 DB 不可被替换，以及 SQLite sidecar、SET/轮转/CLEAR 与句柄收尾；宿主停机前置不变，探针不算产品验收。

两个 CLI 共用关闭等待 helper：Core cleanup_pending/SQLite close timeout 保留原单次有界 close，并带退避继续等待；重复取消不提前释放 guard。未知异常只调用一次并保存首错，必要 transport 收尾后仍须取得既有公开 closed 状态证明，才能重新抛出原异常/取消。未证实全部关闭时保持保护等待，不伪称已关闭；最多提示一次固定收尾状态。此时整体不承诺返回期限，隔离 controller 的总期限只能通过终止整个、归属可证明的维护 child 收尾，不能仅取消 coroutine 后放任后台 worker。

W2-2B R1 独立受影响复核通过，原 close/after 取消及 proof 异常/取消四复现均先保持 guard、取得可靠关闭证明后再传播同一原异常；6 个关键回归通过，无新增 Blocker/Important。R1 ZIP SHA `664d3460c19de07a4bf87d2a8939e82472b7934527f3efee87bc8574ca17e230`，145 项，包内相对 R0 仅 admin_credentials.py 改动；既有 native 与 SDK 字节保持。该结论只关闭维护实现审查，TC06-B 仍未运行，最终 Astra 仍 0/1。

#### TC06-B 自有控制器合同

后续控制器只写 `.architecture-refactor/ff14-w2-credentials-controller.py`，由同一 implementer 编写并独立复核后交 root 执行。旧 controller/runner 仅作参考，不动态执行其 workspace 顶层代码；旧输出、全环境继承和停止逻辑不复用。受信解释器、依赖与指定 AstrBot 源码是明确前提，不声称整机证明。

- 在导入任何产品代码前，以原生 TokenUser/GetUserProfileDirectoryW 定位操作者私有 profile；新建 cohort 时即设置受限 DACL，验证祖先与目标权限。固定 ZIP 只读一次进内存，核对 SHA/封闭成员及解包后的字节；直接放入私有 cohort 的实际插件目录，保持代码 pin 后通过正常 AstrBot loader 启动。该层为私有固定包部署/真实 loader，不能宣称新包已通过标准 upload；不追加一次覆盖受 pin 代码的重复上传。未来若使用上传，必须用同一已校验 bytes。不能靠 env 路径或先导入产品 guard 建立这份前置证明。
- controller 不修改自身 `os.environ`。生成的 32-byte key 仅留内存并传给本 cohort 的 host/maintenance child 显式环境，跨同根重启保持；屏蔽外来 Python/AstrBot/秘密环境。真实输入由 root 读取后经内存管道送维护 child，不进 argv/文件/日志。admin CLI 输入适配保留现有 `_run` 的 guard/repository/finally，不直接 bootstrap repository；Core admin 与 Dashboard 身份各自生成。
- 记录 Popen PID/创建时间、解释器/runner/cohort root，持续保存已观察的 owned descendants；停止前重新核对身份及 listener 归属。root 已退出不代表子树已停止。每个维护 child 有总期限；期限耗尽只终止身份仍可证明的 owned child，不能释放 guard 后继续同进程读写。全部旧进程退出且端口无 listener 后才能维护或重启，归属不足时拒绝，不操作用户 launcher。
- stdout/stderr 持续消费并丢弃；仅记录固定状态/布尔值/计数及固定错误码，不保存 traceback、任意 message、响应正文或 chat 全文。角色 SSE 在内存检查正常结束、角色匹配与查询结果字段；不能拿 help/status 判定器充当 Logs 成功。无第三方 IM 发送。
- 顺序为正常 host 建 DB → 证明停机 → 独立 admin bootstrap/SET → 同 key 启动角色命令 → 同根重启重复 → 停机/CLEAR → 启动确认中文未配置 → owned children 全退出并释放秘密引用。失败也执行可证明的进程收尾，保留脱敏证据，不删除用户指定凭据输入。

上述合同经独立 reviewer 只读复核；原旧控制器的四项 Important 由新合同收束，不代表新控制器实现或 TC06-B 已通过。

controller 输入精确限定 stdin JSON `schema_version=1` 与 `credentials.cn` 的 client_id/client_secret；root 将原本地输入的 fflogs 对象映射为此结构。不接受 Global、任意命令、路径、环境或角色目标；固定验收 `国服 / 潮风亭 / 如月怜 / rdps`。只读核对真实 4.28.2 宿主源码，使用其 3.12.14 解释器/依赖；不继承旧 4.28.0 判断。唯一自测文件为 `.architecture-refactor/test_ff14_w2_credentials_controller.py`，先用无真实秘密的夹具验证权限/固定包/进程边界，独立复核后才由 root 开始真实 cohort。

控制器 R0 已冻结，SHA `c8602ed838a2227c8ed72c54bb416fa8c68652f1c06aedc10923bbc7ce1951b3`，实际解释器的完整 20 项自测通过（13 pure/mock、5 native、2 自有假 HTTP；无真实秘密/宿主）。正常总预算 900 秒，任何 SET 尝试均视为可能已有密文；失败先证明全部停止，且剩余至少 105 秒才补偿 CLEAR。SET/CLEAR 分别核对公开 ConfigSummary 的 configured/unset；物理旧密文清理单独标为未验证。若停止归属无法证明，输出 cleanup_pending 后继续持有 pins/Job；这不是 900 秒内正常返回承诺，外层 root 通过保留的自有 controller process handle/创建时间在外层期限终止该进程，由其 KILL_ON_CLOSE Job 收尾。独立审查通过前不启动真实入口。

### 14.7 W2-4 持久订阅准入合同（独立设计复核通过）

只读分析确认：采集 job 的 config_revision 是部署采集频率版本，不能当订阅 gate 版本；当前 DeliveryEvent 没有可跨重启复核的准入 stamp。优先复用 `(gate 字段最后实际变更 revision, module_runtime_intents.intent_revision)` 组合，而非新增第二个 generation authority。bool 与模块启停分别仍由原 config_entries/runtime intent 单一存储负责，SDK 不增加字段。

- host 仅对字节匹配的受信模块建立通用 module/field 映射，Core 不写 FF14 字段或日期分支。首次初始化显式写入 true 的资格只来自下面的一次迁移窗口，不覆盖既有 false/损坏值；初始化之后缺失或非法值 fail closed。gate 仅允许 KEEP/严格 bool REPLACE；同值写入不改字段 revision/transition time，整体配置 CAS 可以照常推进。重复 ACTIVE enable 沿用既有 no-op。
- gate 实变与 UTC transition time 在既有配置 CAS 的同一 SQLite transaction 提交。生命周期复用已有 intent_revision/updated_at；恢复 cutoff 为当前 enabled gate 与 enabled intent 的较晚时刻。false→true、disable→enable、跨重启均不能把旧 pending/retryable 重新铸为当前资格，SENT/UNKNOWN/receipt 与订阅记录保留。
- 内部 collection lease、delivery event、evaluation checkpoint 保存组合 stamp。claim/commit transaction 检查当前持久 gate/intent，collector/evaluator await 后重查；旧 checkpoint 给模块 previous=None/previous_state=None，但保存仍用真实旧 checkpoint CAS revision。FF14 现有首次完整 baseline 不触发；Core 不清历史去重或硬编码游戏日历日期。
- create 在既有 mutation 和落库事务检查 gate；关闭时本人 list/update/cancel 仍按原 owner/CAS 可用。instant/digest 在最终 claim 和真正进入 sender 时检查；“task 已安排”不能当作“外部发送已开始”。复用 Admission.mutation，在持久检查之后与 MessagePort 调用开始之间不出现可被配置提交插入的调度空隙；不把整个网络等待变成未经分析的全局锁。暂停提交之前已开始的外部发送可完成或 UNKNOWN，不能承诺撤回 host IO。
- digest 只保留当前 stamp 的成员及 `due_at > cutoff` 的未来窗口，不重开历史 envelope/receipt。NULL 旧 stamp 拒绝，不能补当前资格。配置/intent CAS 回滚不推进 stamp，提交后 publication 失败仍以持久状态限制旧工作。
- 候选 migration 为 `infrastructure/sqlite/migrations/0090_subscription_fences.sql`：config_entries 增 nullable subscription_transition_at；collection jobs/delivery events/evaluation states 各增 nullable gate revision/intent revision，不改 SDK JSON。派发前核对迁移编号空缺及实际表名；只做增量迁移，先在自有 cohort 验证，不直接迁移用户实例数据。
- 候选源码白名单：`modules/ff14/yomihime.manifest.json`、`adapters/astrbot/runtime.py`、`services/core_runtime.py`、`admin_operations.py`、`configuration.py`、`subscriptions.py`、`scheduler.py`、`delivery.py`、`b04_runtime.py`、`core/ports.py`、`infrastructure/sqlite/repositories_config.py`、`repositories_subscriptions.py`、上述 migration；若需要，只新增 `services/subscription_gate.py` 封装内部读取准入，不拥有第二写协调器。精确文件名、迁移注册及现存测试路径在派发前再次冻结，不授权泛目录。

此阶段还需将 UI-A 的 gate 状态投影从固定“不支持”更新为实际公共状态，并同步原先 DTO/JS 合同及三方复核；网页写入仍不解锁。必要测试覆盖同值/no-op、快速切换、collector/evaluator/render/route/claim/sender 起跑竞态、重启、旧 stamp、回滚/publication 失败、本人 CRUD 保留、首次 baseline、未来窗口及 UNKNOWN 不重发。当前仅设计候选，不能标记投递或迁移通过。

独立设计初审指出启动反复 seed 缺失 gate 会默认重开，属于 Important。收束候选：0090 原子创建单例 `subscription_gate_bootstrap(singleton=1, phase=pending|complete)`，仅 migration 插入 pending；另建 `subscription_gate_initializations(principal_id,module_id,field)` 复合主键，记录初始化事实而非第二份 bool/revision，不随配置级联删除。取得完整非空 trusted map 后，repository 用一个 BEGIN IMMEDIATE 整批消费 pending：已有字段行原样保留（包括 false/非法 JSON），只对真正缺行插入 true；合法 true 缺 cutoff 时仅补首次 metadata；新增默认字段按同一 target 现有配置版本推进一次，保留 FFLogs 等原字段。所有标记和 complete 与字段写入一起提交/回滚。pending 加已有标记视为不一致，不能自动续写；映射未准备好则不消费。

complete 后只检查、不补字段或标记；bootstrap 缺失/非法也不创建。执行准入同时要求 complete、精确字段标记、严格 true 的有效字段和 fence/cutoff。完整初始化后单独删除字段、标记或 bootstrap，及以后出现新映射，均保持关闭；恢复须另有显式合同。`config_state` 是否存在不作为首次判断。仅唯一 pending 升级窗口允许对缺失字段补默认值；不能区分升级前已删除和从未存在，明确保留这一边界。现有 migration runner 已将 schema 与 ledger 同事务执行，无需改 runner。测试落在现有 SQLite/database、config repository、Core startup 和 subscription repository 用例：新旧库、原子回滚/重试、并发首次消费、false/非法保留、各类丢失后重启、空/不受信 map 不消费。可行性角色核对后，独立 `w2_config_review` 已关闭该设计 Important，无剩余必要设计问题；不代表迁移、并发或实际投递通过，也不消耗 UI-A 轮次/Astra 次数。

W2-4 只读卡的 UX 增量已由 requirements_ux 返回，root 收束为四字段 `supported/enabled/can_run/reason`，不新增重复 state 枚举。enabled 是经初始化与形状校验的 Core 原始 bool/null；can_run 仅表示当前准入条件成立，不证明来源健康、存在订阅或投递成功。有效 false 显示“暂停”；true 且可运行显示“开启”；true 但模块禁用/运行未就绪显示“开关开启，当前不可运行”；字段/初始化/fence 异常显示“不可用”，读取未确认显示“状态未知”，不能误写成用户暂停。当前未接通保持 supported=false 及空值。reason 只用固定安全分类，不带内部记录、路径、revision 或数量。卡片保持只读、标题“订阅推送状态”，配置错误指向原生表单，Core 异常指引管理员检查，不能承诺刷新/重启补回标记；模块正常时暂停仍可本人私聊查看/修改/取消。确切公共 Core 只读 seam、reason 枚举和 API 版本随 W2-4 技术合同冻结，当前不冒充已实现；Designer 正在基于此给已有组件状态规格。

Designer 已给出同一 UX 的最小状态规格：继续复用 card/status、18/26 卡标题、14/20 状态正文和现有主题/断点；开启 success、有效暂停 warning、无法运行 warning、未知/未接通 unknown。已确认配置/初始化异常用现有 `.notice.error`，不假定有 `.status-error` 样式。未接通文案为“当前页面无法确认开关状态”，不能声称没有 Core 总开关；暂停说明保留记录、恢复不补发暂停窗口，暂停前已开始的发送可能完成。不一致组合、未知 reason、false 加 can_run=true 都不能显示成功。概览/设置统一判定；恢复提示无假链接，不增加写控件。相应合成状态/异常输入和已有刷新焦点回归必须覆盖；真实视觉另验。

本轮由`w4_ui_designer`（6.1-Sol/XHigh）实际接续复核：现有组件/主题/断点足够，CSS无需修改；`app.js`仅schema校验与共用`gate()`是必要前端落点，两路由已复用该函数。Core字段/初始化/fence异常指引管理员检查，不暗示刷新/重启会补回；非法组合统一读取错误。最终C冻结后再做同版本三方复核，当前仅设计一致性通过，不是视觉/交互验收。

W2-4 按同一 writer 串行完成三个切片；A1/A2/B1/B2/C已独立本地收束，当前核对联合包的真实宿主路径：

| 切片 | 精确落点与必须保持的边界 |
| --- | --- |
| W4-A 存储 | 0090 编号空缺，MigrationRunner 自动发现，不改 runner。修改 `infrastructure/sqlite/repositories_config.py`、`services/core_runtime.py` 及上述 migration；真实表为 `b04_collection_jobs`、`b04_delivery_events`、`b04_evaluation_states`。配置现有更新会无条件推进字段 revision，gate 同值保护须单独实现。`repositories_subscriptions.py` 的 evaluation state INSERT 必须改显式列名，以兼容新增列。 |
| W4-B 执行链 | 沿用上述精确 services/ports/repository 白名单。runtime intent 键是 package_id + module_id，不能直接拿配置的注册模块 ID 当 SQL key。stamp 只通过内部 ports/repository metadata 携带，`SubscriptionEvaluationSnapshot`、`DeliveryEvent` 属于 SDK，不扩其 JSON 或 DTO。A/B 均完成前，不部署或声称 gate 已生效。 |
| W4-C 只读页面 | Core 增异步公共最小读取 seam；Runtime 和 page handler 随之异步，并在 await 后复查 close/generation。保留已审 `can_run:bool|null`，不改为重复 effective，也不把读未知显示成已知暂停；实际后台准入仍 fail closed。拟用 schema 2，前端兼容 schema 1 的原 unsupported/unknown；reason 随真实读取合同最终冻结。 |

对应现存测试：A 使用 `tests/storage/test_sqlite_database.py`、`test_config_records_repository.py`、`test_runtime_repository.py`、`tests/services/test_core_runtime.py`，并同步 `tests/integration/test_b04_runtime.py` 的 schema 80 断言至 90。B 使用 `tests/storage/test_b04_subscriptions_repository.py`、`tests/services/test_subscriptions.py`、`test_scheduler.py`、`test_delivery.py`、`test_admin_operations.py`、`test_config_records.py`、`test_extension_runtime.py` 和前述 B04 integration。C 使用 `tests/host/test_astrbot_runtime.py`、`test_ff14_pages.py`、`tests/pages/ff14/test_ui_a.mjs`；`tests/packaging/test_release_build.py` 验证 SQL/helper 随包。C 精确新增白名单仅 `adapters/astrbot/ff14_pages.py`、`pages/ff14/app.js` 及上述现存测试；主题/布局沿用已审组件，不新建前端体系。

W4-C 最终公共读取合同：不使用会 `_ensure_state()` 且读取整 target 的 `SQLiteConfigRepository.current()`。新增精确 `executor.run_read`，在同一读事务仅取 bootstrap、精确 marker、指定普通 gate 字段及对应 runtime intent；Core 异步公共 seam 在已有 mutation 中结合 Registry/Lifecycle 状态，不 seed、不修复、不读取 secret 或个人订阅。失败 reason=`state_unknown` 且 enabled/can_run=null；初始化异常=`initialization_invalid`，缺字段=`config_missing`，非普通严格 bool=`config_invalid`，必要 revision/启用 cutoff/intent 异常=`fence_invalid`，均 can_run=false。cutoff 只对当前启用资格要求有效启用时间，不能把保留的合法 false 因无 cutoff 误判异常。合法暂停=`gate_disabled`；true 但 intent 禁用=`module_disabled`；true 但未 active=`runtime_not_ready`；全部条件成立才 reason=null/can_run=true。无受信映射使用 supported=false/其余空值/reason=`unsupported`。异常优先于暂停/开启显示，不返回内部 revision/时间戳/路径。

C按以下函数级合同完成实现及独立复核：`AstrBotRuntime.public_ff14_page_state`改异步并捕获Core实例/Runtime generation；当前Runtime没有generation，需要在初始化更换及关闭首个await前失效，await后复查实例、generation与ready。`FF14Pages._handler`成功/异常两分支均复查closed/generation，迟到结果只返回原固定CLOSED。前端`validateState`接schema1/2，旧schema1严格规范化unsupported；schema2拒绝未知reason/矛盾组合，`gate/render`复用同一卡片。B1若仅返回stamp/None，不能据此猜测页面原因；C需明确结构化纯读结果，保留合法false。相关race/DTO/合成DOM/打包测试沿现有路径，不扩大网页查询或管理权限。工具清单复查仍无浏览器能力，视觉/交互继续标未验证。

C作者八点计划已独立复核，并按6源码/5测试实施收束：`core/ports.py`内部不可变持久状态/公共四字段模型；`SQLiteSubscriptionLifecycleRepository.read_subscription_gate_state`同事务精确读；`services/core_runtime.py`公共异步投影；`adapters/astrbot/runtime.py`、`ff14_pages.py`及`pages/ff14/app.js`异步/generation/schema接线。测试限现有storage B04、CoreRuntime、AstrBotRuntime、FF14Pages、`tests/pages/ff14/test_ui_a.mjs`，打包测试运行但不改builder。B2执行fence/None包装保持，不从它猜公共reason。terminate首await失效后旧initialize不能在await core.start后重新发布bridge/ready；发布前同步复查generation。合法false仅免启用cutoff，不免bootstrap/marker/strict bool/revision/intent形状检查。公开DTO不返回内部intent/revision/cutoff。C R2三方复核关闭卡片规格/C-I2矛盾DTO，无剩余必要问题；SDK/UI-B源码仍未派发。root准备仅本地忽略目录内Node24.21.0便携CLI并核对官方ZIP SHA，供既有JS测试使用，不修改PATH/生产依赖；合成DOM仍不代表浏览器验证。

W4联合验收包SHA `755ccddbeb914c4b5ce2fcbe80f40ec61411fbe5531e4aee87b2ae4ffd0ec28b`，146成员，较819d仅17项W4/README变化、SDK20项字节不变。独立精确成员/CRC/字节/55冻结输入核对通过；root更新README验证状态后标准上传目标19266返回200/ok，上传前后完整配置data相同。overview/settings均schema2、ready/applied、FF14 registered/enabled，gate严格四字段supported/enabled/can_run=true、reason=null。证据`ff14-w4-c-r2-package-manifest.json`与`ff14-w4-c-r2-user-install.json`。只证明实际宿主安装/API状态；未写用户实例FFLogs凭据、未改宿主全局设置，真实IM/浏览器仍未验收。新包FFLogs隔离复验只准备控制器固定artifact绑定，不变更原guard/输入/清理/判定逻辑。

trusted map 仅在 `installation.trusted` 分支，从同次 manifest discovery 取得已审 FF14 bool 字段、预期 descriptor 及 provenance manifest SHA，复制为不可变 composition 输入。installer 已检查包内字节；Core 原 `_matches_trusted_bundled_manifest()` 是 descriptor equality，不能称其自身字节校验。复用 startup 现有 candidate/package/module 唯一匹配，增加 provenance SHA 与字段校验；先验证全部请求映射，再一次消费 pending，不在逐模块 seed 循环内初始化成功子集。空/部分/重复/不受信 map 不消费，complete 后新增映射不补 marker。接线限现有 runtime/Core/helper/config repository，不新增扫描器或修改 installer。

A2 函数级接线计划已核对并派发唯一writer实现。实际manifest为 `modules/ff14/yomihime.manifest.json`，声明已批准的普通 `ff14_subscriptions_enabled`/严格true默认值；ConfigField没有独立type属性，不虚构SDK属性。Runtime trusted分支从同次packaged discovery取得descriptor、字段与sealed provenance manifest SHA；Core先完成完整非空map的candidate/package/module唯一匹配、descriptor、非敏感严格bool默认及provenance/SHA校验，再一次调用A1初始化。精确补充白名单 `infrastructure/sqlite/repositories.py`：仅向现有唯一config repository透传不可变policy及clock，避免setter/平行实例。原ConfigurationCoordinator须在secret staging前拒gate CLEAR/非bool，保持grant/CAS/补偿流程。源码白名单为上述manifest/runtime/repositories及 `services/core_runtime.py`、`services/admin_operations.py`、`services/configuration.py`，A1文件保持冻结。测试采用现有 `tests/host/test_astrbot_runtime.py`、`tests/services/test_core_runtime.py`、`test_admin_operations.py`、`test_config_records.py`（实际coordinator覆盖；不存在test_configuration.py）。A2不接scheduler/delivery或页面，不以存储初始化代表准入生效。

B的只读函数定位已由独立技术角色完成，分两片串行推进。B1接口计划独立核对后已正式派发：create→collection lease→checkpoint→event事务提交。源码限`core/ports.py`、`services/core_runtime.py`、`services/b04_runtime.py`、`services/subscriptions.py`、`services/scheduler.py`、`infrastructure/sqlite/repositories_subscriptions.py`；精确SQL helper留在现有repository内。测试限现有storage B04、subscriptions、scheduler、B04 integration、必要Core composition及共享`tests/fixtures/b04_runtime.py`。保存A2基线，不能把旧W2 diff算成B1。内部EvaluationCheckpoint分开保存真实CAS和模块previous投影：旧CAS=N时expected=N/newstate=N+1，即使模块收到None也不重置CAS。无受信映射时create/claim/commit关闭，不能为旧测试加入None旁路；本人list/revise/cancel保留。旧NULL recurring job仅可在新的合格claim取得stamp，旧lease/event不补资格；历史event冲突整批回滚且cursor/checkpoint不前进。不改SDK、B2/C、A1配置存储或本地controller，不部署/提交。

B2为instant/digest→真实sender起跑，已完成实现及R1独立复核。从event表列读取stamp，digest过滤当前stamp及due_at>cutoff后重渲染；旧FAILED envelope不得无条件重开。现有Admission只是安排coroutine，不能当作MessagePort已开始。最小方案在owned sender内复用mutation，最终持久资格及精确attempt/claim CAS检查后，强持有IO child；child同步标记entered并紧接await MessagePort.send，中间无额外await/排task。握手等待entered或child done，确认开始即释放mutation，网络等待在锁外。开始前取消先撤销child再出锁并drain，记录已知未发；开始后无确定结果记UNKNOWN，重复取消也须等child及归档真实收尾。已开始IO的SENT/FAILED/UNKNOWN/receipt归档只检查原attempt/envelope CAS，不能被后续暂停丢弃。无需扩SDK/MessagePort/Admission；须以安排后暂停、起跑无缝、网络悬挂时配置可提交、取消/超时与digest重渲染验证实现。最初纯asyncio机制检查仅作为设计依据；当前B2代码已通过R1独立受影响复核34项，真实IM另验。

B1独立17/17 PASS、0skip，无Blocker/Important/Polish；包含4个自建SQLite场景，验证写锁等待时gate关闭、多订阅后置event冲突全批回滚及非法值/cutoff/marker拒绝。12文件逆patch精确恢复before SHA，25冻结输入保持。root据此接受B1并正式派发B2；不代表真实IM或最终Astra通过。

B2作者函数计划将精确源码收束到`core/ports.py`、`services/delivery.py`、`services/b04_runtime.py`、`infrastructure/sqlite/repositories_subscriptions.py`；复用B1完整map向windows/deliveries透传，SQL内部提取(fence,cutoff)并保留B1包装，不需要CoreRuntime/output改动。测试限现有`tests/services/test_delivery.py`、`tests/storage/test_b04_subscriptions_repository.py`、`tests/integration/test_b04_runtime.py`及`tests/fixtures/b04_runtime.py`；正例仅synthetic新事件显式赋当前stamp，历史NULL负例保留，不使生产create_event自动补资格。严格due_at>cutoff只用于digest窗口，instant按当前event持久stamp/精确attempt，不虚构SDK due字段。B2按此白名单完成，B1后字节为本片基线；R1只修delivery与其测试，未扩SDK/Admission/MessagePort或实际IM，未打包部署。

### 14.8 UI-B 公开网页入口的前期只读发现（实施以 §14.9 为准）

实际 SDK 无网页 InvocationOrigin；issuer 和宿主 validator 的 COMMAND/LLM_TOOL 依赖真实聊天上下文，当前 Core invoke 还登记聊天路由并调用 output.route。因此三个网页查询不能复用假 COMMAND、LLM_TOOL 或绕过 Gateway 直接调用模块。可行性建议先独立冻结 UI-B0：正式公开查询来源、受信请求 proof、PUBLIC/READ_ONLY 的精确 capability 部署白名单，以及不产生聊天路由/MessagePort/root-output 的有界结果路径；沿用 issuer/Gateway/Admission/Lifecycle/ModuleServices 与 SourceHttp 预算。涉及 SDK 版本/固定 wheel，尚未批准写入；不让 UI-A 或 W2-2B 擅自扩大 SDK 范围。

网页请求只接受 query，或 region/server/character，或 region/days/timezone；不得接收外部 URL/header/actor/target，不能映射 owner/admin。仍待冻结 COMMAND_ONLY 的网页语义、POST body/Origin/CSRF、具体并发/大小/时限与会话失效复核、取消/重载后 flights 收尾合同。验收必须覆盖来源/proof 伪造、越权 capability、PRIVATE/WRITE/订阅拒绝、零聊天发送、禁用/重载竞态、超时/预算释放和真实浏览器。现有切片继续显示网页查询未接通。

后续只读函数定位给出UI-B0候选，尚未合同验收或派发：独立`CoreRuntime/Gateway.invoke_public_web`，adapter固定映射三个PUBLIC/READ_ONLY能力，不允许请求选择module/capability；SDK候选增加`WEB_PUBLIC`来源及显式`COMMAND_AND_PUBLIC_WEB` opt-in，原COMMAND_ONLY不放宽。复用现有CapabilityResult与受控有界DTO，不进入`CoreRuntime._invoke`的route发布/output.route，不创建聊天principal或伪造owner；binder/依赖调用也检查公开只读。SDK升级需要同步固定wheel与bootstrap/builder hashes，精确兼容合同须先复核，不能借W4提前改SDK。Host仍缺受支持的已验证普通Dashboard登录token-kind/proof seam：PluginRequest只有username，现有scope/dashboard-user判断不排asset，不能据此铸造页面proof；不读未验签claim、private请求或宿主JWT secret绕过。该宿主限制独立于SDK技术选择，当前不修改sibling。请求预算/Origin可信来源/服务端deadline及close-drain仍待冻结；bridge导航只忽略迟到响应，不等于取消后台查询。

宿主 4.28.2 授权层须区分：legacy `/api/plug/...` 检查 asset token 路径范围，但 server middleware 对 `/api/v1` 提前跳过；v1 的 ScopeDependency JWT 分支没有同等 token_type/asset scope 检查。root 仅对本插件公共 overview GET 实测：无认证访问 v1 为 401；页面 asset token 作为 Bearer 访问 v1 为 200/business ok；同 token 访问 legacy 为 401。token 只在内存，未访问任何其它插件或管理 API，不修改宿主。UI-A 无秘密/个人详情的 DTO 边界保持；UI-B/C 不能把 v1 scope 当成完整网页授权或 Core 管理身份。

UI-B0独立只读复核提出更小的候选，暂不实施：v1固定查询端点只返回固定同源307至本插件精确legacy非resource路径，不在v1铸proof或查询；仅在公开PluginRequest.path确认为该legacy路径时复用宿主已验证普通Dashboard JWT入口。实际源码AST+公开PluginRequest的无秘密ASGI合成验证通过：普通合成JWT经307到legacy200且POST JSON保持；asset/合成有效API key/无效token最终401、proof/query为零；body伪造path/username/Location不改变host request.path和固定Location；不follow无查询，候选Origin负例拒绝。只替换rate limit/route matcher/key资格与候选handler，未修改或启动真实宿主；首个422是合成endpoint缺Request注解，修正脚手架后通过。此证据不等于真实浏览器redirect/会话撤销验收。仍需冻结可信Origin部署来源、短期proof/expiry、generation与flight取消/drain；不宣称宿主可撤销已签JWT。原SDK opt-in合同还须修正现有非COMMAND_ONLY即允许tool/help的分支，避免新来源意外解锁LLM能力。

expiry窄核对补充（未实施）：公开PluginRequest保留原headers/cookies；在精确legacy非resource路径、宿主已认证username、恰一个标准Authorization Bearer JWT的限定入口，宿主非空Bearer优先且不回退cookie。可绑定同一已验证token字节后仅读exp作为附加截止，不能把未验签claims作为独立授权；拒重复Authorization、cookie-only/非标准Bearer、混合X-API-Key及非法/float/缺失/过期exp。普通Bearer附带cookie时忽略cookie，与host提取一致。实际middleware AST/公开constructor/合成请求20/20通过，无真实token/secret/网络；proof绑定请求/固定能力/generation，截止取短TTL与exp剩余的较小值，await后复核。只补到期边界，注销/撤销仍无host seam，真实浏览器未验证。

UI-B0 UX增量由requirements_ux复核，root采纳为后续合同基线，尚未派发实现：原生配置新增候选`web_public_origin`，标签“网页访问地址（可选）”，默认空。它仅是Host网页访问配置，不进入FF14ConfigSnapshot，不增加平行写存储；缺失/空/非法仅关闭网页查询，聊天与后台保持原四默认值合同，不能复用ordinary_config错误让整插件fail-fast。单个http(s) origin，不允许账号密码、业务路径、query/fragment/通配符；按标准origin规范化默认端口与根斜线，不从Host/Forwarded推断。设置页只读显示有效地址/未配置/格式无效，不回显任意非法原文。概览及三查询页区分未配置、无效、已配置但入口未就绪、当前地址不匹配、读取失败及会话失效；尚不能查询时保留聊天命令。恢复沿原生配置齿轮→填写或修正→保存重载→重开页面/刷新→手动查询，配置完成不自动查外网。REQ-06/07/08/10验收覆盖清空关闭、重载后旧响应拒绝及聊天不受影响；不加多origin、个人订阅、网页管理写入或秘密读取。

UI-B0数据只读核对确认：现SDK无DisplayDocument JSON serializer，聊天GenericDisplayRenderer会读取image/grid资源且只输出text/resource IDs，不能直接用于网页。候选为模块用现有FieldsBlock/TableBlock提供稳定业务语义，Core验证公开只读结果，adapter白名单纯投影title/subject/blocks/sources/timestamps及脱敏status/warnings/error；保留SUCCESS/PARTIAL_SUCCESS/NEEDS_SELECTION/ERROR与截断区别，拒私有/未知对象，不读Image/Grid/Commands资源，不输出路径/资源ID/HTML。物品现6个候选只有中文TextBlock，需结构化item_id/name后才能点击详情，不能拆文案；Logs已有模型的metric/zone/difficulty/partition/spec应显式保留，缺失职业/更新时间显示未知，绝本/零式分类仍需经审来源metadata，不能猜名字或抓统计HTML；服务器歧义需结构化候选映射既有server_hint。日历现最多15条TableBlock/来源/获取时间可投影，窗口/时区/completeness/空窗/截断需FieldsBlock，获取时间不冒充来源更新时间。请求4KiB/响应256KiB是待审候选预算，Core默认30秒及原SourceHttp限额不放宽。该数据合同仅候选，未改变当前C/SDK或来源实现。


755c 联合包 TC06 实跑（2026-10-02）：artifact 窄复核 18/18 PASS 后，root 仅替换 supervisor 两个固定 SHA 并逆替换确认原字符串，运行实际身份/独立私有 cohort。15 秒返回 `cleanup_pending/job_unavailable`；initial_database=true、maintenance_completed=1、owned_stopped=true，credential_set/两次角色查询/reference_cleared/中文未配置均 false，outer finished/controller_exited=true。证据 `ff14-w4-c-r2-tc06-live-01.json`。原控制器在 SET 调用前保守置 may_have_secret，故不能凭失败码声称已清除或没有残留；failed cohort 保留，不扩大权限或盲目重试。旧819d成功不代替新包验证。当前只读定位自有 Windows Job 保护失败，尚无 FFLogs 网络失败证据；用户19266实例及其凭据未改变。



公开日历网络诊断补充（2026-10-02）：只读同一国服 Google 主源，显式本机 HTTP 代理 `127.0.0.1:7890` 返回 HTTP200/181165 bytes/完整 VCALENDAR 外壳/318 个 VEVENT 标记，1.12秒；urllib 直连最终 timeout_or_connect，实际200.09秒（socket timeout=20并非整个请求总期限，不能据此声称20秒总预算）。证据 `ff14-calendar-public-proxy-diagnostic-20261002.json`。未使用凭据、未保存原始内容、未改宿主/插件代理配置；只证明公开源经该网络路径可取，不证明窗口解析、最新/全集、插件查询或实际推送通过。后续若需隔离产品验证，应在自有测试宿主显式代理并独立限制总期限，不修改用户实例全局设置。


### 14.9 UI-B 最小实现基线与切片（B0a 已局部验收）

本节合并实际 requirements_ux、ui_designer、implementer 与独立技术审查的交付；需求/UX 与视觉规格已收束，B0a 合同、pin 修正及样例门控前置已局部验收，证据见本节末尾。此结论不代表网页功能完成。Core/Host 后续片尚未派发，C 的 schema2、W4 已审字节和当前已安装包保持。

| 切片 | 范围与候选落点 | 进入实现前的条件 |
| --- | --- | --- |
| B0 Core/Host 公开入口 | SDK 1.4.0候选，新增 WEB_PUBLIC / COMMAND_AND_PUBLIC_WEB，旧 COMMAND_ONLY 不放宽；独立 Core/Gateway 入口、issuer/Admission/binder/dependency 复核，三能力部署白名单，无聊天 principal/route/output/MessagePort。Host 原生 web_public_origin 单独验证，固定 v1→legacy307 与已验普通 Bearer/Origin/附加 exp proof；有界纯结果投影 | 审查向后兼容、tool/help 非 COMMAND_ONLY 分支、SDK固定wheel/build/bootstrap同步；proof、取消/任务收尾与预算所有权必须先冻结 |
| B1 模块语义 | 现有 items.py、fflogs.py、calendar.py、calendar_evaluator.py；现有 Fields/Table 表达候选、排名语义、窗口/时区/完整性/空窗/截断。无新来源或统计 HTML | 结构化字段合同通过；缺类别 metadata 保留未知，绝本/零式需求保持未通过，不能降低验收 |
| B2 查询页面 | 现有 app.js，只有响应式结果确有需要才扩 styles.css；复用 card/form/notice/status/dl/命令复制 | 同一版本 UX/UI/独立审查；合成 DOM 与真实浏览器分别记录，当前无浏览器工具 |

B0 候选源码白名单：yomihime_sdk/api/{contexts,manifests,version}.py、yomihime_sdk/__init__.py、pyproject.toml；core/{ports,context_issuer,policy,invocation,help_catalog,task_scope}.py；services/{core_runtime,module_services,dependency_calls}.py；_conf_schema.json、main.py；adapters/astrbot/{config_adapter,runtime,ff14_pages}.py 及新增窄纯函数 web_public.py；modules/ff14/yomihime.manifest.json；scripts/build_release.py、build_dashboard_zip.py及必要既有bootstrap固定版本/hash位置（先准确列出后批准）。测试沿现有contracts/context_issuer/invocation/help/dependency/lifecycle、Core/b03、Host/config/pages、bootstrap/packaging。B1使用对应四份既有模块测试，B2沿test_ui_a.mjs，不另建平行测试体系。此为待审白名单，不是直接授权所有候选文件改动。

技术待审重点：全局4/同Bearer2，满额busy；取Core30秒/短proof TTL/token剩余最小期限。现TaskScope在cleanup超时后可能仍有任务存活，需验证最小context-local实际_spawn观察能强持有根与派生任务，实际drain完成才释放额度；不得仅在Gateway返回时释放。close首await使proof/generation失效。插件公开body()无stream，4KiB只是插件处理载荷上限，不声称宿主分配硬限；响应候选256KiB。bridge无AbortSignal，导航只丢弃迟到响应，不能宣称后台取消。宿主无JWT即时撤销seam，现合同只覆盖到期。单一web readiness DTO候选与C schema2分开，避免破坏已验只读状态。

UI规格：物品单“名称或ID”输入，结构化item_id/name候选显式点击详情，不自动选首项；主/补来源分别标识。Logs区域/服务器/角色，rDPS固定文字，server_hint仅来自绑定当前参数的结构化候选；排名保留metric/zone/difficulty/partition/spec，缺职业/更新时间/分类明确未知。日历区域/1–30天/IANA时区，先窗口/来源/获取时间再活动，全天与定时分开；获取时间不是来源更新时间。输入或区域改变即失效旧候选和迟到响应。进入页面、配置恢复或会话恢复均不自动请求外网。

UX两层状态：入口层区分未配置、非法、未就绪、地址不匹配、读取未知、会话失效与无权限；业务层区分未查/加载/待选、成功/完整空、部分/stale/截断、参数错/凭据问题/限流/来源失败/超时。只有来源明确成功且窗口完整才称“当前窗口暂无活动”；部分空保留“无法确认窗口无活动”的既有合同。Logs隐藏/不存在/无排名/口径不支持只能来自明确业务证据，不能由请求失败推断。保留受影响事件/来源、Global未验证标识和聊天替代。登录问题回Dashboard重新登录重开；FFLogs凭据问题找管理员正式维护；仅web_origin错误指向原生齿轮修改→保存重载→重开/刷新→手动查询。重试显式且尊重Retry-After，既有8秒状态读取超时不能直接套用30秒业务查询。

关联REQ01–04/06–10保持未完成验证项：真实来源/凭据链、绝本零式分类、真实307/Origin/过期/禁用重载竞态、零聊天发送、预算释放，以及320px/亮暗主题/长文本/label/键盘焦点与候选操作。REQ05安装验收独立；不以文档/合成验证替代用户能力验收。

UI-B0技术独立复核后有条件准入，先行B0a（SDK合同和完整工件同步），再串行B0b（Core/observer/binder/dependency）、B0c（Host配置/307/proof/投影）。root冻结B0a版本/contract为1.4.0、revision为UI-B0-PUBLIC-WEB，兼容显式保留1.0–1.3；新policy只允许PUBLIC/READ_ONLY，Tool声明严格NATURAL_LANGUAGE_ALLOWED，help仍列出两种聊天命令政策。源码白名单限yomihime_sdk/api/{contexts,manifests,version}.py、yomihime_sdk/__init__.py、pyproject.toml、main.py、scripts/{build_dashboard_zip,build_release}.py、core/help_catalog.py；本片不改FF14部署manifest、不接真实Web入口。准确pin为main.py的版本/revision/兼容列表/SDK_PACKAGE_MANIFEST、Dashboard builder的wheelSHA/entries/METADATA版本、release builder的wheel名称；新hash来自实际新wheel，绝不沿用旧值。

B0a必要测试沿tests/contracts/test_manifests.py、tests/extensions/test_disk_manifest.py、tests/core/test_help_catalog.py、tests/sdk/{test_public_exports,test_empty_template,test_offline_sample}.py、tests/host/test_b05_sdk_bootstrap.py、tests/packaging/{test_sdk_artifact_install,test_release_build}.py；验证新enum、PRIVATE/OWNER/WRITE拒绝、Tool政策、1.0–1.3兼容、help、wheel/ZIP/bootstrap及篡改拒绝。SDK目录及样例无需自动升所有声明；以真实兼容测试决定精确同步。只有唯一writer完成当前本地诊断冻结后才串行开写B0a，不修改正在验证的755c固定包、不部署/提交。

B0a真实安装测试发现两个CLI仍独立固定旧SDK：scripts/configure_source_credentials.py与scripts/admin_credentials.py。root精准扩白名单仅这两文件的version/revision及admin兼容列表常量，不改guard/权限/维护/关闭逻辑；保存before后同步，不能降低真实安装CLI断言。首轮构建相关24项为22PASS/1环境SKIP/1安装CLI失败，保留原证据，修正后重跑安装与现有CLI关键回归。wheel不含这些CLI，d80ac固定SDK产物保持。

B0b补充硬合同：现公开TaskScope.tasks/wait/close是共享scope快照，不能等价代替flight归属。context-local observer必须在_spawn注册后首次yield前同步登记，根与所有派生任务强持有；cancel/deadline/close立即撤销proof/issuer并seal，封闭后新spawn同步拒绝且关闭awaitable或首运行前cancel并持有。根结束且全部实际登记任务done才释放4/2额度。Core现close首次await是取锁，web专用同步fence必须在它之前，不改聊天关闭语义；cleanup_pending保留强引用并可retry。独立审查指出的上述Important均作为实现前合同条件，尚无源码完成或运行通过声明。


TC06无秘密诊断接续：新增本地helper经两项准入修正（允许固定BASE/venv解释器；长驻合成child补足4096读取握手）及独立8项自检后运行，首Assign成功但controller_failed；补封闭failure_detail后独立10项自检通过，再运行证据ff14-job-diagnostic-live-02.json。实测stop阶段、psutil.Error、controller.stop:1140→verify:1023；owned_stopped=true/cleanup_pending=false、无宿主/凭据/网络。原job_unavailable尚未触达，不能归为同因或记修复通过。

独立只读审发现开发控制器退出竞态Important，17项纯AST合成证明最小修法：仅捕获Process/元数据读取的psutil.Error；原identity仍在、原heldhandle PID/创建时间相同且Wait已signaled才returnFalse；活着/未知/身份变化/证明查询失败继续拒绝，不重开PID、不放宽Job。原生证据未记录异常瞬间的signal，因此把退出竞态视为已定位可复现代码缺陷、当前实际失败高度吻合，不声称唯一原生根因。root已排队给同writer在B0a冻结后串行修controller/现有selftest并同步helper固定SHA，审后再测；产品包、用户实例、W4修复预算不变。


B0a R0已冻结并交独立审查（尚未验收）：manifest ui-b0a-sdk-r0-manifest.json SHA 8b22eb7088c99839a52e9f6dd6138bbaac4119e66b3e610ebfebcaa4c209b09e，delta SHA ab1ef94df70981a3f6f7028b87b9715e9fc6e3f9f0e9489080af07c3993d4f2a；20源码/测试输入中17变化、24wheel构建输入。root docs/module-sdk.md be479c36ca848f767158f1eaa93c5c2d9b4f19cecc54d08b05832e805da9507a已冻结；原有两examples README保持。真实wheel d80ac1920546a2dc14696c0523f7b0f5421c488374ad576ac63be835a153c29f（25成员/20SDK），候选ZIP08df9cc77edbf4114293a1d6e1dcffef0cd003ae365310f140f793aed1fd3561（146成员），均在dist/ff14-w2/ui-b0a/，旧755c包保持。

作者记录最终有效覆盖95项：94PASS/1既有WinError1314 symlink权限SKIP；其中50契约和修正pin后的22安装/维护回归通过，首轮CLI失败保留，不伪称最初全通过。Ruff/EOL/CLI非pin AST/20SDK pin与wheel/ZIP字节检查通过。未启动Host/部署/联网/读凭据，SDK新来源只声明合同，三公开查询尚未接入。源码冻结后同writer开始独立本地controller退出竞态修正，B0a与W4修复预算保持，Astra仍0/1。

B0a独立初审已收束，仅1条Important：4个当前SDK测试/夹具漏同步。test_b04_contracts.py与test_b05_admin_contract.py的current-version断言实际失败；b05_runtime.py真实离线构建得到正确d80ac后仍要求旧wheel/hash/安装版本；core_hardening.py在真实安装新wheel后被旧current-version断言挡在Core启动前。这不是有意旧SDK兼容fixture。其余生产权限/契约无必要问题：独立50契约、6固定wheel bootstrap正负例、真实隔离安装/sample/两CLI通过，20before/17变化及patch/wheel25 RECORD+CRC/ZIP146全字节/旧755c仅8member变化/main20SDKpin/CLI非pinAST均核对。

root派发UI-B/C集中R1（预算1/2，子切片不重置）：仅tests/contracts/test_b04_contracts.py、test_b05_admin_contract.py、tests/fixtures/b05_runtime.py、core_hardening.py的当前version/revision/compat/wheelname/hash同步；保留签名、Admin独立revision、旧兼容和反篡改，FF14 manifest与sample声明不变。源码/wheel/ZIP/rootSDK文档冻结；writer先收束当前独立controller修正，再改这4文件并复跑合同/实际安装扫描/CoreHardening probe。初审失败保留，未部署/真实host/浏览器/Astra。


2026-10-02 接续：UI-B0a R1 四处 current-SDK pin 已独立关闭，28 合同与1项无映射 CREATE 拒绝通过。CoreHardening 全5项为3通过/2前置错误，不能称订阅和重启通过。root准入真实样例门控前置片：offline_sample/status 新增普通非敏感 sample_subscriptions_enabled（默认严格true）；未改wheel资源经真实discovery和sealed provenance、固定资源字节验证后，由夹具显式部署完整可信映射。普通inert扫描与可信部署分别验证；如需预置disabled意图，只用正常Core/admin链，禁止SQL seed、伪造信任或放宽receipt。调度fence与due > cutoff走生产路径。SDK ABI/revision维持1.4.0/UI-B0-PUBLIC-WEB，UI-B/C修复计数仍1/2。

root先冻结构建文档：docs/module-sdk.md c4e0cd28847f7ec6aae9bd4b4b4fe08f68a4c970fd512e2bca12086a31e94dc6；examples/offline_sample/README.md 4e2a3290cba179a1f7e35c50903b398a02181692687d5ebb97a2ce59323e17db。唯一writer白名单限样例manifest、tests/fixtures/{core_hardening,b05_runtime}.py、tests/sdk/test_offline_sample.py、tests/integration/{test_b05_core_ready,test_core_hardening}.py、tests/packaging/test_sdk_artifact_install.py；main.py及scripts/build_dashboard_zip.py仅同步实际新工件pins。独立输出dist/ff14-w2/ui-b0a-gate/并保留旧产物，真实安装/native/四CoreHardening场景及打包复验后独立审查。不改产品Core/SDK API/FF14 manifest/CLI，不部署、联网或提交。

TC06本地控制器退出竞态修正已独立验收：controller 9b8fe2497e890b46bb88383e995e33c7711b0bcae7d25133095feb4050897840，仅在原identity、held handle PID/birth保持且已signaled时将psutil.Error视为退出；其余情况仍拒绝。无秘密原生诊断live-03三阶段完成、全部Assign成功，cleanup_pending=false；原job_unavailable间歇问题没有证明根治。新755c包复跑证据ff14-w4-c-r2-tc06-live-02.json：38秒，credential_set=true，first_role_query=false，failure=role_query_failed；补偿reference_cleared=true、owned_stopped=true、maintenance_completed=3、cleanup_pending=false。物理清理未单独验证，重启角色/中文未配置未运行通过。未改用户实例凭据；角色失败缺脱敏细分证据，先只读检查判定器和实际输出合同，不盲重试或记录正文。Astra全W2最终仍0/1，真实浏览器未验证。

TC06只读细分（同755c/9b8fe）：role_query_failed证明query已返回ChatEvidence，HTTP200/Content-Type/SSE绑定及end完整性先前已通过。独立7项纯测试确认已知真实formatter/renderer与成功判定一致（中文UTF8分片、1/40排名及反例），没有证据支持放宽谓词。root批准本地controller诊断增量，排在SDK gate冻结后：输出schema2/query_diagnostics至多3条，固定phase、标题/身份/指标来源/数值等bool、有界计数、完整plain精确匹配Core13种固定文案的enum；未知unclassified、多种mixed。绝不保存正文、身份字符串、token或任意异常；成功规则、补偿CLEAR、停机/Job保护和期限不改。仅现有controller/selftest及helper固定SHA，先独立审再由root运行。upstream_error只是产品统一错误投影，不能据此声称具体网络根因。

SDK gate前置候选已冻结待独立审：ui-b0a-gate-manifest.json SHA9cd88caea17822e598853158e629a977ec3cc1308258ac1a79a8855e9c34f440，delta2aa4a52aa3884aa9c2961471df2a47c859147a72ec7900f3e72e4bbd40967b61。dist/ff14-w2/ui-b0a-gate/内wheel9506b54f94371fd702d034c5320eb2084a05b8d4415d5fd5b5748c15962bdda7、ZIP0efd0116d7e77b4735c40a29747f2bf34b3b8f9271718d2327516f9258a4c197（146成员），比08df仅main资源pin与两个样例资源变化，旧产物保留。作者实际target3.12.14核心/native/sample13/13；artifact/SDK/bootstrap32项31PASS/1既有symlink权限SKIP。后者child exit0，外层成功后读取日志漏UTF8导致GBK解码exit1，单列为报告层失败，未掩成测试失败也未计skip通过。首轮11PASS/2ERROR和no-op探针保留；正常admin先enable再disable形成false intent，关闭后同根map重开通过。生产Core/API/FF14/CLI保持；此为离线集成候选，不是部署、真实推送或浏览器通过。

SDK gate独立验收通过：同一冻结候选target3.12.14独立13/13 PASS、0 skip（15.368秒），包含实际重建/安装、新wheel资源native发现和4个真实Core场景。审查核对全部source/before/build/evidence/artifact hashes、9文件delta、wheel25成员RECORD/CRC、ZIP146完整字节/CRC、main20SDKpins；生产main/builder除pins AST一致，旧产物保留。原inert/未授权断言保留；正常admin enable→disable形成false意图，同根带map重开仍module_disabled，随后正常授权启用；双映射来自真实sealed discovery与原样固定installed资源，reopen复用，scheduler真实产生stamp，无SQL seed或断言降级。无Blocker/Important/Polish。B0a合同、pin修正及样例前置现已收束，UI-B/C修复仍1/2。B0b Core网页入口、B0c Host、B1/B2页面尚未实施；此结果不代表真实AstrBot/IM/浏览器验收。

TC06 schema2诊断局部独立通过：manifest0dd8d6bc0dbf25af9cba14351d48ff02dabc11d91651a5a836d25c3da6f5e133，controller bb08906bdd7549a4148345e712278d1ab276e3074a9a03497937d2259d84cd02。独立28/28（含自有假HTTP）与helper10纯检查通过，另6组秘密哨兵/精确错误/计数边界/未完成SSE占位检查通过；14字段封闭投影，保护及原成功谓词AST保持，无必要finding。

同755c固定包真实live-03（2026-10-02）59秒：initial_database、credential_set、first_role_query、restart_role_query、reference_cleared、owned_stopped均true；两次完整SSE的标题/固定角色服务器/指标来源/数值判定全true。已有真实角色及同根重启链证据，不能把前次role_query_failed的未知原因倒推为已修复。最终CLEAR后启动宿主时原job_unavailable复发，chinese_unconfigured=false，整体status=failed；maintenance3、cleanup_pending=false，物理清理未单独验证。证据ff14-w4-c-r2-tc06-live-03.json。未改用户实例/凭据；原Windows Job间歇故障仍须取立即Win32错误码及root/descendant阶段，不放宽进程保护、不盲重试。初次outer脚本因PowerShell stdin BOM在解析前退出，未读秘密/未启动controller；移除仅脚本文本BOM后才执行这一次实际测试。浏览器/IM未验，完整TC06仍未通过。

B0b实施前只读落点已收束（未派实现）：源码精确限core/{ports,context_issuer,policy,invocation,task_scope}.py和services/{core_runtime,module_services,dependency_calls}.py；TaskScope测试复用tests/core/test_lifecycle.py，不新增平行测试。其余测试限tests/contracts/test_context_issuer.py、tests/core/{test_invocation,test_dependency_invoker}.py、tests/services/test_core_runtime.py、tests/integration/test_b03_services.py。统一命名CoreRuntime.invoke_public_web/Gateway.invoke_public_web，独立于聊天_invoke_host，不创建principal/route/output/MessagePort。默认空的不可变部署白名单；仅消费可信Host composition注入的exact-owned proof验证端口，B0c才mint，不增加另一授权体系，通用issuer.issue(WEB_PUBLIC)和可构造DTO不能绕过。根与依赖都检查部署允许/PUBLIC/READ_ONLY/明确policy和期限；observer在TaskScope._spawn首次yield前登记，runner首指令再查seal，Core root另强持有。全局4/同Bearer2，根与派生实际done后才返还；close首await前web generation失效、seal并取消。Admission/SDK/Host/FF14manifest/output不改。对应伪造proof、无map、越政策依赖、4/2、期限、detached/迟到spawn、重复取消及close前fence均为必验。本段只精确化既定合同，产品源码仍冻结。

Job间歇故障下一取证仅使用现有无秘密helper：首次caller精确OwnedChildren.launch且handle为其root_handle的Assign前固定延迟100ms，整次只一次；不对descendant/未知调用延迟、不重Assign，stdin门闩及原lock保持。保留原调用返回/即时GetLastError/heldhandle状态/phase分类和清理/期限；controller bb08906不改。只增加固定时序标识和纯检查，独立窄审后root运行一次；不再使用真实凭据来盲重试，未复现也不得宣布修复。

无秘密受控时序诊断live-04-delayed已复现Job故障：helper213f46afb40a41006c4c3067055463f5d62a1117a3047903e15bd28218c50bc7先独立pure/AST窄审通过后运行，1秒；root Assign成功、Win32=0、alive/same_job=true/any_job=true；descendant未延迟，其Assign失败、Win32=5、alive/same_job=false/any_job=true。controller.launch:1101抛job_unavailable，owned_stopped=true/cleanup_pending=false、零完成阶段。证据ff14-job-diagnostic-live-04-delayed.json；不涉及宿主/凭据/网络。确证活后代的Job归属冲突，不能由此独断本机redirector源码机制。

最小修法准入（仅本地controller/selftest和helper固定SHA）：自有child argv首项由venv redirector改为既有受信BASE_PYTHON，规范化允许集合原已有BASE，不扩大权限；保留全部Job/身份/后代Assign/门闩/关闭逻辑。共同runner仅在stdin/root校验和audit安装后插入固定venv/Lib/site-packages，之后host/插件路径顺序保持；不依赖环境路径、不执行.pth、不改sys.prefix或共享安装。root实际元数据核验venv/BASE均3.12.14/AMD64/cpython-312，sys.base_prefix都指固定py312根，prefix分别为venv与BASE。目标venv含若干.pth，不推定所有依赖已经可用；先固定依赖导入、无秘密延迟三阶段，再正常宿主建DB/停机smoke，后评估真实TC06。未修改产品包或已验SDK。

BASE候选已局部审：controller86dbf625dcc7e0fc4440ecc4ec8ad414bd058a4c038f4728d187293213a1be97、helperace7821e9e98682970b2bb0afb599eaea8b3f03ba4e5babe977345a63e0dd6f8；独立30/30纯/假HTTP与helper纯检查通过，三处反替换恢复旧完整字节，所有保护保持。root实际BASE只加固定SITE验证aiohttp/quart/psutil/cryptography.fernet/pydantic均从受信SITE导入。相同100ms三阶段无秘密Job实跑live-05-base全部通过，1秒、三root Assign成功且sameJob=true，正常停机无pending。

随后获准的no-secret normal host smoke未通过：ff14-base-host-smoke-01.json，5秒host_not_ready，host_ready/auth_version/database_created均false，owned_stopped=true/cleanup_pending=false。没有maintenance/query/真实FFLogs输入，用户实例未变。基础依赖通过不足以证明全宿主；当前需提取具体导入或启动异常，不推测pywin32、不批量执行.pth、不恢复真实凭据测试。已排只读最小受审诊断，产品/SDK仍冻结。

无秘密startup诊断（ff14-base-host-diagnostic-01.json）取得首错：outer/ModuleNotFoundError，闭集missing_module=pywintypes；限定帧为host initial_loader.py:12及core_lifecycle.py:19，5秒、owned_stopped=true/cleanup_pending=false。内存wrapper保留原runner/audit与initialize原调用/重抛，仅向普通单链接无reparse私有JSON写封闭字段，停机后限4096读取；未改宿主源码/未读真实pair。

root对照本机PyWin32实现并实际BASE -I验证：固定SITE及其win32、win32/lib两Python子目录，另os.add_dll_directory固定pywin32_system32并保留句柄；win32api先导入，再pywintypes/win32event/win32security/pythoncom全部成功且文件均属固定SITE。不执行.pth或改共享环境。唯一writer获准仅将共用runner的audit后依赖接线精确扩为上述目录与DLL句柄，测试前缀同步/helper仅SHA；Job/身份/启动门闩/产品755c保持。候选须局部复核、无诊断wrapper normalhost smoke成功，才恢复真实TC06。

### 14.10 本轮收束（2026-10-02）

- **SDK B0a及样例前置局部验收通过**：SDK 1.4.0/UI-B0-PUBLIC-WEB合同、现有pin修正、真实offline_sample gate与正常Core/admin/scheduler集成完成；独立target3.12.14 13/13、0skip，新候选ZIP0efd0116d7e77b4735c40a29747f2bf34b3b8f9271718d2327516f9258a4c197未部署。工件/源字节固定证据见ui-b0a-gate-manifest.json。UI-B/C集中修复计数保持1/2。
- **755c固定包TC06-B真实隔离链通过**：最终controller0f16c078848cc0dc6605c9340d002e8d7c11a09e2e330d7ca0f2f060c009c7fb，证据ff14-w4-c-r2-tc06-live-04-base.json，63秒。建库、SET、首次角色、同根重启角色、CLEAR、中文AUTH_REQUIRED、owned_stop七项全部true；三段完整SSE/角色与rDPS数值判定通过，status=passed/failure=null/cleanup_pending=false。maintenance3。仍单列physical_cleanup=not_separately_verified，不宣称物理零化。使用的包仍为已部署755c，不冒充新SDK候选已部署/通过宿主验收。
- **测试器问题收束**：无秘密100ms探针曾准确复现venv启动下活descendant的Job Assign Win32=5；BASE直启保持原全部归属保护后同探针三阶段通过。随后真实缺失pywintypes已取得闭集异常证据；只补固定SITE/win32/win32lib及pywin32_system32 DLL cookie，原payload/audit/Job/CLEAR不变。最终独立31/31纯/假HTTP及helper纯检查通过，普通无诊断wrapper host smoke02的ready/auth/version/DB/stop全true（16秒），才执行上述真实TC06。首轮role_query_failed具体原因仍未知，不因后续成功倒推为某项已修缺陷。
- **下一实施片为B0b**：精确8源码/6现存测试落点与硬合同已在§14.9收束，只读方案完成，尚未派实现；随后B0c Host及B1/B2页面。W3真实日历业务/推送、绝本零式分类、UI-C身份接线、浏览器与IM证据保持各自未验状态，不以本轮SDK/TC06通过升级全W2或Core Ready。最终Astra仍0/1，W4集中修复2/2不变。

本轮没有修改用户实例的FFLogs凭据、共享AstrBot/venv或全局环境；未stage/commit/push/tag。已授权凭据输入继续仅留本地忽略文件，临时诊断与运行证据不打包。后续从本节及§14.9继续，不重新使用旧失败工具SHA或把历史包证据套到新工件。

### 14.11 动态模块读取与页面重整（2026-10-03）

**用户新决定**：设计随模块加载变化的页面与配置架构；UI 设计者实际出图，架构与实现者读图评估；优先 Core/模块联调，非 Blocker 问题登记后修。此前 §14.10 的“下一片 B0b”改为 **D0 动态只读目录 → B0b/B0c 查询入口 → B1/B2 业务结果与页面 → 动态配置写入**。这是执行顺序调整，不重开 W1、不撤销已验安全合同，也不把规划/示意图当成实现结果。

本轮实际派发 requirements_ux、架构设计、唯一 implementer（均 GPT-6.1-Sol/High）及 UI designer（GPT-6.1-Sol/XHigh）；稳定候选另交独立 reviewer/High。root 统一文档。W2 最终 Astra 仍 0/1；UI-B/C 从本节初始1/2推进到下述R3限定例外后的3/2，不因D0或新agent重置。

#### 当前需求与验收

| 编号 | 来源 / 优先级 | 验收合同 |
| --- | --- | --- |
| REQ-DYN-01 | 用户动态架构 / P0 | 以 Core 已注册模块及实际生命周期为唯一目录来源；零、一、两个模块均能读取，不再增加每模块 Host 枚举。声明、启用、运行与网页可执行性分别表达。 |
| REQ-DYN-02 | 用户动态页面/配置 / P0 | module_id 驱动模块选择、通用概览、普通配置声明；未知模块不得回退成 FF14 成功。FF14 专业页面只是已打包的展示适配器，通用目录不依赖它。 |
| REQ-DYN-03 | 已有权限合同 / P0 | 首片只读：普通字段仅 name/required，敏感项整项省略；不输出 description/default/current value、输入 schema、秘密引用或私有能力。原 FF14 已审普通值接口保留。声明不是公开值或写权限。 |
| REQ-DYN-04 | 用户美化与设计图 / P1 | 紧凑状态摘要、模块选择和任务入口，诊断折叠；保持宿主外壳与本地技术栈。设计图交架构/实现者实际读取；亮暗、320px、键盘、长文本需分别验证。 |
| REQ-DYN-05 | 动态读取状态合同 / P0 | 加载、完整空、失败、禁用、不可用和模块退出有不同反馈及重试；切模块/刷新/会话变化废弃迟到响应；读目录不触发业务查询。 |
| REQ-DYN-06 | 用户联调优先 / P0 | 先完成真实 Core/Host 对象的离线集成，合成模块覆盖动态性、真实 FF14 覆盖接线；真实 AstrBot bridge、浏览器和 IM 按各自证据另列。 |

#### 架构决定与配置终态

**2026-10-04 最新设计入口**：[T-05 Core 区域配置与市场查询](ff14-market-t05-plan.md)。名称为主入口，显式范围优先，缺范围按 Core cn/global 全服；统一解析、候选续接、区域覆盖 / 部分失败、配置迁移与逐片验收以该修订为准。此前显式 ID / 单服务器限制已被替代；本轮只更新设计，等待确认实施，不扩大查询页管理权限。早期部署与验收状态仍为历史快照。

读取链：受信包发现/注册 → Registry 不可变快照与 Lifecycle → Core 通用只读投影 → AstrBot 受认证页面 API → 通用模块选择/展示。禁止第二套注册表、页面扫描磁盘或直接读取配置数据库。新增模块只要完成既有受信注册便可进入通用目录；可选 FF14 本地 renderer 只改善标签和业务展示，不成为模块目录准入条件。不加载模块提供的任意 JS/HTML。

当前 `ModuleManifest` 没有页面布局声明，`ConfigField` 没有完整控件类型/枚举。D0 复用现有 SDK 1.4，公开 DTO 独立版本化，不通过 default 猜测字段类型，不新增空壳 SDK 扩展。静态原生 `_conf_schema.json` 与模块 Core 配置是不同命名空间，不能宣称前者已能随加载自动改写。

目标配置分层（2026-10-04 用户修订）：Host 仅承担宿主接线；game-link Core 拥有游戏服务默认区域 cn/global，与界面语言、时区、代理和 AstrBot 宿主全局配置无关。FF14 读取 Core 区域，不另存重复开关；日历天数、时区、摘要时间等专属字段由模块声明类型、范围、默认值与文案。Core 统一存取、模块命名空间隔离，敏感字段走既有通道。四项旧 Host 设置分别迁入 Core 区域和 FF14 三项配置；迁移、schema 与管理授权为独立小片，保留原值、唯一权威，不双写，不在 D0 偷做迁移。查询页保持只读，无合法写权限时给恢复说明。

后续最小类型合同在现有 SDK ConfigField/ModuleManifest 边界演进：名称、类型、必填、默认、枚举/范围/格式与声明版本，旧声明可继续只读展示；公开值需字段显式声明并受 Host 部署策略约束，普通值默认仅管理员读写。写入复用 AdminOperations 和 revision 冲突检查。四项 FF14 Host 配置在迁移成功前维持原权威，独立迁移片经授权校验后一次导入 Core 全局区域 / FF14 模块三字段；成功后只以 Core 为权威，失败保留原权威，旧齿轮值不能覆盖新值，既有订阅不随默认配置迁移改变。停用保留配置，卸载移出目录但不自动删配置；同 ID 新版本重新验证声明与配置，旧页面请求失效。读取反映 Core 已加载快照，不承诺目录自动监控或任意第三方热加载。

D0 投影包含 schema_version/catalog_revision、runtime 状态、稳定排序的模块 id/route/category/version/state/reason、PUBLIC READ_ONLY 能力的 id/政策和非敏感字段 name/required。统一字段投影策略由 Core/Host 明确约束；不为每个模块另填一份公开目录白名单。`loaded` 只在 runtime ready、enabled、ACTIVE、身份 epoch 一致且没有 pending cleanup 时成立；disabled 需明确 false；其它状态 unavailable，异常不能伪装空目录。能力声明不代表网页入口已接通，D0 不输出可调用承诺。

页面树：插件概览 → 模块选择 → 模块概览 / 普通配置；FF14 已打包专项视图增加公开角色 Logs / 物品 / 日历。查询入口未接通时保留清楚的聊天指引。桌面一个插件侧栏，窄屏模块和页面选择分开标注；概览优先摘要和任务，长设置步骤留设置页，来源与订阅诊断折叠。空目录、错误和未知状态使用明确文本，不以颜色或绿色“已启用”替代可用性判断。

#### D0 白名单与推进规则

唯一 implementer 可修改 `services/module_catalog.py`（新增纯投影）、`services/core_runtime.py`、`adapters/astrbot/runtime.py`、`adapters/astrbot/ff14_pages.py`、`pages/ff14/{index.html,app.js,styles.css}`；复用 `tests/services/test_core_runtime.py`、`tests/host/{test_astrbot_runtime,test_ff14_pages}.py`、`tests/pages/ff14/test_ui_a.mjs`。不新建平行页面框架，不更改 SDK、模块 manifest、普通配置存储或已冻结 SDK 构建文档。既有 builder 已按 runtime 目录收集 `.py`，先验证新文件收录，只有实际缺口才另行扩精确白名单。

独立审查中的 Blocker（核心链路不可用、秘密泄露、权限绕过、跨模块串数据、失败伪装成功）必须修复。Important/Polish 可进入待办并继续联调，必须保留严重程度、复现/证据、受影响需求及未完成状态；这不把未满足验收条件变成 PASS。若影响整体验收，则最终仍标注未完成，不以用户允许延期为由抹去问题。该推进规则优先于旧文中“任何 Important 都阻止下一切片”的安排。

临时设计、图片和切片证据集中在 `.architecture-refactor/dynamic-modules-ui/`，复用已有忽略目录，不修改共享 `.gitignore`。当前阶段无 stage/commit/push/tag；不改用户实例凭据。设计图不是浏览器截图，合成 DOM 不算真实视觉通过。

**D0 R0 局部验收（2026-10-03 接续）**：后端 7 文件独立 Python 3.12.14 71/71 PASS，另 13 项预算边界通过；前端 4 文件独立 Node 35/35，48 种真实 Core DTO 对照通过。真实 Core 零/一/两模块与 Host FF14 接线、关闭/迟到响应隔离、字段去敏及旧接口回归通过。初次作者新增 terminate 错误预期已保留，独立确认 not_ready/modules=null 符合原合同。设计者与独立 reviewer 均查看实际 Chrome 合成 bridge 截图：深浅桌面、320px iframe 无横向溢出，脚本/资源错误为零；不代表真实 AstrBot 用户会话通过。11 个冻结文件接续核验无漂移。W2 Astra 仍 0/1。

非 Blocker 待办保持未修：D0-UX-01（Important：Host 四项值与 Core 字段声明权威说明）、D0-UX-02（Important：通用配置恢复指引）、D0-UI-05（Important：长模块名桌面标题溢出，scroll6983/client919）；另插件总览摘要、未知模块文案两项 Polish。问题与证据见 `.architecture-refactor/dynamic-modules-ui/review.md`。整片 UI 验收仍不完整，按用户决定继续联调，不消耗新修复轮次来处理这些延期项。

147 成员候选 ZIP `dist/ff14-w2/dynamic-d0-r0/astrbot_plugin_yomihime_game_link-d0-r0.zip` / SHA256 `aa27f20d2fbdeac56a298a31aec78eb6d003197d7a52154ba404f432856e3190` 已经 root 独立逐字节/成员/CRC 验证；打包/bootstrap 22 PASS、1 既有权限 SKIP。标准上传却为 HTTP200/业务失败，旧 overview 仍 ready、无新 catalog；日志 API403，未扩大权限、重复盲上传或重启宿主。当前先做旧 SDK1.3 常驻到新1.4 的离线 bootstrap 复现，不能提前把该假设认定为现场根因。待部署前置解决后再接续 B0b；新包、旧包及所有失败证据保留。

2026-10-03 离线热升级机制已独立复现：固定旧755c包 SDK1.3 正常加载，同进程同路径覆盖新aa27包后，13个SDK模块对象保持原样，原 bootstrap 拒绝 `Unsupported Yomihime SDK contract version`；独立冷进程加载新SDK1.4成功。脚本 `probe_sdk_hot_upgrade.py` SHA256 `af31439af4f2405480649b869171186eb07d4c313324b5a94caa80a270928895`，证据 `sdk-hot-upgrade-evidence.json`（均位于动态UI临时目录）。独立 reviewer 分别重跑 hot/cold 与原证据一致；没有清空 sys.modules、修改校验或启动真实宿主。此结论证明热替换机制，不单凭此认定现场故障唯一根因。

下一验证仅使用新aa27包在私有目录冷启动真实 AstrBot：唯一 writer 复用固定SHA的已审 TC06 controller，全套 Job/身份/路径/停止保护不变；新本地 `cold_host_probe.py` 和机械 binding 只绑定147成员新包、增加三个固定公共GET。合成setup/login/version后读取catalog及旧接口，检查后受控停机；不使用用户凭据、不触碰19266实例、不维护凭据或执行业务查询。先纯检查与独立窄审，再由root运行；默认不进入live。整机停机迁移仍未实施。


2026-10-03 **D0 隔离真实宿主冷启动通过**：固定 aa27/147 成员包在私有 fresh AstrBot 4.28.2 中，host_ready、认证 version、FF14 loaded、目录字段投影、旧 schema2、owned_stop 和完成等 8 项检查全为 true；outer finished/exited，cleanup_pending=false。证据为临时目录内 cold-host-live-evidence.json。wrapper SHA256 为349ece29ccffd43a5d6a28c57b0fa2bc15246ee4a8e25fbaaf4f2d96934120cf，binding 为a0defeb5ebf41baaa470b026c9d17a54adfca90f2e02209c7d72b7e047e2d726；独立 reviewer 复核固定输入和输出。没有使用用户凭据或执行业务查询/maintenance。19266 用户实例仍是旧包；真实用户浏览器、IM 和新业务入口不由此通过。

**B0b 执行与独立审查记录**：唯一 GPT-6.1-Sol/High implementer 按 §14.9 修改 8 个 Core/service 文件、6 个现有测试文件；SDK、Host、业务模块不在该片范围。Core 公共网页入口使用部署能力白名单、身份 proof、PUBLIC READ_ONLY/显式 opt-in、deadline 和 generation fence，4 全局/2 同 Bearer 限额；无聊天身份、路由或 MessagePort。支持的 TaskScope 子任务持有到 actual done，取消/关闭/迟到结果均校验。
- R0 manifest：442cb8784edf24cc0af79dbc8c93ba7ef115f7a03b2d1a2e6909735de2ab946b；delta：5c8c3e188a4546a21b530eec1eb0308c6f66295d46eda75ad68960da4d9584a9。目标 Python3.12.14 为128项127PASS/1既有FAIL/0skip，15新增通过。独立审查发现 Blocker B0b-B01：root done 提前释放 proof/flight，外层发布前不再核验，超时或 close 后仍返回 SUCCESS。
- R2 manifest：f8da65ee8cdefbfda43e0865868da5ee9022baba4eab6d2d80a933343180440f。仅 invocation.py 与现有测试修复发布时校验；132项131PASS/1baselineFAIL。独立28/28通过，但专项9窗口中 PARTIAL_SUCCESS/NEEDS_SELECTION 六种组合仍泄漏过期结果；SUCCESS 三项正确拒绝。因此 R2 明确 NOT ACCEPTED。证据 b0b-r2-independent-status-probe-evidence.json，SHA c1b1653ecb04f212c80724b3f7988a6c7bc43702eb93fb04a354cf1e112f6620。
- root 在既有修复授权和“默认两轮”规则下作出一次限于 B01 状态遗漏的 R3 例外，事前告知用户；这是 root 判断，不是新增用户批准。**UI-B/C 集中修复计数透明保留为3/2，不抹去R2失败，不自动允许后续无限修复。**
- R3 **ACCEPTED_LOCAL**：manifest 004173ff5ff7be2f3eb7a983601dd03cb1575f0bfb15d90b07b95ce2f5fe2e65，delta 67d1a99000e7781126fe53b330f9eba5fc81cf2498b19140df670c4a8fd002b7。两个结果发布门覆盖 SUCCESS/PARTIAL_SUCCESS/NEEDS_SELECTION，ERROR 不变。作者134项133PASS/1baselineFAIL/0error/0skip；独立30/30通过，三个正常状态和九个超时/关闭窗口验证通过，拒绝时无 document/model_facts/pending。证据 b0b-r3-independent-replay-evidence.json，SHA b142d1202354fc37d6e176e4c57b809cca0f71f6e58fef47df0f74642bf3619c。**B0b-B01 CLOSED**；14个冻结文件由 root/reviewer 核验。

**B0b-BASE-01 / Important / DEFERRED_W3**：旧 b03_runtime.py 夹具构造 SQLiteSchedulerRepository 时缺 subscription_gate_bindings，scheduled_claim_and_proof_authorize_persisted_grant_records 在 fence 读取时得到 None。独立 reviewer 在14文件 before 副本复现同一失败，非本次回归或时钟问题；不跳过、不削弱原断言，保留 b0b-baseline-scheduled-evidence.json。未宣称全部回归通过。

**当前 B0c R0：Host 接线实施中，待冻结独立审查。** 精确白名单为 _conf_schema.json、adapters/astrbot/{config_adapter,runtime,ff14_pages}.py、新 web_public.py、modules/ff14/yomihime.manifest.json，及 tests/host/{test_config_adapter,test_ff14_pages,test_astrbot_runtime,test_bundled_extensions}.py。Core/SDK/B0b 冻结，不改 main、builder、FF14 业务或页面源码。
- web_public_origin 默认空；只接受单一 http(s) origin，规范端口/根斜杠，拒绝 userinfo/路径/query/fragment/wildcard，不从 Host/Forwarded 推导。仅剔除此已知 Host 字段后保留原 FF14 四项严格校验，未知键仍拒绝。配置错误仅关闭 web，不影响聊天。独立 GET web-status/schema1；旧 overview/settings/schema2/catalog 不变。
- 固定 POST queries/items={query}、queries/character={region,server,character}、queries/calendar={region,days,timezone}。v1 只做同源固定307至 legacy 非资源路径，不 mint/query。legacy 用实际 PluginRequest.path、Host 认证 username、唯一规范 Authorization Bearer 和唯一正确 Origin；拒绝 API key、资源令牌、cookie-only、重复/混合凭证、伪造目标字段。
- Host 已认证 Bearer 的 exp 仅追加期限约束，不独立冒充 JWT 签名鉴权；拒绝缺失/bool/float/过期 exp。legacy 可信入口起算总30秒上限并取 JWT 剩余期限最小值，包含 body 等待。4KiB 是 JSON 处理界限，不宣称 Host 分配硬上限；纯公开 JSON 响应上限256KiB。固定安全错误不回显原输入/秘密。
- 每个 owned Core 独立 validator。mint 保存 opaque proof/Host generation/固定目标/Bearer摘要/双期限；consume 才绑定 Gateway 传入的 generation 与 exact PublicWebBinding。Host 与 Gateway generation 不作数值等同。is_current 要求 proof/binding 同一对象、已消费、Core身份、Host generation、ready/not closing 和双期限均有效，返回 literal True。initialize/terminate 第一个 await 前关闭旧 authority，Pages.close 停 mint；revoke 幂等，不替 Core 释放 flight。Bearer key 为每生命周期随机盐 HMAC，不保留明文 token，不记录盐/key。
- ff14/ff14 仅 item.lookup、ff14.logs.character、ff14.calendar.query 显式 command_and_public_web/部署白名单，manifest 顶层1.4，output_version 不变。Logs region 映射既有 realm、metric固定rdps；calendar days整数1–30/IANA timezone。正式调用 Core.invoke_public_web，不直调 handler、不经过聊天发送。
- DTO 保留四状态，按白名单输出公开 Text/Links/Table/Fields 及已有标题/来源/时间/警告/固定错误；拒绝 private、资源/未知对象，不输出路径/资源ID/HTML。不解析中文文本伪造候选ID、职业或副本分类；B1丰富语义仍待实现。
- 验证分为实际 Host API 对象/实际鉴权源码的合成 ASGI、真实 Core+bundled FF14 离线 Transport、真实 Host 进程和浏览器四层。目标包括普通 JWT 的307、被拒类型零查询、总期限、同Bearer并发、close前fence、新manifest新slot/旧slot保留。JWT注销无即时服务器撤销接口、bridge无AbortSignal、body先完整读取均明确记录。新 finding 由 root 裁决，不重置3/2历史。

**B2 最小 UX 基线已完成，设计规格已形成，源码尚未派发。** 三个查询页采用手动输入→查询→公开结果→修改/手动重试；进入、刷新状态或恢复会话均不自动查询外网。needs_selection 无结构化ID时指引精确名称/ID或聊天，不造候选按钮。只有完整成功空日历窗口称暂无活动；partial不能确认无活动。未配置/未就绪/登录失效/参数错误/凭据和来源问题各有恢复路径。切页、模块、输入、会话或关闭使旧响应失效，“停止展示”不承诺后台取消。

B2 设计板为临时目录 b2-query-design-board-r1.png，SHA256 83e5a9d9ba16d197053d16c87ab6dd4cc3a915998c107c6887a2259a5b9108c5；三列表示三个独立页面，明确为示意而非运行截图。设计者和 root 已实际看图，架构师/实现者须实际读取后确认差异。复用 D0 主题、原生控件、窄屏单列、公开文本/表格和状态提示，不新增框架。详细基线/规格由 root 维护在已有 requirements-ux.md/ui-spec.md。等待 B0c 冻结通过后才派发页面实现。

2026-10-03 当前 cua.getState 返回 apps=[]/browsers=[]；无受控浏览器。合成 ASGI 或旧 D0 截图不能当 B2 视觉/交互证据。D0 非 Blocker 继续延期；用户19266仍旧包。W2 Astra 最终验收仍0/1，当前不 stage/commit/push/tag。

**B0c R0 ACCEPTED_LOCAL（2026-10-03）**：10文件manifest SHA93e58e1c594967598cde7a586995b5f8d561855c709aeb08b557efe775c05411、delta SHA8045b1e24ed91b75895ac7d6fe110b8a9a359e887b207a58036cc6b5a34e8c22。root与独立reviewer核验全部候选；14Core和6Host来源哈希不变，原4配置语义不变，FF14 manifest仅contract/三policy调整。目标3.12.14独立22/22 PASS；另7身份负例零query+普通Bearer200/1query正控，三数据状态×投影到期/Page.close/Host.close的9尾窗安全拒绝。独立probe证据 b0c-r0-independent-probe-evidence.json，SHA c5727d7f8aea8c9849be7121feec822a2546e85261a69b92b57fe7e6ff278ebd。测试器首次FastAPI注解导致422的无效探针已单列，修测试器并加入正控后才给出上述结果，产品未改。

完整四测试文件为68项64PASS/0FAIL/2既有ERROR/2symlink环境SKIP；两ERROR在before副本独立复现：旧ModuleServices夹具缺config、构建环境缺setuptools.build_meta，保留未解决。尝试既有私有3.12.10构建环境同样缺backend，没有改共享venv。B0c证据是实际Core/FF14离线与合成ASGI，不是新包真实Host/浏览器或外网业务通过。

**B2 R0 已派发**：唯一writer仅改 pages/ff14/{app.js,index.html,styles.css} 与现有 tests/pages/ff14/test_ui_a.mjs；Core/Host/SDK/B1冻结。架构师与implementer均已实际读新图，root统一规格。采用实际apiPost(endpoint,body)，成功直接拿DTO，35秒视图等待，不承诺网络取消。Host bridge只传错误字符串且无即时session事件，记 B2-HOST-01 / Important / DEFERRED；失败统一安全恢复指引，不猜HTTP类型，业务DTO error.code可分类。该缺口和真实浏览器307验证保持未满足，不为通过验收改低原要求。稳定4文件候选后进行同版本UX/UI/独立审查。

**B2 R0 最小链路 ACCEPTED_LOCAL_WITH_DEFERRED；整片 UI 未完整验收（2026-10-03）**：4文件manifest5353bb249cfe830cd368fc215f333d6710b6a849f4b2fb17f814868362ac74cd；作者44/44，requirements_ux与独立reviewer各自44/44。UI designer实际读图并完成同版规格/代码与焦点探针复核，无实际浏览器渲染证据。Core14/Host10未改；前端使用真实Core+FF14离线DTO样本，不造候选或完整日历语义。

| 需求 | 实现位置 | 当前验证与状态 |
| --- | --- | --- |
| REQ-DYN-01/02/03 | services/module_catalog.py、CoreRuntime、Host catalog、app.js动态模块导航 | D0真实Core/Host离线及aa27私有Host冷启动通过；字段类型化与动态配置写入未实现 |
| REQ-01/02/03 | web_public.py、FF14 manifest、app.js三个固定表单/公开结果 | B0c独立22测试、认证/期限探针与真实Core离线三查询通过；B2独立44测试通过。新包真实来源/用户会话未验，B1丰富语义未完成 |
| REQ-06/07/10 | config_adapter.py、_conf_schema.json、web-status、app.js准入/恢复、README | origin缺失仅关闭web、四原配置权威、手动查询通过；Host bridge错误分类/session缺口保持未满足 |
| REQ-08、DYN-04/05 | index.html、styles.css、app.js表单/结果/失效 | 标签/状态/迟到丢弃合成检查通过；焦点Important未修，亮暗/320px/实际键盘未验 |
| REQ-DYN-06 | actual Core+FF14 transport、synthetic ASGI、候选ZIP | 分层证据完整；不以旧aa27冷启动或合成ASGI冒充新B2包真实Host/浏览器通过 |

root合并三方意见，按用户非Blocker延期决定登记而不改候选：
- B2-UI-01 / Important / REQ-08、DYN-04：pending“停止展示”聚焦时完成/失败/超时删控件，焦点落body；UX/UI独立合成复现。按明确键盘需求采UI的Important，不采UX较轻评级。
- B2-LINK-01 / Important / REQ-07/10、DYN-03：编码资源API路径绕过页面Link前缀过滤；用户点击后Host鉴权仍保留，未证明权限绕过或XSS。后续按Host路径解码语义规范化。
- B2-UX-02 / Polish / REQ-01/07：Logs待选/not_found提示“名称或ID”与区服/角色字段不符。
- B2-TZ-01 / Polish / REQ-03/08：Intl接受+01:00但后端ZoneInfo拒绝，服务端安全拒绝，预检提示待统一。

独立证据 b2-r0-independent-probe-evidence.json SHA f65434de655d545e4124d17258a04681de11980c2baca75ef77677d13329b465。B2-HOST-01与旧D0项仍未修；UI-B/C修复计数保持3/2，无新轮，W2 Astra最终仍0/1。

新候选包：dist/ff14-w2/dynamic-b2-r0/astrbot_plugin_yomihime_game_link-v0.1.2.zip，SHA256 404cedcab4720b1ca2590a1597c21717c895232bfe0a0534943b30529c8685fd。标准脚本复用pin正确的SDK1.4 wheel；root核验148成员（128运行+20SDK）逐字节、CRC、校验清单、README和新origin字段，私密工作/测试排除，所有冻结文件未漂移。证据 b2-r0-package-evidence.json；这是本地联调候选，不是Release。没有上传用户实例、重启、stage/commit/push/tag。

接续点（历史）：在新404c候选上准备真实Host查询及用户实例冷迁移；不能复用仅绑定aa27的旧cold_host_probe宣称新包通过。B1结构化候选/角色分类/日历完整性、动态类型化配置写入和UI-C管理身份仍为后续合同，不从公共只读入口扩权。本文及临时requirements-ux/ui-spec/review已经同步，不重开已收束B0b/B0c审查。

**2026-10-03 本机安装测试授权更新：HTTP API ONLY / EXECUTING。** 用户明确要求安装到 `Local_Test_Env.txt` 指定测试实例，并纠正使用 AstrBot HTTP API，不使用 Computer Use。本片采用用户 Launcher 现有入口 `http://localhost:19266`，普通 Dashboard JWT 仅保存在请求进程内存；安装、重启、配置及查询均走实际 Host API。本片不验收浏览器视觉/交互，不提交或推送。已核实实例 4.28.2 正常账号登录与插件列表；所有结果以新404c候选、实际冷进程与业务响应为准。

旧模块在停用状态启动时仍导入 main 顶层 SDK，因此“停用→重启→上传”不能避免旧SDK1.3驻留。可执行升级序列为：备份目标代码和原四项普通配置、SQLite只读一致备份 → 正式卸载接口显式 `delete_config=false/delete_data=false` → 正式 Host restart → 核实新PID/出生时间及目标实例目录 → 标准上传404c → 保留原配置，仅新增 `web_public_origin=http://localhost:19266` → 核验catalog、状态及三项公开查询。依赖已满足，标准安装器不需要改共享venv；不修改Host源码、不写秘密。独立reviewer审核脚本后执行。当前实例尚未配置FFLogs凭据；未配置错误必须单列，不能视为角色Logs成功。

## 2026-10-03 本机 HTTP API 安装与查询实测

用户要求使用 AstrBot HTTP API，停止 Computer Use。目标为指定测试实例19266、AstrBot4.28.2；安装候选404c（完整SHA见上文），SDK1.4。独立审查895f安装脚本及e2bb续接脚本后，通过正式API执行保留config/data卸载→Host冷重启→标准ZIP上传→保存普通配置。旧PID52856退出，新PID30920/出生时间1791012309.957553及实例目录核验通过。新安装148文件逐字节匹配候选ZIP；原四项配置保留，仅新增web_public_origin=http://localhost:19266，与用户Launcher入口一致。

首段脚本在上传成功后进行文件stat时遇到沙箱PermissionError；原失败记录保留，不能改写为首次全程成功。经精确目标只读权限提升核验148文件后，从配置/查询续接，没有重复卸载、上传、重启，没有改ACL、Host源码或共享venv。旧代码ZIP、原配置字节及SQLite一致备份已独立核验hash/CRC/quick_check。

| 项目 | 实际结果 | 判定边界 |
| --- | --- | --- |
| 安装与入口 | 插件activated=true；web-status entry_ready=true；overview/settings/catalog均返回对象 | 安装和HTTP入口联调通过，独立后验另记 |
| 物品44091 | HTTP200；DTO partial_success；error=null | 部分成功。未保存公开warnings/document细节，不能推断是哪个来源或关联缺失 |
| 潮风亭 / 如月怜（cn） | HTTP200；DTO error / auth_required | 当前实例FFLogs有效凭据数量为0，角色数据查询未完成；不在普通配置中写秘密 |
| 国服日历，7天，Asia/Shanghai | HTTP200；DTO success；error=null | 业务返回成功；没有独立核对活动条目数量、窗口完整性或来源新鲜度 |
| 固定入口跳转 | 三个v1 POST均307，Location精确指向各自legacy路径，随后手动POST | 实际Host HTTP重定向合同通过，不代表浏览器bridge行为通过 |
| 异常请求 | 缺Origin400、错误Origin403、混入API key400、重复Bearer400、无效synthetic Cookie401、额外path400、超大body400 | 仅证明这7个实际请求被拒；不扩大为有效Cookie身份或全部权限规则已验证 |
| 保留状态 | 原四项普通配置值不变；另两插件续接前后状态不变；订阅/active凭据数量仍0/0 | 数量不变不等于数据库所有内容或字节不变 |

证据位于现有本地忽略目录：b2-http-preflight-evidence.json、b2-http-live-results.json（保留首次中断）、b2-http-installed-hash-evidence.json、b2-http-continuation-results.json。continuation的passed=true仅指installation_and_api_contract_only，不指三项业务全成功。原备份与任何秘密不提交。

没有产品源码修复、stage/commit/push/tag。本片未执行浏览器视觉/键盘/bridge验收；此前Important/Polish继续延期，W2 Astra仍0/1。后续优先处理真实实例凭据的正式维护通路与角色查询验收；物品部分结果细节留后续有针对性的证据采集，不猜测来源故障。
独立后验完成：固定Host GET确认FF14 loaded、catalog精确三项声明能力、web-status入口ready且origin正确、settings schema2 ready/applied；原四值与备份程序内相等。独立指定目标文件读取再次148/148匹配404c，SDK1.4；新PID身份不变且旧PID已退出。证据 b2-http-independent-metadata.json（SHA253c30bac6e81a7239f70396caf32eb633d26faeab405309f945407fe6b2f535）、b2-http-independent-files.json（SHA41f2f33d304869f4e1dadaa5f8330ef8b05b7bc01800badfb2c6c29ee43a573d）。未重复真实业务请求。root裁定本片安装/API联调完成；上述业务未完成与视觉未验边界保持，W2整体继续EXECUTING。

### 2026-10-03 阶段提交检查

本次提交包含此前W2实现、公开文档与8个文件的纯格式整理；SDK固定wheel及宿主成员哈希同步更新（另4个文件单常量），不修改已延期功能。格式前后8份AST一致。新wheel SHA256为1db47997219e80919020a233a58189e105500705718cd196e854f5183266735d，新标准ZIP SHA256为b41275a2aab51c79b7db2c23a7861f61321e0ce0a37941ca2235fd053619417d，148成员；均未部署，用户实例仍为404c。

提交检查：Ruff0.12全仓check/format通过，git diff --check通过；页面合成测试44/44，SDK/Bootstrap/公开导出相关20/20，标准构建通过。首次SDK检查因宿主旧成员哈希有6项失败，补齐精确常量后20项全部重跑通过，原失败证据保留。另发起Python3.13.9全量unittest，执行会话中断，日志止于storage.test_admin_credentials且没有最终汇总；本次全量回归明确标为未完成，不计PASS，不从局部结果推断全绿。日志留在本地忽略目录precommit-unittest.log，后续仍需完整回归。

待提交文件已检查不含本地已知凭据值；临时controller、数据库、一致备份、日志和dist工件不入库。保留W2执行中、Astra0/1及既有未验/延期事项；这次仅本地带Signed-off-by的阶段提交，不推送、打tag或发布。

## 2026-10-05 H-ADMIN-02 用户确认的授权架构修订

此最新决定替代历史章节中“所有宿主模式必须额外独立 Core 能力凭据”的要求，不追溯改变旧验收证据。Core 通用主体/授权来源/操作/资源/寿命合同保持独立；AstrBot Adapter 经正式宿主管理鉴权形成 owned 内部证明，Core 显式信任且限制四字段，用户无需重复输入 Core 凭据。原生凭据仍可用于独立认证，但不能伪造/自动生成 ACTIVE 来兼容 Host。

本批实现边界为四字段安全管理读取、修改/非法修复、revision/CAS、授权限定回退、启动失败时健康管理基础及显式恢复。公开查询/asset/API key/JSON role 不授权，管理范围不扩至启停、秘密、订阅或通用后台；请求寿命/到期/失效和事务效果 fence 保留。最小冻结合同见 [host-management-authorization-contract.md](host-management-authorization-contract.md)。Host fixed 4.28.2 内部验签适配及实际撤销边界必须有源码/隔离合同证据，不能把删除Cookie解释为所有JWT失效。

这次仅设计修订、源码和隔离测试/独审，不在现有实例启用委托、安装或迁移真实数据，不登录/读取真实凭据、提交/推送。历史热upload与冷加载/恢复窗口未因此关闭，真实启用另确认；历史WebChat8/8、物品真页面和日历/QQ/zeroLLM/runtimeDTO未验边界保留。

## 2026-10-06 模块化连续批次收敛

用户以 `2cfa88f655cd158f6e5249baeda3deb7d6ce5e19` 为基线，扩展计划书首批为 R0、R1、R2 物品/市场纵向切片和有前置条件的 R3；R5 仅方案与隔离合同。本轮源码、构建及测试均使用隔离开发环境，没有操作真实实例或提交推送。

最小合同和归属见 [module-refactor-contract.md](module-refactor-contract.md)。SDK 1.5 增量提供声明式命令参数模式、页面和资源描述符；旧 1.4 合同仍兼容。Host 的 FF14 市场原文及帮助分支已退出，固定数据源/迁移装配和公开业务路由仍未完全通用化。模块资源的清单、摘要、安装复制、正式静态构建及宿主资源重写合同已贯通，不能由此推断真实新包 bridge 已验。

新公开壳使用 Vue/Naive UI，共享本地运行时；物品和市场表单归 FF14，旧 Logs/日历等页面保留兼容。测试第二模块仅在测试中注册。隔离 Chromium 已覆盖冷执行、重复展示通知、延迟刷新保结构、候选、状态、卸载迟到结果和布局边界。R2 两轮集中修复用完后仍有两个 Important：跳至正文仅聚焦不滚动、深色结果的限定文字对比不足；不能宣称 R2 或整包已通过。

通用普通配置目录与既有独立 management 表单不加载 R2 壳，已按依赖拆分完成其独立源码及隔离验证；整体 R3 前置状态仍未通过。四字段写范围、合法宿主证明、CAS、语义校验、修复和受限回退不扩权。存储只建立稳定模块路径及所有权，不物理拆库或迁移真实数据。

后续有边界的任务卡见 [module-refactor-next-batch.md](module-refactor-next-batch.md)。本轮产物继续称待验收候选；新版本真实宿主、来源、QQ 和普通聊天均未验，历史真验仅作为历史保留。

最终完整串行回归仍未通过：执行1273项，1249通过、7失败、3用例错误、14既有跳过；另一个类初始化错误使4项已安装Core测试未执行（runner汇总errors为4）。新增缺口包括SDK固定测试构建夹具、旧版本/服务/目录断言与三项隐式帮助提示退化；两项Windows ACL错误、八项基线格式失败单列。R1/R2本批各两轮预算已用完，停止追加源码修复，下一批先按R1-INTEGRATION卡收敛，不把局部独审或包校验替代整体验收。
