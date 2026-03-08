import ArgumentParser
import Foundation
import KakaoCore

struct StatusCommand: ParsableCommand {
    static let configuration = CommandConfiguration(
        commandName: "status",
        abstract: "Show KakaoTalk installation and connection status"
    )

    func run() throws {
        // Check KakaoTalk.app
        let appPath = "/Applications/KakaoTalk.app"
        let containerExists = FileManager.default.fileExists(atPath: DeviceInfo.containerPath)
        let plistExists = FileManager.default.fileExists(atPath: DeviceInfo.preferencesPath) ||
            FileManager.default.fileExists(atPath: DeviceInfo.containerPreferencesPath)
        let cacheExists = FileManager.default.fileExists(atPath: DeviceInfo.cacheDatabasePath)

        print("KakaoTalk Status")
        print("================")
        print("App installed:      \(FileManager.default.fileExists(atPath: appPath) ? "Yes" : "No")")
        print("Container exists:   \(containerExists ? "Yes" : "No")")
        print("Preferences exist:  \(plistExists ? "Yes" : "No")")
        print("HTTP cache exists:  \(cacheExists ? "Yes" : "No")")

        // Check UUID
        do {
            let uuid = try DeviceInfo.platformUUID()
            print("Device UUID:        \(uuid)")
        } catch {
            print("Device UUID:        ERROR - \(error)")
        }

        // Count .db files in container
        if containerExists {
            let url = URL(fileURLWithPath: DeviceInfo.containerPath)
            let files = (try? FileManager.default.contentsOfDirectory(at: url, includingPropertiesForKeys: nil)) ?? []
            let dbFiles = files.filter {
                $0.pathExtension == "db" ||
                $0.lastPathComponent.range(of: #"^[0-9a-f]{78}$"#, options: .regularExpression) != nil
            }
            print("Database files:     \(dbFiles.count)")
        }

        // Check permissions
        print("\nPermissions")
        print("-----------")
        let hasFullDisk = containerExists && FileManager.default.isReadableFile(atPath: DeviceInfo.containerPath)
        print("Full Disk Access:   \(hasFullDisk ? "Likely OK" : "May be needed")")

        // App lifecycle
        print("\nApp Lifecycle")
        print("-------------")
        let appState = AppLifecycle.detectState()
        print("App state:          \(appState.rawValue)")
        let creds = CredentialStore()
        print("Stored credentials: \(creds.hasCredentials ? "Yes" : "No")")
    }
}
