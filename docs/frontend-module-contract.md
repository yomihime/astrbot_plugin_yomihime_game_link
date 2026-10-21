# Module 前端合同 v1

模块唯一类型入口为 `pages/public-contract.ts`，独立于 Python SDK wheel。模块导出 `PageModule.mount(container, options)`，返回 `update(context)` 和 `dispose()`。不得导入 `pages/frontend/src` 内部实现。

受信 manifest/capture 注册页面；`ModulePageDefinition` 对应 routeId、title、role、order、capabilityId。role 闭合为 `home`、`config`、`aux`，未声明默认为 aux，每 owner 最多一个 home 和一个 config。后端 catalog 以 snake_case 投影这些字段及受审 entry/styles；owner、资源 URL 和 loader 由受信装配派生，不由模块运行时代码选择。

唯一正式根和 pageName 为 `00-game-link`。壳按 role 展示模块主页、配置、辅助查询；没有 home 时显示简洁摘要，没有 config 时不显示业务配置入口。模块不能注册全局页面或替换壳导航。

`PageOptions.services` 只包含当前 owner/page 的 invoke、config.current/save/clear 与 credentials.status/replace/clear。所有写操作携带已读 revision；同页重复写拒绝。clear 凭据由壳统一确认，确认期间切页或失去授权则不提交。config 返回自身字段安全 DTO，credentials 返回 configured、decryptable、upstream:unverified；不返回旧秘密、密文或全局存储管理对象。

当前内部请求协议为六个闭合 `admin/module-*` 动作：config-current/save/clear、credential-status/replace/clear。请求身份包含 owner、page、runtime_id、module_epoch；save 携 values，config clear 携 fields，凭据 replace 携 field/pair，凭据 clear 携 field 与固定确认。身份闭包不是授权；后端每请求独立验证当前 token/page、受信政策、owner/epoch、字段资源 READ/UPDATE、capability 和 CAS。普通查询不因此获得管理权限。

壳每次挂载先通过固定 `admin/module-page-session` 用上述身份取得 server-issued page_session（expires_at 为 UTC epoch seconds，最长 30 秒）。proof 只在内部服务闭包，六动作随请求提交。到期前的并发读取共享一次续期，并仅携自身 previous_session；不能撤销另一视图的 proof。dispose 固定关闭自身 proof，迟到 bootstrap 结果也关闭自身 proof。已提交写入的失败或未知结果不自动重试；本地过期前续期不代表后台已完成写入。Host 必须校验真实会话及 Origin，客户端身份或字段声明本身不授权。

到期后的旧 proof 已被 server registry 清除，不能再用作 previous_session。闲置超过 TTL 后采用 fresh bootstrap，重新校验当前 Host 会话与页面授权；只有未到期 proof 才轮换。并发读取仍只申请一次，授权失效不降级为旧 proof，已经发出的写入不因期间到期而重发。前端只把精确授权错误映射为受控授权失效，未知异常显示固定错误，保留未确认写的人工核对流程。

config.current 可附受限只读 descriptors：每个自身普通字段只包含 description、批准的 value_schema 子集、default 和可选 choices(value/label)。拒绝未知注解、秘密或源 Core 路径，所有 JSON 有界。模块 reply_theme 的 choices 由通用 palette provider 受信注入，包含 null“跟随核心”；模块不得维护 Core 调色板副本。保存显式主题、清除以继承 Core 均携当前 revision/CAS，旧回读不能覆盖提交后新草稿。

scope.signal 在卸载时中止；异步响应提交前必须检查 scope.isCurrent()。壳同时检查路由、runtime、epoch 与授权代际，旧 owner 的迟到响应不得进入新页。dispose 释放模块自建监听器、请求和挂载；模块不得终止共享 Vue runtime。切页后的业务秘密草稿须销毁，普通草稿由模块按 owner/runtime/epoch 分区。

持有草稿的页面可实现可选 `MountedPage.navigationImpact()`，只返回闭合 `{ordinary:boolean, credentials:boolean, pending:boolean, version:number}`；version 为非负安全整数且同次挂载不倒退。摘要不包含字段值、秘密、owner 或任意壳对象。owner 由壳当前可信挂载派生。页面在摘要变化后调用可选 `PageOptions.onNavigationStateChange()` 通知；它不提供导航或管理权限。未实现钩子的旧页面和 minimal 继续使用原清洁页面行为；钩子抛异常、非法或倒退摘要时壳保守拒绝离开并显示固定错误。

壳将当前Module摘要与全局设置保护合并，用同一页面内确认处理点击及hash/history导航和模块操作。取消保留当前实例、输入和秘密；确认后才正常dispose，保留普通分区草稿并清秘密。等待中的操作须提示离开不能撤回已发出请求，未确认结果通过重新读取收敛，不自动重发。确认绑定摘要版本与当前owner/runtime/epoch/宿主上下文，新编辑或身份变化使旧确认失效。此钩子只协调受审同页页面的交互，不是恶意JavaScript的隔离机制。

这是受审可信同页 Module 的能力合同。TypeScript 和闭包用于避免意外耦合，不构成恶意 JavaScript 沙箱；同页代码可能接触全局 bridge。实际权限依赖后端逐请求检查，不能把隐藏 bridge 宣称为权限隔离。

全局主密钥、存储恢复/迁移、字体和默认主题只属于全局设置，均不暴露给 Module services。视觉规则见 [UI 规范](ui-guidelines.md)，全局交互见 [管理页面](management-home-settings.md)，凭据安全与维护前提见 [凭据存储](credential-storage.md)。
