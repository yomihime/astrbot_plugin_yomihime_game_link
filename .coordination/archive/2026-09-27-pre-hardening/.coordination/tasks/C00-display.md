> 归档于 2026-09-27；分类：`historical_accepted_minimum`；原路径：`.coordination/tasks/C00-display.md`。
> 正文中的状态、模型、路径白名单及下一动作是历史记录，不再作为当前派发依据。当前执行范围见[内核收束总任务卡](../../../../tasks/core/CORE-HARDENING-01.md)。B05 归档不表示整卡完成；历史审查结论不变。

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

## B05 SDK identity 迁移例外（实施待窄审）

仅为 B05 SDK identity 迁移，本卡追加的精确 canonical 声明叶白名单为：`yomihime_sdk/api/results.py`。本卡是该叶的唯一写入 owner。此项仅对上述路径覆盖本卡原有路径限制；必须先对该精确路径完成独立窄审，之后才可写代码。窄审通过前不构成派发或实现授权。只迁移本卡原有声明语义，保持字段、签名、校验、导出及运行语义不变；记录 `__module__`/pickle 路径变化并检查持久化和 introspection 消费者。原验收与状态不变。
