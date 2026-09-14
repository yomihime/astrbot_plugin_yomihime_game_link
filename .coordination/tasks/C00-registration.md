# C00-registration

模型：Terra。基线 0f0329b，共享当前目录。审查 Sol。
必读：docs/code-architecture/core.md §5–§7，orchestration.md。
交付：api/manifests.py 静态声明与校验；api/contexts.py 只读上下文 DTO；对应 unittest。
允许修改：api/manifests.py、api/contexts.py、tests/contracts/test_manifests.py、.coordination/handoffs/C00-registration.md。
禁止：包 __init__、version.py、其他 api 文件、主入口、依赖、代码架构文档。
公共依赖：api/version.py 将提供 CONTRACT_VERSION='1.0.0'。标准库，无外部 schema 库。
结构约定：冻结 dataclass、str Enum；必须严格校验输入值/声明引用，防止 command_only 或 write 能力被 Tool 暴露；保留 help 路由。能力/命令/Tool/模块/包描述具备实际可实例化最小签名、版本与唯一约束。展示 privacy 用字符串 public/private，校验一致。
上下文：InvocationOrigin(command,llm_tool,scheduler,admin)，InvocationView 包含 invocation_id, origin, actor_id可空, conversation_id可空, module_id, module_epoch, registry_revision, deadline可空, parent_id可空；可选 grant_id/grant_revision、subscription_id/subscription_revision 成对校验。只读视图不是授权凭证，签发和身份登记由统筹 core/context_issuer.py 实现，模块不得拿视图字段充当授权证明。
不包含：运行时注册表、真实鉴权、模块业务。未实现字段不要声称已受运行时安全保护。
反例：非法 ID、重复模块/命令、help 冲突、错误引用、write/command_only Tool、非支持版本、bool 充当整数、可变容器修改绕过冻结。
验证：python -m unittest tests.contracts.test_manifests -v；ruff check 指定文件。包初始化由统筹提供。
完成后写交接，列实际签名与未覆盖边界，不提交代码。
