# 模块注册与页面首批合同（R0）

基线：`2cfa88f655cd158f6e5249baeda3deb7d6ce5e19`。本合同是 R0/R1/R2 与满足前置条件后的 R3 实施依据。首次范围是受信任的项目模块，不是第三方代码安全沙箱。旧 FF14 页面保留兼容入口；包热替换、物理拆库、真实数据迁移另行验收。

## 归属与版本

| 对象 | 权威归属 | 版本与撤销边界 |
| --- | --- | --- |
| 业务 capability、command、tool、页面、专属配置与语义校验 | 模块的 SDK 清单及实现 | 稳定的 `package_id/module_id`；注册与启用需经过现有 Registry、Lifecycle 和 Admission |
| Core 默认 `cn/global`、通用授权、配置存取、结果与资源路由 | Core | 配置 revision/CAS 不等于运行代际，也不等于资产摘要 |
| 命令消息、正式 Dashboard 管理证明、网页请求 | AstrBot Adapter | 使用宿主正式身份链；客户端 role、asset token、普通 API key 不产生管理权 |
| 导航、主题、公共 UI、模块容器与受限服务上下文 | 页面壳 | 壳契约与共享运行时版本；每次模块挂载绑定 owner 和运行代际 |
| 页面 JS/CSS 与只读资源 | 所属模块；构建器验证后发布 | 清单中的 SHA256/资产版本控制字节，不证明授权；安装、资源服务与包清单一致 |
| 配置、缓存、密钥、业务记录与可写路径 | Core 按稳定模块 ID 路由 | 保留共享 SQLite 权威和既有事务；停用/卸载不删除或搬迁持久记录 |

运行 identity 使用现有 Core runtime ID 与 module epoch；页面请求序号仅用于迟到结果失效。三者不能与配置 revision、目录 revision、资源 SHA256 混用。可信描述符只允许受校验的相对本地资源，不接受任意 URL、路径或脚本地址。

## 命令与页面

`/ygl [模块路由] [命令及参数]` 保持兼容。命令明确声明 `structured` 或 `raw_tail`；后者仍映射到封闭 capability schema 的字符串字段，经权限、admission、结果合同执行，不产生无约束 `module.handle`。FF14 市场解析、候选上下文和帮助归 FF14，Host 不解释物品、服务器或市场语义。显式命令不调用 LLM。

页面声明归模块。壳只从可信目录生成导航，不写 FF14 路由分支。模块提供小型 `mount/update/dispose` 等价合同，接收受限能力调用、只读上下文和资源 scope；不得把公开页改为管理写入口。Vue/Naive UI 共享运行时与独立模块构建须用正式静态包验证。宿主只允许页面目录内资源，复制到宿主资源目录是构建投影，源码及声明仍归模块，复制需清单/摘要校验。

重复 context、主题和语言只更新展示；真实绑定、授权或生命周期变化使查询、候选与延迟 import 失效。相同页面名称不证明身份。状态读取失败保留明确的旧快照与手动恢复入口，不销毁表单、焦点、选区或滚动位置。后端继续逐请求鉴权。

卸载先停止准入并撤销旧代际，再停止任务、监听、订阅与路由，释放模块 DOM、CSS 和浮层。清理失败保留 `cleanup_pending`、所有权和重试路径，阻止重新启用及新的写入实例；隐藏入口不等于卸载完成。ESM 字节缓存可保留，但不得残留已注册副作用。

## 配置与后续入口

四字段迁移清单只描述既有迁移/回退范围，不是通用配置目录。通用目录由 Core 与模块声明投影，注册字段不自动获得写权限。本批普通管理仍限定已经授权的四字段，复用正式管理证明、语义校验、CAS、修复及限定回退；敏感凭据、FFLogs 编辑和订阅开关不开放。模块校验器不可用时阻塞相关修改，不将未校验值标为生效。

R5 自然聊天由宿主正式工具系统进入 `Core.invoke_tool`，不能让模型提供身份。当前三入口暴露冲突需单独兼容合同与负向测试；本批只调查及隔离试验，不启用真实 LLM。

### R3 最小目录与表单合同

满足 R1/R2 无重要阻塞后，管理页单独读取可信配置声明目录，不把四字段迁移清单当成所有模块的配置目录。目录仅投影 Core 与受信任模块的普通字段声明（owner、组、说明、类型、默认值与约束），不返回密钥值或秘密元数据。配置值与 revision 继续经现有管理读取接口获得；目录版本不参与配置 CAS。

目录中的“可写”由本次管理授权范围与服务端校验能力共同决定，不由字段注册决定。本次只开放 Core 默认区域和 FF14 的三个日历默认字段。测试第二模块的普通字段可以出现在目录与表单，但不会因此取得读取其存量值或修改它的权限。客户端仅负责基本类型转换和展示，服务端仍是 schema、模块语义校验、授权与 revision 的权威。

FF14 的时区和投递时间仍由 FF14 校验。可信装配必须明确所需校验器；缺少校验器时目录说明不可修改，服务端也拒绝对应写入和恢复，不把缺少校验器当成“只需类型校验”。Core 只识别通用字段及受信任校验绑定，不导入 FF14 的服务器目录或业务校验规则。

表单按可信声明生成，不在根 UI 维护 FF14 字段标签或分支；保留非法存量修复、显式值清除、CAS 冲突、限定回退与启动失败恢复。公开查询壳不调用管理写入。迁移/回退请求范围与既有四字段合同保持不变；完整管理后台、秘密编辑与订阅授权配置不进入本批。

最小接口是 `POST admin/catalog {}`，沿用 `READ_CONFIG` 的正式宿主管理证明、原路径转换和请求生命周期；原 `admin/read/update/rollback/recover` 数据合同不变。目录为闭合的 `schema_version: 1` 与 `fields` 数组，每项含 `module_id, name, description, group, value_schema, default, required, readable, editable, blocked_reason`。`blocked_reason` 仅为 `null`、`not_granted` 或 `semantic_validator_unavailable`。`editable` 描述服务可提供的范围及校验能力，不能作为当前读取请求的写授权；写入仍单独鉴权。

管理表单先读声明、再读已授权值。未开放字段可以显示声明和不可修改原因，不能显示其存量值或发出修改请求。本批渐进复用独立管理页的现有 DOM/bridge，不为该表单另打包一份共享 UI 运行时；公开壳及 FF14 页面使用 Vue/Naive UI。以后管理 UI 的技术栈统一是独立任务，不影响本次配置合同。

2026-10-06 首批收敛曾因 R2 跳正文及主题对比缺口而未通过前置门槛。随后独立授权的 R1-INTEGRATION＋R2-GATE 两轮修复已在同冻结正式资源、隔离 Chromium 与独审中关闭这些问题，R3 既有目录后端和独立 management DOM 兼容检查通过。该结果只关闭本地集成门槛；统一 Vue 管理表单、真实新 SDK/Host 加载、管理及迁移演练仍未验证，整包继续称待验收候选。

## 当前耦合映射

| 当前位置 | 本批目标 | 仍保留或后续处理 |
| --- | --- | --- |
| `adapters/astrbot/command_bridge.py` descriptor 参数模式 | R1 已接线 structured/raw_tail | 通用封闭 schema、operation_path、统一输出保留 |
| `adapters/astrbot/runtime.py` 通用帮助投影 | R1 已恢复 Core 默认区域与非法配置提示 | 内置 bundle、数据源/凭据、迁移装配绑定不冒充全部解耦 |
| `pages/ff14` 业务页面与根路由数组 | R2：通用壳 + `modules/ff14/pages` | 旧页兼容；Logs/日历等逐页迁移，不改上游 |
| `scripts/build_dashboard_zip.py` 与 `adapters/astrbot/bundled.py` Python-only 清单 | R1/R2：声明、摘要、复制、包与资源一致 | 不凭移动目录宣称可安装 |
| 四字段迁移目录及 management 硬编码标签 | 条件 R3：通用配置目录/表单 | 原迁移、授权、四字段写范围和回退合同保留 |
| SQLite `module_id` / `ConfigTarget` | R0/R1：归属映射及受限路径 | 不按猜测缓存键搬迁，不拆共享事务，不迁移真实数据 |

依赖依据：[Vue 官方工具链](https://vuejs.org/guide/scaling-up/tooling)、[Vite 库构建与 external 合同](https://vite.dev/guide/build.html#library-mode)。实现需锁定实际依赖并保留许可证；运行时不从 CDN 下载依赖。
