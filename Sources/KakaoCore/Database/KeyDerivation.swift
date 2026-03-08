import CommonCrypto
import Foundation

/// Derives database name and encryption key for KakaoTalk's encrypted SQLite database.
///
/// Based on https://gist.github.com/blluv/8418e3ef4f4aa86004657ea524f2de14
public enum KeyDerivation {

    /// PBKDF2-HMAC-SHA256 with 100,000 iterations, 128-byte output.
    static func pbkdf2(password: Data, salt: Data) -> Data {
        let keyLength = 128
        var derivedKey = Data(count: keyLength)
        _ = derivedKey.withUnsafeMutableBytes { derivedKeyPtr in
            password.withUnsafeBytes { passwordPtr in
                salt.withUnsafeBytes { saltPtr in
                    CCKeyDerivationPBKDF(
                        CCPBKDFAlgorithm(kCCPBKDF2),
                        passwordPtr.baseAddress?.assumingMemoryBound(to: Int8.self),
                        password.count,
                        saltPtr.bindMemory(to: UInt8.self).baseAddress,
                        salt.count,
                        CCPseudoRandomAlgorithm(kCCPRFHmacAlgSHA256),
                        100_000,
                        derivedKeyPtr.bindMemory(to: UInt8.self).baseAddress,
                        keyLength
                    )
                }
            }
        }
        return derivedKey
    }

    /// SHA-1 + SHA-256 of UUID, base64-encoded.
    static func hashedDeviceUUID(_ uuid: String) -> String {
        let data = Data(uuid.utf8)
        var sha1 = Data(count: Int(CC_SHA1_DIGEST_LENGTH))
        var sha256 = Data(count: Int(CC_SHA256_DIGEST_LENGTH))

        _ = sha1.withUnsafeMutableBytes { sha1Ptr in
            data.withUnsafeBytes { dataPtr in
                CC_SHA1(dataPtr.baseAddress, CC_LONG(data.count), sha1Ptr.bindMemory(to: UInt8.self).baseAddress)
            }
        }
        _ = sha256.withUnsafeMutableBytes { sha256Ptr in
            data.withUnsafeBytes { dataPtr in
                CC_SHA256(dataPtr.baseAddress, CC_LONG(data.count), sha256Ptr.bindMemory(to: UInt8.self).baseAddress)
            }
        }

        return (sha1 + sha256).base64EncodedString()
    }

    /// Derive the encrypted database filename (without extension).
    public static func databaseName(userId: Int, uuid: String) -> String {
        let hawawa = [".", "F", String(userId), "A", "F",
                      String(uuid.reversed()), ".", "|"].joined(separator: ".")
        let salt = String(hashedDeviceUUID(uuid).reversed())
        let derived = pbkdf2(
            password: Data(hawawa.utf8),
            salt: Data(salt.utf8)
        )
        let hex = derived.map { String(format: "%02x", $0) }.joined()
        let start = hex.index(hex.startIndex, offsetBy: 28)
        let end = hex.index(start, offsetBy: 78)
        return String(hex[start..<end])
    }

    /// Derive the SQLCipher encryption key for the database.
    public static func secureKey(userId: Int, uuid: String) -> String {
        let hashed = hashedDeviceUUID(uuid)
        let parts = ["A", hashed, "|", "F", String(uuid.prefix(5)),
                     "H", String(userId), "|", String(uuid.dropFirst(7))]
        let hawawa = parts.joined(separator: "F")
        let saltStart = uuid.index(uuid.startIndex, offsetBy: Int(Double(uuid.count) * 0.3))
        let salt = String(uuid[saltStart...])
        let derived = pbkdf2(
            password: Data(String(hawawa.reversed()).utf8),
            salt: Data(salt.utf8)
        )
        return derived.map { String(format: "%02x", $0) }.joined()
    }
}
