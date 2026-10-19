import { el, checkbox } from "./ui.js";
import { uiText } from "./i18n/index.js";
import { control } from "./record_ui.js";
import { relocationErrorText } from "./vault_relocation_api.js";

const stageNames = [
  "暂停并等待任务退出",
  "建立恢复点",
  "移动目录",
  "切换位置",
  "校验身份与关联",
];
export const stages = [
  "quiescing",
  "checkpointing",
  "moving",
  "switching",
  "verifying",
];
const text = (tag, value, cls) => el(tag, uiText(value), cls);
function card(title) {
  const n = el("section", null, "relocation-card");
  if (title) n.append(text("h3", title));
  return n;
}
function info(title, rows) {
  const n = card(title);
  for (const [label, detail] of rows) {
    const line = el("div", null, "relocation-info");
    line.append(text("h4", label), text("p", detail));
    n.append(line);
  }
  return n;
}
function path(parent, label, value) {
  const box = el("div", null, "relocation-path");
  box.append(text("span", label), el("code", value));
  parent.append(box);
}
function check(parent, title, detail, result = "保留", complete = true) {
  const row = el("div", null, "relocation-check"),
    body = el("div");
  row.dataset.complete = String(complete);
  body.append(text("h4", title), text("p", detail));
  row.append(
    el("span", complete ? "✓" : "·", "relocation-check-icon"),
    body,
    text("span", result),
  );
  parent.append(row);
}
function button(parent, label, work, primary = false, options) {
  const b = control(label, work, options);
  if (primary) b.classList.add("primary");
  parent.append(b);
  return b;
}
export function renderRelocation(host, state, actions) {
  const { vault, packet, screen, parent, name, error, busy } = state;
  const completed = packet?.state === "complete",
    restored = packet?.state === "restored";
  const titles = {
    choose: "迁移仓库位置",
    review: "预检通过，确认迁移",
    progress: "正在迁移",
    recovery: "迁移已中断，等待恢复",
    result: restored ? "已恢复原位置" : "迁移完成",
  };
  const root = el("div", null, "relocation-workspace"),
    heading = el("header", null, "relocation-heading");
  const title = text("h2", titles[screen] || "迁移仓库位置");
  title.tabIndex = -1;
  heading.append(
    title,
    el(
      "p",
      `${vault.name} · ${screen === "choose" ? "更换存放目录，继续使用原知识库。" : "保留原有知识库身份、历史与关系。"}`,
    ),
  );
  const steps = el("ol", null, "relocation-steps"),
    current = screen === "choose" ? 0 : screen === "review" ? 1 : 2;
  ["1  选择位置", "2  预检与确认", "3  执行与验证"].forEach((label, i) => {
    const item = text("li", label);
    item.dataset.current = String(i === current);
    item.dataset.done = String(i < current || completed || restored);
    if (i === current) item.setAttribute("aria-current", "step");
    steps.append(item);
  });
  const columns = el("div", null, "relocation-columns"),
    left = card(),
    right = el("aside", null, "relocation-aside"),
    footer = el("footer", null, "relocation-footer");
  const alert = text("p", error || "", "error relocation-error");
  alert.hidden = !error;
  alert.setAttribute("role", "alert");
  const privacy = info("保留原有关系", [
    ["知识库身份", `${vault.name} · ${vault.id}`],
    ["历史与内容", "保留版本、来源和已发布内容。"],
    ["人工 MOC", "保留人工成员关系、所有权及其历史。"],
    ["消息入口", "保留绑定、配对和原有运行状态。"],
  ]);
  if (screen === "choose") {
    left.append(text("h3", "位置"));
    path(left, "当前位置", vault.absolute_root || "由启动配置管理");
    const pLabel = text("label", "目标父目录"),
      pInput = el("input"),
      nLabel = text("label", "目录名称"),
      nInput = el("input");
    pInput.id = "relocation-parent";
    pInput.value = parent;
    pInput.autocomplete = "off";
    pInput.dataset.draft = "tracked";
    pLabel.htmlFor = pInput.id;
    nInput.id = "relocation-name";
    nInput.value = name;
    nInput.autocomplete = "off";
    nInput.dataset.draft = "tracked";
    nLabel.htmlFor = nInput.id;
    const destination = el("code", actions.destination()),
      preview = el("div", null, "relocation-path relocation-path-soft");
    preview.append(text("span", "迁移后位置"), destination);
    const change = () => {
      actions.input(pInput.value, nInput.value);
      destination.textContent = actions.destination();
    };
    pInput.addEventListener("input", change);
    nInput.addEventListener("input", change);
    const picker = el("div", null, "relocation-picker");
    button(
      picker,
      "选择父目录…",
      async () => {
        const selected = await actions.pick();
        if (selected) {
          pInput.value = selected;
          change();
        }
      },
      false,
      { restoreFocus: true, feedback: "directory" },
    );
    picker.append(text("small", "Web 可直接输入本机绝对路径。"));
    left.append(
      pLabel,
      pInput,
      picker,
      nLabel,
      nInput,
      preview,
      text("p", "目标目录须尚未存在。预检只核对条件，文件仍留在原位置。"),
    );
    right.append(
      privacy,
      info("首版支持范围", [
        ["本机同一磁盘", "跨磁盘、云同步目录和网络盘暂不支持。"],
        [
          "整库核验",
          "包含附件和隐藏配置；受保护范围、链接和特殊文件会阻止迁移。",
        ],
      ]),
    );
    if (
      state.history?.some((p) =>
        ["complete", "restored", "cancelled"].includes(p.state),
      )
    ) {
      const history = card("迁移记录");
      for (const p of state.history
        .filter((p) => ["complete", "restored"].includes(p.state))
        .slice(0, 5)) {
        button(
          history,
          p.state === "restored"
            ? "查看已恢复原位置的记录"
            : "查看已完成的迁移记录",
          () => actions.show(p),
        );
      }
      right.append(history);
    }
    button(footer, "取消", actions.close);
    button(footer, "预检迁移", actions.preview, true);
    const unsupported = vault.root_kind === "legacy";
    if (unsupported) {
      alert.textContent = relocationErrorText("relocation_legacy_unsupported");
      alert.hidden = false;
      footer.lastChild.disabled = true;
    }
  } else if (screen === "review") {
    const locations = el("div", null, "relocation-location-pair");
    path(locations, "当前位置", packet.source);
    path(locations, "迁移到", packet.destination);
    left.append(locations, text("h3", "迁移保留清单"));
    check(left, "同一个知识库", `${vault.id} · 名称、授权和默认查询设置`);
    check(
      left,
      "正文与历史",
      `${packet.files.toLocaleString()} 个文件 · ${packet.bytes.toLocaleString()} 字节 · 来源与历史保留`,
    );
    check(
      left,
      "人工 MOC 关系",
      `${packet.preservation.mocs} 个 MOC · 关系所有权与历史修订`,
    );
    check(
      left,
      "消息入口与 Agent 授权",
      `${packet.preservation.channels} 个入口 · ${packet.preservation.grants} 项授权`,
    );
    left.append(
      text(
        "p",
        "目标目录未占用 · 同一文件系统 · 范围策略一致",
        "relocation-success",
      ),
      text("small", "开始后会再次核验；源目录或目标条件变化时须重新预检。"),
    );
    right.append(
      info("开始后的影响", [
        [
          "短暂停用本库",
          "查询、写入和自动任务等待迁移完成；其他知识库可继续使用。",
        ],
        [
          "消息保留排队",
          "已接收消息保留在原队列，恢复后继续；不重发已完成事件。",
        ],
        ["先创建恢复点", "备份文件与关联状态，通过检查后才移动目录。"],
      ]),
    );
    const preparation = info("开始前，请先准备", [
        ["关闭外部编辑", "关闭本仓库的 Obsidian、编辑器，并暂停同步。"],
      ]),
      acknowledge = checkbox("我已关闭编辑器，并暂停本库同步", false);
    preparation.append(acknowledge.label);
    right.append(preparation);
    button(footer, "上一步", actions.choose);
    const start = button(footer, "备份并开始迁移", actions.start, true);
    start.disabled = true;
    start.dataset.policyDisabled = "true";
    acknowledge.input.addEventListener("change", () => {
      start.disabled = !acknowledge.input.checked;
      start.dataset.policyDisabled = String(start.disabled);
    });
  } else if (screen === "progress") {
    const restoring = packet.state === "restoring";
    left.append(
      text("span", restoring ? "正在恢复原位置" : "进行中", "relocation-badge"),
      text(
        "h3",
        restoring
          ? "核对并恢复迁移前位置"
          : stageNames[stages.indexOf(packet.state)] || "等待核对",
      ),
      text("p", "本库暂不可用。关闭此页面不会取消后台任务。"),
    );
    const progress = el("progress");
    progress.max = stages.length;
    progress.value = Math.max(0, stages.indexOf(packet.state));
    progress.setAttribute("aria-label", "已完成的迁移阶段");
    left.append(progress);
    for (let i = 0; i < stages.length; i++) {
      const done = packet.completed_phases.includes(stages[i]),
        active = stages[i] === packet.state;
      check(
        left,
        stageNames[i],
        active ? "当前阶段完成后继续" : done ? "已核验" : "尚未执行",
        done ? "已完成" : active ? "进行中" : "等待",
        done,
      );
    }
    right.append(
      info("迁移期间", [
        ["当前记录位置", packet.current_path],
        ["消息入口", "原绑定保留；已接收消息在队列容量范围内等待。"],
        ["安全停止", "停止后保留现场，再核对是否继续或恢复原位置。"],
      ]),
    );
    button(footer, "返回知识库", actions.close);
    button(footer, "请求停止", actions.stop);
  } else if (screen === "recovery") {
    const recovery = packet.recovery;
    left.append(
      text("span", "待恢复", "relocation-badge"),
      text("h3", "迁移未完成，先核对现场"),
      text(
        "p",
        relocationErrorText(packet.reason) ||
          "重新核对两处位置后，再选择继续迁移或恢复原位置。",
      ),
    );
    path(left, "原位置", packet.source);
    path(left, "目标位置", packet.destination);
    if (recovery) {
      check(
        left,
        "当前文件位置",
        recovery.current_path || "无法确认",
        recovery.resume ? "已核对" : "待处理",
        recovery.resume,
      );
      check(
        left,
        "恢复原位置",
        recovery.restore
          ? "原目录可恢复，不覆盖其他文件"
          : relocationErrorText(recovery.reason) || "条件不满足",
        recovery.restore ? "可用" : "不可用",
        recovery.restore,
      );
    }
    button(left, "重新核对现场", actions.recoveryPreview);
    const restore = button(left, "恢复原位置", () =>
      actions.recover("restore"),
    );
    restore.disabled = !recovery?.restore;
    restore.dataset.policyDisabled = String(restore.disabled);
    right.append(
      privacy,
      info("按证据恢复", [
        ["不覆盖已有目录", "目录身份或内容不符时保留现场，并说明阻塞原因。"],
        ["保留历史", "中断与恢复也会留在迁移记录中。"],
      ]),
    );
    button(footer, "返回知识库", actions.close);
    const resume = button(
      footer,
      "继续迁移",
      () => actions.recover("resume"),
      true,
    );
    resume.disabled = !recovery?.resume;
    resume.dataset.policyDisabled = String(resume.disabled);
  } else {
    left.append(
      text(
        "span",
        restored ? "已恢复" : "已完成",
        "relocation-badge relocation-success",
      ),
      text(
        "h3",
        restored ? "已恢复到迁移前位置" : "原来的知识库，新的存放位置",
      ),
    );
    path(left, "当前有效位置", restored ? packet.source : packet.destination);
    check(left, "知识库身份", `${vault.id} · 原有名称与权限`, "一致");
    check(left, "历史记录", "来源、版本、发布与审计记录", "保留");
    check(left, "人工 MOC 关系", "稳定成员 ID 与人工所有权", "一致");
    check(left, "消息入口与 Agent", "无需重新配对；原暂停入口仍暂停", "一致");
    right.append(
      info("迁移记录", [
        [
          "本次变更",
          restored
            ? "已按本次迁移日志恢复原位置。"
            : "同一磁盘移动；仅更换知识库位置。",
        ],
        [
          "恢复点",
          packet.checkpoint?.path || "目录移动前已停止，无需恢复文件。",
        ],
        ["外部应用", "如需在 Obsidian 中继续编辑，请从当前有效目录打开。"],
      ]),
    );
    button(footer, "查看知识库", actions.close);
    button(footer, "重新选择位置", actions.choose);
  }
  columns.append(left, right);
  root.append(heading, steps, alert, columns, footer);
  root.setAttribute("aria-busy", String(busy));
  host.replaceChildren(root);
  if (busy) root.inert = true;
  return { title };
}
