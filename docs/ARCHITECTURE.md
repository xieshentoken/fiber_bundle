# 模块边界与 UI 维护

M2，2026-09-28。前端只调用同源 HTTP API；后台不依赖前端状态或 DOM。随 Python wheel 一起分发静态文件是部署选择，不是业务耦合。后续 Electron 管理窗口复用接口，后台生命周期独立。

## 前端

目录：`src/fiber_bundle/web/`。原生 ES modules，无运行时框架和打包器。

| 模块 | 单一职责 |
|---|---|
| index.html / style.css | 页面结构、视觉与响应式布局 |
| tokens.css / records.css | 基础视觉角色变量与共用记录样式 |
| navigation.js / icons.js | 顶部 Tab 选中、页标题、窄屏可见性及固定装饰图标 |
| record_ui.js | 安全创建共用记录及按节点协调列表，保留未变化记录的界面状态 |
| execution.js / management.js | 执行与费用视图 / 资料与版本视图，分别调用命名 API |
| app.js | 组合 API、会话与各功能视图；登录、刷新与到期清理 |
| api.js | 唯一 HTTP 入口；路由、CSRF、响应校验、错误转换与旧会话响应隔离 |
| session.js | 纯会话状态、版本与到期判断，不访问 DOM/HTTP |
| ui.js | 安全 DOM、页面/会话归属提示、导航入场与按钮/权限表单共用函数 |
| vaults.js | 知识库列表与默认查询设置 |
| clients.js | 客户端创建、权限编辑、页面内撤销确认与凭证显示清理 |
| approvals.js | 待审批库列表与明确授权 |
| batches.js / batch_countdown.js | 批次展示与显式选择 / 可取消的到期只读查询 |
| page_loading.js | 可见页面的延迟提示、首屏空间预留与加载状态清理 |
| unsaved_changes.js | 内存草稿比较、离页确认、提交值确认及会话清理，不追踪凭证 |
| operation_feedback.js | 按钮旁的后台响应等待及实际耗时，不推测业务任务进度 |
| settings.js / appearance.js | 七项字体与显示设置的预览/保存/取消；应用独立中英文字号、用户字体及跟随主题字族 |
| appearance_preferences.js | 外观校验、持久化、已保存值与草稿合成、局部主题提交、同源多窗口同步；不访问DOM或业务API |
| themes.js / theme_picker.js | 固定三主题及旧配色目录 / Logo与设置共用的原生主题选择弹窗、焦点与错误反馈 |
| appearance_bootstrap.js | 模块图加载前同步应用已保存的合法主题/密度/动效/英文字号；只读存储，失败使用默认样式 |
| themes.css / theme_picker.css / appearance_overrides.css | 三主题及旧配色的视觉角色 / 弹窗与同构预览 / 最后加载的用户密度和减少动效覆盖 |
| system_api.js | 复用 api.js 注入的认证 request，封装系统设置、字体列表与用量报表，校验响应合同 |
| font_picker.js | 本机字体下拉框、首次读取/手动刷新、有限等待和错误提示；仅在内存维护字体目录，刷新保留草稿选择 |
| local_fonts.js | 解析所选本机字体标识、按家族整理目录、管理至多两个本地 FontFace 别名；不请求 HTTP 或写入存储 |

调整视觉优先改 HTML/CSS；调整单个功能交互只改对应视图。视图通过注入的命名 API 操作访问后台，不拼接 HTTP 请求。新静态模块须加入 `web_routes.STATIC_ASSETS` 白名单。凭证不写入浏览器持久存储，离开相关页面或会话结束即清理。

列表入场仅由导航调用 `enterList`，最多桌面四条、窄屏两条；刷新不重复播放。执行视图以记录 ID 和响应内容复用节点，数据变化时保留费用详情展开状态及对应操作焦点。异步操作在发起时捕获通知页面与代次，切页/退出后丢弃旧反馈；全局会话错误继续显示。

加载编排不替换已有表单，150ms 延迟提示使用固定槽位；新页面按可用视口预留首屏空间，成功、失败及切页都会恢复样式/aria-busy，长列表总高度仍以实际内容为准。移除空面板自动入场及 min-height 动画，只在数据就绪后调用既有列表入场。草稿追踪仅保存 DOM 控件的内存值，排除 password/file/readonly 凭证；模型配置另以功能 dirty 状态保护导入草稿。保存成功只确认提交时的值，模型配置以编辑代次保留等待期间的更新，CAS 版本仍取已提交结果。强制会话清理不弹确认。`setBusy` 共享操作等待反馈，300ms 后显示无百分比的等待文字及耗时；页面/会话代次变化立即取消提示定时器，后台副作用不会随离页取消。

批次视图拥有一个倒计时控制器。可见时按最近截止时间安排单个查询，重复渲染取消旧定时器，离页/退出取消；每个批次和截止时间只自动查询一次，失败不循环重试。到期不冻结批次或提交选择，10 秒既有状态轮询保持。`freeze_at` 是后台 quiet window 的只读估计，实际状态仍由批次领域模块控制。

Electron 的 `main.mjs` 处理 `will-prevent-unload` 原生对话框，默认留在窗口；明确放弃才忽略 renderer 的离页拦截。取消重连产生的 ERR_ABORTED 不降级到离线页。对话框不读取表单或控制后台，窗口关闭继续与服务生命周期独立。

## 后端

| 模块 | 单一职责 |
|---|---|
| api.py / admin_routes.py | HTTP 输入、认证入口、响应与路由组合 |
| web_routes.py | 静态资源白名单与 MIME，不含权限业务 |
| auth.py | 密码、会话、客户端身份、独立授权和撤销 |
| vaults.py | Vault 路径、申请、批准、默认查询与按库权限规则 |
| operations.py | 已授权的收件、版本、原件和任务操作 |
| payloads.py / http_guard.py | 输入合同、请求限额与本机 HTTP 边界 |
| store.py / database.py / config.py | 原件持久化、事务/迁移/锁与配置 |
| system_admin_routes.py | 系统设置/字体/用量的管理员 HTTP 边界；字体路由先鉴权并关闭本地数据库连接，再读取操作系统元数据 |
| system_fonts.py | 通过 ctypes 调用 macOS CoreText/CoreFoundation，仅枚举字体名称元数据并释放原生对象；非 macOS 返回不支持，读取失败抛 FontCatalogError |

数据流：功能视图 → api.js → HTTP 路由 → 领域函数 → SQLite/原件。路由不重复实现权限或存储规则；所有鉴权与实际操作继续在共享锁内完成。UI 不能直接访问数据库或 Vault 文件。此次拆分不改变 D17 的 API、schema 或授权语义。

查询接口的共用请求类型位于 `answer_payloads.py` 和 `query_http.py`，路由模块不相互导入。`query_task_store.py` 封装本地/云任务与 job 的列表投影、归属及关联 job 查询，调用方在共享锁内完成认证、权限检查和业务操作；该模块不启动 worker 或模型。`query_routes.py`、`query_admin_routes.py` 保留 HTTP 入口与业务调用组装。文档管理查询按下文 M8 边界处理；`media_admin_store.py` 封装媒体任务记录、处理列表和发布状态的只读投影。媒体路由在共同锁内验 Cookie/CSRF 与原件权限，继续调用现有 media_processing/review/review_source/publication/cleanup 核心；投影不承担来源核验、人工接受或文件副作用。

本机字体的读取链路为 `settings.js → font_picker.js → api.systemFonts() → system_admin_routes.py → system_fonts.installed_fonts() → CoreText`。渲染链路为 `appearance.js → local_fonts.js → FontFace(local(...))`；只有所选名称随外观偏好进入当前客户端的 `fiber.appearance.v1`，完整目录不持久化。字号比例变化时重建中文别名，避免 Chromium 复用旧字形度量；缺字或已卸载字体回退到内置字体。Web 与 Electron 复用同一接口，桌面壳和权限策略保持不变，无自定义 IPC、新依赖或数据库迁移。具体签名、存储格式与错误语义见[技术参考：本机字体](TECHNICAL_REFERENCE.md#system-fonts)，实际部署证据见 [STATUS.md](STATUS.md)。

主题切换链路为 `Logo / settings → theme_picker → appearance_preferences → appearance → 根属性/CSS变量`。沿用 `fiber.appearance.v1`，新增 `cursor/oracle/tesla`，保留 `paper/green/gray` 与默认paper。主题立即独立提交，其余七项保存仍由设置页负责；不持久化预览草稿。跨同源窗口同步已保存偏好并保留本窗口预览；不同端口/profile各自独立。英文/中文字体只有显式选择 `theme` 时才使用主题推荐字族，字号、密度、行高和动效优先于主题。完整合同见[主题技术参考](TECHNICAL_REFERENCE.md#theme-switcher)。

CSS按基础token/组件 → themes → theme_picker → appearance_overrides加载。费用SVG直接读取独立系列变量；Sigma通过根属性观察器重绘现有图数据、标签与颜色，不重启布局、不改变相机/选择/固定节点，释放时移除观察器。无新增业务端点、权限、数据库字段或依赖。

## 开发验证

- Python：`uv sync --locked`；`uv run pytest -q`；`uv run mypy`；`uv run ruff check src tests`；`uv run ruff format --check src tests`。
- 前端开发工具：Node 22.13+，`npm ci`；`npm test`；`npm run lint`；`npm run format:check`。生产静态页面不需要 Node。
- 分发：`uv build`；独立安装 wheel 后验证所有 ES module URL/MIME 和 HTTP 闭环；解包 sdist 后运行测试。
- 实际浏览器：合成隔离目录中的登录、授权、审批、撤销确认/取消、退出/到期、断连及桌面/窄屏操作。自动测试不代替可见界面验收。

## M3 受限执行

新增独立 execution_config / execution_ledger / model_gateway / execution / execution_cli 模块与容器 executor 目录。执行与预算完全属于后端，现有前端模块和 M2 HTTP 合同保持不变。完整数据流、配置和操作见 [M3_RUNBOOK](M3_RUNBOOK.md)。

## M4 文章解析、隔离采集、Pi 绑定与发布

`articles.py` 提供 `parse_article(raw_html, source_url)` → `Article` 和 `complete_article(article, images)` → `ArticleDraft`。仅处理字节与类型化数据，不访问网络、模型、数据库或 Vault；图片缺失、格式不符、超限即拒绝生成草稿。本地图片路径确定且相对，便于后续版本目录整体发布。

`vendor/article_parser.py` 是用户指定 Skill 的固定定义子集；来源哈希在同目录 JSON，完整性由测试验证。该第三方源码保留原风格，不参与第一方 Ruff/mypy 检查；类型校验及错误转换集中在受严格检查的 articles.py。旧 Skill 的 Vault 写入、执行入口及容忍缺图的发布逻辑均未纳入。

X 必须提供原始 HTML 中的 Draft.js 或 content_state 序列化；仅有渲染后 DOM 不够。宿主正常v2自动入库不限制图片数量；内部解析接口仍接受显式上限并在超出时明确拒绝，避免静默漏图。单图12MiB及总图80MiB字节资源保护仍有效。固定手动worker镜像独立绑定，不能据宿主验证宣称镜像已更新。图片签名检查不能替代完整解码；真实 X 验收另用系统解码器验证。原始 HTML 必须由后续采集流水线保留，不把 Markdown 当作原件替代品。

`article_fetch.py` 仅在固定采集容器内访问公开 HTTPS；每次跳转重新检查 URL 和 DNS，连接固定已检查 IP，并保留 TLS 主机名验证。无代理、Cookie、凭证转发或自动重试；响应与 gzip 展开均有大小上限。

`article_capture.py` 管理输出合同：先保留原始 HTML/图片，最后写成功清单；宿主在容器退出后重查文件类型、大小、哈希、来源及图片顺序，并重新解析原始 HTML。宿主不接收 worker 自行生成的 Markdown。部分文件保留供排错，重试使用新目录。

`article_worker.py` 负责固定镜像、单目录挂载、资源限制及生命周期。先创建未启动容器，再检查调用方提供的 guard，随后启动；运行中和交付前继续调用 guard。`article_tasks.py` 将该 guard 绑定到当前任务、配置摘要、客户端状态和新增权限；负责调用、幂等复核、取消及孤儿采集器停止。

`article_worker_entry.py` 是固定入口；只接收 URL，输出路径固定为 `/capture`。构建上下文是 `src/fiber_bundle`，Dockerfile 为 `article_executor/Dockerfile`；构建后用 `docker image inspect` 得到完整 sha256 ID 传入 `CapturePolicy`，拒绝可变标签。构建和内部 worker 均不需要模型 key。源码包/wheel 包含所需文件。

`article_bindings.py` 校验收件所有权、库/来源版本绑定和已保存 URL 原件，定义固定模型请求及脱敏状态；`article_schema.py` 是仅新增一张表的 schema 4。原读取执行继续要求 query + original，文章执行只要求本人收件及当前 add，不把两者权限混用。迁移须显式调用并先备份，旧表与原件保持不变。

`executor/article_tool.mjs` 仅发出无参数的命名请求。文章模式的 Pi 无外网、无 read/write/edit/bash，采集输出不在其挂载范围内；宿主从可信绑定取 URL 和镜像，并完全重建模型请求，文章/链接及 Pi 自带文本均不进入云模型。入口仍是可信本机 CLI，现有 HTTP/UI 合同保持不变；用法见 [执行说明](M3_RUNBOOK.md)。


`article_publication.py` 负责当前所有权/add 门禁、采集复核、稳定内容身份、逐提交原件归档和发布账本状态。`publication_files.py` 只处理独占写入、文件集合复核、目录同步及原子 no-replace rename；不访问授权或数据库。`publication_schema.py` 提供 schema 5 的两张加法表；`publication_cli.py` 仅解析参数、认证及转换结果。底层 Store 的 `ingest_locked` 是供已持共享锁的可信领域函数使用的显式边界，自己管理数据库事务。

原件来源版本和笔记发布内容身份分离：完整原始响应作为正常 source_version 保存和授权回查；发布内容摘要仅比较来源元数据、全文、图片路径和字节，动态 HTML 不制造重复笔记。每次任务通过 article_publication_runs 关联自己的原件收件与复用/新增的笔记。原件先持久，账本 preparing → ready → published；不把文件存在或 Pi succeeded 单独当作发布成功。阶段细节与恢复限制见操作说明。


## M5 只读检索首项

`note_index.py` 负责受限目录遍历、UTF-8 Markdown 快照及原文行片段；`note_publications.py` 将现有发布账本转换为标记摘要并验证移动目录的身份，不写文件或账本。`note_inventory.py` 复用目录身份、发布账本与快照读取，供已授权检索/对账调用。`search.py` 负责先授权再读取、默认库选择、确定性匹配、来源分类及引用；`search_routes.py` 只负责 `/v1/search` 的请求校验和认证。API 入口仅组装路由，前端文件没有检索或存储业务。

首项使用每次请求重建的内存索引，不建立持久全文副本，不修改笔记或 schema。默认范围是当前 query 授权与 default_query 开关的交集；显式范围全部先验权，任一不允许即拒绝整次查询。待发布账本目录、隐藏目录、未登记的发布标记目录、符号链接不读；未登记文件身份的已发布目录只读取 note.md，已登记文章可在有效发布目录内解析改名文件，普通历史 Markdown 不依赖 MOC。交付前再次验权和重读命中文件摘要，变化即失败，不交付旧片段。

搜索进行 NFKC/大小写归一、空白分词后的 AND 字面匹配；ASCII 词有字母/数字/下划线边界，正则字符按字面处理。排序按标题/正文命中及固定路径顺序；同分时优先非 Markdown 标题的原文片段；只有标题命中则保留标题。每条笔记最多一段 400 字符原文，返回实际行号与 SHA-256，不改写或替用户裁决冲突。历史笔记标为来源未核实；发布笔记按账本标原文或用户修订。原件链接只在另有 original 权限时返回，下载接口继续实时验权。

当前每库上限：单笔记/发布标记 1 MiB、正文及标记合计 32 MiB、目录项 10000、深度 32；越限、不可解码或非独立普通文件拒绝整次查询，不返回残缺搜索。单请求最多 20 库、8 个查询词、20 条结果。已发布文章整目录在同库移动时，以完整标记摘要对照账本维持来源版本；重复标识冲突拒绝，交付前重验标记。未登记文章只读取 note.md；登记后在标记目录内有界寻找同一物理 Markdown 文件以支持改名，不采纳未知/未发布或其他库标记。当前不含后台持久索引、文章单文件移出发布目录或新前端；显式跨库迁移见下节；检索规模与性能尚未作大库验收。

M7 显式本机对账由 `note_changes.py` 的元数据合同/比较规则、`note_snapshots.py` 的授权/范围/有界观察与不可变存储、`note_changes_cli.py` 的参数及退出码转换组成。复用 query 能力及 storage.lock，不建新数据库表；两次范围一致的读取出现差异或撤权即拒绝捕获，绑定当前 Vault dev/ino。JSON 元数据快照以 SHA-256 寻址，按客户端和 Vault 隔离，0600/O_EXCL/fsync；最大 10000 条/4 MiB，读取验证摘要、严格字段、顺序、身份与当前目录。完整写入前中断可能留下拒绝读取的文件，不自动删除/覆盖。compare/check 不写快照或笔记；路径或哈希改变是冲突，缺失不等同跨设备删除，检查不授予后续写许可。该元数据快照不提供正文历史或原子/远端一致性，真实同步和后续写入保护须独立实现与验收。


M7 旁路身份由 `note_identity_store.py` 的独立 SQLite ledger（schema 1）和 `note_identities.py` 的登记、解析、显式重绑组成。复用 storage.lock 与 Vault query/add：解析只在已授权 query 范围读取，track/rebind 同时要求 add；身份按 Vault 共享，私有快照仍按客户端隔离。初次显式登记创建 0600 sidecar，读取不建账本、不修改行；每库最多 10000 登记，文件限 64 MiB，拒绝符号链接/硬链接/非普通文件及未知 schema。revision 乐观检查与追加事件同事务，不迁主库、写笔记或猜哈希合并。

`note_index.Note` 保存内部物理文件 key（dev/ino/创建时间）及可选 identity，索引入口 note_inventory 读取登记后按 key 解析历史笔记及已发布目录中的单文件改名。无创建时间不能登记；同内容副本不获原 ID，替换文件需显式 rebind，旧可见原文件/其他身份/来源不符/哈希或 revision 变化拒绝。检索仅对登记项增加可选 note_id，并在交付前比较 key；快照使用 note:/publication: 身份，旧任务证据变化即拒绝。创建时间取本机文件系统提供值，不代表跨设备同步身份；读后替换、真实同步/断电及跨库迁移仍需独立验收。完整目录标记始终对照 M4 账本，不因旁路记录放宽来源校验。

### M7 跨库迁移的元数据预检

`note_migration.py` 负责两端 query/add 先验权、当前历史笔记/稳定登记绑定、目标空位/父目录、两端可见 Markdown 和 MOC 元数据的有界观察，以及固定计划失效检查。`note_migration_plans.py` 校验严格 schema 1、规范 JSON 摘要寻址、私有且有界的不可变记录；`note_migration_cli.py` 仅解析 plan/check 参数、令牌和安全错误，独立控制台入口不改旧笔记/MOC 命令。

计划只存身份/路径/摘要及白名单审阅提示，不缓存正文或附件，不改变主库、身份/MOC 账本和任何 Vault 文件。调用方及 SOURCE/TARGET 构成读取/存储作用域，先核验两端授权再读计划；完整登记和两端可见清单/MOC 摘要冻结后，重新观察只报告冲突，不覆写计划。落盘前验权、保存后观察及交付时根身份/权限复验；中断可能留下有效或不完整的元数据文件，有效记录可去重复查，不完整记录拒绝、保留且不修补，始终没有执行 claim。

双链/Markdown/HTML 媒体、现有 MOC 归属及不同文件系统仅产生保守 review_flags；不声称完成入链解析、附件归属或发布原件迁移。没有 apply、自动批准、身份转移或回退执行，schema 1 不能解释为未来写入授权。后续文件与身份事务应另建固定执行意图，并在每个副作用边界复验两端及依赖，不能把预检 match 当作原子性或整体 M7 验收。

### M7 同库 MOC 关联元数据

`moc_store.py` 封装独立 schema 1 的 moc-associations.sqlite3、严格元数据合同、修订检查及追加审计；`moc_associations.py` 负责同库稳定笔记身份、manual/system 并存与只移除 system 的规则，以及当前路径的 Markdown 预览；`moc_cli.py` 只处理参数、token 文件及安全退出。主库 schema 7、HTTP 与前端契约不变。复用 storage.lock，变更须 query/add，预览只需 query；同库获授权客户端共享关联，Vault dev/ino 绑定阻止根目录替换。笔记目标须当前可见、稳定 note:/publication: 身份及匹配 SHA-256；修订冲突、未知身份与跨库关联拒绝。

账本 0600、64 MiB 读取上限，每库最多 1000 个 MOC、每 MOC 1000 个成员/256 KiB 记录；读取不创建或修复账本，拒绝危险文件、非规范记录及未知 schema。当前关联与完整元数据审计同事务，事件失败回滚本次更改，重复状态不增加修订。两次有界 note_inventory 观察路径/摘要/物理身份，并在持久化和交付前验权；不作为原子云同步证明。缺失成员保留归属，预览不输出有效链接；本机改名解析当前路径，不追踪跨库副本。Markdown 转义标签并编码 Vault 根相对链接，不包含笔记正文。关联子项不写 MOC 文件、推断旧属性、自动分类或接入发布/撤销；独立文件投影如下。检索不读取 MOC 账本。

`moc_markdown.py` 只解析无歧义人工双链并拼接已核验的系统区块，保留原 MOC 其他字节；`moc_file_journal.py` 管理私有有界计划、候选、独占 claim 与不可变 outcome；`file_exchange.py` 封装已打开目录间的原子 exchange/no-replace 和有界文件读取；`moc_files.py` 负责授权、稳定关联/源快照、修订与摘要、规划、应用及显式对账。CLI 按动作路由，不把文件操作堆入关联元数据模块，也不改主库/关联账本 schema。plan/apply/reconcile 均需 query/add，范围按 Vault/MOC 共享，计划以完整规范 JSON 摘要绑定路径、根/父目录、MOC 修订、源清单、旧/新文件及系统区块摘要。

候选和备份有正文，位于 state_root 私有计划目录（每 MOC 最多 1000、单文件 1 MiB）；不是前项纯元数据快照。现存目标须为可见的历史 Markdown，可已 track；文章目录、隐藏/危险文件、消失/改动系统区块或源证据变化拒绝。新文件 no-replace，已有文件 exchange 原子取得被替换的原 inode 作备份并核验；系统支持不足/跨文件系统无降级写法。完成后不可变 outcome 表示实际核验结果；claim 之后中断不自动重放。若安全核验显示已交换的候选仍未被改动，可补偿交换回取得的原文件；否则保留双方和 pending。reconcile 核验当前文件/备份，不写 Vault、不推断未知结果；已登记目标可依据严格绑定修复身份账本。多文件/数据库/云端原子性不作承诺，真实多端同步和 Linux 分支仍待验收；发布与撤销的显式接线见下文。L2 仅作格式样本，其包内权限规则不接入应用。

`moc_file_identity.py` 封装已登记历史 MOC 的旧记录捕获、schema 2 身份合同、单目标临时解析及 CAS 登记完成。schema 2 在私有暂存目录先写候选，取得完整 dev/ino/birth 后连同旧登记记录纳入计划摘要，再 no-replace 发布计划目录；schema 1 保持未登记目标合同。`note_inventory.read` 的内部 projection 参数只替换经核验仍为旧值的一条历史登记，不禁用其他登记或文章 provenance；同时要求新文件完整物理身份/摘要匹配且旧物理文件不在可见笔记中。完成顺序为 claim、文件交换及上下文复验、身份账本事务、outcome。未提交账本时可安全补偿文件；账本已提交后保留新文件与 pending，显式恢复幂等完成。主库、MOC 关联及身份账本 schema 均不变，只有已登记目标的写入计划新增 schema 2；track/rebind 不增加正文写权限，新 MOC 不自动登记。

`moc_file_identity.check_moved_target` 核对最后已应用 schema 2/3 的稳定 ID 与完整物理文件身份，允许同一 Vault 内的外部改名/移动接续文件计划；旧路径须无可见笔记，原系统区块仍按已完成摘要核验。moc_files 在规划、领取及交换后复验路径衔接，拒绝副本、替代身份和旧路径竞态；交换后检查失败且身份事务未提交时按原规则补偿。系统链接按新目标目录生成，人工字节不重写。同层改名无内容变化时不产生新计划/身份修订，物理身份解析仍返回当前位置。既有计划保持固定路径、schema 与摘要，不重定向旧计划，不新增移动账本或迁库行为。

`moc_dispatch_store.py` 封装独立 schema 1 元数据队列、客户端/Vault 范围、规范请求摘要 ID、CAS 状态与事务审计；`moc_dispatch_source.py` 核验发布 run/账本/归属和现行取消门禁，并计算实际成员证据与人工双链解析摘要；`moc_dispatch.py` 负责显式入队、默认关闭的有界领取及不重放的显式恢复；`moc_dispatch_cli.py` 只解析参数/凭证及安全输出。复用主 storage.lock，既有发布、元数据关联和文件投影均保持独立职责，没有新增 HTTP/前端或生产服务接线。

schema 3 计划持久记录 dispatch_source，包含调用方、Vault、发布 run/id、MOC 修订、路径、期望摘要和请求 UUID；创建候选后绑定 dev/ino/birth，摘要寻址后入队。文件入口在读取、交换及身份提交边界重验来源，即使由旧 apply-file 入口应用也不能绕过取消。schema 1/2 保持原观察合同；schema 3 的依赖摘要排除无关笔记正文，但全库身份/文章来源解析仍不降级。作业 claimed 先于文件 claim，完成先于队列 applied；两个账本与文件没有整体事务。未决作业阻止同客户端/Vault 后续领取，显式 reconcile 不执行 planned 文件计划，只退休其意图；已知 pending/applied 复用文件证据及身份修复。恢复仅处理既有副作用，任务取消/归属移除/来源笔记缺失不要求重放或把文件身份永久留在旧状态；当前授权、发布绑定、根/文件证据及其他身份校验必须继续成立。语义分类和长期轮询部署保持未启用；显式发布入队与贡献撤销见下节。

### M5 当前会话资料

`session_search.py` 负责本人收件/会话引用/Vault query 授权交集、已存文本正文读取与原件链接；不负责发布和内容提取。共用的字面匹配移至 `search_matching.py`，search.py 只在既有笔记流程外组合会话结果。Store.verify_receipt_locked 明确提供收件清单摘要和完整版本/资产元数据关系校验，不跨模块调用私有存储方法。

客户端必须显式携带当前 session_id；命名空间固定为已认证 client ID，旧会话仅在指定其 ID 时可查。会话候选要求 owner、ingress_scope、session_refs、原始收件清单中的 session_id 同时匹配，避免内容去重后的共享版本把别的会话收件混进来。仅索引 stored 的 text/plain / text/markdown body；每个正文重新核验对象路径、大小、SHA 和 UTF-8，结果交付前重查 query、会话引用及对象摘要。未完成收件、原始事件、URL/HTML/二进制/附件不进入片段；不据 MIME 自动当作已核验专业知识。

每请求最多 1000 个符合授权/引用及显式库范围的候选收件，每正文 1 MiB、累计正文 32 MiB；超过上限拒绝整次查询。会话结果按匹配得分与稳定收件/资产 ID 排序，优先于笔记后共用 limit；不把相同正文的再次提交从收件记录中删除。结果引用仍只针对本次响应；长期身份和完整会话切换/清理 UI 留在后续阶段。

### M5 原文证据式回答

`answer_routes.py` 仅解析 `/v1/answers` 请求、认证和转换返回值；`evidence_answers.py` 复用授权检索，组合原文、定位、不足提示和有限的同单位异值候选；`answer_contract.py` 用严格结构校验确保每条引用逐字等于服务端片段，覆盖全部已检索片段，不允许自由事实断言。HTTP 请求沿用共享存储锁直至交付，原件读取权限继续独立。

问题与字面检索词分开传入，不把字面命中当作语义回答充分。当前是无模型、无持久副作用的原文证据接口；同单位异值仅辅助上下文核对，不构成语义冲突检测。真实 Go 验收使用独立临时数据库、预算及真实 Docker/Pi，由验收宿主固定合成引用白名单替换模型请求；复用相同引用合同校验输出，不给正式 HTTP 接口启用模型或授权真实正文外传。


### M5 会话来源绑定的 Pi 执行

`answer_tasks.py` 封装收件/会话及单库纯笔记绑定、不可变证据快照、固定模型请求、输出校验和实时交付。AnswerInput/NoteAnswerInput 显式区分输入，API/CLI 只做转换；两种任务共用 worker、预算及输出合同。`answer_schema.py` 保留历史 schema 6，`answer_migration.py` 提供 schema 7 兼容重建；database 负责持久备份、事务及私有连接的外键策略。execution_runs.binding_kind 与 answer_tasks.input_kind 区分旧来源/收件及纯笔记，旧行默认旧类型，只有 notes 允许关联来源为空；应用授权再次验证两表类型一致。纯笔记不伪造来源或收件；只要求当前 query，与普通 M3 原件读取和 M4 add 语义分开。

`execution.py` 仅在已有编排点调用 answer 模块：发送前重查快照、宿主构建请求、结算后验权并校验输出、Pi 完成后重查快照。bridge 的 answer 模式不加载扩展、禁用工具，容器无外网。输出仅保存严格合同的引文；一次调用后禁止再次请求。失败的响应不落入结果栏，已发生预算仍保留。

`GET /v1/answers/{run_id}` 在共享 HTTP 交付锁下验证任务所有者、当前 query、收件/会话引用、完整检索快照及成功状态，再用当前 original 权限构建原件入口。模型执行不占用整个请求的共享锁，允许撤权/取消在生成期间生效。快照变更需显式创建新任务，旧结果不作为当前资料交付。当前不作自由生成、语义冲突裁决或模型推理事实核验。


### M5 问答任务提交 API

`answer_submission_routes.py` 仅处理请求校验、认证与结果转换，`answer_submissions.py` 负责客户端内 request_id 去重、输入/策略一致性及状态投影，复用 answer_tasks 与 execution_ledger。唯一入口 app 只注入可选的宿主 ExecutionConfig；服务启动校验策略，API 请求不接受模型/地址/秘密/任意库覆盖。原 CLI 创建语义保留，新 submission_id 为内部可选字段，不进入模型上下文。

同一存储锁串行保护查询旧任务与事务创建，request_id 随现有 request_json 原子写入；相同 ID 不重复创建或执行，差异请求拒绝。状态仍按任务所有者及当前 query/会话引用检查；结果交付另外检查快照。任务提交 API 不在 HTTP 请求里启动 Docker/模型，也不引入自动后台执行器；由独立执行命令或下述限量队列执行器运行排队任务。


### M5 队列执行边界

answer_worker.py 只选择任务、做启动前检查、持有进程级独占锁并调用现有 execution.execute；answer_worker_cli.py 只读取宿主配置与批量上限。它不复制预算、模型 HTTP 或 Pi 生命周期逻辑，不引入数据库迁移或 HTTP BackgroundTasks。原有运行锁解决与手动执行竞争；worker 锁防止多个批量领取进程。领取范围为配置一致的 queued 问答任务且无调用记录，FIFO、有限批次、遇错即停；无常驻轮询或服务自动安装。


默认/跨库笔记任务由 `answer_scope.py` 独立封装输入类型、检索范围解析、持久范围检查和版本化证据快照；复用 search.scope 的 query/default_query 交集规则。request_json 保存原始选择方式和已解析 vault_ids，snapshot_json 保存同一列表及引用来源。创建时固定范围，重放提交不重新解析默认库，执行/交付则对全部已绑定库重新授权。旧任务走原单库格式，新快照使旧执行代码比较失败。API、CLI 只转换输入；execution_ledger 仍负责状态及统一费用账本，未引入新的数据库表或前端业务逻辑。


### 可选自然语言草稿合同（用户已批准）

`answer_drafts.py` 是独立、无副作用的合同校验模块。answer_generation 封装持久模式识别、提示词、模式快照、输出转换与交付警示；answer_tasks 只组合生命周期与授权检查。供应方候选结构为 `mode=cited_draft`、`claims=[{text,citations}]`、`quotations=[{citation,quote}]`；最多 8 条概括、每条 600 字，每条至少一个当前证据引用，全部检索片段仍须逐字保留。禁止额外字段、未知/重复引用、改写/遗漏原文，以及概括正文内嵌数字引用标记；引用由结构字段提供，后续展示层负责呈现。允许模型以空 claims 表示证据不足，无证据时禁止生成概括。

模块仅验证引用一致性，**不验证概括是否被原文蕴含或是否回答了问题**。返回的 `citation_integrity=verified` 只表示编号/原文校验通过；每条概括固定 `semantic_support=not_verified`，整体为 `unverified_draft`，语义充分性、冲突、时效均 not_assessed。模型不能覆盖这些字段或宿主警示。测试包含“正确引用搭配错误概括”的反例，明确该边界；不能把结构测试通过称为回答正确。

`answer_model_output.parse_response` 提供有界响应/严格 JSON 解码，旧 validate_response 继续调用原逐字引用合同，错误码与行为不变。候选通过相同解码器拒绝截断、工具调用、重复 JSON 字段等；不增加模型调用或保存供应方原始响应。用户已批准可选模式：三个持久任务 API 接受显式 cited_draft，默认及旧记录仍为 extractive；无模型接口不变。模式参与幂等身份和 answer-mode/v1 快照，以拒绝旧 worker 或静默模式变化。仅存经过校验的提案字段，交付时重新校验并生成宿主警示；来源类型、原件权限与差异提示保留，不自动归档草稿。SPEC 未修改。


### 草稿固定评估集

`tests/fixtures/m5_draft_eval.json` 固定 16 个合成场景、32 个正反例，覆盖问题/型号相关性、条件/否定/单位保留、冲突并列、历史/无日期资料、证据不足/因果边界、用户修订/会话主张/正文指令及引用与主张对应。每例包含问题、带来源类型的证据、评审要点、预期弃答或限定概括、示例及反例原因。样例由助手编写，**不是独立人工 Gold，也不是真实模型输出**。

`tests/draft_eval_support.py` 仅离线回放实际 prompt/accept/deliver 函数，校验合同结果和宿主未核验标记；它不调用模型，也不基于关键词判断语义。样例标签仅在完整提案与本例固定样例完全相同时关联；新概括为 unreviewed，独立审核始终 not_run。报告分别记录 contract_regression、accepted_counterexamples、model_quality_evaluation=NOT_RUN 和 semantic_accuracy=null，不把合同通过率写成回答准确率。

当前 16 个反例中 15 个仍可通过结构/引用校验，唯独无证据编造被拒绝；这是已知合同边界的回归证据，不是 15 个错误答案已被语义过滤。所有通过的草稿仍带未核验标记，生产输出合同保持不变。样例和报告绑定摘要，sdist 显式包含回放辅助程序与 fixture，便于后续在获得模型调用授权后逐例评审。

草稿提示词现要求 compact JSON、优先一两条必要的简短概括、避免重复结论；这是生成指导，不收紧现有最多 8 条/每条 600 字符的公共合同。全部原文引用、条件、不确定性及冲突保留要求不变，finish_reason 非 stop 仍拒绝。输出预算继续由显式 ExecutionConfig 控制，生产执行没有自动增加预算或重试；本次验收按用户新授权另建任务从 512 提高至 1024，未修改长期配置。单次提高上限后成功不证明固定必要阈值，也不保证后续不会截断。

### 离线逐例审核记录

`draft_review.py` 负责生成待审包与验证明确填写的评审；`draft_review_cli.py` 只处理文件、参数和结果，不访问数据库、Vault 或模型。待审包展示问题、证据、评审要点与候选提案，不展示固定参考答案；完整案例和候选分别绑定 SHA-256。记录时重新生成待审包比对，且 assessment 必须引用该包摘要，每个评审项恰好有一条 pass/fail/uncertain 与非空理由。结果优先 fail，其次 uncertain，全部通过才记录 reviewer_outcome=pass。

程序合同结果 contract_observed 与 reviewer_outcome 分开，合同拒绝的候选也能保留评审意见。审核身份是 self_reported，即使填写 human 也不认证独立性；semantic_support 和 independent_review 始终 not_verified，production_promotion=none，不更新生产答案或知识库。摘要用于绑定内容，不是数字签名或身份凭证。CLI 严格读取显式指定的 JSON（每文件至多 1 MiB），以 0600 独占创建新输出，拒绝覆盖；中断可能留下不完整文件，需另选新路径重做。

`draft_review_summary.py` 汇总一个批次的案例覆盖率与问题清单。批次每例最多一个候选，嵌入 proposal/packet/review，未审核时 review 为 null；不同候选或不同审核者使用不同批次。以完整 fixture 为预期集合（1–100 例），遗漏不缩小分母，分别列 missing、pending、pass、fail、uncertain，按 dimension 计数。汇总前重建待审包，并通过 draft_review.validate_record 重查记录摘要、完整评审项和派生结论；未知/重复案例、内容变化或不一致记录拒绝整批输出。合同拒绝即使搭配填写者 pass 也进入 attention_case_ids。完整覆盖只表示每例有记录，不产生正确率、自动质量通过或生产发布；评审理由和摘要保留在本地报告供回查。


### M6 待确认微信收件日志

`weixin_events.py` 校验可信本地 Binding、平台事件及附件输入，`weixin_inbox.py` 负责授权复查、持久化及游标事务。initialize/cursor/receive 是供后续适配器使用的本地 Python 接口，没有 HTTP/CLI 入口；Binding 不能来自消息正文、昵称或转发信息。只接受指定本人发给指定账号的非群聊事件，其他事件仅计忽略数量。

独立 weixin-inbox.sqlite3 使用自身 schema 1，主库保持 schema 7；沿用主库进程锁，每次操作重新检查客户端 add 权限及库状态。原始事件逻辑 JSON（规范化编码，非原始 HTTP 字节）、已取得的附件 BLOB 与新游标在单一 SQLite 事务提交。文件以 0600 创建；普通结果仅含 job ID、状态、忽略数和 dispatched=false，不含正文、账号或 context_token。备份需额外包含此日志，现有主库单文件备份不覆盖它。

响应最多 8 MiB/100 条事件，每附件最多 8 MiB，整批原始响应及附件合计最多 16 MiB。多文本、附件及一层引用均保留；未知类型为 unsupported，缺少附件为 incomplete，完整事件也仅为 pending。确认前不创建主库 receipts/session_refs/版本/执行任务，不进入检索、模型或发布。下载与补齐恢复尚未实现。

游标使用预期值比较，重复投递验证原始内容和附件摘要；同文新消息 ID 独立收件，同 ID 内容变化拒绝整批。重放不能静默补齐或覆盖附件，后续须有显式恢复流程。磁盘错误及进程在游标更新时退出均须完整回滚。当前只完成日志核心；Hermes 源码未修改，尚未在 getUpdates 后、原游标保存与内容去重前接入，也未部署常驻接收或验证真实平台身份。


### M6 Hermes 早期接线候选

`weixin_hermes.candidate(source_bytes)` 只为已核对 SHA-256 的 Hermes weixin.py 生成新文件内容；源码变化即拒绝，既不覆盖安装文件也不启动网关。候选替换 `_poll_loop`，并把 connect 的本地预检/入口锁错误改为拒绝启动、禁止接管现有进程。原飞书代码不涉及。完整候选可编译；验收从实际候选提取 connect/_poll_loop/disconnect，在隔离对象中回放，不等于完整 Hermes 导入或真实平台验收。

可信本地启动方须先显式初始化 inbox Binding，再在候选 adapter.connect 前调用 `weixin_receiver.configure(adapter, config, binding)`；未初始化、账号不匹配、正在运行或非候选适配器均拒绝。尚未安装网关启动配置，普通 Hermes 实例没有自动接入；不得直接替换文件后按原配置启动。正式切换前仍须停止原入口、确认账号和单 poller，并保留回退配置。

候选轮询仅调用 Hermes `_get_updates`，35 秒长轮询参数、45 秒总等待上限；以隔离日志游标为唯一续接来源，不读/迁旧 sync buf。持有独立 receiver 文件锁跨网络等待，每次轮询前与提交时重查权限。取得响应后同步事务收件，再发起下一轮；不调用旧消息处理、内容去重、Agent 或发送路径。当前仅保留媒体描述，缺件继续 incomplete，下载/补齐尚未实现。

异常即停止，不自动重试或自动回退旧入口；错误只报告 preflight/lock/fetch/capture 分类，不输出第三方异常中的正文或凭证。取消、失败及正常停止均经候选原 disconnect 关闭会话并释放平台锁，避免当前 poll task 自己等待自己。取消发生于网络等待时不推进游标；取得响应后事务提交不被协程取消打断。状态目录锁约束本系统接收器，跨 Hermes 实例仍依赖 Hermes 的平台锁，真实跨进程网关竞争尚未验收。


### M6 持久批次与四操作

`weixin_batches.py` 独立负责收件分批、冻结、选择与分页恢复；本地可信 API 为 initialize/advance/get/list_batches/choose。initialize 在已有隔离日志中显式增加 wx_batches/wx_batch_members 及单账号唯一缓冲索引，不改旧事件/附件表、日志 user_version=1 或主库 schema 7。候选 receiver.configure 初始化此扩展并接受 quiet_seconds（默认 3，有限正数且不超过 300）；修改只影响新建批次。正式部署及备份仍需包含完整隔离日志。

advance 以首次收件 received_at 和事件顺序分配成员，重复事件不再次加入；相邻间隔小于窗口时重置截止时间，达到截止时间即冻结。延迟 tick 会依据已持久时间重建分段；冻结后即使新事件时间倒退，也不得扩大旧批次。当前时间回退不强制冻结未来事件。提示发送层只能使用已冻结范围；本阶段没有发送提示，因此 frozen 仅表示范围已固定，不表示用户收到确认提示。

候选接收器在启动、每次成功收件后和长轮询等待期间调用 advance，计时检查间隔为 min(quiet_seconds, 1 秒)。批次失败使接收器停止，但不回滚此前成功提交的收件与游标，重启可从未分配事件恢复；网络取消时回收未完成 fetch。已有事务锁确保收件、分批、选择及撤权串行化，成员与状态更新整体提交。

choose 必须绑定明确 batch_id，操作为 save_one（保存为一份）、save_separately（分别保存）、consult（仅咨询）、cancel（取消）。保存/咨询要求所有成员完整且类型支持；consult 未带问题则 needs_question=true，可一次补充问题。选择持久且重复请求幂等，冲突选择拒绝；尚未交接执行的 selected 批次可取消，保留原件及已填写问题。取消不能恢复成保存。所有返回均 dispatched=false；selected 表示记录用户选择，不能称为已保存、已回答或已交付。

来源正文不解释为操作命令；choose 仅允许后续经过身份认证的可信命令适配层调用，不能直接将转发文字映射为选择。当前没有自然语言意图路由、单条资料默认保存、自动识别同批问题、显式批次合并、确认提示/提醒/回执或 Store/模型交接。它们沿后续 M6/M12 阶段推进，不由本模块猜测。前端尚无本批次功能，展示层未来通过服务接口接入，不访问 SQLite。


### M6 附件补齐与恢复

`weixin_recovery.py` 提供可信本地 initialize/view/complete 接口。initialize 显式增加 wx_recovered_media 附表，绑定 account/event/item_key、原始事件摘要及新增字节摘要；旧事件、首次附件、收件状态和游标不修改，日志 user_version=1 与主库 schema 7 不变。view 只返回 job ID、原事件摘要、缺少的位置及当前状态，不返回正文、下载凭证或附件字节。

complete 必须携带 job ID、预期原事件 SHA-256 和位置到 bytes 的映射。每次重新检查本人绑定、add 权限、原始事件/附件/补件摘要及原状态；只允许原事件声明的位置（包括一层引用的 .ref），不能替换首次附件或既有补件。每附件至多 8 MiB，事件与合并附件继续按 prepare 的 16 MiB 上限校验；允许分次追加，重复相同字节幂等，任一冲突或写入失败回滚本次全部新增。已取消批次拒绝补件。

补件不会改变原始投递重放结果；receive 仍按首次附件/状态严格比较。批次 snapshot 通过 recovery.current_state 读取合并后的有效状态，成员与冻结边界不变；补齐可从 incomplete 变为 pending，未知类型仍为 unsupported。当前状态是“实际字节已在本地保存”，不认证文件内容或来源语义，也不自动选择、调用模型、发布或回复。

恢复表建立后，批次状态读取会校验对应原件及补件摘要；损坏时拒绝展示为可处理。完整原件和所有补件仍只在受保护日志内，备份/清理须包含附表。本阶段没有下载器或自动重试，没有把消息链接或任意 URL 自动交给网络；后续可信下载器须完成受控定位、超时、大小与来源校验后提交字节。真实平台下载/媒体过期与解密尚未验收。


### M6 受控附件下载接线

`weixin_download.py` 负责原平台媒体描述解析、来源限制、流式 HTTPS 与 AES-128-ECB/PKCS#7 严格解码；`weixin_media.py` 负责按原事件位置取描述、下载后向 recovery.complete 提交，路由/界面不处理网络或字节。恢复模块抽出 ensure_active，下载前和提交时均复查撤权/取消；网络等待不持有数据库锁。已存在的附件不再次下载，失败/取消不写补件，成功仍只标明原件完整，不产生模型、发布或回执。

来源字段来自已持久事件的 image/voice/file/video media，支持一层引用。加密参数只构造固定 novac2c 微信 CDN 地址；full_url 仅接受已核对七个微信 CDN 域名的 HTTPS/443，拒绝用户信息、片段、控制字符及其他主机，不接受正文/文件名 URL 或自定义 CDN 配置。AES 支持图片 hex key、base64 的 16 字节或 32 字节 hex 文本；加密参数缺 key、块长度/填充不合格都拒绝，不把密文当原件。

可选 weixin extra 引入 aiohttp/cryptography，uv.lock 保持原有包版本并新增依赖。每次请求使用独立无凭证/无 cookie 的 ClientSession，trust_env=false；无环境代理、无重定向、无自动重试，TLS 证书按原主机验证。DNS 返回的全部地址必须为公网，并由 connector 使用该次解析结果连接；30 秒总超时，关闭压缩解码，Content-Encoding 非 identity 拒绝，Content-Length 及流式累计双重限额为 8 MiB+16 字节，明文最多 8 MiB，补件仍遵守事件总量限制。

错误只输出固定分类，Source 的 repr 隐藏 URL/密钥，不输出第三方异常文本。测试在本地合成 HTTPS 服务验证证书连接、状态/重定向/编码、流式超限和下载到补件落盘；生产公网校验先证实会阻止本地服务器，随后仅测试内替换地址判定并信任合成证书进行正向回放。不是公网 CDN 验收。真实账号配置和 Hermes 源码未改，没有把下载自动接到 poll loop；后台调度/真实媒体、到期失败处理及确认提示仍待后续阶段。


### M6 有界下载账本与 worker

weixin_media_queue 负责 wx_download_attempts 加法附表、显式入队、状态/分页读取、领取/结算、重试和本地恢复核对；weixin_media_worker 只组合独占锁、顺序有界调度与现有 recover_item。表绑定 account/job/item、原事件摘要和尝试序号，保留失败历史；状态 queued/running/succeeded/failed/interrupted，不保存 URL、密钥或第三方错误文本。主库 schema 7 和收件日志 user_version=1 不变。

初始化、入队和每次读取/修改都复用 inbox 授权；领取事务在联网前持久标 running，worker 另持进程文件锁跨网络等待，数据锁只覆盖短事务。同账号存在未核对 running 时拒绝继续领取，失败结束本次运行；默认只处理一件，显式上限 1–100。字节提交后再结算；结算失败保留 running，不伪装成下载失败或自动重发。撤权使失败状态也无法落盘时返回 blocked/unsettled_attempt，保留记录，待权限恢复后核对。

reconcile 先取得同一 worker 锁，确认没有运行中的执行者，再只核对原事件摘要和实际保存字节；有字节结算成功，无字节标 interrupted，不联网。已取消批次也可以核对收件事实以消除未结算状态，但不能重试；批次取消状态不改变。retry 仅接受最后的失败/中断尝试且附件仍缺失，显式新建下一序号，旧行不覆盖；重复对旧尝试发起重试不会制造额外尝试：仍缺件时返回已有后继，附件已保存时拒绝不必要的重试。协程取消记录 interrupted；若无法写账本，保留 running 并继续传播取消。操作系统退出释放独占锁，已提交领取仍可恢复。

enqueue 只处理显式 job 的缺件，不把旧失败重新置 queued；未安装自动发现或常驻轮询。下载的 success 只证明附件已保存，不是资料发布或回答完成；前端及微信命令接入沿后续阶段处理。本项包括真实子进程领取后退出，以及“补件成功、结算写失败”的隔离验收，未使用真实微信/CDN/模型。


### M6 冻结确认与显式命令

weixin_confirmations 独立保存 wx_confirmation_prompts 与 wx_command_receipts，加法扩展日志，不改原事件、主库 schema 7 或日志 user_version=1。提示编号随机且稳定，绑定账号、批次与按顺序的 job ID/原件摘要；构造及处理时核验原件摘要和冻结范围。prepare 不宣称发送成功；pending/sending/sent/unknown 是发送事实，不能与批次 frozen/selected 混用。tick 补建冻结后未生成的提示以恢复中断。

receiver.configure 初始化扩展；每次 tick 先有界分类至多 100 条，再推进批次并补建提示。扩展启用后 batches.advance 只接纳已标 source 的事件，命令及尚未分类事件不混入资料；只要该账号还有未分类的已收件事件，就暂不推进/冻结批次，避免有界分类页把同一消息突发拆成错误范围。初始化将既有批次成员固定标为 source，不追溯执行历史资料中的命令。普通单文本只有以保留前缀“批次 ”开始才进入命令校验；完整命令须含有效的16位随机提示编号和四个明确操作之一，问题只允许咨询。引用/转发标记、多项内容、子串和非整数文本类型不执行。未标识的普通平台文本不具有独立来源证明；本项不声称已覆盖真实所有转发格式。

事件 envelope 再查绑定本人/账号及非群聊，正文不能提供权限；事件摘要、命令处理回执与 choose_in_transaction 在同一授权事务内处理，重复投递不会重复选择。咨询无问题时回执含明确补问文本；拒绝只含固定错误分类。原始命令仍在收件日志，原投递重放不变，命令不成为可检索/可归档资料。未做自然语言意图猜测、自动合并、长期提醒或 Store/模型交接。

weixin_prompt_delivery 独立封装显式发送 callback。先持久标 sending 再释放数据库锁调用可信账号发送器，接收人固定为 binding.owner_id，30 秒超时；仅明确 True 标 sent，其他结果/异常标 unknown，取消或授权/存储不可用时可能留 sending。不自动重发未知结果，不将外部异常文本写账本。两个并发调用只有一个能领取 pending。选项已被处理时拒绝发送旧确认菜单；发送后与处理前的权限/状态竞态仍由命令端复查阻止错误操作。当前 callback 仅合成验收，未接 Hermes send 或真实微信；回执文本也尚未接实际发送，后续须对接并验收未知结果处理。

### M6 Hermes 一次发送适配

weixin_hermes 候选仅新增显式 `_fiber_send_prompt/_fiber_send_receipt` 编排入口；原 send() 的分片、媒体解析、重试和会话过期无令牌回退不参与该路径。weixin_hermes_sender 封装单次文本请求，复用已核对 Hermes 的 headers/base_info；绑定身份与权限在发送门锁后重查，上下文从当前账号最新落盘原件读取并核验摘要，缺失则拒绝。不更新旧 token cache，不在日志/结果中返回令牌或第三方错误正文。请求限 8 KiB 文本，响应流限 64 KiB，30 秒总时限，不跟随重定向；整型 ret=0 且 errcode 缺省或整型零才确认。

weixin_receipt_delivery 是命令回执的独立发送账本 wx_receipt_deliveries，以 job ID 关联原始事件，保存命令结果摘要、历史文案和 pending/sending/sent/unknown；prepare 可从已提交的命令结果恢复，重复发送请求不重置既有状态。发送先领取再 await，取消/崩溃或撤权后结算失败保留 sending；网络/API 不明确结果记 unknown，不推断可安全重试。提示继续复用既有 weixin_prompt_delivery。两种发送均不宣称归档/回答/本人已读，未改变批次状态机、主库 schema 或自动收件调度；实际 Hermes 安装与真实通道保持未切换。

### M6 候选收件循环的有界发送

weixin_outbound_dispatch 独立选择待发命令回执和仍冻结的提示，复用既有发送账本，不增加重复队列。receiver.configure 的 outbound_limit 默认 0，显式范围 1–100，按一次 run 累计扣除，耗尽后继续收件。调度在 receiver 独占锁内、两次 fetch 之间串行执行；等待 fetch 时只冻结/准备提示，不 await 发送，确保返回响应优先落盘，避免发送失败抛错取消已返回但尚未提交的收件。

回执按事件 rowid 优先，提示按创建 rowid；每次发送前重查身份权限及账号账本。任一 sending/unknown 都使有界调度拒绝继续，包括重启后；发送未明确 sent 则停止 receiver，保留账本和原件游标。已 selected/cancelled 的 pending 提示保持历史记录但不选入调度。出站模块不负责解析意图、Store 交接或已读判断。默认零预算保持此前仅收件行为；未决恢复、真实微信和正式入口切换仍未验收。

### M6 已确认批次到 Store 的可恢复交接

weixin_store_inputs 只负责原件/补件验证和资料投影：复用 recovery.load 校验绑定、原事件摘要及附件字节；按消息 ordinal、item_key 保留文本/引用/附件位置，正文逐字保存，附件使用中性的二进制名称和 MIME。Store raw_event 为无平台秘密的 provenance 描述，完整原始平台 JSON 保持在收件日志，避免令牌/媒体密钥进入来源资产。

weixin_store.transfer 负责权限、选择和持久交接：保存为一份/咨询各一个来源，分别保存按消息一个来源；咨询 retention=session，保存 retention=knowledge，咨询问题为独立控制字段。现有 storage.lock 跨验证和 Store 写入全程持有，使用 Store.ingest_locked 避免递归加锁。新增日志附表 wx_store_handoffs 保存冻结计划及摘要、prepared/stored、已记录结果及摘要。全部资料先校验，计划先提交；Store 由自身事务落盘，再更新交接进度，固定 ingress/event/source 标识消除崩溃恢复重复。不得把 journal 与主库当作原子事务，多目标中断可能部分保存。

取消/撤权后拒绝继续，已存原件保留；get 只读历史交接状态及当前批次状态。交接成功不等于执行/发布/交付，批次 dispatched 仍为 false。本阶段只接显式四操作选择，不从正文猜自然语言意图，也不自动调用模型、文章提取、发布或回执。主库 schema 7、原始收件 schema 和平台状态保持。

### M6 Store 调度与尝试恢复

weixin_store_dispatch 封装资格选择、单次批次预算、独占 worker 锁、尝试持久化与显式恢复。wx_store_attempts 为日志加法附表，每次尝试单独保存 running/failed/stored；Store 成功后才结算 stored。写结果失败可能留下 running，原件和 Store 自身幂等标识保持。自动选择遇到选中批次的 failed/running 或直接交接的 prepared 会停止；resume 追加记录，不重置历史。取消批次不再参与调度且不能恢复，已存原件保留。

receiver.configure 增加默认 0 的 store_limit（显式 1–100），一次 run 总批次数限额，与出站消息预算分别计算。轮询之间先逐批交接，每批后 await 让出取消机会，再处理出站和下一轮收件；无正在等待的 poll 时才执行交接，返回的收件响应已先落盘。worker 锁跨尝试领取与交接持有，不改变 Store 的全批 storage.lock。同步单批内不承诺即时协程取消；它可能包含多个 Store 来源，仍不具备跨库全批原子性。已完成批次不自动重复交接，失败时 receiver 记录 fiber_weixin_store_failed 并停止，不调用模型或发布。

### M6 微信 Store 来源到既有任务输入合同

weixin_task_inputs 是本地输入适配边界，复用 weixin_store.read_locked 在共同 storage.lock 内验证交接账本、当前批次状态、确切父 receipt 和权限，再核对 Store 清单/字节。既有文章接口要求单 URI body，既有问答接口要求 client ingress/session；不放宽这两个现有鉴权合同。适配为客户端会话范围的临时 receipt，使用固定批次/父 receipt/任务类型标识幂等；父 receipt/version/asset 描述保留在 raw_event，原微信来源不改。正文对象复用内容寻址存储，问答问题不成为证据资产。

保存操作只允许文章输入，consult 只允许问答输入；多 receipt 明确选择，多个 body 不择一猜测；文章仅接受微信/X 单个公开 HTTPS 字面链接，问答必须 query 授权。prepare 返回 ArticleInput 或 AnswerInput，不提交 execution run、不启动捕获或模型，不向原批次写 dispatched。weixin_task_bindings 在共同 storage.lock 内复核批次后，先提交微信日志 wx_task_bindings 意图（固定 run ID、输入、任务类型、策略与交接计划摘要、规范请求），再用该 ID 创建原生任务。重复提交恢复同一 run；主库失败回滚任务而保留可恢复意图，提交成功后中断也不另建任务。主库 schema 7 不变，日志 schema 1 增加独立表。

weixin_task_guard 由文章和 receipt 问答鉴权入口调用；仅保留前缀 weixin-task: 的派生输入要求此绑定，原生任务合同保持。调用方持有共同存储锁时，以只读连接查询微信日志，避免递归锁。缺失绑定、计划/请求/策略不一致、取消或撤权均拒绝；覆盖启动、预算预留、文章捕获/发布鉴权及问答接收/交付。批次取消不直接重写 run 状态，不回滚已有外部副作用。submit 只创建 queued，不连接 receiver 自动调度，不运行模型/容器；有界执行和状态同步由独立模块负责，结果回执尚待接线。


### M6 微信任务调度与状态观察

weixin_task_dispatch 只处理显式绑定后的 run，default maximum=0；1–100 按运行尝试计数，worker 文件锁覆盖本轮，原生 run 锁继续排斥其他执行者。wx_task_dispatches 使用 run_id 唯一键记录 observed/claimed/finished/unknown，先持久 claim 后调用 execution.execute，执行期间不持有共同 storage.lock。账户/策略过滤后重查原生授权、问答证据与调用账本，只领取 queued 且无既往模型调用的任务。结果以主库实际状态为准，不信任执行返回值；失败停止本轮，未决领取阻止自动再调度。

weixin_task_status 将主库状态与当前批次状态写入同一调度行，保留领取阶段；只暴露有限元数据。取消同步先写主库 cancelled，再写日志快照，跨库中断可幂等重复；已完成成功/失败不改写、费用不清零。运行中取消依赖既有执行器的持续检查，不声称立即中止外部请求。reconcile 必须获得 worker/run 锁，仅结算日志中的未决领取，不恢复执行或重试；queued/running/missing 保持 unknown。finished 表示本次调度尝试已结算，实际 run_state 仍可为 failed/waiting_budget/interrupted，不代表业务成功。主库 schema 7、日志 schema 1 不变，日志仅增加表。receiver 未自动挂接 worker，也未新增前端或发送结果接口。


### M6 批次任务结果投影与一次交付

weixin_task_results 在共同 storage.lock 下核对 Store 交接、完整父收件集合、任务绑定与终态。文章回执仅陈述捕获账本状态；问答调用 answer_tasks.deliver 复验现有证据，保留来源标签和时效/充分性提示。完整结果摘要绑定回执，正文总量超过单消息限制则替换为完整结果可本机查看的通知，不做不完整引文拼接。尚有未结束任务时不生成整批完成回执。

weixin_task_delivery 使用独立 wx_task_deliveries（批次唯一）并沿用既有 pending/sending/sent/unknown 协议，避免把任务结果伪装成命令事件。原有 outbound_dispatch.ensure_resolved 共用账号未决检查；sender.send_once 的可选 authorize 回调在门锁后、POST 前运行，旧提示/命令调用合同不变。结果 sender 必须传递回调；每次发送只可通过一次校验。发送期间不持有存储锁，调用已发出后的取消仍有不可撤回边界。跨进程并发先持久领取；未知效果不重发，不覆盖既有摘要或重新生成已发送回执。主库 schema 7 不变，日志 schema 1 增加表；没有自动挂接 receiver 或新增 UI。


### M6 结果回执的预算与接收循环接线

weixin_result_queue 只发现本账号有效批次，复用结果投影验证 readiness，不复制正文生成逻辑。ResultPending 明确区分未完成提交/执行与授权或完整性失败；前者跳过，后者停止。weixin_delivery_state 封装原有三类账本的账号未决阻断，供显式结果交付及统一出站调度共用，避免任务交付反向依赖调度模块。

outbound.drain 的可选 result_sender 默认 None；旧操作回执和确认提示优先，之后在剩余同一预算内调用任务结果交付。receiver 的 result_receipts 默认 False，显式启用必须同时提供正预算和专用 guarded sender。每个 run 持有 receiver 锁并只维护一个 remaining，结果不会拥有额外配额。poll 返回先入日志，发送失败不丢已返回响应；发送中不持有 storage.lock。候选生成器将 authorize 传给 send_once，实际网络门锁后再验取消/授权/结果。没有新模型调用入口、主库迁移或生产安装变更。

## M7 迁移、局部组织与撤销

| 模块 | 边界 |
|---|---|
| note_migration_execution / note_migration_store | 授权两端、固定意图/阶段 CAS、普通搬移与 query/publish 未决门禁 |
| note_transfer_links / note_transfer_intent / note_transfer_stage / note_transfer_execution | 历史正文最小路径修改、附件复制、入出链报告、私有候选与已拥有物理步骤恢复 |
| publication_transfer_intent / publication_transfer_stage / publication_transfer | 文章包复制、原正文隐藏保留、发布位置 CAS、稳定身份与原来源权限衔接 |
| note_transfer_mocs / moc_projection | 有界归属 CAS；独立复用既有管理区块计划/恢复，缺失和人工冲突保留 |
| publication_originals | 用来源版本的实际原 Vault 权限构建取原件链接 |
| publication_organization / publication_organization_store | 显式单篇 MOC 选择、前后贡献审计及既有默认关闭队列入队 |
| publication_origins / batch_moc_contributions / weixin_batch_revocation / weixin_revocation_guard / weixin_revocation_store | 新建物理证明、独立判断笔记保留/隐藏与本批新增系统链接撤销、共享贡献保护、提交门禁及冻结范围/阶段审计 |

迁移的文件操作、发布位置、身份和 MOC 不属于同一事务：先持久领取，期间两端检索/新增发布拒绝；物理状态不明则保留，明确 reconcile 只核对既有效果，resume 仅执行已经记录物理身份的剩余步骤。文件投影独立执行，冲突不抹去完成的迁库账本。发布原件留在原库，不改变 source_versions/assets、原权限或历史 run 的归属；原 run 不重建原路径。

组织入口只编排已授权的确定性本地操作，不读模型凭证或调用模型。贡献证明以 MOC 同事务审计为准，已有归属不获得“本批新建”资格。撤销共用 MOC 时冻结每篇的顺序 CAS；笔记是否可隐藏与系统链接是否本批独有分别核验；保留人工编辑或旧/共享笔记时，仍可撤销证明充分的独有链接。其他任务的明确选择即使没有新增 MOC 修订也保护该链接；links_claimed 显式对账不搬文件。人工/共享链接保留为例外。主库 schema 7 不迁移；旁路现有 schema 1 增加有界表，整个 state_root 含候选/备份应一并备份。CLI 只解析参数，HTTP 与前端合同未扩大。

## M8 文档来源、隔离提取、人工审核与发布

`document_bindings` 绑定本人 stored 收件、库/来源版本及明确资产/MIME；`document_tasks` 在存储锁下领取一次并持续查权限、取消及来源字节，退出后重查全部绑定。Pi 命名无参数工具只做固定调度，正文及 OCR 配置留在宿主/worker，沿用执行/费用账本，工具数量与执行成功分别验证。没有另起一条绕过 D02 的资料处理链路。

`document_contract` 保持 schema 1 native PDF；`material_contract`/`material_geometry` 定义 schema 2 静态 Office/聊天/OCR 的严格闭合单元、摘要、附件、源坐标及框，均不带审核权。`pdf_documents` 继续复用固定 fake-expert 原生子集；`office_documents` 只处理有界 UTF-8 OOXML ZIP/XML，不执行 Office/公式/宏或下载外链；`chat_documents` 只接受明确 normalized 合同，未知字段 null、覆盖未知或缺件不完整，不推断平台嵌套格式。

`ocr_documents` 仅在 Linux Docker 内执行固定 Poppler/Tesseract，逐物理页保留 native 摘要、原页图、OCR 字符偏移/像素框及原置信度；需要显式 OCR profile，native 不自动升级。`document_qualification` 在隔离合成源上重复三次建立 availability receipt；`document_ocr` 校验镜像/实际数据与可重复记录的哈希绑定。不是完整 fake-expert/Paddle 资格、独立评审或准确性证明。失败的宿主 Vision 试验与被拒绝的权限放宽没有接入当前运行包。

`document_capture` 负责私有固定文件、规范 JSON 和来源/结果验证；`document_worker` 负责固定镜像、network=none、只读根/非 root/caps=none、资源/挂载/时限及持续 guard。`document_image` 只复制明确依赖及固定代码；默认 native 镜像不安装，显式 OCR Dockerfile 只在镜像构建阶段装官方 APT 依赖，实际镜像与二进制/语言数据摘要冻结。无宿主 OCR 权限放宽、无模型资产下载或秘密挂载。

schema 9 在 v8 上加 document_profiles/reviews/publications，不重建旧表；旧 PDF 默认 native/pdf。`document_evidence` 复核本人 succeeded/extracted 任务及 query 范围后只读定位/检索候选；`document_routes` 只解析 Bearer 请求及传输。候选保持不可发布/未核验字段，来源/结果哈希和单元完整性由宿主验证。

`document_review` 管理 immutable 的管理员逐单元判断，不由工具/模型批准。`document_admin_store` 封装文档任务列表、执行记录和源 MIME 的只读 SQL 投影；`document_admin_routes` 处理本机 cookie/CSRF 权限、请求与响应转换，原件/图像要求源所有者当前 query/original。存储投影由路由在共同锁内调用，不能替代既有 `document_evidence`/`document_bindings` 的来源复核或 `document_publication` 的发布门禁。`web/documents.js` 独立显示字面原文、定位、图片、未知状态及显式核对。审核记录绑定 source/result SHA；全部明确接受且来源完整才解除发布门禁，知识独立核验仍为 false。

`document_publication` 在同一存储锁下重查 query/add，冻结 review、来源/结果、目标根身份与文件摘要；复用 publication_files 的排他暂存/核对/rename，不接管既有路径，不覆盖人工作品，不重建已完成后被删除的笔记。未知部分效果拒绝重放；rename 未完成记账的目录由 note_inventory 排除，完成后才进入普通检索。search 给原提取稿/人工修订各自的来源标记，原件取回仍独立按原 Vault 权限。正文、保留图片、provenance 分层；管理员审核不等于 Gold。

旧 M6 收件/取消/补交与未知原件保留不扩大为文档自动提交；当前操作入口为明确收件的 create-document 和本机审核。真实合并转发 adapter payload、私有语义审核、复杂扫描/二进制 Office 和生产多端仍按 README 矩阵保留实际边界。


## Vault 保存规则与整理（VL2–VL5 候选）

目录发现仍由 `vault_directories` 和 `vault_save_contract/policy/routes` 封装；schema29 保留策略与短期目录预览，schema30 在 `publication_layout_schema` 中保留草稿并增加显式 active 状态及发布位置。正式仍为28。

发布链路：既有来源/审核 → `publication_layout` 冻结逻辑文件到实际路径 → `publication_commit` 独占提交 → `publication_locations` 登记已提交入口/身份 → `note_inventory`、持久索引及 `search` 当前文件交付核验。`publication_links` 处理受支持的图片/笔记引用与可明确投影的 UTF-8 跨度，`publication_file_io` 封装有界、拒绝链接替换的文件读取。旧 bundle 发布器的根直接子项合同保持。通道须在审核侧库完成后才暴露新入口。

`note_placement` 负责同库预览，`note_placement_execution` 分别提交关联与物理归档并记录恢复意图；`vault_layout_migration` 只转换明确选择的账本旧包，真实文件备份进入 state/layout-migration-backups，原包保留；`publication_rebind` 处理外部移动/文件替换后的明确身份修复，不写正文、不合并副本。待核对操作阻止相关索引/查询交付，未完成文件不能进入知识回答。

Web 的 `vault_save_*` 管理规则，`publication_preview` 显示实际落点；`vault_placement_api/vault_placements/vault_rebind` 提供整理、转换和重绑交互。路由只解析请求/权限与转换，文件系统、账本和业务状态保持在对应 Python 模块。Electron 沿用本机 HTTP 与已有目录选择 IPC，无新增后台服务。接口、限额和恢复边界见[技术参考4.10](TECHNICAL_REFERENCE.md#vault-save-policy)。

## 干净分发边界（PKG已本机上线）

正式 `fdc109db` / 发行代码 `944fe9e` 的代码在[独立source](../dist/clean-distribution/20261010-111826/candidate-published/source/)，根工作区既有业务增量不参与构建。`desktop/portable_layout.py`仅生成相对路径启动入口；`locked_dependencies.py`验证并复制固定输入，`runtime_relocation.py`处理内嵌Python迁移，`release.py`组装并签名。Electron继续只管理窗口，后台生命周期独立。

`runtime_components.py`封装本地软件清单验证和显式安装，CLI仅解析输入并输出结果；`agent_connections/installation.py`负责受管运行环境选择及执行前复核，不读取个人配置。组件模式使用当前应用解释器，显式旧安装仍遵循原路径。消息与Agent复用公开Hermes源码但分开依赖、home与进程。

`runtime_capabilities.py`只读收集能力事实，现有management路由输出HTTP合同，web/backend.js展示，不在界面或路由里执行安装 / 账号登录。配置损坏仅令对应组件invalid。基础程序、可选软件、用户数据和加密备份四者分开；backup_preflight排除软件runtime及机器选择，复用既有恢复锁定 / 重绑 / 激活，不新增数据库层或自动更新服务。详细字段见[技术参考第10节](TECHNICAL_REFERENCE.md#portable-distribution)。
