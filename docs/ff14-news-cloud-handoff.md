# FF14 国服新闻：云端开发至本地验证交接

> 本文保留 2026-11-06 首轮交接记录，不代表当前呈现方式。新闻现已改为正文简述与官网移动版截图；最新实现、依赖和待验收边界见[移动版新闻截图](ff14-news-mobile-screenshot.md)。

## 当前交付

- 仓库：`yomihime/astrbot_plugin_yomihime_game_link`。
- 默认分支：`master`；开工核实基线：`2d3e2cdb64a82ef3e4a1ac8cff5d0ac33a76d17c`（PR22 合并）。
- 本地工作分支：`codex/ff14-news-cloud-20261106`。最终变更提交、补丁及归档校验和见移交包 `manifest.json`、`SHA256SUMS`；本文不嵌入自身提交哈希。
- 保持 SDK `0.1.0a24`、模块 ABI `2.0`、bridge `1`、数据库 schema `210`；没有 SDK 契约或数据库迁移。没有新增运行依赖。
- 已实现配置目标、新闻管理员 allowlist、`news addhere`、完整快照/首次基线、三事件持久出队、同目标公开合并、同 ID 编辑不重发、本地卡片与文本同次发送。
- 本任务仅完成云端隔离验证；不推送、不建 PR、不合并、不部署、不读取生产配置/凭据、不发真实消息。后续这些动作仍需对应授权。

## 接收和应用

1. 通过接收端支持的 Library 文件流程，按已确认的文件 ID 和文件名下载移交 ZIP。不能直接访问或假定共享云端 `/workspace` 路径。
2. 对下载 ZIP 核对交接消息给出的 SHA-256，再解压到独立开发目录。核对内部 `SHA256SUMS`，确认 `manifest.json` 的基线与提交身份。
3. 在本地仓库的独立 worktree 或 clone 中取得上述公开基线；保留当前未提交修改，勿在生产插件目录直接应用。
4. 优先保留原提交身份：先 `git bundle verify /下载目录/ff14-news.bundle`，再 `git fetch /下载目录/ff14-news.bundle codex/ff14-news-cloud-20261106:codex/ff14-news-local-verify` 并切换到该新分支。bundle 的 prerequisite 是同一基线；不强制覆盖已存在分支。
5. 补丁替代路径：`git switch -c codex/ff14-news-local-verify 2d3e2cdb64a82ef3e4a1ac8cff5d0ac33a76d17c`，随后 `git am --keep-cr --3way /下载目录/ff14-news-cloud.patch`。冲突时停止并检查，不覆盖生产文件。仓库含 CRLF blob，`--keep-cr` 用于保留补丁内容；git am 的 committer/时间可能不同，因此提交哈希可变化；此路径核对 manifest 的 tree SHA、逐文件 SHA-256 和变更清单。
6. bundle 导入时 `git rev-parse HEAD` 应与 manifest 的 commit 相同；两种路径均用 `git rev-parse HEAD^{tree}` 和 `git diff 基线..HEAD --stat` 核对树与文件清单。

## 实现位置与必要 Core 改动

| 路径 | 职责 |
| --- | --- |
| `modules/ff14/features/news.py` | 固定官网采集、保守正文解析、展示卡、纯 evaluator、公开 composer、addhere |
| `modules/ff14/module.py`、manifest、assembly | 用真实 SDK 注册能力、订阅、共享采集、来源和配置 |
| `services/configured_subscriptions.py` | Host 私有配置协调器；只接受受审装配和现有管理配置，复用订阅 lifecycle/CAS、job 与路由仓库 |
| `services/core_runtime.py`、`adapters/astrbot/runtime.py` | 装配协调器；复用当前主题、可信徽标和共享 painter；失败通过现有 pump_failures 暴露 |
| `services/delivery.py`、`adapters/astrbot/message_port.py` | 公开合并公告在最终发送检查前渲染；文本和单 PNG 同一消息链；UNKNOWN 不二次发送 |
| FF14 settings controller、page content、四语言目录 | 输入与校验目标/管理员数组、同步周期；失效或待保存页面先拒绝写入再解析草稿 |
| `scripts/build_dashboard_zip.py` | 把新源码纳入原有受审源码白名单，未放宽为任意文件 |

原持久订阅发送通道明确只允许文本，因此只给模块添加卡片不能交付图片；上述 Core/Host 改动为真实链路所需。未在模块创建独立定时循环、数据库或消息队列。

## 配置与权限

配置入口为现有登录 Dashboard 下 GameLink → FF14 → 业务配置 → 国服新闻订阅。普通管理 API 的身份、目标 owner、revision/CAS、来源与模块生命周期检查保持。数组输入以逗号分隔，最多 20 项，后端拒绝重复、无效类型、路径分隔符和控制字符。

| 字段 | 示例（合成） | 默认 |
| --- | --- | --- |
| `ff14_news_admins` | `["qq-bot-instance:10001"]` | `[]`，无人可 addhere |
| `ff14_news_targets` | `["qq-bot-instance:GroupMessage:20001", "qq-bot-instance:FriendMessage:10001"]` | `[]` |
| `ff14_news_interval_seconds` | `900` 或 `3600` | `900` |

AstrBot 的 actor_id 已是 `平台实例ID:账户ID`，代码与其精确比较；不得再次拼接平台，也不得仅比较数字账户 ID。平台实例 ID 来自真实 Host 的 `get_platform_id`，不是猜测的 `qq` 或适配器类型名称。

命令：`/ygl ff14 news addhere`，无参数。仅真实根 COMMAND 及 allowlist 中的 actor 可调用；未注册为模型工具或网页写能力。Core 的 SubscriptionOperations 再验证真实 ContextIssuer、模块与 admission，解析当前完整 ConversationRef；不接受命令输入任意 UMO。相同 owner、完整路由、类型和 creation_key 原子复用活动订阅；不同 owner 在相同目标订阅时仍由公开事件合并去重。

删除配置目标只取消配置协调器的对应行；addhere 行不会被顺带取消。总开关 `ff14_subscriptions_enabled` 同时控制日历和新闻。更改 allowlist 不会删除已有订阅。

## 去重、积压与失败语义

- 官网 ID 是事件身份，固定 event version=1；不使用最大 ID、发布时间、SortIndex、订阅 ID 或编辑版本作为新闻业务去重键。
- Core 现行公开事件 business identity 包含模块、完整 `ConversationRef` 与事件 key；覆盖 adapter、会话类型、会话 ID、delivery_route。数据库回归覆盖同数字 ID 的不同完整目的地和同目标两个 owner 的合并。
- 首个完整快照对每个新订阅建立基线，历史不推送。FAILED/PARTIAL 不建立或推进基线。
- 每轮最多 3 个 EvaluationEvent，其余新闻正文保存在 pending 状态；观察、状态与 outbox 由 Core 同一事务提交。已出队不等于已送达。
- UNKNOWN、超时和崩溃后不确定发送继续使用 Core 原状态机，不自动发文本重试，也不宣称 exactly once 或平台送达确认。SENT 仅沿用 Host 返回 accepted 的语义。
- 列表完整扫描最多 32 页/1600 条；待处理最多 1600 条、seen 最多 5000 ID。超限拒绝推进，不截断。此处为明确的维护界限，持续达到上限须另行评审容量策略。
- 既有 Core 路由表以 adapter+conversation_id 保存单一 kind；同一平台实例内同数字 ID 同时用于群组和私聊时会拒绝冲突，不覆盖、误投或合并两种路由。要支持这一碰撞需要单独评审 Core 身份/绑定存储方案，云端未隐式改 schema。
- 若官网在两次成功扫描之间发布后移除新闻，本方案不能保证发现；不声称官网服务稳定或投递端端到端保证。

## 来源核验与内容安全

固定来源：

- 列表：`https://cqnews.web.sdo.com/api/news/newsList`；`gameCode=ff`，分类 `8324,8325,8326,8327,5309,5310,5311,5312,5313`，请求 pageSize=50，pageIndex 从 0 开始。
- 必要详情：`https://cqnews.web.sdo.com/api/news/newsDetail?gameCode=ff&id=<数字ID>`。
- 用户链接：`https://ff.web.sdo.com/web8/index.html#/newstab/newscont/<数字ID>`。

2026-11-06 实际响应观察：请求 pageSize=100 只返回 50 条；列表含 Data/Code/PageCount/TotalCount/RecordCount，按 SortIndex 排序。只读完整采集最终成功 658 条、14 页、约 41 秒，其中 14 条空标题、35 条空摘要。之前完整探测暴露了空标题校验和网络超时，均记录为失败，未假装成功。最终仅保留计数和错误类别，不导出完整官网文章正文。详情单条结构也实读核实。

首次基线仅收集列表与 ID，不逐条请求历史详情；之后仅为新发现且缺标题/摘要的 ID 拉详情。失败保留前次 known_ids，防止失败后误当历史而跳过详情。数据不完整、计数变化、ID 重复、页数超限或首屏复检变化时不推进状态。列表页最多两个并行请求，单次 HTTP 15 秒，整轮来源阶段 80 秒，受信 Core collector 执行上限 90 秒；失败退避和来源配额复用 Core（默认失败退避约 120 秒，来源上限 30 请求/分钟）。

此端点是官网当前公开 JSON 页面来源，未确认有公开开发者 API 服务承诺或抓取授权；不规避访问限制。未知分类显示官网编号，不猜分类名称。官网时间是无偏移墙钟字段，按国服 Asia/Shanghai 明确标注；解析失败显示未知，不用采集时间冒充发布时间。HTML 转为字面文本；script/style/iframe/object 丢弃，不使用 LLM 摘要、不加载 OutLink、HomeImagePath 或正文远程图片。只渲染受控新闻展示卡。

## 云端验证记录

最终命令、通过/失败/跳过汇总及环境版本详见移交包 `validation.json` 与原始日志。开发过程中旧的精确字段/能力/预算断言与源码白名单曾失败，修正后针对性重跑；不得把中间失败隐藏为全量通过。

已建立的专门回归覆盖：完整目标键、重叠 owner、同 ID 编辑、非顺序/置顶分页、首次基线、FAILED/PARTIAL、详情失败与重试、不完整/重复分页、超限、每轮三事件且 pending 持续出队、SQLite 公开合并、UNKNOWN 不重发、addhere 权限与真实 Core 幂等、配置增删、进程级 Core reopen、管理配置权限/CAS、文本和 PNG 同链。合成卡片 `news-card-synthetic.png` 已实际打开检查，不能作为真实 IM 截图或真实新闻内容。

隔离重现（Linux；Windows 按 CONTRIBUTING 的明确 Python 路径调整）：

```sh
python3.13 -m venv .news-test
. .news-test/bin/activate
python -m pip install setuptools==80.9.0 wheel==0.45.1 ruff==0.12.0
python -m pip install -r requirements.txt -r requirements-test.txt
npm ci --prefix pages/frontend
npm run typecheck --prefix pages/frontend
npm run build --prefix pages/frontend
python scripts/prepare_test_font.py
python scripts/verify_frontend_build.py
# 按 CONTRIBUTING 准备独立 AstrBot 4.29.1 源码；不可指向用户实例。
export YGL_TEST_ASTRBOT_SOURCE=/独立源码/AstrBot/astrbot
SOURCE_DATE_EPOCH=315532800 python -m pip wheel --no-index --no-deps --no-build-isolation --no-cache-dir --wheel-dir dist .
export YGL_TEST_SDK_WHEEL="$PWD/dist/yomihime_game_link_sdk-0.1.0a24-py3-none-any.whl"
python -m unittest -v tests.modules.ff14.test_news tests.host.test_ff14_news
python -m unittest discover -s tests -t . -v
ruff check .
ruff format --check .
YGL_TEST_PYTHON="$PWD/.news-test/bin/python" npm test --prefix pages/frontend
node --test tests/pages/ff14/*.test.mjs
python scripts/check_doc_links.py
python scripts/build_release.py --sdk-wheel "$YGL_TEST_SDK_WHEEL"
```

SDK pin：`a0b2f61152e810b3a26febd715487d60f335108dffafba0a41d44db5208fdbb6`。Host 固定源码为 AstrBot v4.29.1 / `dffe922773725e9c178392c423676ad65044748c`，其 AST/DTO 合同不是启动真实 Host。Python 使用 3.13.9；依赖实际解析版本由 `dependencies.txt` 固定记录，项目声明本身未改成新锁文件。

## 本地接续与批准边界

先完成代码审查、隔离回归及包构建，再进入本地验证。若真实实例测试没有授权，使用独立 Host 和模拟消息端口；不要发送任何真实群/私聊消息，不复制生产配置或凭据到云端。

真实 Host 待核验：

1. 按部署方式在独立目录安装构建出的候选 ZIP，核对实际 Core/SDK/FF14 摘要与加载日志；本变更不改变 SDK 身份，无 SDK 强制冷升级要求，但回滚时停机更易核验。
2. 登录页面验证新闻配置可读写、四语言文案、CAS 冲突、失效页面、取消等待后不重发，确认 allowlist 默认无人且普通用户 addhere 不产生订阅。
3. 从目标平台证明真实 `get_platform_id`、账户/群会话 ID 与 FriendMessage/GroupMessage 的对应关系；不用猜测的 UMO。特别检查前述同 ID 异 kind 的既有 Core 限制。
4. 在本地网络检查低频官网列表/详情及完整性；初始化应无历史刷屏。用隔离 fixture 注入一篇新 ID、重叠订阅、同 ID 编辑和七篇积压，核对基线、三篇分轮、重启恢复及 UNKNOWN 保留。
5. 核对本地字体、主题、可信徽标及实际发送链中的文本和 PNG。真实收件人平台的送达测试、生产安装、push、PR 和合并均需要对应的后续明确授权，云端未执行。

预计部署：授权后按仓库正常插件 ZIP 更新流程，在同一实例记录候选包摘要、配置 revision 和回滚包；不直接装未经构建的源码。只使用用户明确选定的测试收件人。

回滚：停止同一 Host，按其受支持方式恢复先前受信插件包，再启动并核对版本。没有数据库迁移，保留数据库、secrets、keys、selector、去重及 outbox 状态；不要删除订阅或数据库以“修复”重复，不为回滚重填密钥。旧版可能不执行新新闻类型，恢复新候选后沿用现有状态。
