# 工作包导航

工作包只描述职责，不提供独立派发许可。后续任务、文件所有权和状态统一见 [新计划](../next-stage-plan.md)，当前为 PLANNING。下表是已完成内核收束的职责导航，不是当前活跃 agent 名单。

| 工作域 | 当前负责角色 | 边界 |
| --- | --- | --- |
| 公共契约与内部端口 | C0 | 唯一 canonical API、兼容和消费者合同 |
| 注册/准入/生命周期/作用域 | L | 模块独立性、实例与任务所有权 |
| SQLite/仓储执行 | S | 事务线程归属、迁移、真实持久状态 |
| Gateway/健康/服务绑定 | G | 完整命令权限、Tool 约束、候选服务 |
| 调度/订阅/投递/输出 | D | 共享采集、发送许可和恢复 |
| 激活/管理/本地装配 | A | 组合真实组件，不能复制底层状态 |
| 扩展工厂解析 | E | 惰性扫描后受控授权解析，不自建生命周期 |
| SDK/样例/工件 | K | 实际安装工件与统一类型身份 |
| 纵向集成 | R | 只组合已实现组件，问题回对应 owner |

生产 HTTP、密钥后端、图片、包更新/卸载、AstrBot 与管理页面在[主计划 CLOSEOUT](../development-plan.md)中保留。历史 C00/C10/C11/C20–C71 目录草案及旧分工在[归档](../../.coordination/archive/2026-09-27-pre-hardening/docs/code-architecture/work-packages.md)，不再与当前实现路径混用。

模块工作包通过[模块导航](../module-development-plan.md)进入[新计划](../next-stage-plan.md)。FF14-W1 是历史实现；后续 W2 规划公开角色 Logs、物品/日历/本人投递、配置与 Plugin Pages。Steam/HBR 继续等待原门禁，Dota 保留占位。本轮只整理文档，尚未派发 W2 代码实现。
