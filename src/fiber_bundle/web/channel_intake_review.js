import { phrase, setText, uiText } from "./i18n/index.js";
import { confirmPublication } from "./publication_preview.js";
import { el, checkbox } from "./ui.js";
import { control } from "./record_ui.js";
import {
  captureDraftSave,
  hasUnsavedChanges,
  rememberDraft,
} from "./unsaved_changes.js";

export async function openIntakeReview(parent, api, identity, batch, index) {
  const old = parent.querySelector(".intake-review");
  if (old && hasUnsavedChanges(old)) return;
  const packet = await api.channelIntakePacket(identity, batch, index);
  if (!parent.isConnected) return;
  old?.remove();
  const root = el("div", null, "intake-review"),
    form = el("form"),
    reason = el("textarea"),
    status = el("p"),
    checks = [
      checkbox("已核对原件与来源", false),
      checkbox("已核对正文 / 字幕及其覆盖范围", false),
      checkbox("已核对图片及顺序（没有图片也须确认）", false),
    ];
  root.append(el("pre", packet.markdown), status);
  const source = el("a", uiText("取回抓取原件 HTML"));
  source.href = api.channelIntakeAssetURL(
    identity,
    batch,
    index,
    "source.html",
  );
  source.download = "source.html";
  root.append(source);
  for (const name of packet.files.filter((n) =>
    /\.(png|jpg|jpeg|gif|webp)$/i.test(n),
  )) {
    const image = el("img");
    image.src = api.channelIntakeAssetURL(identity, batch, index, name);
    image.alt = `原文图片 ${name}`;
    image.loading = "lazy";
    root.append(image);
  }
  if (packet.metadata.gaps)
    root.append(
      el(
        "p",
        phrase("字幕语言：{p0}；未覆盖区间 {p1} 段。现有字幕没有转录或补写。", {
          p0: packet.metadata.language,
          p1: packet.metadata.gaps.length,
        }),
        "muted",
      ),
    );
  form.dataset.draft = "tracked";
  form.addEventListener("submit", (e) => e.preventDefault());
  reason.required = true;
  reason.maxLength = 2000;
  setText(reason, uiText("入库提取审核理由"), "aria-label");
  setText(reason, uiText("填写本次人工核对理由"), "placeholder");
  reason.value = packet.review?.reason || "";
  for (const check of checks) {
    check.input.required = true;
    form.append(check.label);
  }
  let reviewId = packet.review?.id;
  const publish = control("确认发布到所选知识库", async () => {
    if (!reviewId) return;
    if (
      !(await confirmPublication(api, "intake", identity, {
        batch_id: batch,
        index,
        review_id: reviewId,
      }))
    )
      return;
    const result = await api.channelIntakePublish(
      identity,
      batch,
      index,
      reviewId,
    );
    setText(
      status,
      phrase("已发布：{p0}", {
        p0: result.entry_note_path || result.relative_path + "/note.md",
      }),
    );
    form.hidden = true;
  });
  publish.disabled = !reviewId;
  publish.dataset.policyDisabled = String(!reviewId);
  form.append(
    reason,
    control("记录人工审核", async () => {
      if (!form.reportValidity()) return;
      const acknowledge = captureDraftSave(form);
      const result = await api.channelIntakeReview(identity, batch, index, {
        digest: packet.digest,
        reason: reason.value,
        source_reviewed: true,
        text_reviewed: true,
        images_reviewed: true,
      });
      acknowledge();
      reviewId = result.id;
      publish.disabled = false;
      publish.dataset.policyDisabled = "false";
      setText(status, uiText("人工审核已保存，可以确认发布。"));
    }),
    publish,
  );
  if (packet.publication?.state === "published") {
    setText(
      status,
      phrase("已发布：{p0}", {
        p0:
          packet.publication.entry_note_path ||
          packet.publication.relative_path + "/note.md",
      }),
    );
    form.hidden = true;
  }
  root.append(form);
  parent.append(root);
  rememberDraft(form);
}
