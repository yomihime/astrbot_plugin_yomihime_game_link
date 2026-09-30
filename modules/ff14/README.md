# FF14 模块

本模块通过 `/ygl ff14` 提供公开查询和本人日历订阅。

## 查询

| 命令 | 说明 |
| --- | --- |
| `/ygl ff14 status` | 查看功能状态。 |
| `/ygl ff14 item <名称或 ID>` | 查物品公开资料、多候选与获取途径。 |
| `/ygl ff14 logs <国服\|国际服> <服务器> <角色> [metric=rdps\|ndps\|cdps]` | 查公开角色战绩；重名服务器可用 `server_hint=...` 筛选。服务器或角色名有空格时用引号包起。|
| `/ygl ff14 calendar <国服\|国际服> [days=7] [timezone=Asia/Shanghai]` | 查从当前本地日期开始的日历活动，`days` 为 1–30。|

日历查询以本次获取的公开日历为准，会显示获取时间，不把获取时间称作发布者更新时间。若主源失败会尝试固定备用源；都不可用时会明确返回失败，不用空结果或陈旧缓存伪装新数据。

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

`/ygl ff14 logs <国服|国际服> <服务器名> <角色名> [server_hint=...] [metric=rdps|ndps|cdps]` 只查询公开角色数据，不访问私密战斗报告。可按区域单独配置 FFLogs OAuth 客户端凭据；管理员须使用宿主提供的加密凭据配置入口。未配置的区域会失败关闭，不会降级成匿名请求。目前国服角色查询已用实际公开 API 验证；国际服结果会标为待核对的参考结果。

`/ygl ff14 output <国服|国际服> <副本名称> <难度> <职业> [metric=rdps] [period=latest]` 目前暂不可用：FFLogs 统计页面返回 HTTP 403，且数据格式未通过验证，不显示分位数据。
