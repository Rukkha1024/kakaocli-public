import Foundation
import Testing
@testable import KakaoCore

@Test func testUserIdFromCacheRequestObjectData() throws {
    let requestObject: [String: Any] = [
        "Version": 9,
        "Array": [
            false,
            [
                "_CFURLStringType": 15,
                "_CFURLString": "https://talk-pilsner.kakao.com/talk/friends/sync?since=1",
            ],
            [
                "Accept": "application/json",
                "talk-user-id": "15151807",
            ],
        ],
    ]

    let data = try PropertyListSerialization.data(fromPropertyList: requestObject, format: .binary, options: 0)
    let result = DeviceInfo.userId(fromCacheRequestObjectData: data)

    #expect(result == 15151807, "Should recover talk-user-id from cached request object")
}

@Test func testUserIdFromCacheRequestObjectDataMissingHeader() throws {
    let requestObject: [String: Any] = [
        "Version": 9,
        "Array": [
            false,
            [
                "_CFURLStringType": 15,
                "_CFURLString": "https://talk-pilsner.kakao.com/talk/friends/sync?since=1",
            ],
            [
                "Accept": "application/json",
            ],
        ],
    ]

    let data = try PropertyListSerialization.data(fromPropertyList: requestObject, format: .binary, options: 0)
    let result = DeviceInfo.userId(fromCacheRequestObjectData: data)

    #expect(result == nil, "Should return nil when talk-user-id header is absent")
}
