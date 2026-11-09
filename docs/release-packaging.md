# 完整发布包

Core 与 DLC 独立发布，通过 ygl.control/1、module_contract 2.0 与版本化 features 协商，不按固定版本配对。Core 运行时 SDK 为闭合私有载荷，独立 pip wheel 只用于开发。

Core：`python scripts/build_release.py --test-signing --output-dir <private-dir>`。DLC：`python scripts/build_release.py --allow-dirty --test-signing --output <private-dir>/plugin.zip`。测试密钥只在内存存在；每包严格小于 16,000,000 字节。expanded payload 与单文件/文件数限制由对应运行时预算再次检查。

## 连续更新的签名与序号

维护连续发布流时使用受保护、稳定的 Ed25519 发布者密钥，按 `package_id`、发布者 `key_id` 和 `channel` 分别记录发行台账。Core 与各 DLC 独立管理自己的序号。每次发布新内容，都显式传入大于该流已接受 head 的 `--sequence`；不要把轮换密钥或切换渠道当作重置升级历史的办法。发布者密钥保管、轮换与可信登记仍是后续发布配置，本轮不会创建生产密钥或执行发布。

两个构建器实际支持的连续发布参数如下。`SIGNING_KEY_FILE` 指向外部受保护的私钥文件；`NEXT_SEQUENCE` 必须从对应发行台账确定，下面不会生成或显示密钥：

```sh
# 在 Core 仓库运行
python scripts/build_release.py --signing-key "$SIGNING_KEY_FILE" --channel stable --sequence "$NEXT_SEQUENCE" --output-dir "$RELEASE_DIR"

# 在 DLC 仓库运行
python scripts/build_release.py --signing-key "$SIGNING_KEY_FILE" --channel stable --sequence "$NEXT_SEQUENCE" --output "$RELEASE_DIR/plugin.zip"
```

`--channel` 支持 `preview` 和 `stable`，两个 CLI 的默认值都是 `preview`；`--sequence` 默认都是 `1`。`--test-signing` 只支持 preview，并在进程内生成临时测试密钥。上面的开发示例与默认序号 `1` 用于隔离候选验证，不是生产连续更新流程；复用稳定测试密钥进行多次更新验证时，同样要显式递增序号。DLC 的 `--allow-dirty` 只用于本地开发候选，不替代发行台账和序号管理。

版本号与发行序号是不同的域。要让 AstrBot 市场实际显示可更新，还必须提高插件 metadata 的版本号，并让完整发行包中的 release 版本与之匹配；只递增 `--sequence` 不会替代宿主的版本比较。只要新内容使原始 `release.json` 的摘要发生变化，就不能重用旧序号，即使 `release` / 插件版本号已经提高。接收端会拒绝低于已接受 head 的序号，也会以 `release_downgrade` 拒绝“同一序号、不同摘要”；改代码、资源、构建来源或其他清单覆盖内容后都应使用下一序号。无签名 DLC 仍需要精确 artifact 摘要授权，并遵守本包、无签名发布流和渠道的 head 序号检查，不能依赖默认 `1` 完成后续更新。

回退仅限当前授权下内部 last-good 的精确 pin 恢复。手动上传旧 ZIP 仍属于外部降级，不能通过降低或重用序号、仅修改版本号来代替内部恢复；没有公开的跳过版本检查入口。保持最新配置和数据，不把旧数据库覆盖到已升级实例。

Core 在薄壳的持久缓存中原子保存各发布流的最高已接受序号与原始清单摘要。同序号同摘要可重复接收；外部倒退或同序号不同摘要以 `core_release_sequence_rejected` 拒绝，不能进入业务挂载。内部精确 pin 恢复还须匹配当前安装所选公钥、渠道、插件身份及数据库迁移清单，且不降低 head。Core 随原生管理员安装内容选择的公钥提供自验完整性，不等于 DLC 发布者可信登记，也不是针对恶意 ZIP 的独立信任根。

未发布 alpha 的旧非空 Core 缓存若没有完整 head，启动以 `cache_core_head_migration_required` 拒绝，需要保留原始缓存、配置和数据后单独制定维护方案；不能删除 head 或清空缓存来绕过防降级。新的空缓存可正常引导，已有完整 head 的普通兼容更新无需先停用插件或重启 Host。

首次 Core 载荷落盘前先原子建立空 ledger；head 提交失败时，未接纳的候选目录不构成发行历史，也不能执行。已有有效 ledger 和精确 pin 的正常运行、内部恢复及重启不依赖删除该候选目录，后续候选仍须重新验证并成功提交 head。

`release.json` 精确覆盖 ZIP 全部 payload，仅排除自身与 release.sig.json；Core 的 release-key.json 必须被清单覆盖。签名是 Ed25519 对 release.json 原始字节的签名，key_id 是原始 32-byte 公钥的 SHA256。BUILD.json 保存提交、dirty overlay、源文件及构建器摘要。SHA256SUMS 保存完整 ZIP 字节摘要。生产密钥需未来由发布管理配置；禁止把私钥加入安装包。

原生市场 download_url 必须指向完整不可变 Release ZIP asset；自动源码归档缺少生成清单/签名。未上市场可通过 AstrBot 原生 ZIP 上传完整包。普通兼容更新无需 Host 全局重启；共享或原生依赖变更按环境维护处理。pre-coordinator alpha 首次迁移不承诺热切换。外部降级（含普通手动上传）被拒绝；仅当前授权下内部 last-good 回退，无公共 skip-version。
