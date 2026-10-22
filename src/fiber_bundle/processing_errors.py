"""Finite actionable processing errors; never forward provider or credential text."""

from .config import IntegrityError, InvalidInput
from .media_link_failure import REASONS as _LINK_REASONS

MESSAGES = {
    "cloud_metadata_unavailable": (
        "系统未提供 iCloud 同步状态，无法确认当前副本。请使用已获目录访问权限的 Fiber 后台核对；"
        "此状态不代表文件已过期或发生冲突，原任务与文件保持。"
    ),
    "automatic_index_source_changed": (
        "已保存笔记与当前索引不一致，未更改任务状态。请核对笔记位置与内容并更新索引后再恢复。"
    ),
    "project_skill_dependency_python": (
        "离线编译镜像的 Python 版本不满足 Skill 要求，请选择兼容环境。"
    ),
    "project_skill_dependency_pypdf": ("离线编译镜像缺少所需的 pypdf 6.10.0，请选择兼容环境。"),
    "project_skill_dependency_pdftoppm": ("离线编译镜像缺少可用的 pdftoppm，请选择兼容环境。"),
    "project_skill_binding_invalid": ("项目 Skill 与任务绑定不一致，原任务保留，请核对版本快照。"),
    "project_skill_directory_invalid": (
        "项目 Skill 目录不可用，请检查专用目录，不能使用链接或越界路径。"
    ),
    "project_skill_directory_unreadable": ("项目 Skill 目录暂时不可读，请检查本地文件权限后刷新。"),
    "project_skill_path_invalid": ("Skill 包含缺失、链接或不支持的文件，请放入完整本地副本。"),
    "project_skill_files_missing": ("完整 fake-expert Skill 缺少必要脚本或结构定义。"),
    "project_skill_files_invalid": ("Skill 文件存在重名歧义或超出本地运行保护范围。"),
    "project_skill_protocol_incompatible": (
        "此 Skill 与当前原生 PDF 编译接口不兼容，请使用兼容版本。"
    ),
    "project_skill_source_changed": ("检查期间 Skill 文件发生变化，请完成更新后重试。"),
    "project_skill_snapshot_changed": ("任务使用的 Skill 快照缺失或被改动，已停止，旧结果保留。"),
    "project_skill_qualification_changed": ("Skill 的运行环境检查记录不一致，请重新检查环境。"),
    "project_skill_runtime_unavailable": (
        "离线编译环境不可用，请检查已安装的固定镜像；不会下载或回退。"
    ),
    "project_skill_doctor_failed": ("完整 Skill 的离线自检未通过，请核对依赖和兼容版本。"),
    "project_skill_settings_invalid": ("项目 Skill 设置无效，请核对配置。"),
    "project_skill_settings_changed": ("项目 Skill 设置已在其他窗口更新，请刷新后保存。"),
    "project_skill_native_pdf_required": ("当前项目 Skill 适配支持专业资料的原生文本 PDF。"),
    "intake_channel_unavailable": "没有找到此批次对应的消息入口，请在消息入口核对本人绑定。",
    "intake_channel_ambiguous": "有多个入口绑定同一批资料，请先核对入口配置，避免重复入库。",
    "intake_channel_paused": "消息入口尚未运行。请在消息入口启动接收器后，继续此批次入库。",
    "intake_target_required": "请在消息入口的自动入库设置中选择可写的目标知识库，再继续此批次。",
    "intake_batch_changed": "批次状态或处理方式已改变，请刷新后核对；不会覆盖已保存的资料。",
    "intake_archive_interrupted": "原件归档中断，已有资料保留。请在消息入口核对后恢复同一批次。",
    "intake_processing_interrupted": (
        "原消息及原件已归档，后续处理入库中断。请核对本批诊断后恢复，不必重新转发。"
    ),
    "automatic_policy_layout_changed": (
        "知识库保存规则绑定的目录已变化，已停止后续入库。请在知识库的保存规则中"
        "沿用原路径重新预览并确认启用，再在消息入口恢复原批次；不要重新转发。"
    ),
    "layout_directory_limit": (
        "旧发布程序的目录扫描上限阻止保存；已提取结果保留，请更新后从发布阶段继续。"
    ),
    "layout_policy_reconfirmation_required": (
        "保存规则尚未重新确认启用，请核对原目录并预览确认，再恢复原任务。"
    ),
    "layout_directory_changed": "发布目录的身份发生变化，已停止写入；请核对保存规则后恢复原任务。",
    "layout_policy_changed": "保存规则已变化，原发布计划保留；请核对冻结规则，避免覆盖已有资料。",
    "layout_name_conflict": "预留文件名均已被占用，已保留原文件；请核对目标目录中的同名资料。",
    "automatic_stage_requires_reconciliation": (
        "当前处理阶段未完成，已有结果保留；请核对失败阶段与诊断后恢复原任务。"
    ),
    "skill_export_child_originals_required": (
        "专家子包含原件或图片等二进制证据。请在导出向导显式勾选包含附件与原件，"
        "并在连接配置授予原件权限；否则请创建可分享的完整子包，不能裁掉文件后冒称原包有效。"
    ),
    "skill_export_permissions_changed": (
        "导出权限或连接版本已变化。请在 Agent 与客户端恢复所需查询/原件权限，"
        "并重新预览；旧确认不会继续使用。"
    ),
    "skill_export_source_changed": (
        "所选来源版本已改变。请重新预览并创建新的导出版本，旧包不会被覆盖。"
    ),
    "skill_export_storage_unavailable": (
        "导出目录暂不可写或磁盘空间不足。请释放本机磁盘空间并核对应用数据目录权限，再明确继续。"
    ),
    "skill_export_sensitive_content": (
        "所选资料或专家子包含本机绝对路径或疑似凭据，导出已停止。请在来源创建可分享"
        "版本，再重新预览；不会删改原包来伪造摘要通过。"
    ),
    "skill_export_path_invalid": (
        "资料包含无法安全转为相对引用的路径。请修正来源中的绝对路径或越界引用，再重新预览。"
    ),
    "skill_export_capacity": (
        "本次导出超过单包运行保护范围（1000模块/64MiB），请缩小所选资料范围。"
        "这不是提取额度或模型预算。"
    ),
    "skill_export_selection_required": (
        "请选择当前连接有权读取的资料；主题模式请选择一个已有MOC笔记。"
    ),
    "skill_export_confirmation_required": (
        "预览确认已失效或任务已取消。请重新预览并确认生成，不复用旧确认。"
    ),
    "skill_export_output_changed": (
        "导出工作文件或ZIP摘要不一致，已阻止下载。请重新创建导出版本，勿修改生成目录。"
    ),
    "skill_export_not_ready": "导出尚未完整生成并通过校验。请查看此任务的进度和错误后明确继续。",
    "processing_key_budget_unconfigured": (
        "原件已保留，智能处理尚未调用模型。"
        "请在执行与费用 → 统一模型预算为此 API Key 设置预算，再明确继续。"
    ),
    "model_key_budget_exhausted": (
        "所选 API Key 的本周期模型预算不足。请在执行与费用 → 统一模型预算调整上限，再明确继续任务。"
    ),
    "budget_revision_changed": ("模型预算已在其他窗口改变。请刷新统一模型预算后再保存。"),
    "budget_accounting_basis_immutable": (
        "已有账本的预算单位、周期与时区不能直接改写。可调整周期上限；历史消费会保留。"
    ),
    "budget_credential_unconfigured": (
        "此 API Key 尚未配置。请先在模型与查询配置模型凭据，再选择统一预算。"
    ),
    "budget_credential_path_invalid": (
        "凭据引用必须是本机绝对路径且不能是符号链接。请检查模型配置中的凭据文件。"
    ),
    "processing_cloud_content_not_allowed": (
        "尚未允许本次资料发送至云模型。请在知识库 → 提取与入库策略"
        "确认内容外发设置，或选择基础提取。"
    ),
    "processing_model_unconfigured": (
        "尚未配置可用于 Pi 的处理模型。请在模型与查询添加并选择可用模型，或选择基础提取。"
    ),
    "automatic_policy_scope_required": (
        "请选择处理身份和资料类型，并授予目标库查询、原件及新增权限。"
    ),
    "automatic_policy_requires_unified_layout": (
        "自动处理需要有效且已启用的统一知识库目录（v2）。请在保存规则的"
        "“目录规则版本”选择“统一知识库目录”，预览并确认启用后再保存策略。"
    ),
    "processing_input_requires_chunks": (
        "本次输入超过模型单次容量，需要分段处理。原件已保留，请使用分段任务继续。"
    ),
    "intake_processing_budget_required": (
        "原件已保存，智能处理等待模型预算。请在执行与费用调整统一模型预算，再明确继续此链接的处理。"
    ),
    "chat_attachment_missing": "聊天记录引用的附件缺失，原件已保留，请补齐附件后核对。",
    "chat_image_missing": "聊天文字已保留，原包未包含的图片已在笔记对应位置标出。",
    "intake_article_image_limit": "正文超过单篇256张图片处理上限，原链接已保留，请分段导入。",
    "chat_attachment_ambiguous": "聊天记录引用存在多个同名附件，请核对具体路径。",
    "chat_attachment_projection_pending": "聊天附件尚未完整保存到知识库，请在原件保存记录中核对。",
    "chat_source_changed": "聊天原件摘要发生变化，已停止处理，请核对来源。",
    "attachment_projection_copy_diverged": "附件副本已被编辑或替换，已保留现有文件，请人工核对。",
    "processing_execution_timeout": (
        "Pi多轮处理达到任务时限，已完成调用已记账，原件保留；请核对后分段或明确新尝试。"
    ),
    "processing_executor_stopped": "受限执行器提前退出，原件与调用记录保留，请核对运行环境。",
    "processing_executor_protocol": "受限执行器返回不符合协议，已停止发布，请核对运行环境。",
    "processing_model_incomplete": "模型未正常结束处理，尚无可发布草稿；原件与实际调用记录保留。",
    "intake_processing_new_attempt_confirmation_required": (
        "此前模型调用已发生或用量未知，原记录和预算预留保持。请核对后明确确认新增处理尝试；不会自动重发。"
    ),
    "intake_processing_requires_reconciliation": (
        "智能处理未完成或存在未知模型副作用。请先在执行与费用核对本次调用，原件和已完成分段保留。"
    ),
    "intake_processing_configuration_changed": (
        "处理模型或库策略已改变。本次冻结任务未继续，请核对知识库处理策略与已有任务。"
    ),
    "intake_processing_policy_disabled": (
        "目标库的智能处理策略已关闭。请在知识库 → 提取与入库策略核对后，再明确继续。"
    ),
}


MESSAGES.update(
    {
        "intake_video_short_link_unresolved": (
            "视频短链接未能展开，尚未取得视频；请核对分享链接后重试。"
        ),
        "video_skill_asr_empty": (
            "视频已取得，但ASR未识别出有效文字；画面主导路线尚未通过核对，当前未生成笔记。"
        ),
        "video_skill_visual_fallback_invalid": (
            "ASR诊断、音轨或来源不完整/已变化，不能切换画面主导提取；请核对后恢复。"
        ),
        "video_skill_visual_content_missing": (
            "已分析选择帧，但未取得可用于笔记的画面文字或图表事实；当前未入库，可"
            "核对画面模型后恢复。"
        ),
        "video_skill_visual_audit_failed": (
            "画面主导笔记的证据、配图或语音局限说明未通过审计；当前未入库，可查看诊断后恢复。"
        ),
        "intake_x_article_serialization_invalid": (
            "X文章的正文、代码或图片映射未能完整解析，尚未入库；原链接保留，可修复后重试。"
        ),
        "intake_video_single_resource_required": (
            "请提供单个完整视频链接；不自动改用其他分集或播放列表。"
        ),
        "video_runtime_not_configured": (
            "视频任务尚未启动：缺少固定媒体镜像。请在媒体审核 → 视频运行设置配置"
            "已安装的离线媒体镜像。原件保留。"
        ),
        "video_transcription_capability_missing": (
            "视频原件保留，但没有字幕或已验证的本地转写能力。请提供 SRT/VTT"
            "，或在媒体审核 → 视频运行设置配置已验证本地 ASR。API Key"
            " 不能替代此能力。"
        ),
        "video_glossary_confirmation_required": (
            "转写尚未启动：请在视频表单核对术语表并确认；可以明确确认空表。"
        ),
        "video_source_duration_mismatch": (
            "视频实际时长与输入不一致。原件及已完成分段保留；请核对播放器显示的完整"
            "时长，重新建立任务，不能按声明时长截断原件。"
        ),
        "video_segment_timeout": (
            "本视频分段处理超时，已完成分段保留。请在视频运行设置核对离线镜像和每段"
            "超时；核对任务后明确继续。"
        ),
        "video_processing_policy_changed": (
            "目标库处理策略已变化，本次冻结任务不可继续。请在知识库 → 提取与入库"
            "策略核对身份、双开关及模型设置；不会自动重放。"
        ),
        "video_permission_changed": (
            "本次视频的权限已变化。请在 Agent 与客户端核对目标库查询、原件与"
            "新增权限；冻结任务不会自动恢复。"
        ),
        "video_tools_changed": (
            "本次视频绑定的处理工具版本已变化。请核对应用版本和已有分段，不自动重放模型调用。"
        ),
        "video_model_budget_required": (
            "转写和已完成分段保留，Pi 视频整理等待模型预算。请在执行与费用 → "
            "统一模型预算调整后，明确继续任务。"
        ),
        "video_model_reconciliation_required": (
            "Pi 视频整理存在失败或未知费用调用。请先在执行与费用核对该分段；继续"
            "操作不会重试失败的模型请求。"
        ),
        "video_transcription_empty": (
            "未取得有效转写正文，原件保留，不能保存空产物。请核对字幕内容或本地转写结果。"
        ),
        "video_processing_failed": (
            "视频处理未完成，已完成分段和原件保留。请查看视频任务错误状态，并核对媒"
            "体镜像、本地工具及源文件；尚未确认原因，不能判断为 Key 或预算问题"
            "。"
        ),
        "video_tool_failed": (
            "离线媒体工具未成功运行。请核对 Docker 是否启动、视频运行设置中"
            "的固定镜像是否已安装；原件与已完成分段保留。"
        ),
        "video_unified_save_rules_required": (
            "视频已提取，但目标库缺少有效 Inbox 保存规则。请在知识库 → 保"
            "存规则中配置目录，再明确保存。"
        ),
        "video_artifact_changed": (
            "视频产物摘要发生变化，已阻止写入。请核对本次产物与原件，不覆盖或复用变动文件。"
        ),
    }
)

MESSAGES["video_link_runtime_not_configured"] = (
    "公开视频获取未启动。请在媒体审核 → 视频运行设置配置固定公开下载镜像和离线媒体镜像；"
    "也可直接上传本地 MP4/WAV。"
)

for _reason in _LINK_REASONS:
    MESSAGES["video_link_" + _reason] = (
        "公开视频获取失败（"
        + _reason
        + "）。原有任务和已归档原件保留。"
        + (
            {
                "access_restricted": "平台需要登录或不允许获取；请在平台合法取得文件后本地上传。",
                "dependency_unavailable": (
                    "下载镜像缺少 yt-dlp；请在视频运行设置选择已安装的固定获取镜像。"
                ),
                "duration_mismatch": "平台时长与填写值不一致；请核对视频完整时长。",
                "download_size_exceeded": "下载超过任务文件资源保护；请使用较小的原件。",
                "size_unverified": "平台未提供可核对的文件大小或超出容量；请本地上传。",
            }.get(
                _reason,
                "请核对公开视频是否可访问及固定获取镜像；"
                "平台限制无法通过模型 Key 解决，可改用本地上传。",
            )
        )
    )

MESSAGES.update(
    {
        "expert_authoring_headings_unresolved": (
            "原 PDF 未取得可绑定的章节标题，尚未调用模型。"
            "请在资料导入提供同源、已核对章节结构的 workpack，或使用普通文档提取。"
        ),
        "expert_authoring_policy_required": (
            "请在知识库 → 提取与入库策略启用 Pi + Skill，选择有查"
            "询、原件与新增权限的处理身份；明确允许向云模型发送章节资料。"
        ),
        "expert_authoring_binding_changed": (
            "本次专家章节的源文件、权限或模型策略已经变化，已阻止继续。请核对知"
            "识库处理策略与来源版本；已完成材料和费用保留。"
        ),
        "expert_authoring_source_changed": (
            "章节 proposal 的页码、文本片段或摘要与固定源不一致，已阻"
            "止编译。请核对原 PDF 和本次章节材料。"
        ),
        "expert_authoring_scan_workpack_required": (
            "扫描或视觉 profile 缺少已绑定且通过结构审核的 workp"
            "ack。请在资料导入选择明确后端，并提供同源已审核的结构 work"
            "pack；不能把自动 OCR 当作结构审核。"
        ),
        "expert_authoring_unit_invalid": (
            "编译器章节单元缺失、范围错误或模型返回错误单元。原件保留；请核对源"
            " PDF 的章节结构及固定 compiler 版本。"
        ),
        "expert_authoring_proposal_invalid": (
            "章节 proposal 未通过源片段或编译器合同验证，尚未生成完整"
            " Draft。请在资料与版本查看原 PDF 与章节材料；不会跳过编"
            "译器验证或标为 Gold。"
        ),
        "expert_authoring_page_capacity": (
            "PDF 页数或加密状态不符合当前固定 compiler 资源配置。"
            "请在资料处理设置核对页数保护，并使用可读取的 PDF；原件保留。"
        ),
        "expert_authoring_budget_required": (
            "专家章节材料等待统一模型预算，已完成章节和调用不重跑。请在执行与费"
            "用 → 统一模型预算调整后明确继续。"
        ),
        "expert_authoring_model_reconciliation_required": (
            "本章节存在失败或未知费用的模型调用。请先在执行与费用核对；继续操作不会盲目重试该请求。"
        ),
        "expert_authoring_failed": (
            "专家章节材料处理未完成，原件与已完成分段保留。请查看资料与版本中的"
            "章节状态，并核对固定编译器、源文件与模型任务；尚未确认原因，不推测"
            "为 Key 或额度问题。"
        ),
        "expert_authoring_tool_failed": (
            "离线专家工具未成功运行。请核对 Docker、资料处理设置中的固定"
            "完整 fake-expert 镜像和对应 profile；原件保留"
            "，不自动切换 OCR 或下载依赖。"
        ),
        "expert_authoring_tools_changed": (
            "本次章节材料绑定的工具版本已变化。请核对应用版本和已有 propo"
            "sal；不会自动重放模型调用。"
        ),
    }
)

MESSAGES.update(
    {
        "knowledge_version_stale": (
            "来源版本已变化，本次证据未交付。请刷新授权知识目录，核对新版本后重新读取。"
        ),
        "knowledge_module_changed": (
            "已发布专家模块的文件摘要发生变化，读取已停止。请在资料与版本核对原"
            "始 Draft 包及发布记录，不直接覆盖文件。"
        ),
        "knowledge_module_invalid": (
            "已发布专家模块缺少必须的索引、对象或查询组件。请在资料与版本核对完"
            "整 Draft，重新验证后发布。"
        ),
        "knowledge_module_assurance_changed": (
            "专家包生命周期声明与发布绑定不一致，已停止读取。请核对资料与版本中"
            "的 Draft/审核状态，不将其当作 Gold。"
        ),
        "knowledge_expert_runtime_unavailable": (
            "专家只读查询所需的固定运行时未成功启动或退出。请核对 Docker"
            " 与资料处理设置中的完整 fake-expert 镜像；普通笔记检"
            "索仍可用，API Key 无法替代此运行时。"
        ),
        "knowledge_evidence_locator": (
            "证据的行号、页码或时间范围不符合当前版本。请刷新目录并使用返回的实际定位信息。"
        ),
        "knowledge_evidence_capacity": (
            "本次证据为空或超过单次返回保护。请缩小行号、对象或附件范围，再读取"
            "；不会用空结果冒充成功。"
        ),
        "knowledge_catalog_limit": (
            "目录偏移或单页返回量超出保护范围。请使用上一页返回的游标，单页最多50项。"
        ),
        "knowledge_module_capacity": (
            "专家模块超过受控读取容量。请在资料与版本核对包大小并按明确范围重新发布。"
        ),
    }
)


for _phase, _label in {
    "init_job": "视频任务初始化",
    "fiber_public_probe": "公开视频访问",
    "acquire_public": "视频或字幕获取",
    "manage_glossary": "默认术语库采用",
    "normalize_transcript": "字幕校验",
    "run_asr": "本地ASR转写",
    "fiber_asr_api": "API音轨准备或转写校验",
    "select_keyframes": "关键帧提取",
    "crop_illustrations": "配图裁剪",
    "build_evidence_timeline": "证据关联",
    "audit_job": "视频完整性审计",
}.items():
    for _suffix in ("failed", "timeout"):
        MESSAGES[f"video_skill_{_phase}_{_suffix}"] = (
            f"{_label}{'超时' if _suffix == 'timeout' else '未完成'}。原件和成功阶段保留，"
            "请核对访问权限、工具及运行环境后恢复；不会把不完整视频作为成功笔记入库。"
        )
MESSAGES.update(
    {
        "video_skill_not_configured": "视频V3尚未配置，请在资料与版本的视频V3设置中配置完整Skill。",
        "video_skill_processing_policy_required": "请先启用目标知识库的处理策略并配置处理身份。",
        "video_skill_model_reconciliation_required": (
            "视频模型任务结果待核对；旧费用记录保留，新模型尝试需明确确认。"
        ),
        "video_skill_note_requires_recovery": "视频笔记生成未完成，请核对模型任务和预算后恢复。",
        "video_skill_visual_failed": (
            "画面分析失败，不能用纯字幕冒充画面审计成功；可核对模型后恢复。"
        ),
        "video_skill_visual_response_invalid": (
            "画面模型返回的内容或配图区域无效，尚未入库；可明确重试失败调用。"
        ),
        "video_skill_configuration_changed": (
            "视频处理策略或模型已改变，旧任务保留，需核对冻结配置。"
        ),
        "video_skill_source_incomplete": (
            "仅取得部分视频或预览，尚未入库；需补齐可合法访问的完整来源。"
        ),
        "video_skill_source_changed": "视频原件在处理或恢复前发生变化，旧结果保留，已停止发布。",
        "video_skill_final_audit_failed": "视频成品审计未通过，原件保留，可修复后恢复。",
        "video_skill_media_capacity": "视频暂存资料超过配置的磁盘保护，已停止；请核对资源设置。",
        "ocr_profile_required": "需要OCR但尚未配置已验证的OCR环境，原件已保留，配置后可继续。",
        "intake_summary_failed": "原文已入库；可选Pi摘要失败，核对后可继续摘要任务。",
        "intake_summary_invalid_result": "原文已入库；可选摘要格式无效，可确认费用后重新尝试。",
        "intake_abort_store_unavailable": (
            "暂时无法核对取消记录，任务已暂停；请检查入口日志后恢复。"
        ),
        "intake_user_aborted": "任务已由用户取消，后续产物不会发布；如需重新处理，请重新提交资料。",
    }
)


def response(error: Exception) -> tuple[int, dict[str, str]] | None:
    if isinstance(error, (InvalidInput, IntegrityError)) and str(error) in MESSAGES:
        code = str(error)
        status = (
            409
            if code
            in {
                "budget_revision_changed",
                "knowledge_version_stale",
                "knowledge_module_changed",
                "project_skill_settings_changed",
            }
            else 422
        )
        return status, {"error": code, "message": MESSAGES[code]}
    return None


MESSAGES.update(
    {
        "video_asr_api_protocol_unsupported": (
            "所选服务不支持通用音频转写协议，请选择兼容 /audio/transcriptions 的模型。"
        ),
        "video_asr_reconciliation_required": (
            "上次转写调用的结果不明；成功片段保留，请明确确认新付费尝试后恢复。"
        ),
        "video_asr_result_changed": "已保存的转写结果或配置摘要变化，暂停处理，请核对诊断。",
        "video_asr_budget_required": "转写 API 尚未配置 Key 预算，请先在执行与费用中配置。",
        "video_asr_audio_invalid": "待转写音频格式或分片大小不符合要求，原件保留，请核对后恢复。",
        "video_asr_api_failed": "音频转写 API 未完成，成功片段和已有费用保留，请核对后恢复。",
    }
)
