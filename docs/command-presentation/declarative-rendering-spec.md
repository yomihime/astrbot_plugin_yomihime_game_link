# 声明式图片渲染契约与四类设计样例

日期：2026-10-16。状态：**四类内容、五张图片的当前视觉设计已获用户确认**（Help、Item、Logs 浅/深、Market）。声明式卡片阶段已接入 SDK 纯声明、Core 共用渲染、Host 命令发送、两级主题设置和 FF14 事实投影。本轮进一步将正式命令字体改为 Core 持久资源管理，轻量插件包不再附带 OTF；源码已接入，当前字体片离线验收及新包登记见第 8 节。本轮字体片尚未安装到真实 Local Test，不能与正在本地安装的前一阶段 `8139d724…` 包混同。视觉确认不等于真实上游、Windows 或当前会话显示验收，标签语法仍待评审。

## 1. 交付范围与职责

模块只提交内容、结构、受限样式和已取得的静态素材。共用 Core 完成测量、换行、版式、字体、颜色、SVG/位图合成及 PNG 编码。例子不能再用自己的 Pillow、Canvas、SVG 转换器或旧 renderer 画一套“接近生产”的图片。

- **Module**：事实查询、数据来源、可用性、字段含义、数字格式、百分位/评价映射、内容顺序、结构组合、图片资源。不得借渲染器推断业务结论
- **Core presentation**：通用声明结构、固定主题槽、受限标签解析、可信静态素材处理、测量与绘制、已有 A/B 字效、预算及明确失败。不得包含 FF14 物品查询、FFLogs 阈值或名称表
- **Host**：渠道能力、权限、发送、提交状态及纯文本回退。Core 渲染函数不发消息，也不判断消息是否已送达
- **SDK**：`yomihime_game_link_sdk.cards` 提供冻结纯数据定义，`DisplayDocument.card` 承载可选卡片，`ModuleDisplay.logo_resource` 声明模块资源。候选 wheel 为 `0.1.0a7`；ABI `2.0`、Display schema `1.8.0` 不变。SDK 不依赖 Core、Host、Pillow、素材解码器或标签 parser
- **LLM**：继续使用同一事实生成的现有结构化结果，不增加图片、品牌随机文案或样式标签。展示转换不得改动事实模型

模块使用 SDK 声明；`presentation/layout_document.py` 仅重导出同一组 SDK 类型。Core 的 `layout_renderer.py`、`layout_assets.py`、`layout_markup.py` 负责实现。`CommandCardRenderer` 将 `DisplayDocument.card` 交给该 renderer，未提供 card 的既有文档继续走旧投影；`ordered_blocks` 保留完整确定性文本回退。以下分别记录源码实现、离线证据和待实测项，不把它们合并为生产验收。

## 2. SDK 声明与 Core 实现入口

以下冻结声明已进入 SDK `0.1.0a7` 候选。模块只导入 `yomihime_game_link_sdk.cards`；渲染、测量和主题目录函数属于 Core，不是 SDK 功能。

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
- 表格所有数据行使用同一 `panel` 背景，表头用 `panel_alt`。不按分数改变单元格底色，不做绿/金色专属深底徽章
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

用户确认两级配置：**Core 配置选择主题，模块颜色选择器提供 `跟随核心` 或显式覆盖**。Core 自身卡片（包括 `/ygl help`）使用 Core 配色。候选的管理页、配置存储和 OutputService 已接线；当前证据为离线页面及真实代码路径测试，尚未在用户 Local Test 操作页面。

- 全局设置中的 `回复样式 → 图片主题` 对应 Core `image_theme`，默认 `warm_paper`（暖桃纸）。保存一个有效 Core 预设 ID；不能从传入文档的默认主题猜出配置
- 模块同名设置由 Core 通用注入 `reply_theme`，不要求每个模块重复声明字段。首项及默认模式为 `跟随核心`，运行快照对应 `module_theme=None`；其余选项是 Core 目录里的预设。选择跟随核心使用 `CLEAR` 删除覆盖，未存储才表示继承；显式存储 null、空字符串或未知 ID 不合法。它不是新色板，也不是全局空主题
- 两处预设均从 `list_themes() -> tuple[ThemeOption, ...]` 取得；冻结的 `ThemeOption(id, label, palette)` 提供 ID、显示名称和预览色板，目录与渲染器共用同一份不可变配色源。后端以此生成页面 choices，前端不复制预设列表；显示名为暖桃纸、冷白、深靛。目录读取与选择转换不触发字体/Pillow I/O
- `apply_theme_selection(document, core_theme, module_theme=None)` 先验证两级配置。`None` 使用 `core_theme`，**不会保留输入 `document.theme`**；显式模块预设或合法 `ThemePalette` 只覆盖本次模块文档，不改全局设置、Core 帮助或其他模块
- Core 卡片只传 `core_theme`，不传模块覆盖。模块卡片传入该模块获授权的设置快照；没有覆盖时传 `None`。纯函数不负责认证、配置读取/保存、数据库迁移或消息发送
- 未知 Core ID、未知模块 ID、非法类型或格式错误的自定义配色均抛 `BitmapUnavailable("invalid_input")`，不静默回退。即使模块有覆盖，Core 配置仍须有效；空字符串不代替 `None`
- 本轮模块 UI 是 `跟随核心 + Core 预设` 下拉框，不包含任意颜色编辑器。完整 `ThemePalette` 只在直接 renderer 或纯 `apply_theme_selection` 函数收到受控显式参数时可用；当前正式持久化/管理页只接受目录 ID 或清除覆盖。OutputService 使用该设置快照覆盖 `Document.theme`，不会读取模块文档色板作为隐藏配置。来源业务文本不能变成配置权限；SDK 仍只承载纯数据定义
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
- Core 根运行依赖固定 `resvg_py==0.5.0`（底层 resvg `0.48.1`）和 `Pillow==12.3.0`；SVG 使用前者的预编译 wheel，位图使用后者。所选 SVG 后端不要求手动安装 Cairo/DLL，不自行安装、不使用 CLI 或在线转换器后备。没有所需依赖时明确报错
- [官方 PyPI 0.5.0 文件](https://pypi.org/project/resvg_py/0.5.0/)包含 Windows `cp310-abi3` 的 amd64/arm64/win32 wheel；[官方安装说明](https://resvg-py.readthedocs.io/en/latest/installation.html)说明不需系统库。本轮实际执行只验证 Linux/Python 3.12，Windows 尚未实机运行，发布 wheel 不等于 Windows 验收通过
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

正式命令的字体由 `ManagedFont` 管理，独立于插件安装目录和业务模块。SDK a7、ABI、Display schema 与模块声明均不增加字体选择接口。固定标准资源为 Noto Sans CJK SC Regular **2.004 / face 0**：

- 文件名 `NotoSansCJKsc-Regular.otf`，准确大小 **16,437,364 bytes**
- SHA-256 `2c76254f6fc379fddfce0a7e84fb5385bb135d3e399294f6eeb6680d0365b74b`
- 固定上游 revision `523d033d6cb47f4a80c58a35753646f5c3608a78`；[指定字体下载](https://raw.githubusercontent.com/notofonts/noto-cjk/523d033d6cb47f4a80c58a35753646f5c3608a78/Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Regular.otf)，许可与来源见 [NOTICE](../../presentation/assets/fonts/NOTICE.md) / [OFL](../../presentation/assets/fonts/OFL.txt)
- 持久位置由 Core 固定为 `data_dir/resources/fonts/NotoSansCJKsc-Regular.otf`。模块、请求参数和上传文件名不能选择目标路径
- 轻量插件 ZIP 只保留 OFL 与 NOTICE，**不包含 OTF**。源码仓库中的原版 OTF 保留作离线可复现字体；出现许可说明不代表运行环境已经取得字体

启动与使用顺序：

1. 运行时启动字体任务后继续 Core 启动，不等待网络下载。已有持久字体须先通过准确大小、SHA 和 Pillow 实际加载验证，之后才发布为标准字体；缺失或不匹配不作为有效缓存使用
2. 标准字体尚不可用时尝试有限、固定的系统字体候选，同时后台取得固定 URL 的标准资源。系统字体只作尽力回退，实际 face 0 的 Unicode cmap 决定支持范围；不能因为文件能打开就声称支持所有中文
3. 每个系统候选最多 16 MiB，超过预算的 TTC 可能被跳过。Core 不递归扫描磁盘、不执行 fontconfig/PATH 命令、不接受模块/用户指定路径。Windows 固定顺序先尝试 `msyh.ttc/msyh.ttf`、`simhei.ttf`、`simsun.ttc`、`simkai.ttf`、`simfang.ttf`，再尝试 Arial；其他平台也仅有代码中的有限固定位置。是否已安装、是否在预算内均需实际确认，本轮未实机验证 Windows。系统字体以同一不可变 bytes 解析覆盖并交给 Pillow，不复制进持久字体目录、不重新分发；本轮不是任意上传字体服务或 native 解码器安全沙箱
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

## 6. 四个内容样例与验收

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
- 数据是已提供截图的展示 fixture，不代表本轮重新查询，不能把截图日期说成数据更新时间。本轮物品详情卡视觉已单独获用户确认。实际模块另将本次 `ItemRecord` 映射成 SDK 卡片，不把截图事实塞进生产结果；真实上游和当前会话仍待实测

### 6.3 Logs 长卡（浅色 + 深色）

- `ChatGPT @ OpenAI`，一张长图包括绝境战及当前资料片零式两个组；沿用历史离线 fixture 的 7 + 13 行，主图 M12S 本体保持 `99.7`
- fixture 的角色、数字、版本、开放目录均为合成排版数据。图外明确说明，不增加图内调试墙、样式图例、“13 条记录”或真实查询时间
- 五列依次是 `副本 / 记录版本`、`职业`、`最高 rDPS`、`过本次数`、`最佳百分位`；记录版本在副本名下方，不独立占第六列
- 百分位固定一位小数、右对齐、实际字形垂直居中；rDPS 为整数千位逗号。`ROUND_HALF_UP` 是参考展示选择，不擅自宣称最终业务取整标准已确认
- 先用原值映射等级再显示：`99.96 → 100.0%` 仍粉色，专门边界测试保留；原始 `100` 且 MAX 证据成立才金色 + A
- 精确标准色依次为灰 `#666666`、绿 `#1eff00`、蓝 `#0070ff`、紫 `#a335ee`、橙 `#ff8000`、粉 `#e268a8`、金 `#e5cc80`
- 缺失用普通色 `—`，有效零正常显示，不可评价文字原样普通色，只有无效/异常值红色 `ERR`。高分不红；不强行给无证据数据授金
- `totalKills` 显示为普通色非负整数，不从 `len(ranks)` 或样本数推断；最高 rDPS 与最佳百分位独立，职业/版本属于后者的记录
- 页脚逐字为 `Powered by yomihime/astrbot_plugin_yomihime_game_link`；来源只显示实际区域对应 `数据源：cn.fflogs.com` 或 `数据源：www.fflogs.com`

目标范围仍由 FF14 业务模块负责：全部已开放绝境战 + 查询地区当前资料片全部已开放零式；仅当前资料片尚无开放零式才允许回退上一资料片，空成绩/查询失败不是回退依据。当前候选不宣称已证明该完整目标：

- 既有默认命令先取得实际默认 zone，再尝试固定 schema/catalog 查询；仅在 introspection 确认 `WorldData.expansions`、`Expansion { id, name, zones }`、`Encounter.zone` 后使用关系，按精确 Ultimate/绝境战、Savage/零式标签选取。零式只取实际默认 zone 所属资料片；以 canonical `Encounter.zone` 和 ID 排除旧重复
- 图片使用诚实范围名 `FFLogs目录及默认副本所属资料片`，不把目录等同于地区当前游戏版本或已开放列表。关系/标签证据不足则保留已知单 zone 成绩并显示简短限制，不用离线名单补齐；没有顺序/发布证据就不回退上一资料片。当前聚合文本说明绝境按标准阵容历史分区查询，零式在明确合格的标准及非标准非 Echo 分区中按原始百分位整行选优，不再声称使用各 zone 默认分区；版本及完整查询合同见 [Logs 表格交接](../fflogs-table-presentation.md)。返回的 encounter ID 若不在整份已验证缓存目录中，图片显示带数量的不完整目录警告。已知 canonical alias 继续去重，不猜归属、不增加刷新请求
- 元数据最多 2 请求、每来源缓存 15 分钟、单请求 5 秒；成绩 alias 批次最多 12 zone/15 秒、总流程 30 秒。命令沿用 40 行上限及完整文本分块回退；LLM 工具仍是原请求与 20 行事实结果
- 真实上游仍须验证新增关系字段、精确难度标签、国服/国际服支持、每记录版本与 bestSpec 含义。无证据版本显示 `—`；击杀数只用真实响应 `totalKills`，不以样本数量代替。合成 handler 测试不能关闭这些待验项

### 6.4 市场价格卡

- 新增 `build_market_document()`，继承历史市场样张的暖桃纸方向与离线示意数据；与其余样例一样，只组装 `Document` / `Section` / `Row` / `Text`，调用同一 Core renderer
- 品牌 `游戏连结 · FF14 · 市场`、右上陨石、突出 `犎牛牛排`，范围 `物品 44091 · 中国全区`
- 两个清晰 NQ/HQ 面板：NQ `30 Gil / 件`、`静语庄园`、`世界上传于 12 分钟前`；HQ `500 Gil / 件`、`神拳痕`、`世界上传于 2 小时前`。价格大字且不拆行，不增加未提供的库存、销量或品质档位
- 这些价格和相对时间复用 [历史样图](../../examples/command_cards/samples/01_market_warm_paper.png)，是合成/示意 fixture，没有本轮实时查询。图外 provenance 明确来源与范围，图内不加入调试标签或用新生成时间冒充报价时间
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

源码样例继续读取仓库原版 OTF，不通过 Host 自动下载；下面的离线入口要求该 fixture 存在。轻量发布包不附带 OTF，不能把它当成完整源码样例环境。

样例的独立 requirements 与 Core 根运行依赖都固定 `Pillow==12.3.0`、`resvg_py==0.5.0`；SDK wheel 没有运行依赖。新 renderer 不要求 fontTools，字体维护专项测试如使用它遵循各自依赖。CLI 默认创建系统临时目录；显式输出必须在仓库外，文件独占创建，不覆盖旧 `samples/`。配套 `provenance.json` 记录来源、fixture 范围、尺寸、字节数和 SHA-256。

获准归档的五张样图在 [samples/declarative](../../examples/command_cards/samples/declarative/)，附统一 provenance。迁移到纯 SDK 后五张 PNG 字节不变。历史样图保留；视觉确认不改变 Help 可用性、Logs、Market 为离线 fixture、Item 为截图转录的数据性质。

## 8. 当前证据与本地 Codex 交接

### 8.1 本轮字体片状态与本地交接

本轮实现 §5.1/5.2 的受管资源与 Core 页面，固定 SDK a7 wheel 字节不变，不扩展模块权限或新建字体 SDK。轻量候选为 **285 成员、1,474,301 bytes**，SHA-256 `5b745694b4da35e6cb4be8a9dd914a2f147891442d718fdc9ee64c57daf04395`；准确 SDK/Core payload、OTF 排除、OFL/NOTICE 已核验；最终 **13 项解包检查通过（5.100 秒，0 失败/错误/跳过）**，bootstrap/工厂通过。测试使用真正 Host 默认 ManagedFont，覆盖当前系统字体渲染/文字回退、授权状态/重试/上传、上传后标准字体帮助 PNG、持久资源复用且不再次下载；仅 aiohttp 传输为合成响应，外部网络被阻断。全部 285 个 payload 与 259 个源码输入逐项匹配；源码提交以最终 PR 交接为准。

字体片验证分批记录：作者 108 项定向通过（126 秒，该批启动时含 9 项管理器），最新管理器 11 项另过，presentation 74 项另过；前端 typecheck/build、相关 16 项与字体 6 项重验分别通过。独立六套相关检查 114 项通过（137.453 秒，启动早于新增 Windows 候选测试），最新管理器 11 项、UI 6 项、正式管理入口 7 项另验通过。独立范围内未发现阻断项；这不是一次全仓检查，不叠加为单一总数，也不证明真实网络下载、Windows 字体或 Local Test 页面。

前一阶段 `8139d724…` 为包含 OTF 的全量包；其摘要、体积和前轮通过范围均不适用于本轮轻量候选。

当前本地任务正在安装前一阶段 `8139d724…` 包；本文件不据此宣称其安装、页面或四条真实命令已经成功，也不要求中断该任务换包。完成现有本地任务后，后续字体片安装/验证须另有用户对目标和操作的明确授权：

1. 核对本轮轻量包、提交与摘要；本轮不改 SDK。若目标已经正确冷加载 a7，无需仅为本片再次执行 a6→a7 的卸载步骤；使用正式插件更新流程，并保留 Core 数据资源
2. 启动后在 Core 全局设置观察检查/下载/就绪或系统回退。已有有效缓存时不为演示失败而删除或破坏文件；网络下载和系统字体覆盖分别下结论
3. 需要手动恢复时，只经页面下载指定版本，再上传同一固定文件或重试。不要输入任意 URL、修改字体摘要、改 OS 网络安全设置或读取凭据；上传操作本身仍按实际本地授权执行
4. 用获准的命令预算检查无字体/缺字时的完整文字回退，以及标准字体就绪后新命令的 PNG；不要自动重发早先命令。此前四条消息预算不因本片自动重置，先记录已执行次数
5. 保持已安装新版和用户选择的配置，不自动回滚、清空字体缓存或动 LLM/其他插件。真实 Windows、上游下载、当前页面和 IM 图片可见性仍由本地实测分别证明

### 8.2 前一阶段声明式卡片证据（8139 包）

以下保留前一阶段候选及各批检查的原范围，尚不包含本轮字体管理。其“随包字体”核验针对当时包含 OTF 的包；当前轻量包以本轮登记为准。


- SDK `0.1.0a7` 候选 wheel 为 **53,256 bytes**，SHA-256 `b6eaefee0045b91a4cc4ab71acd6b3f86824dcfcf5ea6fad574087106b937cb1`。纯声明、TaggedText/Cell、card/logo_resource 已接入；ABI `2.0`、Display `1.8.0` 未改
- 修后插件候选 `astrbot_plugin_yomihime_game_link-v0.1.2.zip`：**284 成员、15,054,948 bytes**，SHA-256 `8139d724d2c6027bd43e5e6ca102cf7709b90d34aad177bfa3914e063ae86284`。精确成员/字节、CRC、SDK/字体/SVG 固定摘要和解包后的 5 种 bootstrap 模式已验证；仅 `modules/ff14/features/fflogs.py` 相比前包改变，其他 283 个 payload 逐字节相同。受影响的解包 Logs 检查 **2/2 通过（14.820 秒，0 失败/错误/跳过）**，149 个产品模块来源确认为新解包目录；5 种 bootstrap 与 SDK 样例工厂重验通过。测试后全部 284 个 payload、258 个包内源码输入稳定；最终源码提交以 PR 交接记录为准
- 返修前包 **15,054,822 bytes**、SHA-256 `6eb65a6bd8d04e1e4f7f62857dfe74c39a1f70d5577ee30bdb1f3970c7936e55` 仅保留为历史证据。其包内代码路径 **8 项通过（54.734 秒，0 失败/错误/跳过）**，核验 151 个产品模块从解包目录加载，覆盖 help/item/market/国服与国际服 Logs、回退和主题/过期边界；SDK 样例工厂也通过。随后发现并修正聚合文本“全部分区”误标及未知 encounter 目录警告；旧 8 项结果不能冒充修后包完整重跑。所有包内测试使用合成传输并阻断外部网络，无真实平台或上游调用
- 五张固定样图已获用户视觉确认；共享 renderer 的限额、素材、标签、几何、A/B 字效、数字对齐和无裁剪有离线检查。当前真实命令输出使用实际事实，不能要求它与合成样图逐字节一致
- Core/Host 作者最终相关 **83 项通过**；既有 Output/CoreRuntime/AstrBotRuntime 回归按初次 111 项及 pinned Host 补验 2 项分批通过。覆盖精确 event、同一 renderer 字节、PNG 解码、manifest logo、主题拒绝/覆盖/清除、帮助不受模块覆盖、过期/重复/发送前回退。全部使用隔离环境和合成上游
- 配置作者最终 Python 组合 **183 项通过（50.065 秒）**，含 pinned Host、管理授权、CAS 和已加载模块主题写入/健康状态回归；前端 shell **85 项**、management **146 项** 分批通过，typecheck/build 通过。实际页面读写已接线；真实浏览器 Local Test 操作仍待验
- FFLogs 作者初批 **52 项通过**；分区标签和未知 encounter 警告修正后另跑 **64 项通过（60.654 秒）**，含 56 项模块/卡片/工具合同、2 项声明卡 Host 和 6 项既有工具 Host 检查，覆盖真实 handler 的合成 7+13 行、21 行增长、局部错误/缺失、原值满分与来源计数；这不证明真实 FFLogs schema/目录或地区开放状态。物品/市场作者最终定向 **99 项**、实际注册入口与 Host PNG 集成 **2 项**分批通过，使用合成传输；最终独审结果见[交接入口](README.md)
- 独立广回归原批次实际运行 **1012 项：954 通过、58 项环境错误**（验证命令遗漏 Host overlay，触发 PyJWT 版本前置错误；无断言失败）。原错误保留；正确环境下精确 58 项补验为 **57 通过、1 断言失败、0 环境错误（415.140 秒）**：既有显式 `/ygl ff14 status` 检查预期 1 次发送，实际 0 次，provider trace 为空；该 method 单独重跑仍失败（9.461 秒）。独立最终 Logs 另 **56 项通过（0.353 秒）**，该检查批次 559 份选定源码哈希稳定。独审随后确认旧测试 Event 夹具缺少 `send` / `chain_result`，真实状态 PNG 已生成，精确事件路径因此安全返回 FAILED。仅迁移该测试函数的 pinned Host 发送观测，保留 Context 广播零次及 provider 零次断言；作者单项 **1 通过（9.878 秒）**，独立最终 **5 项通过（30.872 秒，0 失败/错误/跳过）**，包括原失败项和 4 项真实声明式命令链路。559 份选定源码哈希稳定，生产文件/当前 ZIP 未变。该遗漏已关闭；限定源码与离线交付审查为 **0 Blocker / 0 Important**，不代表真实平台或完整授权审计。不能将各批次拼成“1012 项全绿”或把重复范围相加。
- 上述为不同批次，不加总成一次“全量全绿”。旧 string/CardSpec 路径、历史图片、既有全仓失败/环境失败原样记录；限定源码与打包独审已闭环。未安装新候选，未运行 Windows、真实 FFLogs 或真实 LLM

### 8.3 前一阶段安装准备（a6 → a7）

本地 Codex 的主任务是安装固定候选、检查正式设置页并实测命令，**不是重新开发 SDK、Core、主题页面或再做一套绘图**。从最终交接提交取得本文件、候选 ZIP/摘要、SDK wheel 摘要、五图/provenance、各批测试和独审结果；先核对候选确切字节。本段的候选和离线独审指 §8.2 的前一阶段 `8139d724…` 包，实际操作以其本地任务最新授权为准；不用返修前包或旧 `737bc643…` 候选替代。

实际本地操作尚未执行，须由用户向本地 Codex 明确授权目标 Local Test、game-link 正式卸载/上传/启用、冷重启、主题检查及下述最多四条消息。复用已经提供的本地角色目标，不再次索取或把账户/角色资料写进公开仓库。

1. 确认是指定的可丢弃 Local Test，仅处理 game-link。用户已取消自动备份、回退包、旧安装溯源及 backup helper 完整性前置；已有材料保留，不重启这些门槛，也不把旧缺口记为修复
2. SDK 从 a6 升为 a7，需要协调一次**正式卸载旧 game-link 测试插件 → 目标 Local Test 冷重启 → 正式上传固定候选 → 启用**。冷重启会暂时中断该宿主的其他插件运行，须纳入授权；不更改 LLM、其他插件、模型、凭据或网络安全设置，不用热更新冒充 SDK 冷加载
3. 依赖按候选根 requirements 安装。所选 resvg wheel 无需手动 Cairo DLL；实际 Windows 可用性仍须此次机器验证，失败记录原异常及目标环境。不要临时换在线转换器、字体或另一绘图实现来掩盖问题
4. 使用既有正式管理/登录流程。已有凭据无需读取、导出或预先重新配置；需要登录或敏感输入时交由用户安全完成。Logs 凭据不可用单列为该项阻塞，不阻止 help/item/market 或插件安装核验
5. 页面核验 Core `回复样式 → 图片主题` 与模块同名设置：同一目录、模块跟随核心、正常权限/CAS、切换后读回及清除覆盖。记录用户授权的最终配置，不随意永久改动其他字段

### 8.4 前一阶段四条命令预算与验收

拟议预算为 **4 条真实用户命令，各一次，无 LLM**；尚未执行，也不自动视为用户已经批准。专用会话复用用户已经提供的本地目标，公开文档只保留占位符：

```text
/ygl help
/ygl ff14 item 44091
/ygl ff14 market 44091 region=cn quality=all intent=min
/ygl ff14 logs 国服 <服务器> <角色>
```

Logs 参数依当前命令 parser 分词，含空格的角色名按正式帮助格式引用。本地执行方替换为已知目标；不发送占位符、不添加探针/重试命令，不为失败自动消耗额外预算。

逐条保留：请求、完整 SSE/正常终态、机器人记录数、图或明确文字回退、实际附件 HTTP/媒体类型/PNG 解码、当前会话可见性、原尺寸和聊天缩放截图。图片应为单次回复，不出现重复图片或额外文字补发；若按既有合同退回完整分块文本，单列实际块数。UNKNOWN 不盲目补发。Help 核对实际可用模块和 Core 主题；Item 核对当前来源/缺项；Market 核对本次价格、范围和世界上传时间（不是要求仍为示意的 30/500）；Logs 核对实际来源、条件目录覆盖、缺失/错误和现有局限，不把单 zone 或无凭据称为完整聚合成功。

主题切换可在四条命令前后按获授权的页面步骤安排，不增加命令预算。图片以本次设置快照为准，切换不改写历史消息。对登录、SDK 加载、图片依赖、来源数据、页面状态、PNG 发送分别下结论；没看到真实 UI/图片就记未验。实际 Windows、真实上游、非管理员身份或其他 IM 平台未覆盖的项明确列出。

测试后保持新 game-link 版本安装并启用，不自动卸载、回滚、清理会话/图片或改回旧版；只按用户指定保留主题值。失败时报告确切步骤、目标、证据与剩余授权，不绕过拒绝，不以样图替代真实返回。提交、推送、PR 发布与 Local Test 安装是不同动作，各按当前用户授权执行；本文不授予额外权限。
