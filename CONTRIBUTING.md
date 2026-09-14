# 开发指南

## 环境与检查

使用 Python 3.12 或以上版本，并安装 Ruff。在仓库根目录执行：

```bash
ruff check .
ruff format --check .
python -m unittest discover -v
python -m compileall -q api core examples tests main.py
```

需要统一代码格式时运行 `ruff format .`。测试发现应从仓库根目录启动，以正确加载测试包。

插件由 AstrBot 加载，不直接执行 `python main.py`。集成验证需在运行中的 AstrBot 环境中完成；静态检查和契约测试不能代替插件加载、命令调用及消息收发验证。最低兼容 AstrBot 版本尚未确定。

## 代码与设计

- [代码架构](docs/code-architecture.md)：主体与模块的职责边界。
- [契约示例](examples/contracts.py)：清单与结构化结果的最小用法。
- [协作协议](docs/code-architecture/orchestration.md)：任务分工与审查约定。

当前已实现最小公共契约、参数校验及上下文签发，存储、调度、网络与游戏模块尚无完整实现。服务 Protocol 仅定义接口。

## 贡献约定

- 游戏业务放在对应模块，通用接口由主体统一维护。
- 模块返回结构化数据，由主体渲染和发送。
- 运行数据放在 AstrBot 插件数据目录，不写入源码目录。
- 不提交凭据、账号数据、个人目录或环境专属配置。
- 公开说明围绕功能、安装和使用编写；开发过程及验收记录放在 `.coordination/`。
- 接口变更应同步设计文档和相关验证；未完成的能力须明确标注。
