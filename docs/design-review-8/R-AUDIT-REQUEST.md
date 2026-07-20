# External targeted audit — post-Stage-4 host reconciliation candidate

## Assignment

Independently audit the S4-POST-R1 ledger and S4-POST-R2 exact-tree two-parent candidate. Return
**READY** only if the candidate can safely advance host `main` by fast-forward and proceed to the
separately controlled deployment-branch cutover slice. Otherwise return **BLOCKED** with the
smallest preconditions.

This audit authorizes no ref movement, pull-request merge, default-branch change, Coolify change,
deployment, database/environment mutation, production probe, legacy retirement, branch deletion,
side-effecting-lane migration, or Stage 5.

## Repositories and frozen refs

- Taskq authority: `~/Documents/projects/outlabs-taskq`
- Host: `~/Documents/projects/outlabsAPI`
- Accepted host evidence parent: `9348f85adec3a77c5eab0a313feeed0853b315ad`
- Old host `main`: `7df6b7f3367602a658e09b8cb94121fc1eaa0493`
- Deployed `origin/staging-prep`: `3f50b7d46601c407d4c184582618072031a8473a`
- R1/R2 evidence branch: `origin/codex/s4-03-cycle1` at `a2500a4`
- Candidate branch: `origin/codex/s4-post-r2-reconcile`
- Claimed candidate: `2ed736b6bfadff4b4ba1a1384224b32e03d5517c`
- Claimed candidate/base tree: `ded6d43ace2fced88600f19128dedcfcfe9fe0be`

Fetch all refs and tags before checking them. Do not trust any identity in this request until Git
reproduces it.

## Authority read order

1. taskq `AGENTS.md` and `docs/README.md`;
2. Tier-0 Protocol v1 and Function Manifest 0.1.2;
3. accepted ADRs;
4. `TASKS.md` and the Build Plan;
5. the Host Branch Reconciliation Specification;
6. immutable Round-8 request and response;
7. host `docs/taskq-s4-post-r1-reconciliation-ledger.md` at evidence ref `a2500a4`;
8. host `docs/taskq-s4-post-r2-candidate.md` at the same evidence ref; and
9. the actual host graph, trees, source, tests, lock, images, and remote refs.

Tier 0 and ADRs win. Report a Contract question if reconciliation requires taskq SQL, wire,
permission, role, or capability changes. Never name third-party queue projects in the response.

## Audit A — regenerate R1 without trusting the ledger

Independently derive:

- merge bases among current `main`, `staging-prep`, accepted evidence, and candidate;
- every commit reachable from exactly one of `9348f85` and `7df6b7f`;
- the claimed 27 production/evidence-only and three default-only counts;
- all changed surfaces for each one-sided commit; and
- whether every default-only intent is semantically present or superseded with no missing forward
  port.

For every R1 row, verify the disposition against source and its named wrong-disposition oracle.
Commit-message similarity is inadmissible. Recompute the four list/path-manifest SHA-256 values in
the ledger. Any missing row, unproven semantic equivalence, default-only security/migration fix, or
required forward port blocks READY and invalidates the candidate tree.

## Audit B — exact two-parent construction

Using raw Git objects, prove or falsify:

1. candidate commit is exactly `2ed736b`;
2. parent 1 is exactly `9348f85` and parent 2 exactly `7df6b7f`, in that order;
3. candidate tree and accepted-parent tree both equal `ded6d43`;
4. recursive raw, name-status, mode, symlink, deletion, and content diffs from `9348f85` are empty;
5. zero forward-port and zero unclassified-path claims remain true;
6. both accepted histories and current `origin/main` are ancestors of the candidate;
7. advancing `main` to the candidate is possible with fast-forward-only semantics; and
8. the candidate commit carries the required trailer.

Do not accept a summary diff. Git tree identity is the primary oracle; explicit empty recursive
diffs are the independent oracle.

## Audit C — rollback tag integrity

Verify all tags exist locally and remotely as annotated tag objects, then independently peel them:

| Tag | Required target |
|---|---|
| `s4-post-r2-old-main-7df6b7f` | `7df6b7f3367602a658e09b8cb94121fc1eaa0493` |
| `s4-post-r2-deployed-3f50b7d` | `3f50b7d46601c407d4c184582618072031a8473a` |
| `s4-post-r2-evidence-9348f85` | `9348f85adec3a77c5eab0a313feeed0853b315ad` |

Compare remote tag object ids to the R2 evidence. Any lightweight, moved, missing, or incorrectly
peeled tag blocks READY. Confirm no branch was archived or deleted.

## Audit D — remote and deployment non-mutation

Verify from remote refs and available deployment evidence:

- `origin/main` is still `7df6b7f`;
- `origin/staging-prep` is still `3f50b7d`;
- candidate exists only on the non-deploying `codex/` branch;
- the evidence-only `a2500a4` commit is not a candidate parent or tree input;
- Coolify still tracks the existing production ref/revision;
- no deployment, environment/settings, database, IAM, queue, or production traffic mutation belongs
  to R1/R2; and
- no pull request or automatic branch policy can merge/deploy the candidate during the audit.

If deployment-branch status cannot be inspected safely, state that limit and require an exact
pre-move recheck. Do not open or merge a pull request.

## Audit E — gates and byte-identical-tree reasoning

Reproduce or validate:

- `uv lock --check`;
- host suite: expected 72 regular passes and five existing infrastructure skips;
- Ruff and MyPy across 64 files;
- offline Alembic full-upgrade compile;
- taskq core/HTTP/OutLabs imports and configured host import;
- API and standing-worker Docker image builds; and
- exact dependency pins for OutLabs Auth a24 and immutable taskq a2 URL/hash.

The R2 evidence did not rerun the local production-shape harness because the candidate tree is
byte-identical to accepted `9348f85`. Decide explicitly whether the immutable-tree proof plus prior
accepted harness evidence is sufficient for this ancestry-only candidate. If not, rerun the local
harness or make it a precondition. Do not touch production.

The first unconfigured `app.main` import in R2 failed because required auth configuration was
absent; the configured local-only import passed. Verify that this is the accepted fail-closed
boundary and not hidden source drift.

## Audit F — scope and next authorization

Confirm the only host mutations were:

- candidate merge commit and non-deploying candidate ref;
- three annotated rollback tags; and
- R1/R2 evidence-only documentation commits on the evidence branch.

There must be no candidate-tree source change, taskq source/SQL/migration/Tier-0/IAM change,
authoritative-ref movement, deployment, production mutation, retirement, branch deletion,
side-effecting-lane migration, or Stage-5 work.

A READY verdict opens only the next controlled slice:

1. fast-forward `main` to the accepted candidate;
2. prove `main` and candidate commit/tree identity;
3. change API and standing-worker deployment branches only under the frozen cutover/rollback
   choreography; and
4. stop for independent acceptance before any legacy retirement.

## Required response

Create only `docs/design-review-8/R-AUDIT-RESPONSE.md` in taskq; modify nothing else. Include:

1. **READY** or **BLOCKED**;
2. independently derived refs, parents, trees, ancestors, one-sided counts, and tag objects/targets;
3. findings numbered `R8A-01...` with severity, evidence, impact, smallest remediation, and owner;
4. a separate Contract-questions section, even if none;
5. explicit BR-01..05 and R8-01 dispositions for this pre-move boundary;
6. gates run and honest environmental limits;
7. exact preconditions for moving `main` or deployment branches; and
8. an explicit statement that the response authorizes no retirement, deletion, side-effecting lane,
   or Stage 5.

Leave the response uncommitted for byte-identical recording by the implementation agent.
