# 模块开发范围导航

当前后续工作仅以 [后续执行与验收计划](next-stage-plan.md) 为入口。本文不再维护并行派发顺序或重复 FF14 状态；本轮为规划，不执行实现。

| 模块 | 接续规则 |
| --- | --- |
| FF14 | W1 本地实现与审查保留；W2 承接国服角色 Logs、物品、日历/本人推送、长路径、配置与 Plugin Pages。统计网页不进入本期验收。 |
| Steam / HBR | 维持 planned_gated，等待既有 Core Ready 前置及新的明确任务；不随 FF14 计划自动开工。 |
| Dota 2 | 等参考插件与范围确认，维持 blocked_reference。 |

模块只依赖唯一 `yomihime_sdk.api`，不自行网络请求、SQL、渲染发送或建立永久调度。管理类操作仍依赖可信主体、权限和效果合同；本人订阅 CRUD 保留私聊命令，页面不冒充聊天 owner。新字段与配置来源不得形成双重权威。

- [模块目录](../.coordination/tasks/modules/README.md)
- [全局开发范围](development-plan.md)
- [Core Ready 合同](../.coordination/contracts/core-ready.md)
- [整理前模块并行计划](../.coordination/archive/2026-09-29-w2-plan/originals/docs/module-development-plan.md)
