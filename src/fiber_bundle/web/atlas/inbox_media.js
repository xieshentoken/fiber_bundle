// Media review: explicit human audio/visual assessments. Evidence is literal text; sampled
// frames never replace full-picture review; publication state is re-read after every attempt.
import { h, clear, icon } from "./dom.js";
import { createInboxHistory } from "./inbox_history.js";
import { strings } from "./i18n.js";
import {
  button,
  card,
  checkbox,
  empty,
  facts,
  failure,
  field,
  listRow,
  notice,
  select,
  skeleton,
  textarea,
  disclosure,
} from "./ui.js";
import { run, toast } from "./feedback.js";
import { confirmPublication } from "./publish.js";
import {
  mediaState,
  publicationState,
  verdicts,
  verdictText,
} from "./labels.js";
import {
  captureDraftSave,
  hasUnsavedChanges,
  rememberDraft,
} from "../unsaved_changes.js";
import { bytes, dateTime, shortId } from "./format.js";

const t = strings({
  zh: {
    tasks: "媒体任务",
    task: "媒体任务 {id}",
    none: "暂无媒体处理任务。",
    pick: "从左侧选择一个处理完成的媒体任务开始核对。",
    switchDirty: "媒体审核还有未保存内容，确定切换？",
    needAll: "请核对完整来源和音频，并明确画面核对状态。",
    needReasons: "请为每个来源单元记录实际判断和理由。",
    evidence: "本次审核的证据绑定",
    local: "本地原件",
    platformOpen: "打开平台来源核对完整播放",
    declared: "平台声明时长",
    converted: "转换文件测量时长",
    convertedNone: "旧记录未单独测量",
    access: "获取方式",
    platformNote: "获取成功不代表完整平台覆盖；登录与完整播放仍需核对。",
    direct: "直接上传原件；没有绑定的平台来源。",
    noContext: "后台未提供来源信息，请刷新后核对原件。",
    sourceSha: "原件 SHA-256",
    resultSha: "处理结果 SHA-256",
    glossary: "术语修订",
    structureNote: "结构处理完成和术语确认记录不能替代本人语义及画面审核。",
    download: "下载完整原件并核对",
    audioOnly: "音频优先整理，未核验画面内容。",
    framesNote: "关键帧是抽样证据，不能替代完整视频核对。",
    timestamps: "时间定位依据：{v}",
    incomplete: "转录覆盖或术语疑点未解决，不能发布。",
    historical: "链接媒体已按确认清理；转录、关键帧和审计记录保留。",
    history: "最新已保存审核",
    noReview: "尚无已保存的人工判断。",
    reviewId: "审核记录",
    recorded: "记录时间",
    visualDone: "画面：已核对",
    visualAudio: "画面：音频模式，无画面审核",
    coverage: "覆盖区间（ms）",
    historyNote:
      "发布依据为此已保存记录；新判断需重新填写并保存。仍未独立核验。",
    pubState: "发布状态：{state}；审核已冻结。",
    pubPath: "发布位置",
    checkSource: "我已核对完整来源及全时长覆盖",
    checkAudio: "我已核对音频、转录、数字和术语",
    checkVisual: "我已核对完整视频画面，抽样帧以外的内容也已检查",
    verdict: "判断",
    reason: "理由",
    frame: "抽样 {ms} ms",
    corrections: "转录规范化",
    original: "原始",
    normalized: "规范化",
    ambiguities: "转录疑点",
    save: "记录媒体人工审核",
    publishSaved: "发布已保存审核的媒体",
    published: "媒体已发布",
    conflict: "发布冲突，需核对",
    resume: "核对并恢复媒体发布",
    unknownPub: "发布状态暂不可确认，请刷新后核对；审核已暂时锁定。",
    frozen: "任务已进入发布流程，审核冻结。",
    dirty: "有未保存的新判断，保存或刷新前不能发布。",
    ready: "人工审核已保存，可显式发布；仍未独立核验。",
    notReady: "需核对完整来源、音频、画面及转录疑点。",
    saved: "媒体判断已记录，已重新读取保存的判断与理由。",
    changed: "已保存审核记录发生变化，请刷新并核对最新记录。",
    reviewChanged: "审核记录已变化，请核对最新保存的判断后再发布。",
    pubResult: "媒体发布状态：{state}",
    cleanup: "查看链接媒体清理范围",
    cleanupConfirm:
      "我确认清理此任务自有的 {n} 个媒体副本（{size}），保留转录、关键帧和审计。",
    cleanupApply: "按此清单清理",
    cleanupDone: "链接媒体清理状态：{state}",
  },
  en: {
    tasks: "Media tasks",
    task: "Media task {id}",
    none: "No media processing tasks.",
    pick: "Pick a processed media task on the left to start reviewing.",
    switchDirty: "The media review has unsaved input. Switch anyway?",
    needAll:
      "Check the full source and audio, and state whether the picture was reviewed.",
    needReasons: "Record a judgment and reason for every source unit.",
    evidence: "Evidence bound to this review",
    local: "Local original",
    platformOpen: "Open the platform source to check full playback",
    declared: "Platform-declared duration",
    converted: "Measured converted duration",
    convertedNone: "Not measured in older records",
    access: "Access method",
    platformNote:
      "A successful fetch doesn't mean full platform coverage; sign-in and full playback still need checking.",
    direct: "Uploaded directly; no platform source bound.",
    noContext:
      "The backend didn't provide source information. Refresh and check the original.",
    sourceSha: "Original SHA-256",
    resultSha: "Result SHA-256",
    glossary: "Glossary revision",
    structureNote:
      "Structural completion and glossary confirmation don't replace your own semantic and visual review.",
    download: "Download the full original to check",
    audioOnly: "Audio-first processing; picture content not verified.",
    framesNote:
      "Key frames are samples and can't replace reviewing the full video.",
    timestamps: "Timestamp basis: {v}",
    incomplete:
      "Transcript coverage or glossary questions are unresolved; can't publish.",
    historical:
      "Linked media was cleaned up as confirmed; transcript, frames and audit are kept.",
    history: "Latest saved review",
    noReview: "No saved human judgments yet.",
    reviewId: "Review record",
    recorded: "Recorded",
    visualDone: "Picture: reviewed",
    visualAudio: "Picture: audio mode, no picture review",
    coverage: "Coverage (ms)",
    historyNote:
      "Publication uses this saved record; new judgments must be entered and saved again. Not independently verified.",
    pubState: "Publication: {state}; review frozen.",
    pubPath: "Published to",
    checkSource: "I checked the full source and full-duration coverage",
    checkAudio: "I checked audio, transcript, numbers and terms",
    checkVisual: "I checked the full video picture, beyond the sampled frames",
    verdict: "Judgment",
    reason: "Reason",
    frame: "Sample at {ms} ms",
    corrections: "Transcript normalization",
    original: "Original",
    normalized: "Normalized",
    ambiguities: "Transcript questions",
    save: "Record media review",
    publishSaved: "Publish the saved review",
    published: "Media published",
    conflict: "Publication conflict — check",
    resume: "Check and resume publication",
    unknownPub:
      "Publication state can't be confirmed. Refresh and check; the review is locked for now.",
    frozen: "The task entered publication; review frozen.",
    dirty: "Unsaved new judgments — save or refresh before publishing.",
    ready:
      "Human review saved; you can publish explicitly. Not independently verified.",
    notReady: "Check the full source, audio, picture and transcript questions.",
    saved: "Media judgments recorded and re-read from the saved record.",
    changed: "The saved review changed. Refresh and check the latest record.",
    reviewChanged:
      "The review record changed; check the latest saved judgments before publishing.",
    pubResult: "Media publication: {state}",
    cleanup: "Show linked-media cleanup scope",
    cleanupConfirm:
      "I confirm cleaning up {n} media copies owned by this task ({size}); transcript, frames and audit are kept.",
    cleanupApply: "Clean up this list",
    cleanupDone: "Linked-media cleanup: {state}",
  },
});
function platformUrl(value) {
  if (typeof value !== "string") return null;
  return /^https:\/\/www\.bilibili\.com\/video\/BV[A-Za-z0-9]{10}$/.test(
    value,
  ) || /^https:\/\/www\.youtube\.com\/watch\?v=[A-Za-z0-9_-]{11}$/.test(value)
    ? value
    : null;
}
export function assessment(packet, source, audio, visual, decisions) {
  const c = packet.candidate;
  if (!source || !audio || visual !== (packet.mode === "full_evidence"))
    throw new Error(t("needAll"));
  if (
    decisions.length !== packet.units.length ||
    decisions.some((d) => !d.reason.trim())
  )
    throw new Error(t("needReasons"));
  return {
    source_sha256: c.source_sha256,
    result_sha256: c.result_sha256,
    glossary_revision: c.glossary_revision,
    glossary_sha256: c.glossary_sha256,
    source_reviewed: true,
    audio_reviewed: true,
    visual_reviewed: visual,
    coverage_ranges: [{ start_ms: 0, end_ms: c.duration_ms }],
    judgments: decisions,
  };
}
function evidence(context, candidate) {
  const pairs = [];
  if (context) {
    pairs.push([
      t("local"),
      `${context.media_type} · ${bytes(context.source_bytes)} · ${context.duration_ms} ms`,
    ]);
    const platform = context.platform;
    if (platform) {
      const url = platformUrl(platform.url);
      if (url)
        pairs.push([
          "URL",
          h(
            "a",
            { href: url, target: "_blank", rel: "noopener noreferrer" },
            url,
          ),
        ]);
      pairs.push([t("declared"), platform.declared_duration_ms + " ms"]);
      pairs.push([
        t("converted"),
        platform.converted_duration_ms === null
          ? t("convertedNone")
          : platform.converted_duration_ms + " ms",
      ]);
      pairs.push([t("access"), platform.access_state]);
    }
  }
  pairs.push([t("sourceSha"), h("code", candidate.source_sha256)]);
  pairs.push([t("resultSha"), h("code", candidate.result_sha256)]);
  pairs.push([
    t("glossary"),
    h("code", `${candidate.glossary_revision} · ${candidate.glossary_sha256}`),
  ]);
  return disclosure(
    t("evidence"),
    facts(pairs, "is-rows"),
    !context
      ? notice(t("noContext"), "warning")
      : context.platform
        ? h("p.a-subtle", t("platformNote"))
        : h("p.a-subtle", t("direct")),
    h("p.a-subtle", t("structureNote")),
  );
}
function history(review, publication) {
  const root = h("div.a-stack");
  if (!review) root.append(h("p.a-subtle", t("noReview")));
  else {
    const a = review.assessment;
    root.append(
      facts([
        [t("reviewId"), h("code", review.review_id)],
        [t("recorded"), dateTime(review.recorded_at)],
        ["", a.visual_reviewed ? t("visualDone") : t("visualAudio")],
        [t("coverage"), JSON.stringify(a.coverage_ranges)],
      ]),
      h(
        "ul.a-judgments",
        a.judgments.map((j) =>
          h(
            "li",
            h("strong", `${j.unit_id} · ${verdictText(j.verdict)}`),
            h("p", j.reason),
          ),
        ),
      ),
      h("p.a-subtle", t("historyNote")),
    );
  }
  if (publication)
    root.append(
      notice(
        t("pubState", { state: publication.state }),
        "info",
        h(
          "p",
          h("code", publication.entry_note_path || publication.relative_path),
        ),
      ),
    );
  return disclosure(t("history"), root);
}

export function create(ctx, params) {
  const listHost = h("div.a-rows");
  const completedHistory = createInboxHistory(() => drawList(), params.id);
  const detail = h("div.a-stack");
  const root = h(
    "div.a-split",
    h(
      "div.a-split-list",
      card(
        { title: t("tasks"), className: "is-flush" },
        completedHistory.node,
        listHost,
      ),
    ),
    detail,
  );
  let generation = 0,
    epoch = 0,
    selected = params.id || null,
    tasks = [];

  function drawList() {
    const shown = completedHistory.filter(tasks);
    clear(
      listHost,
      shown.length
        ? shown.map((task) => {
            const pub = task.publication_state
              ? publicationState(task.publication_state)
              : null;
            return listRow({
              iconName: "media",
              title: task.filename || t("task", { id: shortId(task.job_id) }),
              detail: shortId(task.job_id),
              status:
                pub && task.publication_state !== "not_published"
                  ? pub
                  : mediaState(task.state),
              selected: selected === task.job_id,
              dataset: { id: task.job_id },
              onClick:
                task.state === "complete"
                  ? () => {
                      if (
                        hasUnsavedChanges(detail) &&
                        !window.confirm(t("switchDirty"))
                      )
                        return;
                      selected = task.job_id;
                      drawList();
                      void open(task);
                    }
                  : null,
            });
          })
        : empty(t("none"), { iconName: "media" }),
    );
  }
  async function open(task) {
    const mine = ++epoch;
    clear(detail, skeleton(6));
    let packet;
    try {
      packet = await ctx.api.mediaPacket(task.job_id);
    } catch (error) {
      if (mine === epoch)
        clear(
          detail,
          failure(error, () => open(task)),
        );
      return;
    }
    if (mine !== epoch) return;
    const isCurrent = () => mine === epoch;
    const historyHost = h("div", history(packet.review, packet.publication));
    const original = packet.candidate.historical
      ? null
      : h(
          "a.a-button",
          {
            href: `/admin/media/${encodeURIComponent(task.job_id)}/source`,
            download: "",
          },
          icon("download"),
          t("download"),
        );
    const source = checkbox(t("checkSource"));
    const audio = checkbox(t("checkAudio"));
    const visual = checkbox(t("checkVisual"));
    if (packet.mode === "audio_only") visual.input.disabled = true;
    const decisions = [];
    const form = h("form.a-form", { dataset: { draft: "tracked" } });
    for (const unit of packet.units) {
      const verdict = select(verdicts(), "uncertain", {
        "aria-label": unit.unit_id + " " + t("verdict"),
      });
      const reason = textarea({
        required: true,
        maxLength: 2000,
        rows: 2,
        "aria-label": unit.unit_id + " " + t("reason"),
      });
      const frame = packet.candidate.frames.find(
        (f) =>
          f.sha256 === unit.locator.sha256 &&
          f.timestamp_ms === unit.locator.timestamp_ms,
      );
      decisions.push({ unit, verdict, reason });
      form.append(
        h(
          "fieldset.a-unit",
          h("legend", unit.unit_id),
          frame
            ? h(
                "figure.a-figure",
                h("img", {
                  src: `/admin/media/${encodeURIComponent(task.job_id)}/frames/${encodeURIComponent(frame.sha256)}`,
                  alt: t("frame", { ms: frame.timestamp_ms }),
                  loading: "lazy",
                }),
                h("figcaption", t("frame", { ms: frame.timestamp_ms })),
              )
            : null,
          h("div.a-unit-text", h("pre.a-pre", unit.text)),
          h("div.a-unit-meta", h("code", JSON.stringify(unit.locator))),
          h(
            "div.a-form-grid",
            field(t("verdict"), verdict),
            field(t("reason"), reason),
          ),
        ),
      );
    }
    const transcript = packet.candidate.transcript;
    if (transcript.corrections.length)
      form.append(
        disclosure(
          t("corrections"),
          transcript.corrections.map((c) =>
            h(
              "pre.a-pre",
              `${t("original")}: ${c.original}\n${t("normalized")}: ${c.normalized}`,
            ),
          ),
        ),
      );
    if (transcript.ambiguities.length)
      form.append(
        disclosure(
          t("ambiguities"),
          transcript.ambiguities.map((issue) =>
            h("pre.a-pre", JSON.stringify(issue)),
          ),
        ),
      );
    const gate = h("p", { role: "status" });
    const save = h("button.a-button", { type: "submit" }, t("save"));
    const publish = h(
      "button.a-button.is-primary",
      { type: "button" },
      t("publishSaved"),
    );
    const plan = button(t("cleanup"), { variant: "ghost", iconName: "trash" });
    const cleanupHost = h("div.a-stack");
    form.append(
      h("div.a-checks", source.label, audio.label, visual.label),
      h("div.a-form-actions.is-sticky", save, publish, plan),
      cleanupHost,
    );

    const readAssessment = () =>
      assessment(
        packet,
        source.input.checked,
        audio.input.checked,
        visual.input.checked,
        decisions.map(({ unit, verdict, reason }) => ({
          unit_id: unit.unit_id,
          verdict: verdict.value,
          reason: reason.value,
        })),
      );
    const snapshot = () => {
      try {
        return JSON.stringify(readAssessment());
      } catch {
        return JSON.stringify([
          source.input.checked,
          audio.input.checked,
          visual.input.checked,
          decisions.map((d) => [d.verdict.value, d.reason.value]),
        ]);
      }
    };
    const controls = [
      source.input,
      audio.input,
      visual.input,
      ...decisions.flatMap(({ verdict, reason }) => [verdict, reason]),
    ];
    const fixedDisabled = controls.map((control) => control.disabled);
    let saved = snapshot(),
      dirty = false,
      busy = false,
      unknown = false;
    function update() {
      const frozen = !!packet.publication;
      controls.forEach(
        (control, i) =>
          (control.disabled = fixedDisabled[i] || busy || frozen || unknown),
      );
      save.disabled = busy || frozen || unknown;
      publish.disabled =
        busy ||
        dirty ||
        unknown ||
        !packet.review?.review_id ||
        !packet.review_gate.publication_ready ||
        ["published", "conflict"].includes(packet.publication?.state);
      publish.textContent =
        packet.publication?.state === "published"
          ? t("published")
          : packet.publication?.state === "conflict"
            ? t("conflict")
            : frozen
              ? t("resume")
              : t("publishSaved");
      gate.textContent = unknown
        ? t("unknownPub")
        : frozen
          ? t("frozen")
          : dirty
            ? t("dirty")
            : packet.review_gate.publication_ready
              ? t("ready")
              : t("notReady");
    }
    for (const control of controls)
      for (const name of ["input", "change"])
        control.addEventListener(name, () => {
          dirty = snapshot() !== saved;
          update();
        });
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (busy || packet.publication || unknown || !isCurrent()) return;
      let submitted;
      try {
        submitted = readAssessment();
      } catch (error) {
        toast(error.message, { tone: "error" });
        return;
      }
      const acknowledge = captureDraftSave(form);
      busy = true;
      save.setAttribute("aria-busy", "true");
      update();
      try {
        const result = await ctx.api.reviewMedia(task.job_id, submitted);
        const fresh = await ctx.api.mediaPacket(task.job_id);
        if (!isCurrent()) return;
        packet = fresh;
        clear(historyHost, history(fresh.review, fresh.publication));
        if (fresh.review?.review_id !== result.review_id)
          throw new Error(t("changed"));
        saved = JSON.stringify(submitted);
        dirty = snapshot() !== saved;
        acknowledge();
        toast(t("saved"), { tone: "success" });
      } catch (error) {
        if (isCurrent()) toast(error.message, { tone: "error" });
      } finally {
        busy = false;
        save.removeAttribute("aria-busy");
        if (isCurrent()) update();
      }
    });
    publish.addEventListener("click", async () => {
      if (publish.disabled || busy || !isCurrent()) return;
      const displayed = packet.review.review_id;
      busy = true;
      publish.setAttribute("aria-busy", "true");
      update();
      let problem;
      try {
        if (
          !(await confirmPublication(ctx.api, "media", task.job_id, {
            review_id: displayed,
          }))
        ) {
          busy = false;
          publish.removeAttribute("aria-busy");
          if (isCurrent()) update();
          return;
        }
        if (!isCurrent()) return;
        const result = await ctx.api.publishMedia(task.job_id, {
          review_id: displayed,
        });
        if (isCurrent())
          toast(t("pubResult", { state: result.state }), { tone: "success" });
      } catch (error) {
        problem = error;
      }
      // Even a failed response may have prepared or renamed a publication.
      try {
        const fresh = await ctx.api.mediaPacket(task.job_id);
        if (!isCurrent()) return;
        packet = fresh;
        clear(historyHost, history(fresh.review, fresh.publication));
        if (
          fresh.review?.review_id !== displayed &&
          (!problem || problem.status === 422)
        )
          problem = new Error(t("reviewChanged"));
      } catch (error) {
        unknown = true;
        problem = error;
      } finally {
        busy = false;
        publish.removeAttribute("aria-busy");
        if (isCurrent()) {
          update();
          if (problem) toast(problem.message, { tone: "error" });
        }
      }
    });
    plan.addEventListener("click", () =>
      run(plan, async () => {
        const value = await ctx.api.mediaCleanupPlan(task.job_id);
        if (!isCurrent()) return;
        const agree = checkbox(
          t("cleanupConfirm", {
            n: value.file_count,
            size: bytes(value.bytes),
          }),
        );
        const apply = button(t("cleanupApply"), { variant: "danger-outline" });
        apply.disabled = true;
        agree.input.addEventListener(
          "change",
          () => (apply.disabled = !agree.input.checked),
        );
        apply.addEventListener("click", () =>
          run(apply, async () => {
            const result = await ctx.api.mediaCleanup(task.job_id, {
              confirmation_sha256: value.confirmation_sha256,
            });
            if (!isCurrent()) return;
            original?.remove();
            toast(t("cleanupDone", { state: result.state }), {
              tone: "success",
            });
          }),
        );
        clear(cleanupHost, agree.label, h("div", apply));
      }),
    );
    rememberDraft(form);
    update();
    clear(
      detail,
      card(
        {
          title: task.filename || t("task", { id: shortId(task.job_id) }),
          subtitle: task.job_id,
          actions: original,
        },
        h("div.a-review-gate", icon("shield"), gate),
        packet.candidate.complete ? null : notice(t("incomplete"), "error"),
        packet.candidate.historical ? notice(t("historical"), "info") : null,
        notice(
          packet.mode === "audio_only" ? t("audioOnly") : t("framesNote"),
          "warning",
          h("p.a-subtle", t("timestamps", { v: transcript.timestamp_basis })),
        ),
        evidence(packet.source_context, packet.candidate),
        historyHost,
        form,
      ),
    );
  }
  async function refresh() {
    const mine = ++generation;
    if (!tasks.length) clear(listHost, skeleton(4));
    try {
      const list = await ctx.api.media();
      if (mine !== generation) return;
      tasks = list.tasks;
      drawList();
      if (!detail.childElementCount)
        clear(detail, card({}, empty(t("pick"), { iconName: "media" })));
      if (selected && !detail.querySelector("form")) {
        const task = tasks.find(
          (item) => item.job_id === selected && item.state === "complete",
        );
        if (task) void open(task);
      }
    } catch (error) {
      if (mine === generation) clear(listHost, failure(error, refresh));
    }
  }
  return {
    node: root,
    refresh,
    dirty: () => hasUnsavedChanges(detail),
    dispose() {
      generation++;
      epoch++;
    },
  };
}
