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

配置按 `module_id` 命名空间保存。Core 的 cn/global 是游戏服务区域；FF14 日历默认值由模块定义。普通字段目录只提供声明，实际读取、写入和服务端语义校验另行授权。敏感配置不能声明普通 `value_schema`，不能出现在普通值或公开页；需要受信任、受限的管理策略和加密通道。管理请求还须通过来源、操作、资源、生命周期和 revision 校验。

图片主题属于 Core 管理的通用呈现偏好：全局 `game_link/core.image_theme` 默认 `warm_paper`；各模块的保留字段 `reply_theme` 默认跟随 Core。两处设置位于「回复样式 → 图片主题」，预设 ID 和名称均来自 Core 渲染器的 `list_themes()`。`reply_theme` 由 Core 注入配置服务与管理目录，不写入模块 manifest，也不是 FF14 业务字段；模块和 Host 配置均不能重新声明这些保留名称。可信装配另行授予具体字段的管理权限，声明或目录本身不授信。

主题沿用已有 `config_entries/config_state`、授权、revision/CAS 和草稿保护，不新增表或迁移。显式预设保存字符串；「跟随核心」使用 `CLEAR` 删除模块覆盖，读取投影为 `None`。缺省值与非法数据分别处理，已存 `null`、未知 ID 均拒绝，不能静默变成默认值。Core 的 `ThemeSettingsView.current(module_id)` 在共享 mutation gate 内一次读取两级配置及各自 revision；Core 帮助只调用 `current()`。设置仅影响后续生成的图片。

来源声明约束主机与超时，受控 HTTP 校验路径格式及请求/响应预算；凭据消费另受可信策略的精确资源/token 路径约束。FFLogs 客户端凭据按国服/国际服分开，OAuth token 由 Core 消费通道持有，模块只发公开 API 请求。市场单价、品质、服务器和时间由验证后的结构化响应共同投影；接口刚获取不代表实时可买，区域请求成功不代表全部 World 完整覆盖。最多 5 条挂牌是样本。

页面源码与发布投影必须贯通构建、资源清单摘要和固定 Host 的重写规则。共享 Vue/Naive 运行时在各合法页面根发布一致字节，不能跨出 management 根导入 shell 运行时。模块资产按 owner/版本登记：同模块切页保留已加载资源；真正卸载、版本或安全失效清理自有样式/视图/浮层并封锁旧异步结果，再通过正式入口重新投影。`cleanup_pending` 不是重新启用许可。

`runtime_id + module_epoch` 标识运行 owner，页面本地请求序号隔离迟到结果，`catalog_revision` 是 Registry 投影版本。声明的 `module_version` 与根据 resource(path,sha256) 计算的 `asset_version` 不同；配置 revision 用于持久化 CAS。这些字段不能互相替代，也不是客户端自授权或安全热替换完成的证明。资源投影的短期访问证明只允许读取已授权资源，不授予业务调用或管理权限。

模块持久目录由稳定 `module_id` 的 SHA256 路由到 `modules/<摘要>/{config,cache,data,secrets}`，这是一组可用的所有权目录，不证明现有配置与缓存已经全部搬入。当前 `runtime.sqlite3` 与 SecretStore 仍共享；命名空间隔离不等于物理拆库。模块停用/卸载默认保留配置、缓存、秘密和业务数据；插件级宿主卸载还应明确选择保留配置/数据。代码 slot 不作为用户数据目录。没有迁移和恢复合同，不搬库或根据猜测清缓存。

第二模块的现有证据是隔离 fixture：通用导航、页面挂载/失效、配置声明拒绝未授权读写，以及非 FF14 工具的正式 Core/Host 调用。它不证明第二个生产包、任意来源或新管理权限已验收。模块声明、受信装配与实际部署能力应分别检查。

当前 Host 仍明确选择内置 FF14 发布包。装配描述及源码 fingerprint 证明选定输入的一致性，不是独立发布签名，也不让扫描到的模块自动获得来源、网页或管理权限。旧业务 renderer 和专属业务状态装配已退出；保留的旧物理路径只返回静态定位说明。物品、市场、Logs 和日历由正式 Vue 模块页执行。共享存储与旧安装槽的清理由独立迁移/恢复方案处理。

旧 Host `config_adapter.py` 与 `ff14_pages.py` 兼容别名已删除；普通配置使用 Core 当前设置服务，页面使用 `PublicPages`。已退役的 FF14 定位页及其兼容投影也已删除；所有 FF14 业务界面由当前模块页承载。

## 统一设置与生命周期能力矩阵

| 需求 | 当前正式能力 | 验收边界 |
| --- | --- | --- |
| REQ01 | 壳底部固定全局设置，空目录或 FF14 未加载仍可进入 Core 设置 | 真实 monitor 入口、Core 往返/CAS、解除挂载 FF14 后全局设置可用已验证 |
| REQ02 | 公共表单按 owner/group 显示；全局只选 Core，各模块导航有自己的设置入口；普通值与凭据接口分离，保存采用 revision CAS | schema/catalog 只描述 UI；资源权来自显式部署政策和逐请求鉴权，不能从声明自动获得 |
| REQ03 | 当前注入 KeyProvider/codec 提供无秘密 readiness；未就绪或未知时禁止填写/替换；已受权 clear 仍须确认并通过最新 catalog/CAS，keep 零写；FFLogs cn/global 独立操作 | 仅接入现有 external/env 密钥提供者（默认 `YGL_SECRET_KEY`）；没有持久主钥生成或自动轮换实现，预检不读取旧 secret |
| REQ04 | 公共调用使用闭合 owner/page/capability_id/parameters 信封；Host 不分派行情业务，FF14 解释输入并验证行情事实 | 保留受信 allowlist、epoch、准入、只读/隐私检查与 32 blocks/512 nodes 总预算 |
| REQ05 | Logs/日历使用模块 Vue 页及现有客户端、错误和恢复语义；旧业务 renderer 已移除 | 旧兼容入口仅指引使用宿主插件菜单正式重开；真实未配置/来源失败处理已验证，真实 FFLogs 和日历内容仍待外部条件 |
| REQ06 | exact owner disable/unload/restore；停用保留设置；卸载先 fence、排空与 cleanup，再撤销工具对象、许可、来源及 Registry 注册 | pending 保留可重试所有权；配置、秘密、缓存和 data 均保留。恢复只使用受信原候选；旧壳不自动续资产证明，应从宿主正式重开 |
| REQ07 | 隔离中性模块有普通配置、公共只读能力和页面；正式 Host→Core 链验证同名页面隔离、声明不授信与持久数据恢复 | fixture 不入发布包；不等于任意第三方生产包安装验收 |

Core 与模块的命名空间配置是当前读写权威；Core 目录不依赖 FF14 的生命周期。首次安装从当前声明和通过校验的 SQLite 值初始化，不读取旧配置快照或执行旧字段导入。非法初始配置仍可通过受权管理入口修复；启动不清空现有数据库或订阅。

`disable` 关闭业务准入，继续允许模块设置与启用管理。显式 `unload` 退出 active 普通/凭据目录、页面、来源、工具和 owner 注册，但模块管理仍显示可信恢复候选；保留最大部署政策不允许旧请求在卸载后提交。精确 owner、epoch 和生命周期检查不授予配置字段读写权。

当前薄 SDK 发布构建使用 `yomihime_game_link_sdk-0.1.0a9-py3-none-any.whl`，实际摘要与 pin 由构建核对。SDK 发行编号、模块 ABI `2.0`、输出/展示/存储合同 `1.8.0` 及磁盘/工厂协议 `1` 独立判断；旧模块接口在开发期不兼容，不能将旧 1.x SDK 作为当前发行加载。已发布旧工件字节及受限旧展示记录读取边界保留，不迁移配置、凭据或数据库。升级固定 SDK 需要保留数据的宿主卸载与冷加载，以退出旧进程中已导入的 SDK；不能以同壳恢复模拟该步骤。此前 SDK 1.7 的独立实例安装、首次冷加载、最小公开查询、普通管理读写及保留数据解除挂载/恢复，以及先前隔离 AstrBot 4.29.1 ZIP 冒烟，均为历史实机证据；本轮 Windows、用户实例、真实凭据查询、IM 平台和模型调用分别验收。

SDK a8 引入的 PUBLIC `BACKGROUND` 调度与 1–32 组固定 `background_parameters` 继续包含在 a9；详情见[SDK 说明](module-sdk.md#background-collectors)。PR #8 固定候选 a9 wheel 为 42,636 bytes，SHA-256 `4d8d71c4ab04805c3a1edbd9e194b5228e66c9e0290caf01daae9374d65a74e5`；本轮 Windows 构建和宿主验收以对应原始记录为准。历史 a7/a8 工件及测试不作为当前候选的验收。

固定宿主 iframe 不允许原生 `confirm` 或顶层导航。现有草稿与模块生命周期守卫使用有归属的页面内确认，保留操作、owner、revision 与生命周期检查；取消等待按钮解除禁用后才安全恢复焦点，不抢新页面或用户已转移的焦点。需要新资源投影时，使用宿主左侧“插件”菜单和目标 monitor 正式重开；页面内文字指引不承诺自动跳转、无刷新热加载或资源授权续期。

目录刷新按钮忙碌时保持原可聚焦节点，用 `aria-disabled`、`aria-busy` 和读取提示表达状态；实时忙碌检查与既有生命周期准入继续拒绝重复或失效请求。刷新结束不主动抢回焦点，切页与用户主动移焦点遵循各自原有行为。

对应检查入口：声明与工厂见 [磁盘解析测试](../tests/extensions/test_disk_manifest.py)、[工厂解析测试](../tests/extensions/test_factory_resolver.py) 和 [扩展生命周期测试](../tests/services/test_extension_runtime.py)；第二模块 fixture 见 [壳生命周期测试](../tests/pages/shell/lifecycle.test.mjs)、[配置目录测试](../tests/services/test_configuration_catalog.py) 和 [宿主工具测试](../tests/host/test_llm_tools.py)。凭据正反例见 [受管凭据测试](../tests/host/test_managed_credentials.py) 与 [来源凭据测试](../tests/services/test_source_credentials.py)。这些是定位与运行入口，实际通过范围仍以对应冻结版本的报告为准。

参考方向沿用此前记录的早柚核心审阅版本 `87c06f11ae10c12b3bb8e76b3c6f420c831282a8`，只用于模块注册、帮助、配置、订阅及展示的设计比较；本批未重新读取最新版或引入参考源码。game-link 已有上述合同和单 FF14 生产切片；生产多模块分发、任意包热替换、物理拆库和完整 IM 身份/投递验收仍有差距。不能用功能数量或百分比代替这些证据，也不应因此把游戏业务移入 Core。

维护时先运行变更涉及的模块、Core 输出及 Host 合同测试，页面修改显式运行管理/模块页面测试和正式构建预览。冻结后独立审查，主要跨层修改集成后安排一次全量。离线回放、隔离浏览器、真实宿主和模型回答分别记录；已有 ACL、跳过、格式及 EOF 提示单列。私有工作记录、凭据、数据库、备份和候选 ZIP 不入 Git 或发布包。

模块展示保留可选 `ModuleDisplay` 与磁盘 `display`，由模块提供名称和翻译。FF14 owner 与 `/ygl ff14` 路由不变。当前根配置声明仅保留网页访问地址；FF14 普通设置由模块目录和已校验的 SQLite 值提供。旧配置准备文件、迁移完成 marker 与回滚选择器已退出当前安装合同。


### 声明式命令图片与模块徽标

SDK a7 可在 `DisplayDocument.card` 附带纯 `cards.Document`，文本块仍为回退，模型事实保持独立。
`card.logo=None` 时，命令展示由 Core 使用当前已注册模块的 `display.logo_resource`，从清单声明且 SHA-256 固定的资源中有界读取；模块显式提供 `cards.Asset` 时优先使用该资源。
这个绑定仅用于命令图片，不是新公共资源下载接口。模块不能用 `__file__` 拼接并读取自身静态资源目录：隔离的源码捕获不包含静态资源。应声明 `ModuleDisplay.logo_resource` 与对应 `ModuleManifest.resources`，交给 Core 绑定。

模块内容采用普通字符串、`cards.Inline` / `cards.Span`，或者显式 `cards.TaggedText`。外部名称优先作为普通字符串或 Span，避免无意将外部内容解析成标签。只有 TaggedText 请求 Core 解析约定的有界标签语法；普通字符串保持字面量。


### Core 受管字体资源

命令图片字体由 Core 管理，不是模块设置、SDK 字体选择器或模块来源权限。模块继续提交同一 `cards.Document`；不得指定系统路径、下载 URL、摘要或任意上传字体。标准资源固定为原版 Noto Sans CJK SC Regular 2.004，准确文件与预算见[展示契约 §5.1](command-presentation/declarative-rendering-spec.md#51-core-受管字体与轻量发布)。

正式 Host 在启动后台核验 `data_dir/resources/fonts` 下固定文件，缺失/不匹配时从固定上游下载，验证同一 bytes 后原子发布。当前渲染使用不可变 provider 快照，不逐命令读盘/联网。标准字体未就绪时只尝试有界系统候选，并按实际 cmap 检查覆盖；系统字体不复制进持久目录或重新分发，排版可与标准字体不同；没有可用字形就沿用完整文本回退。模块不能因此省略事实或假定图片一定生成成功。

Core 全局「回复样式 → 图片字体」的状态、重试、上传复用正式管理身份/来源/资源/生命周期检查；只接受准确大小和 SHA 的同一固定字体，客户端文件名不决定目标路径。它不是主题配置草稿，不使用配置 CAS；状态 revision 也不授权写入。切页只忽略迟到回执，不保证取消已提交上传。

仓库与轻量插件只分发字体 OFL 与 NOTICE；固定哈希的原版 OTF 是由 `scripts/prepare_test_font.py` 准备的开发夹具，不随源码或插件包分发。不能因包内有 NOTICE 就声称运行时已有字体。后台网络不等待启动完成，但文件/哈希/Pillow 检查有同步开销；系统回退字体渲染不声明为与标准字体完全同图。实际安装和渲染状态须按对应候选单独验收。
