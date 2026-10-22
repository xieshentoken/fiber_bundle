// Metadata only: do not download, resolve a conflict, or read file contents.
import Foundation
import Darwin

enum CloudDownloadState: String { case available, pending, stale, invalid }

func cloudDownloadState(ubiquitous: Bool, status: String?) -> CloudDownloadState {
    if !ubiquitous { return .available }
    // Missing metadata is not proof of a current local version, including directories.
    guard let status = status else { return .invalid }
    if status == URLUbiquitousItemDownloadingStatus.notDownloaded.rawValue { return .pending }
    return status == URLUbiquitousItemDownloadingStatus.current.rawValue ? .available : .stale
}

guard setiopolicy_np(IOPOL_TYPE_VFS_MATERIALIZE_DATALESS_FILES,
    IOPOL_SCOPE_THREAD, IOPOL_MATERIALIZE_DATALESS_FILES_OFF) == 0 else { exit(2) }

let arguments = CommandLine.arguments
guard [3, 4].contains(arguments.count), ["--tree", "--file", "--tree-excluding", "--items"].contains(arguments[1]),
      arguments[2].hasPrefix("/") else { exit(2) }
var excluded: [String] = []
if arguments[1] == "--tree-excluding" {
    guard arguments.count == 4,
        let data = arguments[3].data(using: .utf8),
        let parsed = try? JSONSerialization.jsonObject(with: data) as? [String],
        parsed.count <= 100,
        parsed.allSatisfy({ !$0.isEmpty && !$0.hasPrefix("/") && !$0.contains("\\") &&
            !$0.split(separator: "/", omittingEmptySubsequences: false).contains(where: { $0.isEmpty || $0 == "." || $0 == ".." }) }) else { exit(2) }
    excluded = parsed
} else if arguments.count != 3 { exit(2) }
let root = URL(fileURLWithPath: arguments[2])
let keys: Set<URLResourceKey> = [.isDirectoryKey, .isSymbolicLinkKey,
    .isUbiquitousItemKey, .ubiquitousItemDownloadingStatusKey,
    .ubiquitousItemHasUnresolvedConflictsKey]
var stack = [root]
if arguments[1] == "--items" {
    let data = FileHandle.standardInput.readDataToEndOfFile()
    guard data.count <= 262144,
        let names = try? JSONSerialization.jsonObject(with: data) as? [String],
        !names.isEmpty && names.count <= 256,
        names.allSatisfy({ !$0.isEmpty && $0 != "." && $0 != ".." && !$0.contains("/") && !$0.contains("\\") && !$0.contains("\0") }) else { exit(2) }
    stack = names.map { root.appendingPathComponent($0) }
}
var entries = 0, pending = 0, stale = 0, conflicts = 0, errors = 0, excludedPresent = 0
var metadataUnavailable = 0
while let url = stack.popLast() {
    let relative = url.path == root.path ? "" : String(url.path.dropFirst(root.path.count + 1))
    if excluded.contains(where: { relative == $0 || relative.hasPrefix($0 + "/") }) {
        excludedPresent += 1
        continue
    }
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
        switch cloudDownloadState(ubiquitous: value.isUbiquitousItem == true,
                status: value.ubiquitousItemDownloadingStatus?.rawValue) {
        case .available: break
        case .pending: pending += 1; continue
        case .stale: stale += 1; continue
        case .invalid: errors += 1; metadataUnavailable += 1; continue
        }
        if ["--tree", "--tree-excluding"].contains(arguments[1]) && value.isDirectory == true {
            let children = try FileManager.default.contentsOfDirectory(at: url,
                includingPropertiesForKeys: nil, options: [])
            if entries + stack.count + children.count > 20000 { errors += 1; break }
            stack.append(contentsOf: children)
        }
    } catch { errors += 1 }
}
let data: [String: Any] = ["schema": "cloud-status/v1",
    "available": pending == 0 && stale == 0 && conflicts == 0 && errors == 0 && excludedPresent == 0,
    "entries": entries, "pending": pending, "stale": stale,
    "conflicts": conflicts, "errors": errors, "excluded_present": excludedPresent,
    "metadata_unavailable": metadataUnavailable]
if let output = try? JSONSerialization.data(withJSONObject: data, options: [.sortedKeys]) {
    FileHandle.standardOutput.write(output)
} else { exit(2) }
