# Core 通用授权与 AstrBot 有限管理合同

状态：2026-10-05 用户明确修订后冻结的最小实现合同（H-ADMIN-02）。实现及隔离验收在本批；真实实例启用另行确认。

## 已确认的修订与边界

Core 保持独立，可在不导入 AstrBot 的进程中初始化、管理配置并拒绝未授权操作。AstrBot 部署使用宿主正式管理后台鉴权，不要求用户再输入独立 Core 能力凭据。原生 Core 凭据仍是一种独立认证来源，不是其它 Host 授权来源的 ACTIVE 前提；禁止为兼容检查自动生成或塞入无意义的密钥。

本合同替代 basic-design/SDK 中“所有 Host 管理必须再证明独立 Core 凭据”的旧规定。旧维护 CLI 的真实凭据生命周期、安全 guard 和原生认证测试保留；不能将这些约束静默删除或让 Host 授权获得 rotate/revoke 等额外权限。

## 通用合同与不变量

- Core 定义通用主体、授权来源/authority、操作、目标及字段范围、请求/证明寿命、来源 generation/失效语义。Core 不认识 AstrBot JWT、账号对象、页面或 FF14 字段。
- Core 装配显式注册可信 authority/adapter 及最大操作/资源策略。上下文由被信任适配器创建和拥有；字符串 ID、SDK 数据对象、JSON role/admin/grant 均不能自行证明授权。不得使用恒 True validator。
- 原生凭据路径继续查自身持久 ACTIVE/generation；可信 Host 路径查自己的注册来源、owned proof、epoch/有效期/请求存活和精确 operation/resource，不读 native ACTIVE 来授予/拒绝 Host 权限。来源不可由客户端切换。
- Core 签发的 grant 必须绑定真实证明及精确请求/资源；伪造同字段 grant、串来源、跨 Core/适配器、过期/取消/结束/close/restart/revoke 的证明拒绝。授权、await 后读取/写入效果前均重验；SQLite 修改/回退在同一事务检查授权效果 fence 与 revision/CAS。不能只在 adapter 入口验一次或把 native generation 当 Host 证明。
- 跨线程效果检查须安全读取 proof 生命周期/期限与 authority 状态，检查前后不得放行已知失效；在事务提交边界定义可证明的线性化点，保留取消/失效与排队事务负例。不能以退出 HTTP handler 就断言已排空的事务未提交；提交结果未知时使用 revision/marker 查明。
- 通用测试适配器替换 Host 时，不修改 FF14 或配置服务；未装配可信 authority 时默认拒绝。资源策略由部署装配声明，未来操作/模块没有自动授权。

## 本次有限资源和操作

部署只声明 Core `default_region` 与 FF14 `ff14_calendar_default_days`、`ff14_calendar_default_timezone`、`ff14_calendar_default_delivery_time`。目标/字段映射归 Host/FF14 装配，Core 只执行通用策略。

读取返回合法普通值、逐字段 valid/invalid/presence/source 状态及各目标 config revision；非法原值只显示安全状态，不反射未知/敏感字段。修改和非法存量修复沿用 ConfigPatch REPLACE/CLEAR、新值 schema/模块语义校验、expected config revision/CAS；未触及非法项不得被默认为已修。

带 grant 的仓储读取只接受明确的读取操作，并校验目标与字段；更新 grant 不能借读取方法取得额外权限。受限管理操作的成功快照、字段状态及失败恢复快照同样按 grant 投影，不返回范围外普通值或密钥元数据。业务健康发布与秘密收据维护所需的完整快照留在 Core 内部可信路径，不作为受限调用的返回值；原生无限资源授权及内部无 grant 读取的既有用途保留。

响应投影范围可在有效 owned grant 下、效果发生前捕获为不可变策略；它只约束输出，不是新的读写授权。配置 CAS 已提交后，既有操作的收据完成、健康发布和旧秘密清理仍按原有关闭期限排空，不能因响应投影再次查询已失效授权而误报清理失败。新操作以及实际写入前和 SQL 提交边界的生命周期检查继续有效；关闭前提交的效果不会被之后的关闭逆转，结果未知仍须通过新合法请求核对 revision。

限定回退为显式管理操作，只能影响既有 ordinary migration 定义的四字段/完成标记，两目标 expected revision、after-row revision/value 以及授权效果在同事务检查；保留无关配置、后续同值写入和订阅。不直接暴露仓储内部方法，也不自动整库恢复/schema 降级。

配置错误/迁移失败发生在业务恢复之前时，保留健康的有限管理 foundation（配置仓储/声明/授权），关闭业务 ingress、模块启动、HTTP 查询与 pump。不能依赖已关闭 Core，不重开 cleanup-pending 实例。schema/数据库本身不可用时明确管理 unavailable，不伪造可恢复状态。

修复后使用显式、受授权的恢复操作重新校验/迁移并尝试业务启动；不得把一次保存自动等同恢复。缺原始迁移准备材料不能编造旧配置。若支持“以已授权四字段完整显式新值完成恢复”，必须要求四个有效新键确实存在、明确该恢复意图和两目标 revision，标注来源为管理员替换，原旧值/准备材料仍保留；不能把未知旧值标成缺失或使用 Host 注入默认代替。

## AstrBot v4.28.2 的最小适配

固定 Host commit：`3c7adafa1397e182d60b1016bf88759265113c8a`。管理仅使用精确 legacy extension 路由和宿主正式普通 Dashboard JWT 鉴权链，内部调用该 Host 实际验签/到期方法，取得经过宿主校验的主体和期限；不自行解码 claims 授权，也不查找/导出宿主 secret。

当前 PluginRequest 只有 username，未公开 verified via/exp。最小兼容适配可通过真实 PluginRequest 所绑定的底层 request/app server 对象调用固定 Host 验签器，再要求主体与正式 dispatch 身份一致；该内部依赖须用固定源码合同测试覆盖，缺对象/版本失配时失败关闭并报告准确原因。不能从请求 JSON 补 via/exp。

只收单一 Bearer、严格同源 Origin、受限 POST 和字节/期限均有界的 JSON；拒绝 Cookie-only、普通 API key/回落、asset JWT、公开查询证明、重复认证头、客户端身份字段和范围外字段/操作。管理 Origin 与宿主真实请求 URL 的 scheme/authority 比较，仅作为同源防护，不以它证明管理身份；不依赖可留空的公开查询 Origin 配置，也不放宽公开查询合同。v1 JWT 分支不能仅凭 `via=jwt` 或 wildcard 放行，因为固定版本没有等价 asset-token 排除。官方页面父端通过 global axios 向 v1 extension 路由发请求；复用已有无业务副作用的同源 307 到精确 legacy 路由，只有 legacy 才签发管理证明/执行操作。固定父端与 axios interceptor 源码核对不替代实际浏览器验收；插件页面不自行读取或导出 Host 浏览器存储。

证明绑定真实 request object、适配器实例/epoch、已验主体和允许的操作/资源，期限为宿主已验 exp 与短请求 TTL 的较早者；不跨请求缓存。finally、取消、断连、terminate 失效，业务未启动时只有有限管理路由可用。管理页面如需要入口，使用独立有限管理页面/路由和已有 bridge/组件，公开查询页面保持只读，不建通用后台。

宿主 logout 只清 Cookie；固定账号更新路径不自动替换签名密钥，不能承诺删除 Cookie/改密码使所有已发 JWT 即时失效。每请求重新走宿主正式验证，Core/Adapter 自身请求失效与宿主 token 撤销分别报告。

## 最小操作入口

独立管理页为 `pages/management/index.html`，由宿主发现并通过官方 `AstrBotPluginPage` bridge 打开；FF14 公开查询页不增加写操作。以下 endpoint 由父端拼接 v1 extension 路径，307 到精确 legacy 管理路径后才执行。

| 操作 | bridge endpoint | 必需参数 | 范围及效果 |
| --- | --- | --- | --- |
| 读取 | `admin/read` | 空对象 | 四字段合法值/安全状态、来源和两目标 revision |
| 保存或非法值修复 | `admin/update` | `module_id`、`expected_revision`、`updates` | 单目标字段；`replace` 必须带有效新值，`clear` 不带 value；CAS，不自动恢复业务 |
| 限定回退 | `admin/rollback` | 两目标 `expected_revisions` | 只回退该四字段迁移及标记；拒绝后续相关修改，保留无关数据 |
| 显式恢复 | `admin/recover` | 两目标 `expected_revisions`、显式布尔 `complete_from_current` | 重新校验/完成迁移并尝试业务启动；缺原准备材料时 true 路径须四个新键均真实显式存在且有效 |

本部署的两个目标为 `game_link/core` 与 `ff14/ff14`；这不是 Core 内置的宿主或模块权限。legacy 路由前缀为 `/api/plug/astrbot_plugin_yomihime_game_link/`，只能 POST。正式宿主鉴权失败与 Core 拒绝分别记录；本插件使用 400 非法参数、403 授权拒绝、409 revision 冲突、503 操作不可用。v1 的 307 不是管理成功。

官方父端会将成功响应的 `data` 解包后交给子页，页面校验该 endpoint 的实际数据合同，不再要求额外成功 envelope。错误或连接中断若未取得最终确认，先刷新核对配置/revision；不能断言未写入。页面不读取或导出 JWT、Cookie 或浏览器存储，不要求独立 Core 凭据。`ordinary_snapshot`/`ordinary_rollback` 的通用 Protocol 与安全字段投影由 SDK H-ADMIN-02 声明；业务恢复为 CoreRuntime/Host 的装配操作，不伪称为已声明的 SDK 方法。

请求体限制的实际边界：固定 Host 的 `bind_quart_request_context` 会在进入插件 handler 前预读 `request.body()`。本插件的 8192 字节检查和短请求证明期限约束进入 handler 后的接收/授权效果，不覆盖宿主此前的流等待或内存占用；预读期间不会执行 Core 管理效果。该 Host 前置边界本批不修改，也不绕过官方请求链，不能宣称整个 HTTP 接收过程已有同样的大小/总期限保障。

## 验收与外部边界

必须覆盖无 AstrBot 的 Core 初始化/合法管理/拒绝、替换测试 adapter、native 与 Host 来源隔离、未 bootstrap/revoked native 不阻断合法 Host、伪造 context/grant、越操作/越字段、过期/请求结束/取消/authority close、等待期间失效与事务 fence、四字段安全读取/非法修复/CAS/回退/无关数据保护、首次配置启动失败管理与恢复。

隔离 Host 合同测试运行固定实际鉴权源码，使用合成认证材料，覆盖普通 JWT、asset/APIkey、Origin/重复头、真实请求绑定与生命周期；这不是真实实例验收。SDK 修改须重新构建并同步静态 source/wheel pins，保留原安全测试。最终同版相关测试、Ruff、构建/包校验、一次完整测试及独立审查。

本批不安装、迁移现有数据、启用真实委托、创建真实凭据、登录或读取/导出真实 JWT/Cookie，不提交或推送。Host 热上传/旧任务/SDK/恢复窗口的部署前置仍单独判断；本合同及离线通过不自动授予部署权限。
