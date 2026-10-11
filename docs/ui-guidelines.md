# Yomihime 插件 UI 规范

本规范面向仓库内 Vue / Naive UI 页面；第一版样板为全局设置和 FF14 配置。不新增 UI SDK、宿主授权或业务合同。

## 导航与信息层级

外层宿主导航保持原样。插件侧栏使用完整品牌、首页、当前目录中的模块，底部为全局设置。桌面宽度 258px，999px 以下为 220px，719px 以下使用移动导航。模块默认进入主页；内部配置按实际管理声明显示，辅助查询按模块现有页面 descriptor 显示，无能力不造空业务页。

主页复用运行状态、已声明功能和管理入口；健康未知保持未知。查询名称只用 descriptor 标题。新入口为 `#/module/<encoded-owner>` 与 `#/module/<encoded-owner>/-/queries`；旧 `#/settings/module/<encoded-owner>`、`#/module/<encoded-owner>/<route>` 和全局 `#/settings` 均保留。浏览器前进、后退及刷新使用同一 hash 路由和离页确认。

## 密度与组件

公共实现位于 `pages/frontend/src/ui/`。`PageHeader`、`SettingsGroup`、`SettingsRow`、`ActionBar`、`StatusHint` 只负责呈现，调用方持有权限、草稿、确认和请求状态。

|项目|约定|
|---|---|
|字号|正文 15px，页面标题 28px，组标题 18px，辅助状态 13px|
|间距|4 / 8 / 12 / 16 / 24 / 32px，组内 16–20px，组间 12–16px|
|控件|最小高度 40px，短 210px、中 320px、长最多 560px|
|标签|桌面约 128px；窄屏放控件上方，不用 placeholder 代替|
|行|标签、适长控件、字段恢复默认，行距 8–12px，无每字段分隔线|
|颜色|`tokens.css` 的 panel / text / muted / border / accent / error / warning / success；与 Naive 浅深主题同步|

正例：三行日历用短数值、时间、中等时区输入；错误紧跟字段；主要保存按钮紧邻本组。反例：短数值全宽、常驻大成功框、靠缩字或缩点击范围压密度、隐藏冲突、把所有业务入口塞进侧栏。

## 状态与交互

成功用短提示，3.5 秒后消退；错误、冲突、未保存提示不自动消失。加载和刷新保留表单主体及输入节点；按钮通过既有单飞/current 栅栏拒绝重复写，避免焦点被忙态原生 disabled 意外抢走。键盘标签、可见焦点和正常 Tab 顺序必须保留。

“刷新模块状态”刷新目录及运行代际；“刷新配置与版本”读取配置/凭据状态及当前 CAS 版本，两者不能互相替代。普通设置保存与凭据保存使用不同接口，但同一 owner 共用 revision；每次保存成功刷新两类 snapshot，下一次保存使用新版本。组成功不表示其它组已提交，不并行旧版本、不宣称跨接口原子事务。

字段“恢复默认”只产生该字段的 clear 草稿；普通保存保持 owner 范围。“放弃本模块修改”恢复该 owner 的普通草稿，并清空该 owner 的凭据草稿。离页确认可取消；确认离开保留普通草稿、清空秘密。地区切换使用稳定 keyed 节点和 hidden 保留两区草稿。凭据显隐仅显示本次输入，服务器已存秘密永不读回；清除仍需明确勾选确认。存储成功不代表上游验证成功。

## 模块呈现扩展

模块可在 `modules/*/pages/src/presentation.json` 声明内部显示名、字段顺序、控件宽度、时间控件及凭据地区名称。构建期发现这些装饰，仅应用于管理目录实际返回的字段；它不注册模块、页面或权限，也不修改 schema、默认值和 SDK。未知模块用 descriptor / description / group 安全回退，业务标签不硬写进公共组件。Core 全局设置装饰由公共层持有。运行时外置模块无需这个可选文件仍能使用通用表单；新增包内装饰须重建前端。

## 本地参考与检查

仓库根运行 `npm.cmd run reference --prefix pages/frontend`，打开 `http://127.0.0.1:18762/reference.html`。这是轻量组件页，支持浅深主题和 loading / dirty / error / conflict / readonly / success 状态，无宿主连接和业务请求。

运行 `npm.cmd run typecheck --prefix pages/frontend`、`npm.cmd run build --prefix pages/frontend` 与受影响测试。构建同时更新壳、独立设置入口和必要发布资源。视觉验收使用隔离 bridge 回放，记录 CSS 视口、缩放、DPR；比较桌面配置、窄屏和深色，再抽查主页/辅助查询。离线 DOM、真实浏览器、宿主 HTTP、真实上游分别记录，互不代替。
