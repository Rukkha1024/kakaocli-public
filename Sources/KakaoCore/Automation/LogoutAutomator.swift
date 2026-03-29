import AppKit
import ApplicationServices
import CoreGraphics
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

        if AppLifecycle.isLoginScreenVisible() || isOnscreenLoginWindowVisible() {
            return .alreadyLoggedOut
        }

        try clickLogoutMenuItem()
        try waitForLoginScreen()
        return .loggedOut
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

    private static func clickLogoutMenuItem() throws {
        if clickLogoutMenuItemViaStatusBar() {
            return
        }

        if clickLogoutMenuItemViaAX() {
            return
        }

        throw LogoutError.menuActionFailed("error: could not activate KakaoTalk status bar logout menu")
    }

    private static func clickLogoutMenuItemViaStatusBar() -> Bool {
        let script = """
        tell application "System Events"
            tell process "KakaoTalk"
                try
                    click menu bar item 1 of menu bar 2
                    delay 0.3
                    try
                        click menu item "Log out" of menu 1 of menu bar item 1 of menu bar 2
                    on error
                        click menu item "로그아웃" of menu 1 of menu bar item 1 of menu bar 2
                    end try
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
        return runAppleScript(script).contains("ok")
    }

    private static func clickLogoutMenuItemViaAX() -> Bool {
        try? AXHelpers.activateApp(bundleId: AppLifecycle.bundleId)
        guard let app = try? AXHelpers.appElement(bundleId: AppLifecycle.bundleId) else {
            return false
        }

        guard let appMenu = AXHelpers.findFirst(app, role: "AXMenuBarItem", text: "KakaoTalk") else {
            return false
        }
        guard AXHelpers.performAction(appMenu, kAXPressAction as String) else {
            return false
        }

        Thread.sleep(forTimeInterval: 0.2)

        if let logoutMenu = AXHelpers.findFirst(app, role: "AXMenuItem", text: "Log out") ??
            AXHelpers.findFirst(app, role: "AXMenuItem", text: "로그아웃") {
            return AXHelpers.performAction(logoutMenu, kAXPressAction as String)
        }
        return false
    }

    private static func waitForLoginScreen(timeout: TimeInterval = 30.0) throws {
        Thread.sleep(forTimeInterval: 0.8)
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            let state = AppLifecycle.detectState(aggressive: false)
            if state == .loginScreen || AppLifecycle.isLoginScreenVisible() || isOnscreenLoginWindowVisible() {
                return
            }

            if state == .unknown || state == .launching {
                dismissConfirmationIfNeeded()
            }

            if AppLifecycle.isLoginScreenVisible() || isOnscreenLoginWindowVisible() {
                return
            }
            Thread.sleep(forTimeInterval: 0.5)
        }

        let finalState = AppLifecycle.detectState()
        if finalState == .loginScreen || AppLifecycle.isLoginScreenVisible() || isOnscreenLoginWindowVisible() {
            return
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
            if let button = nearbyButton(in: window, matchingAny: candidates, maxDepth: 2) {
                _ = AXHelpers.performAction(button, kAXPressAction as String)
                Thread.sleep(forTimeInterval: 0.3)
                return
            }

            let directSheets = AXHelpers.children(window).filter { AXHelpers.role($0) == "AXSheet" }
            for sheet in directSheets {
                if let button = nearbyButton(in: sheet, matchingAny: candidates, maxDepth: 3) {
                    _ = AXHelpers.performAction(button, kAXPressAction as String)
                    Thread.sleep(forTimeInterval: 0.3)
                    return
                }
            }
        }
    }

    private static func nearbyButton(in element: AXUIElement, matchingAny candidates: [String], maxDepth: Int, depth: Int = 0) -> AXUIElement? {
        guard depth <= maxDepth else {
            return nil
        }

        for child in AXHelpers.children(element) where AXHelpers.role(child) == "AXButton" {
            let label = (AXHelpers.title(child) ?? AXHelpers.value(child) ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
            if candidates.contains(where: { label.localizedCaseInsensitiveContains($0) }) {
                return child
            }
        }

        for child in AXHelpers.children(element) {
            if let button = nearbyButton(in: child, matchingAny: candidates, maxDepth: maxDepth, depth: depth + 1) {
                return button
            }
        }
        return nil
    }

    private static func isOnscreenLoginWindowVisible() -> Bool {
        let windows = CGWindowListCopyWindowInfo([.optionOnScreenOnly], kCGNullWindowID) as? [[String: Any]] ?? []
        return windows.contains { window in
            guard let owner = window[kCGWindowOwnerName as String] as? String, owner == "KakaoTalk" else {
                return false
            }
            guard let name = window[kCGWindowName as String] as? String else {
                return false
            }
            guard name.lowercased().contains("log in") || name == "로그인" else {
                return false
            }
            guard let bounds = window[kCGWindowBounds as String] as? [String: Any],
                  let width = bounds["Width"] as? Double,
                  let height = bounds["Height"] as? Double,
                  width >= 200, height >= 300 else {
                return false
            }
            let alpha = window[kCGWindowAlpha as String] as? Double ?? 1.0
            return alpha > 0.0
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
