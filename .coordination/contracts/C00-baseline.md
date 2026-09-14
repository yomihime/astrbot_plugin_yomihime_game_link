# C00 本地契约基线

状态：2026-09-14 经 [Sol](../reviews/C00-sol.md) 与 [Astra](../reviews/C00-astra.md) 审查通过，仅冻结以下最小批次。版本 `1.0.0` 是本地初始版本，尚非可发布的第三方扩展 SDK。

## 已有接口与入口

| 文件 | 实际交付 | 消费者 |
| --- | --- | --- |
| api/manifests.py | 包、模块、能力、命令、Tool 静态描述及声明校验 | C10 注册与帮助，C60 清单加载 |
| api/contexts.py、core/context_issuer.py | 来源视图、主体签发、继承、撤销与截止时间检查 | C10 调用、C11 依赖、C70 桥接 |
| api/validation.py | validate_parameters(capability, parameters)，封闭 schema 类型/范围验证，返回不可变对象 | C10 调用入口 |
| api/display.py、api/results.py | 通用展示块、精确数值、隐私标记、类型化结果/错误、模型事实 | C30 展示，模块展示映射 |
| api/storage.py | JSON 快照、公开/用户/授权范围、版本记录、集合与缓存协议 | C20/C21 前置接口 |
| api/services.py | 模块工厂/实例、能力健康、账号操作、按调用绑定的服务协议 | C11 及模块工厂 |
| api/subscriptions.py | 采集键、观察完整性、订阅视图、同步匹配结果、周期限制及描述 DTO | C40 前置接口 |
| core/ports.py | 主体消息、身份、最小事务/仓储及出口协议 | 后续主体适配实现 |

`examples/contracts.py` 提供最小清单及结果样例。测试通过根包的 `ygl_test_subject` 别名验证类型身份；生产代码使用相对导入。该别名只存在于测试环境，第三方正式导入名及扩展加载方式由 C60/C70 确定。

能力输入是封闭 object，支持嵌套 object/array、string/integer/number/boolean、枚举、数值上下界、文本长度。没有默认类型转换、额外字段、nullable、$ref 或全量 JSON Schema。参数校验不授予权限。

InvocationView 不是凭据。ContextIssuer 验证由同一主体签发的原始对象及祖先；绑定服务实现仍需校验当前 epoch、注册版本、授权版本、订阅版本和对象范围。签发器不能注入模块服务。协议并不提供同进程恶意代码沙箱。

模块工厂收到 ModuleServices，处理器按当前 InvocationView 调用 scopes.bind 获取 InvocationServices；具体实现须在操作时重验有效性，并禁止把绑定句柄复用于其他调用。当前只有该接口，尚无实现，因此不能宣称用户隔离运行验证通过。

展示内容只有数据与资源 ID。主体负责渲染及发送；资源实际可见性须由资源服务重验，模块自报 public 并非授权依据。private 结果不得携带模型事实。选择结果必须携带候选展示文档，错误结果只携带稳定错误详情；事件时间戳要求带时区 datetime。

## 本批完成边界与下一步门槛

本批是 C00 的最小契约交付，不能把整个 C00 或全部下游工作包标成 ready。

- C10 的静态注册、两级帮助、纯参数校验可依据已审查接口派发；运行时策略仍需完成身份/授权和生命周期接线。
- C30 的协议文本投影可依据展示契约派发；图片渲染和真实资源读取尚不可验收。
- 调度与订阅描述目前独立于 ModuleManifest；C00 后续须补清单注册关联、周期默认值/配置项及完整通知配置，再开放 C40 真实调度实现。
- Collector.collect 的 CollectionView 尚不携带可信调用上下文，无法直接通过 scopes.bind 使用网络等服务。采集侧绑定签名须在 C00 后续补齐；当前 Collector 是待接线契约草案，不可据此派发真实采集器。
- 配置/来源/集合声明、账号登录状态模型、管理 DTO、完整事务仓储及投递记录接口仍需按后续消费者补齐。最小 UnitOfWork 协议不代表 SQLite 仓储设计已经可全部派发。
- 正式扩展加载、Dota/Steam 共享公开身份语义和模块外部来源证据尚未冻结；不让模块各自发明接口。
- 当前无真实数据库、网络采集、账号授权、调度器、渲染器、游戏模块实现。本批不改变 main.py 的用户可见命令。

## 验证入口

```powershell
python -m unittest discover -v
ruff check .
ruff format --check .
python -m compileall -q api core examples tests main.py
```

本机 Python 3.13.9；3.12 为目标最低版本，未运行真实 3.12 测试。AstrBot 加载和消息收发须在部署环境完成。

最终验证：40 项 unittest 通过，Ruff 检查、格式检查与 compileall 通过。Astra 独立探针发现的记录分页成员、采集视图键和 HTTP 状态码类型问题已修复并经两轮审查复核。
