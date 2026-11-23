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

func localDirectoryReady(directory: Bool, status: String?) -> Bool {
    // A directory's download version describes its synchronized container, not
    // every child. Only the known local-copy state is accepted for traversal.
    directory && status == URLUbiquitousItemDownloadingStatus.downloaded.rawValue
}

final class CloudStatusCounts: @unchecked Sendable {
    let lock = NSLock()
    var pending = 0, stale = 0, conflicts = 0, errors = 0, unknown = 0
    var failures: [[String: String]] = []
    func record(_ counts: [Int], name: String? = nil) {
        lock.lock(); defer { lock.unlock() }
        if let name = name, counts.contains(where: { $0 > 0 }), failures.count < 4 {
            let reason = counts[2] > 0 ? "conflict" : counts[0] > 0 ? "pending" :
                counts[1] > 0 ? "stale" : counts[4] > 0 ? "metadata_unavailable" : "io_error"
            failures.append(["name": name, "reason": reason])
        }
        pending += counts[0]; stale += counts[1]; conflicts += counts[2]
        errors += counts[3]; unknown += counts[4]
    }
}

func metadataCounts(_ url: URL, directory: Bool = false) -> [Int] {
    var info = stat()
    guard lstat(url.path, &info) == 0 else { return [0, 0, 0, 1, 0] }
    if (info.st_flags & UInt32(SF_DATALESS)) != 0 || url.pathExtension == "icloud" {
        return [1, 0, 0, 0, 0]
    }
    if directory && (info.st_mode & S_IFMT) != S_IFDIR { return [0, 0, 0, 1, 0] }
    let keys: Set<URLResourceKey> = [.isDirectoryKey, .isSymbolicLinkKey, .isUbiquitousItemKey,
        .ubiquitousItemDownloadingStatusKey, .ubiquitousItemHasUnresolvedConflictsKey]
    do {
        let value = try url.resourceValues(forKeys: keys)
        if value.isSymbolicLink == true { return [0, 0, 0, 1, 0] }
        if value.ubiquitousItemHasUnresolvedConflicts == true ||
            !(NSFileVersion.unresolvedConflictVersionsOfItem(at: url) ?? []).isEmpty {
            return [0, 0, 1, 0, 0]
        }
        switch cloudDownloadState(ubiquitous: value.isUbiquitousItem == true,
            status: value.ubiquitousItemDownloadingStatus?.rawValue) {
        case .available: return [0, 0, 0, 0, 0]
        case .pending: return [1, 0, 0, 0, 0]
        case .stale:
            return directory && localDirectoryReady(directory: value.isDirectory == true,
                status: value.ubiquitousItemDownloadingStatus?.rawValue) ? [0, 0, 0, 0, 0] : [0, 1, 0, 0, 0]
        case .invalid: return [0, 0, 0, 1, 1]
        }
    } catch { return [0, 0, 0, 1, 0] }
}

// Bounded request frames; no observations or resource values survive a request.
func serveMetadata() -> Never {
    var buffer = Data()
    while true {
        let chunk = FileHandle.standardInput.availableData
        if chunk.isEmpty { exit(buffer.isEmpty ? 0 : 2) }
        buffer.append(chunk)
        if buffer.count > 262144 { exit(2) }
        while let end = buffer.firstIndex(of: 10) {
            let frame = buffer.prefix(upTo: end)
            buffer.removeSubrange(...end)
            autoreleasepool {
                guard let request = try? JSONSerialization.jsonObject(with: frame) as? [String: Any],
                    Set(request.keys) == Set(["mode", "path", "names"]),
                    let mode = request["mode"] as? String,
                    ["--file", "--directory", "--items"].contains(mode),
                    let path = request["path"] as? String, path.hasPrefix("/"), !path.contains("\0"),
                    let names = request["names"] as? [String], names.count <= 256,
                    mode == "--items" ? !names.isEmpty : names.isEmpty,
                    names.allSatisfy({ !$0.isEmpty && $0 != "." && $0 != ".." &&
                        !$0.contains("/") && !$0.contains("\\") && !$0.contains("\0") }) else { exit(2) }
                let root = URL(fileURLWithPath: path)
                let urls = mode == "--items" ? names.map { root.appendingPathComponent($0) } : [root]
                let counts = CloudStatusCounts()
                DispatchQueue.concurrentPerform(iterations: min(8, urls.count)) { worker in
                    guard setiopolicy_np(IOPOL_TYPE_VFS_MATERIALIZE_DATALESS_FILES,
                        IOPOL_SCOPE_THREAD, IOPOL_MATERIALIZE_DATALESS_FILES_OFF) == 0 else {
                        counts.record([0, 0, 0, 1, 0]); return
                    }
                    for index in stride(from: worker, to: urls.count, by: min(8, urls.count)) {
                        counts.record(metadataCounts(urls[index], directory: mode == "--directory"),
                            name: mode == "--items" ? urls[index].lastPathComponent : nil)
                    }
                }
                let value: [String: Any] = ["schema": "cloud-status/v1",
                    "available": counts.pending + counts.stale + counts.conflicts + counts.errors == 0,
                    "entries": urls.count, "pending": counts.pending, "stale": counts.stale,
                    "conflicts": counts.conflicts, "errors": counts.errors, "excluded_present": 0,
                    "metadata_unavailable": counts.unknown, "failures": counts.failures]
                guard var data = try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys])
                    else { exit(2) }
                data.append(10)
                FileHandle.standardOutput.write(data)
            }
        }
    }
}

// A single metadata process for one fingerprint. Scope is applied before lstat,
// hidden-name filtering matches the Python walker, and no content is opened.
func emitNoteInventory(root: URL) -> Never {
    let input = FileHandle.standardInput.readDataToEndOfFile()
    guard input.count <= 524288,
          let scope = try? JSONSerialization.jsonObject(with: input) as? [String: Any],
          Set(scope.keys) == Set(["include", "exclude", "policy_sha256"]),
          let includes = scope["include"] as? [String], !includes.isEmpty, includes.count <= 100,
          let excludes = scope["exclude"] as? [String], excludes.count <= 100 else { exit(2) }
    func validPrefix(_ value: String) -> Bool {
        return value.utf8.count <= 8192 && !value.hasPrefix("/") && !value.contains("\\") &&
            !value.contains("\0") && (value.isEmpty ||
                !value.split(separator: "/", omittingEmptySubsequences: false).contains {
                    $0.isEmpty || $0 == "." || $0 == ".."
                })
    }
    guard (includes + excludes).allSatisfy(validPrefix) else { exit(2) }
    func inside(_ path: String, _ prefix: String) -> Bool {
        prefix.isEmpty || path.utf8.elementsEqual(prefix.utf8) ||
            path.utf8.starts(with: Array(prefix.utf8) + [47])
    }
    func reachable(_ path: String) -> Bool {
        !excludes.contains { inside(path, $0) } &&
            includes.contains { inside(path, $0) || inside($0, path) }
    }
    func signature(_ info: stat) -> [Int64] {
        [Int64(info.st_dev), Int64(info.st_ino), Int64(info.st_mode), Int64(info.st_nlink),
         info.st_size, Int64(info.st_mtimespec.tv_sec) * 1_000_000_000 + Int64(info.st_mtimespec.tv_nsec),
         Int64(info.st_ctimespec.tv_sec) * 1_000_000_000 + Int64(info.st_ctimespec.tv_nsec)]
    }
    let wanted = Set([".fiber-publication.json", ".fiber-analysis.json"])
    let keys: Set<URLResourceKey> = [.isUbiquitousItemKey, .ubiquitousItemDownloadingStatusKey,
        .ubiquitousItemHasUnresolvedConflictsKey]
    var stack: [(URL, String, Int)] = [(root, "", 0)]
    var rows: [[Any]] = [], rootSignature: [Int64] = []
    var inspected: [(String, [Int64])] = []
    var fileChecks: [(URL, [Int64])] = []
    var names = 0, entries = 0, pending = 0, stale = 0, conflicts = 0, errors = 0, unknown = 0
    while let (url, relative, level) = stack.popLast() {
        // Root admission was checked by Python before invoking the helper.
        if !relative.isEmpty && !reachable(relative) { continue }
        var info = stat()
        if lstat(url.path, &info) != 0 { errors += 1; break }
        let directory = (info.st_mode & S_IFMT) == S_IFDIR
        if (info.st_mode & S_IFMT) == S_IFLNK {
            if relative.isEmpty { errors += 1; break }
            continue  // The established fingerprint walker ignores child symlinks.
        }
        if !directory && !url.lastPathComponent.lowercased().hasSuffix(".md") &&
            !wanted.contains(url.lastPathComponent) { continue }
        entries += 1
        if entries > 20000 || (directory && level > 32) { errors += 1; break }
        if (info.st_flags & UInt32(SF_DATALESS)) != 0 || url.pathExtension == "icloud" {
            pending += 1; break
        }
        if !directory {
            let observed = signature(info)
            rows.append([relative] + observed.map { $0 as Any })
            inspected.append((url.path, observed))
            fileChecks.append((url, observed))
            continue
        }
        do {
            let value = try url.resourceValues(forKeys: keys)
            if value.ubiquitousItemHasUnresolvedConflicts == true ||
                !(NSFileVersion.unresolvedConflictVersionsOfItem(at: url) ?? []).isEmpty {
                conflicts += 1; break
            }
            switch cloudDownloadState(ubiquitous: value.isUbiquitousItem == true,
                status: value.ubiquitousItemDownloadingStatus?.rawValue) {
            case .available: break
            case .pending: pending += 1
            case .stale: stale += 1
            case .invalid: errors += 1; unknown += 1
            }
            if pending + stale + errors > 0 { break }
            let observed = signature(info)
            inspected.append((url.path, observed))
            if relative.isEmpty { rootSignature = observed }
            if directory {
                let children = try FileManager.default.contentsOfDirectory(at: url,
                    includingPropertiesForKeys: nil, options: [])
                var admitted: [(URL, String, Int)] = []
                for child in children {
                    let name = child.lastPathComponent
                    let path = relative.isEmpty ? name : relative + "/" + name
                    if !reachable(path) || (name.hasPrefix(".") && !wanted.contains(name)) { continue }
                    names += 1
                    if names > 10000 { errors += 1; break }
                    admitted.append((child, path, level + 1))
                }
                if errors > 0 { break }
                // UTF-8 lexicographic order preserves Python's code-point path order.
                admitted.sort { $0.1.utf8.lexicographicallyPrecedes($1.1.utf8) }
                stack.append(contentsOf: admitted.reversed())
            } else {
                rows.append([relative] + observed.map { $0 as Any })
            }
        } catch { errors += 1; break }
    }
    // File metadata calls are independent. Keep every conflict/version check, with
    // at most eight local I/O workers; each worker explicitly denies materialization.
    if pending + stale + conflicts + errors == 0 {
        let failures = CloudStatusCounts()
        let files = fileChecks
        let workers = min(8, files.count)
        if workers > 0 {
            DispatchQueue.concurrentPerform(iterations: workers) { worker in
                guard setiopolicy_np(IOPOL_TYPE_VFS_MATERIALIZE_DATALESS_FILES,
                    IOPOL_SCOPE_THREAD, IOPOL_MATERIALIZE_DATALESS_FILES_OFF) == 0 else {
                    failures.record([0, 0, 0, 1, 0]); return
                }
                var counts = [0, 0, 0, 0, 0]
                for index in stride(from: worker, to: files.count, by: workers) {
                    let (url, observed) = files[index]
                    var info = stat()
                    guard lstat(url.path, &info) == 0, signature(info) == observed,
                        (info.st_flags & UInt32(SF_DATALESS)) == 0 else {
                        counts[3] += 1; continue
                    }
                    let result = metadataCounts(url)
                    for index in counts.indices { counts[index] += result[index] }
                }
                failures.record(counts)
            }
        }
        pending += failures.pending; stale += failures.stale; conflicts += failures.conflicts
        errors += failures.errors; unknown += failures.unknown
    }
    // Reject directory membership or file identity changes during the native walk.
    if pending + stale + conflicts + errors == 0 {
        for (path, before) in inspected {
            var info = stat()
            if lstat(path, &info) != 0 || signature(info) != before ||
                (info.st_flags & UInt32(SF_DATALESS)) != 0 { errors += 1; break }
        }
    }
    let status: [String: Any] = ["schema": "cloud-status/v1",
        "available": pending + stale + conflicts + errors == 0,
        "entries": entries, "pending": pending, "stale": stale, "conflicts": conflicts,
        "errors": errors, "metadata_unavailable": unknown, "excluded_present": 0]
    let report: [String: Any] = ["schema": "cloud-note-inventory/v1", "status": status,
        "root": rootSignature, "files": rows]
    guard let output = try? JSONSerialization.data(withJSONObject: report, options: [.sortedKeys]),
          output.count <= 8 * 1024 * 1024 else { exit(2) }
    FileHandle.standardOutput.write(output)
    exit(0)
}

// Only this explicit command may request hydration. All existing commands stay metadata-only.
func recoverFile(_ arguments: [String]) -> Never {
    let url = URL(fileURLWithPath: arguments[2])
    guard ["inspect", "download"].contains(arguments[3]),
          let device = Int64(arguments[4]), let inode = UInt64(arguments[5]) else { exit(2) }
    func sameFile() -> Bool {
        var info = stat()
        return lstat(url.path, &info) == 0 && (info.st_mode & S_IFMT) == S_IFREG &&
            info.st_nlink == 1 && Int64(info.st_dev) == device && info.st_ino == inode
    }
    guard sameFile() else { exit(2) }
    var report: [String: Any] = ["schema": "cloud-file-recovery/v1", "state": "unknown",
                               "requested_now": false]
    let keys: Set<URLResourceKey> = [.isUbiquitousItemKey, .ubiquitousItemDownloadingStatusKey,
        .ubiquitousItemDownloadRequestedKey, .ubiquitousItemIsDownloadingKey,
        .ubiquitousItemDownloadingErrorKey, .ubiquitousItemHasUnresolvedConflictsKey]
    do {
        let value = try url.resourceValues(forKeys: keys)
        var info = stat()
        guard sameFile(), lstat(url.path, &info) == 0 else { exit(2) }
        let dataless = (info.st_flags & UInt32(SF_DATALESS)) != 0
        let current = value.isUbiquitousItem == false ||
            (value.isUbiquitousItem == nil && !url.pathComponents.contains("Mobile Documents")) ||
            value.ubiquitousItemDownloadingStatus == .current
        let conflict = value.ubiquitousItemHasUnresolvedConflicts == true
        let error = value.ubiquitousItemDownloadingError
        let downloading = value.ubiquitousItemIsDownloading == true
        let requested = value.ubiquitousItemDownloadRequested == true
        var state = "unknown"
        if conflict { state = "conflict" }
        else if let error = error { state = "error"; report["error_code"] = error.code }
        else if current && !dataless { state = "available" }
        else if downloading { state = "downloading" }
        else if requested { state = "requested" }
        else if value.isUbiquitousItem == true && value.ubiquitousItemDownloadRequested == false {
            state = "not_requested"
        }
        if arguments[3] == "download" && value.isUbiquitousItem == true &&
            !conflict && state != "available" && !requested && !downloading {
            guard sameFile() else { exit(2) }
            try FileManager.default.startDownloadingUbiquitousItem(at: url)
            state = "requested"
            report["requested_now"] = true
        }
        report["state"] = state
    } catch {
        report["state"] = "error"
        report["error_code"] = (error as NSError).code
    }
    guard let data = try? JSONSerialization.data(withJSONObject: report, options: [.sortedKeys])
        else { exit(2) }
    FileHandle.standardOutput.write(data)
    exit(0)
}

guard setiopolicy_np(IOPOL_TYPE_VFS_MATERIALIZE_DATALESS_FILES,
    IOPOL_SCOPE_THREAD, IOPOL_MATERIALIZE_DATALESS_FILES_OFF) == 0 else { exit(2) }

let arguments = CommandLine.arguments
if arguments.count == 2 && arguments[1] == "--serve-metadata" { serveMetadata() }
if arguments.count == 6 && arguments[1] == "--file-recovery" && arguments[2].hasPrefix("/") {
    recoverFile(arguments)
}
if arguments.count == 3 && arguments[1] == "--note-inventory" && arguments[2].hasPrefix("/") {
    emitNoteInventory(root: URL(fileURLWithPath: arguments[2]))
}
guard [3, 4].contains(arguments.count), ["--tree", "--file", "--directory", "--tree-excluding", "--items"].contains(arguments[1]),
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
var itemFailures: [[String: String]] = []
var metadataUnavailable = 0
if arguments[1] == "--items" {
    let urls = stack
    stack = []
    entries = urls.count
    let counts = CloudStatusCounts()
    let workers = min(8, urls.count)
    DispatchQueue.concurrentPerform(iterations: workers) { worker in
        guard setiopolicy_np(IOPOL_TYPE_VFS_MATERIALIZE_DATALESS_FILES,
            IOPOL_SCOPE_THREAD, IOPOL_MATERIALIZE_DATALESS_FILES_OFF) == 0 else {
            counts.record([0, 0, 0, 1, 0]); return
        }
        for index in stride(from: worker, to: urls.count, by: workers) {
            counts.record(metadataCounts(urls[index]), name: urls[index].lastPathComponent)
        }
    }
    pending = counts.pending; stale = counts.stale; conflicts = counts.conflicts
    errors = counts.errors; metadataUnavailable = counts.unknown
    itemFailures = counts.failures
}
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
        if arguments[1] == "--directory" && (info.st_mode & S_IFMT) != S_IFDIR {
            errors += 1; continue
        }
        if value.ubiquitousItemHasUnresolvedConflicts == true ||
            !(NSFileVersion.unresolvedConflictVersionsOfItem(at: url) ?? []).isEmpty {
            conflicts += 1; continue
        }
        switch cloudDownloadState(ubiquitous: value.isUbiquitousItem == true,
                status: value.ubiquitousItemDownloadingStatus?.rawValue) {
        case .available: break
        case .pending: pending += 1; continue
        case .stale:
            if arguments[1] == "--directory" && localDirectoryReady(
                directory: value.isDirectory == true,
                status: value.ubiquitousItemDownloadingStatus?.rawValue) { break }
            stale += 1; continue
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
    "metadata_unavailable": metadataUnavailable, "failures": itemFailures]
if let output = try? JSONSerialization.data(withJSONObject: data, options: [.sortedKeys]) {
    FileHandle.standardOutput.write(output)
} else { exit(2) }
