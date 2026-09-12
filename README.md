# Yomihime Game Link · 怜的游戏连结

连接你的游戏世界，在聊天中查询角色、战绩与游戏资讯。

> 开发准备阶段：当前仅提供 `/ygl` 插件介绍命令，游戏查询与账号绑定尚未实现。

## 目标

- 聚合多款游戏的信息查询，首批计划支持 Dota2 与 FF14。
- Dota2：对局详情、玩家战绩。
- FF14：角色相关 Logs 查询。
- 按聊天用户绑定游戏账号，让“查询我的战绩”等请求使用对应绑定。
- 为后续游戏与信息源保留模块扩展空间。

自然语言查询、多账号规则、FF14 国服与国际服支持范围及数据源将在后续设计阶段确定。
Yomihime Arcade 名称预留给未来的 Bot 聊天游戏项目。

## 开发方式

本项目是独立 Git 仓库。本地路径：

```text
E:\AI\Dev\AstrBot\astrbot_plugin_yomihime_game_link
```

相邻的 AstrBot 本体仓库用于本体调试和 PR 工作，不作为本插件的本地运行环境。
本地只进行插件代码开发与静态检查；集成验证在实际部署的 AstrBot 环境中进行。

## 安装与集成验证

在实际运行的 AstrBot WebUI 中，通过插件管理使用本仓库地址安装：

```text
https://github.com/yomihime/astrbot_plugin_yomihime_game_link
```

确认插件加载后，向 Bot 发送 `/ygl`，应返回插件名称与开发状态。
更新代码后，在部署环境更新插件并重载，再次验证命令。
实际命令前缀、唤醒条件和平台接入以 AstrBot 配置为准。

当前尚未完成真实 AstrBot 环境的加载及消息收发验证，也未确定最低兼容版本。

## 本地检查

准备 Python 3.12 或以上与 Ruff，在插件目录运行：

```powershell
ruff format .
ruff check .
python -m compileall -q main.py
```

当前没有额外运行依赖。后续引入第三方库时，通过 `requirements.txt` 声明。
插件依赖 AstrBot 运行时，不应直接执行 `python main.py`。

## 开发约定

- 游戏业务按实际需求逐步拆分为模块。
- 持久化账号与缓存放在 AstrBot 数据目录，不写入插件源码目录。
- API 凭据通过 AstrBot 插件配置管理，不提交到仓库。
- 网络请求使用异步客户端，并处理超时、限流与上游错误。

## 来源与许可

基于 [AstrBot 官方插件开发指南](https://docs.astrbot.app/dev/star/plugin-new.html) 推荐的 [Soulter/helloworld](https://github.com/Soulter/helloworld) 模板初始化，保留模板的 AGPL-3.0 许可证，详见 [LICENSE](LICENSE)。
