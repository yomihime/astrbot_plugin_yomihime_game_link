# 游戏连结：离线命令卡片视觉原型

这份可重复运行的 Python 样例用于评审版式与数据展示语义。默认采用已选定的「暖桃纸」配色，另保留两个明确命名的配色预设。**不是插件功能实现**：没有更改 Host、API、模型、SDK、真实模块加载器或消息发送链路，也不查询实时市场或 FFLogs。

## 先看样张

| 样张 | 尺寸 | 内容 |
| --- | --- | --- |
| [市场卡](samples/01_market_warm_paper.png) | 920 × 800 | NQ / HQ 横向价格行与来源限制 |
| [总帮助卡](samples/02_help_warm_paper.png) | 920 × 800 | 当前单模块示例、推荐开场白与帮助命令 |
| [单张 Logs 长卡](samples/03_logs_warm_paper.png) | 1040 × 1993 | 两组、共 20 行合成成绩 |
| [三配色对照](samples/00_palette_comparison.png) | 1736 × 1020 | 暖桃纸、轻柔紫、浅薄荷，同一数据与几何 |

四张 PNG 均小于 1 MB。三张主样张已目视检查。图像由 Pillow 绘制文字与形状、本地 SVG 转换器绘制模块图标，未使用图像生成模型。

## 文件与依赖

- `renderer.py`：市场 / 帮助卡，另提供共用 Canvas、配色、字体发现、可信 SVG 转换与输出工具
- `render_logs.py`：Logs 数据结构、两种数字格式化函数、动态高度长卡与合成数据
- `test_renderers.py`：离线单元与布局测试；测试只在临时目录写文件
- `requirements.txt`：仅此样例的 Pillow 依赖，不应合并进插件根运行时依赖
- `assets/ff14-meteor.svg`：三种卡片共用的单色 SVG；来源与限制见同目录 NOTES 文件
- `samples/`：用于评审的 PNG；不是产品静态资源或用户数据

需要 Python 3.10+、Pillow、覆盖中文的字体，以及 **CairoSVG 或 Inkscape 至少一种** SVG 转换器。脚本优先使用 CairoSVG；未安装时，使用 PATH 上已有的 Inkscape。两者均无时会说明缺失依赖。脚本不会自动安装软件、下载字体或联网。

```sh
cd examples/command_cards
python -m pip install -r requirements.txt
# 如没有可用的 Inkscape，可另行安装 CairoSVG；部分系统还需其原生 Cairo 依赖
python -m pip install CairoSVG
python renderer.py --seed 14
python render_logs.py
python -m unittest -v test_renderers.py
```

安装转换器是使用者自行选择的环境准备步骤；示例并未固定生产渲染栈。已经安装 Inkscape 时，不需要执行 CairoSVG 安装命令。验证环境使用 Python 3.12、Pillow 12.3 与 Inkscape 后备转换；Windows 字体发现与其他转换器路径尚未实机验证。

### 字体可移植性

不附带字体文件，也不声明任何字体许可。默认在 Linux、Windows、macOS 的常见系统及用户字体目录中查找 Noto Sans CJK SC / Noto Sans SC。若无匹配字体，传入已有的中文字体路径即可；不要求固定开发机目录。

```sh
python renderer.py --font ./fonts/NotoSansCJKsc-Regular.otf --bold-font ./fonts/NotoSansCJKsc-Bold.otf --seed 14
python render_logs.py --font ./fonts/NotoSansCJKsc-Regular.otf
```

`--bold-font` 可省略，此时复用常规字体。TTC 集合默认尝试查找简体中文 face，也可用 `--font-index 2` 显式指定；独立 OTF / TTF 通常为 0。自带字体应覆盖中文及「犎」字，更换字体后需要重新验图。字体路径仅由使用者在本机选择，不随源文件提交。

### 输出与重复运行

不传 `--output-dir` 时，每次在当前目录的 `command-card-output/run-*` 新目录中输出文件。可以显式选择目录：

```sh
python renderer.py --output-dir ./preview --seed 14 --compare
python render_logs.py --output-dir ./preview
```

脚本以独占创建方式写入 PNG；如果同名文件已存在，会报 `FileExistsError`，不会覆盖。要再次渲染请换一个新目录。不要把 `samples/` 当作默认输出目录。导入模块不会自动渲染、打印或建立输出目录。

`--palette` 可取 `warm_paper`（默认）、`soft_lilac`、`cool_mint`。`--compare` 额外产生一张市场 / 帮助三配色对照；配色不会随机变化。Logs 默认始终输出一张包含两个分组的完整长卡，没有分页或每组单独图片。

## 样例语义与真实接入边界

### 帮助：筛选实际可用模块，随机推荐一次

`build_help_data(modules, seed=None)` 从调用方传入的模块中筛选 `loaded and enabled and available`，按 key 去重，然后随机选择一次。开场白使用 `display_name`；同一请求后续渲染和切换配色复用结果，不重新选择。固定 `seed` 只用于可重复样例或测试，正常请求可省略。

示例注册表只有 FF14；零模块时展示空状态，不推荐未加载模块，也不输出不可用命令。卡片中的功能名称是静态文字，不是可点击按钮。

**当前布局只展示一次推荐的模块，单模块样例恰好也是完整列表。真实多模块总帮助仍应列出全部本次 active 模块，再独立提供随机推荐；这个多模块列表布局尚未实现。** 接入时必须来自真实加载器状态，不能把未来模块预登记为可用。

### 市场：价格与上传时间均为演示

犎牛牛排 / 44091、中国全区、NQ 30 Gil、HQ 500 Gil、世界与上传时间均用作静态布局示例，没有发生实时查询。「最低价」仅表示 Universalis 有限返回样本中的最低单价，不是全量市场扫描，不保证此刻仍可买到。显示的时间是对应世界的上传时间，不是单条挂单创建时间。

### Logs：两组长卡，全部数据合成

角色固定为「示例冒险者 @ 示例服务器」。7 条绝境战与 13 条零式记录的百分位、rDPS **全部为新造的演示值**，没有真实角色成绩。品牌「如月怜的游戏连结」保留为产品标题。

副本名称和「7.0 · 零式阿卡狄亚登天斗技场」标题仅是固定排版 fixture，包含阶段分行；它们不声明当前版本、地区开放状态或实时完整副本目录，不能当作生产目录或人工名称映射表。生产目录发现、地区可用性与命名来源由外部适配层另行实现。

- `highest_rdps`：保留原始高精度 `Decimal`，仅显示时 `ROUND_HALF_UP` 舍入成整数并加千位逗号，例如 `41234.567890123 → 41,235`
- `best_percentile`：原始值保留，显示时用 `ROUND_HALF_UP` 固定一位小数，例如 `44.65 → 44.7%`
- 最高 rDPS 与最佳百分位独立取值，可能来自不同场次；不能把后者对应场次的 rDPS 冒充最高 rDPS
- 本例 `job` 与 `version` 属于最佳百分位记录；最高 rDPS 的来源职业 / 场次未在卡片逐行展开
- 缺失指标显示破折号，不变成 0；空分组显示「未提供该组记录」，不推断「未通关」
- 高度由传入行数及换行计算，没有把 20 行或总高度写死；长副本名最多两行，极端长文本缩小或省略

Canvas 对最终宽、高与像素数设置上限（2048 × 8192 范围内、最多 8,000,000 像素），超限会明确报错；尚未实现生产消息渠道的分页、压缩或附件限制策略。

### SVG 来源与限制

陨石 SVG 是参考 [Square Enix 官方周边图片](https://na.store.square-enix-games.com/final-fantasy-xiv-messenger-bag) 手写重建的近似轮廓，不是官方矢量文件。FINAL FANTASY XIV 及其标识归 Square Enix 所有；本样例不额外声明对该标识的所有权或再分发许可。未包含官方照片。详见 [素材说明](assets/ff14-meteor.NOTES.txt)，扩大使用前需审查适用性。

SVG 使用 `currentColor`，由配色的 `accent` 替换后转换；中空部分保持透明。转换器只面向项目中可信的本地静态素材。元素 / 属性白名单仅检查支持的格式，**不是不可信 SVG 安全沙箱**，不得直接用于任意用户上传文件。Inkscape 后备同样仅供可信资产的离线原型预览。

## 验证范围

```sh
# 在本目录运行
python -m unittest -v test_renderers.py
# 或在仓库根目录运行
python -m unittest discover -s examples/command_cards -p 'test_*.py' -v
```

新增 Python 文件也已按仓库基线 Ruff 0.12.0（Python 3.12、88 列、E4 / E7 / E9 / F / I）格式化并检查。请从仓库根目录运行，保持 import sorting 的项目发现上下文一致：

```sh
ruff check examples/command_cards
ruff format --check examples/command_cards
```

18 项测试覆盖 Decimal 格式与半入边界、原始精度保留、缺失指标独立性、零 / 单 / 多模块选择、不可用状态排除、去重、固定种子、每请求只选择一次、长名称与数字、动态高度、20 行样例、SVG 单色和边缘、格式拒绝、图片尺寸上限、禁止覆盖、纯导入无渲染副作用。它们不替代 API 集成测试、真实模块加载器测试、跨平台字体测试或实际消息渠道验收。
