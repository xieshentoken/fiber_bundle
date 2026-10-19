import Foundation

// The backend validates the reviewed catalog intent; this helper only invokes macOS Trash.
let args = CommandLine.arguments
if args.count != 2 || !args[1].hasPrefix("/") { exit(64) }
let source = URL(fileURLWithPath: args[1])
do {
    let attributes = try FileManager.default.attributesOfItem(atPath: source.path)
    guard attributes[.type] as? FileAttributeType == .typeDirectory else { exit(65) }
    var component = ""
    for part in args[1].split(separator: "/") {
        guard part != "." && part != ".." else { exit(65) }
        component += "/" + part
        let item = try FileManager.default.attributesOfItem(atPath: component)
        guard item[.type] as? FileAttributeType == .typeDirectory else { exit(65) }
    }
    var destination: NSURL?
    try FileManager.default.trashItem(at: source, resultingItemURL: &destination)
    guard let destination else { exit(70) }
    let data = try JSONSerialization.data(withJSONObject: ["schema": "vault-trash/v1", "trash_path": destination.path ?? ""])
    FileHandle.standardOutput.write(data)
} catch {
    // Never emit private file contents or provider credentials.
    FileHandle.standardError.write(Data("vault_trash_failed\n".utf8))
    exit(74)
}
