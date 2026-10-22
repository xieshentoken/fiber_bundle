// Generated object identities and current authenticated backend evidence only.
import { lstat, realpath, open } from "node:fs/promises";
import { constants } from "node:fs";
import { createHash } from "node:crypto";
const uuid = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/;
const note = /^(note|publication):[a-f0-9-]{36}$/;
export function trusted(event, window, backend) {
  if (
    !window ||
    event.sender !== window.webContents ||
    event.senderFrame !== window.webContents.mainFrame ||
    event.senderFrame.url.split("#")[0] !== backend.split("#")[0]
  )
    throw new Error("Untrusted native request");
}
async function request(window, url, options = {}) {
  const session = window.webContents.session;
  const cookies = await session.cookies.get({ url });
  const response = await session.fetch(url, {
    ...options,
    redirect: "error",
    headers: {
      ...options.headers,
      Cookie: cookies.map((c) => `${c.name}=${c.value}`).join("; "),
    },
  });
  if (!response.ok) {
    let code = "request_denied";
    try {
      const body = await response.json();
      if (
        typeof body?.error === "string" &&
        /^[a-z][a-z0-9_]{0,95}$/.test(body.error)
      )
        code = body.error;
    } catch {
      /* Unknown remote content is never forwarded. */
    }
    const error = new Error("本机操作未完成，请刷新后核对状态。");
    error.nativeStatus = response.status;
    error.nativeCode = code;
    throw error;
  }
  return response.json();
}
export async function openNote(window, backend, value, shell) {
  if (
    !value ||
    Object.keys(value).sort().join() !== "clientId,noteId,vaultId" ||
    !uuid.test(value.clientId) ||
    !uuid.test(value.vaultId) ||
    !note.test(value.noteId)
  )
    throw new Error("Invalid note identity");
  const url =
    backend +
    "admin/knowledge-graph/node?" +
    new URLSearchParams({
      client_id: value.clientId,
      vault_id: value.vaultId,
      note_id: value.noteId,
    });
  const before = await request(window, url),
    path = before.absolute_path;
  if (
    typeof path !== "string" ||
    !path.startsWith("/") ||
    !path.endsWith(".md") ||
    (await realpath(path)) !== path
  )
    throw new Error("Invalid current note path");
  const info = await lstat(path);
  if (
    !info.isFile() ||
    info.isSymbolicLink() ||
    info.nlink !== 1 ||
    info.size > 1048576
  )
    throw new Error("Note changed");
  const descriptor = await open(
    path,
    constants.O_RDONLY | constants.O_NOFOLLOW,
  );
  let content;
  try {
    const opened = await descriptor.stat();
    if (
      opened.dev !== info.dev ||
      opened.ino !== info.ino ||
      !opened.isFile() ||
      opened.nlink !== 1
    )
      throw new Error("Note changed");
    content = await descriptor.readFile();
  } finally {
    await descriptor.close();
  }
  if (createHash("sha256").update(content).digest("hex") !== before.sha256)
    throw new Error("Note changed");
  const after = await request(window, url);
  if (after.absolute_path !== path || after.sha256 !== before.sha256)
    throw new Error("Note changed");
  const final = await lstat(path);
  if (
    final.dev !== info.dev ||
    final.ino !== info.ino ||
    final.mtimeMs !== info.mtimeMs ||
    (await realpath(path)) !== path
  )
    throw new Error("Note changed");
  const error = await shell.openPath(path);
  if (error) throw new Error("无法打开本机笔记，请核对默认应用。");
  return { opened: true };
}
export async function trashIntent(window, backend, value) {
  if (
    !value ||
    Object.keys(value).sort().join() !== "confirmedRoot,csrf,id" ||
    !uuid.test(value.id) ||
    typeof value.confirmedRoot !== "string" ||
    !value.confirmedRoot.startsWith("/") ||
    value.confirmedRoot.length > 4096 ||
    value.confirmedRoot.includes("\0") ||
    typeof value.csrf !== "string" ||
    value.csrf.length > 256
  )
    throw new Error("Invalid reviewed Trash intent");
  try {
    return await request(
      window,
      backend + `admin/vault-catalog/trash/${value.id}/confirm`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": value.csrf,
          Origin: new URL(backend).origin,
        },
        body: JSON.stringify({ confirmed_root: value.confirmedRoot }),
      },
    );
  } catch (error) {
    if (Number.isInteger(error.nativeStatus))
      return {
        native_error: { status: error.nativeStatus, code: error.nativeCode },
      };
    throw error;
  }
}
