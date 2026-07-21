# Round 10 targeted delta acceptance request

## Assignment

Perform only the targeted delta check authorized by the Round-10 response.
Decide whether R10-01 through R10-04 are **ACCEPTED** or remain **BLOCKED**.
Do not reopen the accepted host-adoption architecture, contracts, migrations,
authorization model, or read-model design.

This review authorizes no artifact publication, host change, dependency pin,
deployment, database/IAM mutation, queue-profile change, producer/consumer
action, retirement work, UI work, side-effecting lane, or Stage-5 pilot.

## Pinned identity

- Repository: `~/Documents/projects/outlabs-taskq`
- Baseline: `8b2b0e0` (Round-10 response recorded)
- Delta tip: `3af5559` on `main`
- Immutable response SHA-256:
  `285e134320715a4d67aaafa7079bb6f78a9787267d8777d6188ec48558ac44c6`

Derive exact commit/tree identities and changed paths from Git before trusting
this request. Confirm the user-owned ADR-018/UI documentation batch remains
outside the committed delta and is neither staged nor adjudicated.

Write only `docs/design-review-10/DELTA-RESPONSE.md`, leave it uncommitted, and
modify nothing else.

## Required checks

1. **R10-01:** prove the plan is executable per artifact. `a3`, built from
   `40aa9b5` plus only a release-version change, applies/verifies exactly
   0004→0005 and ends at contract 0.1.4 with capabilities exactly
   `{"active":[]}`. `a4`, built from accepted `1610b5a` plus only a
   release-version change, alone applies/verifies 0006 and ends at exactly
   `{"active":["read_model_list_ready"]}`. No artifact may be asked to
   verify a state its own manifest cannot accept.
2. **R10-04:** inspect a3's actual reserved H-08/H-11 disposition. It must be
   a typed TQ501 responder, absent from OpenAPI, with no generated client
   method—not a claimed 404/no-command condition. Confirm the post-D a4→a3
   rollback remains a zero-DML application switch whose ready request returns
   that typed bridge response.
3. **R10-02:** confirm a4's source identity is pinned as `1610b5a` with the
   same isolated release-version discipline as a3; reject any floating tip,
   local path, branch, range, or reused artifact identity.
4. **R10-03:** confirm C now requires a current backup test-restored once to a
   disposable target before the first production contract migration, and that
   the wording honestly limits the claim to that backup artifact rather than
   claiming general restore/PITR completion.
5. Confirm the corrected range is documentation/board only, the response hash
   is byte-identical, no Contract question exists, and all original non-goals
   remain closed.

## Required disposition

State the exact commands/identities and any environmental limit. If all five
checks pass, state **READY — the first-host read-model A→E sequence may begin**.
READY permits only that separately frozen sequence; each production migration
step still requires its listed evidence. It does not authorize `running` or
`finished`, UI work, retirement, side-effecting lanes, or Stage 5.
