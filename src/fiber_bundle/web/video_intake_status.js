// Shared video progress and bounded recovery inputs for Atlas and classic pages.
import { locale } from "./i18n/index.js";

const pick = (zh, en) => (locale() === "en" ? en : zh);

export function videoProgressLines(link) {
  const p = link.video_progress;
  if (!p) return [];
  const lines = [
    pick(
      `图片分析：${p.frames_complete}/${p.frames_total ?? "—"}${p.failed_frame ? ` · 停在 ${p.failed_frame}` : ""}`,
      `Image analysis: ${p.frames_complete}/${p.frames_total ?? "—"}${p.failed_frame ? ` · Stopped at ${p.failed_frame}` : ""}`,
    ),
  ];
  if (p.frames_remaining !== null && p.frames_remaining > 0)
    lines.push(
      pick(
        `剩余 ${p.frames_remaining} 帧；后续处理共用本视频的 Token 总预算。`,
        `${p.frames_remaining} frames remain; all further processing shares this video's token budget.`,
      ),
    );
  if (p.token_budget) {
    const q = p.token_budget;
    const n = (value) => Number(value).toLocaleString("en-US");
    lines.push(
      pick(
        `本视频 Tokens：${n(q.used_tokens)} / ${n(q.limit)} · 剩余 ${n(q.remaining_tokens)}`,
        `Video tokens: ${n(q.used_tokens)} / ${n(q.limit)} · ${n(q.remaining_tokens)} remaining`,
      ),
    );
    if (q.reserved_tokens > 0)
      lines.push(
        pick(
          `其中 ${n(q.reserved_tokens)} tokens 为进行中或用量待核对的预留，未按零消耗计算。`,
          `${n(q.reserved_tokens)} tokens are reserved for pending or unconfirmed usage, not counted as zero.`,
        ),
      );
  }
  return lines;
}

export function videoResumeControls(link, { atlas = false } = {}) {
  const root = document.createElement("div");
  root.dataset.videoJob = link.video_processing?.job_id ?? "";
  root.className = atlas ? "a-stack" : "field";
  const label = document.createElement("label");
  label.textContent = pick(
    "本视频 Token 总上限（含历史消耗）",
    "Total token limit for this video (including prior usage)",
  );
  const input = document.createElement("input");
  input.type = "number";
  input.min = "0";
  input.max = "1000000000";
  input.step = "1";
  input.value = String(link.video_progress?.token_budget?.limit ?? 500000);
  input.className = atlas ? "a-input" : "";
  input.dataset.draft = "ignore";
  input.setAttribute("aria-label", label.textContent);
  label.append(input);
  root.append(label);
  const mode = document.createElement("label");
  const direct = document.createElement("input");
  direct.type = "checkbox";
  direct.checked = true;
  direct.dataset.draft = "ignore";
  mode.append(
    direct,
    pick(
      "提取完整后直接排版笔记（推荐）",
      "Build a note directly from complete evidence (recommended)",
    ),
  );
  root.append(mode);
  const help = document.createElement("p");
  help.className = atlas ? "a-subtle" : "muted";
  help.textContent = pick(
    "默认 500,000 tokens；输入与输出合计，重试不清零。剩余预算不足以下一次调用预留时暂停，原 Key 预算同时生效。",
    "Default: 500,000 tokens, input plus output. Retries do not reset usage. Pauses when the next call cannot be reserved; the API Key budget also applies.",
  );
  root.append(help);
  return {
    root,
    options() {
      const count = Number(input.value);
      if (
        !input.value.trim() ||
        !Number.isSafeInteger(count) ||
        count < 0 ||
        count > 1000000000
      )
        throw new Error(
          pick(
            "Token 总上限须为0至1,000,000,000的整数。",
            "The token limit must be an integer from 0 to 1,000,000,000.",
          ),
        );
      return {
        max_video_tokens: count,
        ...(direct.checked ? { video_note_mode: "direct" } : {}),
      };
    },
  };
}
