# 如月怜的游戏连结

面向 AstrBot 的游戏信息插件，当前仍处于开发阶段。插件介绍、安装包和开发说明也可查看[仓库](https://github.com/yomihime/astrbot_plugin_yomihime_game_link)。

> Windows 安装限制：当前托管密钥保护会检查整个插件数据根目录的 READ/LIST 权限，继承的 `Users` 等非受信主体读取 ACE 也可能阻断初始化。普通继承 ACL 环境尚未证明开箱即用；Windows 本地证据与 Ubuntu CI 分开，详见下方[Windows 资格](#windows-原生文件系统资格)。

## FF14 功能

- `/ygl help`：当前模块目录；`/ygl ff14 help`：FF14 能力目录；具体指令后加 `help` 查看参数。
- `/ygl ff14 status`：查看当前功能状态和上游限制。
- `/ygl ff14 item <物品名称或 ID>`：查询公开物品资料与已验证的获取途径。
- `/ygl ff14 market <物品名称或 ID> [region=cn|global] [server=服务器|dc=大区] [quality=all|nq|hq] [intent=overview|min|listings]`：手动查询 Universalis 行情。省略范围时使用 Core 默认区域全区；名称有歧义时选择候选后保留原范围、品质和意图。最低价仅指本次返回数据，挂牌明细最多 5 条样本，不保证全部服务器覆盖或实时可买。
- `/ygl ff14 logs <国服|国际服> <服务器名> <角色名> [metric=rdps|ndps|cdps]`：查询公开角色战绩。服务器名或角色名含空格时可用双引号包起；也可用 `server_hint=...` 缩小重名服务器结果。
- `/ygl ff14 calendar <国服|国际服> [days=7] [timezone=Asia/Shanghai]`：查询从当前本地日期开始的日历活动，`days` 范围为 1 到 30。
- `/ygl ff14 calendar subscribe <国服|国际服> [timezone=Asia/Shanghai] [time=08:00]`：主动订阅每日活动摘要，默认每天本地 08:00。首次完整采集在设定时刻或之后完成时，当天不发送，从次日开始发送。实际发送受同步结果与任务调度影响，不承诺精确准点。
- `/ygl ff14 calendar subscriptions [page=1]`：查看本人订阅，每页最多 10 条；日历订阅管理需从本人私聊发出。
- `/ygl ff14 calendar update <subscription-id> <expected-revision> [timezone=IANA] [time=HH:MM]`：按当前 revision 修改本人订阅。
- `/ygl ff14 calendar cancel <subscription-id> <expected-revision>`：按当前 revision 取消本人订阅。

FFLogs 角色战绩查询需要按区域配置 OAuth 客户端凭据，维护步骤和平台限制见下节。国服和国际服凭据分别设置；未配置的区域会明确拒绝查询，不会改用匿名请求。物品和日历查询不需要 FFLogs 凭据。

普通聊天可查询物品、市场和公开角色 Logs。模型结合同一用户在当前会话中的相关前文理解简称、追问和选择，模块校验参数、可信目录与真实用户事件；工具返回的内容不会成为新的用户指令。物品名称或 ID 可来自相关前文。候选支持明确名称、序号或候选中实际存在的区别；含糊、否定、多个选择和询问区别会继续澄清，候选条件保持不变。

市场服务器或大区重名时会先给可信目录候选，再按用户的明确选择查询；物品、市场范围和 Logs 服务器使用各自的候选批次与 ID，不可混用。续接保存原物品、品质、价格意图和配置快照，原始或确认消息不能再次消费同一选择。

Logs 可用 `查 FFLogs 国际服 Cerberus Synthetic Hero`、`Synthetic Hero@Cerberus` 等自然表达，含空格的角色名无需特殊聊天引号。简称由模型解释为标准服务器名，再由 FFLogs 目录验证。缺参时只需补充缺少的字段；例如先给角色名，再补服务器或国服。相关前文只由现有模型对话上下文理解，模块仅保留五分钟内的有界待补参数和候选；更正条件会替换旧批次。重名服务器需要明确一个候选或其区域/数据中心。市场与 Logs 的聊天默认范围依次考虑用户明确区域、可信服务器归属、当前查询表达的简繁中文或其他语言、最近相关用户请求语言，最后采用 Core 默认；仅有名称、数字或不明确混合语言不会由名称推断范围，明确区域与服务器冲突时会澄清。命令与网页仍采用 Core 默认。

Logs 的 `metric=rdps|ndps|cdps` 省略时使用 rDPS；`zone_id=N difficulty=N partition=N` 必须一起提供。聊天最多返回 20 项，缺失值与来源更新时间保留未知；角色排名不表示全体输出分位或战斗分析。模型接收结构化来源事实并按既有人格组织回答。有限市场样本不能证明全服绝对最低，World 上传、来源获取及挂牌创建时间分别处理，未知不补零。“已配置但凭据不可用”时，具体原因未通过角色查询接口公开，请管理员从配置管理核对加密与来源状态。

FFLogs 输出统计尚未实现，当前不提供；旧 `output` 指令不再注册。

这些是开发版实现，离线检查、真实宿主启动与真实业务服务验收分别记录。当前入口与验证边界见[首版能力清单](docs/ff14-first-release.md)；实际 FFLogs、日历来源、订阅投递及 QQ/NapCat 仍需本地验证。

### FF14 插件页面

在 AstrBot 插件列表点击“打开视图”（显示器图标），默认进入 `00-game-link` 查询壳。导航来自本次运行的模块目录；FF14 提供物品、市场、公开角色 Logs 和日历页。退役的 `ff14` 定位页已删除。目录声明不自动授予网页调用或管理权限，无法确认的凭据状态和来源新鲜度显示未知。刷新状态、主题和语言不自动提交业务查询；同模块切页保留已加载资产，生命周期失效后须从宿主正式入口重新打开。

网页查询需要正常 Dashboard 登录和有效的 `web_public_origin`，只在手动提交时请求来源。“停止展示”使旧结果不再显示，不保证取消后台请求。本人订阅仍从本人私聊管理。普通配置与 FFLogs 凭据使用正式独立管理入口，不能从公开查询会话写入；AstrBot 模式无需再输入独立 Core 管理口令。SDK 升级须另行核对冷加载和清理条件，上传成功不能证明内存里的版本已更新。

### 配置归属与管理

从正式独立管理入口读取当前值与 revision，修改后按 CAS 保存；发生冲突先重读并复核，不覆盖并发修改。Core 默认区域、图片主题与 FF14 日历设置分组管理。无效当前配置可在已登录的设置页修正，再在配置维护重新校验恢复。保存保留授权和 revision/CAS 检查，不会静默重置数据。

| 配置项 | 默认值 | 用途 |
| --- | --- | --- |
| Core 默认区域 | `cn` | 游戏服务区域 `cn`/`global`，不是语言或时区；市场省略范围分别为国服全区、标准国际四区 |
| 日历默认查询天数 | `7` | 整数 1–30，用于省略 `days` 的新查询 |
| 日历默认时区 | `Asia/Shanghai` | 可解析的 IANA 时区，用于省略 `timezone` 的新查询和新订阅 |
| 新订阅每日摘要时间 | `08:00` | 严格的 24 小时制 `HH:MM`，用于显式新建订阅时省略 `time` 的情况 |
| 日历后台同步周期 | `21600` 秒 | 可选 6、12、24 小时；控制新同步调度 |
| 图片主题 | `warm_paper` | Core 默认主题；模块可选择跟随 Core 或独立覆盖 |
| FF14 订阅总开关 | 开启 | 仍须用户显式订阅；关闭不删除订阅，恢复不补发暂停窗口 |
| 宿主网页访问地址（`web_public_origin`） | 空 | 仍由原生插件配置管理；填写浏览器实际访问的单一 http(s) origin，仅协议、主机和端口 |

显式参数优先；修改默认值只影响之后的新查询或新建订阅，不自动创建订阅或改写既有订阅。候选续接固定原查询上下文。本 alpha 以全新安装为目标；普通设置只由插件设置页维护，不读取旧 Host 配置或迁移准备文件。旧 Host 的默认区域、日历天数、时区和每日摘要时间四字段未迁移，旧用户须在插件设置页重新配置，无需删除用户数据。普通配置不存储 FFLogs 秘密；模块总开关不能替代用户的显式订阅授权。

网页访问地址留空或格式无效时，仅关闭网页查询，聊天和后台功能保持原有规则；修改后保存、重载并重新打开页面。使用反向代理时填写浏览器实际访问的地址，不填写内部服务地址。

服务端校验日历范围、IANA 时区和严格 `HH:MM`，非法值不得标为生效。若当前配置无效，使用受控管理读取及修复路径；不能靠重新打开公开查询或强制启用绕过。不提供旧版本升级迁移，加载时也不会删除旧数据。

### FFLogs 来源凭据

在正式独立管理页的 FF14 分组分别管理国服、国际服 Client ID 与 Client Secret。首次安装且所有加密库存确认为空时，Core 会在受限插件数据目录持久建立托管主密钥；“凭据存储”面板可查看状态、重试初始化和验证恢复原密钥。已有加密数据、未知文件或来源冲突不会自动换钥；全域重置须明确确认，会使所有模块的加密配置和授权凭据失效，保留普通配置与授权记录。高级部署也可在 AstrBot 进程环境中显式提供 `YGL_SECRET_KEY`，包括空值或错误值都不会回退到托管密钥。读取只显示配置状态和 revision，两个输入不会回填旧值；选择保留不发写入，更新须输入完整一对，清除须明确确认。凭据经 Core 加密保存，冲突后重读，不覆盖其他管理员修改。保存成功只表示“已保存、上游未验证”，不证明 FFLogs 认证或角色查询成功。加密状态未就绪时拒绝保存，不能降级为明文。

不要在公开查询页、原生普通配置、聊天、URL 或日志中填写凭据。未配置、已配置但不可使用、认证拒绝、超时、限流与上游异常分别处理；国服与国际服不相互借用。新的表单和错误分类仍须另行真实验收，不以合成凭据测试证明上游可用。

### 独立或离线管理员维护（保留入口）

以下是独立认证的离线运维入口，不是 AstrBot 管理页的必需前置，也不能在运行中的实例上执行。它保留文件所有权、安全目录与独立管理员凭据校验。

先停止 AstrBot，并确认插件数据目录中已有 `runtime.sqlite3`。在安装包目录 `astrbot_plugin_yomihime_game_link/` 的父目录，用具备本地文件所有者权限的交互终端运行；`--data-dir` 须为绝对路径且不得含 `..` 路径分量，平台验证边界见下文。不要从源码工作树或聊天命令执行：

```text
python -m astrbot_plugin_yomihime_game_link.scripts.admin_credentials --database <existing-data-dir>/runtime.sqlite3 bootstrap
python -m astrbot_plugin_yomihime_game_link.scripts.configure_source_credentials --data-dir <existing-data-dir> --realm cn set
python -m astrbot_plugin_yomihime_game_link.scripts.configure_source_credentials --data-dir <existing-data-dir> --realm global set
```

首次使用先 bootstrap 独立管理员凭据；凭据仅通过隐藏提示输入。FFLogs 客户端 ID 和密钥也使用隐藏提示，可按区域分别设置；清除区域凭据使用对应命令并将 `set` 改为 `clear`。维护命令使用同一插件数据目录中的持久主密钥选择器；托管来源无需另设环境变量，高级环境来源必须在 AstrBot 与维护命令中显式提供同一份 `YGL_SECRET_KEY`（base64 编码的 32 字节密钥）。缺失、错误或冲突的环境来源不会自动切换。不要把密钥或凭据放在命令行参数中。工具要求数据库、脚本和父目录权限受限。维护进程本身不启动业务 Core，也不执行网络查询；完成配置后由管理员重新启动 AstrBot。

POSIX 入口保留交互终端和文件所有者检查；Windows 仅接受已资格的运行环境与受限 NTFS 目录。身份、权限或文件状态无法确认时拒绝执行。操作者须先停止 AstrBot，工具不负责停止宿主；本次云验证没有在用户 Windows 或生产凭据上操作。环境范围见下方资格说明及[贡献指南](https://github.com/yomihime/astrbot_plugin_yomihime_game_link/blob/master/CONTRIBUTING.md)。

## 安装

请安装 Actions 构建出的插件 ZIP：

1. 打开[构建工作流](https://github.com/yomihime/astrbot_plugin_yomihime_game_link/actions/workflows/build.yml)，选择通过验证且构建成功的运行记录。
2. 下载该记录中的 `release-artifacts-<run-id>` 工件，并解开下载得到的外层压缩包。
3. 在解开的文件中找到 `astrbot_plugin_yomihime_game_link-v0.1.2.zip`，通过 AstrBot WebUI 的插件管理上传这个内层 `ZIP`。

不要把外层 Actions 工件 `ZIP`、独立的 SDK `.whl` 或 `SHA256SUMS` 上传为插件。项目仓库直装尚未包含 release 构建时从已验证 SDK wheel 注入的运行资源，不能作为开箱即用的安装方式。

当前 `metadata.yaml` 声明的版本是 `v0.1.2`，尚无对应 tag 或 GitHub Release。Actions 工件属于开发构建，不要把它当作正式发布。



## 开发

开发环境、构建命令和验证范围见[贡献指南](https://github.com/yomihime/astrbot_plugin_yomihime_game_link/blob/master/CONTRIBUTING.md)。项目更新记录见[CHANGELOG](https://github.com/yomihime/astrbot_plugin_yomihime_game_link/blob/master/CHANGELOG.md)。本地构建会生成插件 ZIP、独立 SDK wheel 和 SHA-256 校验文件。发布插件到 AstrBot 市场时，请以[AstrBot 插件发布说明](https://docs.astrbot.app/dev/star/plugin-publish.html)为准。

Core、Host、SDK、FF14 的职责与模块入口见[模块维护速查](https://github.com/yomihime/astrbot_plugin_yomihime_game_link/blob/master/docs/module-maintainer-guide.md)；首版能力、离线检查和待真实验收范围见[FF14 首版能力表](https://github.com/yomihime/astrbot_plugin_yomihime_game_link/blob/master/docs/ff14-first-release.md)。当前文档及构建完成不代表新版本已经部署、真实 FFLogs 或全部平台验收通过。

## 来源与许可

插件骨架由 AstrBot 的[helloworld 插件模板](https://github.com/Soulter/helloworld)初始化。仓库许可证见[LICENSE](https://github.com/yomihime/astrbot_plugin_yomihime_game_link/blob/master/LICENSE)（AGPL-3.0）；独立 SDK 及随包示例使用 [MIT](SDK-LICENSE)，两者分开授权，第三方组件保留各自许可。

## 当前安装边界

最低宿主版本通过官方 `astrbot_version: ">=4.29.1"` 元数据声明，当前以 AstrBot 4.29.1 的正式插件页面接口验证。只安装构建生成的 ZIP；源码 checkout 缺少构建页面和已校验 SDK 载荷，不能直接加载。外部运行依赖声明最低版本，构建工具和 SDK 完整性 pin 单独固定。

全新空库存会在受限插件数据目录建立持久托管主密钥，普通用户可从设置页配置 FFLogs；设置页“凭据存储”只显示安全状态，不显示密钥。备份和迁移须连同一致的 `runtime.sqlite3`、`secrets` 与私有 `keys` 目录保存，不能只复制数据库。已有密文若缺少原密钥，须先从“凭据存储”面板验证恢复，不能自动生成新钥覆盖。高级环境来源 `YGL_SECRET_KEY` 需要标准 Base64 编码的 32 字节 AES 密钥；显式提供空值、坏值或与已选托管密钥冲突时保持阻断。普通设置、帮助和无需凭据的功能可继续使用。不要把密钥写入聊天或日志。

### Windows 原生文件系统资格

宿主 4.29.1 兼容与 Windows 原生底层资格分别验证。当前限定 Windows 10.0.26200/10.0.26300、x64 CPython（GIL）3.13.9、3.12.10 或 3.12.14；数据/插件目录须为本地 NTFS 的盘符绝对路径。UNC、junction/reparse 和硬链接拒绝。未资格环境会显示 `unsupported_environment`，不会降低句柄身份与竞态保护。其它 Windows/Python 组合需要对应原生安全测试，Linux 云回归不能代替实机资格。

当前托管密钥实现拒绝非受信主体对整个插件数据根目录的 READ/LIST 访问，并非只保护 `keys` 子目录。继承的 `Users` 等读取 ACE 可能使符合 OS/Python 资格的安装仍无法初始化。已有 Windows 本地验证使用受限私有根，不证明普通继承 ACL 环境开箱即用；Ubuntu CI 成功也不证明这一点。遇到阻断，先查看设置页凭据存储状态及安全错误码；确有授权时由操作者检查该根目录 ACL，或请系统管理员协助。不要通过递归放宽权限、删除数据或关闭安全检查排障。
