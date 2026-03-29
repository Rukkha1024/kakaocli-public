# Issue 001: Add KakaoTalk logout command with TDD-first implementation

**Status**: Done
**Created**: 2026-03-29

## Background

The CLI can detect login-related UI states but cannot actively log out of KakaoTalk.
We need a dedicated `kakaocli logout` command that ends the running app session without
deleting stored Keychain credentials, and we need to build it with tests first.

## Acceptance Criteria

- [x] `kakaocli logout` is available as a top-level command.
- [x] The command logs out a running KakaoTalk session without deleting saved credentials.
- [x] The command treats an already logged-out state as success.
- [x] The command treats an app-off state as success without launching KakaoTalk.
- [x] Unit tests cover logout decision logic and login-screen success detection.

## Tasks

- [x] 1. Add issue-aligned Swift tests for logout decision and parsing behavior.
- [x] 2. Implement a testable logout automation seam in `KakaoCore`.
- [x] 3. Add the `logout` CLI command and register it in the root command list.
- [x] 4. Record command behavior in work tracking and CLI help output without touching unrelated README edits.
- [x] 5. Run tests and manual verification for logout behavior.

## Notes

- Logout success now keys off the login window becoming visible because KakaoTalk's menu bar can keep showing `Log out` briefly after the app has already returned to the login screen.
