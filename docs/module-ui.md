# Module 共享 UI 接入

Module 不需要复制 Core 的页面布局、表单分组、主题配色或组件实现。视图从 `@game-link/ui` 导入公开展示组件；生命周期和受限服务类型仍从 [pages/public-contract.ts](../pages/public-contract.ts) 导入。公开展示入口是 [pages/public-ui.ts](../pages/public-ui.ts)，不要深层导入 `pages/frontend/src`。

## 公开组件

- `PageTemplate` 根据 `content` 声明生成页头、分组、简单字段和操作。它自带 `UiProvider`，接收当前 `context`、`styleMountTarget`、稳定的 `idPrefix`，以及可选的 `state` 和 `appearance`。
- `UiProvider` 也可以单独使用。传入规范化的 `PageContext.theme`（`light` / `dark`）、`PageContext.locale` 和当前挂载容器 `styleMountTarget`。它给 `.ui-page` 根、Naive UI 组件和默认插槽提供同一份配色。
- `PageHeader`：`title` 必填，可选 `titleId`、`eyebrow`、`subtitle`；默认 slot 放页头操作。
- `SettingsLayout`：默认 slot 放主内容，可选 `aside` slot 放说明。
- `LogoMark` 是静态装饰徽标。调用方用 CSS mask 指定已验证的本地资源和尺寸。旧 `effect` prop 已弃用，传入它仍显示静态徽标。动态效果由模块维护，目录接入方式见[前端模块合同](frontend-module-contract.md)。
- `Surface`：语义化内容面板，可选 `title`、`eyebrow`。
- `SettingsGroup`：`title` 必填，可选 `number`；`fieldset` 使相关表单控件采用 fieldset/legend。
- `SettingsRow`：表单行布局；标签与控件通过默认 slot 提供。
- `ActionBar`：操作区域。
- `FormGrid`：仅布局，`columns` 为 `1` 或 `2`（默认 `2`），窄屏为单列；默认 slot 放字段。它不创建 `<form>`，不处理提交、校验、请求或权限。
- `FormField`：`controlId` 和 `label` 必填；可选 `hint`、`error`、`layout`（`stacked` / `row`，默认 `stacked`）及稳定的 `labelId` / `hintId` / `errorId`。默认 scoped slot 的 `controlAttrs` 提供 `id`、`aria-labelledby`、`aria-describedby`、`aria-invalid`；调用方必须传给真正可聚焦的控件，而不是外层包装。`actions` slot 放字段操作。它只展示调用方给出的错误，不解释业务 schema、读取值或改变草稿。
- `StatusHint`：`tone` 为 `neutral` / `success` / `warning` / `error`；显式 `role` 为 `status` / `alert` / `none`，默认 `status`。状态礼貌播报、警报及时播报，`none` 不生成 live region。颜色不自动推断请求成功或失败。

原生表单控件可复用 `ui-control`、`ui-button`（`data-variant="primary"` 或 `quiet`）、`ui-badge`、`ui-segmented` 样式类。为控件提供真实 label、可聚焦按钮和正确的 disabled/ARIA 状态；共享布局不会自动实现业务校验。Core 壳提供公共样式和 token；模块只增加有模块前缀的业务布局，不复制调色板或全局 CSS reset。

## 模板接入

在已有 `mount(container, options)` 中使用响应式草稿和业务控制器：

```ts
import {h} from 'vue';
import {PageTemplate} from '@game-link/ui';

h(PageTemplate, {
  context: currentContext, styleMountTarget: container, idPrefix: mountId,
  content: {
    title: '状态查询',
    sections: [{
      id: 'criteria', title: '查询条件',
      fields: [{id: 'name', label: '名称', control: 'text', width: 'medium'}],
    }],
    actions: [{id: 'query', type: 'submit'}, {id: 'cancelWait'}],
  },
  state: {values: {name: draft.name}, errors, pending},
  onFieldChange: ({id, value}) => editDraft(id, value),
  onAction: handleAction,
})
```

`idPrefix` 在当前文档所有同时挂载的模板中唯一，并在该实例生命周期内固定。分组和字段使用稳定 ID，字段 ID 在整个模板中唯一。简单控件支持 `text`、`number`、`time`、`select` 和 `checkbox`，选项顺序、值和显示名由调用方提供。复选框发出布尔值，其余发出原始字符串。控制器继续负责解析、校验、草稿、请求、权限和 revision/CAS。

只有声明一个 `type: 'submit'` 的操作才生成主表单。点击、单行输入 Enter 和 `requestSubmit()` 共用一次提交路径。模板内的 `sectionExtra`、`control` 属于主栏，需避免在其中添加独立表单或提交按钮。模板本身应放在外层表单之外。 `aside`、`results`、`footer` 和 `headerActions` 位于主表单之外，独立凭据表单可放在 `aside`。操作默认是普通按钮。

复杂字段声明 `control: 'custom'`，在 `control` 插槽中把 `controlAttrs`、`disabled` 和 `readonly` 应用到实际控件，并用 `onChange` 回传草稿。所有插槽均收到 `presentation`，可读取当前主题、语言和解析后的色板。 `logo` 插槽可渲染已验证资源的 `LogoMark`。在弹出控件中继续使用当前挂载容器作为 `to`，让内容保留在本模板内。

`state.disabled` 表示真正不可用，`state.readonly` 限制字段编辑。 `state.pending` 让常规操作进入保留焦点和 Tab 顺序的忙态，显式声明的 `cancelWait` 可继续使用。控制器接收动作时仍需在等待请求前同步开启原单飞门。 `state.notice` 由控制器选择；已提交但状态未核实用 `pendingUnverified`。 `uiText(key, locale)` 提供通用操作与状态的纯文本默认值。

`appearance.light` 和 `appearance.dark` 可分别覆盖 `background`、`panel`、`text`、`muted`、`border`、`accent`、`accentContrast` 和 `logo`，值须为六位十六进制色码。任一槽或值非法时使用整套 Core 默认色。删掉主题或槽会恢复对应默认值；错误、警告、成功色由 Core 保持。完整例子见 [shared-ui 示例](../examples/modules/shared-ui/pages/src/entry.ts)。

## 视图生命周期与主题

`mount(container, options)` 创建模块自己的视图实例，根使用 `PageTemplate` 或 `UiProvider`。将 `options.context` 放入响应式状态，`update(nextContext)` 更新现有实例的主题和语言。主题切换保留输入、焦点和待处理请求。主题和语言取自规范化的 `PageContext`。

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

[shared-ui 页面源码](../examples/modules/shared-ui/pages/src/entry.ts) 演示用 `PageTemplate` 声明字段、选项、配色及默认操作，并通过既有页面服务调用演示 `status` 能力。

该源码示例在类型检查和真实 Vite 内存构建中作为独立 entry 编译，JS 仅引用共享 `runtime.js`，不产生第二份 Vue 或公共 CSS。它缺少可安装包所需的 manifest、后端工厂、能力/资源声明与摘要和受信装配，因此不是可直接安装的模块。生产模块通过受审构建投影 `pages/dist` 资源，并继续使用 manifest/hash/loader 校验；示例测试不授予接入权限。

现有 [minimal 页面](../examples/modules/minimal/pages/entry.js) 保持纯 DOM 兼容，不要求所有旧模块迁移到 Vue。

验证命令：

```sh
npm run typecheck --prefix pages/frontend
node --test tests/pages/shell/shared-ui-consumer.test.mjs
```

[消费者测试](../tests/pages/shell/shared-ui-consumer.test.mjs) 使用实际公开 facade 和共享 runtime 的内存构建，核对壳独立拥有且只输出一层公共 CSS、示例不复制 runtime/CSS，并验证非 FF14 页面挂载、主题更新保留节点、同一绑定暂不可用后的恢复、旧请求结果隔离与幂等卸载；不写入正式资源或 manifest。JSDOM 不进行真实布局和宿主授权验收，正式构建及浏览器视觉检查仍须另行执行。
