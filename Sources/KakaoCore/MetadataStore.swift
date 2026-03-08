import Foundation

/// Persistent metadata store for KakaoTalk chat names and harvest info.
/// Stored at ~/.kakaocli/metadata.json
public final class MetadataStore {

    public struct ChatInfo: Codable {
        public var displayName: String
        public var memberCount: Int?
        public var chatType: Int?
        public var lastHarvested: Date?
        public var messageCount: Int?
    }

    private let filePath: String
    private var chats: [String: ChatInfo]

    public init() {
        let dir = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".kakaocli")
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        filePath = dir.appendingPathComponent("metadata.json").path

        if let data = FileManager.default.contents(atPath: filePath),
           let decoded = try? JSONDecoder().decode([String: ChatInfo].self, from: data) {
            chats = decoded
        } else {
            chats = [:]
        }
    }

    public func name(for chatId: Int64) -> String? {
        chats[String(chatId)]?.displayName
    }

    public func info(for chatId: Int64) -> ChatInfo? {
        chats[String(chatId)]
    }

    public func update(chatId: Int64, name: String, memberCount: Int? = nil,
                       chatType: Int? = nil, messageCount: Int? = nil) {
        let key = String(chatId)
        var existing = chats[key] ?? ChatInfo(displayName: name)
        existing.displayName = name
        existing.lastHarvested = Date()
        if let memberCount { existing.memberCount = memberCount }
        if let chatType { existing.chatType = chatType }
        if let messageCount { existing.messageCount = messageCount }
        chats[key] = existing
    }

    public func save() throws {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        encoder.dateEncodingStrategy = .iso8601
        let data = try encoder.encode(chats)
        try data.write(to: URL(fileURLWithPath: filePath))
    }

    public var allChats: [String: ChatInfo] {
        chats
    }

    public var count: Int { chats.count }
}
