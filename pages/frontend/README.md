# 模块页面前端

本目录是隔离的 npm 构建工程。源组件使用 Vue 3 的 TypeScript render 函数、Naive UI 和 Vite；运行时不下载 CDN 代码。

```powershell
npm.cmd ci --registry=https://registry.npmjs.org
npm.cmd run typecheck
npm.cmd run build
npm.cmd run test
```

`build` 发布 `pages/shell/{app.js,runtime.js,styles.css,index.html}`，独立构建 `modules/ff14/pages/dist/{entry.js,styles.css}`，生成模块资源 SHA256 清单，并按实际 runtime chunk 的 rendered modules 输出第三方许可证。业务源码和查询合同归 FF14 模块；`pages/ff14/query-contract.js` 是为旧入口生成的兼容副本。

独立管理页面由 `src/management.js` 保留校验与管理控制器，`src/management-view.ts` 使用 keyed Vue 表单和 Naive UI 的主题、卡片与提示呈现。原生 schema 控件保留标签、焦点与选区；管理目录声明与已授权 snapshot 分开读取，新增声明不会授予存量读取或写入权限。保存确认仅重置提交时未再编辑的草稿。管理页面只使用本地 `./runtime.js`；构建一次共享运行时，再将相同字节及完整许可证复制到每个独立合法页面根目录。

固定 AstrBot 4.28.2 按目录名排序发现页面，插件列表监视入口选择第一项。`pages/00-game-link` 因此是默认新壳入口，独立包含 HTML、app、runtime、许可证与打包生成的 loader、stylesheet template 和模块资源。旧 `ff14`、独立 `management`、显式 `shell` 仍保留。默认 HTML 静态声明 `pageName=00-game-link`，壳以该声明绑定 bridge；错误的 shell/default 页面名拒绝加载。无宿主补丁或重定向。

管理合同测试需显式执行 `node --test ../../tests/pages/management/catalog-contract.test.mjs ../../tests/pages/management/test_management.mjs`，默认 `npm.cmd run test` 只执行壳测试。模块 A 的迟到资源失败仍撤销 A 及清理其资产；当前选择 B 时保留 B 的状态、视图与可用性，重新访问 A 须正式重开页面。

模块入口的 Vue 和 Naive UI imports 均 external 到发布后的共享 `runtime.js`。壳仅根据可信 catalog 的 pages 生成导航，通过 builder 生成的 literal import loader 加载模块。构建 Dashboard 包时必须同步模块 manifest 摘要、复制模块资源、生成 loader 和 inert stylesheet template；目录里的空 template 不是完成的 Dashboard 发布包。宿主会给 HTML 中的 literal stylesheet href 和 JavaScript literal imports 加入资源授权，模块 CSS 通过 clone 该 template 的 link 加载。

资产归属于当前 document 的模块生命周期（owner、runtime、epoch、asset_version、页面资源合同和 context 安全边界），CSS 加载须收到 `load` 事件，`error` 或超时会封锁该 owner。有效生命周期内切页保留已加载 CSS，并复用入口 Promise；FF14 items/market 共用 entry。视图 scope 仍在切页时清理组件、浮层与请求，资产 scope 则只在真实模块失效或 document 关闭时清理。失败的清理保留原 owner 和回调，可通过清理按钮重试。

目录中的模块失效、生命周期/资源合同变化，或 context 安全边界变化后，旧 HTML template 和 ESM 投影不能恢复使用。必须关闭当前页并从宿主插件详情正式重新打开以获得新投影；状态刷新只读取目录，不刷新资源授权。壳不读取、解码或重签 opaque URL，也不增加资源刷新 API。其他仍有效的模块保留其资产；上下文失效则封锁整页。首次迟到访问未加载的模块仍可能因资源授权过期失败，失败提示同样要求正式重开。

`mount(container, options)` 返回 `update(context)` / `dispose()`；options 仅包含当前页面受限 query service、只读 context 与资源 scope。当前公共 transport 合同要求 `endpoint === queries/${route_id}`，capability 必须与当前页面描述符一致；这只是客户端路由约束，每次后端请求仍须正式鉴权。

`tests/pages/shell` 中的第二模块、bridge 和 public DTO 都是离线夹具，不进入默认业务包。Node 使用 stripTypeScriptTypes 测试源控制器，并对实际 production JS 执行 JSDOM 检查；这些检查不替代真实宿主或浏览器验收。旧 Python 投影测试通过 `YGL_TEST_PYTHON` 指向已准备的测试环境。
