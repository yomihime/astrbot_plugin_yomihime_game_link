# Yomihime Game Link · 怜的游戏连结

AstrBot 游戏信息聚合插件，面向游戏角色、战绩与游戏平台资讯查询。

> 项目开发中。当前仅提供 `/ygl` 插件介绍命令，游戏查询、账号绑定和订阅功能尚未开放。

## 功能规划

| 模块 | 计划支持 |
| --- | --- |
| Dota 2 | 玩家资料、近期战绩、对局详情 |
| FF14 | 角色与 FFLogs 成绩查询 |
| Steam | 平台账号、游戏检索、价格与折扣 |
| 炽焰天穹（HBR） | 账号资料、BOX 与高分挑战 |

## 安装

在 AstrBot WebUI 的插件管理中，使用以下仓库地址安装：

```text
https://github.com/yomihime/astrbot_plugin_yomihime_game_link
```

安装后启用插件。更新时通过插件管理更新并重载。

## 使用

| 命令 | 说明 |
| --- | --- |
| `/ygl` | 查看插件介绍与开发状态 |

命令前缀和唤醒方式以 AstrBot 配置为准。当前版本尚未完成部署环境的集成验证。

## 文档

- [需求说明](docs/requirements.md)
- [基本设计](docs/basic-design.md)
- [代码架构](docs/code-architecture.md)
- [开发指南](CONTRIBUTING.md)

## 反馈

问题反馈与功能建议请提交至 [Issues](https://github.com/yomihime/astrbot_plugin_yomihime_game_link/issues)。

## 许可证

采用 [AGPL-3.0](LICENSE) 许可证。项目基于 AstrBot 官方推荐的
[helloworld 插件模板](https://github.com/Soulter/helloworld) 初始化。
