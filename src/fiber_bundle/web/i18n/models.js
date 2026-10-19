// UI message catalog; parameters preserve source data.
export default {
  "model_budget_settings.72ecd1bff6": ["统一模型预算", "Shared model budget"],
  "model_budget_settings.bfa84a64a7": [
    "请先在模型与查询中配置 API Key；基础提取无需 Key 或预算。",
    "Configure an API key in Models & Query first. Basic extraction needs no key or budget.",
  ],
  "model_budget_settings.a98585871c": ["显示名称", "Display name"],
  "model_budget_settings.a4541a70e8": ["预算单位", "Budget unit"],
  "model_budget_settings.089a228eca": [
    "周期上限（0 暂停付费步骤）",
    "Period limit (0 pauses paid steps)",
  ],
  "model_budget_settings.e9bf2c2fea": ["周期", "Period"],
  "model_budget_settings.fd7b67c923": ["每日", "Daily"],
  "model_budget_settings.68b21af949": ["每月", "Monthly"],
  "model_budget_settings.b4817a5cad": ["保存模型预算", "Save model budget"],
  "model_budget_settings.bf5aead102": [
    "预算由使用此 Key 的 Fiber 模型任务共享；基础提取无额度要求。外部 Agent 自己调用模型的消费由其宿主管理，服务商余额未知。",
    "Fiber model tasks using this key share the budget. Basic extraction needs no quota. External Agent model spending is managed by its host; provider balance is unknown.",
  ],
  "model_budget_settings.cec0a3987c": [
    "本周期已用 {p0} {p1}（含未核对预留）",
    "Used this period: {p0} {p1} (including unchecked reservations)",
  ],
  "model_budget_settings.a004e1524a": [
    "尚未设置统一预算，当前仍使用已有模型配置限额。",
    "No shared budget set. Existing model configuration limits still apply.",
  ],
  "model_budget_settings.ce02290d2b": [
    "当前预算有未保存的更改，切换 Key 将放弃这些更改。确定切换？",
    "Budget has unsaved changes. Switching keys discards them. Continue?",
  ],
  "model_budget_settings.1e531a0e35": [
    "模型预算已保存；历史消费和未知预留保留。",
    "Model budget saved. Historical spending and unknown reservations retained.",
  ],
  "model_wizard.d1492757f2": ["服务商默认", "Provider default"],
  "model_wizard.aa9e366f68": ["低", "Low"],
  "model_wizard.a567bdaa11": ["中", "Medium"],
  "model_wizard.b1c27820fe": ["高", "High"],
  "model_wizard.a362aecad5": ["更高", "Higher"],
  "model_wizard.8672864e90": ["最高", "Highest"],
  "model_wizard.168e845a86": ["添加模型", "Add model"],
  "model_wizard.e53e02f62c": [
    "选择服务商，再读取此 Key 的模型列表。",
    "Select a provider, then load models for this key.",
  ],
  "model_wizard.a573affa93": ["服务商", "Provider"],
  "model_wizard.dd2cf36c8a": ["使用已有 API Key", "Use existing API key"],
  "model_wizard.20e436a63a": ["Key 自定义名称", "Custom key name"],
  "model_wizard.83a42e8520": ["API 基础地址", "API base URL"],
  "model_wizard.44455611b9": ["高级设置", "Advanced settings"],
  "model_wizard.03be803bbe": ["兼容协议", "Compatible protocol"],
  "model_wizard.e03edb5050": ["OpenAI Chat 兼容", "OpenAI Chat compatible"],
  "model_wizard.16b2870ce4": [
    "OpenAI Responses 兼容",
    "OpenAI Responses compatible",
  ],
  "model_wizard.aed3f30f68": [
    "Anthropic Messages 兼容",
    "Anthropic Messages compatible",
  ],
  "model_wizard.e1a6e80b9a": [
    "每日 Tokens 预算（同一 Key 的模型共享）",
    "Daily token budget (shared by models using this key)",
  ],
  "model_wizard.97bcfd4365": [
    "已有 Key 沿用现有预算。可在统一模型预算中修改；价格未核实时费用显示未知。",
    "Existing keys keep their current budgets. Edit them under Shared model budget. Costs remain unknown until rates are verified.",
  ],
  "model_wizard.1afed6a81a": ["选择模型", "Select model"],
  "model_wizard.000f03156e": ["可用模型", "Available models"],
  "model_wizard.e09dbab53c": [
    "手动模型 ID（列表接口不可用时）",
    "Manual model ID (when list API unavailable)",
  ],
  "model_wizard.c0948afa16": [
    "准确的模型 ID；不会自动标记调用已验证",
    "Exact model ID; does not mark calls as verified",
  ],
  "model_wizard.9cc170e9fe": ["思考强度 / effort", "Thinking effort"],
  "model_wizard.6e18b7346f": ["读取可用模型", "Load available models"],
  "model_wizard.f5c2506e70": ["保存模型", "Save model"],
  "model_wizard.438144d9aa": [
    "连接信息已变化，请重新读取模型。",
    "Connection details changed. Load models again.",
  ],
  "model_wizard.b84097c575": [
    "留空沿用；填写新 Key 则轮换，不清零预算",
    "Leave blank to keep; enter a new key to rotate without resetting the budget",
  ],
  "model_wizard.34527f790d": ["仅输入 API Key", "API key only"],
  "model_wizard.829d8aa432": [
    "放弃当前未保存的模型配置？",
    "Discard unsaved model configuration?",
  ],
  "model_wizard.c0b82598d9": [
    "正在读取模型；不会发送知识库资料或调用模型。",
    "Loading models. No vault content is sent and no model is called.",
  ],
  "model_wizard.621e846d2b": ["请选择模型", "Select a model"],
  "model_wizard.dc67e0944f": [
    "已读取 {p0} 个模型；调用权限和服务商余额尚未验证。",
    "Loaded {p0} models. Call permissions and provider balance not verified.",
  ],
  "model_wizard.3887f8596c": [
    "服务返回空列表，可手动填写模型 ID；调用尚未验证。",
    "Provider returned an empty list. Enter a model ID manually; calls not verified.",
  ],
  "model_wizard.d66cbeb643": [
    "可修正连接后重新读取；服务未提供列表时可手动填写模型 ID。",
    "Correct the connection and reload, or enter a model ID if listing is unsupported.",
  ],
  "model_wizard.2ac815332e": [
    "请选择模型或填写准确的模型 ID。",
    "Select a model or enter its exact ID.",
  ],
  "model_wizard.c346d0c662": ["自定义服务商", "Custom provider"],
  "model_wizard.2fbee19163": ["本地服务", "Local service"],
  "model_wizard.47f009cef9": ["新增 API Key", "Add API key"],
  "model_wizard.c7ea2c9bc4": ["编辑模型", "Edit model"],
  "model_wizard.84e0f4adc2": [
    "读取模型只查询元数据；保存后可单独测试调用。",
    "Loading models queries metadata only. Test calls separately after saving.",
  ],
  "models.6e741f63d4": ["已入队", "Queued"],
  "models.5026a63b58": ["执行中", "Executing"],
  "models.c0b3fbff51": ["完成", "Done"],
  "models.68c8e28a97": ["预算不足", "Insufficient budget"],
  "models.e30eee5c7c": ["暂无模型查询任务。", "No model query tasks."],
  "models.ef11f09c93": ["本地查询", "Local query"],
  "models.1d9fdc821d": ["云端整理", "Cloud organization"],
  "models.8bea63f61f": ["有限执行：{p0}{p1}", "Bounded execution: {p0}{p1}"],
  "models.30086cc7fc": [
    "批准此任务的一次模型调用",
    "Approve one model call for this task",
  ],
  "models.dc2bc8e8bf": ["启动一次", "Start once"],
  "models.63231e6ae7": [
    "请先批准此次模型调用。",
    "Approve this model call first.",
  ],
  "models.75aa011fb8": ["取消待执行任务", "Cancel pending task"],
  "models.50301ab0cb": ["核对中断状态", "Check interrupted state"],
  "models.b8f7e75584": ["查看答案与引文", "View answer and citations"],
  "models.aa61514a48": ["云端未核验草稿", "Unverified cloud draft"],
  "models.96aa84ea01": ["准备云端整理", "Prepare cloud organization"],
  "models.b7588be24c": [
    "批准配置中的备用模型",
    "Approve a configured fallback model",
  ],
  "models.b4125790aa": [
    "读取此任务的备用列表",
    "Load this task's fallback list",
  ],
  "models.e63fbe8234": [
    "{p0} · 任务上限 {p1} nano USD",
    "{p0} · Task limit {p1} nano USD",
  ],
  "models.ef8cce6f9d": [
    "此任务配置没有声明备用模型。",
    "No fallback model declared for this task.",
  ],
  "models.77b7a206ff": [
    "明确批准声明中的此备用模型",
    "Explicitly approve this declared fallback",
  ],
  "models.ab6420e80b": ["批准备用选择", "Approve fallback selection"],
  "models.0eb9cec0fc": [
    "请读取备用列表并明确批准此次选择。",
    "Load the fallback list and explicitly approve this selection.",
  ],
  "models.2c73810c80": ["查看调用费用与审计", "View costs and audit"],
  "models.82986b7ac1": [
    "费用状态：{p0}；预留 {p1} nano USD；结算 {p2} nano USD",
    "Cost status: {p0}; reserved {p1} nano USD; settled {p2} nano USD",
  ],
  "models.ae28135879": [
    "本地调用：{p0}；输入 {p1} / 输出 {p2} token",
    "Local call: {p0}; input {p1} / output {p2} tokens",
  ],
  "models.7cc746ed86": [
    "备用选择 {p0}：{p1} {p2}",
    "Fallback selection {p0}: {p1} {p2}",
  ],
  "models.5992181226": ["尚无模型调用记录。", "No model calls recorded."],
  "models.abb20ef623": [
    "请先保存模型配置。",
    "Save model configuration first.",
  ],
  "models.d589af3e71": [
    "查询已入队，请在任务卡片明确启动。",
    "Query queued. Explicitly start it from its task card.",
  ],
  "models_catalog.1c8218d8e4": ["刷新模型列表", "Refresh models"],
  "models_catalog.d875f27b77": [
    "模型已保存；保存不触发调用。",
    "Model saved. Saving does not call it.",
  ],
  "models_catalog.c906fb6362": [
    "模型已保存；调用是否可用以对应验证结果为准。",
    "Model saved. Check its verification result for call availability.",
  ],
  "models_catalog.095a8559b5": [
    "尚未添加模型。可直接输入 API Key，无需配置文件或镜像摘要。",
    "No models added. Enter an API key directly; no config file or image digest required.",
  ],
  "models_catalog.f8b3ab2297": ["应用默认", "App default"],
  "models_catalog.bf4ad76184": ["本地", "Local"],
  "models_catalog.f38a484277": [
    "调用验证通过（当前配置）",
    "Call verified (current configuration)",
  ],
  "models_catalog.79e4760d52": [
    "已保存 · 调用未验证",
    "Saved · Calls not verified",
  ],
  "models_catalog.a8c3698b5b": ["已停用", "Disabled"],
  "models_catalog.c34a1591b9": ["关联：{p0}", "References: {p0}"],
  "models_catalog.815da624f7": [
    "尚无默认项或固定 Agent 引用",
    "No default or pinned Agent references",
  ],
  "models_catalog.a67bd77944": [
    "此配置用于音频转写或其他专用接口；请在实际任务中核对，文本测试不适用。",
    "This configuration uses audio transcription or another specialized API. Check it in an actual task; text tests do not apply.",
  ],
  "models_catalog.357211096f": ["设为默认", "Set as default"],
  "models_catalog.23b3924dd4": [
    "默认模型已更新；已有任务保留原配置。",
    "Default model updated. Existing tasks keep their original configuration.",
  ],
  "models_catalog.0518365699": ["编辑", "Edit"],
  "models_catalog.a6731ea242": [
    "测试调用（消耗 Tokens）",
    "Test call (uses tokens)",
  ],
  "models_catalog.9e014e97e9": [
    "测试已提交；不发送知识库内容。",
    "Test submitted. No vault content sent.",
  ],
  "models_catalog.4e6fd0e28c": ["停用", "Disable"],
  "models_catalog.f4f0ead111": ["启用", "Enable"],
  "models_catalog.2f9daa8289": ["删除", "Delete"],
  "models_catalog.89d2befae3": [
    "请先替换这些引用：{p0}。Agent 的绑定在 Agent 与客户端中修改。",
    "Replace these references first: {p0}. Change Agent bindings in Agents & Clients.",
  ],
  "models_catalog.8684574c90": [
    "请先替换这些引用：{p0}",
    "Replace these references first: {p0}",
  ],
  "models_catalog.485081a277": [
    "请先替换这些引用：",
    "Replace these references first:",
  ],
  "models_catalog.753d4028fa": [
    "。Agent 的绑定在 Agent 与客户端中修改。",
    ". Change Agent bindings in Agents & Clients.",
  ],
  "models_catalog.77dca205a4": [
    "删除模型 {p0}？历史调用与费用会保留。",
    "Delete model {p0}? Historical calls and costs retained.",
  ],
  "models_catalog.1428b901c5": [
    "模型测试调用通过；Tokens 已记账，价格未核实时费用未知。",
    "Model test passed. Tokens recorded; cost unknown until rates are verified.",
  ],
  "models_catalog.43d46aee66": [
    "测试未完成（{p0}）；请核对该模型设置。",
    "Test incomplete ({p0}). Check model settings.",
  ],
  "models_catalog.737ae7cd56": [
    "正在核验受限运行环境…",
    "Checking restricted runtime…",
  ],
  "models_catalog.3d269fd1af": ["受限运行环境", "Restricted runtime"],
  "models_catalog.a87f6dc59b": [
    "Pi {p0} · {p1} · 已就绪",
    "Pi {p0} · {p1} · Ready",
  ],
  "models_catalog.88ffa32411": ["环境未就绪", "Environment not ready"],
  "models_catalog.0d90ab2d28": ["环境诊断", "Environment diagnostics"],
  "models_catalog.9fd6d39cde": [
    "请先启动 Docker 后重新检查。",
    "Start Docker, then check again.",
  ],
  "models_catalog.c25fb86b1e": ["重新检查", "Check again"],
  "models_catalog.cf6c14150e": ["准备运行环境", "Prepare runtime"],
  "models_catalog.d9e125c74e": [
    "开始准备应用附带的已核实 Pi 环境；不会安装 Docker 或启动模型任务。",
    "Preparing the verified Pi environment bundled with the app. This does not install Docker or start model tasks.",
  ],
  "models_policy.262e1683fa": [
    "受限执行镜像摘要",
    "Restricted executor image digest",
  ],
  "models_policy.02739d1bae": ["服务地址", "Service URL"],
  "models_policy.8b84338de3": ["模型 ID", "Model ID"],
  "models_policy.05cc90ea7a": [
    "本机凭证文件绝对路径",
    "Absolute local credential file path",
  ],
  "models_policy.1224e556b6": ["输入 token 上限", "Input token limit"],
  "models_policy.c5fa526946": ["输出 token 上限", "Output token limit"],
  "models_policy.16479a8bf0": ["请求字节上限", "Request byte limit"],
  "models_policy.e7b8936857": ["每任务调用上限", "Calls per task"],
  "models_policy.e352caeb68": ["每日调用上限", "Calls per day"],
  "models_policy.f7431c6783": ["单次超时秒数", "Timeout per call (seconds)"],
  "models_policy.0671001ab0": ["价格版本", "Pricing version"],
  "models_policy.7e6a34620a": [
    "每输入 token 费用（nano USD）",
    "Input cost per token (nano USD)",
  ],
  "models_policy.631c3b924b": [
    "每输出 token 费用（nano USD）",
    "Output cost per token (nano USD)",
  ],
  "models_policy.c2fec50f55": [
    "每任务费用上限（nano USD）",
    "Cost limit per task (nano USD)",
  ],
  "models_policy.bdffc5d375": [
    "每日费用上限（nano USD）",
    "Cost limit per day (nano USD)",
  ],
  "models_policy.b40e06723d": ["最大尝试次数", "Maximum attempts"],
  "models_policy.50e0d1e831": ["账单时区", "Billing timezone"],
  "models_policy.04ee13cd1d": ["货币", "Currency"],
  "models_policy.f0cfd463ca": [
    "请导入已准备的路由配置文件。",
    "Import a prepared routing configuration file.",
  ],
  "models_policy.977d087e62": ["云端答案整理", "Cloud answer organization"],
  "models_policy.e2864acbe5": [
    "声明备用模型 {p0}",
    "Declared fallback model {p0}",
  ],
  "models_policy.2f6dbdaace": ["声明备用模型 ", "Declared fallback model"],
  "models_policy.4bfb5a06b5": [
    "配置文件超过 64 KiB。",
    "Configuration file exceeds 64 KiB.",
  ],
  "models_policy.7d762ed3f8": [
    "请选择关闭自动执行的路由配置文件。",
    "Choose a routing configuration with automatic execution disabled.",
  ],
  "models_policy.eafb190cc3": [
    "配置已载入表单，请核对后保存。",
    "Configuration loaded into the form. Check it before saving.",
  ],
  "models_policy.2f2de8c569": [
    "配置文件无法载入，请检查文件格式和大小。",
    "Cannot load configuration. Check file format and size.",
  ],
  "models_policy.cdfe54401b": [
    "配置已保存；仅逐任务明确启动。",
    "Configuration saved. Each task still requires explicit start.",
  ],
  "models_policy.290639bb98": [
    "模型配置已保存。",
    "Model configuration saved.",
  ],
  "models_policy.65af391064": [
    "提交的配置已保存；保留等待期间的新编辑，仍需明确保存。",
    "Submitted configuration saved. Edits made while waiting are retained and still need saving.",
  ],
  "models_policy.521df27e29": [
    "提交的配置已保存；表单还有未保存编辑。",
    "Submitted configuration saved. The form has further unsaved edits.",
  ],
  "models_policy.9c9e7b18ff": [
    "保留未保存编辑；保存后才成为任务配置。",
    "Unsaved edits retained. They become task configuration only after saving.",
  ],
  "models_policy.f317b33b57": [
    "服务器配置已变化；未保存编辑保留，原版本保存将拒绝冲突。可明确放弃编辑后刷新。",
    "Server configuration changed. Edits retained; saving the old revision will reject conflicts. Explicitly discard edits before refreshing.",
  ],
  "models_policy.f110bfe562": [
    "配置已保存；自动执行关闭。",
    "Configuration saved. Automatic execution off.",
  ],
  "models_policy.02c0b2af51": ["尚未配置模型。", "No model configured."],
  "models_policy.bdd3214f7b": [
    "放弃未保存的模型配置编辑并重新读取？",
    "Discard unsaved model edits and reload?",
  ],
  "project_skills.79f47b14c6": ["专业资料 Skill", "Professional source Skill"],
  "project_skills.74074f7c23": [
    "使用项目 fake-expert + Pi",
    "Use project fake-expert + Pi",
  ],
  "project_skills.03913f97c4": [
    "已安装的离线编译镜像摘要",
    "Installed offline compiler image digest",
  ],
  "project_skills.257fc94819": [
    "检查并保存 Skill 设置",
    "Check and save Skill settings",
  ],
  "project_skills.6688ab5747": ["刷新 Skill 状态", "Refresh Skill status"],
  "project_skills.a4bd31cf41": [
    "离线编译镜像摘要",
    "Offline compiler image digest",
  ],
  "project_skills.68b0606c3b": ["离线运行环境", "Offline runtime"],
  "project_skills.94bf4be55a": [
    "将完整 Skill 放入上方目录。兼容更新用于新任务；正在处理的书籍继续使用原快照。Pi 沿用目标库模型与统一消耗预算。",
    "Place the complete Skill in the directory above. Compatible updates apply to new tasks; running books retain their snapshots. Pi uses the target vault's model and shared budget.",
  ],
  "project_skills.8e91230f9d": [
    "正在检查本地 Skill 和离线环境…",
    "Checking local Skill and offline environment…",
  ],
  "project_skills.1931b3b929": [
    "Skill 设置已保存，只影响新任务。",
    "Skill settings saved. Applies only to new tasks.",
  ],
  "project_skills.d81a0f68d1": ["未发现 Skill", "Skill not found"],
  "project_skills.3d2f0fd9fb": ["Skill 暂不可用", "Skill unavailable"],
  "project_skills.6d3c283f1d": [
    "已发现，待离线检查",
    "Found; offline check pending",
  ],
  "project_skills.d99d6ad57f": ["离线检查通过", "Offline check passed"],
  "source_policy.751b9e7eb8": ["提取文本字节上限", "Extracted text byte limit"],
  "source_policy.ee2a0c459d": [
    "候选结果字节上限",
    "Candidate output byte limit",
  ],
  "source_policy.65981d1148": ["图片总像素上限", "Total image pixel limit"],
  "source_policy.c571e8bd97": ["图片边长上限", "Image side-length limit"],
  "source_policy.810c7d24af": ["单批字节上限", "Byte limit per batch"],
  "source_policy.47414abb00": [
    "固定文档 Worker 镜像摘要",
    "Pinned document worker image digest",
  ],
  "source_policy.7d2ad03a76": [
    "固定文章 Worker 镜像摘要（可留空）",
    "Pinned article worker image digest (optional)",
  ],
  "source_policy.d8854e68dc": ["任务超时秒数", "Task timeout (seconds)"],
  "source_policy.758022b377": [
    "OCR 资格凭据 JSON（可留空）",
    "OCR qualification credentials JSON (optional)",
  ],
  "source_policy.104992b6b7": [
    "DOC 转换资格凭据 JSON（可留空）",
    "DOC conversion qualification credentials JSON (optional)",
  ],
  "source_policy.d06ff30443": [
    "完整 fake-expert 固定版本资格凭据 JSON（可留空）",
    "Pinned full fake-expert qualification credentials JSON (optional)",
  ],
  "source_policy.bcb78d7339": [
    "单 Markdown 超限处理",
    "Oversized Markdown handling",
  ],
  "source_policy.56bde49d7f": [
    "暂停（保留旧任务语义）",
    "Pause (retain previous task semantics)",
  ],
  "source_policy.f3697fbb7c": [
    "按完整结构分片，无法安全拆分则暂停",
    "Split by complete structure; pause if safe splitting is impossible",
  ],
  "source_policy.dd03839378": [
    "保存资料处理设置",
    "Save source processing settings",
  ],
  "source_policy.206d8fdd47": [
    "总量限额仍生效。分片会保留正文并生成目录；不可拆分结构和跨段引用会暂停。设置只影响新任务。",
    "Total limits still apply. Splitting retains text and generates an index; indivisible structures and cross-section references pause. Settings affect new tasks only.",
  ],
  "source_policy.81a6e442ed": [
    "运行资格凭据需要有效 JSON。",
    "Runtime qualification credentials require valid JSON.",
  ],
  "source_policy.456f53aca8": [
    "资料处理设置已保存，只影响新任务。",
    "Processing settings saved. Applies only to new tasks.",
  ],
};
