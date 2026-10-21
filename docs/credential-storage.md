# 凭据、私密存储与恢复

本文是凭据、Windows 存储边界和恢复语义的权威说明。页面入口和部署接线见相应管理与安装文档。

主密钥生命周期属于 Core 全局管理。模块只保存、替换完整业务凭据对，并读取安全状态。存储就绪、凭据已配置、可解密、上游请求成功是四项不同证据；存储状态不证明上游可用。

## 环境密钥转托管

全局 `master-key-adopt-environment` 操作只接收最新 `expected_revision`、`expected_generation`、`inventory_digest`，使用独立全局管理授权。它将当前已经选择且能解开全部库存的原环境密钥写入不可覆盖的私密候选文件。不会生成替代密钥，不要求重新填写业务凭据。

候选持久化成功后才提交 selector。接管不改变密钥或凭据 generation，增加 revision，旧进程持有的 revision 随即失效。journal 的 `environment_adoption` purpose 区分接管和普通恢复；中断后再次执行同一显式操作恢复。普通刷新、启动、retry 或 restore 不代替接管。

接管成功且同一环境密钥仍存在时，状态为 managed，`environment_present=true`、`environment_matches=true`。确认成功后可从宿主环境配置移除该变量并重启；刷新页面不能修改进程环境。不同值、空值或无效格式仍阻断存储，不会静默改选。缺少原环境密钥且尚未成功提交接管时，需要恢复原宿主环境后继续。

每次改变检查授权、CAS、库存、操作身份和生命周期；迟到成功响应仍必须通过输出围栏。秘密、密钥、密文和内部路径不出现在状态 DTO 中。

全局主密钥、存储恢复、普通设置及受限历史配置复制使用同一 Host 请求围栏：每个 source live/SQLite 提交检查同步重新调用实际 Dashboard 验证器，并核对原请求、主体、Bearer 会话、Origin 和绑定的验证 authority。会话身份仅在请求内保留随机键控摘要，原请求到期时间固定，复核不会续期。验证异常、异步返回、撤权或身份变化均拒绝；提交前拒绝会回滚事务。错误包装和最后异步清理后仍复核，避免把失效授权表现为成功。合法提交后的撤权不能撤销已经提交的状态；若成功回执未取得确认，应重新读取状态收敛，不自动重试写入。详情见 [Host 管理鉴权合同](host-management-authorization-contract.md)。

## Windows 边界与已有数据

现有布局不会自动搬迁、修复 ACL、重置或切换到空数据库。Windows Host 在创建数据库和 Core 前解析权威存储布局，并在 Core 生命周期内持有存储 guard。只有权威根和数据库边界通过安全检查后，guard 才会单独核验并 pin `keys`、`secrets`、`temp`、`recovery` 凭据子树；如果该子树不安全而根/数据库仍可确认，Core 可隔离运行非秘密功能，但该次打开的凭据 guard 会锁存为 blocked，所有密钥/秘密读取和写入均拒绝，直到同一 Host 正常冷重启后重新检查。根/数据库身份、安全性、selector、sidecar、provenance 或数据库存在性无法确认时，不选择其他目录、不降级为新库，也不触碰凭据环境变量。

锁存的存储阻断仅返回安全状态：库存数量和摘要未知，环境变量存在性/匹配性未观察。不能把 unknown 当成空库存、缺少密钥或密钥不匹配；不得因此读取/重填旧环境密钥、清 selector、重置或改变真实 ACL。应由部署方修正运行身份或访问条件后，正常冷重启同一 Host；不要通过重开页面或刷新状态解除锁存。当前版本不提供生产旧库迁移 adapter。

`storage_security` 独立于凭据 ready；其他旧布局仍标记 `legacy_review_required`，保留原数据并遵守原密钥及操作系统安全检查。原本可用且检查合格的密钥不要求重新填写。权限不合格不能通过手填原密钥绕过操作系统检查。

`infrastructure/private_storage.py` 的 `resolve_private_storage` 为全新空 Windows 实例选择运行身份的 KnownFolder LocalAppData 下的稳定实例目录，不信任 `LOCALAPPDATA` 环境变量。Host 必须在构造数据库前解析布局，并在整个 Core 生命周期持有 `open_guard()`；生产装配和正式包的证据需单列，独立原生文件测试不代表已经更新真实实例。

相关数据库及 WAL/SHM、keys、secrets、临时和恢复文件都必须落在已选定的安全布局内。父路径替换、非受信 owner、重解析点及未知 ACL 均拒绝。新建私密目录时设置受保护且向子项继承的 DACL；既有目录不会被自动改 ACL。运行期间 pin 祖先和固定私密子目录，文件按句柄校验身份、ACL 与链接数。POSIX 保留既有路径与安全检查。旧 ACL 不合格不会因目录选择或 Core 初始化而自动修复。

ACL 用于限制其他非授权本地身份的读取、修改和父路径替换。它不能防止同一运行用户下已经被攻陷的进程，也不能防止管理员或 SYSTEM；不得将其描述为恶意模块的安全沙箱。

## 显式存储迁移

`master-key-migration-plan` 是独立读取授权的只读计划；返回实例身份摘要、源 generation/revision、完整库存和数据库快照摘要，不返回路径、key、密文或凭据。CLI 只提供 plan；管理页也只显示计划，不提供 execute 按钮。后端保留 `master-key-migration-execute` 拒绝路径，需独立修改授权和最新计划，但当前没有真实旧部署 supervisor 能签发来源 lease，故正常在线旧布局会安全拒绝 `requires_stopped_host`。计划、确认或停止 Host 本身都不授权迁移；当前版本不提供生产旧库迁移 adapter。

下述迁移传输算法仍说明内部 API、测试 runner 和未来合格 supervisor 的安全合同，供维护代码审阅；它不是可由操作者照做的迁移步骤或当前部署支持声明。不得手工复制目录、改 selector/ACL 或通过清除数据绕过监督门禁。

共享执行器位于 `infrastructure/private_storage_migration.py`。执行只接受受信部署 supervisor 签发且仍在作用域内的精确进程来源 lease。用户勾选、PID、进程名扫描、一次文件哈希稳定、SQLite 锁、目录 pin、只排空当前 Core 或从 JSON 恢复的收据，都不能代替完整来源证明。新拿到锁也不能追认此前不存在旧 writer；无法证明旧进程及所有启动器已停止和禁止重启时必须拒绝。受控离线 runner 与真实部署 adapter 的支持和验证范围需分别记录；没有适配当前真实旧部署的来源证明时，该部署的迁移仍然阻塞。

前提满足后，执行器重验 plan CAS、安全读取原 key 并解密全部 receipt 库存，持久化源 epoch fence 和所有用户表写拒绝 trigger。独立 read connection 通过 SQLite backup 复制包含 WAL 的一致快照；SQL fence 不被宣称能阻止其他进程已经加密但尚未发布的文件写，这类 writer 必须已由 supervisor 证明退出。

目标按 operation 独占创建；复制并 flush 全部登记文件，核字节、receipt、解密及文件身份。目标预备 journal 与源 fence 分开，目标激活后才在私密实例根发布同目录、write-through 的原子 selector。跨卷只进行复制，不假定跨卷 rename 原子。源及恢复材料保留，不删除或改原 ACL。

selector 发布是唯一提交点。发布前中断保留 preparing，可在重新计划、核 CAS 和重新取得来源 lease 后恢复同一 operation；不会自动解开源 fence。私密实例内的 durable attempt journal 在写入前记录候选及文件创建意图，再绑定目录与文件身份。半写 backup 或 payload 所在候选只会在已登记身份与库存核对后标为 abandoned，原字节保留，新建另一候选完成复制；不会覆盖或删除 partial。未知文件、被替换的身份或无解释的库存会停止操作并保留。单 operation 最多保留 16 个候选；达到上限需要明确维护处理，不会自动清理。

发布后的取消、崩溃或回执丢失为 commit-unknown，只能重新授权读取 selector 收敛，不能自动切回源。冷加载与同 operation 回执恢复使用同一只读校验：selector、源 file identity/fence/epoch/journal、目标目录及 DB file identity/activated epoch/journal、manifest 哈希与字段配对、当前库存和可解密状态都必须一致。manifest 的 DB 内容哈希是迁移时的审计检查点；正常目标写入后不要求保持该历史内容哈希。源已永久围栏，仍核对其冻结逻辑快照与原文件库存。任何不一致均阻塞；旧源的持久写和旧 epoch/proof 不得恢复有效。

模块页面的存储动作由固定 `module-page-session` bootstrap 获取服务端短期 proof，退出时使用 `module-page-session-close`；六个模块配置/业务凭据动作仍需逐次验证实际 Host 主体、会话、owner/page/runtime/epoch、字段与 READ/UPDATE 授权。普通 JWT 或客户端标签本身不构成 page proof。该 proof 限缩已有管理权限，不能证明请求来自某个可信 DOM，也不是恶意同页 JavaScript 的沙箱；平台缺少这种 DOM 来源证明。保留的 execute API 拒绝路径不构成页面操作入口，也不表示生产 adapter 已接通。

## 全域重配

reset 是逻辑失效与重新配置：撤销旧引用、generation 和权限后生成新的选择。它不是密码学擦除，不能保证磁盘、备份、日志、交换文件或介质中的旧数据消失。旧文件和恢复材料保留其既有保护与恢复约束。真实数据的 reset、迁移、密钥操作及权限调整都需要相应明确授权。
