# C00-display

模型：Luna。基线 0f0329b，共享当前目录，Sol 审查。
必读：docs/code-architecture/core.md §8；orchestration.md。
只修改：api/display.py、api/results.py、tests/contracts/test_display.py、.coordination/handoffs/C00-display.md。
目标：可导入、明确校验的 DisplayDocument 和 CapabilityResult 契约，不实现渲染器。
公共依赖 api/version.py: CONTRACT_VERSION='1.0.0'。标准库 dataclass/enum，无依赖。
需要：冻结结构和容器保护；text、fields/metrics、table、item_grid、image、series、links/commands 通用块；所有块文本语义或替代文本可表达。数字/金额保留精确值及币种，时间需明确时区；只接受资源 ID 不允许任意路径。privacy 为 public/private。未知必需块拒绝，未知可选块须 fallback_text。使用 Python 具体类联合即可，不要求通用 JSON schema 解析框架。
结果包含协议版本、result_id、状态、document可空、model_facts可空、来源/警告和稳定错误；成功、部分成功、需选择、错误状态须一致。FactDocument 仅公开事实，private 结果不允许返回模型事实，错误消息不含原始对象。定义 ErrorCode 至少 parameter_error/unbound/auth_required/auth_expired/not_found/not_public/no_records/unparsed/rate_limited/upstream_error/module_unavailable/unsupported/unknown。
边界：模块返回结构化数据，不能携带 HTML/CSS 回调；text 作为普通数据不拒绝普通 '<' 文本，由主体后续转义。不新增游戏专属块。
验证：合法网格、未知块/版本、错误状态组合、private 模型事实、精度/时间、冻结容器、非法资源引用。python -m unittest tests.contracts.test_display -v；ruff check 对应文件。
禁止改其他 api 文件、初始化、测试公共配置或依赖。交接列实际公开签名，问题直接反馈统筹，不自行实现主体渲染。无提交。
