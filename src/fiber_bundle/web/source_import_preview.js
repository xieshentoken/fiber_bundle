export function previewStatus(
  ext,
  policy,
  engine = "ordinary",
  projectSkill = null,
) {
  if (
    engine === "fake-expert" &&
    ext === "pdf" &&
    projectSkill?.settings.enabled
  )
    return "使用项目 fake-expert + Pi；新任务冻结当前兼容版本，原生文本资格仍须核对";
  if (engine === "fake-expert")
    return ext === "pdf" && policy.expert_runtime
      ? "完整 Skill 已配置，语义材料和运行条件仍须核对"
      : "可保存原件，完整 Skill 能力未就绪";
  if (
    [
      "png",
      "jpg",
      "jpeg",
      "webp",
      "bmp",
      "gif",
      "tif",
      "tiff",
      "heic",
      "heif",
    ].includes(ext)
  )
    return policy.ocr_runtime
      ? "图片 OCR 已配置，须显式选择 OCR 模式"
      : "可保存原件，图片 OCR 未就绪";
  if (ext === "zip")
    return "普通 ZIP 须识别为微信文本导出后处理；未知格式保存原件，不作为专家 workpack。";
  if (ext === "doc")
    return policy.conversion_runtime
      ? "DOC 转换已配置，须显式选择转换模式"
      : "可保存原件，DOC 转换未就绪";
  return ["md", "markdown", "txt", "pdf", "docx", "xlsx", "pptx"].includes(
    ext,
  ) && policy.document_image
    ? "普通提取 Worker 已配置，运行待核对"
    : "可保存原件，提取能力未就绪";
}
