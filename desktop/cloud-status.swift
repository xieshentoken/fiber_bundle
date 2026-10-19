// Metadata only: do not download, resolve a conflict, or read file contents.
import Foundation
import Darwin

guard setiopolicy_np(IOPOL_TYPE_VFS_MATERIALIZE_DATALESS_FILES,
    IOPOL_SCOPE_THREAD, IOPOL_MATERIALIZE_DATALESS_FILES_OFF) == 0 else { exit(2) }

let arguments = CommandLine.arguments
guard arguments.count == 3, ["--tree", "--file"].contains(arguments[1]),
      arguments[2].hasPrefix("/") else { exit(2) }
let root = URL(fileURLWithPath: arguments[2])
let keys: Set<URLResourceKey> = [.isDirectoryKey, .isSymbolicLinkKey,
    .isUbiquitousItemKey, .ubiquitousItemDownloadingStatusKey,
    .ubiquitousItemHasUnresolvedConflictsKey]
var stack = [root]
var entries = 0, pending = 0, stale = 0, conflicts = 0, errors = 0
while let url = stack.popLast() {
    entries += 1
    if entries > 20000 { errors += 1; break }
    var info = stat()
    if lstat(url.path, &info) != 0 { errors += 1; continue }
    if (info.st_flags & UInt32(SF_DATALESS)) != 0 || url.pathExtension == "icloud" {
        pending += 1; continue
    }
    do {
        let value = try url.resourceValues(forKeys: keys)
        if value.isSymbolicLink == true { errors += 1; continue }
        if value.ubiquitousItemHasUnresolvedConflicts == true ||
            !(NSFileVersion.unresolvedConflictVersionsOfItem(at: url) ?? []).isEmpty {
            conflicts += 1; continue
        }
        if value.isUbiquitousItem == true {
            guard let state = value.ubiquitousItemDownloadingStatus else {
                errors += 1; continue
            }
            if state == .notDownloaded { pending += 1; continue }
            if state != .current { stale += 1; continue }
        }
        if arguments[1] == "--tree" && value.isDirectory == true {
            let children = try FileManager.default.contentsOfDirectory(at: url,
                includingPropertiesForKeys: nil, options: [])
            if entries + stack.count + children.count > 20000 { errors += 1; break }
            stack.append(contentsOf: children)
        }
    } catch { errors += 1 }
}
let data: [String: Any] = ["schema": "cloud-status/v1",
    "available": pending == 0 && stale == 0 && conflicts == 0 && errors == 0,
    "entries": entries, "pending": pending, "stale": stale,
    "conflicts": conflicts, "errors": errors]
if let output = try? JSONSerialization.data(withJSONObject: data, options: [.sortedKeys]) {
    FileHandle.standardOutput.write(output)
} else { exit(2) }
