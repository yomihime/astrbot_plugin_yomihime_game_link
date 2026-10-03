# 如月怜的游戏连结

面向 AstrBot 的游戏信息插件，当前仍处于开发阶段。插件介绍、安装包和开发说明也可查看[仓库](https://github.com/yomihime/astrbot_plugin_yomihime_game_link)。

## FF14 功能

- `/ygl ff14 status`：查看当前功能状态和上游限制。
- `/ygl ff14 item <物品名称或 ID>`：查询公开物品资料与已验证的获取途径。
- `/ygl ff14 logs <国服|国际服> <服务器名> <角色名> [metric=rdps|ndps|cdps]`：查询公开角色战绩。服务器名或角色名含空格时可用双引号包起；也可用 `server_hint=...` 缩小重名服务器结果。
- `/ygl ff14 calendar <国服|国际服> [days=7] [timezone=Asia/Shanghai]`：查询从当前本地日期开始的日历活动，`days` 范围为 1 到 30。
- `/ygl ff14 calendar subscribe <国服|国际服> [timezone=Asia/Shanghai] [time=08:00]`：主动订阅每日活动摘要，默认每天本地 08:00。首次完整采集在设定时刻或之后完成时，当天不发送，从次日开始发送。实际发送受采集周期影响，通常最多有一个 15 分钟采集周期的延迟，不承诺精确准点。
- `/ygl ff14 calendar subscriptions [page=1]`：查看本人订阅，每页最多 10 条；日历订阅管理需从本人私聊发出。
- `/ygl ff14 calendar update <subscription-id> <expected-revision> [timezone=IANA] [time=HH:MM]`：按当前 revision 修改本人订阅。
- `/ygl ff14 calendar cancel <subscription-id> <expected-revision>`：按当前 revision 取消本人订阅。

FFLogs 角色战绩查询需要按区域配置 OAuth 客户端凭据，维护步骤和平台限制见下节。国服和国际服凭据分别设置；未配置的区域会明确拒绝查询，不会改用匿名请求。物品和日历查询不需要 FFLogs 凭据。

`/ygl ff14 output ...` 当前不可用：FFLogs 统计页面返回 HTTP 403，且页面数据格式未通过验证，因此暂不展示分位数值。

上述命令已纳入当前开发实现。已验证隔离 Windows 长路径宿主的安装、重启与 help/status，以及 AstrBot 4.28.2 测试实例中的物品 44091 和普通配置保存/重载。国服角色 Logs 已通过隔离宿主的凭据导入、查询、同目录重启再查及清除验证。2026-10-03 已通过 HTTP API 将 SDK1.4 联调候选安装至本机 AstrBot 4.28.2：物品 44091 返回部分成功、国服日历返回成功；该实例未配置 FFLogs 凭据，角色查询返回 auth_required。此次没有核对日历活动条目和来源新鲜度，物品部分成功的具体缺项待查。实际定时投递及浏览器视觉、交互仍未验收；本地结果不代表所有部署环境均可用。

### FF14 插件页面

当前开发版页面从本次运行的模块目录读取导航；FF14 提供概览、公开角色 Logs、物品查询、活动日历和设置入口，其它已注册模块呈现通用只读信息。目录中的能力声明不代表已经支持网页调用。概览与设置只读插件运行状态、当前有效的普通配置及订阅推送状态；刷新不会检查外部来源。订阅开关开启、暂停、当前不可运行与状态未知分别显示，开启仅表示当前准入条件成立。暂停时保留订阅记录；模块正常时，本人仍可在私聊查看、修改和取消。恢复后只处理未来窗口，不补发暂停期间的窗口，暂停前已开始的发送可能完成。凭据状态和来源新鲜度无法确认时显示未知，不代表未配置或不可用。

三项网页查询已完成离线集成及本机 Host HTTP 入口联调，真实浏览器交互仍待验证；旧包可能仍只提供聊天命令。SDK1.3 升级到1.4需要冷启动，单纯热重载不能替换已经加载的SDK；本机已验证保留配置和数据卸载、重启宿主后安装新包的升级流程。网页查询需要普通 Dashboard 登录及下表的网页访问地址设置，只在手动提交时请求来源。停止展示或离开页面不保证后台请求立即取消。本人订阅仍需在本人私聊中查看和管理，页面不显示个人订阅；FFLogs 凭据也不在页面或普通表单填写。修改普通配置请返回已安装插件列表，点击本插件卡片的配置齿轮，保存后由 AstrBot 重载插件。

### 普通插件配置

在 AstrBot 插件管理中打开本插件的原生配置表单。保存会重载插件；编辑前先刷新表单，避免旧页面覆盖新设置。

| 配置项 | 默认值 | 用途 |
| --- | --- | --- |
| 默认 FF14 区域 | `cn` | 接受 `cn`、`global`，用于帮助和缺参提示；命令仍需明确填写区域 |
| 日历默认查询天数 | `7` | 整数 1–30，用于省略 `days` 的新查询 |
| 日历默认时区 | `Asia/Shanghai` | 可解析的 IANA 时区，用于省略 `timezone` 的新查询和新订阅 |
| 新订阅每日摘要时间 | `08:00` | 严格的 24 小时制 `HH:MM`，用于显式新建订阅时省略 `time` 的情况 |
| 网页访问地址（`web_public_origin`，开发版新增） | 空 | 填写实际打开 AstrBot 的单一地址原点，例如 `http://localhost:19266`；仅协议、主机和端口，不含账号密码、页面路径或查询参数 |

命令中的显式参数优先；修改默认值不会自动创建订阅或改写已有订阅。普通配置不存储 FFLogs 凭据，也不控制 Core 的订阅开关。

网页访问地址留空或格式无效时，仅关闭网页查询，聊天和后台功能保持原有规则；修改后保存、重载并重新打开页面。使用反向代理时填写浏览器实际访问的地址，不填写内部服务地址。

其余四项 FF14 设置的宿主表单类型检查不能覆盖全部范围、时区及时间格式。插件会在启动前进一步校验；无效设置可能已保存，但插件会暂停启动并在命令回复中说明修复方式，不运行后台任务或使用凭据。请修正配置后重新保存、重载；保存请求成功不等于插件功能已恢复。

### 管理员本地维护 FFLogs 凭据

先停止 AstrBot，并确认插件数据目录中已有 `runtime.sqlite3`。在安装包目录 `astrbot_plugin_yomihime_game_link/` 的父目录，用具备本地文件所有者权限的交互终端运行；平台验证边界见下文。不要从源码工作树或聊天命令执行：

```text
python -m astrbot_plugin_yomihime_game_link.scripts.admin_credentials --database <existing-data-dir>/runtime.sqlite3 bootstrap
python -m astrbot_plugin_yomihime_game_link.scripts.configure_source_credentials --data-dir <existing-data-dir> --realm cn set
python -m astrbot_plugin_yomihime_game_link.scripts.configure_source_credentials --data-dir <existing-data-dir> --realm global set
```

首次使用先 bootstrap 独立管理员凭据；凭据仅通过隐藏提示输入。FFLogs 客户端 ID 和密钥也使用隐藏提示，可按区域分别设置；清除区域凭据使用对应命令并将 `set` 改为 `clear`。AstrBot 与维护命令必须使用同一份外部管理的 `YGL_SECRET_KEY`（base64 编码的 32 字节密钥）；不要把它或任何凭据放在命令行参数中。工具要求数据库、脚本和父目录权限受限。维护进程本身不启动 Core，也不执行网络查询；完成配置后由管理员重新启动 AstrBot。

POSIX 入口沿用交互终端和文件所有者检查。当前 Windows 开发候选正在验证本地权限检查：仅接受受限 NTFS 目录和明确支持的运行环境，身份、权限或文件状态无法确认时拒绝执行。操作者仍须先停止 AstrBot；工具不负责停止宿主。基础 Windows 权限与 SQLite 写入试验已通过，755c候选在指定隔离宿主完成凭据写入、首次及重启角色查询、清除与停机验证；404c候选未重跑凭据维护，当前用户测试实例也未配置FFLogs凭据。这些限定结果不代表所有Windows环境或当前实例的凭据链路均已通过。环境范围见[贡献指南](https://github.com/yomihime/astrbot_plugin_yomihime_game_link/blob/master/CONTRIBUTING.md)。

## 安装

请安装 Actions 构建出的插件 ZIP：

1. 打开[构建工作流](https://github.com/yomihime/astrbot_plugin_yomihime_game_link/actions/workflows/build.yml)，选择通过验证且构建成功的运行记录。
2. 下载该记录中的 `release-artifacts-<run-id>` 工件，并解开下载得到的外层压缩包。
3. 在解开的文件中找到 `astrbot_plugin_yomihime_game_link-v0.1.2.zip`，通过 AstrBot WebUI 的插件管理上传这个内层 `ZIP`。

不要把外层 Actions 工件 `ZIP`、独立的 SDK `.whl` 或 `SHA256SUMS` 上传为插件。项目仓库直装尚未包含 release 构建时从已验证 SDK wheel 注入的运行资源，不能作为开箱即用的安装方式。

当前 `metadata.yaml` 声明的版本是 `v0.1.2`，尚无对应 tag 或 GitHub Release。Actions 工件属于开发构建，不要把它当作正式发布。

较早的插件 `ZIP` 未包含 `README.md`。请下载并安装包含 README 的新构建包，然后在插件管理中重载插件或重新打开插件详情；刷新页面不能补回旧安装包中缺失的文件。

## 开发

开发环境、构建命令和验证范围见[贡献指南](https://github.com/yomihime/astrbot_plugin_yomihime_game_link/blob/master/CONTRIBUTING.md)。项目更新记录见[CHANGELOG](https://github.com/yomihime/astrbot_plugin_yomihime_game_link/blob/master/CHANGELOG.md)。本地构建会生成插件 ZIP、独立 SDK wheel 和 SHA-256 校验文件。发布插件到 AstrBot 市场时，请以[AstrBot 插件发布说明](https://docs.astrbot.app/dev/star/plugin-publish.html)为准。

## 来源与许可

插件骨架由 AstrBot 的[helloworld 插件模板](https://github.com/Soulter/helloworld)初始化。仓库许可证见[LICENSE](https://github.com/yomihime/astrbot_plugin_yomihime_game_link/blob/master/LICENSE)；随包 SDK 的许可声明以独立 SDK 工件为准，两者的许可对应关系仍待核对。
