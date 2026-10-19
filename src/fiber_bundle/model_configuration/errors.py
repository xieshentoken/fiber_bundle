"""Safe field-specific model diagnostics; upstream bodies never become UI messages."""

from ..config import InvalidInput

MESSAGES = {
    "model_test_audio_unsupported": (
        "model",
        "此模型已用于音频转写，请在实际转写任务中核对；文本测试不适用，未发起调用。",
    ),
    "model_timeout": (
        "base_url",
        "模型服务请求超时，请核对接口地址与服务状态；调用时的未知消费保留，系统不会自动重试。",
    ),
    "model_runtime_archive_missing": (
        "runtime",
        "应用缺少已核实的运行环境归档，请重新安装完整应用包，再准备运行环境。",
    ),
    "model_runtime_prepare_timeout": (
        "runtime",
        "加载受限运行环境超时，请检查 Docker 状态及磁盘空间，再重新检查环境。",
    ),
    "model_backup_secret_missing": (
        "api_key",
        "备份缺少模型解密材料，恢复已暂停；请使用包含模型凭据的完整加密备份。",
    ),
    "model_secret_corrupt": (
        "api_key",
        "模型凭据密文损坏或认证失败，请从完整加密备份恢复，或重新输入 API Key。",
    ),
    "model_test_failed": (
        "model",
        "测试调用未完成，后台已保留失败状态和预留；请检查模型配置与后台诊断后明确重试。",
    ),
    "model_test_interrupted": (
        "model",
        "后台重启中断了模型测试，用量尚未核实；请检查服务商用量后明确重试。",
    ),
    "model_test_restored": ("model", "此测试来自恢复副本，已暂停；请核对恢复配置后提交新测试。"),
    "model_test_approval_required": ("approve_tokens", "请先确认允许这次测试消耗少量 Tokens。"),
    "model_test_busy": ("model", "已有模型正在测试，请等待完成后重试。"),
    "model_key_budget_exhausted": (
        "budget_tokens",
        "本应用的 Key 预算不足，请到模型预算中调整上限或等待下个周期。",
    ),
    "model_price_unknown_use_token_budget": (
        "credential_id",
        "此模型价格尚未核实，不能使用金额预算。请选择 Tokens 预算的 Key，"
        "或继续已核价的旧配置；不会转换或清零旧消费。",
    ),
    "model_runtime_architecture": (
        "runtime",
        "此运行环境适用于 ARM64；当前 Docker 架构不匹配，请使用匹配的环境包。",
    ),
    "model_key_required": ("api_key", "请填写此服务商的 API Key。"),
    "model_key_format": ("api_key", "API Key 含空白或控制字符，请仅粘贴 Key。"),
    "model_keychain_unavailable": (
        "api_key",
        "系统钥匙串无法读取，请解锁登录钥匙串或重新配置 API Key。",
    ),
    "model_credential_revoked": (
        "api_key",
        "此任务绑定的 Key 已停用或轮换，请使用当前配置重新提交。",
    ),
    "model_revision_conflict": ("name", "配置已被另一操作修改，请刷新后重新保存。"),
    "model_endpoint_invalid": (
        "base_url",
        "请填写合法的 API 基础地址；云端需 HTTPS，本地服务需回环地址。",
    ),
    "model_endpoint_forbidden": (
        "base_url",
        "此地址指向系统元数据或非模型资源，请修改 API 基础地址。",
    ),
    "model_referenced": ("configuration_id", "此模型仍被默认项或 Agent 引用，请先替换关联模型。"),
    "model_unconfigured": ("configuration_id", "请先添加并选择模型配置。"),
    "model_disabled": ("configuration_id", "此模型已停用，请选择其他模型或重新启用。"),
    "model_effort_unsupported": (
        "effort",
        "当前模型或协议未核实支持此思考强度，请选择服务商默认或受支持的等级。",
    ),
    "model_discovery_unsupported": ("model", "服务未提供模型列表，请手动填写准确的模型 ID。"),
    "model_authentication_failed": (
        "api_key",
        "服务商拒绝此 API Key，请检查 Key 是否有效及所属账户/项目。",
    ),
    "model_permission_denied": (
        "api_key",
        "服务商拒绝此操作，请在服务商控制台检查 Key 的模型或列表读取权限。",
    ),
    "model_rate_limited": (
        "api_key",
        "服务商限制了请求频率，请稍后重试或检查该 Key 的速率限制；余额未核实。",
    ),
    "model_provider_quota": ("api_key", "服务商明确报告额度不足，请在服务商控制台检查账户额度。"),
    "model_transport_failure": ("base_url", "无法连接模型服务，请检查接口地址、网络和证书。"),
    "model_response_invalid": (
        "base_url",
        "服务返回的数据不符合所选协议，请检查接口地址和协议类型。",
    ),
    "model_protocol_unsupported": ("protocol", "此模型需要尚未适配的协议，请选择兼容模型。"),
    "model_runtime_missing": ("runtime", "受限 Pi 环境尚未准备，请点击准备运行环境。"),
    "model_runtime_changed": (
        "runtime",
        "受限运行环境与可信清单不符，请重新准备，不使用同名替代镜像。",
    ),
    "model_docker_unavailable": ("runtime", "Docker 后台不可用，请安装并启动 Docker 后重新检查。"),
}


class ModelError(InvalidInput):
    def __init__(self, code: str):
        self.code = code
        self.field, self.message = MESSAGES.get(code, ("model", "模型操作失败，请检查配置或重试。"))
        super().__init__(code)

    def public(self) -> dict[str, str]:
        return {"code": self.code, "field": self.field, "message": self.message}
