# Storage migration supervision

An in-process drain proves only that runtime's accepted work has stopped. It cannot prove that an older Host process has stopped publishing encrypted files. Migration therefore needs both a stable inventory and an exact `OfflineSourceLease` issued by a supervisor that owns the complete consumer and launcher lineage. A PID list, checkbox, receipt file, directory pin or runtime drain is insufficient.

`CoreRuntime.quiesce_storage_migration` parks ingress, background work and administration, excludes its exact migration requester, drains other accepted work and closes the SQLite executor. It does not call the requesting task's `core.close()`. Its nonserializable `RuntimeStorageLease` expires with the maintenance scope and cannot authorize the filesystem transfer. A failed drain leaves maintenance unavailable and issues no lease.

The implemented `SyntheticSourceSupervisor` is a closed deployment adapter for a newly created synthetic source. Its only launcher starts a fixed, non-spawning Python worker with synthetic key material. The supervisor retains the fresh Windows process HANDLE and verifies PID and process creation time through that handle. A worker encrypts before waiting; migration refuses while it is alive. A lease requires the supervisor's own completed `q` stop protocol, exact successful exit code zero and acknowledgment, bound to that consumer's fresh identity and launch generation, complete known launcher coverage and the cross-process maintenance lock. An exited process alone, even exit zero, is insufficient. Abnormal exit, cancellation, stop I/O failure, unknown exit or lost handle identity permanently poison that supervisor session, including when the caller catches the stop error. Launching is fenced throughout maintenance and permanently disabled when that source retires. A coverage gap or generation change invalidates the proof. This tests the public migration algorithm, including the delayed file-writer hazard; it does not establish authority over an existing deployment.

There is currently no complete deployment adapter for an arbitrary existing AstrBot installation. The operator entry point deliberately returns `offline_supervisor_unavailable` for execution. Do not stop a real Host, infer its lineage or install a permissive adapter to make this command succeed.

From the parent of the installed plugin package, the operator command is:

```powershell
python -m astrbot_plugin_yomihime_game_link.scripts.migrate_private_storage plan --data-root C:\absolute\plugin-data
```

Planning does not copy or change database, key, secret, selector or migration-journal content. Windows layout resolution can create empty identity-owned private scaffolding before producing a plan or refusing execution. The administrative plan API, which already holds a resolved layout, has no such layout-initialization step. Public results contain safe status and inventory identifiers, not paths or key material.

Every SQLite executor transaction checks the expected storage epoch on entry, statement execution and commit. Synchronous startup migrations also check at actual commit. A fenced source or changed epoch refuses new work and rolls back pending effects. The transfer algorithm additionally installs source write-denial triggers to fence old database writers; those triggers cannot substitute for the supervisor's proof that delayed filesystem writers have exited.

The native synthetic process, filesystem and HTTP tests use isolated storage and mock transport. They do not claim a real Host installation, production migration, business API or IM acceptance.
