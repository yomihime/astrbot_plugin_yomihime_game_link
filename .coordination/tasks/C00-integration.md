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
