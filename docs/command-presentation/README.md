# 命令展示改进交接入口

本目录记录管理页紧凑间距、命令图片风格、LLM 事实输出及 FF14 Logs 聚合的设计与实施顺序，供后续本地 Codex 在同一 Draft PR 中继续。

**本次交付只有设计文档和离线可运行视觉原型，没有生产功能补丁。SDK 保持 `0.1.0a6`。下列生产实施任务均未完成。**

基线打包脚本的静态检查表明，[SDK 构建脚本](https://github.com/yomihime/astrbot_plugin_yomihime_game_link/blob/8251a52580b8839964f8f232d45706a98c1fd02d/scripts/build_release.py)只收录既有 SDK 示例，[插件打包清单](https://github.com/yomihime/astrbot_plugin_yomihime_game_link/blob/8251a52580b8839964f8f232d45706a98c1fd02d/scripts/build_dashboard_zip.py)不包含本目录和 `examples/command_cards/`。这不是候选包构建验证，也不是工件字节一致性声明。

- 仓库：`yomihime/astrbot_plugin_yomihime_game_link`
- 代码审查基线：[`8251a52580b8839964f8f232d45706a98c1fd02d`](https://github.com/yomihime/astrbot_plugin_yomihime_game_link/tree/8251a52580b8839964f8f232d45706a98c1fd02d)
- 续作分支：`feat/command-presentation-foundation`

## 从这里开始

1. 阅读 [设计约束](design.md)，区分已定要求、实现提案与待验证事项
2. 按 [实施计划](implementation-plan.md) 的 P0 核对工作区，再只执行当前阶段任务卡
3. 先完成 P1 的 UI、文本帮助和 facts 兼容性；不要因图片链路或 FFLogs 元数据未决而搁置它
4. P2 依照 [FFLogs 探查清单](fflogs-discovery.md) 只读核验；结果不足时保留缺口
5. 查看并运行 [图片原型](../../examples/command_cards/README.md)，再进入 P3；P4 必须等目录规则有证据后才开始
6. 每阶段报告改动、验证结果与未决问题；提交继续留在本 Draft PR，不自动合并或发布

## 要保留的决定

- UI 先修紧贴位置；提示到动作约 8px、组间约 12px，不重做导航
- 人类命令可以亲切简短；LLM 只收结构化事实、单位、覆盖范围、来源和下一步
- 所有命令图片统一为暖桃色纸感风格；物品卡的具体版式尚未设计确认
- 总帮助使用完整品牌名，列出所有活跃可用模块，再随机推荐一个入口
- Logs 使用一张长卡、一次主标题、绝境战与零式两组；展示范围由地区发布状态决定
- 不维护副本短名表或翻译表；优先上游本地化名称，缺失时保留原名
- 原型中的玩家、服务器与成绩均为明显标注的合成样例，不可作线上数据证据

## 当前最重要的缺口

- 实际 UI 黏连截图尚未提供；目前只定位了三处静态布局疑点
- FFLogs 的 v2 中文选择机制、分类、发布状态、稳定排序和历史重复目录规则尚未验证
- 现有 Host 没有接通图片读取、生成和投递；原型出图不代表产品已支持图片
- rDPS 显示整数已确定；具体舍入采用 `ROUND_HALF_UP` 仍是实现提案

设计与代码基线冲突时，先复核当前代码并记录差异；不要靠聊天上下文补齐隐含要求，也不要将未验证的设想写成已完成功能。
