// Read-only native notifications. No enumeration, metadata probe, body read or download.
import CoreServices
import Foundation

final class EventOutput {
    let root: String
    let includes: [String]
    let excludes: [String]
    init(_ root: String, _ includes: [String], _ excludes: [String]) {
        self.root = root; self.includes = includes; self.excludes = excludes
    }
    func inside(_ path: String, _ prefix: String) -> Bool {
        prefix.isEmpty || path == prefix || path.hasPrefix(prefix + "/")
    }
    func emit(_ value: [String: Any]) {
        if let data = try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]) {
            FileHandle.standardOutput.write(data); FileHandle.standardOutput.write(Data([10]))
        }
    }
    func receive(_ count: Int, _ raw: UnsafeMutableRawPointer,
                 _ flags: UnsafePointer<FSEventStreamEventFlags>,
                 _ ids: UnsafePointer<FSEventStreamEventId>) {
        let paths = unsafeBitCast(raw, to: NSArray.self) as! [String]
        var entries: [[Any]] = []
        for i in 0..<count {
            // Commit at most 512 events per line. Each cursor covers only its own
            // prefix, so interruption cannot acknowledge an unread suffix.
            if entries.count >= 512 {
                emit(["events": entries, "cursor": String(ids[i-1])]); entries.removeAll()
            }
            let invalidated = flags[i] & UInt32(kFSEventStreamEventFlagUserDropped |
                kFSEventStreamEventFlagKernelDropped | kFSEventStreamEventFlagEventIdsWrapped |
                kFSEventStreamEventFlagRootChanged) != 0
            if invalidated { entries.append(["", 1]) }
            let path = paths[i]
            guard path == root || path.hasPrefix(root + "/") else { continue }
            let relative = path == root ? "" : String(path.dropFirst(root.count + 1))
            guard !excludes.contains(where: { inside(relative, $0) }),
                  includes.contains(where: { inside(relative, $0) || inside($0, relative) }) else { continue }
            let markers = [".fiber-publication.json", ".fiber-analysis.json", ".knowledge-organizer-policy.json"]
            guard !relative.split(separator: "/").contains(where: {
                $0.hasPrefix(".") && !markers.contains(String($0))
            }) else { continue }
            let lost = flags[i] & UInt32(kFSEventStreamEventFlagMustScanSubDirs |
                kFSEventStreamEventFlagUserDropped | kFSEventStreamEventFlagKernelDropped |
                kFSEventStreamEventFlagEventIdsWrapped | kFSEventStreamEventFlagRootChanged) != 0
            let history = flags[i] & UInt32(kFSEventStreamEventFlagHistoryDone) != 0
            if !history {
                var kind = lost ? 1 : 0
                if flags[i] & UInt32(kFSEventStreamEventFlagItemModified) != 0 { kind |= 2 }
                if flags[i] & UInt32(kFSEventStreamEventFlagItemRenamed) != 0 { kind |= 4 }
                if flags[i] & UInt32(kFSEventStreamEventFlagItemRemoved) != 0 { kind |= 8 }
                if flags[i] & UInt32(kFSEventStreamEventFlagItemCreated) != 0 { kind |= 16 }
                entries.append([relative, kind])
            }
        }
        if count > 0 {
            let cursor = ids[count-1] == 0 ? FSEventsGetCurrentEventId() : ids[count-1]
            emit(["events": entries, "cursor": String(cursor)])
        }
    }
}

let args = CommandLine.arguments
guard args.count == 3, args[1].hasPrefix("/"), let cursor = UInt64(args[2]),
      let input = try? JSONSerialization.jsonObject(with: FileHandle.standardInput.readDataToEndOfFile()) as? [String: Any],
      let includes = input["include"] as? [String], let excludes = input["exclude"] as? [String],
      !includes.isEmpty, includes.count <= 100, excludes.count <= 100 else { exit(2) }
let output = EventOutput(args[1], includes, excludes)
var context = FSEventStreamContext(version: 0,
    info: Unmanaged.passUnretained(output).toOpaque(), retain: nil, release: nil, copyDescription: nil)
let callback: FSEventStreamCallback = { _, info, count, paths, flags, ids in
    guard let info = info else { return }
    Unmanaged<EventOutput>.fromOpaque(info).takeUnretainedValue().receive(count, paths, flags, ids)
}
let since = cursor == 0 ? FSEventsGetCurrentEventId() : cursor
guard let stream = FSEventStreamCreate(nil, callback, &context, [args[1]] as CFArray,
    since, 1.0,
    UInt32(kFSEventStreamCreateFlagFileEvents | kFSEventStreamCreateFlagUseCFTypes |
           kFSEventStreamCreateFlagWatchRoot)) else { exit(3) }
FSEventStreamSetDispatchQueue(stream, DispatchQueue.main)
guard FSEventStreamStart(stream) else { exit(4) }
output.emit(["ready": true, "cursor": String(since)])
CFRunLoopRun()
FSEventStreamStop(stream); FSEventStreamInvalidate(stream); FSEventStreamRelease(stream)
