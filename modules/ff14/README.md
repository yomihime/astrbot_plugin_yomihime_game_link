# FF14 模块

本模块通过 `/ygl ff14` 提供公开查询和本人日历订阅。

## 查询

| 命令 | 说明 |
| --- | --- |
| `/ygl ff14 status` | 查看功能状态。 |
| `/ygl ff14 item <名称或 ID>` | 查物品公开资料、多候选与获取途径。 |
| `/ygl ff14 logs <服务器> <角色> [metric=rdps\|ndps\|cdps]` | 由可信服务器目录自动选择地区，查公开角色战绩；重名服务器可用 `server_hint=...` 筛选。服务器或角色名有空格时用引号包起。|
| `/ygl ff14 calendar <国服\|国际服> [days=7] [timezone=Asia/Shanghai]` | 查从当前本地日期开始的日历活动，`days` 为 1–30。|

日历查询读取本地持久 ICS 快照，普通查询不会再次联网。模块启用后，Core 为国服、国际服分别管理后台同步，默认每 6 小时，可在模块设置「日历同步周期」改为 12 或 24 小时；没有任何提醒订阅也会同步。提醒仍每 15 分钟检查同一份快照，不额外请求上游。

- 第一次没有可用快照时，查询做一次有界同步；主源完整成功后不请求备用源。主源失败或不完整才尝试固定备用源，数据不混合
- `/ygl ff14 calendar 国服 refresh=true` 可手动刷新；这与普通查询一样允许命令和公开网页调用，每个区服/来源有持久的 5 分钟冷却，所有查询、手刷和后台同步共享单次请求。冷启动失败后的重复查询也遵守冷却
- 主、备源分别最多 15 秒完整获取预算，包含排队、连接、响应头和完整响应体；慢主源到期后，备用源仍有独立的 15 秒预算。受信装配为日历查询与后台同步声明 45 秒整轮上限，为双来源获取及解析、持久化留出余量；命令入口仍使用原有 90 秒上限，后台同步执行租约为 55 秒。热查询直接读取本地快照，不等待网络。后台失败保留同源最后完整数据，并按所选周期再次尝试。重启保留到期时间，不因重启重新拉取新鲜数据。中断的来源请求计入本次期限；Core 的短租约恢复仅回收任务执行权，不绕过来源到期时间。没有快照时，用户在 5 分钟冷却后仍可通过普通查询或手动刷新恢复
- 每个区服/来源固定保存一份完整和一份不完整原始 ICS，各最多 1,000,000 个 UTF-8 字节，共最多四个来源槽。完整 URL 指纹参与身份校验，换源不复用旧内容。合法空日历可以替换旧数据，不完整结果不能覆盖完整快照
- 保留原始 ICS，查询按本次 IANA 时区和 1–30 天窗口展开；浮动时间仍按查询时区解释，不声称这是发布者时区。提醒拒绝无法确定时区的内容
- 显示真实成功获取时间，来源内容更新时间仍标为未知。超过两个同步周期提示陈旧；失败时明确说明正在显示上次完整日历。没有任何可用快照时明确失败
- 为避免发送过旧活动信息，超过 48 小时未成功获取的快照不生成自动摘要；恰好 48 小时仍允许。普通查询继续展示并提示陈旧，成功同步后提醒自行恢复。此限制不修改已有订阅时间或补发已经错过的日期

此功能不使用 ETag/304；模块停用、卸载和旧运行实例的迟到写入由 Core 生命周期与持久任务租约拒绝。配置仍使用现有 revision/CAS 更新，不重写历史订阅。

此模块随插件使用当前固定 SDK，并用独立后台调度声明。SDK 版本、ABI 与冷加载边界见[模块 SDK 合同](../../docs/module-sdk.md)及[开发指南](../../CONTRIBUTING.md)。代码或离线检查不能证明已经部署、真实上游同步或提醒送达。

## 日历订阅

- `/ygl ff14 calendar subscribe <国服|国际服> [timezone=Asia/Shanghai] [time=08:00]`：默认时区为 `Asia/Shanghai`，默认每日 08:00，仅在本人私聊中可以订阅。
- `/ygl ff14 calendar subscriptions [page=1]`：查看本人订阅，每页最多 10 条。
- `/ygl ff14 calendar update <subscription-id> <expected-revision> [timezone=IANA] [time=HH:MM]`；`/ygl ff14 calendar cancel <subscription-id> <expected-revision>`：使用本人订阅 ID 与当前 revision 修改或取消。

每个订阅为所选区服汇总一张活动摘要，只包含公开日历内容。订阅需先完成一次完整采集建立基线；首次完整采集在设定时刻或之后完成时，当天不发送，从次日开始发送。之后的数据在每个采集周期检查，设定时刻后的完整采集会触发当天摘要，送达时间取决于采集与调度周期，通常可能延迟最多 15 分钟。

## FFLogs 配置与限制

从 AstrBot 正式插件菜单打开唯一 GameLink 页面，进入 FF14 的「业务配置」，分别填写国服、国际服完整 Client ID 与 Client Secret，直接保存或替换，再查询 Logs。页面分别显示未配置、已配置与可解密状态；保存成功的上游状态仍为未验证，FFLogs 认证结果需通过后续查询确认。

存储、主密钥、环境来源冲突、恢复及全域重配在「全局设置」处理。FF14 页面不接收主密钥，也不提供存储迁移或密钥重置操作。具体安全前提、旧实例限制与恢复语义见[凭据存储规范](../../docs/credential-storage.md)。

高级部署的环境来源和离线维护工具见[项目说明的 FFLogs 来源凭据](../../README.md#fflogs-来源凭据)。主密钥和 Client Secret 只通过安全控件输入，不要发到聊天、URL 或日志。备份需一致保存数据库、密文及私有托管密钥文件，不能只复制数据库。

未配置的 FFLogs 区域会失败关闭，不会改用匿名请求。物品和日历查询不需要 FFLogs 凭据。

`/ygl ff14 logs <服务器名> <角色名> [server_hint=...] [metric=rdps|ndps|cdps]` 只查询公开角色数据，不访问私密战斗报告。服务器的地区归属来自可信服务器目录，FFLogs 的 server ID 和 slug 仍由 FFLogs 解析；目录不可用、未知或歧义时分别失败，不猜地区或改用另一地区凭据。旧版“地区＋服务器＋角色”三位置写法不再接受。自然聊天中显式说明的地区只作归属校验，不改变其他能力的默认地区。

可按区域单独配置 FFLogs OAuth 客户端凭据；管理员须使用宿主提供的加密凭据配置入口。未配置的区域会失败关闭，不会降级成匿名请求。查询只使用所选区域来源；可见结果的范围由该次实际返回和覆盖状态决定，不能仅凭图片生成或保存凭据推断目录完整。

FFLogs 输出统计尚未实现，当前不提供；旧 `output` 指令不再注册。

## 市场查询 facts 版本

In the normal Module capture path for FF14 market public facts, the legacy `core_revision` key and `module_revision` both report the revision of that same captured destination snapshot, not a global Core revision.

## 模块页面与业务配置

FF14 在唯一 `00-game-link` 页面根中注册主页、业务配置视图及物品、市场、Logs、日历四个辅助查询视图；各视图通过该页面入口访问。主页简述模块功能；查询保留现有展示与来源限制。业务配置维护模块自身的默认区域、日历天数、IANA 时区、投递时间、同步周期及订阅开关，以及两组 FFLogs 来源凭据；回复图片主题可以显式覆盖或选择「跟随全局设置」（页面提交 null，由服务端转换为 CLEAR；持久层删除覆盖，不存储 null）。可选主题和标签读取公开配置描述，由 Core 的受信通用展示来源提供，不在模块复制调色板。全局默认主题、字体与凭据存储管理归全局设置。

前端只从 [公开页面合同](../../pages/public-contract.ts) 消费 `PageOptions`、受限 `invoke` 与 owner 绑定的配置及凭据服务；不导入壳控制器或 HTTP 管理接口。注册与授权规则见[前端接入合同](../../docs/frontend-module-contract.md)。目录声明本身不授予管理权限；每次读写仍由后端检查页面、字段、当前实例及授权。

普通草稿按 owner、runtime、epoch、boundary 隔离，同一绑定切回可恢复。保存期间仍可修改普通值和主题继承，新草稿保持未保存状态，旧回执或回读不会覆盖。普通配置与两组凭据共享同一 target revision；页面使用一致快照与 CAS，外部冲突须重新读取，不自动重试写入。

普通写入失败、CAS 冲突或结果未确认时，本 owner/view 的普通草稿仍可编辑，但保存保持阻断，直至重新读取一致快照。待返回或未确认写入之后显式选择「跟随全局设置」及普通默认值，即使等于旧基线也保留为新的未保存意图；刷新不能把它覆盖成可能已提交的旧值。新快照只更新已确认基线，之后由用户显式提交；「放弃修改」是单独的清除草稿操作。真正授权失效、切 owner、旧 view 撤销后，普通编辑与秘密输入均遵守原有围栏。

凭据输入只在内存，每项最多 512 字符且拒控制字符；保存成功清空本区域输入，取消清除确认保留两区域草稿及当前可用状态，授权/owner 失效或卸载则清空全部秘密。重复提交被禁止。清除由壳显示当前页面及 revision 绑定的确认，取消或 Escape 是未写入的中性操作。手动「停止等待」不能撤回已发出的操作；响应丢失时先重新读取状态，再决定是否保存。授权或模块绑定变化后旧结果不能重新落到新页面。

非 FF14 的最小接入参见[中性样例](../../examples/modules/minimal/README.md)，不需要为新模块添加壳内业务分支。


## 模块徽标来源与权利说明

FFXIV METEOR MODULE EMBLEM

File: ff14-meteor.svg
Trusted, bundled, hand-authored monochrome SVG reference rendering.
Copied unchanged from the five user-approved declarative card samples. It is
not an official vector or exact logo file. The hollow ring and asymmetric
flames were recreated with reference to Square Enix's product photography:
https://na.store.square-enix-games.com/final-fantasy-xiv-messenger-bag

FINAL FANTASY XIV and its marks belong to Square Enix. This note makes no claim
of ownership or permission to redistribute the underlying mark and does not
apply a new license to it. Review suitability for each distribution. No official
photograph or font is included.

The manifest declares this bundled asset as display.logo_resource and pins its
SHA-256 in resources. The module emits card.logo=None; Core resolves that
registered default to captured immutable bytes through the public SDK Asset
contract. Module code performs no filesystem or remote reads for presentation.
Core owns SVG validation, theme color substitution and rasterization. Explicit
SDK Asset values can override a registered logo without adding any game logic
in Core. The original fallback document and full source links are preserved.
