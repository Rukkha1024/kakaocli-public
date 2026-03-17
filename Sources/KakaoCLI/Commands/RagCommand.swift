import ArgumentParser
import Foundation

struct RagCommand: ParsableCommand {
    static let configuration = CommandConfiguration(
        commandName: "rag",
        abstract: "Manage the RAG pipeline for semantic search",
        discussion: """
        Subcommands (handled by the Python backend):
          init      Full history ingest + semantic index build
          update    Incremental sync + index update
          query     Query the RAG store
          status    Show RAG store and service status

        Examples:
          kakaocli rag init
          kakaocli rag query "회의 일정"
          kakaocli rag status
        """
    )

    @Argument(parsing: .captureForPassthrough)
    var arguments: [String] = []

    func run() throws {
        // Resolve repo root from binary path
        // Binary is at .build/release/kakaocli → repo root is 3 levels up
        // Do NOT resolve symlinks: .build/release is a symlink to
        // .build/arm64-apple-macosx/release, which would add an extra level.
        let binaryPath = URL(fileURLWithPath: CommandLine.arguments[0]).standardizedFileURL
        let repoRoot = binaryPath
            .deletingLastPathComponent()  // release/
            .deletingLastPathComponent()  // .build/
            .deletingLastPathComponent()  // repo root

        // Resolve venv Python
        let venvPython = repoRoot.appendingPathComponent(".venv/bin/python").path
        guard FileManager.default.isExecutableFile(atPath: venvPython) else {
            fputs("Error: Python venv not found at \(venvPython)\n", stderr)
            fputs("Run: ./bin/install-kakaocli\n", stderr)
            throw ExitCode.failure
        }

        // Resolve cli.py
        let cliScript = repoRoot.appendingPathComponent("tools/live_rag/cli.py").path
        guard FileManager.default.fileExists(atPath: cliScript) else {
            fputs("Error: RAG CLI script not found at \(cliScript)\n", stderr)
            throw ExitCode.failure
        }

        // Spawn Python process
        let process = Process()
        process.executableURL = URL(fileURLWithPath: venvPython)
        process.arguments = [cliScript, "--binary", binaryPath.path] + arguments
        process.currentDirectoryURL = repoRoot

        // Forward environment + set LIVE_RAG_PYTHON
        var env = ProcessInfo.processInfo.environment
        env["LIVE_RAG_PYTHON"] = venvPython
        process.environment = env

        try process.run()
        process.waitUntilExit()

        if process.terminationStatus != 0 {
            throw ExitCode(process.terminationStatus)
        }
    }
}
