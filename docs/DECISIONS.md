# Architecture decisions

UI components handle presentation and interaction. HTTP routes and IPC validate inputs and permissions. Feature modules own business operations and persisted state.

SQLite records task and publication identity. Content hashes bind original material and verified outputs. Recovery reuses completed steps under the current authorization and cancellation checks.

Model and external service requests use configured providers, explicit budgets and audit records. Cached indices support retrieval inside the granted vault scope.
