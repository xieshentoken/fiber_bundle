import { joinText, setText, uiText } from "./i18n/index.js";
// Project-only compiler Skill settings. Existing model and worker settings stay independent.
import { el, action, captureMessage } from "./ui.js";
import {
  rememberDraft,
  hasUnsavedChanges,
  captureDraftSave,
} from "./unsaved_changes.js";

export function createProjectSkillsView(api, root) {
  let epoch = 0,
    form = null,
    value = null;
  function clear() {
    epoch++;
    form = null;
    value = null;
  }
  async function refresh() {
    if (form && hasUnsavedChanges(form)) return;
    const selected = ++epoch,
      next = await api.projectSkills();
    if (selected !== epoch || (form && hasUnsavedChanges(form))) return;
    value = next;
    if (!form) {
      const section = el("section"),
        path = el("p", null, "project-skill-path");
      section.id = "project-skills-settings";
      section.append(el("h3", uiText("专业资料 Skill")), path);
      form = el("form");
      form.dataset.draft = "tracked";
      form.id = "project-skills-form";
      const enabled = el("input"),
        enabledLabel = el("label", uiText("使用项目 fake-expert + Pi")),
        image = el("input"),
        imageLabel = el("label", uiText("已安装的离线编译镜像摘要")),
        advanced = el("details"),
        status = el("p"),
        save = el("button", uiText("检查并保存 Skill 设置")),
        reload = el("button", uiText("刷新 Skill 状态"));
      enabled.id = "project-skills-enabled";
      enabled.type = "checkbox";
      image.id = "project-skills-image";
      image.placeholder = "sha256:…";
      setText(image, uiText("离线编译镜像摘要"), "aria-label");
      enabledLabel.append(enabled);
      imageLabel.append(image);
      advanced.append(el("summary", uiText("离线运行环境")), imageLabel);
      save.type = "submit";
      reload.type = "button";
      status.id = "project-skills-status";
      status.setAttribute("aria-live", "polite");
      form.append(
        enabledLabel,
        advanced,
        el(
          "p",
          uiText(
            "将完整 Skill 放入上方目录。兼容更新用于新任务；正在处理的书籍继续使用原快照。Pi 沿用目标库模型与统一消耗预算。",
          ),
        ),
        save,
        reload,
        status,
      );
      section.append(form);
      root().prepend(section);
      action(reload, refresh);
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const generation = epoch,
          acknowledge = captureDraftSave(form),
          notify = captureMessage("inventory", "project-skills");
        save.disabled = true;
        setText(status, uiText("正在检查本地 Skill 和离线环境…"));
        try {
          const result = await api.saveProjectSkills({
            enabled: enabled.checked,
            image: image.value.trim() || null,
            expected_sha256: value.settings_sha256,
          });
          if (generation !== epoch) return;
          value = result;
          acknowledge();
          setText(status, uiText("Skill 设置已保存，只影响新任务。"));
          notify(status.textContent);
          await refresh();
        } catch (error) {
          if (generation === epoch) {
            setText(status, uiText(error.message));
            notify(error.message, true);
          }
        } finally {
          save.disabled = false;
        }
      });
    }
    const section = form.closest("section");
    setText(
      section.querySelector(".project-skill-path"),
      uiText(next.directory),
    );
    form.querySelector("#project-skills-enabled").checked =
      next.settings.enabled;
    form.querySelector("#project-skills-image").value =
      next.settings.image || "";
    const names = {
      missing: "未发现 Skill",
      invalid: "Skill 暂不可用",
      detected: "已发现，待离线检查",
      ready: "离线检查通过",
    };
    setText(
      form.querySelector("#project-skills-status"),
      joinText([
        "",
        names[next.state] || next.state,
        "",
        next.snapshot
          ? ` · ${next.snapshot.release} / ${next.snapshot.snapshot_sha256.slice(0, 12)}`
          : "",
        "",
        next.reason ? ` · ${next.message || next.reason}` : "",
        "",
      ]),
    );
    rememberDraft(form);
  }
  return { clear, refresh };
}
