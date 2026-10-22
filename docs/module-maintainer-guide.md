# 模块维护速查

本项目的发布边界是插件包与固定 SDK；模块声明不是授权证明。本说明描述现有机制，不承诺任意模块、任意代码热替换或完整平台验收。

| 层 | 负责 | 不应承担 |
| --- | --- | --- |
| SDK | 版本化能力、命令、工具、页面、配置、来源及服务合同 | AstrBot 身份、游戏业务、秘密明文回读 |
| Core | Registry、准入、生命周期、入口策略、输出校验、缓存、配置 CAS、加密凭据和持久化所有权 | FF14 物品/服务器规则、宿主 JWT 或页面类型 |
| Host Adapter | 正式宿主入口、可信身份与会话、工具对象持有/撤销、受信装配、资源投影及管理身份适配 | 模型自报身份、无约束业务分派、游戏查价算法 |
| FF14 | 物品解析、行情计划/聚合、候选、FFLogs、日历语义、模块配置与页面 | 绕过公共输出预算、独立身份/配置存储或凭据服务器 |

模块在 `yomihime.manifest.json` 声明入口；工厂通过 `ModuleServices` 装配 handler。命令的 `operation_path`、参数模式、capability 和 schema 共同决定分派，`raw_tail` 的解释留给模块。工具参数直接进入能力，不拼接命令字符串；工具 owner 来自可信宿主事件。页面通过受限服务上下文调用能力，遵守 mount/update/dispose 和代际失效。帮助读取注册声明及健康投影，元数据读取不执行查询。

磁盘声明包含 `package_id`、`contract_version`、局部 `module_id` 和 `factory_entry`，全局 owner 是 `package_id/module_id`。现有 FF14 的 `factory_entry="module:Factory"`；`ModuleFactory.create(ModuleServices)` 返回实现 `handlers/start/stop/check_health` 的实例，handlers 的 capabilities、collectors 和 evaluators 必须与声明精确一致。生产链经可信授权、不可变源码捕获、工厂解析、受限服务创建、生命周期接管后注册；测试直接调用 Registry 注册 fixture 不等于第二个生产包安装成功。

| 维护对象 | 实际入口与检查位置 |
| --- | --- |
| 能力、命令、工具、字段、来源和页面声明 | [FF14 manifest](../modules/ff14/yomihime.manifest.json)；声明必须通过磁盘解析与固定 SDK 合同校验 |
| 工厂与业务处理 | [FF14 工厂](../modules/ff14/module.py)；handler 使用受限服务，不直接取得宿主账号或秘密 |
| 宿主工具对象 | [工具发布适配器](../adapters/astrbot/tool_publisher.py)；冲突拒绝，撤销 exact object，旧回调重新检查 owner/epoch |
| 显式受审模块装配 | [模块装配数据](../modules/ff14/assembly.json)、[模块纯支持](../modules/ff14/assembly.py)、[宿主适配](../adapters/astrbot/runtime.py)；只处理 Host 明确选择的捕获输入，JSON 不指定 import 或授予权限 |
| 通用配置目录与管理 | [配置目录](../services/configuration/catalog.py)、[管理操作](../services/admin_operations.py)；目录与授权分别成立 |
| FF14 凭据表单与消费 | [模块装配中的表单声明](../modules/ff14/assembly.json)、[受限管理政策](../services/managed_source_credentials.py)、[来源消费](../services/source_credentials.py) |
| 公共壳与管理视图 | [前端构建](../pages/frontend/build.mjs)；管理页测试需单独运行，`npm test` 当前只含 shell 测试 |
| 模块业务页面与资源 | [模块页面源码](../modules/ff14/pages/src)、[资源摘要](../modules/ff14/pages/resources.json)；构建后校验 manifest 和发布投影 |
| 稳定持久目录 | [存储路由](../services/module_storage.py)；Core 选择 module_id，用户输入不能选择路径 |
| 正式包与 SDK | [发布构建](../scripts/build_release.py)、[插件包清单](../scripts/build_dashboard_zip.py)；核对实际 wheel、pin、资源和许可证，不只测试源码副本 |

配置按 `module_id` 命名空间保存。FF14 default region and calendar defaults belong to the Module.普通字段目录只提供声明，实际读取、写入和服务端语义校验另行授权。敏感配置不能声明普通 `value_schema`，不能出现在普通值或公开页；需要受信任、受限的管理策略和加密通道。管理请求还须通过来源、操作、资源、生命周期和 revision 校验。

图片主题属于 Core 管理的通用呈现偏好：全局 `game_link/core.image_theme` 默认 `warm_paper`；各模块的保留字段 `reply_theme` 默认跟随 Core。两处设置位于「回复样式 → 图片主题」，预设 ID 和名称均来自 Core 渲染器的 `list_themes()`。`reply_theme` 由 Core 注入配置服务与管理目录，不写入模块 manifest，也不是 FF14 业务字段；模块和 Host 配置均不能重新声明这些保留名称。可信装配另行授予具体字段的管理权限，声明或目录本身不授信。

主题沿用已有 `config_entries/config_state`、授权、revision/CAS 和草稿保护，不新增表或迁移。显式预设保存字符串；「跟随核心」使用 `CLEAR` 删除模块覆盖，读取投影为 `None`。缺省值与非法数据分别处理，已存 `null`、未知 ID 均拒绝，不能静默变成默认值。Core 的 `ThemeSettingsView.current(module_id)` 在共享 mutation gate 内一次读取两级配置及各自 revision；Core 帮助只调用 `current()`。设置仅影响后续生成的图片。

来源声明约束主机与超时，受控 HTTP 校验路径格式及请求/响应预算；凭据消费另受可信策略的精确资源/token 路径约束。FFLogs 客户端凭据按国服/国际服分开，OAuth token 由 Core 消费通道持有，模块只发公开 API 请求。市场单价、品质、服务器和时间由验证后的结构化响应共同投影；接口刚获取不代表实时可买，区域请求成功不代表全部 World 完整覆盖。最多 5 条挂牌是样本。

页面源码与发布投影必须贯通构建、资源清单摘要和固定 Host 的重写规则。正式构建仅在 `pages/00-game-link` 发布共享 Vue/Naive 运行时；全局管理嵌入该根，模块入口与资源按受信 manifest 投影，不发布独立 `shell` 或 `management` 页面根。模块资产按 owner/版本登记：同模块切页保留已加载资源；真正卸载、版本或安全失效清理自有样式/视图/浮层并封锁旧异步结果，再通过正式入口重新投影。`cleanup_pending` 不是重新启用许可。

`runtime_id + module_epoch` 标识运行 owner，页面本地请求序号隔离迟到结果，`catalog_revision` 是 Registry 投影版本。声明的 `module_version` 与根据 resource(path,sha256) 计算的 `asset_version` 不同；配置 revision 用于持久化 CAS。这些字段不能互相替代，也不是客户端自授权或安全热替换完成的证明。资源投影的短期访问证明只允许读取已授权资源，不授予业务调用或管理权限。

模块持久目录由稳定 `module_id` 的 SHA256 路由到 `modules/<摘要>/{config,cache,data,secrets}`，这是一组可用的所有权目录，不证明现有配置与缓存已经全部搬入。当前 `runtime.sqlite3` 与 SecretStore 仍共享；命名空间隔离不等于物理拆库。模块停用/卸载默认保留配置、缓存、秘密和业务数据；插件级宿主卸载还应明确选择保留配置/数据。代码 slot 不作为用户数据目录。没有迁移和恢复合同，不搬库或根据猜测清缓存。

第二模块的现有证据是隔离 fixture：通用导航、页面挂载/失效、配置声明拒绝未授权读写，以及非 FF14 工具的正式 Core/Host 调用。它不证明第二个生产包、任意来源或新管理权限已验收。模块声明、受信装配与实际部署能力应分别检查。

当前 Host 仍明确选择内置 FF14 发布包。装配描述及源码 fingerprint 证明选定输入的一致性，不是独立发布签名，也不让扫描到的模块自动获得来源、网页或管理权限。旧业务 renderer、专属业务状态装配及旧定位页均已删除。物品、市场、Logs 和日历由正式 Vue 模块页执行。共享存储与旧安装槽的处理遵守[存储与迁移合同](credential-storage.md)。

旧 Host `config_adapter.py` 与 `ff14_pages.py` 兼容别名已删除；普通配置使用 Core 当前设置服务，页面使用 `PublicPages`。已退役的 FF14 定位页及其兼容投影也已删除；所有 FF14 业务界面由当前模块页承载。

## 统一设置与生命周期能力矩阵

| 需求 | 当前正式能力 | 验收边界 |
| --- | --- | --- |
| REQ01 | 壳底部固定全局设置，空目录或 FF14 未加载仍可进入 Core 设置 | 全局设置不依赖 FF14 模块生命周期；管理授权仍按 Host/Core 合同逐请求检查 |
| REQ02 | 公共表单按 owner/group 显示；全局只选 Core，各模块导航有自己的设置入口；普通值与凭据接口分离，保存采用 revision CAS | schema/catalog 只描述 UI；资源权来自显式部署政策和逐请求鉴权，不能从声明自动获得 |
| REQ03 | Core 持久记录主密钥来源、代际与库存；全域空库存时可自动建立私有托管密钥，旧密文须验证原密钥可解后恢复；未就绪或未知时禁止填写/替换；已受权 clear 仍须确认并通过最新 catalog/CAS，keep 零写；FFLogs cn/global 独立操作 | 高级环境来源 `YGL_SECRET_KEY` 显式提供时不回退；全域重置须明确确认并使所有模块的旧加密配置/授权凭据失效，普通配置与授权记录保留。诊断不回读密钥，主密钥就绪不证明上游 OAuth 有效 |
| REQ04 | 公共调用使用闭合 owner/page/capability_id/parameters 信封；Host 不分派行情业务，FF14 解释输入并验证行情事实 | 保留受信 allowlist、epoch、准入、只读/隐私检查与 32 blocks/512 nodes 总预算 |
| REQ05 | Logs/日历使用模块 Vue 页及现有客户端、错误和恢复语义；旧业务 renderer 已移除 | FF14 页面只呈现模块结果和安全状态；上游结果按地区与实际范围判定 |
| REQ06 | exact owner disable/unload/restore；停用保留设置；卸载先 fence、排空与 cleanup，再撤销工具对象、许可、来源及 Registry 注册 | pending 保留可重试所有权；配置、秘密、缓存和 data 均保留。恢复只使用受信原候选；旧壳不自动续资产证明，应从宿主正式重开 |
| REQ07 | 隔离中性模块有普通配置、公共只读能力和页面；正式 Host→Core 链验证同名页面隔离、声明不授信与持久数据恢复 | fixture 不入发布包；不等于任意第三方生产包安装验收 |

Core 与模块的命名空间配置是当前读写权威；Core 目录不依赖 FF14 的生命周期。模块字段从当前受信 manifest 与通过校验的 SQLite 值读取。FF14 默认区域仍由模块拥有；仅固定旧 Core 区域来源可按 `LegacyConfigCopyPolicy` 在目标缺失时投影/显式复制。其它未登记旧字段不被推断为可迁移。非法配置可通过受权管理入口修复；加载不会清空现有数据库或订阅。

`disable` 关闭业务准入，继续允许模块设置与启用管理。显式 `unload` 退出 active 普通/凭据目录、页面、来源、工具和 owner 注册，但模块管理仍显示可信恢复候选；保留最大部署政策不允许旧请求在卸载后提交。精确 owner、epoch 和生命周期检查不授予配置字段读写权。

当前 SDK 为 `0.1.0a13`，模块 ABI 为 `2.0`，输出/展示/存储合同为 `1.8.0`，磁盘/工厂协议为 `1`；这些版本各自描述不同合同。SDK a10 增加 invocation-scoped diagnostics 服务：现有模块消费者可继续使用原服务；实现 `InvocationServices` 的 Host provider 与测试替身须提供 diagnostics。依赖此属性的新模块不保证运行于不含它的旧 Host。构建时核对固定 wheel 字节与来源；版本和 ABI 声明不替代具体安装验证。

SDK 的后台调度声明仍受 PUBLIC ownership、固定参数和 schema 验证约束；细节见[SDK 说明](module-sdk.md#background-collectors)。历史候选和验收流水不属于当前模块维护合同。

固定宿主 iframe 不允许原生 `confirm` 或顶层导航。现有草稿与模块生命周期守卫使用有归属的页面内确认，保留操作、owner、revision 与生命周期检查；取消等待按钮解除禁用后才安全恢复焦点，不抢新页面或用户已转移的焦点。需要新资源投影时，使用宿主左侧“插件”菜单和目标 monitor 正式重开；页面内文字指引不承诺自动跳转、无刷新热加载或资源授权续期。

目录刷新按钮忙碌时保持原可聚焦节点，用 `aria-disabled`、`aria-busy` 和读取提示表达状态；实时忙碌检查与既有生命周期准入继续拒绝重复或失效请求。刷新结束不主动抢回焦点，切页与用户主动移焦点遵循各自原有行为。

对应检查入口：声明与工厂见 [磁盘解析测试](../tests/extensions/test_disk_manifest.py)、[工厂解析测试](../tests/extensions/test_factory_resolver.py) 和 [扩展生命周期测试](../tests/services/test_extension_runtime.py)；第二模块 fixture 见 [壳生命周期测试](../tests/pages/shell/lifecycle.test.mjs)、[配置目录测试](../tests/services/test_configuration_catalog.py) 和 [宿主工具测试](../tests/host/test_llm_tools.py)。凭据正反例见 [受管凭据测试](../tests/host/test_managed_credentials.py) 与 [来源凭据测试](../tests/services/test_source_credentials.py)。这些是定位与运行入口，实际通过范围仍以对应冻结版本的报告为准。

模块注册、帮助、配置、订阅及展示继续遵守本项目的公开 SDK、前端合同和当前实现。模块数量、扫描结果或 fixture 不能代替来源、授权、宿主集成与数据迁移各自的合同；不要把游戏业务移入 Core。

维护时先运行变更涉及的模块、Core 输出及 Host 合同测试，页面修改显式运行管理/模块页面测试和正式构建预览。冻结后独立审查，主要跨层修改集成后安排一次全量。离线回放、隔离浏览器、真实宿主和模型回答分别记录；已有 ACL、跳过、格式及 EOF 提示单列。私有工作记录、凭据、数据库、备份和候选 ZIP 不入 Git 或发布包。

模块展示保留可选 `ModuleDisplay` 与磁盘 `display`，由模块提供名称和翻译。FF14 owner 与 `/ygl ff14` 路由不变。当前根配置声明仅保留网页访问地址；FF14 普通设置由模块目录和已校验的 SQLite 值提供。旧配置准备文件、迁移完成 marker 与回滚选择器已退出当前安装合同。


### 声明式命令图片与模块徽标

模块可在 `DisplayDocument.card` 附带纯 SDK 卡片声明；文本块仍为发送前回退，模型事实保持独立。当前 SDK 版本、ABI 和 invocation diagnostics 的 Host provider 要求见[模块 SDK 合同](module-sdk.md)。
`card.logo=None` 时，命令展示由 Core 使用当前已注册模块的 `display.logo_resource`，从清单声明且 SHA-256 固定的资源中有界读取；模块显式提供 `cards.Asset` 时优先使用该资源。
这个绑定仅用于命令图片，不是新公共资源下载接口。模块不能用 `__file__` 拼接并读取自身静态资源目录：隔离的源码捕获不包含静态资源。应声明 `ModuleDisplay.logo_resource` 与对应 `ModuleManifest.resources`，交给 Core 绑定。

模块内容采用普通字符串、`cards.Inline` / `cards.Span`，或者显式 `cards.TaggedText`。外部名称优先作为普通字符串或 Span，避免无意将外部内容解析成标签。只有 TaggedText 请求 Core 解析约定的有界标签语法；普通字符串保持字面量。


### Core 受管字体资源

命令图片字体由 Core 管理，不是模块设置、SDK 字体选择器或模块来源权限。模块继续提交同一 `cards.Document`；不得指定系统路径、下载 URL、摘要或任意上传字体。标准资源固定为原版 Noto Sans CJK SC Regular 2.004，准确文件与预算见[展示契约 §5.1](command-presentation/declarative-rendering-spec.md#51-core-受管字体与轻量发布)。

正式 Host 在启动后台核验 `data_dir/resources/fonts` 下固定文件，缺失/不匹配时从固定上游下载，验证同一 bytes 后原子发布。当前渲染使用不可变 provider 快照，不逐命令读盘/联网。标准字体未就绪时只尝试有界系统候选，并按实际 cmap 检查覆盖；系统字体不复制进持久目录或重新分发，排版可与标准字体不同；没有可用字形就沿用完整文本回退。模块不能因此省略事实或假定图片一定生成成功。

Core 全局「回复样式 → 图片字体」的状态、重试、上传复用正式管理身份/来源/资源/生命周期检查；只接受准确大小和 SHA 的同一固定字体，客户端文件名不决定目标路径。它不是主题配置草稿，不使用配置 CAS；状态 revision 也不授权写入。切页只忽略迟到回执，不保证取消已提交上传。

仓库与轻量插件只分发字体 OFL 与 NOTICE；固定哈希的原版 OTF 是由 `scripts/prepare_test_font.py` 准备的开发夹具，不随源码或插件包分发。不能因包内有 NOTICE 就声称运行时已有字体。后台网络不等待启动完成，但文件/哈希/Pillow 检查有同步开销；系统回退字体渲染不声明为与标准字体完全同图。实际安装和渲染状态须按对应候选单独验收。


## Backend boundaries and module pages

Modules import only the SDK, the standard library and their own package. Core, Host, infrastructure, internal repositories, dynamic loaders and service private fields are forbidden. The enforced rule is tested in [dependency checks](../tests/contracts/test_module_dependency_boundary.py); direct fixture Registry registration is not formal discovery/capture/trusted selection/load evidence.

Business diagnostics use scope.diagnostics.emit(event_id, payload) with module-owned closed schemas and separately selected trusted policies. See the [SDK contract](module-sdk.md#invocation-bound-diagnostics-a10) for bounds and authority/cancellation behavior. Calendar sources/warnings and FFLogs fields belong to FF14, not Host log handlers or Core. Only CacheUnavailable permits uncached fallback; do not swallow generic ServiceUnavailable or rebind to rotate proof.

Frontend modules consume only [the public contract](../pages/public-contract.ts). Page roles home/config/aux are declared in manifests, with at most one home and one config per module. Owner and entry assets are captured by trusted assembly. Config and credential services are limited to current owner/page/runtime/epoch and trusted declared fields, with independent READ/UPDATE authority and CAS. A single 00-game-link page token does not grant management authority. Credential snapshots contain only configured/decryptable/upstream=unverified.

Reviewed LegacyConfigCopyPolicy accepts only a policy ID and revisions from clients. Source owner/field/value/schema are server-selected. Existing target values are preserved, missing source is a no-op, invalid/secret source is refused. Temporary effective projection uses only the destination field name until explicit target configuration exists; no core_defaults object or arbitrary Core read port is exposed. Explicit copy keeps source rows.


### Diagnostic and configuration maintenance

Calendar diagnostics omit absent HTTP status metadata; an ordinary source transport failure must still commit its failure state and try the existing fallback. Awaited diagnostic size ceilings (including strings) only drop optional output. Schema/type/unsafe-content errors remain strict, and cancellation, authorization and stale-epoch failures always propagate. Build diagnostics from module-owned closed field projections and trusted policy; do not copy arbitrary plaintext or treat keyword scans as proof that a string contains no secret. Bounded booleans and finite status enums are preferable to free-form text. The generic Host sink checks and forwards only the generic envelope; it does not sanitize or interpret FF14 meaning.

Use effective module-page configuration values and their controlled source/migration_pending metadata; do not substitute declaration defaults for a pending legacy destination. Source rows remain retained. Generic retained source fields do not appear in ordinary snapshots and cannot be patched/cleared through the active field list.

Module config pages preserve reply_theme with null meaning follow Core. Read choices from the authorized descriptors supplied by Core; never hardcode the Core palette or import its implementation. Config descriptors contain only the selected page's authorized ordinary fields and a bounded approved schema subset. Opaque page sessions bind real Host management authority across awaits and commit, without claiming DOM attestation.
