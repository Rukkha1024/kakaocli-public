import Testing
@testable import KakaoCore

@Test func testParseMenuItemsHandlesMissingValueSeparators() {
    let raw = "Open KakaoTalkmissing valueRead Allmissing valueLock modemissing valueLog outmissing valueQuit"
    let items = LogoutAutomator.parseMenuItems(raw)

    #expect(items == ["Open KakaoTalk", "Read All", "Lock mode", "Log out", "Quit"])
}

@Test func testParseMenuItemsHandlesNewlinesAndLocalization() {
    let raw = "카카오톡 열기\n로그아웃\n종료\n"
    let items = LogoutAutomator.parseMenuItems(raw)

    #expect(items == ["카카오톡 열기", "로그아웃", "종료"])
}

@Test func testMenuItemsThrowsWhenAppleScriptReturnsError() throws {
    do {
        _ = try LogoutAutomator.menuItems(fromScriptOutput: "error: accessibility permission missing")
        Issue.record("Expected menu read failure")
    } catch let error as LogoutError {
        #expect(error == .menuReadFailed("error: accessibility permission missing"))
    }
}

@Test func testPreflightReturnsAppOffWhenNotRunning() {
    let decision = LogoutAutomator.preflightDecision(
        appRunning: false,
        menuItems: ["Log out"],
        loginScreenVisible: false
    )

    #expect(decision == .appOff)
}

@Test func testPreflightTreatsVisibleLoginScreenAsAlreadyLoggedOutEvenIfMenuLooksStale() {
    let decision = LogoutAutomator.preflightDecision(
        appRunning: true,
        menuItems: ["Open KakaoTalk", "Log out", "Quit"],
        loginScreenVisible: true
    )

    #expect(decision == .alreadyLoggedOut)
}

@Test func testPreflightPerformsLogoutWhenLogoutMenuIsPresentAndLoginScreenIsHidden() {
    let decision = LogoutAutomator.preflightDecision(
        appRunning: true,
        menuItems: ["Open KakaoTalk", "Log out", "Quit"],
        loginScreenVisible: false
    )

    #expect(decision == .performLogout(menuTitle: "Log out"))
}

@Test func testPreflightReturnsUnknownWhenRunningWithoutLogoutMenuOrLoginScreen() {
    let decision = LogoutAutomator.preflightDecision(
        appRunning: true,
        menuItems: ["Open KakaoTalk", "Quit"],
        loginScreenVisible: false
    )

    #expect(decision == .unknown)
}
