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
| 显式受审模块装配 | [模块装配数据](../modules/ff14/assembly.json)、[模块纯支持](../modules/ff14/assembly.py)、[宿主适配](../adapters/astrbot/trusted_assembly.py)；只处理 Host 明确选择的捕获输入，JSON 不指定 import 或授予权限 |
| 通用配置目录与管理 | [配置目录](../services/configuration_catalog.py)、[管理操作](../services/admin_operations.py)；目录与授权分别成立 |
| FF14 凭据表单与消费 | [模块装配中的表单声明](../modules/ff14/assembly.json)、[受限管理政策](../services/managed_source_credentials.py)、[来源消费](../services/source_credentials.py) |
| 公共壳与管理视图 | [前端构建](../pages/frontend/build.mjs)；管理页测试需单独运行，`npm test` 当前只含 shell 测试 |
| 模块业务页面与资源 | [模块页面源码](../modules/ff14/pages/src)、[资源摘要](../modules/ff14/pages/resources.json)；构建后校验 manifest 和发布投影 |
| 旧 FF14 定位入口 | [静态兼容页](../modules/ff14/pages/compat) 构建投影到 `pages/ff14`；仅引导从宿主插件列表正式重开，不执行业务或转发授权 URL |
| 稳定持久目录 | [存储路由](../services/module_storage.py)；Core 选择 module_id，用户输入不能选择路径 |
| 正式包与 SDK | [发布构建](../scripts/build_release.py)、[插件包清单](../scripts/build_dashboard_zip.py)；核对实际 wheel、pin、资源和许可证，不只测试源码副本 |

配置按 `module_id` 命名空间保存。Core 的 cn/global 是游戏服务区域；FF14 日历默认值由模块定义。普通字段目录只提供声明，实际读取、写入和服务端语义校验另行授权。敏感配置不能声明普通 `value_schema`，不能出现在普通值或公开页；需要受信任、受限的管理策略和加密通道。管理请求还须通过来源、操作、资源、生命周期和 revision 校验。

来源声明约束主机与超时，受控 HTTP 校验路径格式及请求/响应预算；凭据消费另受可信策略的精确资源/token 路径约束。FFLogs 客户端凭据按国服/国际服分开，OAuth token 由 Core 消费通道持有，模块只发公开 API 请求。市场单价、品质、服务器和时间由验证后的结构化响应共同投影；接口刚获取不代表实时可买，区域请求成功不代表全部 World 完整覆盖。最多 5 条挂牌是样本。

页面源码与发布投影必须贯通构建、资源清单摘要和固定 Host 的重写规则。共享 Vue/Naive 运行时在各合法页面根发布一致字节，不能跨出 management 根导入 shell 运行时。模块资产按 owner/版本登记：同模块切页保留已加载资源；真正卸载、版本或安全失效清理自有样式/视图/浮层并封锁旧异步结果，再通过正式入口重新投影。`cleanup_pending` 不是重新启用许可。

`runtime_id + module_epoch` 标识运行 owner，页面本地请求序号隔离迟到结果，`catalog_revision` 是 Registry 投影版本。声明的 `module_version` 与根据 resource(path,sha256) 计算的 `asset_version` 不同；配置 revision 用于持久化 CAS。这些字段不能互相替代，也不是客户端自授权或安全热替换完成的证明。资源投影的短期访问证明只允许读取已授权资源，不授予业务调用或管理权限。

模块持久目录由稳定 `module_id` 的 SHA256 路由到 `modules/<摘要>/{config,cache,data,secrets}`，这是一组可用的所有权目录，不证明现有配置与缓存已经全部搬入。当前 `runtime.sqlite3` 与 SecretStore 仍共享；命名空间隔离不等于物理拆库。模块停用/卸载默认保留配置、缓存、秘密和业务数据；插件级宿主卸载还应明确选择保留配置/数据。代码 slot 不作为用户数据目录。没有迁移和恢复合同，不搬库或根据猜测清缓存。

第二模块的现有证据是隔离 fixture：通用导航、页面挂载/失效、配置声明拒绝未授权读写，以及非 FF14 工具的正式 Core/Host 调用。它不证明第二个生产包、任意来源或新管理权限已验收。模块声明、受信装配与实际部署能力应分别检查。

当前 Host 仍明确选择内置 FF14 发布包。装配描述及源码 fingerprint 证明选定输入的一致性，不是独立发布签名，也不让扫描到的模块自动获得来源、网页或管理权限。旧业务 renderer 和专属业务状态装配已退出；保留的旧物理路径只返回静态定位说明。物品、市场、Logs 和日历由正式 Vue 模块页执行。共享存储与旧安装槽的清理由独立迁移/恢复方案处理。

旧 Host `config_adapter.py` 仅保留弃用标记，不再提供普通配置兼容函数；实际调用已迁至 FF14 的配置支持。根 `pages/ff14` 由静态兼容页构建生成并核对字节，不能作为另一套手改业务实现。

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

Core 与模块的命名空间配置是当前读写权威；Core 目录不依赖 FF14 的 lifecycle 或 migration。原 native 四字段只保留受审的一次性导入机制，不再作为运行时配置权威或普通目录资源政策。非法初始配置仍可通过受权管理入口修复；本批没有自动执行任何真实配置或共享 SQLite 迁移。

`disable` 关闭业务准入，继续允许模块设置与启用管理。显式 `unload` 退出 active 普通/凭据目录、页面、来源、工具和 owner 注册，但模块管理仍显示可信恢复候选；保留最大部署政策不允许旧请求在卸载后提交。SDK 1.7 的生命周期 marker 仅用于精确 owner 管理操作，不能据此读写任何配置字段。

当前发布构建使用 `yomihime_module_sdk-1.7.0-py3-none-any.whl`，实际摘要与 pin 由构建核对；1.0–1.6 的兼容闭合集合保留，已发布 1.6 字节不改。升级固定 SDK 需要保留数据的宿主卸载与冷加载，以退出旧进程中已导入的 SDK；不能以同壳恢复模拟该步骤。本批已验证授权独立实例的安装、SDK 首次冷加载、最小公开查询、普通管理读写及模块保留数据解除挂载/恢复；真实凭据查询、IM 投递和新版本真实模型仍未验证。

固定宿主 iframe 不允许原生 `confirm` 或顶层导航。现有草稿与模块生命周期守卫使用有归属的页面内确认，保留操作、owner、revision 与生命周期检查；取消等待按钮解除禁用后才安全恢复焦点，不抢新页面或用户已转移的焦点。需要新资源投影时，使用宿主左侧“插件”菜单和目标 monitor 正式重开；页面内文字指引不承诺自动跳转、无刷新热加载或资源授权续期。

目录刷新按钮忙碌时保持原可聚焦节点，用 `aria-disabled`、`aria-busy` 和读取提示表达状态；实时忙碌检查与既有生命周期准入继续拒绝重复或失效请求。刷新结束不主动抢回焦点，切页与用户主动移焦点遵循各自原有行为。

对应检查入口：声明与工厂见 [磁盘解析测试](../tests/extensions/test_disk_manifest.py)、[工厂解析测试](../tests/extensions/test_factory_resolver.py) 和 [扩展生命周期测试](../tests/services/test_extension_runtime.py)；第二模块 fixture 见 [壳生命周期测试](../tests/pages/shell/lifecycle.test.mjs)、[配置目录测试](../tests/services/test_configuration_catalog.py) 和 [宿主工具测试](../tests/host/test_llm_tools.py)。凭据正反例见 [受管凭据测试](../tests/host/test_managed_credentials.py) 与 [来源凭据测试](../tests/services/test_source_credentials.py)。这些是定位与运行入口，实际通过范围仍以对应冻结版本的报告为准。

参考方向沿用此前记录的早柚核心审阅版本 `87c06f11ae10c12b3bb8e76b3c6f420c831282a8`，只用于模块注册、帮助、配置、订阅及展示的设计比较；本批未重新读取最新版或引入参考源码。game-link 已有上述合同和单 FF14 生产切片；生产多模块分发、任意包热替换、物理拆库和完整 IM 身份/投递验收仍有差距。不能用功能数量或百分比代替这些证据，也不应因此把游戏业务移入 Core。

维护时先运行变更涉及的模块、Core 输出及 Host 合同测试，页面修改显式运行管理/模块页面测试和正式构建预览。冻结后独立审查，主要跨层修改集成后安排一次全量。离线回放、隔离浏览器、真实宿主和模型回答分别记录；已有 ACL、跳过、格式及 EOF 提示单列。私有工作记录、凭据、数据库、备份和候选 ZIP 不入 Git 或发布包。
