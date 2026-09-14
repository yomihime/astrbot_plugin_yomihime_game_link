# C00-services

实现 Terra，审查 Sol，基线 0f0329b + 本批 C00 文件。只修改 api/storage.py、api/subscriptions.py、api/services.py、core/ports.py、tests/contracts/test_services.py、.coordination/handoffs/C00-services.md。
依赖：api/contexts.py InvocationView、api/results.py CapabilityResult、api/manifests.py 描述类由其他任务交付。先读代码/联系父 agent 确认名字，不改其文件。相对导入，不在模块中直接 import 顶层 api。
目标：标准库 Protocol 与类型化 DTO 实现存储、账号服务、采集/订阅和主体内部端口可导入契约；不写真实数据库/网络/游戏业务。
包含：模块工厂/实例/处理器映射和 ModuleServices 协议，受作用域限制 ConfigView/IdentityResolver/AccountOperations/SubscriptionOperations/ModuleRecords/CacheAccess/SourceHttp/ResourceAccess/DependencyInvoker/TaskScope；避免任意 SQL/发送/换 origin API。集合 owner scope+revision，Grant 引用及版本；采集规范键输入、Observation 完整性、EvaluationDecision、周期限制、通知范围；主体内部 MessagePort/仓储最小事务/鉴权/结果出口协议。
只定义具有明确消费者的最小接口，不堆全量未实现仓储类。签名可先用 Protocol forward references；不要 Any 混掉安全关键字段，公开/私人 scope 明确，私人必须 user+grant revision。
验证合法构造和非法参数/修订/范围，类型注解可解析，factory 假实现沿公共协议接线。观察 payload 是结构化 JSON 数据快照，转换冻结防止后续修改；订阅 matcher 不执行 IO。
不要自行颁发可信权限。调用参数 InvocationView 是只读视图，主体签发服务以后实现并检查身份。未实现的服务用 Protocol 明确表达，不用假成功实现。
只用标准库，python -m unittest tests.contracts.test_services -v；ruff check 对应文件。完成后交接列真实导出类和边界，无 git 提交。
