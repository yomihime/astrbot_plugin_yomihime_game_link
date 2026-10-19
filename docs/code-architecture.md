# 当前代码分层

- `yomihime_game_link_sdk/`：独立 MIT 声明与薄协议；无 Host/网络/存储实现依赖，a9，ABI 2.0
- `core/`：生命周期、调用权限、注册和边界校验；`core/contracts/` 是 SDK 接收边界，不能成为第二套公开 SDK
- `services/`：通用协调；`configuration/operations.py` 管理值校验、事务与 CAS，`defaults.py` 管理 Core/模块默认值，`catalog.py` 投影配置声明
- `infrastructure/`：SQLite、HTTP、文件和加密；`windows/` 隔离经过环境资格限定的原生文件系统/维护实现
- `adapters/astrbot/`：真实宿主请求、消息与页面适配，不能给模块数据凭空授予权限
- `modules/ff14/`：FF14 来源、能力、日历同步、模块配置和 Vue 页面
- `presentation/`：通用图像、布局和字体接口；游戏特定解释仍属于模块
- `pages/frontend/`：共享页面源代码；`pages/{shell,management,00-game-link}` 与模块 `pages/dist/` 由构建生成，不跟踪重复产物

配置只读取当前 SQLite 值及声明默认值。无旧 Host JSON 导入、迁移准备文件或旧配置回退。显式无效值仍拒绝启动并保留管理修复入口；修复与恢复必须通过鉴权及 revision 检查。数据库 schema 初始化与正常资源恢复不属于被删除的旧配置迁移。

发行 ZIP 从白名单输入生成，SDK 从独立可复现 wheel 注入并校验摘要。第三方许可和 `py.typed` 保留。仓库 checkout 不能替代正式安装 ZIP。

大型生命周期、调度及存储事务协调器没有仅为行数做机械拆分；跨状态/取消/原子性边界的重构应另有独立设计与完整回归。
