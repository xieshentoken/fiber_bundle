# Public source privacy review

This publication derives from the frozen Fiber bundle 0.6.0a2 source snapshot c5fab0516cc7. The running local application uses its original installation and configuration.

The public copy contains application source, example configuration, tests, synthetic fixtures, bundled public fonts and their licenses, and development documentation. Local account paths and knowledge-base identifiers use example values. Local task journals have been replaced with source-oriented maintenance documentation.

Credentials, user configuration, knowledge bases, original messages, SQLite databases, model secrets, browser profiles, screenshots, application bundles, backups and local Git history are excluded from the uploaded snapshot.

Third-party copyright notices, public source provenance and synthetic security-test inputs are preserved. The Office, PDF, image and video fixtures were inspected as synthetic test assets. Font files and application logos match the existing public repository.

Gitleaks 8.30.1 was run on the publication content. Its default scan found seven reviewed non-secret values, consisting of six public SHA-256 file fingerprints and one literal browser preference namespace. The included .gitleaks.toml limits exceptions to those exact values in their specific files. The reviewed scan has zero remaining findings.

Publication validation includes Python syntax and JSON parsing, focused Python integration tests, frontend API and intake tests, and a second privacy scan of the staged content. Validation uses isolated fixtures. Live-provider fresh-message acceptance remains a separate operational check.

The publication copy passed 186 focused Python tests with three optional compiler-runtime tests skipped, and 28 frontend tests. All 1,601 Python source files and public JSON files passed syntax parsing.
