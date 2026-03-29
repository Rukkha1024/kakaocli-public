import AppKit
import ApplicationServices
import Foundation

public enum LogoutResult: Sendable, Equatable {
    case appNotRunning
    case alreadyLoggedOut
    case loggedOut
}

enum LogoutPreflightDecision: Equatable {
    case appOff
    case alreadyLoggedOut
    case performLogout(menuTitle: String)
    case unknown
}

public enum LogoutAutomator {

    public static func logout() throws -> LogoutResult {
        guard AppLifecycle.isRunning() else {
            return .appNotRunning
        }

        try? AXHelpers.activateApp(bundleId: AppLifecycle.bundleId)
        Thread.sleep(forTimeInterval: 0.3)

        let appState = AppLifecycle.detectState()
        if appState == .loginScreen {
            return .alreadyLoggedOut
        }

        let loginScreenVisible = AppLifecycle.isLoginScreenVisible()
        let initialDecision = preflightDecision(
            appRunning: true,
            menuItems: loginScreenVisible ? [] : try statusBarMenuItems(),
            loginScreenVisible: loginScreenVisible
        )

        switch initialDecision {
        case .appOff:
            return .appNotRunning
        case .alreadyLoggedOut:
            return .alreadyLoggedOut
        case .performLogout(let menuTitle):
            try clickStatusBarMenuItem(named: menuTitle)
            try waitForLoginScreen()
            return .loggedOut
        case .unknown:
            try? AXHelpers.activateApp(bundleId: AppLifecycle.bundleId)
            Thread.sleep(forTimeInterval: 0.5)
            if AppLifecycle.isLoginScreenVisible() {
                return .alreadyLoggedOut
            }
            throw LogoutError.unknownState
        }
    }

    static func parseMenuItems(_ rawOutput: String) -> [String] {
        rawOutput
            .replacingOccurrences(of: "\r\n", with: "\n")
            .replacingOccurrences(of: "\r", with: "\n")
            .replacingOccurrences(of: "missing value", with: "\n")
            .split(separator: "\n")
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty && !$0.lowercased().hasPrefix("error:") }
    }

    static func preflightDecision(
        appRunning: Bool,
        menuItems: [String],
        loginScreenVisible: Bool
    ) -> LogoutPreflightDecision {
        guard appRunning else {
            return .appOff
        }
        if loginScreenVisible {
            return .alreadyLoggedOut
        }
        if let logoutTitle = logoutMenuTitle(in: menuItems) {
            return .performLogout(menuTitle: logoutTitle)
        }
        return .unknown
    }

    private static func logoutMenuTitle(in menuItems: [String]) -> String? {
        menuItems.first { item in
            let normalized = item.lowercased()
            return normalized == "log out" || item == "로그아웃"
        }
    }

    private static func statusBarMenuItems() throws -> [String] {
        let script = """
        tell application "System Events"
            tell process "KakaoTalk"
                try
                    click menu bar item 1 of menu bar 2
                    delay 0.3
                    set oldDelims to AppleScript's text item delimiters
                    set AppleScript's text item delimiters to linefeed
                    set menuText to (name of every menu item of menu 1 of menu bar item 1 of menu bar 2) as text
                    set AppleScript's text item delimiters to oldDelims
                    key code 53
                    return menuText
                on error errMsg
                    try
                        key code 53
                    end try
                    return "error: " & errMsg
                end try
            end tell
        end tell
        """
        return try menuItems(fromScriptOutput: runAppleScript(script))
    }

    static func menuItems(fromScriptOutput rawOutput: String) throws -> [String] {
        let trimmed = rawOutput.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.lowercased().hasPrefix("error:") {
            throw LogoutError.menuReadFailed(trimmed)
        }
        return parseMenuItems(rawOutput)
    }

    private static func clickStatusBarMenuItem(named menuTitle: String) throws {
        let escapedTitle = menuTitle.replacingOccurrences(of: "\"", with: "\\\"")
        let script = """
        tell application "System Events"
            tell process "KakaoTalk"
                try
                    click menu bar item 1 of menu bar 2
                    delay 0.3
                    click menu item "\(escapedTitle)" of menu 1 of menu bar item 1 of menu bar 2
                    return "ok"
                on error errMsg
                    try
                        key code 53
                    end try
                    return "error: " & errMsg
                end try
            end tell
        end tell
        """
        let output = runAppleScript(script)
        guard output.contains("ok") else {
            throw LogoutError.menuActionFailed(output.trimmingCharacters(in: .whitespacesAndNewlines))
        }
    }

    private static func waitForLoginScreen(timeout: TimeInterval = 15.0) throws {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            try? AXHelpers.activateApp(bundleId: AppLifecycle.bundleId)
            dismissConfirmationIfNeeded()
            if AppLifecycle.isLoginScreenVisible() {
                return
            }
            Thread.sleep(forTimeInterval: 0.5)
        }
        throw LogoutError.timeout
    }

    private static func dismissConfirmationIfNeeded() {
        guard let app = NSRunningApplication.runningApplications(withBundleIdentifier: AppLifecycle.bundleId).first else {
            return
        }

        let axApp = AXUIElementCreateApplication(app.processIdentifier)
        let candidates = ["OK", "확인", "Log out", "로그아웃", "Logout"]

        for window in AXHelpers.windows(axApp) {
            for text in candidates {
                if let button = AXHelpers.findFirst(window, role: "AXButton", text: text) {
                    _ = AXHelpers.performAction(button, kAXPressAction as String)
                    Thread.sleep(forTimeInterval: 0.3)
                    return
                }
                for sheet in AXHelpers.findAll(window, role: "AXSheet") {
                    if let button = AXHelpers.findFirst(sheet, role: "AXButton", text: text) {
                        _ = AXHelpers.performAction(button, kAXPressAction as String)
                        Thread.sleep(forTimeInterval: 0.3)
                        return
                    }
                }
            }
        }
    }

    private static func runAppleScript(_ script: String) -> String {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
        process.arguments = ["-e", script]
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = FileHandle.nullDevice

        do {
            try process.run()
            process.waitUntilExit()
            return String(data: pipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
        } catch {
            return "error: \(error.localizedDescription)"
        }
    }
}

public enum LogoutError: Error, CustomStringConvertible, Equatable {
    case menuReadFailed(String)
    case menuActionFailed(String)
    case timeout
    case unknownState

    public var description: String {
        switch self {
        case .menuReadFailed(let output):
            return "Logout menu inspection failed: \(output)"
        case .menuActionFailed(let output):
            return "Logout menu action failed: \(output)"
        case .timeout:
            return "Logout did not reach the login screen before timeout"
        case .unknownState:
            return "KakaoTalk is running, but its logout state could not be determined"
        }
    }
}
