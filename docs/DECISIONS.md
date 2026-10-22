## 2026-10-20 Atlas 独立完整主题

按用户确认，以统一主题选择入口加载 Atlas 完整界面，保留经典五主题；根 URL 不变，仅运行一套界面。Atlas 负责导航/布局/样式，领域控制器和 API 共用，不嵌入旧整页。首页运行看板合并重复健康信息，区分独立查询/保存与回执。桌面呈现镜像增加仅 Atlas 可用的浅深色字段，兼容既有缓存；哈希路由仅允许同一根文档，其他 origin/path/query 和子框架仍不可信。

# 架构决策

初版日期：2026-09-26。更新日期：2026-09-28。来自用户确认；D14/D15 为桌面与参考范围决策，本次新增已确认的 D16（M1 存储）。实现状态见 STATUS.md。

## D01 独立知识后台，Carme 第二阶段接入
- Decision：Hermes 是微信入口，知识后台管理来源/资产/任务/权限，Pi 执行，Obsidian 是可编辑知识正文；Carme 作为后续客户端。
- Reason：当前主要需求是手机保存、问答及文档制作，外部 Agent 共享同一知识接口。
- Alternatives rejected：旧规划以 Carme 为唯一业务正本；继续使用 OpenClaw/共享目录 Watcher 作为新主链路。
- Consequences：旧规划保留但不指导新中枢；不迁移旧任务，不复制旧运行数据库。

## D02 统一 Pi 执行，后台强制边界
- Decision：资料处理和文档生成统一专用 Pi + Skill/MCP；后台确定性收件、权限、预算和发布。
- Reason：用户希望统一维护 Skill/MCP，同时要求可靠保存。
- Alternatives rejected：所有采集脚本绕过 Pi 独立编排；让模型自行执行权限判断。
- Consequences：模型有额外调用成本；已有 Skill 直接写 Vault 的发布路径需适配，提取逻辑优先复用。

## D03 受限执行与模型配置
- Decision：Pi 仅任务目录和 API，无直接 Vault/控制数据库权限；网络允许。Hermes/Pi 分别配置，oMLX 独立可选；版本显式升级。
- Reason：保护人工数据，兼顾 Skill/MCP 维护和模型选择。
- Alternatives rejected：复用个人 Pi 全权限；仅提示词隔离；默认禁止全部网络。
- Consequences：须验证容器/其他受限环境兼容性，具体技术待 M0 决定；本地浏览器和 oMLX 需受控连接，不将宿主秘密挂载进去。

## D04 本地查询不等同全程离线
- Decision：云 Hermes 可调度，本地 Pi/oMLX 读取原始证据，生成答案可返回云端润色；失败不转云。
- Reason：用户明确选择此分工。
- Alternatives rejected：全程本地；本地失败自动云兜底。
- Consequences：答案中的报价/摘录可能进入云模型，不能宣称内容完全不出机；工具返回不能附带原始证据包。

## D05 多 Vault 与独立客户端权限
- Decision：用户可跨库查询；默认查询开关独立配置；每 Agent/后续 Carme 账号独立凭证。创建空库后须本机确认权限。
- Reason：分类清晰，便于未来调用方接入和撤权。
- Alternatives rejected：共享总凭证；自动赋予新库权限；仅目录名称隔离。
- Consequences：读、原件、新增、建库分别授权；衍生内容扩大可见范围须审批，已外传数据不能保证收回。

## D06 原文、分析和人工内容分层
- Decision：全文忠实保存，整理区独立；原件和版本独立保留；人工修改优先但不抹去来源差异。
- Reason：可追溯，避免模型摘要成为新事实。
- Alternatives rejected：用改写摘要替代全文；自动覆盖旧正文。
- Consequences：来源/证据/笔记/产物/索引分开记录，索引可重建；旧笔记来源未知仅旁路标记。

## D07 MOC 自动增量维护
- Decision：同库关联、可自动建 MOC，保留人工归属并可添加；全库整理手动触发。
- Reason：Agent 是主要检索使用者，人工不应维护全部目录。
- Alternatives rejected：只靠 MOC 检索；自动跨库链接；定期全库重写。
- Consequences：全文与证据索引独立于目录；未分类不等于不完整，仍可检索。

## D08 意图、会话与批次确认
- Decision：纯资料默认保存，提问资料仅会话保留；静默窗口初值 3 秒可调，多条必须确认，提示后范围冻结。
- Reason：逐条转发丢失作者且难撤回，先确认可降低误处理。
- Alternatives rejected：按时间断言消息来自转发；收到第一条即执行；确认范围随新消息变化。
- Consequences：正常连续打字也可能触发确认；需要来源未知标记、批次账本、取消和恢复。仅待确认批次每 24 小时进入每日一条提醒。

## D09 可恢复撤销和生命周期
- Decision：取消停止任务保留检查点；批次撤销只影响本批新增内容，人工修改例外；删笔记不删原件，不自动重建。
- Reason：避免误删已使用、共用或人工加工资料。
- Alternatives rejected：撤销直接全量删除；会话删除带走知识资产。
- Consequences：需记录变更归属、引用和期望哈希；费用、外部消息不在可撤销范围。

## D10 文档草稿与投递
- Decision：可配置执行器默认 Pi/API，输出随能力；直接交付未核验草稿，输入版本固定。发送失败不自动重试。
- Reason：用户自行审阅，希望简化交付。
- Alternatives rejected：独立复核 Agent 必经；自动归档全部生成物；投递失败重生成。
- Consequences：格式存在检查不能充当内容验收；后续修订新增版本，用户明确保存才入库。

## D11 媒体与既有 Skill 适配
- Decision：直接提交音视频长期保留；链接下载媒体发布成功后可清理；复用已认可术语表，无新增歧义免逐次确认。
- Reason：兼顾原件保存与磁盘占用、自动处理体验。
- Alternatives rejected：所有视频永久保存；所有媒体成功即删；每个任务强制重新确认同一术语表。
- Consequences：覆盖旧视频 Skill 默认行为时需显式适配和回归；失败保留媒体，不能以旧 Skill 成功推断受限环境可用。

## D12 单机与本地管理
- Decision：本机登录管理 GUI 以 Web 前端 + Electron 提供，本机 API 和独立后台；测试库在项目目录，长期支持 iCloud Vault；运行状态本地，手动备份，备用机恢复切换。桌面交付与生命周期见 D14。
- Reason：先覆盖用户自用场景，减少远程与多主写入复杂度。
- Alternatives rejected：外网 API/管理页；同步运行中数据库；多主机并发写同一业务状态。
- Consequences：离线期间按平台实际保留能力补收；iCloud 只反映本机可见状态；备份须包括 Vault 外原件和元数据。M13 使用可选显式 `vault_roots` 扩展既有配置，运行状态保持本地；离线状态与 Vault 分别认证加密，恢复先核验原字节再审计重绑/停用，新副本须明确单主机激活。管理 UI 仅准备离线操作，秘密在终端隐藏输入；便携应用内后台独立启动。外部凭证/浏览器登录重配，未知费用/副作用不释放或重放；真实跨设备与签名资格须另验。

## D13 渐进验收与预算
- Decision：八类输入均为最终必需，逐项启用；问答优先，单任务/每日预算，有限重试、安全阶段恢复。
- Reason：尽快可用，同时保留完整需求和成本控制。
- Alternatives rejected：所有能力齐备才使用；不限额自动执行；将失败/未知副作用盲重试。
- Consequences：能力矩阵须明确未就绪/部分/已验证；一次只做一个小里程碑，失败修复后才推进。

## D14 Electron macOS GUI 是必需交付物（2026-09-27）

- Decision：交付可安装的 Electron macOS 应用，复用规划的 Web 管理前端，通过本机 API 连接独立后台。关闭管理窗口不停止收件/任务，重开连接同一后台；明确停止后台为独立操作。
- Reason：用户明确要求桌面 GUI，并确认 Electron 方案；复用 Web 界面避免另写原生管理 UI，保留已确定的本机登录和权限规则。
- Alternatives rejected：只交网页/浏览器快捷方式；Tauri 或全原生 GUI 作为第一版默认路线；把后台生命周期绑定到窗口；用 Electron 沙盒替代 Pi 隔离。
- Consequences：M0 检查桌面构建与后台运行前提，M2 的 Web UI 可复用，M12 验证 GUI/后台生命周期，M13 验证 `.app` 和安装包；UI 不直接访问数据库/Vault，依赖版本、签名/公证及真实 Mac 验证需记录。桌面框架的主进程/渲染进程分工参考 [Electron 官方说明](https://www.electronjs.org/docs/latest/tutorial/process-model)；[Tauri](https://v2.tauri.app/concept/architecture/) 保留为比较依据，未纳入本轮实现。

## D15 WeChatBridge 为参考，手机 Hermes 主入口不变（2026-09-27）

- Decision：M0 审查 [WeChatBridge](https://github.com/freestylefly/WeChatBridge) 的主动导出归档、文本/媒体映射、批次及原件保存实现；第一版不新增必须开发的系统 Share Extension，也不直接以该工具写入 Vault 绕过知识后台。2026-10-08 用户另行确认 Web/Electron 本地资料导入入口，独立于该参考项目，走统一来源/权限/任务与审核发布接口。
- Reason：上游文档描述 macOS 微信主动通过分享扩展交付聊天 ZIP，可提供实现参考；本项目要验证的是手机发给 Hermes Bot 后 iLink 实际返回的内容，两条路径不能互相证明。
- Alternatives rejected：用桌面分享流程替换手机主入口；把桌面可导出 TXT/媒体推断为 iLink 可读完整卡片；因参考项目存在就扩大为第一版必需分享功能。
- Consequences：核实上游具体 commit、许可、关键源文件和格式样例后才决定复用内容；目前只有公开说明审阅，没有本机安装或格式解析验收。手机缺失字段仍按既定补交规则处理；若未来新增分享入口，另行确认范围并走统一收件/授权/发布接口。

## 旧文档差异处理

已由用户后续选择明确解决：Carme 中枢改为后续客户端；OpenClaw 不参与新主链；独立受限 Pi 为统一执行方；生成报告允许未经内容验收作为草稿交付；视频媒体采用区别保留；术语复用预认可表；任务文件发送失败不自动重试。旧上传 Carme 测试失败不被删除，也不是新系统待修代码。

2026-09-27 用户确认将仅本机网页交付扩展为 Electron macOS GUI，并同意 WeChatBridge 先作参考；此项允许更新 SPEC §1/2/3/8/9/10 及 PLAN M0/M2/M6/M12/M13，不意味着已授权实施桌面分享功能或切换服务。

尚需 M0 核实的事实不是授权扩张：微信原始 payload 可得性、可靠收件点、飞书工具隔离、受限环境兼容、模型费用计量、Electron 构建与后台运行前提、WeChatBridge 固定版本和格式边界。若事实要求改变 SPEC 行为，列出矛盾请用户决定，不悄悄降级。

## D16 M1 本地存储具体实现（2026-09-28 用户确认）

- Decision：采用已确认的 M1_STORAGE_DESIGN.md：Python 3.12 + 标准库 sqlite3，七张表，独立不可变原件对象；开发状态目录可配置为项目下 `.local-state/`，仅两测试 Vault。事件幂等与来源内容复用分开；原件、版本和任务不随会话引用删除。
- Reason：满足持久收件、可恢复版本和引用保留，用少量直接模块实现，无额外服务依赖。
- Alternatives rejected：正文哈希代替事件身份；把原件挂在可删除会话目录；数据库与 Vault 混放；直接复用旧系统数据库；在 M1 引入 API/Agent/GUI。
- Consequences：文件和 SQLite 分阶段提交，必须对账恢复；receipt 可先存在而原件未齐，不能报已存；M1 只准备元数据快照和对象清单，不代替 M13 完整备份。M2 才承接对外权限与接口。

## D17 M2 本机 API 与权限合同（2026-09-28 用户确认开始施工）

- Decision：执行 M2_API_AUTH_DESIGN.md：FastAPI/Uvicorn 仅 127.0.0.1，SQLite v2 显式迁移，独立客户端 token/Vault 权限、单管理员 scrypt/会话/CSRF、同源静态管理页。新库限定测试根，先 pending 后本机批准。
- Reason：沿用 M1 数据流并在共享锁内完成鉴权和操作，提供后续受限执行器与 Electron 共用的接口。
- Alternatives rejected：开放 LAN、任意目录注册、默认全权、UI 直连数据库、复制存储实现、在 M2 打包 Electron。
- Consequences：v1 先备份再迁移；旧客户端不自动获得数据权限；原件为有上限缓冲交付，已交付数据不可收回。后续模型/微信/桌面打包仍分阶段验收。

## D18 前后端与功能模块分离（2026-09-28 用户明确要求）

- Decision：保留同源 HTTP 部署边界；前端拆为 API、会话、共用 DOM 与知识库/客户端/审批功能模块，app.js 只组合页面。后台静态托管单独路由，权限与 Vault 业务收回 auth/vaults 模块。
- Reason：后续独立修改 UI/UX 时无需修改存储与授权规则；功能输入输出明确，纯 API/会话模块可独立测试。
- Alternatives rejected：继续在单个脚本堆积全部交互；UI 访问数据库；为拆分引入无实际需要的框架、Manager 或服务层。
- Consequences：新增模块加入静态白名单和包验证；开发增加 Node 内置测试、ESLint/Prettier。部署仍只需 Python，API/schema/权限合同和 SPEC 不变。模块说明见 ARCHITECTURE.md。

## D19 M3 受限 Pi、执行账本与计费通道（2026-09-28 用户确认）

- Decision：执行 M3_EXECUTION_DESIGN 的 A/B：固定 Docker/Pi、单任务挂载、非 root 与只读根；Pi 使用容器内部 loopback 转 stdio 的专用通道，宿主按绑定身份校验工具和模型调用；schema 3 增加 execution_runs/model_calls；提供独立 CLI。
- Reason：保持本机 HTTP/LAN 边界，不给容器真实模型 key/Vault/控制库；预算在供应方请求前预留，重启和取消不抹去不确定费用。
- Alternatives rejected：宿主 0.0.0.0 服务、共享个人 Pi、将真实 key 注入容器、任意 URL/宿主命令代理、按进程退出码认定模型成功、未知价格按零计费。
- Consequences：固定 image digest 与 Pi 0.87.1；现有 M2 API/UI 合同不变。用户指定 OpenCode Go 仅用于本次构建验收，批准最多 3 次/每次 512 输出 token；实际使用 glm-5.3-flash 2 次。长期额度仍拒绝默认放行；真实文章/视频 Skill、MCP 与桌面管理后续验收。

## D20 M4 文章发布与 Skill 参考边界（2026-09-28 用户确认）

- Decision：每个来源版本独立目录，笔记和图片完整后一次发布；只新增，不覆盖人工笔记；发布账本负责中断恢复与重复提交。此发布机制已实现；真实验收范围见 STATUS。
- Reason：使笔记引用与图片生命周期一致，避免半成品进入 Vault；已有版本与人工修改保留。
- Skill 参考：用户指定的 wechat-to-obsidian 仅提供固定解析定义，不运行原发布入口、不修改原 Skill。来源与裁剪后哈希记于 vendor/article-source.json。第一方类型边界在 articles.py，正文解析不调用模型。
- Consequences：解析成功和图片完整只产生草稿，不代表授权发布；后台仍须重新校验当前 add 权限、来源绑定与落盘完整性。用户另批准 M4 合成调度最多 3 次 OpenCode Go 调用、每次最多 512 输出 token；文章正文不发送到云模型。公众号真实验收链接已由用户提供并完成采集/发布验收；本次使用合成 provider 驱动真实 Pi，未增加真实模型调用，详见 STATUS。


D20 实施补充：真实 X 多次采集显示整页 HTML 动态字段变化而正文/图片一致，因此原件版本与发布内容身份分别记录。每次提交保留自己的知识原件收件；同库同 URL 的正文/图片内容身份复用已有发布目录，包括回到已发布旧内容版本。发布准备放在目标 Vault 隐藏目录中，保证与最终目录同一文件系统；系统不支持原子 no-replace rename 时拒绝发布。准备中断的部分目录保留且不自动清理，已发布文件发生人工变化时拒绝重写。

## D21 schema 7 纯笔记任务兼容迁移（2026-09-28 用户批准）

- Decision：允许在持久快照之后、同一事务内兼容重建 execution_runs 和 answer_tasks；新增绑定类型，仅纯笔记任务允许来源版本与收件为空，旧任务仍保留强制来源绑定。
- Reason：纯笔记问答不应伪造收件或来源版本；这是 M5 对“迁移采取加法方式”的明确例外。
- Consequences：保留旧行、费用账本与外键引用；迁移失败整体回滚，外键只在私有迁移连接的事务外临时关闭，提交前检查。CLI/API 显式指定单一库，查询权限与证据快照在执行及交付时重查。旧收件 API 保持兼容，不新增真实模型调用额度。


## D22 持久笔记模型任务范围（2026-09-28 实施约定）

- Decision：为默认/跨库模型任务增加独立入口，沿用检索层授权规则，创建时固定完整库列表及范围选择方式；后续每次执行/交付重查全部库授权，默认开关变化不修改旧任务。
- Reason：排队或重启后不能因新授权/默认开关变化隐式扩大模型上下文，也不能在部分撤权后悄悄缩小回答证据范围。
- Consequences：schema 7 的 request_json 与版本化 snapshot_json 保存范围；execution_runs.vault_id 仅为新入口的首库外键锚点。空范围拒绝创建，旧单库/收件 API 不变。新快照与旧 worker 的列表格式不兼容，旧代码拒绝执行新任务。跨库输入仍为严格引用，不引入自由总结或新的模型用量授权。


## D23 可选自然语言草稿（2026-09-28 用户明确批准）

- Decision：三个持久任务提交入口与 CLI 可显式选择 cited_draft，默认仍为 extractive。概括、引用和全部原文分开返回，宿主始终标注“模型草稿，语义未核验”。
- Reason：引用编号与逐字原文可以机械校验，但不能据此证明概括被原文支持；不将结构校验当成语义验证。
- Consequences：模式进入持久请求、幂等比较和版本化快照；旧 worker 拒绝新模式任务，模式变化须新建请求。草稿最多一次模型调用，沿用授权/证据/取消/费用门禁；模型无权覆盖宿主标记。主库 schema 7 不变，无模型额度新增、无自动发布或知识归档。POST /v1/answers 仍为无模型原文接口。


## D24 M6 隔离施工与待确认收件（2026-09-28）

- Decision：按用户批准，M5 核心接口作为 M6 依赖，草稿弃答格式/来源措辞缺陷继续保留。先实现独立的 weixin-inbox.sqlite3（日志自身 schema 1），主库保持 schema 7；本人/账号/客户端/库绑定由可信本地设置固定，每次接收重新验 add 权限。
- Reason：未确认平台事件、缺件和未知类型不能提前成为 M5 可检索会话资料或知识版本。日志将原始事件 JSON、已取得的附件字节和游标放在同一个 SQLite 事务中，避免先确认游标后丢失数据；不以消息处理之后的 hook 冒充可靠收件。
- Consequences：事件内容及 context_token 只进入本地受保护日志，不进入普通日志；对外仅返回状态与 job ID。确认、附件补齐/恢复、日志保留清理和正式 Store 转交在后续阶段实现，待确认状态不触发模型/发布。日志是备份范围的一部分，单独备份 metadata.sqlite3 不包含它。当前没有部署该日志或改动现有 Hermes/飞书实例；实际接线、单 poller、真实身份及手机收发仍须单独验收。

## D25 M7 迁库保留与本批贡献撤销（2026-10-02 用户确认）

- Decision：附件只复制、原库全部保留；原库人工 MOC 归属及审计保留，目标只加入明确指定的 MOC，否则待分类；正文笔记链接保持并列出待修复项。
- Reason：迁走一篇笔记不能推断共用附件、其他笔记或人工分类的处置权。发布文章的原件归档保持原 Vault 权限，迁库不会扩大原件授权。
- Consequences：笔记迁移与 MOC 文件投影分开提交/对账；未知文件效果不重放。批次撤销只处理可证的本批创建及系统关联，人工修改/共有/原有内容列例外，原件、附件、MOC 文件和账本保留。

## D26 M8 显式文档配置与管理员审核（2026-10-03 实施边界）

- Decision：沿用 Pi 无参数工具与固定来源绑定，OCR 只在显式 Docker profile 中运行；Office/normalized 聊天共享有界候选合同。只有本机管理员逐单元、绑定 source/result SHA 的判断才能解除发布门禁，发布保持 knowledge_verified=false。
- Reason：提取/OCR/模型调用不能代替原件、视觉与数字单位的实际核对；未知平台字段和不完整来源不能靠推测补全。
- Consequences：主 schema 9 加法迁移三张表；OCR receipt 是合成可运行记录，独立准确性/Gold 未验证。发布冻结源、审核、根身份与文件摘要；不覆盖既有内容，不自动复活人删笔记。实际合并转发须真实脱敏 payload，既有微信自动提交/绑定不扩大；旧 Vision 权限放宽已被自动审批拒绝，运行包改用原 Docker 隔离边界。

## D27 管理界面视觉升级（2026-10-06 用户确认）

- Decision：采用 Opus 意见收敛后的方案 A：浅雾绿底、白色浮起面板、清新绿色强调、柔和阴影和克制动效；保留顶部 Tab 的顺序、12 个入口及主要操作流程。当前页面名称替代重复的权限管理大标题；Tab 使用白色导航面板、浅绿圆角选中态，窄窗口可横向滚动并保持选中项可见。品牌栏更紧凑，操作区与内容层级统一。
- Reason：用户明确关注视觉精致度，现有 UI/动效已具备，不能以重复增加基础能力或功能扩展替代视觉升级。先用真实页面结构与合成数据制作三个可运行样板，验证后推广。
- Alternatives deferred：左侧导航、深色模式、密度切换、渐变/噪点纹理、批量操作、全局搜索及数据表格框架。本轮不采用明显弹跳、持续成功状态动画或统一水波纹。
- Consequences：保留原生 JavaScript/CSS、现有 API/会话/业务权限和后台生命周期；复用并调整已有 token，不并存两套系统、不引入大型组件库或通用 Manager。Electron 沿用原生标题栏、控制按钮及现有菜单，无须增加网页拖拽区。冻结 M13 发行包保留，部署/正式安装不属于此视觉施工授权。

以下为已确认方案的样板初稿参数，允许经实际渲染、对比度和密度检查做小幅校准：

| 视觉用途 | 基线 |
|---|---|
| 页面／主面板／次级区域／选中背景 | `#EEF3F0`／`#FFFFFF`／`#F7FAF8`／`#E8F3ED` |
| 主色／悬停／按下 | `#2D7A5F`／`#246850`／`#1D5540` |
| 主文字／辅助文字 | `#20352D`／`#60736A` |
| 普通边界／输入框边界 | `#DCE6DF`／`#BACCC0` |
| 成功文字／背景 | `#246B4D`／`#EAF5EE` |
| 等待文字／背景 | `#8A571C`／`#FFF4E5` |
| 错误文字／背景 | `#A13232`／`#FCEEEE` |
| 信息文字／背景 | `#28667A`／`#EAF4F8` |
| 控件阴影 | `0 1px 2px rgba(24,42,33,.06)` |
| 面板阴影 | `0 2px 6px rgba(24,42,33,.04), 0 8px 24px rgba(24,42,33,.04)` |
| 浮层阴影 | `0 8px 24px rgba(24,42,33,.10), 0 24px 48px rgba(24,42,33,.06)` |
| 字号／行高／字重 | 页标题 28/36/600；区域标题 18/26/600；正文 14/22/400；长文 15/24/400；辅助 12/18/400（尺寸 px） |
| 圆角 | 控件 10px；卡片 14px；主面板 18px；状态标签胶囊形 |
| 间距 | 4/8/12/16/20/24/32px；桌面内边距 20px、窄屏 16px、密集记录 12–16px |

- 组件：统一线性图标、按钮主次及对齐；表单标签/说明/错误靠近字段，填写不等于校验通过；记录按标题与状态、摘要、操作分层，长路径/标识可完整查看；技术详情可展开，空状态紧凑。成功反馈可短暂显示，错误/未知结果/待处理信息持续可见；状态同时有文字，不仅依赖颜色。
- 动效：hover 100ms、上移最多 1px；按下 120ms、缩放 0.98；颜色/边框/焦点 150ms；Tab 内容入场 240ms、上移 4px并淡入；列表首次入场 220ms、间隔 35ms，仅前四项；弹窗入场 300ms、退出 180ms；连接中 2s 脉动，完成即停止。入场曲线 `(0.2,0,0,1)`、退出 `(0.4,0,1,1)`、状态过渡 `(0.4,0,0.2,1)`；后台刷新不重播，操作不等待动画，减弱动画模式关闭位移与循环。
- 业务语义：准备、入队、执行、未知、完成保持区分；审核/发布门禁、权限撤销、预算和精确后台实例确认不变；备份密码继续在终端隐藏输入。UI 不读取数据库/Vault，不自动执行模型/发布/消息。视觉提炼不得隐藏必要证据或改变保存语义。


## D28 DESIGN.md 视觉延展与旧后端部署（2026-10-07 用户确认）

- Decision：将已确认首页预览的 Monad 风格推广到源码 12 页、登录和 Electron 离线页。采用暖纸色 `#f6f3f1`、墨色 `#242424`、细边框 `#cecac8`、无面板阴影、常规字重衬线标题、等宽功能文字及胶囊按钮；键盘焦点保留蓝色。标题中文使用本机宋体，功能文字使用 PingFang 回退，不增加外部字体请求。顶部 Tab 顺序、现有动效及主要操作保持。设计依据为用户提供的 `/Users/USER/Downloads/DESIGN.md` 和已确认 `previews/monad-home-20261007/`。本条覆盖 D27 的视觉色彩参数。
- Deployment scope：用户明确选择“先上线视觉更新，保留现有后端和数据结构”。正式本机服务保持 M9/schema 12 和 6 页，只替换旧包的 HTML/CSS；为兼容原静态资源白名单，将 token/records/style 合并到已有 style.css，后端及业务 JavaScript 逐字节保持。源码 schema 19 功能与冻结 M13 发行不随本次发布。
- Consequences：使用新版本目录、部署前一致性 SQLite 备份和旧版本保留；服务上线后核对 health、全部在线资源及数据库摘要/表数量。不新增模型执行、审核发布、权限逻辑、密码重置或开机服务。


## D29 全部 12 项正式部署与显式迁移（2026-10-07 用户追加确认）

- Decision：用户要求将剩余 6 项以新 UI 上线，并明确确认“同意备份后升级并上线全部 12 项”。因此扩大 D28 的发布范围：保留已确认 Monad UI，部署与其匹配的 schema 19 后端及全部 12 页。新后端启动前，先验证主库一致性副本 12→19 迁移，再停止旧服务、保存新鲜私有一致性快照并显式迁移正式主库。
- Preservation：原 29 张表的字段及全部行、管理员密码/会话/权限保持；新增 12 张表为空，完整性/外键通过。只升级本机管理服务，不启用模型、微信、自动 worker、开机服务或受管理后台自动恢复；普通 cli serve 继续独立于窗口。冻结 M13 发行和 Vault 原件保持。
- Rollback：旧版本和迁移前数据库/config/部署元数据保留。旧 schema 12 包不能读取 schema 19；回退须先停止服务、保全升级后数据，再成对恢复旧库与旧包，不能只切换目录指针。私有 SQLite 快照不替代完整 Vault 备份。

## D30 M13 部署状态兼容与备份校验边界（2026-10-07，按 handoff 授权实施）

- Decision：业务状态继续按原范围保全；现存部署记录、迁移备份、M4/M5/M6/M8 验收与 M8 运行归档，以及 `.DS_Store` 按明确路径纳入加密状态部分。它们作为历史原字节证据，不作为当前业务数据库、服务启动或主库回滚指令。后续部署/测试产物放到状态目录之外；本次不删除或移动现存数据，不允许任意顶层目录。
- Reason：只排除非业务资料会丢失历史原件和账本，宽泛开放状态目录会掩盖未知业务数据。沿用范围/大小上限、独立普通文件、活动锁、变化检查和恢复门禁，先兼容实际部署结构，再由操作者完成真实离线验收。
- Consequences：HTTP 列表及恢复准备只检查有界完成标记和元数据，`encrypted_complete` 是完成记录，始终带 `ciphertext_verified=false`；完整密文哈希在终端显式 verify，密码认证与原件/关联核验仍由恢复重新执行。构建标识绑定独立公开源码快照及 wheel/sdist 摘要；记录身份不等于实时完整性通过，缺身份的旧包明确未知。冻结 M13 不覆盖，实际部署更新及跨机/真实通道验收仍独立处理。

## D31 独立 Hermes 飞书知识入口（2026-10-07 用户明确授权）

- Decision：新增独立飞书自建应用与 Hermes gateway，承接手机微信“其他应用”分享的 ZIP/公众号链接。旧飞书 Bot 原权限和用途保持。新应用使用独立本机 home、凭证引用、会话/缓存及 Fiber 客户端和本人 app-scoped open_id；App ID 机器级独占锁仍由 Hermes 执行。
- Boundary：复用统一后台的持久收件日志/四操作/Store，内部兼容 item 索引显式标为 `fiber-feishu-source/v1`，保存 Feishu 原始来源数据并去除回调验证 token，使用 `feishu:` 来源及会话 scope，不伪装成 iLink 原始事件。ZIP 原包和实际成员保留，TXT 仅支持可验证 UTF-8；无法取得的原聊天身份/时间/消息数及完整性不补造。未知输入隔离，不直接发布。
- Startup：独立配置默认 disabled，下载/Store 单次总额度默认 0；模型/正式发布/出站 0。本人未绑定时仅运行显式校验码配对，收候选身份后停止，须本机核对本人发送后才注册知识收件绑定。普通 Agent 与出站入口拒绝，SDK 仅订阅消息事件。运行环境引用已有固定公开 Hermes 快照，记录关键源码摘要、候选 SDK 版本与独立安全扫描二进制，避免后台自动下载。
- Validation：本机合成与实际 SDK 回调不替代新飞书应用连接、手机转发或审核发布验收；该授权增加独立入口，不取消既有微信直连验收。UI 实操问题仍按 checkout 延后修复。


## D32 checkout 修复的来源、额度与 schema 20（2026-10-08，按 handoff 实施）

- Decision：复用既有 Store、execution、文档/文章候选和发布；新增固定来源绑定与文章人工审核，不创建平行业务框架。schema 20 为原生 text/archive 配置及两张主库绑定/审核表的明确兼容升级，正式迁移另受部署授权门禁。
- Data：原件、旧 manifest、历史费用和交付账本不重写。ZIP 编码/成员/引用以补充投影及新笔记 provenance 记录；未知/歧义拒绝猜测。日期取第一方 HTML 明确证据，缺失或冲突保持未知；旧文章重放仍遵守原发布文件合同，人工笔记优先。
- Safety：飞书四类额度按明确授权 UUID 持久消费，操作前预留，失败/UNKNOWN 不返还；重启或相同授权不能增额。专用 query worker/发送边界保留本人及库绑定、当前权限、取消、费用和交付前复核。未知交付的用户反馈是独立追加证据，不能改成协议 ack 或解除重发屏障。
- Recovery：代码回退可使用公共备份；schema 回退前必须保全升级后新增账本与消费记录，不能用旧快照恢复额度或自动重放。正式服务切换前备份主库、inbox、配置及适用数据，隔离副本通过不等于正式迁移或真实通道验收。


## D33 可选知识图谱候选边界（2026-10-08，用户授权 S1–S3 与本地中文模型）

- Decision：按 handoff 顺序完成关联建议、增量生命周期和检索/GUI；用户指定使用 BAAI/bge-small-zh-v1.5。SDK、模型文件及算法固定版本，由受限离线 Docker 执行，宿主只传显式授权的单 Vault 快照。引擎可通过版本化 JSON 契约替换，不引入动态插件加载或默认语义接管。
- State：派生图、向量缓存、CAS 审核和写回意图保存在独立私有 sidecar；候选主库 schema21 的持久授权代次防止撤权再授权复活旧任务。模块默认关闭，基础服务不依赖 SDK；恢复保留审核并隔离未完成任务，不自动重放。
- Evidence：当前语义/拓扑结果仅是有来源的找资料线索，不是事实概率或独立证实。相似度与主题群不冒充中文事实抽取；技术型号精确约束、来源版本及权限在交付前后复核。默认搜索和自动回答接口保持现有行为。
- Retrieval content（2026-10-08手动验收确认）：用户选择知识图谱/既有笔记检索不接入OCR，以控制处理成本及效率；使用已有文本/属性，图片保留供人工查阅。后续只优化有效正文取样与多主题归属；独立文档处理OCR属于原有明确任务流程，本轮没有停用或扩大该流程。
- Writeback：复用既有 MOC 元数据与受管块写入，必须预览后明确确认并绑定权限代次、来源、revision 和文件 SHA。记录本建议新增贡献，撤销保留人工及共享贡献；跨存储中断依靠绑定计划对账，未知归属/未认领计划拒绝盲重放。
- Release：本授权交付隔离候选，不扫描真实 Vault、不迁移正式 schema20、不切换现有应用或飞书入口。真实样本、性能规模和正式备份部署另行取得适用授权；合成模型验证不能替代真实效果。

## D34 GUI 导入与统一处理策略（2026-10-08 用户确认）

- Decision：本地 GUI 导入与独立飞书明确处理请求复用同一后台合同，记录真实入口；普通提取和完整 fake-expert 保留明确选择，不自动降级。归档/确定性解析不要求无关模型配置；需要语义模型的步骤单独判断能力和预算。
- Policy：策略按版本/摘要 CAS 保存，任务固定完整策略、有效限额、引擎/profile 与源摘要；新设置只影响新任务。旧三字段配置只读映射既有默认，显式保存才升级。P1 使用现有 source_task_bindings.request_json 的版本化封装，不新增主库表；旧任务从既有固定 worker 字段读取默认合同，不回填或重放。
- Capacity：旧默认 PDF/源 8 MiB、100 页、单 MD 1 MiB 保持。总 MD、文本、结果 JSON、图像和批次额度独立有界；字节上限仍维持既有容量；P3.1 持久索引与 P3.2 显式结构分片已分别验收。更大源/PDF 分批、增量调度、图片/DOC 和完整 Skill 按余下阶段独立验收，不截断或偷偷钳制配置。
- Release：按 HANDOFF 的 P1→P6 依赖实施隔离候选，正式 schema20 不能直接运行更高 schema 的候选源码；本授权不自动切换服务、恢复历史费用/发送额度或宣称真实资料/完整 Skill/生产性能通过。


## D35：确定性导入与来源模型任务分开（2026-10-08）

- Decision：P2 使用主库 schema22 的授权上传会话/分块账本；归档复用 Store/不可变对象，窗口仅通过 HTTP。`binding_kind=document` 的有限 Worker 执行模型额度严格为0，不能进入 Pi 模型启动/消费路径；旧 source/notes 保留正预算和原配置语义。
- Binding：请求、来源身份/Vault、权限代次、原件摘要、完整处理策略及明确附件映射冻结；新提交与网络重放分别记录。MD 正文保留结构，管理信息旁路；附件复制到受控摘要路径，缺映射阻止发布。恢复和异常进程不自动重放，正式部署/更大容量继续独立验收。

## D36：持久分块索引只作召回缓存（2026-10-08）

- Decision：P3.1显式在Vault内授权构建独立SQLite FTS5 sidecar；聚合正文逐篇消费，不把全库正文留在内存。未建索引保留旧快照，有索引则当前查询使用缓存召回；不在查询中偷偷全库重建。中文短词沿用字面规则，在磁盘分块匹配并复核。
- Authority：缓存不授予来源、身份或原件权限；交付重读命中正文与当前注册/发布绑定。元数据或可见性变化使代次stale，明确拒绝旧证据，显式重建；恢复保留派生记录但失效，不重放处理或模型。单篇容量/大源文件与多MD仍按P3剩余阶段贯通，不能因缓存能检索大库而声称大书已完整导入。

## D37：分片身份与内容完整性绑定发布账本（2026-10-08）

- Decision：旧 `pause` 策略及冻结任务不变；管理员显式保存 `split` 才让新任务按完整结构发布。目录和正文清单作为既有发布账本中的摘要文件，不引入第二套发布数据库。分片身份由原任务与有序文件名确定，人工登记沿用该身份，避免重建索引生成新身份。
- Evidence：清单绑定原件版本/审核与渲染正文摘要、每片区间及来源单元；正文重组必须逐字节等于拆分前稿，目录单独纳入总预算且标为导航管理信息。缺片、清单篡改或身份冲突拒绝交付完整证据，修订区分于审核稿，历史发布重放不覆盖或重建用户文件。
- Recovery：当前 Vault 授权仍由恢复后的主库重绑检查；原发布时的物理根记录保留为历史写入证据。重新启用测试身份后显式重建索引，不复活旧权限、模型调用或发布。更大容量与 PDF 分批未因此放行。

## D38：P3–P5 有界原件、派生运行与完整 Skill 候选（2026-10-08）

- Capacity：native PDF有界文件流与25页批次独立核验，新任务可明确冻结16MiB/300页/总MD4MiB；旧默认/任务不回填。其他解析保留8MiB源容量，图片总像素/帧/边长与输出分别限制，超限失败保留原件，不缩放或截断后宣称完整。
- Runtime：OCR解码、DOC转换和完整Skill采用各自固定镜像/依赖摘要及资格凭据；资格仅证明可用性，不证明准确率。DOC禁宏/加密/外链主动执行，全流程网络关闭，原件和derived DOCX的SHA映射保留。完整Skill保留全部上游模块与协议，普通页面提取不冒称完整编译。
- Draft：完整Skill以显式profile/backend和workpack编译Draft；缺语义材料/扫描条件明确暂停，无自动模型补齐或Gold。恢复复制旧作业；换入新workpack走新plan，旧hash绑定不改。完整canonical包保留，host可读笔记另标候选分析，检索不冒充原文；人工确认候选导入不改变独立语义review_state=deferred。
- Channels：GUI与飞书精确任务UUID共享绑定；飞书须明确expert开关/独立有限额度和授权UUID，纯资料不变操作、关联不明要求选择，未知派发不自动重放。恢复保留原件/审核/尝试/消费，撤销旧身份并失效索引，不自动启动。

## D39：左侧导航与统一表单呈现（2026-10-08 用户确认）

- Decision：按 [.docs/handoff.md](../.docs/handoff.md#gui-vault-channel-graph-20261008) 的 U1 范围采用浅色黑白交互、左侧可折叠导航、用户 Logo、DM Sans/Noto Sans SC，并移除 footer；替代旧横向导航与衬线/等宽普通 UI 字体约束，未确认的全站深色方案不进入本次施工。
- Boundary：侧栏只持有布局偏好；自定义单选下拉为原生 select 的可访问投影，原元素继续承载值、校验和草稿。打开、移动高亮、取消和程序赋值不发变更事件，确认不同值才发 input/change。其他原生输入保留语义并统一样式。
- Delivery：字体与来源/许可证、Logo 原图随包离线交付，Electron 离线页复制同一资源，网络权限仍限本机与明确本地资源。真实 Web/Electron 和打包资源分别验证；源码候选不等同正式部署。

## D40：目录登记、消息入口与跨批次图谱边界（2026-10-08）

- 多仓库扩展采用主库schema26附加目录/范围/意图字段与表，保留旧schema25 ID和权限语义。稳定ID与物理目录绑定，祖先策略及固定L2排除在遍历前执行；耗时复制/登记/索引为独立可取消任务。废纸篓使用持久意图与固定哈希Swift Foundation helper，文件系统和SQLite不冒充原子操作；未知结果保留核对且不重试。
- 通道配置/收发账本独立sidecar，凭证及每配置Hermes home私有且不备份。复用固定适配器与已有证据/模型任务，不启用Hermes Agent。微信适配器的固定 connect 仅在独立子进程中替换原添加前检，保留bot-token独占锁与实际二维码登录；本人确认、单库权限和预算仍由宿主执行。查询/模型/发送配额按配置版本持久消费，暂停、源变化、撤权与UNKNOWN不重放。
- 前端保持现有ES modules；固定Sigma3.0.2、Graphology0.26.0、ForceAtlas2 0.10.1，以esbuild0.25.12生成本地画布及静态Worker并保留MIT许可。图投影汇总32批当前有效分析，1000节点/5000边/32MiB源边界公开；当前来源核对后才连接语义边，显式链接与审核状态独立。Electron只暴露目录选择、稳定笔记身份打开、经后台验证的Trash意图，不提供任意文件/命令接口。

## D41：受管 Agent 在构造前检索，能力验证独立于安装检查（2026-10-09）

- 本机 Hermes 0.19.1 的插件与调用层会吞掉 `pre_llm_call` 异常，故由 Fiber 受管入口在导入/构造 `AIAgent` 前完成检索与来源复核，再调用公开的 `run_conversation`。不修改上游源码；配置、身份或授权代次改变即丢弃旧轮次，回答引用绑定宿主提供的来源，模型补充独立且保持未核验草稿。
- A0 已验证实际 Hermes 入口与合成本机 provider 的顺序、零调用失败阻断和不重试。静态版本/摘要仅表示候选兼容；独立 profile 下的合成通过不等同正式模型、MCP 或 GUI 交付。Hermes 初始化还会探测本地元数据；A4 需处理实际网络范围、辅助进程、硬截止及持久模型预算，同步回调检查不能代替进程取消。
- A1 采用主库新增连接/操作两张表并引用既有客户端权限；A2 的来源复核通过受控 HTTP 边界复用后台校验，不让适配器直接读 Vault、不消耗额外检索配额。GUI 按访问、配置、探测、问答分别展示状态，恢复默认为暂停和验证过期；具体施工与证据定位见 HANDOFF/STATUS。


## D42：Agent schema27、受管进程与共享模型预算（2026-10-09）

- 两张主库STRICT表引用既有clients/grants；每连接仅query，默认暂停。数据库与私有文件先落意图后核对，崩溃/恢复需修复，不重放。秘密不进入导出/备份，恢复需轮换、重新应用与显式授权。
- 固定MCP1.26.0；受管Hermes0.19.1及其OpenAI2.24.0/httpx0.28.1通过公共源码/依赖摘要绑定。独立profile与模型配置引用，不修改上游或既有gateway。HTTP/MCP负责检索；来源复核不额外检索。真实运行事件轨迹由宿主记录，不采信模型自述。
- GUI和Bearer入口共用有限执行：持久入场计数，每连接每日10次，与内置本地查询共享配置的每日上限；失败/UNKNOWN不返还，无备用/自动重试。后台只停止自己持有的Popen，不按名字/遗留PID杀进程；SIGTERM清理MCP/Hermes子进程。进行中的Agent操作阻止完整备份。
- 用户要求由本地 Qwen3.8 改用 OpenCodeGo 验证后，新增显式`opencodego-primary`引用；复用已配置主模型的凭据/价格/限额，固定 Go HTTPS 端点，不修改 M10 云整理输入合同。云端 Agent 将问题与选定证据作为本轮上下文发送，GUI须明确告知且不默认选云。预约存入现有操作 JSON，与既有`model_calls`双向共享费用和次数；失败/未知按预约保留、成功按报告用量结算，本地配额独立，无新表迁移。
- 云端元数据探测在本机响应，推理请求加入自身 User-Agent、稳定 session 和 JSON 模式；实际 Hermes 仍仅一次调用，宿主保留严格 JSON/来源校验。真实模型可能输出措辞偏差，协议与独有事实通过不代替人工语义审核；分层验证和当前部署状态集中于 STATUS。
- 版本、授权、来源改变使旧投影/答案失效。实际Hermes在合成provider下通过可验证的顺序和取消等测试；真实oMLX质量与Electron需独立验收，不将候选构建或合成结果提升为总交付通过。
# UV contracts (2026-10-12)

- Develop from sanitized schema30 commit `4d1b88a` in an independent copy; preserve the root's parallel changes, release bytes and live data. Baseline directory/publication/MOC tests: 40 PASS before modification.
- Save policy v2 is explicit; v1 serialization excludes every new default. Automatic MOC execution is distinct from the historical preview-only flag. Attachment directories are namespaces excluded from note indexing; object storage remains the immutable origin.
- System review credentials bind task, source/result/coverage hashes, actor, policy revision and decision. Trusted actor is generated internally; old human reviews and rejections are preserved. Rule review never claims semantic or visual human verification.
- Expert intent binds verified owner/chat/client/Vault, event/reply, one-time expiry and downloaded asset SHA. Ordinary and expert engines are immutable requests, not mutable labels on running jobs.
- Merge preserves stable identity and immutable origin, freezes permissions and directory versions, stages content behind a visibility barrier, and records each filesystem/DB boundary. Target-original access requires target authorization; existing source permissions are never silently granted. Uncertain effects require reconciliation. No budget reset, deletion of source data or history replay.
- Schema additions use the next free version in this isolated baseline and explicit migration only; read endpoints never upgrade storage. Version allocation is rechecked before any later integration/deployment.


## 2026-10-12：GUI观察与业务操作分离

采用现有ES modules/CSS/SVG和FastAPI；从已上线UV冻结源码增量施工。首页只读协调与状态推导分离，保留原业务页、会话、权限和原生桌面边界。索引摘要读取缓存而不做磁盘核对；能力声明只证明合同。B3趋势不进入首版，主库schema31保持。
