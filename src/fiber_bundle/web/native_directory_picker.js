// Bounded native selection. Late results never overwrite manual edits or another view.
import { locale } from "./i18n/index.js";
const text = (zh, en) => (locale() === "en" ? en : zh);
export const DIRECTORY_TIMEOUT = 120000;
export async function chooseDirectory(
  currentPath = "",
  {
    select = window.fiberDesktop?.selectDirectory,
    timeout = DIRECTORY_TIMEOUT,
    schedule = setTimeout,
    cancel = clearTimeout,
  } = {},
) {
  if (!select)
    throw new Error(
      text(
        "请直接填写本机文件夹的绝对路径。",
        "Enter the absolute folder path on this Mac.",
      ),
    );
  let timer;
  try {
    const result = await Promise.race([
      Promise.resolve().then(() => select(currentPath)),
      new Promise((_, reject) => {
        timer = schedule(
          () => reject(new Error("directory_picker_timeout")),
          timeout,
        );
      }),
    ]);
    if (result === null) return null;
    if (
      typeof result !== "string" ||
      !result.startsWith("/") ||
      result.length > 4096 ||
      result.includes("\0")
    )
      throw new Error("directory_picker_invalid_result");
    return result;
  } catch (error) {
    const code = error?.message || "";
    if (code.includes("directory_picker_timeout"))
      throw new Error(
        text(
          "文件夹选择超时。请取消系统选择窗口后重试，或直接输入绝对路径；设置尚未保存。",
          "Folder selection timed out. Cancel the system chooser and retry, or enter an absolute path. Settings were not saved.",
        ),
        { cause: error },
      );
    if (code.includes("directory_picker_busy"))
      throw new Error(
        text(
          "已有文件夹选择窗口，请先完成或取消；也可直接输入绝对路径。",
          "A folder chooser is already open. Complete or cancel it first, or enter an absolute path.",
        ),
        { cause: error },
      );
    throw new Error(
      text(
        "无法完成文件夹选择。请重试或直接输入绝对路径；原有输入已保留。",
        "Could not select a folder. Retry or enter an absolute path. Your input was kept.",
      ),
      { cause: error },
    );
  } finally {
    if (timer !== undefined) cancel(timer);
  }
}
export async function fillDirectory(input, options) {
  const previous = input.value;
  const selected = await chooseDirectory(previous, options);
  if (
    !selected ||
    !input.isConnected ||
    input.closest("[hidden]") ||
    input.value !== previous
  )
    return false;
  input.value = selected;
  for (const name of ["input", "change"])
    input.dispatchEvent(
      new input.ownerDocument.defaultView.Event(name, { bubbles: true }),
    );
  return true;
}
