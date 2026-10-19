import { uiText } from "./i18n/index.js";
// Short page purpose and next step. Existing forms/actions keep their own owners.
import { el } from "./ui.js";
import { revealSection } from "./view_sections.js";
const copy = {
  vaults: [
    "保存与整理",
    "先接入已有仓库，再配置六类目录和处理身份。MOC、自动策略及整库合并各自保留预览与确认。",
  ],
  imports: [
    "从原件到候选",
    "选择身份、目标知识库和提取方式。普通资料与 fake-expert 分别处理；保存原件、生成 Draft 和正式发布是独立阶段。",
  ],
  worklist: [
    "集中处理，保留原始证据",
    "按来源查看当前待办。进入对应记录后完成审批、补充、审核或核对，不在此页批量自动放行。",
  ],
  pending: ["知识库接入审批", "核对申请方、目标库与授予权限，再批准启用。"],
  batches: [
    "选择批次处理方式",
    "收件缓冲与等待本人选择分开显示。冻结后选择处理方式；未知结果先核对，不重发。",
  ],
  documents: [
    "文档与文章审核",
    "核对完整来源及候选中的内容、图片和数字，再记录实际判断。执行完成不代表已经发布。",
  ],
  media: [
    "音频与画面审核",
    "核对完整音视频、转录覆盖和术语疑点。抽样帧不能替代完整画面审核；保留人工审核接口。",
  ],
  channels: [
    "收件、绑定与连接",
    "分别查看飞书/微信绑定、Hermes 进程、心跳及传输。暂停入口不影响已有知识；解绑与删除需明确操作。",
  ],
  clients: [
    "Agent 接入与客户端权限",
    "先选择 Agent 连接或客户端权限。每个身份独立授权；配置、工具可用及问答结果分别验收。",
  ],
  models: [
    "配置路由，按需验证",
    "保存模型与预算后，明确发起测试。配置存在或历史调用成功不代表当前可用；首页不会自动调用模型。",
  ],
  execution: [
    "执行记录与费用口径",
    "执行、取消和未知结果核对在下方处理。云费用是配置单价估算；本地调用记录独立计数。",
  ],
  inventory: [
    "原件、版本与处理配置",
    "按身份和知识库读取资料版本。新资料统一从独立“资料导入”入口进入；组件清单不代表服务在线。",
  ],
  graph: [
    "笔记关联与 MOC",
    "配置自动归类并复核疑难，或手动调整笔记与专家 Skill 的 MOC。高级关联分析保留为补充。",
  ],
  creation: [
    "有来源的文档制作",
    "先准备来源与任务，确认预算后执行。模型草稿、人工确认和保存结果分别记录。",
  ],
  backend: [
    "后台运行与提醒",
    "核对当前实例与恢复状态。桌面窗口和后台独立运行；提醒与启动操作由你明确发起。",
  ],
  backups: [
    "备份、恢复与分发",
    "先准备完整备份范围，再在终端输入加密密码。完成标记不替代密文与恢复验证。",
  ],
  settings: [
    "本机显示与会话",
    "调整主题、字体和字号；安全设置按现有确认保存。显示偏好不改变模型、通道或知识库权限。",
  ],
};
export function mountPageContext() {
  for (const [page, [title, description]] of Object.entries(copy)) {
    const panel = document.getElementById(page + "-panel");
    if (!panel || panel.querySelector(":scope > .page-context")) continue;
    const intro = el("div", null, "page-context");
    intro.append(el("strong", title), el("p", uiText(description), "muted"));
    panel.prepend(intro);
  }
}
export function locateTask(panel, source, id) {
  if (!id) return true;
  const target = [...panel.querySelectorAll("[data-task-id]")].find(
    (node) => node.dataset.taskId === id && node.dataset.taskKind === source,
  );
  if (!target) return false;
  revealSection(target);
  target.tabIndex = -1;
  target.focus({ preventScroll: true });
  target.scrollIntoView({ block: "center", behavior: "instant" });
  return true;
}
