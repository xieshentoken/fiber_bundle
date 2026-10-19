import { phrase, uiText } from "./i18n/index.js";
// Literal provenance and frame evidence, separate from assessment and publication actions.
import { el } from "./ui.js";

function platformUrl(value) {
  if (typeof value !== "string") return null;
  return /^https:\/\/www\.bilibili\.com\/video\/BV[A-Za-z0-9]{10}$/.test(
    value,
  ) || /^https:\/\/www\.youtube\.com\/watch\?v=[A-Za-z0-9_-]{11}$/.test(value)
    ? value
    : null;
}

export function sourceEvidence(context, candidate) {
  const root = el("section", null, "media-provenance");
  root.append(el("h4", uiText("本次审核的证据绑定")));
  if (context) {
    root.append(
      el(
        "p",
        phrase("本地原件：{p0} · {p1} 字节 · {p2} ms", {
          p0: context.media_type,
          p1: context.source_bytes,
          p2: context.duration_ms,
        }),
      ),
    );
    const platform = context.platform;
    if (platform) {
      const url = platformUrl(platform.url);
      if (url) {
        const link = el("a", uiText("打开平台来源核对完整播放"));
        link.href = url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        root.append(link, el("p", url));
      }
      root.append(
        el(
          "p",
          phrase("平台声明时长：{p0} ms", {
            p0: platform.declared_duration_ms,
          }),
        ),
        el(
          "p",
          platform.converted_duration_ms === null
            ? uiText("旧获取记录未单独测量转换文件时长；本地时长见上方。")
            : phrase("转换文件测量时长：{p0} ms", {
                p0: platform.converted_duration_ms,
              }),
        ),
        el("p", phrase("获取方式：{p0}", { p0: platform.access_state })),
        el("p", uiText("获取成功不代表完整平台覆盖；登录与完整播放仍需核对。")),
      );
    } else root.append(el("p", uiText("直接上传原件；没有绑定的平台来源。")));
  } else root.append(el("p", uiText("后台未提供来源信息，请刷新后核对原件。")));
  root.append(
    el("p", phrase("原件 SHA-256：{p0}", { p0: candidate.source_sha256 })),
    el("p", phrase("处理结果 SHA-256：{p0}", { p0: candidate.result_sha256 })),
    el(
      "p",
      phrase("术语修订：{p0} · SHA-256：{p1}", {
        p0: candidate.glossary_revision,
        p1: candidate.glossary_sha256,
      }),
    ),
    el("p", uiText("结构处理完成和术语确认记录不能替代本人语义及画面审核。")),
  );
  return root;
}

export function frameEvidence(jobId, unit, frames) {
  const frame = frames.find(
    (frame) =>
      frame.sha256 === unit.locator.sha256 &&
      frame.timestamp_ms === unit.locator.timestamp_ms,
  );
  if (!frame) return null;
  const figure = el("figure", null, "media-frame");
  const image = el("img");
  image.src =
    "/admin/media/" +
    encodeURIComponent(jobId) +
    "/frames/" +
    encodeURIComponent(frame.sha256);
  image.alt = "源视频抽样帧 " + frame.timestamp_ms + " ms";
  image.loading = "lazy";
  figure.append(
    image,
    el(
      "figcaption",
      phrase("抽样时间：{p0} ms · SHA-256：{p1}", {
        p0: frame.timestamp_ms,
        p1: frame.sha256,
      }),
    ),
  );
  return figure;
}
