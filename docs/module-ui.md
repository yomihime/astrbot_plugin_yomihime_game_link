# Module 共享 UI 接入

Module 不需要复制 Core 的页面布局、表单分组、主题配色或组件实现。视图从 `@game-link/ui` 导入公开展示组件；生命周期和受限服务类型仍从 [pages/public-contract.ts](../pages/public-contract.ts) 导入。公开展示入口是 [pages/public-ui.ts](../pages/public-ui.ts)，不要深层导入 `pages/frontend/src`。

## 公开组件

- `UiProvider`：页面根。传入规范化的 `PageContext.theme`（`light` / `dark`）、`PageContext.locale` 和当前挂载容器 `styleMountTarget`。配置共用组件的主题与语言，并提供 `.ui-page` 根。
- `PageHeader`：`title` 必填，可选 `titleId`、`eyebrow`、`subtitle`；默认 slot 放页头操作。
- `SettingsLayout`：默认 slot 放主内容，可选 `aside` slot 放说明。
- `LogoMark`：装饰性矢量徽标。调用方用 CSS mask 指定已验证的本地资源和尺寸；组件不读取目录、网络或业务状态。每次页面挂载只播放一次扫光回弹，约 2.17 秒后保持全亮，不淡出、不循环；同一页面的主题或配置刷新不会重播，离开后重新进入的新实例会重新播放。减少动态效果偏好下立即静态全亮；没有徽标的通用占位符不使用该组件。
- `Surface`：语义化内容面板，可选 `title`、`eyebrow`。
- `SettingsGroup`：`title` 必填，可选 `number`；`fieldset` 使相关表单控件采用 fieldset/legend。
- `SettingsRow`：表单行布局；标签与控件通过默认 slot 提供。
- `ActionBar`：操作区域。
- `FormGrid`：仅布局，`columns` 为 `1` 或 `2`（默认 `2`），窄屏为单列；默认 slot 放字段。它不创建 `<form>`，不处理提交、校验、请求或权限。
- `FormField`：`controlId` 和 `label` 必填；可选 `hint`、`error`、`layout`（`stacked` / `row`，默认 `stacked`）及稳定的 `labelId` / `hintId` / `errorId`。默认 scoped slot 的 `controlAttrs` 提供 `id`、`aria-labelledby`、`aria-describedby`、`aria-invalid`；调用方必须传给真正可聚焦的控件，而不是外层包装。`actions` slot 放字段操作。它只展示调用方给出的错误，不解释业务 schema、读取值或改变草稿。
- `StatusHint`：`tone` 为 `neutral` / `success` / `warning` / `error`；显式 `role` 为 `status` / `alert` / `none`，默认 `status`。状态礼貌播报、警报及时播报，`none` 不生成 live region。颜色不自动推断请求成功或失败。

原生表单控件可复用 `ui-control`、`ui-button`（`data-variant="primary"` 或 `quiet`）、`ui-badge`、`ui-segmented` 样式类。为控件提供真实 label、可聚焦按钮和正确的 disabled/ARIA 状态；共享布局不会自动实现业务校验。Core 壳提供公共样式和 token；模块只增加有模块前缀的业务布局，不复制调色板或全局 CSS reset。

## 最小字段视图

在已有 `mount(container, options)` 中创建自己的响应式草稿，使用下面的组合即可复用字段、布局与反馈；保留原有业务控制器和服务调用，不把授权交给组件：

```ts
import {h} from 'vue';
import {FormField, FormGrid} from '@game-link/ui';
import type {FormFieldSlot} from '@game-link/ui';

h(FormGrid, {columns: 1}, {
  default: () => h(FormField, {
    controlId: 'my-module-query', label: '查询',
    hint: '只在手动提交后查询。', error: errors.query,
  }, {
    default: ({controlAttrs}: FormFieldSlot) => h('input', {
      ...controlAttrs, class: 'ui-control', value: draft.query,
      onInput: (event: Event) => editQuery((event.target as HTMLInputElement).value),
    }),
  }),
})
```

这里的 `draft`、`errors`、`editQuery` 由模块持有；组件不提供保存、重试或凭据 API。完整可类型检查的 [shared-ui 示例](../examples/modules/shared-ui/pages/src/entry.ts) 展示 `UiProvider`、布局、字段、状态、受限 `status` 调用与 `mount/update/dispose`。示例字段只修改本地标题，不声称保存配置。真实业务字段应沿现有 `PageOptions.services`、revision/CAS 和草稿失效合同提交。

## 视图生命周期与主题

`mount(container, options)` 创建模块自己的视图实例，根使用 `UiProvider`。将 `options.context` 放入响应式状态；`update(nextContext)` 更新 provider 的 theme/locale，而不是重新挂载整个页面。主题切换不应丢失输入、焦点或待处理请求。只读取规范化的 `PageContext`，不要直接读取 Host bridge、`isDark` 或自行探测宿主主题。

`dispose()` 只卸载该模块实例，并释放其监听器与请求。通过 `options.scope.onDispose` 注册同一幂等清理函数；不能终止共享 Vue runtime。异步结果提交前检查 `scope.isCurrent()`、当前可用性和 owner/runtimeId/epoch/boundary 绑定。`available:false` 也可能表示普通目录刷新，不能单独将它解释为永久撤权：暂时禁用操作并使旧请求结果失效；同一绑定恢复可用后允许用户发起新请求，不自动重发。owner/runtimeId/epoch/boundary 改变或 scope 失效后不能恢复旧请求；绑定改变后的实例不得因为再次收到旧上下文而复活。

页面浅色/深色主题与命令图片的 `reply_theme` 是两件事。图片主题仍走现有配置 descriptors 和 revision/CAS，不能用 UI facade 查询 Core 配置、复制图片主题列表或绕过授权。

## 构建与运行时

在本仓库受审构建中，TypeScript 将 `@game-link/ui` 解析到公开入口；生产构建将该导入与 `vue` / `naive-ui` 一并外置到壳发布的同一份 `runtime.js`。模块不要打包第二份 Vue、复制 Core 组件实现或硬编码运行时资源 URL。`@game-link/ui` 是当前受审构建支持的入口，不宣称它已单独发布为 npm 包。模块页面的 manifest、资源摘要、可信捕获及装配准入仍遵守[前端合同](frontend-module-contract.md)。

公共 CSS 的唯一生产所有者是壳入口 `pages/frontend/src/main.ts`，它显式导入共享 tokens 并与壳样式一起构建为 `pages/00-game-link/styles.css`；不依赖先创建或导航到管理页面。`index.html` 在启动时加载该 stylesheet，独立组件预览可自行加载其专用样式入口。Module 只使用公共类，不深层导入 Core CSS，不按硬编码 URL 动态加载 tokens，也不复制主题变量。共享组件并非可脱离壳 CSS 的独立 npm 包。

展示 facade 不导出服务、Host bridge、全局管理对象、存储或权限。业务继续调用 `PageOptions.services`，保留 owner/page/runtime/epoch、后端逐请求授权、revision/CAS 和草稿保护。这里只复用视图，不新增后端服务或扩大管理权限；也不是恶意同页 JavaScript 的沙箱。

## 模块徽标与目录集成

模块继续在已有 manifest `display.logo_resource` 中声明相对图片资源，并在 resources 中登记对应摘要；Core 不维护游戏徽标映射。目录展示 DTO 的可选 `display.logo_resource` 将已加载模块的声明投影为 `module-assets/{owner}/{relativeAsset}`，未加载或没有徽标声明时为 null；没有 display 的模块仍保持 display 为 null。

前端仅接受属于同一 owner、列在当前 resources 中且扩展名为 svg/png/jpg/jpeg 的图片路径。旧目录省略该字段、null 或空字符串时采用通用标记；跨 owner、未声明资源、外部 URL 或非图片路径不被当作有效徽标。首页将已校验徽标通过 CSS mask 呈现为单色金色轮廓，不保证保留原始彩色图像；模块名称仍提供文字标识。这个展示投影不新增资源权限，不改变原有资源摘要、缓存和访问校验。

并行集成目录扩展时需协调三个共用接缝：[后端目录投影](../services/module_catalog.py)、[前端 DTO](../pages/frontend/src/contracts.ts) 和[目录校验](../pages/frontend/src/catalog.ts)。这些链接用于维护和集成定位，不是 Module 可导入的公共 API。保留可选字段与旧目录兼容，不引入游戏专属 Core 分支；Module 视图继续使用公开 facade 与 PageOptions。

## 非 FF14 示例与验证

[shared-ui 页面源码](../examples/modules/shared-ui/pages/src/entry.ts) 是独立于 FF14 的源码级消费者：公开导入布局与状态组件，用 `UiProvider` 跟随 context，并通过既有页面服务调用演示 `status` 能力。它没有 manifest、后端工厂或生成资产，不能直接安装，也不代表第二个生产包已通过验收。集成到真实模块时必须声明并受权其实际能力和资源。

该源码示例在类型检查和真实 Vite 内存构建中作为独立 entry 编译，JS 仅引用共享 `runtime.js`，不产生第二份 Vue 或公共 CSS。它缺少可安装包所需的 manifest、后端工厂、能力/资源声明与摘要和受信装配，因此不是可直接安装的模块。生产模块通过受审构建投影 `pages/dist` 资源，并继续使用 manifest/hash/loader 校验；示例测试不授予接入权限。

现有 [minimal 页面](../examples/modules/minimal/pages/entry.js) 保持纯 DOM 兼容，不要求所有旧模块迁移到 Vue。

验证命令：

```sh
npm run typecheck --prefix pages/frontend
node --test tests/pages/shell/shared-ui-consumer.test.mjs
```

[消费者测试](../tests/pages/shell/shared-ui-consumer.test.mjs) 使用实际公开 facade 和共享 runtime 的内存构建，核对壳独立拥有且只输出一层公共 CSS、示例不复制 runtime/CSS，并验证非 FF14 页面挂载、主题更新保留节点、同一绑定暂不可用后的恢复、旧请求结果隔离与幂等卸载；不写入正式资源或 manifest。JSDOM 不进行真实布局和宿主授权验收，正式构建及浏览器视觉检查仍须另行执行。
