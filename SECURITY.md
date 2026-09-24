# Security Policy

Do not report production data, credentials, model weights, private endpoints, or customer identifiers through public issues.

Before publishing a change, run `python scripts/release_guard.py` and inspect the complete Git diff. If a sensitive asset has entered Git history, deleting the working-tree file is insufficient; rewrite the history before publication.

Persistent-mode checkpoints use AES-256-GCM with a key injected through `GRIDCAST_CHECKPOINT_KEY`. Never add that key or `.gridcast-state/` to Git. SQLite metadata, database size, file names, WAL/SHM existence and access times are not encrypted. Restrict the state directory, key, backups, and access to status APIs. The public sample has no authentication and is intended for local demonstration only.
