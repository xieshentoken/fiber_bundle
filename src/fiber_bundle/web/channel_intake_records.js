import { phrase, uiText, joinText } from "./i18n/index.js";
import { el, checkbox } from "./ui.js";
import { confirmAction } from "./confirm_dialog.js";
import { control, showJSON } from "./record_ui.js";
import {
  captureDraftSave,
  hasUnsavedChanges,
  rememberDraft,
} from "./unsaved_changes.js";
import { channelErrorText } from "./channel_api.js";
import { openIntakeReview } from "./channel_intake_review.js";

export function intakeLabel(code) {
  return (
    {
      waiting: "附件待补齐",
      queued: "等待归档",
      archiving: "归档中 / 中断待核对",
      stored: "原件已归档",
      manual: "待手动确认",
      needs_review: "待核对 / 可明确重试",
      link_only: "仅链接已保存",
      extracting: "提取中 / 中断待核对",
      review_required: "正文 / 字幕待审核",
      metadata_only: "仅来源信息；正文待补全",
      published: "已保存入库",
      sent: "回执已发送",
      unknown: "回执结果待手机核对",
      sending: "回执发送中 / 中断待核对",
      budget_exhausted: "回执额度不足",
      not_sent: "已核对未发送",
      off: "提取未开启",
      pending: "等待提取",
      complete: "提取尝试已结束",
      cancelled: "提取已取消",
      intake_site_not_allowed: "包含未允许的网站，整批保留待确认。",
      intake_material_not_selected: "包含未勾选的资料类型。",
      intake_command_requires_routing: "查询或操作命令不会作为资料保存。",
      intake_message_unsupported: "存在未支持的消息部分，已保留原事件。",
      intake_attachments_missing: "附件待下载；请核对获取额度。",
      intake_download_budget_exhausted:
        "获取额度不足，请调整累计上限后明确重试。",
      intake_store_budget_exhausted:
        "归档额度不足，请调整累计上限后处理所选批次。",
      intake_policy_changed: "策略已改变，请明确选择是否处理此批次。",
      intake_contains_historical_messages:
        "包含策略生效前的消息，需要手动确认。",
      intake_user_cancelled: "已停止自动处理，原消息保留。",
      intake_user_aborted: "已取消后续提取和发布，原件及已入库资料保留。",
      intake_archive_interrupted: "保存中断，需明确恢复；不会自动重复写入。",
      intake_processing_interrupted:
        "原件已归档，后续入库中断；核对后恢复此批次，不必重新转发。",
      automatic_policy_layout_changed:
        "保存规则的目录已变化。请在知识库沿用原路径重新预览并确认启用，再恢复此批次。",
      intake_requires_review: "已有批次等待恢复或核对，不代表已入库。",
      intake_worker_stopped: "自动入库子任务已停止；核对诊断后重启入口。",
      intake_worker_unobserved: "自动入库子任务状态未确认，请刷新核对。",
      intake_storage_busy: "自动入库正在等待后台存储。",
      processing: "提取或入库处理中",
      intake_batch_changed: "批次已被选择或取消，请核对。",
      intake_content_incomplete: "正文或图片不完整，原链接保留，未发布。",
      intake_article_image_limit:
        "正文超过内部配置的图片上限，原链接已保留；正常自动入库不限制图片数量。",
      intake_extractor_unavailable: "没有启用的提取器，仅链接已保存。",
      intake_fetch_failed: "获取失败，请核对来源后明确重试。",
      intake_video_access_restricted: "视频需要登录或不可访问。",
      intake_subtitles_unavailable: "未取得已有字幕，正文待补全。",
      intake_subtitles_access_restricted: "字幕需要登录，正文待补全。",
      intake_video_duration_over_limit: "视频超过当前 60 秒范围，仅保存链接。",
      intake_video_duration_unverified: "视频时长无法核实，仅保存链接。",
      intake_channels_content_unavailable:
        "视频号内容目前无法直接取得，已保留来源待补全。",
      intake_redirect_not_allowed: "跳转目标不在白名单，已停止获取。",
      intake_read_only_vault: "目标知识库只读，不能自动写入。",
      intake_target_required: "请选择已有可写授权的目标库。",
      intake_revision_conflict:
        "策略已在其他窗口改变；请刷新核对，当前草稿保留。",
      intake_permission_unavailable: "入库授权不可用，请检查客户端权限。",
      intake_add_permission_required: "此入口尚未启用资料入库权限。",
      intake_target_unavailable: "知识库目录或范围不可用，请核对接入状态。",
      intake_owner_or_channel_unavailable: "入口尚未绑定本人或已解除。",
      intake_channel_paused: "入口未运行，新任务暂不执行。",
      intake_binding_changed: "本人绑定已改变，请核对并重新保存策略。",
      intake_download_requires_reconciliation:
        "附件下载中 / 中断待核对；不会自动重试。",
      intake_attachment_download_failed: "附件获取失败，可明确选择此批次重试。",
    }[code] || (code ? "需要核对，详情见诊断。" : "")
  );
}

export function intakePhase(phase) {
  return (
    {
      init_job: "初始化视频任务",
      resolve_source: "展开并核对视频短链接",
      fiber_public_probe: "核对公开视频访问",
      acquire_public: "获取视频或字幕",
      normalize_transcript: "校验字幕",
      asr_api: "转写 API 处理中",
      run_asr: "本地 ASR 转写",
      fiber_visual: "核对画面主导提取证据",
      select_keyframes: "提取关键帧",
      visual_analysis: "分析选择帧",
      crop_illustrations: "裁剪笔记配图",
      build_evidence_timeline: "关联时间戳与证据",
      note_authoring: "Pi 生成视频笔记",
      evidence_note: "整理已有画面笔记",
      audit_job: "审计视频产物",
      complete: "视频产物已完成",
    }[phase] ||
    phase ||
    ""
  );
}

export function renderIntakeRecords(root, api, identity, snapshot, reload) {
  if (hasUnsavedChanges(root)) return;
  root.replaceChildren(el("h4", uiText("最近入库记录")));
  root.dataset.draft = "tracked";
  const selected = new Set(),
    history = new Map(snapshot.history.map((b) => [b.batch_id, b]));
  const selectables = [];
  for (const record of snapshot.records) {
    history.delete(record.batch_id);
    const card = el("article", null, "record-card intake-record");
    card.append(
      el("p", `${record.batch_id} · ${intakeLabel(record.state)}`),
      el("p", uiText(intakeLabel(record.reason)), "muted"),
    );
    if (record.value.failure_history?.length) {
      const history = el("details");
      history.append(el("summary", uiText("恢复前的失败记录")));
      for (const failure of record.value.failure_history)
        history.append(el("p", uiText(intakeLabel(failure.code)), "muted"));
      card.append(history);
    }
    if (record.outcome) {
      const outcome = el(
        "p",
        record.outcome.body.split("\n").slice(1).join("\n"),
      );
      outcome.dataset.intakeOutcome = record.outcome.stage;
      card.append(outcome);
    }
    for (const original of record.value.original_projections || [])
      card.append(
        el(
          "p",
          original.state === "projected"
            ? phrase("{p0} · 原件副本已保存 · {p1}", {
                p0: original.original_name,
                p1: original.path,
              })
            : uiText("原件已归档，目录副本待核对"),
          "muted",
        ),
      );
    if (
      ["manual", "needs_review", "archiving", "waiting"].includes(record.state)
    ) {
      const choice = checkbox("选择此批次（待确认 / 明确恢复）", false);
      choice.input.addEventListener("change", () =>
        choice.input.checked
          ? selected.add(record.batch_id)
          : selected.delete(record.batch_id),
      );
      selectables.push(choice.input);
      card.append(choice.label);
    }
    if (["queued", "waiting"].includes(record.state))
      card.append(
        control("停止此批次的自动处理", async () => {
          await api.channelIntakeCancel(identity, {
            expected_revision: snapshot.revision,
            batch_ids: [record.batch_id],
          });
          await reload();
        }),
      );
    if (record.state === "stored") {
      if (record.value.cards?.length)
        card.append(
          el(
            "p",
            uiText("视频号卡片已归档；正文 / 字幕待补全，不会自动转录。"),
            "muted",
          ),
        );
      card.append(
        el(
          "p",
          uiText(
            "已保存收到的消息/附件；远端正文、视频及笔记是否已取得，请以任务结果为准。",
          ),
          "muted",
        ),
        control("查看本批原件", async () => {
          const result = await api.channelIntakeOriginals(
              identity,
              record.batch_id,
            ),
            list = el("div");
          for (const asset of result.assets) {
            const link = el(
              "a",
              phrase("取回原件：{p0}", { p0: asset.original_name }),
            );
            link.href = api.channelIntakeOriginalURL(
              identity,
              record.batch_id,
              asset.asset_id,
            );
            link.download = asset.original_name;
            list.append(link, el("br"));
          }
          card.querySelector(".intake-originals")?.remove();
          list.className = "intake-originals";
          card.append(list);
        }),
      );
      if (!record.value.links?.length)
        card.append(
          el(
            "p",
            record.value.automatic_imports?.some(
              (item) => item.kind === "chat_archive",
            )
              ? uiText(
                  "聊天记录按原文生成笔记并引用已有附件，处理进度可在收件箱查看。",
                )
              : record.value.automatic_imports?.length
                ? uiText(
                    "已按资料类型进入处理队列；成功后按本库人工／自动入库策略继续，失败可恢复。",
                  )
                : uiText(
                    "原件已保留；启用本库处理策略后，新资料按类型提取。历史资料需明确选择处理。",
                  ),
            "muted",
          ),
        );
      for (const [index, link] of (record.value.links || []).entries()) {
        const line = el("div", null, "intake-link");
        line.append(
          el("p", link.original_url),
          el(
            "p",
            link.publication?.state === "published"
              ? uiText("已保存入库")
              : joinText([
                  "",
                  link.state === "needs_review"
                    ? uiText("处理失败，可恢复")
                    : uiText(intakeLabel(link.state || "link_only")),
                  " · ",
                  link.error_message || intakeLabel(link.reason),
                  "",
                ]),
          ),
        );
        if (link.video_processing)
          line.append(
            el(
              "p",
              phrase("视频任务 {p0} · {p1}", {
                p0: link.video_processing.job_id,
                p1: uiText(intakePhase(link.video_processing.phase)),
              }),
              "muted",
            ),
          );
        if (link.video_processing?.transcript_route === "visual_primary")
          line.append(
            el(
              "p",
              uiText("画面主导提取：依据关键帧文字与图表，语音内容未核验。"),
              "muted",
            ),
          );
        if (record.value.extraction === "off")
          line.append(
            el(
              "p",
              uiText(
                "尚未开启正文提取。请在上方勾选文章正文及原图提取并保存策略，再明确重试此链接；历史记录不会自动补跑。",
              ),
              "muted",
            ),
          );
        if (link.receipt)
          line.append(
            control("核对正文 / 图片 / 字幕并审核", async () =>
              openIntakeReview(line, api, identity, record.batch_id, index),
            ),
          );
        if (
          ["needs_review", "extracting", "link_only"].includes(
            link.state || "link_only",
          )
        )
          line.append(
            control("明确重试此链接的提取", async () => {
              const current = await api.channelIntake(identity);
              const article =
                /^https?:\/\/(?:mp\.weixin\.qq\.com|(?:[^/]+\.)?x\.com|(?:[^/]+\.)?twitter\.com)\//i.test(
                  link.original_url,
                );
              if (
                !(article
                  ? current.settings.fetch_articles
                  : current.settings.fetch_subtitles)
              )
                throw new Error(
                  channelErrorText(
                    article
                      ? "intake_article_extraction_disabled"
                      : "intake_subtitle_extraction_disabled",
                  ),
                );
              const newAttempt =
                link.requires_new_model_attempt ??
                [
                  "failed",
                  "interrupted",
                  "cancelled",
                  "waiting_budget",
                ].includes(link.pi_processing?.state);
              if (
                newAttempt &&
                !(await confirmAction({
                  title: "重新执行失败的 Pi 片段？",
                  body: [
                    "旧调用记录和预算预留会保留，用量未知的调用不会退款。",
                    "确认后创建新的处理尝试，可能再次消耗 Tokens；已完成片段继续复用。",
                  ],
                  confirmLabel: "确认新增处理尝试",
                }))
              )
                return;
              await api.channelIntakeRetry(
                identity,
                record.batch_id,
                index,
                snapshot.revision,
                newAttempt,
              );
              await reload();
            }),
          );
        card.append(line);
      }
      if (record.value.receipt_state) {
        const stage =
          record.value.receipt_updates?.[record.value.receipt_event]?.stage;
        const label = {
          received: "接收确认",
          saved: "入库结果",
          failed: "处理失败结果",
          index_failed: "索引更新结果",
          awaiting_review: "待审核结果",
          waiting_budget: "预算暂停结果",
          waiting_dependency: "环境等待结果",
          cancelled: "取消结果",
        }[stage];
        card.append(
          el(
            "p",
            `${label ? label + " · " : ""}${intakeLabel(record.value.receipt_state)}`,
          ),
        );
      }
      if (["unknown", "sending"].includes(record.value.receipt_state)) {
        card.append(
          el(
            "p",
            uiText("请先暂停入口并核对手机；以下按钮只记录结果，不会重发。"),
          ),
        );
        for (const [label, state] of [
          ["手机已收到回执", "sent"],
          ["手机确认未收到回执", "not_sent"],
        ])
          card.append(
            control(label, async () => {
              await api.channelIntakeReceiptAck(
                identity,
                record.batch_id,
                state,
              );
              await reload();
            }),
          );
      }
    }
    showJSON(card, record, "入库诊断详情");
    root.append(card);
  }
  root.append(
    el("h4", uiText("历史待确认批次（最近 50 条）")),
    el("p", uiText("仅明确选择的批次会进入处理；不会自动补跑历史。")),
  );
  for (const row of history.values()) {
    const choice = checkbox(
      `选择历史批次 ${row.batch_id} · ${row.last_received}`,
      false,
    );
    choice.input.addEventListener("change", () =>
      choice.input.checked
        ? selected.add(row.batch_id)
        : selected.delete(row.batch_id),
    );
    selectables.push(choice.input);
    root.append(choice.label);
  }
  const actions = el("div", null, "panel-actions");
  for (const [label, checked] of [
    ["全选待确认批次", true],
    ["清空批次选择", false],
  ])
    actions.append(
      control(label, async () => {
        for (const node of selectables) {
          node.checked = checked;
          node.dispatchEvent(new window.Event("change", { bubbles: true }));
        }
      }),
    );
  actions.append(
    control("处理所选批次 / 明确恢复", async (notify) => {
      if (!selected.size) {
        notify("请先选择批次。");
        return;
      }
      if (selected.size > 20) {
        notify("每次最多处理 20 个所选批次。");
        return;
      }
      const acknowledge = selectables.map((node) => captureDraftSave(node));
      await api.channelIntakeBatches(identity, {
        expected_revision: snapshot.revision,
        batch_ids: [...selected],
      });
      for (const saved of acknowledge) saved();
      await reload();
      notify("所选批次已提交；后台按当前授权执行，仅模型调用受统一预算控制。");
    }),
  );
  root.append(actions);
  rememberDraft(root);
}
