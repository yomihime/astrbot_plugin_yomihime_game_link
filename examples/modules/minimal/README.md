# 最小中性模块与页面

本例 owner 为 `minimal/demo`，只返回确定性的公开状态，没有游戏、网络、模型、秘密或定时任务。它与 `examples/offline_sample` 的多服务 SDK 合同演示用途不同：这里专门展示正式磁盘发现、Python factory、受信 Host 选择及模块页面接入，不替代或删除后者。

`module.py` 只导入公开 Python SDK；`pages/entry.js` 是无依赖原生 ESM，消费[前端公开合同](../../../pages/public-contract.ts)的 owner 绑定 `services.invoke('status', {})`。前端合同与 Python wheel 分别发布，页面不导入壳内部状态或任意管理 API。`yomihime.manifest.json` 声明主页、能力和页面资源 SHA256；`assembly.json` 声明受信选择的闭合政策，`assembly.py` 为经审查的纯 Host 支持。扫描或导入这些文件本身不授予权限。

将本目录作为 `minimal` 包放在隔离 `extension_root` 下，由 Host 的受信装配配置显式选择该发布输入及 `SUPPORT`，交给通用装配、资源投影与页面目录。不要在 Core、壳或路由增加 `minimal` 特判，不要直接向 Registry 注册实例。后端 discovery/immutable factory capture 与页面资源捕获均复用生产机制；页面入口可直接投影，不需要另装前端依赖或生成第二个可发现页面根。

在仓库根、已安装当前 SDK 的共享环境运行：

```powershell
python -m unittest tests.modules.minimal.test_formal_chain -v
node --test tests/modules/minimal/test_page.test.mjs
```

`tests/modules/minimal/test_formal_chain.py` 将此包复制到测试自有临时目录，经真实 discovery、Core 的正式 loader/factory capture 与生命周期管理启用模块；离线 Host 公共页面证明绑定 exact owner/home/status，经过真实 `invoke_public_web` 返回固定公开事实。Host-current fixture 由 exact Core、active session、当前 generation 与 Core.closed 驱动，检查错误 Core、inactive session、代际变化、已消费请求证明关闭失效及正式调用尚在等待时关闭。模块停用恢复后旧 proof 拒绝，新 proof 可用；未选择装配、跨 owner 证明复用均拒绝。测试不手工注入 ModuleHandlers 或 Registry。临时 Core 正常关闭后目录回收。

这里的 Host 状态是受控离线 fixture，不是 AstrBot 登录或 JWT 会话证明；测试中的资源捕获也不替代 P5 的生产模块选择、发行资源投影及实际页面挂载验收。

页面测试位于 `tests/modules/minimal/test_page.test.mjs`，不放入发布模块库存；它为合成 DOM 与受控服务，验证挂载、重复点击、授权变化与迟到响应释放，断言保留在上面的新命令中。模块包只包含运行代码、声明资源、README 和 assembly，不能通过放宽可信库存规则来容纳测试文件。页面测试与真实离线 Core 链分层报告。正式 AstrBot 页面 bridge、真实浏览器视觉/交互、Local Test 安装及实际 IM 仍需各自验收，不能由此处离线证据替代。`enabled` 是中性装配 gate 示例字段，不是游戏配置；本例没有配置页或全局管理入口。
