# 贡献指南

本文件面向仓库开发者，不随插件 ZIP 发布。面向插件用户的说明在[README](https://github.com/yomihime/astrbot_plugin_yomihime_game_link/blob/master/README.md)；用户可见更新记录在[CHANGELOG](https://github.com/yomihime/astrbot_plugin_yomihime_game_link/blob/master/CHANGELOG.md)。

## 环境

CI 使用 Python 3.13.9，并固定以下构建与检查工具：

- setuptools 80.9.0
- wheel 0.45.1
- Ruff 0.12.0
- 运行依赖按 `requirements.txt` 的范围或固定版本安装（含 tzdata）；新旧依赖端点的实测不代表区间每个版本均已验证
- 离线 Host 合同测试依赖单独放在 `requirements-test.txt`：PyJWT 2.10.1、FastAPI 0.135.2、Starlette 0.52.1、HTTPX 0.28.1、Quart 0.20.0、jsonschema 4.23.0；它们不加入插件运行依赖。其余传递依赖由 pip 解析，此文件不是完整依赖锁定文件
- Node.js 24 用于前端类型检查、构建及页面状态/DOM 合成测试；前端依赖由 `pages/frontend/package.json` 和锁文件声明，使用 `npm ci --prefix pages/frontend` 安装，不是插件运行依赖

主测试与发布构建使用 Python 3.13.9。固定 AstrBot v4.28.2 的 Python 要求为 `>=3.12`，SDK 的要求为 `>=3.11`；Host 合同测试因此要求 Python 3.12 或以上，并不提高 SDK 的最低版本。可另建 Python 3.12.14 虚拟环境验证 FF14/Host 合同；发布构建仍使用 3.13.9。

本地在隔离虚拟环境中安装相同版本：

```powershell
& 'C:\Path\To\Python313\python.exe' --version # 先确认明确路径为 3.13.9
& 'C:\Path\To\Python313\python.exe' -m venv .architecture-refactor/test-venv
. .architecture-refactor/test-venv/Scripts/Activate.ps1
python -m pip install setuptools==80.9.0 wheel==0.45.1 ruff==0.12.0
python -m pip install -r requirements.txt
python -m pip install -r requirements-test.txt
```

## 准备离线 Host 合同源码

Host 页面和 runtime 测试从独立的 [AstrBot 上游源码](https://github.com/AstrBotDevs/AstrBot/tree/3c7adafa1397e182d60b1016bf88759265113c8a) 读取真实 DTO 和 auth/dispatch AST，固定版本为 v4.28.2、commit `3c7adafa1397e182d60b1016bf88759265113c8a`。环境变量 `YGL_TEST_ASTRBOT_SOURCE` 必须指向该 checkout 下的 `astrbot` 目录，不能指向插件仓库或正在运行的用户实例。

以下 PowerShell 命令仅准备源码；不会启动 Host、运行 Host 安装脚本或修改实例配置：

```powershell
Add-Content -LiteralPath .git/info/exclude -Value '/.architecture-refactor/host-contract/'
git clone --filter=blob:none --no-checkout https://github.com/AstrBotDevs/AstrBot.git .architecture-refactor/host-contract
git -C .architecture-refactor/host-contract sparse-checkout init --cone
git -C .architecture-refactor/host-contract sparse-checkout set astrbot/api astrbot/dashboard astrbot/core
git -C .architecture-refactor/host-contract checkout --detach 3c7adafa1397e182d60b1016bf88759265113c8a
$env:YGL_TEST_ASTRBOT_SOURCE = (Resolve-Path .architecture-refactor/host-contract/astrbot).Path
```

Linux 可用相同 Git 命令，并设置 `export YGL_TEST_ASTRBOT_SOURCE="$PWD/.architecture-refactor/host-contract/astrbot"`。CI 两个平台均独立 checkout 此固定 commit，源码目录通过 Git 本地 exclude 忽略，不作为插件或 SDK 构建输入。

helper 每次加载前检查 checkout 根目录、HEAD、两处版本声明、相关源文件的 SHA256（UTF-8、LF 换行）和相关文件的 Git 脏改。缺少环境变量、源码、Git 或固定测试依赖，以及版本、commit、摘要或依赖版本不匹配，均报告 `AstrBot contract test environment` 错误；不得用 skip 或 mock Host 合同绕过。无需安装 AstrBot 的完整依赖或访问任何真实实例。

## 构建插件包

从仓库根目录运行：

```powershell
python scripts/build_release.py
```

脚本要求 Python 3.13 和固定版本的 setuptools、wheel。它从临时 SDK 源码副本构建并验证固定的 SDK wheel，然后生成：

- `dist/astrbot_plugin_yomihime_game_link-v0.1.2.zip`：上传到 AstrBot WebUI 的插件包；
- `dist/yomihime_game_link_sdk-0.1.0a6-py3-none-any.whl`：独立 SDK 工件，不是插件 ZIP；
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

准备上述环境后，可先运行限定测试：

```text
python -m unittest -v tests.host.test_astrbot_contract tests.host.test_ff14_pages tests.host.test_astrbot_runtime tests.host.test_item_display_contract tests.modules.ff14.test_item_display
node tests/pages/ff14/test_ui_a.mjs
node tests/pages/ff14/test_item_display.mjs
```

第二个 Node 测试调用当前 PATH 中的 `python` 导出实际产品与 `project_result` DTO，再交给页面渲染器验证；运行时需已激活对应虚拟环境，也可用 `YGL_TEST_PYTHON` 明确指定虚拟环境解释器。页面测试验证合成 DOM、状态和 bridge 交互边界，不替代真实 AstrBot 页面中的亮暗主题、窄屏、键盘和截图验收。

Ubuntu job 保留全量 unittest，不过滤已知历史失败；回归记录必须保留最终 passed/failed/skipped 汇总，并区分历史失败与本次新增回归。局部测试通过不等于全量通过。

依赖验证通过后，Windows job 运行限定的宿主 bootstrap、固定源码合同、FF14 页面与 runtime、物品展示合同和打包回归测试，然后构建并上传工件。离线源码/AST/ASGI 测试、宿主 HTTP、浏览器视觉与交互、实际 IM 投递是不同证据层；这些 CI 结果不代表真实实例已安装、加载或完成消息收发验收，也不能用 HTTP 200 或 activated 状态替代后续功能验收。

原生 Windows 文件系统扫描器的精确候选包含 Windows 10 build 26200/26300 与 CPython 3.12.10、3.12.14、3.13.9 的 x64 环境。新增 26300/3.12.14 已有本地 E 盘原生资格测试；C 盘沙箱夹具的祖先枚举拒绝不计通过，真实用户实例的安装/模块加载另有验证。不要把这些记录外推为所有系统/解释器组合、目录 ACL 或 Windows CI 均已通过。

## 文档与许可

README、CHANGELOG 和本文件用于公开项目说明；仓库中的设计、路线图和协作材料可能面向开发过程，不应作为插件已经提供的用户功能说明。提交插件代码或资源时，请保留其各自适用的版权与许可声明；本指南不重新定义或合并不同工件的许可。

R5 isolated tool tests require the pinned Host core/agent, core/provider, core/star, core/platform, core/pipeline/process_stage and astr_agent_context.py sources. Prepare astrbot/core through sparse-checkout before running the tests; tests validate every required source hash and never fetch missing files automatically.
