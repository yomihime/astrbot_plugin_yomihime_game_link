# 执行看板

基线：0f0329b。2026-09-13 用户授权开始按文档执行并调用子 agent。
当前批次：C00 最小公共契约已验收。所有后续主体/模块任务须逐项核对其编码依赖；完整 C00 尚未全部就绪。
工作模式：共享目录、文件独占；不自动提交或推送。

| 任务 | 实现角色 | 状态 | 可写范围 |
| --- | --- | --- | --- |
| C00-registration | Terra | accepted_minimum | api/manifests.py、api/contexts.py、tests/contracts/test_manifests.py |
| C00-display | Luna，统筹修订 | accepted_minimum | api/display.py、api/results.py、tests/contracts/test_display.py |
| C00-services | Terra，统筹集成修复 | accepted_minimum | api/storage.py、api/subscriptions.py、api/services.py、core/ports.py、专属测试 |
| C00-integration | 统筹 | accepted_minimum | 包初始化、version、上下文签发、参数校验、契约样例、任务/契约记录、共享验证接线 |

审查：各子任务完成后由 Sol 检查，修复后交 Astra 检查本批实际集成结果。不能将 C00 通过等同于游戏查询、数据库或 AstrBot 运行验证完成。

当前实现选型：Python 3.12+ 标准库 dataclass/enum/Protocol，unittest，无新增第三方依赖。协议初始版本 1.0.0；外部可导入命名空间尚未冻结，本地通过相对导入保持同一类型定义。

2026-09-14 续跑：上一轮服务实现/Sol 审查因用量限制中断，用户要求继续后已恢复。Sol 初审发现嵌套可变数据、结果状态/隐私校验及 schema enum 等问题，修复前不冻结契约。Astra 尚未终检。

展示任务再次唤起遭遇 agent thread limit，活动列表中已无 display；其剩余修复由统筹接管。新增统筹所有文件 api/validation.py、tests/contracts/test_validation.py，提供能力参数的实际校验与不可变快照。服务任务仍由 Terra 独占修订。全部 38 个测试及 Ruff check 已通过一轮，最终审查尚未完成。

2026-09-14 最终状态：Sol 与 Astra 均已对本批最小基线给出 PASS。统筹补充注解解析及 Astra DTO 反例修复后，40 个测试、Ruff check/format check、compileall 全通过；审查证据见 [Sol](reviews/C00-sol.md) 与 [Astra](reviews/C00-astra.md)。当前没有提交或推送。

可派发接口和限制以 [C00 基线](contracts/C00-baseline.md) 为准。下一批先补 Collector 的可信调用绑定、调度/订阅清单关联及配置声明；C40 与真实游戏模块接线尚不能按完整依赖就绪派发。C10 静态注册/帮助和 C30 纯文本投影可在各自卡中限定范围后独立执行。
