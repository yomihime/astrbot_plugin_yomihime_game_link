# 开发主计划

> 文档定位更新（2026-09-29）：本文保留全局架构范围、CLOSEOUT 和 Core Ready 条件，不再作为当前派发入口。后续范围、状态、测试与配置/页面方案统一见 [新计划](next-stage-plan.md)。当前为 PLANNING；旧实施授权和阶段说明仅解释历史，不自动启动新工作。

> 更新：2026-09-27。本文定义范围、顺序和完成标准；后续状态统一维护在[新计划](next-stage-plan.md)。
> 本轮内核收束入口：[CORE-HARDENING-01 总任务卡](../.coordination/tasks/core/CORE-HARDENING-01.md)，已完成本地验收；实时状态与后续派发统一见[执行看板](../.coordination/board.md)。

## 1. 已确认的方向

2026-09-28 用户确认以 FF14 模块联调推动内核完善：按 [FF14-MVP-01](../.coordination/tasks/modules/ff14/FF14-MVP-01.md) 的纵向切片配对派发 Core 与模块任务，不再等待全局 Core Ready OPEN 才允许 FF14 开发。每片只补真实用例需要的主体能力，保持模块通过公开 SDK 调用；通过该片的离线、真实来源和目标宿主验收后，才启用对应能力。Steam、HBR 保持原 Core Ready 前置；Dota 2 仍等待参考插件。保留现有 SDK、Registry、服务、SQLite、展示与宿主适配分层，不做整体重写。

初审时已有较完整的离线组件，但缺少统一准入、实例装配和产品入口闭环；本轮实现及复审进度统一记录在看板。按[2026-09-27 审查](../.architecture-refactor/core-review-2026-09-27.md)收束，不再用旧批次的任务卡完成数量衡量总体可用性。

实际公共声明在 `yomihime_sdk/api/`，旧 `api/` 是转发；本地 Windows scanner 已有受限平台验证。`docs/module-sdk.md` 参与固定 wheel 元数据；本轮 K 已同步校正说明并重建 wheel/宿主 bootstrap pin。后续修改仍须同批重建工件，不能只改说明而破坏已有校验。

- 历史 C00 最小契约、B02/B03/B04 在当时范围内验收成立，保留其实现和证据。
- B05 只有有限本地切片：SDK/工件、静态扫描、管理 facade/凭据、help-only 宿主入口；整批未验收。
- 旧任务/手册与被取代的提案已进入[归档](../.coordination/archive/2026-09-27-pre-hardening/README.md)。归档不删除未完成需求，也不追认 B05 accepted。
- 模块可独立启停；能力级配置/依赖缺失只影响相关能力，允许 active/degraded。旧候选提案的 all-AVAILABLE 启动条件不再适用。

## 2. 已完成阶段：内核本地收束

已完成 [CORE-HARDENING-01](../.coordination/tasks/core/CORE-HARDENING-01.md) 的 W0–W5。当前卡保留收束状态和证据入口；子任务范围、波次、所有权与派发提示保存在[原执行卡](../.coordination/archive/2026-09-27-core-hardening/.coordination/tasks/core/CORE-HARDENING-01.md)，20 项验收见[证据索引](../.architecture-refactor/hardening/acceptance-matrix.md)，不另开一套重复总计划。

交付范围：

1. F1/F2/F7：模块代次、共同准入、根任务超时/取消、作用域与授权记录回收。
2. F4/F5：命令/Tool 完整权限路径、候选服务与能力健康，允许部分能力不可用。
3. F3：发送许可与停用/撤销的并发边界，保持 receipt/UNKNOWN 语义。
4. F6：SQLite 有界检查、真实异步执行与事务线程归属。
5. 本地 activation、真实 AdminOperations、持久启用意图及失败恢复；已安装 SDK 样例通过真实组件组合。

结束条件是 `accepted_local`，不是 Core Ready。真实外部 IO 可注入替身，但 Registry/Gateway/Lifecycle/管理消费者和 SQLite 不能用替身代替。

## 3. 后续 CLOSEOUT 清单

本表保留未完成工作。FF14 联调所需的 HTTP、秘密、宿主、订阅管理隐私等子范围，由统筹按新总卡中的真实代码与文件权属逐片派发；未涉及的 CLOSEOUT 工作继续留在 backlog，不要求先完成整表。不得用旧白名单原样重启 B05，也不能把 FF14 某片完成追认为整个 CLOSEOUT 完成。本轮先修订任务卡，尚未派发代码实现。

| ID | 范围与责任边界 | 前置及完成标准 |
| --- | --- | --- |
| CLOSEOUT-HTTP | 生产网络 transport、凭据引用解析、TLS/代理/重定向/超时/限流与额度 | 消费受控 SourceHttp；真实受控网络验证；秘密不进入日志/模型；不加入游戏专属 API |
| CLOSEOUT-SECRETS | 生产加密 codec、key provider、密钥部署/轮换及恢复 | 保留 SQLite/secret 账本语义；重启可解密、缺 key 失败、无明文降级；明确部署方式与许可 |
| CLOSEOUT-IMAGE | 通用展示块排版成图片，资源读取、字体/素材许可、限额和文本回退 | 实际生成并查看图像；无游戏分支，不仅返回已有 asset ID |
| CLOSEOUT-PACKAGE | 扩展兼容更新、保留启用意图、restart-required、卸载与 orphan 数据可见性 | 复用唯一 runtime；旧版回退/安全禁用有定义；配置/绑定/订阅/缓存不暗删；覆盖原 E-08 更新部分、E-09/E-10 |
| CLOSEOUT-CONTRACT | 基本设计 DependencyDescriptor 的显式必需/可选模块依赖、版本约束与当前 SDK 的差异收束 | 当前 SDK 为能力级 required_capabilities；HARDENING H01/H06 验证依赖停用只降级关联能力、模块独立能力继续可用。单独核对是否仍需扩展模块级公开声明；若新增，明确 ABI 兼容和已安装工件回归，不允许未声明调用 |
| CLOSEOUT-MANAGEMENT-PRIVACY | 不依赖外部 Grant 的用户私密管理结果与可用的账号/订阅查看命令 | 当前 SDK/Core 的 PRIVATE 结果与 Grant 授权耦合，不能安全表达仅属于可信聊天用户的管理列表。需显式用户结果权限、内部 principal、DIRECT 收件人与最后发送复核的契约设计及兼容回归。HARDENING 离线样例仅验证真实增删查委托和持久隔离，管理回执为不含任何用户信息的固定 PUBLIC 文本；这不完成面向用户的列表展示或从回执取得管理 ID，不得将原带 ID 结果改标 PUBLIC 或要求普通绑定预有 Grant |
| CLOSEOUT-HOST | 持久 PluginRuntime 接 main；可信 actor/conversation、命令解析、Tool 发布撤销、MessagePort、调度驱动、启动/关闭 | 目标 AstrBot/Python/root/import identity 实证；受控会话验证 command/Tool/receipt/停止，不把 activated=true 当验收 |
| CLOSEOUT-WEB | 动态管理页面、配置/敏感字段、模块/能力/调度状态、服务端管理授权 | 依赖已实现 AdminOperations 及目标 session/route/CSRF；零模块、冲突、未授权、可信来源展示均实证；无游戏专属布局 |
| CLOSEOUT-DATA | 接管早期内部/直接仓储调用生成的数据前，盘点未初始化调度行与用户所有权命名空间 | 旧公开 main/B05 只有 help/静态发现，无订阅创建入口；此证据不代表历史数据库无内部写入。当前 Core 新建记录必须完整持久调度并可重开；旧 NULL job 被 CREATE/REVISE 触达时已可补齐。未触达遗留行及不明 namespace 所有权需要可信、有界、可审计的接管方案，不自动猜测映射或删除业务数据 |
| CLOSEOUT-READY | 全部内核能力与宿主纵向验收、可安装工件、独立终检 | 下述 Core Ready 条件全部通过；未验证项必须列出，不能抵消必需门禁 |

上述 HTTP、加密、图片与宿主不是游戏模块的补课内容；不得让游戏 agent 自建库、直接渲染或发送来绕过缺失能力。本地任务不等待全部宿主证据，但宿主任务缺相应环境时只能记录 blocked。

## 4. B05 原验收的接续关系

编号含义见[历史 B05 卡](../.coordination/archive/2026-09-27-pre-hardening/.coordination/tasks/B05/README.md)，这里明确新的责任范围，避免归档后遗漏。

| 原卡 | 本卡负责 | 后续保留 |
| --- | --- | --- |
| H-01–H-14 | H-11 管理合同及实际消费、H-12 本地 ABI、H-13/H-14 本地授权/代次回归 | H-01–H-10 目标宿主整体；H-12 目标 root；H-13/H-14 session/CSRF/真实 route/transport → HOST/WEB；生产秘密后端 → SECRETS |
| E-01–E-11 | E-01–E-07/E-11 静态和授权装载回归；E-08 本地 disable/stop | E-04 未覆盖目标平台；E-08 host Tool/update；E-09/E-10 更新/卸载 → PACKAGE/HOST |
| W-01–W-14 | 对应 Core DTO、真实 Operations、配置/权限/凭据的本地消费者部分 | 所有页面渲染、session、route、真实读写及可见行为 → WEB/HOST；绝不把 facade 单测视为 W 整卡通过 |
| K-01–K-09 | 全部重建安装工件、唯一身份、样例与兼容回归；增加真实内核组合 | 目标宿主 Python/import 支持范围 → HOST/READY |
| R-01–R-12 | R-02/R-03 本地激活、R-06–R-10 本地组合、R-11 业务范围与 R-12 本地证据 | R-01/R-03–R-05 真实宿主、R-06 页面、R-08 Tool 撤销及 R-12 总体验收 → HOST/WEB/READY |

原项的未完成部分不得因为“本地已覆盖”被改成全项通过。需求、基本设计优先于历史任务细则；独立开关与 degraded 需求已经在新卡纠正。

## 5. Core Ready Gate

以下条件定义**整体内核就绪**，证据集中在 [core-ready](../.coordination/contracts/core-ready.md)，状态由看板引用。Steam/HBR 仍待整体开放；FF14 按用户最新决定采用逐片开发与验收，不受整体 OPEN 的开发前置限制。FF14 联调不能跳过秘密、权限、发送、数据保护和实际 IO 的对应片验收，也不自动改变整体 Core Ready 状态。图片增强、完整 Web 管理、包更新/卸载及其它与本片无关的项继续单独记录，不阻塞已具备安全闭环的 FF14 文本能力。

1. 唯一公开 SDK、明确兼容版本和可安装工件；模块不用测试包名导入公共类型。
2. Registry/Gateway/Policy/Help/能力健康/生命周期/任务/依赖调用真实实现，F1–F7 关闭；独立启停和部分可用正确。
3. SQLite 迁移及配置/集合/缓存/资源/身份/绑定/授权真实服务可用；生产秘密后端无明文降级。
4. 受控 HTTP、文本及图片/资源降级、命令/Tool/订阅出口完成，核心无游戏分支。
5. 共享采集、私人隔离、匹配/持久投递、摘要/重试/UNKNOWN/重启恢复均有并发和恢复验证。
6. 扩展发现、兼容、激活/停用、更新/卸载与数据保留完成；零模块可运行，真实多模块组合通过。
7. 真实 AstrBot 下 `/ygl`、两级 help、模块命令、Tool、MessagePort、调度、管理页面和启动/停止完成目标环境验证。
8. SDK 空模板/离线样例、脱敏 fixture 和测试说明可复用；样例经过真实内核，不能仅 status 或静态扫描通过。
9. 可重建源码/工件与明确环境、独立最终审查和全部阻断关闭。未验证边界逐项记录，不与必需功能混淆。

## 6. 状态和归档规则

新计划集中维护当前阶段、角色、依赖与用户决定；看板仅作导航，本文不复制逐日进度。原始实现/审查证据归档保存，最新状态不从多个 handoff 拼接。

已完成批次归档为 historical_accepted；被新方案替换但未完成的卡为 superseded_partial；planned_gated 模块卡仍保留。未完成需求必须在本卡或 CLOSEOUT 有接续项，不能因移动文件而关闭。

每轮交接绑定内容哈希或可重建提交，保留已有未提交工作。提交、发布和部署不因计划定稿自动执行。
