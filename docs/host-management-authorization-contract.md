# 当前 Host 管理鉴权合同

验证基线为 AstrBot 4.29.1，commit `dffe922773725e9c178392c423676ad65044748c`。版本是测试出处，不是运行时精确版本开关。

## 管理请求

`register_web_api` 注册现行管理路由。v1 入口按官方宿主分发转到普通 Dashboard 入口；管理权限只来自真实请求绑定的宿主 Dashboard JWT 验证器。

- 仅精确 POST 路径；同源 Origin；单 Authorization 和单 Origin
- 严格 Bearer JWT 形状，不接受 X-API-Key
- 当前请求的 `dashboard_app_adapter._app` 必须就是请求应用；验证器必须是该 adapter 的 Dashboard server 上真实绑定的同步方法，拒绝 coroutine、generator 和 async-generator 函数
- 验证器签名必须能绑定 token、精确路径两个位置参数；参数名称、兼容的可选参数与 varargs 不构成拒绝理由。返回必须是精确二元 tuple，payload 为精确 dict，error 为精确空字符串；异常、不兼容或异步返回均拒绝，已创建的 coroutine 会关闭
- 必须为普通用户 token，无 token_type；未过期，username 与 PluginRequest 和 Dashboard middleware 状态一致
- 资产 token 仅用于静态文件，不能授予配置、凭据或模块管理权限
- 所有普通字段/凭据/模块管理操作仍需独立 Core source-owned proof、声明资源范围和 revision/CAS；异步边界继续验证授权 generation

不解析宿主私钥、不复制宿主 JWT 签名实现、不使用允许 API-key fallback 的 require_scope 代替普通管理用户合同。

全局普通配置、历史配置 copy、主密钥等管理动作也由 source live 围栏重新调用真实 Dashboard 验证器，核对原请求、同一主体、同一 Bearer 会话、Origin 和当前 authority；不因复核延长原 deadline。在异步边界、SQLite 提交前及最后一次 await 后继续复核，验证器异常或撤权即安全拒绝。提交前拒绝保证事务零写；提交后回执丢失不能承诺回滚，也不能自动重试写入。

## 4.29.1 页面

官方首选 `AstrBotPluginView` bridge。`pages/` 仍为支持的目录，`views/` 若存在会优先；本项目只保留一种目录，避免双根遮蔽。官方 `/views/{view}/_t/{token}/...` 路径令牌使浏览器原生相对 URL 在 HTML、CSS、JavaScript 静态/动态导入间继承资产权限。插件不再假定宿主会重写 JS/CSS，不自行传播或持久化令牌。

### 模块管理页面会话

唯一正式根为 `00-game-link`。模块的配置与凭据动作还要求独立的、仅内存保存的 `page_session`；它不能由静态资产令牌、页面名称、Referer或客户端角色声明替代。

`POST admin/module-page-session` 接收当前目录中的 `owner`、`page`、`runtime_id`、`module_epoch`，可选 `previous_session` 仅旋转本视图先前的opaque proof。后端先验证真实Dashboard JWT、Origin及当前请求身份，再核当前注册／启用／active模块与运行identity，并将页面声明字段和受信部署资源取交集，独立核普通配置及凭据字段的READ／UPDATE授权。返回 `{page_session, expires_at}`；后者为UTC epoch秒，最长30秒。会话表最多128项并清理到期项，不保存JWT原文或持久化proof。

六个 `module-config-current/save/clear`、`module-credential-status/replace/clear` 请求在原身份正文之外携带 `page_session`。Host逐次检查真实会话、scope、runtime／epoch、字段交集及generation，再签发当前动作的Core管理proof；后续异步边界及提交围栏继续检查。会话不能选择任意管理API、全局密钥、Core主题或其他owner字段。`module-page-session-close` 只关闭绑定到同一真实会话的本proof，正文仅 `{page_session}`。两个视图各有独立proof；刷新仅替换自己的previous proof，不使另一视图的并发读取失效。卸载、runtime更换、关闭或到期使旧proof失效。

前端把proof留在服务闭包中。挂载前取得会话，刷新为singleflight，写操作不因过期或UNKNOWN而自动重试；离开时关闭本proof。该模型针对受审可信模块代码，不能隔离同页恶意JavaScript。

AstrBot 4.29.1父页bridge用普通Dashboard JWT发送API请求，没有向插件后端传递可验证的来源DOM证明。这里证明的是经Host认证且有范围的页面授权，不声称证明实际请求来自某个DOM。旧shell／management资产token一律不能换成管理权限；发行包不再发现其index，不转移／复用这些token。

## 当前配置与恢复

全新安装从当前SQLite值和声明默认值启动。普通历史字段只有经受信、闭合的copy policy才能按目标字段名作临时effective projection；目标已有值即停止，source保留。显式plan／execute进行双revision CAS，不能任意读取Core配置；详情见[架构规范](code-architecture.md)及[凭据存储](credential-storage.md)。显式无效值保留，管理读取只返回安全状态；管理员修正后按全部目标revision重新验证恢复。恢复不重置数据库、生成秘密或改变订阅。

加密诊断只返回 `key_state`、`codec_state` 固定枚举。缺少部署密钥仅阻止需要秘密的操作，不阻止非秘密能力。

## 测试边界

离线合同测试读取并核验官方独立源码，真实集成测试执行完整宿主安装、鉴权和页面服务。测试根目录、JWT/凭据及所有来源响应均为隔离 fixture；不代表用户生产实例、真实业务 API 或 Windows 原生文件系统实机通过。
