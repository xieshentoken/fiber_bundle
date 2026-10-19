import {
  optionText,
  phrase,
  setText,
  translate,
  uiText,
} from "./i18n/index.js";
// V3 setup is explicit. Opening settings never starts extraction or model calls.
import { el } from "./ui.js";
import {
  captureDraftSave,
  rememberDraft,
  hasUnsavedChanges,
} from "./unsaved_changes.js";

export function videoSkillSettings(api) {
  const root = el("details"),
    content = el("div");
  root.append(el("summary", uiText("图片提取与 ASR 模型设置")), content);
  let loaded = false,
    generation = 0;
  root.addEventListener("fiber:clear", () => {
    generation++;
    loaded = false;
    root.open = false;
    content.replaceChildren();
  });
  root.addEventListener("toggle", async () => {
    if (!root.open || loaded) return;
    loaded = true;
    const current = ++generation;
    try {
      const snapshot = await api.video.skill();
      if (!root.isConnected || current !== generation) return;
      const form = el("form"),
        output = el("p"),
        fields = {};
      form.dataset.draft = "tracked";
      form.append(
        el(
          "p",
          phrase(
            "Skill：{p0}；字幕优先，无字幕自动 ASR，默认术语库无需逐次确认。",
            {
              p0:
                snapshot.skill_state === "configured"
                  ? uiText("已配置")
                  : uiText("待配置"),
            },
          ),
        ),
      );
      form.append(
        el("h3", uiText("图片提取与视频画面分析")),
        el(
          "p",
          uiText(
            "先在“模型与查询 → 模型配置”添加服务地址、模型名和 API Key，再选择支持图片输入的模型。保存不代表已验证模型能力。",
          ),
          "muted",
        ),
      );
      function selectField(key, title, choices, fallback = "") {
        const label = el("label", uiText(title)),
          input = el("select");
        input.name = key;
        for (const [value, name] of choices)
          input.append(optionText(name, value));
        const saved = snapshot.settings[key] ?? fallback;
        if (saved && ![...input.options].some((o) => o.value === saved))
          input.append(optionText("已保存配置（当前不可用）", saved));
        input.value = saved;
        fields[key] = input;
        label.append(input);
        form.append(label);
        return input;
      }
      const models = (snapshot.models || []).filter((m) =>
        /^model_[a-f0-9]{32}$/.test(m.id),
      );
      selectField(
        "vision_model_configuration_id",
        "图片提取模型（使用该模型的 Key 预算）",
        [
          ["", "使用知识库所选处理模型"],
          ["app-default", "跟随应用默认模型"],
          ...models.map((m) => [
            m.id,
            m.name +
              (m.processing_location === "local" ? " · 本地服务" : " · 云端"),
          ]),
        ],
      );
      form.append(
        el("h3", uiText("语音转写（ASR）")),
        el(
          "p",
          uiText(
            "优先使用视频字幕；无可用字幕时使用以下设置。当前任务保留原快照，修改只影响新任务。",
          ),
          "muted",
        ),
      );
      const backend = selectField(
        "asr_backend",
        "ASR 处理方式",
        [
          ["local-qwen3", "本地 Qwen3-ASR"],
          ["transcription-api", "音频转写 API（会发送音频到所选服务）"],
        ],
        "local-qwen3",
      );
      const localFields = [],
        apiFields = [];
      for (const [key, title, fallback] of [
        ["asr_model", "本地 ASR 模型名称或绝对目录", "Qwen/Qwen3-ASR-0.6B"],
        ["asr_language", "转写语言（auto 自动识别；API 可用 zh / en）", "auto"],
      ]) {
        const label = el("label", uiText(title)),
          input = el("input");
        input.name = key;
        input.value = snapshot.settings[key] || fallback;
        input.required = true;
        fields[key] = input;
        label.append(input);
        form.append(label);
        if (key === "asr_model") localFields.push(label);
      }
      localFields.push(
        selectField(
          "asr_device",
          "本地计算设备",
          [
            ["auto", "自动"],
            ["cpu", "CPU"],
            ["mps", "Apple Metal (MPS)"],
            ["cuda", "NVIDIA CUDA"],
          ],
          "auto",
        ).parentElement,
      );
      localFields.push(
        selectField(
          "asr_dtype",
          "本地计算精度",
          [
            ["auto", "自动"],
            ["float32", "Float32"],
            ["float16", "Float16"],
            ["bfloat16", "BFloat16"],
          ],
          "auto",
        ).parentElement,
      );
      apiFields.push(
        selectField(
          "asr_api_model_configuration_id",
          "转写 API 模型（服务需支持 /audio/transcriptions）",
          [
            ["", "请选择已配置的音频转写模型"],
            ...models
              .filter((m) => ["openai", "custom", "local"].includes(m.provider))
              .map((m) => [m.id, m.name]),
          ],
        ).parentElement,
      );
      apiFields.push(
        selectField(
          "asr_api_response_format",
          "转写 API 返回格式",
          [
            ["json", "JSON（通用）"],
            ["verbose_json", "Verbose JSON（服务需支持）"],
          ],
          "json",
        ).parentElement,
      );
      const reserveLabel = el("label", uiText("每 30 秒音频的 Token 预留上限")),
        reserve = el("input");
      reserve.name = "asr_api_reserve_tokens";
      reserve.type = "number";
      reserve.min = "1";
      reserve.max = "200000";
      reserve.value = snapshot.settings.asr_api_reserve_tokens ?? 16384;
      fields.asr_api_reserve_tokens = reserve;
      reserveLabel.append(reserve);
      form.append(reserveLabel);
      apiFields.push(reserveLabel);
      const hint = el(
        "p",
        uiText(
          "本地模型需预先安装，本应用不会自动下载。API 按 30 秒分片；时间戳表示音频分片区间。服务未返回 Token 用量时保留预算预留，金额未知。",
        ),
        "muted",
      );
      form.append(hint);
      function updateBackend() {
        const apiMode = backend.value === "transcription-api";
        for (const label of localFields) {
          label.hidden = apiMode;
          label.querySelector("input, select").disabled = apiMode;
        }
        for (const label of apiFields) {
          label.hidden = !apiMode;
          label.querySelector("input, select").disabled = !apiMode;
        }
        fields.asr_api_model_configuration_id.required = apiMode;
      }
      backend.addEventListener("change", updateBackend);
      updateBackend();
      for (const [key, title] of [
        ["skill_path", "V3 Skill 目录（留空使用项目内置目录）"],
        ["python_path", "视频工具 Python（留空自动查找）"],
        ["asr_python", "ASR Python（留空查找已安装环境）"],
      ]) {
        const label = el("label", uiText(title)),
          input = el("input");
        input.value = snapshot.settings[key] || "";
        input.name = key;
        fields[key] = input;
        label.append(input);
        form.append(label);
      }
      const termsLabel = el(
          "label",
          uiText("默认术语（每行一个，任务固定词库版本）"),
        ),
        terms = el("textarea");
      terms.value = snapshot.settings.default_terms.join("\n");
      terms.name = "default_terms";
      termsLabel.append(terms);
      form.append(termsLabel);
      const toolsLabel = el(
          "label",
          uiText("视频工具目录（每行一个，留空沿用系统 PATH）"),
        ),
        tools = el("textarea");
      tools.name = "tool_directories";
      tools.value = (snapshot.settings.tool_directories || []).join("\n");
      toolsLabel.append(tools);
      form.append(toolsLabel);
      const save = el("button", uiText("保存提取模型与视频设置"));
      save.type = "submit";
      const reload = el("button", uiText("重新读取设置"));
      reload.type = "button";
      reload.addEventListener("click", () => {
        if (
          hasUnsavedChanges(form) &&
          !window.confirm(translate("放弃此处未保存的提取模型设置并重新读取？"))
        )
          return;
        generation++;
        loaded = false;
        content.replaceChildren();
        root.dispatchEvent(new window.Event("toggle"));
      });
      output.setAttribute("role", "status");
      form.append(save, reload, output);
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        save.disabled = true;
        const acknowledge = captureDraftSave(form);
        try {
          const result = await api.video.configureSkill({
            expected_sha256: snapshot.sha256,
            settings: {
              ...snapshot.settings,
              ...Object.fromEntries(
                Object.entries(fields).map(([key, input]) => [
                  key,
                  key === "asr_api_reserve_tokens"
                    ? Number(input.value)
                    : input.value.trim() || null,
                ]),
              ),
              default_terms: [
                ...new Set(
                  terms.value
                    .split("\n")
                    .map((t) => t.trim())
                    .filter(Boolean),
                ),
              ],
              tool_directories: tools.value
                .split("\n")
                .map((p) => p.trim())
                .filter(Boolean),
            },
          });
          snapshot.sha256 = result.sha256;
          snapshot.settings = result.settings;
          acknowledge();
          setText(
            output,
            uiText("已保存，新任务使用新设置；进行中的任务保留原快照。"),
          );
        } catch (error) {
          setText(output, uiText(error.message));
        } finally {
          save.disabled = false;
        }
      });
      content.replaceChildren(form);
      rememberDraft(form);
    } catch (error) {
      if (current !== generation) return;
      setText(content, uiText(error.message));
      loaded = false;
    }
  });
  return root;
}
