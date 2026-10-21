# 模块页面前端

本目录是隔离的 npm 构建工程。源组件使用 Vue 3 的 TypeScript render 函数、Naive UI 和 Vite；运行时不下载 CDN 代码。

从仓库根目录执行：

```powershell
npm ci --prefix pages/frontend
node pages/frontend/node_modules/typescript/bin/tsc --project pages/frontend/tsconfig.json --noEmit
node pages/frontend/build.mjs
node --test tests/pages/shell/*.test.mjs
```

Windows若仅npm.ps1受执行策略限制，可使用已核来源的Node标准npm CLI入口，例如`node "C:/Program Files/nodejs/node_modules/npm/bin/npm-cli.js" ci --prefix pages/frontend`；不改全局执行策略。依赖来自项目锁，不复用未知旧环境。

`build`仅发布`pages/00-game-link`一个页面根，含app、共享runtime、样式、index、字面import loader、声明模块资源及第三方许可证。模块入口和CSS从各包manifest声明生成，对应`pages/dist`入口编译其`pages/src`源码；原生ESM声明资源直接验证并投影。不生成旧shell／management／ff14 index或兼容合同副本，业务源码归Module。

全局Core设置嵌在唯一壳中；Module拥有自己的home／config页面。`src/management.js`保留通用管理校验与控制器，原生schema控件保留标签、焦点与选区；目录声明与授权snapshot分开读取，新增声明不授予读写权。保存只重置提交后未再编辑的草稿。所有模块页面复用唯一根内`./runtime.js`及其完整许可证。

固定AstrBot 4.29.1原生发现唯一`00-game-link`入口。HTML静态声明pageName，壳拒绝错误绑定，不保留旧页面或续签旧asset token。后端独立验证普通Dashboard身份和有界模块page_session；pageName不是可验证DOM来源证明。

开发预览可指定`node pages/frontend/build.mjs --out-root=C:/absolute/task-staging`，相对输出相对调用目录解释。受信构建者可显式添加`--bundle-root=examples/modules`选择中性样例源；默认仅`modules`。staging不写正式输出，不代表最终安装包或验收。

默认npm test只执行壳测试，还须显式运行tests/pages/management、tests/pages/ff14及tests/modules/minimal中的测试入口，完整命令见[开发指南](../../CONTRIBUTING.md)。模块A的迟到资源失败撤销A并清理其资产；当前选择B时保留B的视图与状态，重新访问A须正式重开。

模块Vue／Naive UI imports external到共享runtime；壳按可信catalog的pages及role导航，通过构建生成的literal loader加载。打包同步manifest摘要、资源、loader和inert stylesheet template。AstrBot原生带token路径使浏览器相对HTML／CSS／ESM URL继承资产权限，不依赖宿主重写JS／CSS。模块CSS从template克隆link加载。

资产归属于当前 document 的模块生命周期（owner、runtime、epoch、asset_version、页面资源合同和 context 安全边界），CSS 加载须收到 `load` 事件，`error` 或超时会封锁该 owner。有效生命周期内切页保留已加载 CSS，并复用入口 Promise；FF14 items/market 共用 entry。视图 scope 仍在切页时清理组件、浮层与请求，资产 scope 则只在真实模块失效或 document 关闭时清理。失败的清理保留原 owner 和回调，可通过清理按钮重试。

目录中的模块失效、生命周期/资源合同变化，或 context 安全边界变化后，旧 HTML template 和 ESM 投影不能恢复使用。必须关闭当前页并从宿主插件详情正式重新打开以获得新投影；状态刷新只读取目录，不刷新资源授权。壳不读取、解码或重签 opaque URL，也不增加资源刷新 API。其他仍有效的模块保留其资产；上下文失效则封锁整页。首次迟到访问未加载的模块仍可能因资源授权过期失败，失败提示同样要求正式重开。

`mount(container, options)`返回`update(context)`／`dispose()`；options仅含公开合同中的受限services、只读context与资源scope。Module不取得bridge、HTTP、JWT、page_session或任意管理通道；每次后端动作仍核当前owner／page／runtime／epoch、声明字段、独立READ／UPDATE与CAS。详见[公开前端合同](../../docs/frontend-module-contract.md)。

`tests/pages/shell` 中的第二模块、bridge 和 public DTO 都是离线夹具，不进入默认业务包。Node 使用 stripTypeScriptTypes 测试源控制器，并对实际 production JS 执行 JSDOM 检查；这些检查不替代真实宿主或浏览器验收。旧 Python 投影测试通过 `YGL_TEST_PYTHON` 指向已准备的测试环境。
