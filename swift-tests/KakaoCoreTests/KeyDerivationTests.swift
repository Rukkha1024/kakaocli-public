import Testing
@testable import KakaoCore

@Test func testHashedDeviceUUID() {
    // Verify the hashing function produces consistent output
    let result1 = KeyDerivation.hashedDeviceUUID("TEST-UUID-1234")
    let result2 = KeyDerivation.hashedDeviceUUID("TEST-UUID-1234")
    #expect(result1 == result2, "Same input should produce same output")
    #expect(!result1.isEmpty, "Output should not be empty")
}

@Test func testDatabaseNameDeterministic() {
    let name1 = KeyDerivation.databaseName(userId: 12345, uuid: "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE")
    let name2 = KeyDerivation.databaseName(userId: 12345, uuid: "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE")
    #expect(name1 == name2, "Same inputs should produce same database name")
    #expect(name1.count == 78, "Database name should be 78 characters")
}

@Test func testSecureKeyDeterministic() {
    let key1 = KeyDerivation.secureKey(userId: 12345, uuid: "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE")
    let key2 = KeyDerivation.secureKey(userId: 12345, uuid: "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE")
    #expect(key1 == key2, "Same inputs should produce same key")
    #expect(!key1.isEmpty, "Key should not be empty")
}

@Test func testDifferentUsersGetDifferentKeys() {
    let uuid = "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE"
    let key1 = KeyDerivation.secureKey(userId: 11111, uuid: uuid)
    let key2 = KeyDerivation.secureKey(userId: 22222, uuid: uuid)
    #expect(key1 != key2, "Different user IDs should produce different keys")
}
