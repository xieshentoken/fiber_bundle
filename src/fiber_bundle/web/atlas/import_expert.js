// fake-expert results: explicit compiler artifacts and candidate approval. Source text never
// executes actions; the draft stays unverified (semantic review deferred).
import { h, icon } from "./dom.js";
import { strings } from "./i18n.js";
import {
  button,
  checkbox,
  codeBlock,
  field,
  input,
  notice,
  facts,
} from "./ui.js";
import { run, toast } from "./feedback.js";
import { sheet } from "./dialog.js";
import { confirmPublication } from "./publish.js";

const t = strings({
  zh: {
    title: "结构化 Draft 与证据锚点",
    pdf: "下载原 PDF 核对",
    draftNote: "Reference Draft · 独立语义审核待完成 · 模型调用 0",
    workpack: "workpack ZIP 导入编号",
    workpackHelp: "明确归档的 workpack（同身份、同库）；留空恢复原作业。",
    resume: "恢复完整 Skill",
    open: "查看 Draft",
    counts: "{o} 个对象 · {a} 个证据锚点 · draft / deferred",
    query: "查询原始候选协议",
    queryHelp: "保留 assurance、对象与证据 ID。",
    ask: "查询 Reference Draft",
    package: "下载完整 Reference Draft 包",
    published: "候选已导入：{path}；索引仍须显式更新。",
    checkSource: "我已核对原 PDF、对象与证据锚点",
    checkCandidate: "以未独立核实的候选分析导入，不作为原文证据",
    publish: "确认导入候选笔记与结构化包",
    need: "请先核对来源并确认候选状态。",
    done: "候选已导入；请显式更新索引。",
  },
  en: {
    title: "Structured draft and evidence anchors",
    pdf: "Download the original PDF",
    draftNote:
      "Reference draft · independent semantic review pending · 0 model calls",
    workpack: "workpack ZIP import ID",
    workpackHelp:
      "An explicitly archived workpack (same identity and vault); empty resumes the original job.",
    resume: "Resume full Skill",
    open: "View draft",
    counts: "{o} objects · {a} evidence anchors · draft / deferred",
    query: "Query the raw candidate protocol",
    queryHelp: "Keeps assurance, object and evidence IDs.",
    ask: "Query reference draft",
    package: "Download the full reference draft package",
    published:
      "Candidate imported: {path}; the index still needs an explicit update.",
    checkSource: "I checked the original PDF, objects and evidence anchors",
    checkCandidate:
      "Import as unverified candidate analysis, not source evidence",
    publish: "Import candidate note and structured package",
    need: "Check the source and acknowledge the candidate status first.",
    done: "Candidate imported; update the index explicitly.",
  },
});

export function expertControls(ctx, item, onChanged) {
  const task = item.expert;
  if (!task) return null;
  const q = `client_id=${encodeURIComponent(item.client_id)}`;
  const nodes = [
    h(
      "p.a-subtle",
      `${task.code || task.state}${task.next_action ? " · " + task.next_action : ""}`,
    ),
    h("p.a-subtle", t("draftNote")),
    h(
      "a.a-link",
      {
        href: `/admin/imports/${encodeURIComponent(item.id)}/source?${q}`,
        download: "source.pdf",
      },
      icon("download"),
      t("pdf"),
    ),
  ];
  if (task.state === "paused") {
    const workpack = input({ value: task.workpack_import_id || "" });
    const resume = button(t("resume"), { variant: "small", iconName: "play" });
    resume.addEventListener("click", () =>
      run(resume, async () => {
        await ctx.api.resumeExpert(item.id, {
          client_id: item.client_id,
          workpack_import_id: workpack.value.trim() || null,
        });
        await onChanged();
      }),
    );
    nodes.push(
      h(
        "div.a-field-row",
        field(t("workpack"), workpack, { help: t("workpackHelp") }),
        resume,
      ),
    );
  }
  if (task.state === "completed") {
    const open = button(t("open"), { variant: "small", iconName: "eye" });
    open.addEventListener("click", () =>
      run(open, () => openDraft(ctx, item, onChanged)),
    );
    nodes.push(open);
  }
  return h("div.a-stack.a-expert", nodes);
}
async function openDraft(ctx, item, onChanged) {
  const packet = await ctx.api.expertArtifact(item.id, item.client_id);
  const view = sheet({
    title: t("title"),
    subtitle: item.filename,
    wide: true,
  });
  const question = input({ maxLength: 256 });
  const answer = h("div");
  const ask = button(t("ask"), { iconName: "search" });
  ask.addEventListener("click", () =>
    run(ask, async () => {
      const result = await ctx.api.expertQuery(
        item.id,
        item.client_id,
        question.value,
      );
      answer.replaceChildren(codeBlock(JSON.stringify(result, null, 2)));
    }),
  );
  view.body.append(
    facts([
      ["", t("counts", { o: packet.objects.length, a: packet.anchors.length })],
    ]),
    codeBlock(
      JSON.stringify(
        { objects: packet.objects, anchors: packet.anchors },
        null,
        2,
      ),
    ),
    h(
      "div.a-field-row",
      field(t("query"), question, { help: t("queryHelp") }),
      ask,
    ),
    answer,
    h(
      "a.a-link",
      {
        href: `/admin/imports/${encodeURIComponent(item.id)}/expert-package?client_id=${encodeURIComponent(item.client_id)}`,
        download: "reference-draft.zip",
      },
      icon("download"),
      t("package"),
    ),
  );
  if (packet.publication?.state === "published") {
    view.body.append(
      notice(
        t("published", {
          path:
            packet.publication.entry_note_path ||
            packet.publication.relative_path,
        }),
        "success",
      ),
    );
    return;
  }
  const source = checkbox(t("checkSource"));
  const candidate = checkbox(t("checkCandidate"));
  const publish = button(t("publish"), {
    variant: "primary",
    iconName: "upload",
  });
  publish.addEventListener("click", () =>
    run(publish, async () => {
      if (!source.input.checked || !candidate.input.checked)
        throw new Error(t("need"));
      const body = {
        client_id: item.client_id,
        artifact_sha256: packet.manifest_sha256,
        source_reviewed: true,
        candidate_acknowledged: true,
      };
      if (!(await confirmPublication(ctx.api, "expert", item.id, body)))
        return false;
      await ctx.api.publishExpert(item.id, body);
      toast(t("done"), { tone: "success" });
      view.close();
      await onChanged();
    }),
  );
  view.body.append(
    h("div.a-checks", source.label, candidate.label),
    h("div.a-form-actions", publish),
  );
}
