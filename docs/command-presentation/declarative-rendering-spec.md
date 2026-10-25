# 声明式图片渲染契约与四类设计样例

模块使用公开 SDK 声明，Core 统一渲染，Host 负责平台授权和消息发送。本文描述当前代码合同和设计约束；SDK 版本以[模块 SDK](../module-sdk.md)为准，当前文档索引见[docs/README](../README.md)。历史候选、逐批测试与本机交付记录不作为当前安装指导。

## 1. 交付范围与职责

模块只提交内容、结构、受限样式和已取得的静态素材。共用 Core 完成测量、换行、版式、字体、颜色、SVG/位图合成及 PNG 编码。例子不能再用自己的 Pillow、Canvas、SVG 转换器或旧 renderer 画一套“接近生产”的图片。

- **Module**：事实查询、数据来源、可用性、字段含义、数字格式、百分位/评价映射、内容顺序、结构组合、图片资源。不得借渲染器推断业务结论
- **Core presentation**：通用声明结构、固定主题槽、受限标签解析、可信静态素材处理、测量与绘制、已有 A/B 字效、预算及明确失败。不得包含 FF14 物品查询、FFLogs 阈值或名称表
- **Host**：渠道能力、权限、发送、提交状态及纯文本回退。Core 渲染函数不发消息，也不判断消息是否已送达
- **SDK**：`yomihime_game_link_sdk.cards` 提供冻结纯数据定义，`DisplayDocument.card` 承载可选卡片，`ModuleDisplay.logo_resource` 声明模块资源。当前 SDK、模块 ABI 与 Display schema 版本见[模块 SDK 合同](../module-sdk.md)。SDK 不依赖 Core、Host、Pillow、素材解码器或标签 parser
- **LLM**：继续使用同一事实生成的现有结构化结果，不增加图片、品牌随机文案或样式标签。展示转换不得改动事实模型

模块使用 SDK 声明；`presentation/layout_document.py` 仅重导出同一组 SDK 类型。Core 的 `layout_renderer.py`、`layout_assets.py`、`layout_markup.py` 负责实现。`CommandCardRenderer` 将 `DisplayDocument.card` 交给该 renderer，未提供 card 的既有文档继续走旧投影；`ordered_blocks` 保留完整确定性文本回退。以下分别记录源码实现、离线证据和待实测项，不把它们合并为生产验收。

## 2. SDK 声明与 Core 实现入口

以下冻结声明属于 SDK 的公开 cards 合同。模块只导入 `yomihime_game_link_sdk.cards`；渲染、测量和主题目录函数属于 Core，不是 SDK 功能。

```python
Content = str | Inline | TaggedText
Block = Text | Section | Row | PropertyGrid | Table | Image

Span(text, color=None, emphasis="normal")
Inline(spans: tuple[Span, ...])
TaggedText(source: str)  # 显式请求 Core 解析受限标签

ThemePalette(outside, surface, panel, panel_alt, border,
             text, secondary, title, accent)
ThemeOption(id: str, label: str, palette: ThemePalette)

Document(title, blocks=(), subtitle="", brand="游戏连结", logo=None,
         theme="warm_paper", layout="detail", sources=(),
         powered_by="Powered by yomihime/astrbot_plugin_yomihime_game_link",
         width=1040)
Text(content, style="body", align="left", wrap="wrap")
Section(title, blocks=(), subtitle="", panel=True)
Row(cells, weights=(), valign="center")
Property(label, value)
PropertyGrid(properties, columns=2)
Column(title, weight=1, align="left", valign="center", wrap="wrap")
Cell(content, align="left", valign="top")
Table(columns, rows)  # 每格为 Content 或 Cell
Image(asset, width=None, height=160, align="left", caption="", fit="contain")

Asset(kind, content, alt="", required=True)

render_document(document, *, bounds=BitmapBounds(), font_provider=None)
    -> RenderedBitmap(content: bytes, width: int, height: int,
                      media_type="image/png")
measure_document(document, *, bounds=BitmapBounds(), font_provider=None)
    -> MeasuredDocument(width, height, boxes)
list_themes() -> tuple[ThemeOption, ...]
apply_theme_selection(document, core_theme: str,
                      module_theme: str | ThemePalette | None = None) -> Document
```

`DisplayDocument.card: cards.Document | None = None` 是附加展示，不替代原有 `ordered_blocks`、隐私或事实结果。`ModuleDisplay.logo_resource: str | None = None` 只声明资源身份，不授予读取权限。公开 Content 仅有纯 `str` / `Inline` / `TaggedText`，单元格覆盖使用 `Cell`；Core 仍可兼容既有 `RichText` / `TextCell`，这些内部类型不泄漏到 SDK，也不要求模块导入 Core。

所有声明为冻结数据对象；集合字段使用 tuple。构造对象本身不意味着输入已通过边界检查，完整树在渲染/测量入口校验。未知类型、字段取值或错误嵌套不能作为“尽量画出来”的理由。

### 2.1 版式、容器与对齐

- `layout` 是 `directory`、`detail`、`table` 三种通用间距预设，不是游戏类型。Module 仍用相同容器表达实际内容
- `Text.style` 为 `title`、`heading`、`body`、`caption`、`stat`；字号由 Core 定义。没有任意 CSS、绝对坐标或字体路径
- `strong`/`b` 在本版是标题色的语义强调，不是真粗体字重。固定字体只有 Regular，不合成假粗体。将来如需粗体，须另行核验 FontProvider 与字体资源，不能把标签名当作已有粗体证据
- `Text.align` 控制文字在自身文本区域内的左/中/右对齐；`wrap="wrap"` 完整换行，`nowrap` 超宽明确失败，不缩字或截断
- `Row.weights` 是正的相对列宽，省略则等分；`Row.valign` 控制整块子内容在该行中的 `top`/`center`/`bottom` 位置
- `Column.wrap="nowrap"` 可明确禁止该列数据换行（表头仍可换行）；数字列由 Module 主动选择，不由 Core 猜测字段业务。超长数据明确失败
- `Column.align` 和 `Column.valign` 控制表格列的文字及单元格内垂直位置，跨多行副本名仍按整个单元格高度处理。可用 SDK `Cell(content, align, valign)` 明确覆盖单元格对齐
- 文字居中与容器居中是不同层次。垂直居中按实际字形范围校验，不能用某字体的估算基线代替；右对齐也不能只按字符串长度估计
- `PropertyGrid` 用于紧凑属性组。材料清单可以用两列 `Row(Text(...), Text(...))`，不必把每个材料名降成细小属性标签
- `Image.align` 控制素材盒的位置；`fit="contain"` 保持比例完整显示，`cover` 保持比例覆盖盒并裁边。正文事实不裁剪；图片裁边必须由调用方明确选择
- 表格数据行按 `panel` / `panel_alt` 交替整行背景，表头用 `panel_alt`。不按分数改变单元格底色，不做绿/金色专属深底徽章
- `measure_document` 的带路径边界用于测试/诊断，不是模块自己画图的坐标 API；测量成功也不保证解码、PNG 字节预算或 Host 发送成功

## 3. 主题与文字样式

### 3.1 固定槽，既支持预设也支持自定义

SDK 声明及直接 `render_document` 接受预设名 `warm_paper`、`cool_white`、`deep_indigo`，也接受完整 `ThemePalette`。自定义主题不是“把预设名字换掉”；九个颜色槽都必须提供合法 `#rrggbb`。正式 Host 命令另按下节的用户设置解析主题并覆盖 `Document.theme`，模块不能用文档中的自定义色板绕过用户选择。

| 槽 | 含义 |
| --- | --- |
| outside | 卡片外侧底色 |
| surface | 主卡底色 |
| panel | 内容面板、表格数据行底色 |
| panel_alt | 表头、备用素材占位等辅助面板 |
| border | 边界与分隔线 |
| text | 普通文字，包括缺失和不可评价值 |
| secondary | 次要说明 |
| title | 标题和 strong 文字 |
| accent | 品牌装饰、emphasis 和单色 SVG 的 currentColor |

暖桃纸继续作为物品卡方向；Logs 同时提供浅/深版本，不把深色或冷白单方宣布为默认。现有精确色值、间距和字号是实现选择，仍可根据样张评审调整。主题不会改写明确的 span 颜色或 FFLogs 等级色。

### 3.2 Core 默认主题与模块覆盖

用户确认两级配置：**Core 配置选择主题，模块颜色选择器提供 `跟随核心` 或显式覆盖**。Core 自身卡片（包括 `/ygl help`）使用 Core 配色。Core 与模块设置由各自配置字段、owner 和 revision/CAS 维护；管理与配置操作受当前 Host 授权合同约束。代码与离线测试不代表具体实例的部署或验收。

- 全局设置中的 `回复样式 → 图片主题` 对应 Core `image_theme`，默认 `warm_paper`（暖桃纸）。保存一个有效 Core 预设 ID；不能从传入文档的默认主题猜出配置
- 模块同名设置由 Core 通用注入 `reply_theme`，不要求每个模块重复声明字段。首项及默认模式为 `跟随核心`，运行快照对应 `module_theme=None`；其余选项是 Core 目录里的预设。选择跟随核心使用 `CLEAR` 删除覆盖，未存储才表示继承；显式存储 null、空字符串或未知 ID 不合法。它不是新色板，也不是全局空主题
- 两处预设均从 `list_themes() -> tuple[ThemeOption, ...]` 取得；冻结的 `ThemeOption(id, label, palette)` 提供 ID、显示名称和预览色板，目录与渲染器共用同一份不可变配色源。后端以此生成页面 choices，前端不复制预设列表；显示名为暖桃纸、冷白、深靛。目录读取与选择转换不触发字体/Pillow I/O
- `apply_theme_selection(document, core_theme, module_theme=None)` 先验证两级配置。`None` 使用 `core_theme`，**不会保留输入 `document.theme`**；显式模块预设或合法 `ThemePalette` 只覆盖本次模块文档，不改全局设置、Core 帮助或其他模块
- Core 卡片只传 `core_theme`，不传模块覆盖。模块卡片传入该模块获授权的设置快照；没有覆盖时传 `None`。纯函数不负责认证、配置读取/保存、数据库迁移或消息发送
- 未知 Core ID、未知模块 ID、非法类型或格式错误的自定义配色均抛 `BitmapUnavailable("invalid_input")`，不静默回退。即使模块有覆盖，Core 配置仍须有效；空字符串不代替 `None`
- 模块 UI 是 `跟随核心 + Core 预设` 下拉框，不包含任意颜色编辑器。完整 `ThemePalette` 只在直接 renderer 或纯 `apply_theme_selection` 函数收到受控显式参数时可用；当前正式持久化/管理页只接受目录 ID 或清除覆盖。OutputService 使用该设置快照覆盖 `Document.theme`，不会读取模块文档色板作为隐藏配置。来源业务文本不能变成配置权限；SDK 仍只承载纯数据定义
- 设置复用 `config_entries` / `config_state`、现有合法管理入口、授权检查和 revision/CAS 保存规则，无新增 SQL 存储。Core 恢复默认清除配置并回到暖桃纸，模块恢复默认清除覆盖并继承 Core。进入、读取、切走再返回及从基线继承切换后再选继承不会制造脏草稿。主题专用写入不进入模块业务健康字段，但继续递增共用配置 revision
- `ThemeSettingsView.current(module_id)` 在同一闸门内读取 `core_theme`、`module_theme` 和两级 revision；Core 帮助使用 `current()`。OutputService 每次公共命令图片使用一次该快照，按上述优先级解析实际主题，再绘制整张图片；不会在绘制途中反复读取设置
- 若启用渲染缓存，key 必须纳入**解析后的实际主题**（包括自定义槽值或稳定摘要），不能只包含模块名或可变的 `跟随核心` 标签
- 设置只作用于之后生成的图片，不改写历史消息/旧 PNG；文字回退、业务事实和 LLM 结构化结果保持不变。显式颜色、FFLogs 等级字面色和金光资格不受主题选择影响；B 按最终实际背景决定

Core 纯函数调用示例（实际命令由 OutputService 传入受控设置快照，模块不自行读取数据库）：

```python
from presentation.layout_renderer import list_themes, apply_theme_selection, render_document
from examples.command_cards.declarative_samples import build_help_document, build_item_document

options = list_themes()  # 两处 UI 预设共用 option.id / option.label / option.palette
core_theme = "cool_white"  # 来自本次已取得的有效 Core 设置快照
module_theme = None  # 此模块选择“跟随核心”；显式 ID 才覆盖该模块

help_document = apply_theme_selection(build_help_document(seed=14), core_theme)
item_document = apply_theme_selection(build_item_document(), core_theme, module_theme)
help_image = render_document(help_document)
item_image = render_document(item_document)
```

已确认的独立样例仍直接渲染其示范主题，包含 Logs 浅/深对照，不代表已读取部署用户的实际设置；真实命令按实际事实与设置生成，固定 fixture 不随部署设置改写。

### 3.3 纯文本、显式 Span 与标签

普通 `str` 永远是纯文本。例如物品名含 `[gold]` 时，直接传入 `Text(name)` 只能显示原样。模块可以选择显式结构，或提交 SDK `TaggedText` 主动请求 Core 解析；不需要也不应导入 Core parser：

```python
from yomihime_game_link_sdk.cards import Inline, Span, TaggedText, Text

source_name = "名称 [含方括号]"
explicit = Text(Inline((
    Span("物品："),
    Span(source_name, emphasis="strong"),  # 外部文字直接作为字面内容
)))
tagged = Text(TaggedText("[strong]说明[/strong]：由 Core 统一排版"))
```

外部文字优先使用普通 `str` 或 `Inline` / `Span`。只有明确要插入 TaggedText 时，先依次把反斜杠、左方括号、右方括号转义为 `\\`、`\[`、`\]`；不要把未经处理的名称拼进标签源码。SDK 只携带源码，实际解析和预算检查在 Core。

当前实现支持的标签语法（待评审）：

- `[color=#rrggbb]文字[/color]`：六位十六进制颜色；没有 RGB 函数、CSS 或背景属性
- `[strong]`、`[secondary]`、`[emphasis]` 及各自同名关闭标签；`[b]...[/b]` 是 strong 别名
- `[gray]`、`[green]`、`[blue]`、`[purple]`、`[orange]`、`[pink]`、`[gold]` 是固定字面色别名，用同名标签关闭。它们不自动生成业务等级证据
- `[gold]金色[/gold]` 只是金色文字；`[gold glow]满分[/gold]` 明确请求既有 A 金光，固定字面 `#e5cc80`。显式结构对应 `Span(..., emphasis="gold")`，不能再附自选颜色
- 嵌套必须严格配对；内层样式覆盖相应外层样式，关闭后恢复。金光与显式颜色不能互相嵌套，防止“任意颜色 + 金光”绕开固定契约
- `\[`、`\]`、`\\` 表示字面方括号和反斜杠；未知转义、裸 `]`、未知标签、未闭合或错序关闭均报错。Core 内部的 `escape_inline_markup` 实现同一转义规则，但不是模块的 SDK 依赖
- 不解析 HTML/XML 实体；`&lt;` 保持字面文本，不变成标签。不要将来源文本先做实体解码再无条件送入 parser
- parser 是单行入口，拒绝控制字符和换行。多段落用多个 `Text`，一般多行文字可以直接用 `str`。空或只有样式而无文字的 parser 输入报错，空字段用普通空字符串

Core parser 的直接调用失败抛 `MarkupError`，内容固定为 `Invalid bounded inline markup`；通过 `TaggedText` 渲染时统一映射为 `BitmapUnavailable("invalid_input")`，不回显不可信原文。不把坏标签静默显示成业务结果，也不执行任何代码。

### 3.4 共用 A/B 字效，不产生第二套实现

沿用 [模块展示样式 Spec](module-display-style-spec.md) 与 `presentation/rich_text.py`：

- B「居中柔影」按实际已绘制底色采样，只有字面/底色对比度 `< 1.5` 才启用；零偏移、原始字形蒙版模糊、不膨胀、不硬描边。所有文字角色都适用
- A「贴字柔光」只为模块明确提交的金色满分样式服务，不从文字 `100`、百分号或颜色自动推断。模块必须先建立来源、比较范围及 MAX 证据
- 全部效果层先绘制，清晰字面最后绘制；相邻 span 的柔光不能染掉前一个字面
- 普通黄色/金色文字没有 A。Raw color 不等于业务 Grade。FFLogs 的 `Grade.GOLD` 可以适配到该固定金光样式；其他语义仍由模块决定

## 4. 静态素材契约

`Asset.kind` 只接受 `svg`、`png`、`jpeg`。PNG/JPEG 只接收 bytes；SVG 接收 UTF-8 bytes 或源码字符串。Core 不接受路径、URL、资源下载指令或网络请求。

模块通过 `ModuleDisplay.logo_resource` 声明已登记资源。当前 Core 使用精确已安装 manifest，经有界、哈希固定的 `capture_page_resources` 取得 bytes；资源权限由可信装配单独授权，展示声明本身不扩权。卡片 `logo=None` 继承该模块图标，显式 SDK `Asset` 优先。帮助也绑定本次目录内模块的同一资源，不设 FF14 图标特判。样例读取既有 `examples/command_cards/assets/ff14-meteor.svg` 后传入 bytes；实际 FF14 资产由模块自身 manifest 登记，模块事实投影没有文件读取。

- SVG 支持 `svg`、`g`、`path`、`rect`、`circle`、`ellipse`、`line`、`polyline`、`polygon` 及受限 transform/fill/stroke/viewBox 属性
- `title`、`desc`、有限无障碍/身份 metadata 可接受但在重序列化时剥离，不参与字体渲染
- `currentColor` 明确替换为本次主题 `accent`，保持透明孔洞；显式填色保持显式颜色
- 不支持 text/font、CSS/style、script/event、动画、foreignObject、filter、use、href/url 引用、外部资源、DTD/entity/declaration。拒绝任何需要联网或执行内容的素材
- PNG/JPEG 限静态单帧、受限尺寸与 bytes；格式不匹配或损坏报错。既有素材颜色/透明度不由业务等级改写
- Core 根运行依赖按 `requirements.txt` 使用 `resvg_py>=0.5.0`（底层 resvg `0.48.1`）和 `Pillow>=12.3.0`；SVG 使用前者的预编译 wheel，位图使用后者。样例环境在独立 requirements 中固定版本。所选 SVG 后端不要求手动安装 Cairo/DLL，不自行安装、不使用 CLI 或在线转换器后备。没有所需依赖时明确报错
- [官方 PyPI 0.5.0 文件](https://pypi.org/project/resvg_py/0.5.0/)包含 Windows `cp310-abi3` 的 amd64/arm64/win32 wheel；[官方安装说明](https://resvg-py.readthedocs.io/en/latest/installation.html)说明不需系统库。发布 wheel 的存在本身不构成目标宿主环境的运行验收
- 该白名单和预算是输入格式护栏，不宣称 native decoder 的安全沙箱。只用于经审查的静态模块资源；不作为任意用户上传 SVG 服务
- `required=True` 的素材失败使整张渲染失败。`required=False` 可在原素材盒里显示有界 `alt` 文字占位；占位也必须测量，装不下仍失败，不能静默删掉原本需要的信息
- `rasterize_asset` 返回调用方负责关闭的 RGBA 图像，不缓存；样例不调用它绘图，只调用顶层 `render_document`

现有陨石是手绘近似轮廓，不是 Square Enix 官方矢量资源。素材来源和权利说明保持 [原 NOTES](../../examples/command_cards/assets/ff14-meteor.NOTES.txt)，未扩大授权声明。

## 5. 限额、错误与发送前回退

预算可降低，不可通过传入更大 `BitmapBounds` 提升硬上限。超限不省略事实、不缩小到不可读字号、不自动分图。

| 类别 | 当前内部硬上限 |
| --- | --- |
| 文档宽/高/像素 | 宽 400–2048；高 8192；最终 8,000,000 像素 |
| PNG bytes | 1,048,576 |
| 输入文字 | 总 24,000 字；单 Content 4096 字；显式输入行 1000 |
| 输出文字 | 换行后 1600 行；测量 60,000 次 |
| 结构 | 深度 8；节点 512；每块列表 128；Section 32；表格总行 200；列最多 8 |
| PropertyGrid | 属性最多 64；列 1–4，并受文档列预算约束 |
| Inline | 每 span 512 字；每 Content 最多 32 span；仍受全文预算约束 |
| 标签 parser | 直接入口源码 8192 字；SDK TaggedText 另受单 Content 4096 字及全文预算约束；可见文字 1024 字；32 span；嵌套 8 |
| 资源 bytes | SVG 131,072；PNG/JPEG 1,048,576；单文档最多 32 个素材、源码合计 8 MiB |
| 资源原始尺寸 | 单边 4096；8,000,000 像素 |
| 素材输出栅格 | 单边 2048；4,000,000 像素；包含内部 2 倍绘制倍率 |
| SVG 复杂度 | 256 元素；深度 16；单 path 32,768 字；path 总 65,536 字 |
| 字效预算 | work 128 MiB；累计 mask 64,000,000 像素；受管字体 224 MiB |

Image 声明 width 为 16–2048 或自动，height 为 16–1024；还须满足容器、素材实际 2 倍栅格和总预算。声明合法不等于在任何剩余宽度都能排下。具体字效 patch、字体覆盖、owned-raster 计量沿用既有 Core 限制，不宣称这些数字是整个 Python 进程 RSS 上限。

顶层失败使用既有 `BitmapUnavailable.reason` 的固定有限集：

- `invalid_input`：类型、颜色、样式、标签、声明或资源格式不合法
- `dependency_missing`：必要渲染/转换依赖不可用
- `font_unavailable`：本次选定的受信字体提供者不可用，包括源码固定字体或正式 Host 的受管/受限系统字体
- `glyph_unavailable`：实际字体覆盖没有确认所需字符
- `layout_overflow`：文本、结构、测量、几何、像素或字效预算超限
- `byte_limit`：资源或输出字节超限

内部异常、路径、上游正文不能拼到公开错误消息里。正式 Host 命令由 Core 注入本节所述的受管字体快照；纯 `render_document(..., font_provider=None)` 与离线样例仍默认使用源码仓库固定字体，测试可显式注入受信 `FontProvider`。字体管理不是 SDK 模块可调用的任意字体路径、网络下载或用户文件解码 API。

当前 `CommandCardRenderer` 先保留同一事实的完整文本，再尝试声明卡。OutputService 仅对公共、无 Grant、非 OWNER 的命令图片使用此路径，经既有 PNG 资源、预算、TTL、取消及一次性发送许可，交给捕获的精确 event。私有/Grant/OWNER、LLM facts 和订阅保留原路径。**仅在发送提交前**因渲染/渠道限制退回文本；图片提交后结果 UNKNOWN 不盲目补发。图文均无法完整发送时明确请求缩小范围。离线测试已覆盖真实 Core/Host 代码和合成上游，不构成真实平台送达证据。

### 5.1 Core 受管字体与轻量发布

正式命令的字体由 `ManagedFont` 管理，独立于插件安装目录和业务模块。SDK 当前版本、ABI、Display schema 与模块声明均不增加字体选择接口。固定标准资源为 Noto Sans CJK SC Regular **2.004 / face 0**：

- 文件名 `NotoSansCJKsc-Regular.otf`，准确大小 **16,437,364 bytes**
- SHA-256 `2c76254f6fc379fddfce0a7e84fb5385bb135d3e399294f6eeb6680d0365b74b`
- 固定上游 revision `523d033d6cb47f4a80c58a35753646f5c3608a78`；[指定字体下载](https://raw.githubusercontent.com/notofonts/noto-cjk/523d033d6cb47f4a80c58a35753646f5c3608a78/Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Regular.otf)，许可与来源见 [NOTICE](../../presentation/assets/fonts/NOTICE.md) / [OFL](../../presentation/assets/fonts/OFL.txt)
- 持久位置由 Core 固定为 `data_dir/resources/fonts/NotoSansCJKsc-Regular.otf`。模块、请求参数和上传文件名不能选择目标路径
- 轻量插件 ZIP 只保留 OFL 与 NOTICE，**不包含 OTF**。源码样例运行前由 `scripts/prepare_test_font.py` 从固定官方来源下载并校验 OTF；字体文件不跟踪入库。出现许可说明不代表运行环境已经取得字体

启动与使用顺序：

1. 运行时启动字体任务后继续 Core 启动，不等待网络下载。已有持久字体须先通过准确大小、SHA 和 Pillow 实际加载验证，之后才发布为标准字体；缺失或不匹配不作为有效缓存使用
2. 标准字体尚不可用时尝试有限、固定的系统字体候选，同时后台取得固定 URL 的标准资源。系统字体只作尽力回退，实际 face 0 的 Unicode cmap 决定支持范围；不能因为文件能打开就声称支持所有中文
3. 每个系统候选最多 16 MiB，超过预算的 TTC 可能被跳过。Core 不递归扫描磁盘、不执行 fontconfig/PATH 命令、不接受模块/用户指定路径。Windows 固定顺序先尝试 `msyh.ttc/msyh.ttf`、`simhei.ttf`、`simsun.ttc`、`simkai.ttf`、`simfang.ttf`，再尝试 Arial；其他平台也仅有代码中的有限固定位置。是否已安装、是否在预算内均需在目标环境确认。系统字体以同一不可变 bytes 解析覆盖并交给 Pillow，不复制进持久字体目录、不重新分发；这不是任意上传字体服务或 native 解码器安全沙箱
4. 正式渲染获取当时的不可变 provider 快照，不逐命令下载或重新读盘。一次渲染不会中途换字体；标准字体下载/上传成功只影响之后开始的图片。系统字体布局不保证与标准字体样图逐字节一致
5. 没有可用字体，或选中字体不覆盖本次文字时，继续既有完整文字回退，不输出方框/缺字图片、不缩减事实。下载成功不会追补历史命令或重发旧图片

下载只允许上述固定 HTTPS 资源，关闭重定向、环境代理和凭据继承；总超时 45 秒，连接/读取各 10 秒，限制响应字节与编码。准确长度和摘要通过后，从同一 bytes 验证可绘制性，写同目录临时文件、flush/fsync 后原子替换，再发布 provider。失败保留仍可用的系统回退；临时/损坏资源不发布。启动检查、文件读写、哈希和 Pillow 验证是后台任务中的有界同步工作，**不宣称磁盘 IO 完全异步或零事件循环耗时**。退出取消受管任务并阻止旧生命周期发布。

### 5.2 Core 设置页：状态、重试与标准文件上传

仅 Core 全局设置的「回复样式」显示「图片字体」，模块设置不复制字体控件。页面读取 Core 返回的文件名、大小、摘要和固定下载链接，不再配置另一份字体来源：

- 「刷新字体状态」读取现状；「重试下载」仅在无活动任务且标准字体未就绪时启动一次下载。已有标准字体或已有任务不会重复取得同一资源
- 「下载指定字体」指向固定上游文件；「上传标准字体」使用现有正式 bridge multipart 上传。前端文件名/大小检查只帮助避免误选，服务端忽略客户端文件名，只接收一个文件且总请求最多 `FONT_SIZE + 8192` bytes，按上述准确大小/SHA 验证。改名不改变资源身份，其他字体或其他版本均拒绝
- `admin/font-status` 使用现有读取授权，`admin/font-retry` / `admin/font-upload` 使用现有管理写授权；继续检查 Dashboard 身份、来源、资源、当前 Core 和生命周期，在 await 后、发布前复查。字体声明或普通配置目录不授予这些权限
- 字体是不可变固定资源，不是普通主题草稿或可选字体配置；**不使用配置 CAS**。状态 `revision` 仅作观察信息，不能当作写入授权。上传加入独立串行发布流程，与下载协调，拒绝旧授权/旧生命周期提交
- 状态字段为 `state=checking/downloading/ready/unavailable/stopped`、`source=canonical/system/none`，安全错误为 `null/download_failed/resource_unavailable`。`unavailable` 可以同时有 `source=system`，意为标准字体取得失败但系统回退仍可用；是否能画本次文字还要实际覆盖检查
- 字体控件独立于主题草稿；取消选文件不提交，重复操作受 busy 门控。只在控件可见且 checking/downloading 时轮询，切页/换上下文丢弃迟到观察。正式 bridge 没有取消已提交上传的接口，**切页不承诺撤销后端上传**；后端仍按授权与生命周期完成或拒绝同一固定资源，不能把前端忽略回执说成已回滚

## 6. 四类内容样例

可运行文件：[declarative_samples.py](../../examples/command_cards/declarative_samples.py)。它仅组合声明和调用共用 renderer，没有 Pillow 或旧参考 renderer import。五张 PNG 表达四个内容样例（Help、Item、Logs 浅/深、Market）：

### 6.1 总帮助

- 完整品牌 `如月怜的游戏连结`，清晰主标题、推荐语 `从最终幻想XIV开始吧`
- 单个 FF14 模块卡、现有陨石、简洁 `/ygl ff14 help` 命令
- 名称、route、命令来自仓库 FF14 manifest；loaded/enabled/available 是明确的离线可用性 fixture，不冒充实际部署状态
- builder 接受调用方 catalog，过滤非 loaded/enabled/available、按 key 去重，列出全部可用模块；未知模块使用自己提供的 summary 或通用说明，不套 FF14 功能
- 推荐在构建文档时随机选择一次，样例 seed 14 保持可复现。换主题/渲染同一声明不重抽；无模块有明确空态
- 实际帮助已由 Core 的本次不可变、过滤后 catalog/推荐快照生成；生产不导入这个离线 catalog、不维护第二套人工注册表

### 6.2 物品详情

- 暖桃纸、突出名称 `犎牛牛排`、右上陨石；属性紧凑显示 `44091` / 物品等级 `684` / 装备等级 `1`
- 任务与制作材料分组；材料两列，保留职业 ID `15`、等级 `99`、记录 ID `70996` 和区域 `图莱尤拉`
- 从用户截图转录任务 `缇姨家的塔可饼的老主顾`，材料 `图拉尔玉米油×1、高山食盐×1、犎牛肩肉×1、龙息椒×1、亚斯拉尼蒜头×2、黑夜醋×1、物品 ID 8×8、物品 ID 13×8`
- 不猜缺失材料名，不未经核验把职业 ID 转成职业名；保留简短 `Garland 制作关联信息不完整` 提示
- 图内来源收敛为一行；完整原始链接保留在样例旁的 provenance.json：`https://xivapi-v2.xivcdn.com/api/sheet/Item/44091` 与 `https://garlandtools.cn/db/#item/44091`
- 数据是截图转录的展示 fixture，不代表实时查询，不能把截图日期说成数据更新时间。生产模块将实际 `ItemRecord` 映射成 SDK 卡片，不把截图事实写入查询结果

### 6.3 Logs 长卡（浅色 + 深色）

样图使用合成角色与排名数据，展示模块交付事实、Core 排版和浅/深主题。它不证明地区目录完整、真实角色查询、上游字段解释或平台投递。

当前 Logs 分组、范围、分区、记录版本、选优与缺失状态的合同见 [FFLogs 表格规范](../fflogs-table-presentation.md)。FFLogs 请求、地区凭据和业务事实由 FF14 模块负责；Core renderer 只排版声明内容。生产规则不由离线卡片 fixture 推导，也不在 Core 中维护 FF14 目录。

样图来源、结构与固定字节见 [`examples/command_cards/samples/declarative/`](../../examples/command_cards/samples/declarative/) 及其 provenance。更新样图时按该来源记录同步更新 fixture 说明，不把合成数据写入生产查询结果。

### 6.4 市场价格卡

- 新增 `build_market_document()`，继承历史市场样张的暖桃纸方向与离线示意数据；与其余样例一样，只组装 `Document` / `Section` / `Row` / `Text`，调用同一 Core renderer
- 品牌 `游戏连结 · FF14 · 市场`、右上陨石、突出 `犎牛牛排`，范围 `物品 44091 · 中国全区`
- 两个清晰 NQ/HQ 面板：NQ `30 Gil / 件`、`静语庄园`、`世界上传于 12 分钟前`；HQ `500 Gil / 件`、`神拳痕`、`世界上传于 2 小时前`。价格大字且不拆行，不增加未提供的库存、销量或品质档位
- 这些价格和相对时间复用 [样图](../../examples/command_cards/samples/01_market_warm_paper.png)，属于合成/示意 fixture，不是实时查询结果。图外 provenance 明确来源与范围，图内不加入调试标签或用新生成时间冒充报价时间
- 保留简短 `有限返回样本中的最低价；非实时全量报价，不保证此刻仍可买到`，并说明时间属于对应世界上传时间，**不是单条挂牌时间**
- 来源 `数据源：Universalis`，统一左下 Powered by 页脚。展示“样本最低价”不代表全量市场最低价、当前仍有货或可以据此立即成交
- CLI 追加 `05_market_warm_paper.png`，原有四张名称/内容不变；批量输出仍要求仓库外的新目录，不能覆盖旧图。实际市场命令另从已验证 query/outcomes/listings/minimums/observed_at/catalog 投影，支持 min/overview/listings；缺少某品质报价不制造零价或假面板，来源上传时间不冒充挂牌时间。两者都调用同一 renderer，没有新增市场 endpoint

## 7. 完整函数调用与运行

模块仅构造 SDK 内容，完整文本和图片使用同一事实。下面的函数可直接调用，不导入 Core、Pillow 或 parser：

```python
from yomihime_game_link_sdk import cards
from yomihime_game_link_sdk.display import DisplayDocument, TextBlock


def build_result(name: str, count: int) -> DisplayDocument:
    plain = f"{name}\n数量：{count}\n由 Core 统一排版"
    return DisplayDocument(
        title=name,
        subject=name,
        ordered_blocks=(TextBlock(plain),),
        card=cards.Document(
            title=name,
            brand="如月怜的游戏连结",
            blocks=(
                cards.PropertyGrid((cards.Property("数量", str(count)),)),
                cards.Section("说明", (
                    cards.Text(cards.TaggedText("[strong]由 Core 统一排版[/strong]")),
                    cards.Text(cards.Inline((cards.Span("名称："), cards.Span(name)))),
                )),
            ),
        ),
    )


result = build_result("示例 [名称]", 42)  # 名称里的方括号保持字面
assert result.card is not None
```

实际模块把该 `DisplayDocument` 作为既有结果返回，Host/Core 负责主题、资源和发送；模块不自行写盘或发消息。若在仓库根做离线 PNG 检查，可接着运行：

```python
from pathlib import Path
from tempfile import mkdtemp
from presentation.layout_renderer import apply_theme_selection, render_document

selected = apply_theme_selection(result.card, core_theme="warm_paper")
bitmap = render_document(selected)
output = Path(mkdtemp(prefix="ygl-card-")) / "card.png"
output.write_bytes(bitmap.content)
assert bitmap.media_type == "image/png"
```

四样例调用（Logs 额外提供深色版本）：

```python
from examples.command_cards.declarative_samples import (
    build_help_document, build_item_document, build_logs_document,
    build_market_document, render_sample,
)

help_png = render_sample(build_help_document(seed=14))
item_png = render_sample(build_item_document())
logs_light_png = render_sample(build_logs_document("cool_white"))
logs_dark_png = render_sample(build_logs_document("deep_indigo"))
market_png = render_sample(build_market_document())
```

批量写盘与测试：

```sh
python -m pip install -r examples/command_cards/requirements-declarative.txt
python -m examples.command_cards.declarative_samples
# 或显式选仓库外的新目录：
python -m examples.command_cards.declarative_samples --output-dir ../ygl-review-new
python -m unittest examples.command_cards.test_declarative_samples -v
python -m unittest discover -s tests/presentation -t . -p 'test_layout*.py' -v
```

源码样例读取由 `scripts/prepare_test_font.py` 下载并校验的本地 OTF fixture；运行前先按[准备步骤](../../examples/command_cards/README.md#运行)获取。字体文件不跟踪入库，也不通过 Host 自动下载。轻量发布包不附带 OTF，不能把它当成完整源码样例环境。

样例独立 requirements 固定 `Pillow==12.3.0`、`resvg_py==0.5.0`；Core 根运行依赖的下限见 `requirements.txt`。SDK wheel 没有运行依赖。新 renderer 不要求 fontTools，字体维护专项测试如使用它遵循各自依赖。CLI 默认创建系统临时目录；显式输出必须在仓库外，文件独占创建，不覆盖旧 `samples/`。配套 `provenance.json` 记录来源、fixture 范围、尺寸、字节数和 SHA-256。

保存的五张样图在 [samples/declarative](../../examples/command_cards/samples/declarative)，附统一 provenance。样图字节由 provenance 单独记录。视觉参考不改变 Help 可用性、Logs、Market 为离线 fixture、Item 为截图转录的数据性质。

## 有界加权拼贴（SDK a15）

`Document` 可使用单个 `WeightedCollage` 正文块，最多 200 个唯一 key 的 `CollageItem`。模块给出正有限权重、标题、真实数值说明，以及 `Asset` 或已准备的 `ResourceImage`；Core 不解释业务单位。资源引用在异步准备阶段按当前 invocation 权限解析为本地字节，绘制期间不联网。可选缺图使用占位并保留标题及说明；完整文字回退由模块同步提供。

布局先按权重降序、稳定 key 升序确定全局排名，再用整数 shelf 组成若干可见面积档。跨度不足约 1.4 倍时采用同一期望档；跨度更大时只让前部有限候选进入较大档，行槽数沿排名不递减。每行整数宽度完全铺满正文，前后行的高度约束保证后一行最大实际像素面积不超过前一行最小面积；像素舍入也不能颠倒全局排名面积。相同输入、画布和布局版本结果确定，单页正文无孔、不重叠、不越界。面积是非线性分档展示，不是精确比例图；同档、同权重可有少量整数舍入差异。默认偏好 2:3 竖块；cover 至少保留三分之二源面积，无法满足时等比 contain 或占位，绝不拉伸。只有一项、两项及极端权重也受同一面积、裁切和预算约束。

默认正文高度参数为 2800，标题、页码/范围及来源区另计；仍受整张像素与转换预算约束。超长内容只在完整行边界按排名顺序拆页，最多 8 页；每页是唯一、连续的全局排名区间，页码也遵循排名顺序。跨页仍保留实际面积单调与紧密铺满。本地 PNG 上限为每页 4 MiB、总计 16 MiB。普通命令卡片保留原单图限制。Host 在最终发送许可前完成所有页的解码校验、图片组件与消息链构造；一次发送包含全部页。拼贴文字回退局部上限为 24,000 字符、1,024 行，超过时提示缩小查询；普通卡片的 6,000 字符、200 行不变。超预算、可选素材失败或构造失败通过受控文字回退呈现；已开始且结果不明的发送不自动重试。
