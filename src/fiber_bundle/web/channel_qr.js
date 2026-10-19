// Session-bound private PNG, never persisted in browser storage or opened externally.

export async function channelQr(
  fetcher,
  session,
  onUnauthorized,
  id,
  ApiError,
) {
  const revision = session.revision,
    controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 10000);
  try {
    const response = await fetcher(
      `/admin/channels/${encodeURIComponent(id)}/qr`,
      { credentials: "same-origin", signal: controller.signal },
    );
    if (revision !== session.revision)
      throw new ApiError("会话已变化，请刷新。", 409);
    if (response.status === 401) {
      onUnauthorized();
      throw new ApiError("会话已失效，请重新登录。", 401);
    }
    if (
      !response.ok ||
      !response.headers.get("content-type")?.startsWith("image/png")
    )
      throw new ApiError("二维码尚未生成或登录已结束，请稍后刷新。");
    const blob = await response.blob();
    if (blob.size > 262144) throw new ApiError("二维码超出大小限制。");
    const result = await new Promise((resolve, reject) => {
      const reader = new window.FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = () => reject(new ApiError("二维码读取失败。"));
      reader.readAsDataURL(blob);
    });
    if (revision !== session.revision)
      throw new ApiError("会话已变化，请刷新。", 409);
    return result;
  } finally {
    clearTimeout(timer);
  }
}
