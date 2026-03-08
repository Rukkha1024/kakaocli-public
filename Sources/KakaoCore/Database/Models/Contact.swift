import Foundation

/// A KakaoTalk contact/friend.
public struct Contact: Sendable {
    public let id: Int64
    public let name: String
    public let profileImageUrl: String?
    public let statusMessage: String?
}
