import ArgumentParser
import Foundation
import KakaoCore

struct LoginCommand: ParsableCommand {
    static let configuration = CommandConfiguration(
        commandName: "login",
        abstract: "Login to KakaoTalk (saves credentials and performs actual login)"
    )

    @Flag(name: .long, help: "Check login status")
    var status = false

    @Flag(name: .long, help: "Remove stored credentials")
    var clear = false

    @Flag(name: .long, help: "Only save credentials without attempting login")
    var saveOnly = false

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

        // Collect credentials (from args, prompt, or existing keychain)
        let needsNewCreds = email != nil || password != nil || !store.hasCredentials
        let emailValue: String
        let passwordValue: String

        if needsNewCreds {
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
        } else {
            print("Using stored credentials.")
        }

        if saveOnly {
            return
        }

        // Perform actual login
        try performLogin(store: store)
    }

    private func performLogin(store: CredentialStore) throws {
        let state = AppLifecycle.detectState()
        print("Current app state: \(state.rawValue)")

        if state == .loggedIn {
            print("Already logged in to KakaoTalk.")
            return
        }

        print("Attempting login...")
        do {
            try AppLifecycle.ensureReady(credentials: store)
            print("Login successful! KakaoTalk is ready.")
        } catch let error as LifecycleError {
            Swift.print("Error: \(error.description)")
            throw ExitCode.failure
        }
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
