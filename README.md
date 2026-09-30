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

上述命令已纳入当前开发实现，但实时来源与目标宿主仍有未验收项：短路径宿主的安装/重启/help/status 已通过，长路径安装、物品/日历实时查询和实际定时推送仍待后续验证。原生插件配置表单与专用管理页面尚未实现；请勿把本地测试通过视为所有部署环境均可用。

### 管理员本地维护 FFLogs 凭据

先停止 AstrBot，并确认插件数据目录中已有 `runtime.sqlite3`。在安装包目录 `astrbot_plugin_yomihime_game_link/` 的父目录，用具备本地文件所有者权限的 POSIX 交互终端运行；不要从源码工作树或聊天命令执行：

```text
python -m astrbot_plugin_yomihime_game_link.scripts.admin_credentials --database <existing-data-dir>/runtime.sqlite3 bootstrap
python -m astrbot_plugin_yomihime_game_link.scripts.configure_source_credentials --data-dir <existing-data-dir> --realm cn set
python -m astrbot_plugin_yomihime_game_link.scripts.configure_source_credentials --data-dir <existing-data-dir> --realm global set
```

首次使用先 bootstrap 独立管理员凭据；凭据仅通过隐藏提示输入。FFLogs 客户端 ID 和密钥也使用隐藏提示，可按区域分别设置；清除区域凭据使用对应命令并将 `set` 改为 `clear`。AstrBot 与维护命令必须使用同一份外部管理的 `YGL_SECRET_KEY`（base64 编码的 32 字节密钥）；不要把它或任何凭据放在命令行参数中。工具要求数据库、脚本和父目录权限受限。维护进程本身不启动 Core，也不执行网络查询；完成配置后由管理员重新启动 AstrBot。Windows 当前无法验证所需 ACL，工具会失败关闭，不支持该维护操作。

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
