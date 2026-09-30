> 归档于 2026-09-27；分类：`historical_accepted_minimum`；原路径：`.coordination/tasks/C00-integration.md`。
> 正文中的状态、模型、路径白名单及下一动作是历史记录，不再作为当前派发依据。当前执行范围见[内核收束总任务卡](../../../../tasks/core/CORE-HARDENING-01.md)。B05 归档不表示整卡完成；历史审查结论不变。

# C00-integration

归属统筹，审查 Sol，终检 Astra。基线 0f0329b。
允许：根包初始化、api/__init__.py 与 version.py、core/__init__.py、core/context_issuer.py、tests 包初始化和 test_context_issuer.py、examples/contracts.py、契约及协作记录。
续跑补充：api/validation.py、tests/contracts/test_validation.py、test_examples.py、README 本地验证入口；原 display agent 不再可唤起后，统筹接管其稳定文件的剩余 Sol 修复。
目标：将 C00 子任务整合成单一可导入包，提供可信上下文的最小签发边界与跨文件样例；不更改 main.py 用户可见行为。
设计决定：测试用 ygl_test_subject 包别名模拟宿主包加载，不冻结第三方公开导入名；生产相对导入。协议 1.0.0 为本地初始版本，真实注册器、存储和网络实现另行任务。
输入：其他 C00 模块的实际类型；输出：可运行合法清单/展示样例和伪造来源、过期、父撤销的验证。
权限：上下文视图本身不可授权，ContextIssuer 以精确对象身份登记并验证祖先。host bridge 在签发前鉴权；发布者不进入 ModuleServices。无恶意同进程沙箱承诺。
完成依据：所有契约测试及 Ruff 通过，样例能在同一包类型系统构造；Sol/Astra 对实际文件给出结论。
未验证：AstrBot 集成、消息发送、真实外部接口、3.12 运行环境（本地 Python 3.13.9）。

## B05 SDK identity 迁移例外（实施待窄审）

仅为 B05 SDK identity 迁移，本卡追加的精确 canonical 声明叶白名单为：`yomihime_sdk/api/__init__.py`、`yomihime_sdk/api/validation.py`。本卡是这两叶的唯一写入 owner。此项仅对上述路径覆盖本卡原有路径限制；必须先对这两个精确路径分别完成独立窄审，之后才可写代码。窄审通过前不构成派发或实现授权。只迁移本卡原有声明语义，保持字段、签名、校验、导出及运行语义不变；记录 `__module__`/pickle 路径变化并检查持久化和 introspection 消费者。原验收与状态不变。
