# 当前 Host 管理鉴权合同

验证基线为 AstrBot 4.29.1，commit `dffe922773725e9c178392c423676ad65044748c`。版本是测试出处，不是运行时精确版本开关。

## 管理请求

`register_web_api` 注册现行管理路由。v1 入口按官方宿主分发转到普通 Dashboard 入口；管理权限只来自真实请求绑定的宿主 Dashboard JWT 验证器。

- 仅精确 POST 路径；同源 Origin；单 Authorization 和单 Origin
- 严格 Bearer JWT 形状，不接受 X-API-Key
- 验证器必须绑定到当前请求对应的 Dashboard server；异常或合同不匹配即拒绝
- 必须为普通用户 token，无 token_type；未过期，username 与 PluginRequest 和 Dashboard middleware 状态一致
- 资产 token 仅用于静态文件，不能授予配置、凭据或模块管理权限
- 所有普通字段/凭据/模块管理操作仍需独立 Core source-owned proof、声明资源范围和 revision/CAS；异步边界继续验证授权 generation

不解析宿主私钥、不复制宿主 JWT 签名实现、不使用允许 API-key fallback 的 require_scope 代替普通管理用户合同。

## 4.29.1 页面

官方首选 `AstrBotPluginView` bridge。`pages/` 仍为支持的目录，`views/` 若存在会优先；本项目只保留一种目录，避免双根遮蔽。官方 `/views/{view}/_t/{token}/...` 路径令牌使浏览器原生相对 URL 在 HTML、CSS、JavaScript 静态/动态导入间继承资产权限。插件不再假定宿主会重写 JS/CSS，不自行传播或持久化令牌。

## 当前配置与恢复

全新安装从当前 SQLite 值和声明默认值启动。旧 Host 字段、迁移准备文件、迁移标记与限定回退不参与启动。显式无效值保留，管理读取只返回安全状态；管理员修正当前设置后，使用全部目标 revision 重新验证并恢复。恢复不重置数据库，不生成秘密，不改变已有订阅。

加密诊断只返回 `key_state`、`codec_state` 固定枚举。缺少部署密钥仅阻止需要秘密的操作，不阻止非秘密能力。

## 测试边界

离线合同测试读取并核验官方独立源码，真实集成测试执行完整宿主安装、鉴权和页面服务。测试根目录、JWT/凭据及所有来源响应均为隔离 fixture；不代表用户生产实例、真实业务 API 或 Windows 原生文件系统实机通过。
