# 贡献指南

本文件面向仓库开发者，不随插件 ZIP 发布。面向插件用户的说明在[README](https://github.com/yomihime/astrbot_plugin_yomihime_game_link/blob/master/README.md)；用户可见更新记录在[CHANGELOG](https://github.com/yomihime/astrbot_plugin_yomihime_game_link/blob/master/CHANGELOG.md)。

## 环境

CI 使用 Python 3.13.9，并固定以下构建与检查工具：

- setuptools 80.9.0
- wheel 0.45.1
- Ruff 0.12.0
- 运行依赖按 `requirements.txt` 的范围或固定版本安装（含 tzdata）；新旧依赖端点的实测不代表区间每个版本均已验证
- Node.js 24 仅用于 FF14 页面状态/DOM 合成测试，无 npm 依赖，不是插件运行依赖

本地可安装相同版本：

```powershell
python -m pip install setuptools==80.9.0 wheel==0.45.1 ruff==0.12.0
python -m pip install -r requirements.txt
```

## 构建插件包

从仓库根目录运行：

```powershell
python scripts/build_release.py
```

脚本要求 Python 3.13 和固定版本的 setuptools、wheel。它从临时 SDK 源码副本构建并验证固定的 SDK wheel，然后生成：

- `dist/astrbot_plugin_yomihime_game_link-v0.1.2.zip`：上传到 AstrBot WebUI 的插件包；
- `dist/yomihime_module_sdk-1.4.0-py3-none-any.whl`：独立 SDK 工件，不是插件 ZIP；
- `dist/SHA256SUMS`：上述工件的摘要。

可用 `--tag v0.1.2` 校验 tag 必须与 `metadata.yaml` 中的版本相同。该参数只做版本校验，不创建 tag。发布工件的下载和安装步骤见[README](https://github.com/yomihime/astrbot_plugin_yomihime_game_link/blob/master/README.md)。

不要直接把未经构建的仓库快照安装到 AstrBot：SDK 运行文件由已验证的 wheel 注入插件 ZIP。SDK 契约与工件细节见[模块 SDK 说明](https://github.com/yomihime/astrbot_plugin_yomihime_game_link/blob/master/docs/module-sdk.md)。

## 验证

CI 在 Ubuntu 上运行完整 unittest 套件和 Ruff：

```text
python -m unittest discover -s tests -t . -v
ruff check .
ruff format --check .
```

FF14 页面还需运行 `node tests/pages/ff14/test_ui_a.mjs`。它验证合成 DOM、状态和 bridge 交互边界，不替代在真实 AstrBot 页面中的亮暗主题、窄屏、键盘和截图验收。

依赖验证通过后，Windows job 运行宿主 bootstrap 与打包回归测试（`tests.host.test_b05_sdk_bootstrap`、`tests.packaging.test_sdk_artifact_install`、`tests.packaging.test_release_build`），然后构建并上传工件。Windows job 是限定的宿主与打包检查，不代表在生产 AstrBot 实例中的完整验收；本地测试、静态检查和安装包构建也不能替代目标 AstrBot 环境中的加载与消息收发验证。

原生 Windows 文件系统扫描器的精确候选包含 Windows 10 build 26200/26300 与 CPython 3.12.10、3.12.14、3.13.9 的 x64 环境。新增 26300/3.12.14 已有本地 E 盘原生资格测试；C 盘沙箱夹具的祖先枚举拒绝不计通过，真实用户实例的安装/模块加载另有验证。不要把这些记录外推为所有系统/解释器组合、目录 ACL 或 Windows CI 均已通过。

## 文档与许可

README、CHANGELOG 和本文件用于公开项目说明；仓库中的设计、路线图和协作材料可能面向开发过程，不应作为插件已经提供的用户功能说明。提交插件代码或资源时，请保留其各自适用的版权与许可声明；本指南不重新定义或合并不同工件的许可。
