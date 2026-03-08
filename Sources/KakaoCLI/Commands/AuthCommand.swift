import ArgumentParser
import Foundation
import KakaoCore

struct AuthCommand: ParsableCommand {
    static let configuration = CommandConfiguration(
        commandName: "auth",
        abstract: "Derive keys and verify database access"
    )

    @Flag(name: .long, help: "Show derived values (key, db name) for debugging")
    var verbose = false

    @Option(name: .long, help: "Override user ID instead of reading from plist")
    var userId: Int?

    @Option(name: .long, help: "Override device UUID instead of reading from ioreg")
    var uuid: String?

    func run() throws {
        // 1. Get device UUID
        let deviceUUID: String
        if let override = uuid {
            deviceUUID = override
        } else {
            deviceUUID = try DeviceInfo.platformUUID()
        }
        print("UUID: \(deviceUUID)")

        // 2. Get user ID
        let uid: Int
        if let override = userId {
            uid = override
        } else {
            do {
                uid = try DeviceInfo.userId()
            } catch let error as KakaoError {
                print("Error: \(error)")
                print("\nCould not auto-detect user ID.")
                print("Try: kakaocli auth --user-id <YOUR_KAKAO_USER_ID>")
                print("\nTo find your user ID, check:")
                print("  defaults read com.kakao.KakaoTalkMac")
                throw ExitCode.failure
            }
        }
        print("User ID: \(uid)")

        // 3. Derive database name and key
        let dbName = KeyDerivation.databaseName(userId: uid, uuid: deviceUUID)
        let secureKey = KeyDerivation.secureKey(userId: uid, uuid: deviceUUID)

        if verbose {
            print("Database name: \(dbName)")
            print("Secure key: \(secureKey.prefix(16))...")
        }

        // 4. Check if database file exists (try without extension first, then with .db)
        let candidates = [
            "\(DeviceInfo.containerPath)/\(dbName)",
            "\(DeviceInfo.containerPath)/\(dbName).db",
        ]
        guard let dbPath = candidates.first(where: { FileManager.default.fileExists(atPath: $0) }) else {
            print("Database NOT found at: \(DeviceInfo.containerPath)/\(dbName)[.db]")
            print("\nListing files in container:")
            let containerURL = URL(fileURLWithPath: DeviceInfo.containerPath)
            if let files = try? FileManager.default.contentsOfDirectory(at: containerURL, includingPropertiesForKeys: nil) {
                for file in files where !file.lastPathComponent.hasSuffix("-shm") && !file.lastPathComponent.hasSuffix("-wal") {
                    print("  \(file.lastPathComponent)")
                }
            } else {
                print("  Could not list directory (check Full Disk Access)")
            }
            throw ExitCode.failure
        }
        print("Database found: \(dbPath)")

        // 5. Try opening the database
        let reader = DatabaseReader(databasePath: dbPath)
        do {
            try reader.open(key: secureKey)
            let tables = try reader.schema()
            print("\nDatabase opened successfully!")
            print("Tables found: \(tables.count)")
            for table in tables {
                print("  - \(table.name)")
            }
        } catch {
            print("\nFailed to open database: \(error)")
            print("This may mean SQLCipher is needed. Install: brew install sqlcipher")
        }
    }
}
