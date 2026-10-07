# 如月怜的游戏连结

面向 AstrBot 的游戏信息插件，当前仍处于开发阶段。插件介绍、安装包和开发说明也可查看[仓库](https://github.com/yomihime/astrbot_plugin_yomihime_game_link)。

## FF14 功能

- `/ygl help`：当前模块目录；`/ygl ff14 help`：FF14 能力目录；具体指令后加 `help` 查看参数。
- `/ygl ff14 status`：查看当前功能状态和上游限制。
- `/ygl ff14 item <物品名称或 ID>`：查询公开物品资料与已验证的获取途径。
- `/ygl ff14 market <物品名称或 ID> [region=cn|global] [server=服务器|dc=大区] [quality=all|nq|hq] [intent=overview|min|listings]`：手动查询 Universalis 行情。省略范围时使用 Core 默认区域全区；名称有歧义时选择候选后保留原范围、品质和意图。最低价仅指本次返回数据，挂牌明细最多 5 条样本，不保证全部服务器覆盖或实时可买。
- `/ygl ff14 logs <国服|国际服> <服务器名> <角色名> [metric=rdps|ndps|cdps]`：查询公开角色战绩。服务器名或角色名含空格时可用双引号包起；也可用 `server_hint=...` 缩小重名服务器结果。
- `/ygl ff14 calendar <国服|国际服> [days=7] [timezone=Asia/Shanghai]`：查询从当前本地日期开始的日历活动，`days` 范围为 1 到 30。
- `/ygl ff14 calendar subscribe <国服|国际服> [timezone=Asia/Shanghai] [time=08:00]`：主动订阅每日活动摘要，默认每天本地 08:00。首次完整采集在设定时刻或之后完成时，当天不发送，从次日开始发送。实际发送受采集周期影响，通常最多有一个 15 分钟采集周期的延迟，不承诺精确准点。
- `/ygl ff14 calendar subscriptions [page=1]`：查看本人订阅，每页最多 10 条；日历订阅管理需从本人私聊发出。
- `/ygl ff14 calendar update <subscription-id> <expected-revision> [timezone=IANA] [time=HH:MM]`：按当前 revision 修改本人订阅。
- `/ygl ff14 calendar cancel <subscription-id> <expected-revision>`：按当前 revision 取消本人订阅。

FFLogs 角色战绩查询需要按区域配置 OAuth 客户端凭据，维护步骤和平台限制见下节。国服和国际服凭据分别设置；未配置的区域会明确拒绝查询，不会改用匿名请求。物品和日历查询不需要 FFLogs 凭据。

`/ygl ff14 output ...` 当前不可用：FFLogs 统计页面返回 HTTP 403，且页面数据格式未通过验证，因此暂不展示分位数值。

这些是开发版实现，验收须按版本和入口区分。历史 Local Test 已验证物品、名称市场查询、独立管理四字段读写及普通聊天的市场工具链；模型最终回复仍有部分通过项。国服角色 Logs 有较早隔离宿主凭据与查询证据，不能替代新管理入口的真实验证。当前代码、离线检查、真实通过及待外部条件见[首版能力清单](docs/ff14-first-release.md)；日历内容、实际订阅投递及 QQ/NapCat 验收继续延期。

### FF14 插件页面

插件列表的 monitor 默认进入 `00-game-link` Vue 查询壳，导航来自本次运行的模块目录；FF14 自有物品与市场页。旧 `ff14` 显式入口保留公开角色 Logs、日历及旧链接；它的概览和设置继续只读。目录中的声明不自动授予网页调用或管理权限，公开页无法确认的凭据状态和来源新鲜度显示未知。刷新状态、主题和语言更新不自动提交业务查询；同模块切页保留已加载资产，真正生命周期失效后需从正式入口重新打开。

网页查询需要正常 Dashboard 登录和有效的 `web_public_origin`，只在手动提交时请求来源。“停止展示”使旧结果不再显示，不保证取消后台请求。本人订阅仍从本人私聊管理。普通配置与 FFLogs 凭据使用正式独立管理入口，不能从公开查询会话写入；AstrBot 模式无需再输入独立 Core 管理口令。SDK 升级须另行核对冷加载和清理条件，上传成功不能证明内存里的版本已更新。

### 配置归属与管理

从正式独立管理入口读取当前值与 revision，修改后按 CAS 保存；发生冲突先重读并复核，不覆盖并发修改。Core 默认区域与 FF14 三个日历字段分组管理。非法存量修复、限定回退和启动失败恢复沿用受限管理合同；不等于整库恢复或数据迁移验收。

| 配置项 | 默认值 | 用途 |
| --- | --- | --- |
| Core 默认区域 | `cn` | 游戏服务区域 `cn`/`global`，不是语言或时区；市场省略范围分别为国服全区、标准国际四区 |
| 日历默认查询天数 | `7` | 整数 1–30，用于省略 `days` 的新查询 |
| 日历默认时区 | `Asia/Shanghai` | 可解析的 IANA 时区，用于省略 `timezone` 的新查询和新订阅 |
| 新订阅每日摘要时间 | `08:00` | 严格的 24 小时制 `HH:MM`，用于显式新建订阅时省略 `time` 的情况 |
| 宿主网页访问地址（`web_public_origin`） | 空 | 仍由原生插件配置管理；填写浏览器实际访问的单一 http(s) origin，仅协议、主机和端口 |

显式参数优先；修改默认值只影响之后的新查询或新建订阅，不自动创建订阅或改写既有订阅。候选续接固定原查询上下文。原生表单中的旧 `ff14_*` 四字段仅保留迁移/回退兼容，迁移完成后不再生效；不要同时维护两套默认区域。普通配置不存储 FFLogs 秘密，也不开放订阅授权开关。

网页访问地址留空或格式无效时，仅关闭网页查询，聊天和后台功能保持原有规则；修改后保存、重载并重新打开页面。使用反向代理时填写浏览器实际访问的地址，不填写内部服务地址。

服务端校验日历范围、IANA 时区和严格 `HH:MM`，非法值不得标为生效。若旧配置使业务无法启动，使用受控管理读取及修复路径；不能靠重新打开公开查询或强制启用绕过。现有迁移需依其准备和恢复合同另行执行，不因配置页面可读写就宣称迁移通过。

### FFLogs 来源凭据

在正式独立管理页的 FF14 分组分别管理国服、国际服 Client ID 与 Client Secret。读取只显示配置状态和 revision，两个输入不会回填旧值；选择保留不发写入，更新须输入完整一对，清除须明确确认。凭据经 Core 加密保存，冲突后重读，不覆盖其他管理员修改。保存成功只表示“已保存、上游未验证”，不证明 FFLogs 认证或角色查询成功。缺少宿主外部管理的 `YGL_SECRET_KEY` 时拒绝保存，不能降级为明文。

不要在公开查询页、原生普通配置、聊天、URL 或日志中填写凭据。未配置、已配置但不可使用、认证拒绝、超时、限流与上游异常分别处理；国服与国际服不相互借用。新的表单和错误分类仍须另行真实验收，不以合成凭据测试证明上游可用。

### 独立或离线管理员维护（保留入口）

以下是独立认证的离线运维入口，不是 AstrBot 管理页的必需前置，也不能在运行中的实例上执行。它保留文件所有权、安全目录与独立管理员凭据校验。

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
