import Foundation

/// Manages KakaoTalk login credentials using macOS Keychain.
public final class CredentialStore: Sendable {

    private static let service = "com.kakaocli.credentials"
    private static let emailAccount = "kakaotalk-email"
    private static let passwordAccount = "kakaotalk-password"

    public init() {}

    // MARK: - Read

    public var email: String? {
        Self.readKeychain(account: Self.emailAccount)
    }

    public var password: String? {
        Self.readKeychain(account: Self.passwordAccount)
    }

    public var hasCredentials: Bool {
        email != nil && password != nil
    }

    // MARK: - Write

    public func save(email: String, password: String) throws {
        try Self.writeKeychain(account: Self.emailAccount, value: email)
        try Self.writeKeychain(account: Self.passwordAccount, value: password)
    }

    public func clear() {
        Self.deleteKeychain(account: Self.emailAccount)
        Self.deleteKeychain(account: Self.passwordAccount)
    }

    // MARK: - Keychain Primitives

    private static func readKeychain(account: String) -> String? {
        // Use `security` CLI to avoid code-signing ACL issues with swift run.
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/security")
        process.arguments = [
            "find-generic-password",
            "-s", service,
            "-a", account,
            "-w",  // output password only
        ]
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = FileHandle.nullDevice
        do {
            try process.run()
            process.waitUntilExit()
            guard process.terminationStatus == 0 else { return nil }
            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            return String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines)
        } catch {
            return nil
        }
    }

    private static func writeKeychain(account: String, value: String) throws {
        // Use `security` CLI to avoid code-signing ACL issues with swift run.
        // The Security framework ties keychain items to the signing identity of the binary,
        // which changes every time swift run rebuilds to a new path.
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/security")
        process.arguments = [
            "add-generic-password",
            "-s", service,
            "-a", account,
            "-w", value,
            "-U",  // update if exists
        ]
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        try process.run()
        process.waitUntilExit()
        guard process.terminationStatus == 0 else {
            throw CredentialError.keychainError(OSStatus(process.terminationStatus))
        }
    }

    private static func deleteKeychain(account: String) {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/security")
        process.arguments = [
            "delete-generic-password",
            "-s", service,
            "-a", account,
        ]
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        try? process.run()
        process.waitUntilExit()
    }
}

public enum CredentialError: Error, CustomStringConvertible {
    case keychainError(OSStatus)

    public var description: String {
        switch self {
        case .keychainError(let status):
            return "Keychain error: exit code \(status)"
        }
    }
}
