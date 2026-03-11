import Foundation
import os

private let log = Logger(subsystem: "com.samantha.spike", category: "FileTools")

private let maxReadBytes = 1_048_576 // 1MB

private let sensitiveRelativeDirs: Set<String> = [".ssh", ".gnupg"]

private let protectedWritePrefixes = [
    "/etc", "/usr", "/bin", "/sbin", "/System", "/Library", "/boot", "/proc",
]

enum FileToolError: Error, LocalizedError {
    case missingArgument(String)
    case blocked(String)
    case notFound(String)
    case isDirectory(String)
    case tooLarge(Int)
    case permissionDenied(String)
    case ioError(String)

    var errorDescription: String? {
        switch self {
        case .missingArgument(let arg): return "Missing required argument: \(arg)"
        case .blocked(let reason): return "Blocked: \(reason)"
        case .notFound(let path): return "File not found: \(path)"
        case .isDirectory(let path): return "Path is a directory: \(path)"
        case .tooLarge(let bytes): return "File too large (\(bytes) bytes, max \(maxReadBytes))"
        case .permissionDenied(let path): return "Permission denied: \(path)"
        case .ioError(let msg): return msg
        }
    }
}

private func parseArgs(_ jsonString: String) throws -> [String: String] {
    guard let data = jsonString.data(using: .utf8),
          let obj = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
        return [:]
    }
    var result: [String: String] = [:]
    for (key, value) in obj {
        if let s = value as? String { result[key] = s }
    }
    return result
}

private func resolveUserPath(_ raw: String) -> String {
    let expanded = NSString(string: raw).expandingTildeInPath
    let url = URL(fileURLWithPath: expanded).standardized
    // Resolve relative paths against home dir
    if !raw.hasPrefix("/") && !raw.hasPrefix("~") {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        return URL(fileURLWithPath: home).appendingPathComponent(raw).standardized.path
    }
    return url.path
}

private func isInsideHome(_ resolved: String) -> Bool {
    let home = FileManager.default.homeDirectoryForCurrentUser.path
    return resolved == home || resolved.hasPrefix(home + "/")
}

private func isSensitiveDir(_ resolved: String) -> Bool {
    let home = FileManager.default.homeDirectoryForCurrentUser.path
    for dir in sensitiveRelativeDirs {
        let sensitive = home + "/" + dir
        if resolved == sensitive || resolved.hasPrefix(sensitive + "/") { return true }
    }
    return false
}

private func isProtectedWritePath(_ resolved: String) -> Bool {
    for prefix in protectedWritePrefixes {
        if resolved == prefix || resolved.hasPrefix(prefix + "/") { return true }
    }
    return false
}

func handleFileRead(_ arguments: String) async throws -> String {
    let args = try parseArgs(arguments)
    guard let rawPath = args["path"], !rawPath.isEmpty else {
        return formatError("file_read", FileToolError.missingArgument("path"))
    }

    let resolved = resolveUserPath(rawPath)

    if !isInsideHome(resolved) {
        return formatError("file_read", FileToolError.blocked("path \(resolved) is outside home directory"))
    }
    if isSensitiveDir(resolved) {
        return formatError("file_read", FileToolError.blocked("cannot read from sensitive directory"))
    }

    let fm = FileManager.default
    var isDir: ObjCBool = false
    guard fm.fileExists(atPath: resolved, isDirectory: &isDir) else {
        return formatError("file_read", FileToolError.notFound(resolved))
    }
    if isDir.boolValue {
        return formatError("file_read", FileToolError.isDirectory(resolved))
    }

    do {
        let attrs = try fm.attributesOfItem(atPath: resolved)
        let size = (attrs[.size] as? Int) ?? 0

        if size > maxReadBytes {
            // Read first 1MB and append truncation notice
            let handle = FileHandle(forReadingAtPath: resolved)
            defer { handle?.closeFile() }
            guard let handle else {
                return formatError("file_read", FileToolError.permissionDenied(resolved))
            }
            let data = handle.readData(ofLength: maxReadBytes)
            let text = String(data: data, encoding: .utf8)
                ?? String(data: data, encoding: .ascii)
                ?? "(binary content)"
            return text + "\n... truncated (\(size) bytes total, showing first \(maxReadBytes))"
        }

        let contents = try String(contentsOfFile: resolved, encoding: .utf8)
        log.info("file_read: \(resolved) (\(size) bytes)")
        return contents
    } catch let error as NSError where error.domain == NSCocoaErrorDomain && error.code == NSFileReadNoPermissionError {
        return formatError("file_read", FileToolError.permissionDenied(resolved))
    } catch {
        return formatError("file_read", FileToolError.ioError(error.localizedDescription))
    }
}

func handleFileWrite(_ arguments: String) async throws -> String {
    let args = try parseArgs(arguments)
    guard let rawPath = args["path"], !rawPath.isEmpty else {
        return formatError("file_write", FileToolError.missingArgument("path"))
    }
    guard let content = args["content"] else {
        return formatError("file_write", FileToolError.missingArgument("content"))
    }

    let resolved = resolveUserPath(rawPath)

    if !isInsideHome(resolved) {
        return formatError("file_write", FileToolError.blocked("path \(resolved) is outside home directory"))
    }
    if isSensitiveDir(resolved) {
        return formatError("file_write", FileToolError.blocked("cannot write to sensitive directory"))
    }
    if isProtectedWritePath(resolved) {
        return formatError("file_write", FileToolError.blocked("cannot write to protected path \(resolved)"))
    }

    let fm = FileManager.default
    let parentDir = URL(fileURLWithPath: resolved).deletingLastPathComponent().path

    do {
        if !fm.fileExists(atPath: parentDir) {
            try fm.createDirectory(atPath: parentDir, withIntermediateDirectories: true)
        }

        guard let data = content.data(using: .utf8) else {
            return formatError("file_write", FileToolError.ioError("Failed to encode content as UTF-8"))
        }
        try data.write(to: URL(fileURLWithPath: resolved))

        let byteCount = data.count
        log.info("file_write: \(resolved) (\(byteCount) bytes)")
        return "Wrote \(byteCount) bytes to \(resolved)"
    } catch let error as NSError where error.domain == NSCocoaErrorDomain && error.code == NSFileWriteNoPermissionError {
        return formatError("file_write", FileToolError.permissionDenied(resolved))
    } catch {
        return formatError("file_write", FileToolError.ioError(error.localizedDescription))
    }
}

private func formatError(_ tool: String, _ error: FileToolError) -> String {
    log.error("Error in \(tool): \(error.localizedDescription)")
    return "Error in \(tool): \(error.localizedDescription)"
}
