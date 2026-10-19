import { phrase, setText, uiText } from "./i18n/index.js";
// Local upload, explicit video controls and persistent evidence status.
import { el, checkbox, message } from "./ui.js";
import { rememberDraft, captureDraftSave } from "./unsaved_changes.js";
import { videoSkillSettings } from "./video_skill_settings.js";

const sha = async (bytes) =>
  [...new Uint8Array(await window.crypto.subtle.digest("SHA-256", bytes))]
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
const encoded = (bytes) => {
  let result = "";
  for (const b of bytes) result += String.fromCharCode(b);
  return window.btoa(result);
};

export function createVideoPanel(api) {
  let generation = 0,
    mounted = false,
    controls = null;
  const root = () => document.getElementById("video-processing");
  function clear() {
    generation++;
    mounted = false;
    controls = null;
    root()?.replaceChildren();
  }
  async function refresh() {
    if (mounted) return;
    const scope = ++generation;
    const [clients, resources, sourcePolicy] = await Promise.all([
      api.clients(),
      api.video.settings(),
      api.sourcePolicy(),
    ]);
    if (scope !== generation) return;
    mounted = true;
    const panel = el("details"),
      form = el("form"),
      list = el("div"),
      output = el("p");
    panel.append(
      el("summary", uiText("视频提取（字幕 / 本地转写 / Pi + Skill）")),
    );
    panel.append(videoSkillSettings(api));
    form.dataset.draft = "tracked";
    const field = (title, input) => {
      const label = el("label", uiText(title));
      label.append(input);
      form.append(label);
      return input;
    };
    const client = field("处理身份", el("select")),
      vault = field("目标知识库", el("select"));
    for (const c of clients.filter((c) => c.active))
      client.append(new window.Option(c.name, c.id));
    const fillVaults = () => {
      vault.replaceChildren();
      const c = clients.find((c) => c.id === client.value);
      for (const g of c?.grants || [])
        if (g.can_add && g.can_query && g.can_original)
          vault.append(new window.Option(g.vault_id, g.vault_id));
    };
    client.addEventListener("change", fillVaults);
    fillVaults();
    const input = field("本地视频 MP4 / 音频 WAV", el("input"));
    input.type = "file";
    input.accept = ".mp4,.wav";
    input.required = true;
    const url = field("或使用公开 Bilibili / YouTube 视频链接", el("input"));
    url.type = "url";
    url.addEventListener("input", () => {
      input.required = !url.value.trim();
    });
    const duration = field("完整时长（秒，后台会独立核对）", el("input"));
    duration.type = "number";
    duration.min = "1";
    duration.max = "7200";
    duration.step = "0.001";
    duration.required = true;
    const subtitle = field("已有字幕 SRT / VTT（优先使用，可选）", el("input"));
    subtitle.type = "file";
    subtitle.accept = ".srt,.vtt";
    const frames = checkbox(
      "保留每10秒抽样关键帧（不代表画面语义已核验）",
      false,
    );
    const glossary = field(
      "自定义术语表 JSON（可选，留空采用默认术语库）",
      el("textarea"),
    );
    form.append(
      frames.label,
      el(
        "p",
        uiText(
          "字幕优先，无可用字幕时自动转写并采用默认术语库。智能处理继承目标库的 Pi、外发许可、模型预算及人工／自动入库策略。此本地上传入口保留2小时及文件资源保护；消息入口的视频链接使用 V3 流程。",
        ),
      ),
    );
    const submit = el("button", uiText("开始视频提取"));
    submit.type = "submit";
    form.append(submit, output);
    async function rows() {
      if (!client.value || !vault.value) return;
      const items = await api.video.list(client.value, vault.value);
      if (scope !== generation) return;
      list.replaceChildren();
      for (const item of items) {
        const row = el("article", null, "record-card");
        row.dataset.taskId = item.id;
        row.dataset.taskKind = "videos";
        row.append(
          el(
            "p",
            `${item.id} · ${item.state} · ${item.completeness_status || "unknown"}`,
          ),
          el(
            "p",
            phrase("分段完成 {p0}/{p1}", {
              p0:
                item.segments?.filter((s) => s.state === "complete").length ||
                0,
              p1: item.segments?.length || 0,
            }),
          ),
        );
        if (item.error_code || item.reason)
          row.append(
            el(
              "p",
              item.error_message || item.error_code || item.reason,
              "error",
            ),
          );
        if (item.known_gaps?.length)
          row.append(el("pre", item.known_gaps.join("\n")));
        for (const [operation, title, visible] of [
          [
            "resume",
            "继续 / 核对中断任务",
            [
              "failed",
              "waiting_budget",
              "cancelled",
              "interrupted",
              "running",
              "launching",
            ].includes(item.state),
          ],
          [
            "cancel",
            "取消后续处理",
            !["published", "unavailable", "cancelled"].includes(item.state),
          ],
          [
            "publish",
            "确认保存此产物（保留缺项）",
            item.state === "needs_confirmation",
          ],
        ])
          if (visible) {
            const button = el("button", uiText(title));
            button.type = "button";
            button.addEventListener("click", async () => {
              button.disabled = true;
              try {
                await (
                  item.kind === "link_acquisition"
                    ? api.video.linkAction
                    : api.video.action
                )(item.id, client.value, operation);
                await rows();
              } catch (error) {
                message(error.message, true, "media");
              } finally {
                button.disabled = false;
              }
            });
            row.append(button);
          }
        list.append(row);
      }
    }
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      submit.disabled = true;
      const acknowledge = captureDraftSave(form);
      try {
        if (url.value.trim()) {
          const sub = subtitle.files[0];
          await api.video.createLink({
            client_id: client.value,
            vault_id: vault.value,
            url: url.value.trim(),
            duration_ms: Math.round(Number(duration.value) * 1000),
            subtitle_kind: sub?.name.toLowerCase().endsWith(".vtt")
              ? "vtt"
              : "srt",
            subtitle: sub ? await sub.text() : "",
            frames: frames.input.checked,
            glossary: glossary.value.trim() ? JSON.parse(glossary.value) : null,
            glossary_confirmed: Boolean(glossary.value.trim()),
          });
          if (scope !== generation) return;
          acknowledge();
          setText(
            output,
            uiText("公开链接获取任务已启动；访问受限时可改用本地上传。"),
          );
          await rows();
          return;
        }
        const file = input.files[0];
        if (!file || !client.value || !vault.value)
          throw Error("请选择文件、处理身份与有权限的目标库。");
        if (file.size > sourcePolicy.effective_policy.limits.source_bytes)
          throw Error(
            "文件超过本机资源保护上限；请在资料处理设置调整单文件容量，或上传较小的原件。",
          );
        if (!subtitle.files.length && !resources.settings.asr_resources)
          throw Error(
            "无字幕且未配置已验证本地 ASR。请提供字幕，或在下方视频运行设置配置本地转写。",
          );
        const bytes = new Uint8Array(await file.arrayBuffer());
        let imported = await api.createImport({
          client_id: client.value,
          vault_id: vault.value,
          submission_id: window.crypto.randomUUID(),
          filename: file.name,
          source_sha256: await sha(bytes),
          source_bytes: bytes.length,
          configuration_sha256: sourcePolicy.configuration_sha256,
          action: "archive",
          engine: "ordinary",
          profile: "native",
          references: {},
          overrides: {},
        });
        for (
          let offset = 0, ordinal = 0;
          offset < bytes.length;
          offset += imported.chunk_bytes, ordinal++
        ) {
          const chunk = bytes.slice(offset, offset + imported.chunk_bytes);
          await api.importChunk(imported.id, {
            client_id: client.value,
            ordinal,
            sha256: await sha(chunk),
            data: encoded(chunk),
          });
          setText(
            output,
            phrase("已上传 {p0} / {p1} 字节", {
              p0: Math.min(offset + chunk.length, bytes.length),
              p1: bytes.length,
            }),
          );
        }
        imported = await api.finishImport(imported.id, client.value);
        const sub = subtitle.files[0];
        const task = await api.video.create({
          client_id: client.value,
          import_id: imported.id,
          duration_ms: Math.round(Number(duration.value) * 1000),
          subtitle_kind: sub?.name.toLowerCase().endsWith(".vtt")
            ? "vtt"
            : "srt",
          subtitle: sub ? await sub.text() : "",
          frames: frames.input.checked,
          glossary: glossary.value.trim() ? JSON.parse(glossary.value) : null,
          glossary_confirmed: Boolean(glossary.value.trim()),
        });
        await api.video.action(task.id, client.value, "start");
        if (scope !== generation) return;
        acknowledge();
        setText(output, uiText("原件已归档，分段任务已启动；可刷新查看状态。"));
        await rows();
      } catch (error) {
        if (scope === generation) setText(output, uiText(error.message));
      } finally {
        submit.disabled = false;
      }
    });
    const reload = el("button", uiText("刷新视频任务"));
    reload.type = "button";
    reload.addEventListener("click", () =>
      rows().catch((error) => message(error.message, true, "media")),
    );
    const advanced = el("details"),
      settingsForm = el("form"),
      image = el("input"),
      asr = el("textarea"),
      linkImage = el("input"),
      save = el("button", uiText("保存视频运行设置"));
    advanced.append(el("summary", uiText("视频运行设置")));
    image.value = resources.settings.image || "";
    setText(image, uiText("sha256:… 固定离线媒体镜像"), "placeholder");
    setText(image, uiText("视频镜像摘要"), "aria-label");
    linkImage.value = resources.settings.link_image || "";
    setText(linkImage, uiText("公开视频下载镜像摘要"), "aria-label");
    setText(linkImage, uiText("sha256:… 固定 yt-dlp 获取镜像"), "placeholder");
    asr.value = resources.settings.asr_resources
      ? JSON.stringify(resources.settings.asr_resources, null, 2)
      : "";
    setText(asr, uiText("已验证本地ASR资源配置"), "aria-label");
    save.type = "submit";
    settingsForm.dataset.draft = "tracked";
    settingsForm.append(
      el(
        "p",
        uiText(
          "需预装含 ffmpeg 的固定媒体镜像；ASR只接受应用已验证的本地权重运行时，不会自动下载。",
        ),
      ),
      image,
      linkImage,
      asr,
      save,
    );
    advanced.append(settingsForm);
    settingsForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      save.disabled = true;
      const acknowledge = captureDraftSave(settingsForm);
      try {
        const result = await api.video.configure({
          expected_sha256: resources.sha256,
          settings: {
            ...resources.settings,
            image: image.value || null,
            link_image: linkImage.value || null,
            asr_resources: asr.value.trim() ? JSON.parse(asr.value) : null,
          },
        });
        Object.assign(resources, result);
        acknowledge();
        message("视频运行设置已保存。", false, "media");
      } catch (error) {
        message(error.message, true, "media");
      } finally {
        save.disabled = false;
      }
    });
    panel.append(form, reload, list, advanced);
    controls = { client, vault, fillVaults, rows, panel };
    root().replaceChildren(panel);
    rememberDraft(form);
    rememberDraft(settingsForm);
    await rows();
  }
  return {
    refresh,
    clear,
    async selectScope(cid, vid) {
      await refresh();
      if (!controls) return;
      controls.client.value = cid;
      controls.fillVaults();
      controls.vault.value = vid;
      controls.panel.open = true;
      await controls.rows();
      rememberDraft(controls.panel);
    },
  };
}
