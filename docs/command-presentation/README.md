# 命令展示文档入口

本页导航展示合同和可复用样例，不记录安装授权、候选包、逐批验收或本机交付状态。当前 SDK 版本与模块 ABI 见[模块 SDK](../module-sdk.md)；当前通用职责见[代码分层](../code-architecture.md)。

## 规范

- [命令展示设计](design.md)：人类命令、LLM facts、图片与发送回退的职责边界。
- [声明式图片渲染合同](declarative-rendering-spec.md)：SDK DTO、Core 渲染、主题、资源、预算与失败语义。
- [模块展示样式规范](module-display-style-spec.md)：模块评价映射、百分位与字效规则。
- [FFLogs 查询来源合同](fflogs-discovery.md)：目录探查及来源范围。
- [FFLogs 表格合同](../fflogs-table-presentation.md)：历史范围、分区、选优与字段表达。

## 实现与样例

生产页面发送入口位于 Core presentation、命令桥和 AstrBot Host adapter。FF14 业务事实与范围留在 `modules/ff14/`；通用布局和主题留在 `presentation/`。模块只使用公开 SDK 声明。边界与构建入口见[模块维护指南](../module-maintainer-guide.md)和[贡献指南](../../CONTRIBUTING.md)。

`examples/command_cards/` 提供离线布局参考及其 fixture。样例不代表真实来源响应、真实宿主安装、平台投递或完整验收；素材授权和来源以样例 README、NOTICE 与许可证为准。
