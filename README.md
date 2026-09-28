# 如月怜的游戏连结

面向 AstrBot 的游戏信息插件，当前仍处于开发阶段。插件介绍、安装包和开发说明也可查看[仓库](https://github.com/yomihime/astrbot_plugin_yomihime_game_link)。

## 当前状态

目前只接入了帮助入口：

- `/ygl`
- `/ygl help`

目前没有注册的游戏模块，帮助页显示暂无已注册模块是预期状态。因此，游戏资料查询、账号绑定和订阅功能尚未接通；安装插件不会提供这些功能。

命令前缀可能受 AstrBot 配置影响。需要了解 AstrBot 插件的元数据、Logo 和开发流程，可参阅[插件开发指南](https://docs.astrbot.app/dev/star/plugin-new.html)。

## 安装

请安装 Actions 构建出的插件 ZIP：

1. 打开[构建工作流](https://github.com/yomihime/astrbot_plugin_yomihime_game_link/actions/workflows/build.yml)，选择通过验证且构建成功的运行记录。
2. 下载该记录中的 `release-artifacts-<run-id>` 工件，并解开下载得到的外层压缩包。
3. 在解开的文件中找到 `astrbot_plugin_yomihime_game_link-v0.1.2.zip`，通过 AstrBot WebUI 的插件管理上传这个内层 ZIP。

不要把外层 Actions 工件 ZIP、独立的 SDK `.whl` 或 `SHA256SUMS` 上传为插件。项目仓库直装尚未包含 release 构建时从已验证 SDK wheel 注入的运行资源，不能作为开箱即用的安装方式。

当前 `metadata.yaml` 声明的版本是 `v0.1.2`，尚无对应 tag 或 GitHub Release。请以成功构建工件中的插件 ZIP 文件名为准；不要把手动工作流产物误认为正式发布。

### WebUI 没有显示 README

较早的插件 ZIP 未包含 `README.md`。请下载并安装包含 README 的新构建包，然后在插件管理中重载插件或重新打开插件详情。刷新页面不能补回旧安装包中缺失的文件。

## 开发

开发环境、构建命令和验证范围见[贡献指南](https://github.com/yomihime/astrbot_plugin_yomihime_game_link/blob/master/CONTRIBUTING.md)。项目更新记录见[CHANGELOG](https://github.com/yomihime/astrbot_plugin_yomihime_game_link/blob/master/CHANGELOG.md)。本地构建会生成插件 ZIP、独立 SDK wheel 和 SHA-256 校验文件。

要发布插件到 AstrBot 市场，请以[AstrBot 插件发布说明](https://docs.astrbot.app/dev/star/plugin-publish.html)为准。

## 来源与许可

插件骨架由 AstrBot 的[helloworld 插件模板](https://github.com/Soulter/helloworld)初始化。仓库许可证见[LICENSE](https://github.com/yomihime/astrbot_plugin_yomihime_game_link/blob/master/LICENSE)；随包 SDK 的许可声明以独立 SDK 工件为准，两者的许可对应关系仍待核对。
