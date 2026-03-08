import AppKit
import ApplicationServices
import Foundation

/// Automates the KakaoTalk login screen via Accessibility API.
public enum LoginAutomator {

    /// Perform credential-based login on the KakaoTalk login screen.
    ///
    /// KakaoTalk's login screen uses id="Main Window" with title="Log in".
    /// It has two AXTextFields (email first, password second) and a "Log in" button.
    public static func login(email: String, password: String) throws {
        let bundleId = AppLifecycle.bundleId
        try AXHelpers.activateApp(bundleId: bundleId)
        let app = try AXHelpers.appElement(bundleId: bundleId)

        let windows = AXHelpers.windows(app)
        guard !windows.isEmpty else {
            throw LifecycleError.loginFailed("No login window found")
        }

        // Login window has id="Main Window" but title="Log in" or contains login logo
        let loginWindow = windows.first(where: {
            let title = AXHelpers.title($0) ?? ""
            return title.lowercased().contains("log in") || title == "로그인"
        }) ?? windows.first(where: {
            AXHelpers.findFirst($0, role: "AXImage", identifier: "Logo") != nil
        })

        guard let loginWindow else {
            fputs("DEBUG: Window titles: \(windows.map { AXHelpers.title($0) ?? "?" })\n", stderr)
            throw LifecycleError.loginFailed("Could not identify login window")
        }

        fputs("Attempting auto-login...\n", stderr)

        // Find text fields — KakaoTalk uses regular AXTextField for both email and password
        let textFields = AXHelpers.findAll(loginWindow, role: "AXTextField")
        let secureFields = AXHelpers.findAll(loginWindow, role: "AXSecureTextField")

        // Email is first text field, password is second text field or first secure field
        guard textFields.count >= 1 else {
            fputs("DEBUG: Login window tree:\n", stderr)
            fputs(AXHelpers.dumpTree(loginWindow, maxDepth: 5), stderr)
            throw LifecycleError.loginFailed("Could not find email field. The login UI may have changed.")
        }
        let emailField = textFields[0]

        // Password: try AXSecureTextField first, then second AXTextField
        let passwordField: AXUIElement
        if let secure = secureFields.first {
            passwordField = secure
        } else if textFields.count >= 2 {
            passwordField = textFields[1]
        } else {
            fputs("DEBUG: Login window tree:\n", stderr)
            fputs(AXHelpers.dumpTree(loginWindow, maxDepth: 5), stderr)
            throw LifecycleError.loginFailed("Could not find password field. The login UI may have changed.")
        }

        // Check "Keep me logged in" checkbox
        if let keepLoggedIn = AXHelpers.findFirst(loginWindow, role: "AXCheckBox", text: "Keep me logged in") ??
           AXHelpers.findFirst(loginWindow, role: "AXCheckBox", text: "로그인 유지") {
            let checked = AXHelpers.intAttribute(keepLoggedIn, kAXValueAttribute as String) ?? 0
            if checked == 0 {
                _ = AXHelpers.performAction(keepLoggedIn, kAXPressAction as String)
                Thread.sleep(forTimeInterval: 0.2)
            }
        }

        // Fill email
        AXHelpers.clickElement(emailField)
        Thread.sleep(forTimeInterval: 0.1)
        AXHelpers.selectAll()
        Thread.sleep(forTimeInterval: 0.05)
        if !AXHelpers.setValue(emailField, email) {
            AXHelpers.typeText(email)
        }
        Thread.sleep(forTimeInterval: 0.2)

        // Fill password
        AXHelpers.clickElement(passwordField)
        Thread.sleep(forTimeInterval: 0.2)
        AXHelpers.selectAll()
        Thread.sleep(forTimeInterval: 0.05)
        // Try setValue first, fall back to typing
        if !AXHelpers.setValue(passwordField, password) {
            AXHelpers.typeText(password)
        }
        Thread.sleep(forTimeInterval: 0.3)

        // Click login button or press Enter
        if let loginButton = findLoginButton(in: loginWindow) {
            _ = AXHelpers.performAction(loginButton, kAXPressAction as String)
        } else {
            AXHelpers.pressKey(keyCode: 36) // Return
        }

        // Wait for login to complete.
        // After clicking login, the window may disappear briefly during the transition.
        // Poll with status bar menu detection since the AX window may not be visible.
        let loginStart = Date()
        let deadline = Date().addingTimeInterval(30.0)
        while Date() < deadline {
            let state = AppLifecycle.detectState(aggressive: false)
            if state == .loggedIn {
                fputs("Login successful.\n", stderr)
                return
            }
            if state == .loginScreen && Date().timeIntervalSince(loginStart) > 5.0 {
                // Still on login screen after 5s — likely an error
                let windows = AXHelpers.windows(app)
                let hasLoginWindow = windows.contains {
                    AXHelpers.role($0) == "AXWindow" && (AXHelpers.title($0) ?? "").lowercased().contains("log in")
                }
                if hasLoginWindow {
                    try checkForLoginErrors(app: app)
                }
            }
            Thread.sleep(forTimeInterval: 1.0)
        }

        // Final check
        let finalState = AppLifecycle.detectState(aggressive: false)
        if finalState == .loggedIn {
            fputs("Login successful.\n", stderr)
            return
        }

        throw LifecycleError.loginFailed("Login timed out. Final state: \(finalState.rawValue)")
    }

    // MARK: - Private Helpers

    private static func findEmailLoginTab(in window: AXUIElement) -> AXUIElement? {
        let candidates = ["카카오계정", "이메일", "email", "계정", "Account"]
        for text in candidates {
            if let el = AXHelpers.findFirst(window, role: "AXButton", text: text) { return el }
            if let el = AXHelpers.findFirst(window, role: "AXCheckBox", text: text) { return el }
            if let el = AXHelpers.findFirst(window, role: "AXStaticText", text: text) { return el }
        }
        return nil
    }

    private static func findLoginButton(in window: AXUIElement) -> AXUIElement? {
        let candidates = ["로그인", "Login", "Log In", "Sign In", "확인"]
        for text in candidates {
            if let button = AXHelpers.findFirst(window, role: "AXButton", text: text) { return button }
        }
        return nil
    }

    private static func checkForLoginErrors(app: AXUIElement) throws {
        let windows = AXHelpers.windows(app)
        for window in windows {
            let allText = AXHelpers.findAll(window, role: "AXStaticText")
            for textElement in allText {
                let text = (AXHelpers.value(textElement) ?? AXHelpers.title(textElement) ?? "").lowercased()
                if text.contains("인증") || text.contains("verification") || text.contains("otp") || text.contains("2fa") {
                    throw LifecycleError.otpRequired
                }
                if (text.contains("비밀번호") && text.contains("틀")) || text.contains("incorrect") || text.contains("wrong") {
                    throw LifecycleError.wrongPassword
                }
                if text.contains("네트워크") || text.contains("network") || text.contains("connection") {
                    throw LifecycleError.networkError
                }
            }
        }
        throw LifecycleError.loginFailed("Login did not succeed. Check your credentials.")
    }
}
