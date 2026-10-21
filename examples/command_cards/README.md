# 命令卡片离线样例

本目录提供 SDK 卡片声明、Core renderer 调用样例和固定 PNG fixtures。样例用于检查布局与事实表达，不会请求实时市场或 FFLogs，不代表 AstrBot 安装、实际消息投递或完整来源覆盖。

## 运行

从仓库根目录安装样例依赖并生成离线样图：

```sh
python -m pip install -r examples/command_cards/requirements-declarative.txt
python -m examples.command_cards.declarative_samples
python -m unittest examples.command_cards.test_declarative_samples -v
```

样例使用固定的 Noto 字体资源；准备和校验方式见 [`prepare_test_font.py`](../../scripts/prepare_test_font.py)。输出工具使用新建目录，不覆盖随仓库保存的参考图片。

## 当前规范与样图

- [`declarative_samples.py`](declarative_samples.py) 组合公开 SDK 声明并调用 Core renderer；它不实现独立生产布局。
- [声明式渲染合同](../../docs/command-presentation/declarative-rendering-spec.md)说明 SDK DTO、布局、资源、主题、预算和失败语义；[展示样式规范](../../docs/command-presentation/module-display-style-spec.md)说明模块评价与 FFLogs 显示规则。
- [`samples/declarative/`](samples/declarative/) 保存帮助、物品、市场和 Logs 参考图以及 [`provenance.json`](samples/declarative/provenance.json)。Logs 数据为合成 fixture；市场图片为示意；物品图片的数据来自记录在 provenance 中的展示来源。
- [`samples/style_reference_checks.json`](samples/style_reference_checks.json)记录样式参考检查。生成图不能替代上游查询或平台发送验证。

图片中的第三方内容、字体与标识各自保留来源、版权和许可证；仓库样例代码按项目 AGPL-3.0 授权。查看 [Noto 字体 NOTICE](../../presentation/assets/fonts/NOTICE.md)、[OFL](../../presentation/assets/fonts/OFL.txt) 和 [许可证索引](../../NOTICE)。样图或代码的许可不自动扩展为上游游戏数据、素材或商标的再分发授权。
