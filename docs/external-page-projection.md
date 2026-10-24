# 固定配对的外包页面

外部扩展包仍由独立 AstrBot 插件注册和拥有 Python 业务。此 alpha 沿用稳定页面合同，通过明确构建输入把受审静态资源投影到 Core 的唯一 `00-game-link` 页面；不提供运行时动态下载或任意 URL 装载。

在独立构建 staging 完成现有前端构建后，可以复用精确匹配的 canonical SDK wheel：

```powershell
python scripts/build_release.py --sdk-wheel <a13.whl> --output-dir <new-output-dir> --external-page-package <reviewed-package-root>
```

参数可重复，package root 必须直接包含 `yomihime.manifest.json`。仅捕获合法 manifest 明确声明、SHA-256 匹配的静态资源，不导入或收录外包 Python，不放松内置模块的固定可信清单。拒绝链接、资源摘要错误、重复 package、内置 owner 覆盖、路径与总量超限，以及输出覆盖输入。构建参数是维护者对具体候选的审查选择，manifest 自身不授予可信性。

完整 manifest 字节进入配对元数据，每包以其 SHA-256 派生资源路径。Core 只为当前真实外包 lease、完整 manifest 和模块声明相符的页面投影 catalog，并在 page-session 及配置操作的每个现有绑定检查处再次核对；旧 epoch、旧授权与 CAS 检查保留。失配时不提供可装载的页面/资源，配置页面操作拒绝；命令与模块生命周期继续遵守原有独立合同。

页面升级后需要重新构建匹配的 Core 静态投影、更新 DLC 并重启。必须从宿主正式入口重新打开页面；旧 HTML 的封闭 loader 无法接受新 manifest 派生的资源路径。正常构建不指定外包输入时仍只装配既有内置页面。此功能不代表 dots 新 UI 已同步，也不改变 SDK、业务权限或支持任意 Python 原地热替换。

验证入口为 `tests.packaging.test_external_page_projection`，并复验模块页面 HTTP/session、旧 lease、两插件真实 Host 安装及浏览器。源码、静态构建探针、真实 Host/API、真实浏览器、整壳/IM/模型须分别记录；仅生成 loader 不证明 Host 资源链可用。
