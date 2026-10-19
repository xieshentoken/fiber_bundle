const errors = {
  channel_pause_processing_required:
    "修改入口配置前，请先分别暂停查询和保存服务并等待当前阶段结束。",
  channel_service_revision_conflict: "服务状态已变化，请刷新后重试。",
  channel_service_still_running: "当前阶段尚未结束，请等待服务停止后再启动。",
  channel_services_already_independent: "该入口已启用独立运行，请刷新。",
  channel_services_activation_required: "请先启用独立运行。",
  channel_service_role_unavailable: "此入口未配置该职责，请先检查入口配置。",
  channel_service_binding_unavailable: "机器人绑定不可用，请先核对绑定。",
  channel_service_launch_failed:
    "服务启动失败，配置和历史记录保留，请查看诊断。",
  channel_service_image_changed: "服务运行版本校验失败，请重新准备该版本。",
  channel_query_cannot_capture_materials:
    "查询入口不能同时开启资料接收，请使用保存入口。",
  channel_intake_requires_feishu_add:
    "笔记保存入口需要飞书机器人及资料接收权限。",
  channel_query_role_unavailable: "此入口负责笔记保存，请使用查询入口提问。",
  channel_query_service_paused: "查询处理已暂停，请先继续查询服务。",
};
export const serviceErrorText = (code) => errors[code] || null;
export function createChannelServicesApi(request) {
  const path = (id) => `/admin/channels/${encodeURIComponent(id)}/services`;
  return {
    channelServicesActivate: (id, revision) =>
      request(`${path(id)}/activate`, "POST", {
        expected_channel_revision: revision,
      }),
    channelServiceAction: (id, role, operation, revision) => {
      if (
        !["query", "intake"].includes(role) ||
        !["start", "pause", "update"].includes(operation)
      )
        throw new Error("Invalid service action");
      return request(`${path(id)}/${role}/${operation}`, "POST", {
        expected_revision: revision,
      });
    },
  };
}
