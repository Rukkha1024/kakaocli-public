import ArgumentParser
import Foundation
import KakaoCore

struct LogoutCommand: ParsableCommand {
    static let configuration = CommandConfiguration(
        commandName: "logout",
        abstract: "Log out of KakaoTalk via UI automation"
    )

    func run() throws {
        do {
            switch try LogoutAutomator.logout() {
            case .appNotRunning:
                print("KakaoTalk is not running. Nothing to log out.")
            case .alreadyLoggedOut:
                print("KakaoTalk is already showing the login screen.")
            case .loggedOut:
                print("Logout successful. KakaoTalk is now showing the login screen.")
            }
        } catch let error as LogoutError {
            Swift.print("Error: \(error.description)")
            throw ExitCode.failure
        }
    }
}
