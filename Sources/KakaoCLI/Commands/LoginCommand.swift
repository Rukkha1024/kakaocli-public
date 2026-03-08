import ArgumentParser
import Foundation
import KakaoCore

struct LoginCommand: ParsableCommand {
    static let configuration = CommandConfiguration(
        commandName: "login",
        abstract: "Manage KakaoTalk login credentials"
    )

    @Flag(name: .long, help: "Check login status")
    var status = false

    @Flag(name: .long, help: "Remove stored credentials")
    var clear = false

    @Option(name: .long, help: "Email address (skips interactive prompt)")
    var email: String?

    @Option(name: .long, help: "Password (skips interactive prompt; prefer interactive for security)")
    var password: String?

    func run() throws {
        let store = CredentialStore()

        if clear {
            store.clear()
            print("Credentials removed from Keychain.")
            return
        }

        if status {
            printStatus(store: store)
            return
        }

        // Store new credentials
        let emailValue: String
        let passwordValue: String

        if let e = email {
            emailValue = e
        } else {
            Swift.print("KakaoTalk email: ", terminator: "")
            guard let input = readLine()?.trimmingCharacters(in: .whitespacesAndNewlines), !input.isEmpty else {
                Swift.print("Error: Email cannot be empty.")
                throw ExitCode.failure
            }
            emailValue = input
        }

        if let p = password {
            passwordValue = p
        } else {
            guard let cStr = getpass("KakaoTalk password: ") else {
                Swift.print("Error: Could not read password.")
                throw ExitCode.failure
            }
            passwordValue = String(cString: cStr)
            guard !passwordValue.isEmpty else {
                Swift.print("Error: Password cannot be empty.")
                throw ExitCode.failure
            }
        }

        try store.save(email: emailValue, password: passwordValue)
        print("Credentials saved to Keychain.")
    }

    private func printStatus(store: CredentialStore) {
        let hasCreds = store.hasCredentials
        let appState = AppLifecycle.detectState()

        print("Login Status")
        print("============")
        print("Stored credentials: \(hasCreds ? "Yes" : "No")")
        if hasCreds, let email = store.email {
            print("Email:              \(maskEmail(email))")
        }
        print("App state:          \(appState.rawValue)")

        switch appState {
        case .loggedIn:
            print("\nKakaoTalk is running and logged in.")
        case .loginScreen where hasCreds:
            print("\nKakaoTalk is on the login screen. Run any command to auto-login.")
        case .loginScreen:
            print("\nKakaoTalk is on the login screen. Store credentials first:")
            print("  kakaocli login")
        case .notRunning:
            print("\nKakaoTalk is not running. It will be launched automatically when needed.")
        case .updateRequired:
            print("\nKakaoTalk needs an update. Please update the app manually.")
        default:
            break
        }
    }

    private func maskEmail(_ email: String) -> String {
        guard let atIndex = email.firstIndex(of: "@") else { return "***" }
        let local = email[email.startIndex..<atIndex]
        if local.count <= 2 { return "**@\(email[email.index(after: atIndex)...])" }
        return "\(local.prefix(2))***@\(email[email.index(after: atIndex)...])"
    }
}
