# Round 17 evidence addendum — scheduled backup wrapper

After the request was frozen, the internal review challenged whether the
installed recurring wrapper—not only its underlying backup script—persisted
the package-database flags.

The repository installer was re-run with:

```text
QDARTE_TASKQ_CONTACT_BACKUP_ENABLED=true
QDARTE_TASKQ_CONTACT_DB=qdarte_contact_verify
```

It installed LaunchAgent `io.outlabs.qdarte-production-backup` for 03:15 and
wrote both values to the host-owned mode-0600 wrapper environment. The exact
installed wrapper then ran successfully at `20260722-191547` and:

- created one API manifest containing `qdarteapi.dump`,
  `qdarte_contact_verify.dump`, `globals.sql`, and checksums;
- copied that set and the Intake set to the external Server87 roots;
- uploaded all five API/package files and all four Intake files to the
  configured object-storage prefixes; and
- reported the contact taskq dump as included before exiting zero.

This adds backup evidence only. The deployed API code remains `65fbd22`, mode
remains `legacy`, the package queue remains paused and empty, the worker and
egress services remain absent, and no package job, provider call, result
application, direct-lane mutation, or C7-02 action occurred.

QDarte API documentation commit `4de92288b4f076740447982af60df9112102d46a`
records the same wrapper proof. Review it as an additive evidence tip after the
request's pinned `3303126`; no source or deployed image changed.
