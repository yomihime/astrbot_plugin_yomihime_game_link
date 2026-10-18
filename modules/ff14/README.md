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
- 主、备源分别最多 3 秒网络预算，慢主源不会耗尽备用源的预算；后台失败保留同源最后完整数据，并按所选周期再次尝试。重启保留到期时间，不因重启重新拉取新鲜数据。进程在请求中断开也计入本轮预算；Core 的短租约恢复仅回收任务执行权，不绕过来源到期时间。没有快照时，用户在5分钟冷却后仍可通过普通查询或手动刷新恢复
- 每个区服/来源固定保存一份完整和一份不完整原始 ICS，各最多 1,000,000 个 UTF-8 字节，共最多四个来源槽。完整 URL 指纹参与身份校验，换源不复用旧内容。合法空日历可以替换旧数据，不完整结果不能覆盖完整快照
- 保留原始 ICS，查询按本次 IANA 时区和 1–30 天窗口展开；浮动时间仍按查询时区解释，不声称这是发布者时区。提醒拒绝无法确定时区的内容
- 显示真实成功获取时间，来源内容更新时间仍标为未知。超过两个同步周期提示陈旧；失败时明确说明正在显示上次完整日历。没有任何可用快照时明确失败
- 为避免发送过旧活动信息，超过 48 小时未成功获取的快照不生成自动摘要；恰好 48 小时仍允许。普通查询继续展示并提示陈旧，成功同步后提醒自行恢复。此限制不修改已有订阅时间或补发已经错过的日期

此功能不使用 ETag/304；模块停用、卸载和旧运行实例的迟到写入由 Core 生命周期与持久任务租约拒绝。配置仍使用现有 revision/CAS 更新，不重写历史订阅。

此版本使用 SDK 0.1.0a8 的独立后台调度声明。安装构建包后需完整重启 AstrBot 以冷加载新 SDK；不能把代码合并或离线测试视为已经部署、真实上游同步或提醒送达验收。

## 日历订阅

- `/ygl ff14 calendar subscribe <国服|国际服> [timezone=Asia/Shanghai] [time=08:00]`：默认时区为 `Asia/Shanghai`，默认每日 08:00，仅在本人私聊中可以订阅。
- `/ygl ff14 calendar subscriptions [page=1]`：查看本人订阅，每页最多 10 条。
- `/ygl ff14 calendar update <subscription-id> <expected-revision> [timezone=IANA] [time=HH:MM]`；`/ygl ff14 calendar cancel <subscription-id> <expected-revision>`：使用本人订阅 ID 与当前 revision 修改或取消。

每个订阅为所选区服汇总一张活动摘要，只包含公开日历内容。订阅需先完成一次完整采集建立基线；首次完整采集在设定时刻或之后完成时，当天不发送，从次日开始发送。之后的数据在每个采集周期检查，设定时刻后的完整采集会触发当天摘要，通常最多有 15 分钟延迟，不承诺准点送达。

## FFLogs 限制

管理员本地配置 FFLogs 凭据时，先停止 AstrBot，并确认插件数据目录中已有 `runtime.sqlite3`。从安装目录 `astrbot_plugin_yomihime_game_link/` 的父目录，在具备文件所有者权限的 POSIX 交互终端运行；不要从源码树或聊天命令执行：

```text
python -m astrbot_plugin_yomihime_game_link.scripts.admin_credentials --database <existing-data-dir>/runtime.sqlite3 bootstrap
python -m astrbot_plugin_yomihime_game_link.scripts.configure_source_credentials --data-dir <existing-data-dir> --realm cn set
python -m astrbot_plugin_yomihime_game_link.scripts.configure_source_credentials --data-dir <existing-data-dir> --realm global set
```

先通过隐藏提示建立独立管理员凭据，之后可按区域分别设置 FFLogs 客户端 ID 和密钥；`clear` 操作会额外要求隐藏输入 `CLEAR` 确认。AstrBot 和维护工具必须使用相同的外部持久 `YGL_SECRET_KEY`（base64 编码的 32 字节密钥）。凭据不接受命令行参数。维护仅支持能验证本地权限的 POSIX 交互环境；Windows 当前无法验证所需 ACL，会失败关闭。工具要求已有数据库，不会创建或初始化 AstrBot 数据目录。维护进程本身不启动 Core，也不执行网络查询；完成配置后由管理员重新启动 AstrBot。

安装后的帮助入口：

```text
python -m astrbot_plugin_yomihime_game_link.scripts.configure_source_credentials --help
python -m astrbot_plugin_yomihime_game_link.scripts.admin_credentials --help
```

未配置的 FFLogs 区域会失败关闭，不会改用匿名请求。物品和日历查询不需要 FFLogs 凭据。

`/ygl ff14 logs <服务器名> <角色名> [server_hint=...] [metric=rdps|ndps|cdps]` 只查询公开角色数据，不访问私密战斗报告。服务器的地区归属来自可信服务器目录，FFLogs 的 server ID 和 slug 仍由 FFLogs 解析；目录不可用、未知或歧义时分别失败，不猜地区或改用另一地区凭据。旧版“地区＋服务器＋角色”三位置写法不再接受。自然聊天中显式说明的地区只作归属校验，不改变其他能力的默认地区。

可按区域单独配置 FFLogs OAuth 客户端凭据；管理员须使用宿主提供的加密凭据配置入口。未配置的区域会失败关闭，不会降级成匿名请求。目前国服角色查询已有真实公开 API 历史证据；国际服结果仍为待核对的参考结果，图片成功不代表目录覆盖完整。

FFLogs 输出统计尚未实现，当前不提供；旧 `output` 指令不再注册。


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
