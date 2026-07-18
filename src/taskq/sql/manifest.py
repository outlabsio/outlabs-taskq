"""Machine-readable PostgreSQL catalog manifest for SQL contract 0.1.1.

The canonical prose contract remains ``docs/Task Queue 0.1 Function
Manifest.md``.  This module is its executable catalog projection: the verifier
compares these closed sets and attributes with ``pg_catalog`` without deriving
expectations from the live installation or from migration SQL.
"""

from __future__ import annotations

from dataclasses import dataclass

CONTRACT_VERSION = "0.1.1"
SCHEMA_OWNER = "taskq_owner"
PINNED_SEARCH_PATH = ("pg_catalog", "taskq", "pg_temp")

ROLES = (
    "taskq_owner",
    "taskq_producer",
    "taskq_runner",
    "taskq_observer",
    "taskq_operator",
    "taskq_housekeeper",
)
ROLE_CONFIGS = {
    "taskq_owner": frozenset(),
    "taskq_producer": frozenset(
        {"statement_timeout=30s", "idle_in_transaction_session_timeout=10s"}
    ),
    "taskq_runner": frozenset({"statement_timeout=30s", "idle_in_transaction_session_timeout=10s"}),
    "taskq_observer": frozenset(
        {"statement_timeout=30s", "idle_in_transaction_session_timeout=10s"}
    ),
    "taskq_operator": frozenset(
        {"statement_timeout=30s", "idle_in_transaction_session_timeout=10s"}
    ),
    "taskq_housekeeper": frozenset(),
}

TABLES = frozenset(
    {
        "concurrency_limits",
        "control_state",
        "job_attempts",
        "job_deps",
        "job_events",
        "jobs",
        "meta",
        "queues",
        "schema_migrations",
        "workers",
        "workflows",
    }
)
VIEWS = frozenset({"dead_jobs", "queue_stats", "worker_status"})
SEQUENCES = frozenset({"job_events_id_seq"})

# relname -> (column count, digest of ordered name/type/nullability/default rows)
TABLE_SHAPES = {
    "concurrency_limits": (4, "75911ee35d8add1f962fa83dc55fb7b0"),
    "control_state": (5, "d4b439e011e97384b2e2ae638dcc570e"),
    "job_attempts": (10, "c77285741656eeebe691db4f3e40ae29"),
    "job_deps": (3, "9d2a532798d70fa2514644b7a61da3c7"),
    "job_events": (8, "fee387eec268693cd507c443a58e1322"),
    "jobs": (38, "cb58712f28f993c5ef35a672be3f2da2"),
    "meta": (3, "6b0aa3a5745ebdd662479daa8c766d1d"),
    "queues": (15, "8b4245eace4fba779059cd86de2cdea2"),
    "schema_migrations": (4, "69a0d325516891e9b309ec0d42be5f05"),
    "workers": (9, "25f0d3e2a63909dd4c52719c1f53bae4"),
    "workflows": (10, "13447b7e9f326989906a2f341a0c6dc8"),
}

# relname -> (constraint count, digest of ordered name/type/definition rows)
CONSTRAINTS = {
    "concurrency_limits": (6, "3f77e9dafb7dd2811875a17c7b7ffd3c"),
    "control_state": (2, "beb0982d42ce624222df05859e6e178a"),
    "job_attempts": (9, "9267c1981e975abd52c56379f09a95b5"),
    "job_deps": (6, "c194d152e93008dca2eba839ea1a3bc2"),
    "job_events": (6, "b3e092ddcf3bf7401c36fb0743b6bcc4"),
    "jobs": (34, "31cdfed49f25b22c5a1a73ec9d097116"),
    "meta": (4, "df64636c92f6d9e105aa5b91f40fadd0"),
    "queues": (21, "6cf33a141c254139c477ec5f7d47c20b"),
    "schema_migrations": (2, "194f73c9b925a5b725b9d4790c959ee0"),
    "workers": (5, "f15ddafbc0c52e082d989d4c3e4d1dd2"),
    "workflows": (9, "9f619cfe7bd8859881cb2d189b5102d6"),
}

INDEXES = {
    "concurrency_limits_pkey": "4451cd41ab2a31b52ae1a34d69435e19",
    "control_state_pkey": "43b5b6e33a824e152859f4ef9ccbb046",
    "job_attempts_job_idx": "ba54a88c3bf510cc36006927db2537cc",
    "job_attempts_pkey": "92b24894eba1fd618bc2ee26a429986e",
    "job_deps_pkey": "8a56437cde35ae91cdd9f30a658744aa",
    "job_deps_reverse_idx": "f7c276b5faaef7af08cbc914478616d4",
    "job_events_job_idx": "aa405717d334ce25891c6ca9d870ecfc",
    "job_events_pkey": "f7e772aee8a50604aa5d3b102b6cea7d",
    "job_events_time_brin": "e60dbd8f51af7980da0a9a7e76b1ecdd",
    "jobs_affinity_idx": "b11b463a3c128b45b3e9da6aea3f14ee",
    "jobs_claim_idx": "d7a7f8cdbdf8d939a0aec58bf770b829",
    "jobs_finished_idx": "bad49c3e9743bc3636d9ab5e08192bdf",
    "jobs_idem_uq": "f98d23c969575471f8495ad15cf52e7c",
    "jobs_pkey": "b59e69add87d0884c846718de43ad608",
    "jobs_running_idx": "afaae7903e591ffc4b37aa0803909d8e",
    "jobs_workflow_idx": "1898c697bd3b04b15de0c0aa340bc0c3",
    "meta_pkey": "0d779a67c6f4038a1c416b7775e6c96e",
    "queues_pkey": "afbb7fc868e58dcae6742808a3d01d91",
    "schema_migrations_pkey": "c72ebe664c34fb56088d702ab3bb8864",
    "uq_job_attempts_running": "dc6b831d4b3259c15d2a6c7f68b6794a",
    "workers_pkey": "c800776a247ce583b0e856c87493c7c4",
    "workers_seen_idx": "c5414b98a1d4a1df241eacf53433412e",
    "workflows_open_idx": "5478259e72cc66459918dccbc11126ac",
    "workflows_pkey": "0c296a3e7b13c6006a18b95cd1c8a451",
    "workflows_workflow_key_key": "53dcbddde181e0115456404141f742d7",
}

VIEW_DEFINITIONS = {
    "dead_jobs": "a1d7c075defc79dd3863aed346024101",
    "queue_stats": "76c6cf76aa0accc11b8c9a1b07a54d9a",
    "worker_status": "7e1a77b0bc8380895aaf512c29e6f1d1",
}

COMPOSITES = {
    "claim_batch": (("state", "text"), ("jobs", "taskq.claimed_job[]")),
    "claimed_job": (
        ("job_id", "uuid"),
        ("queue", "text"),
        ("job_type", "text"),
        ("priority", "smallint"),
        ("payload", "jsonb"),
        ("headers", "jsonb"),
        ("progress", "jsonb"),
        ("attempt_id", "uuid"),
        ("attempt_number", "integer"),
        ("failure_count", "smallint"),
        ("max_attempts", "smallint"),
        ("lease_expires_at", "timestamp with time zone"),
        ("workflow_id", "uuid"),
        ("step_key", "text"),
    ),
    "settle_result": (
        ("result", "text"),
        ("job_status", "text"),
        ("scheduled_at", "timestamp with time zone"),
    ),
}


@dataclass(frozen=True, slots=True)
class FunctionSpec:
    identity: str
    arguments: str
    result: str
    language: str
    volatility: str
    parallel: str
    grants: frozenset[str]


_FUNCTION_ROWS = r"""
taskq.backoff_seconds(text,integer,integer,integer)|p_mode text, p_base integer, p_cap integer, p_failures integer|integer|sql|v|u|
taskq.cancel_job(uuid,text,text)|p_job_id uuid, p_actor text, p_reason text DEFAULT NULL::text|TABLE(result text, job_status text)|plpgsql|v|u|taskq_operator
taskq.cancel_running_job(uuid,uuid,text,text)|p_job_id uuid, p_attempt_id uuid, p_worker_id text, p_reason text|taskq.settle_result|plpgsql|v|u|taskq_runner
taskq.claim_janitor_due()||boolean|plpgsql|v|u|
taskq.claim_jobs(text,text,integer,text[],integer,text,uuid)|p_queue text, p_worker_id text, p_batch integer DEFAULT 1, p_job_types text[] DEFAULT NULL::text[], p_lease_seconds integer DEFAULT NULL::integer, p_affinity_key text DEFAULT NULL::text, p_job_id uuid DEFAULT NULL::uuid|taskq.claim_batch|plpgsql|v|u|taskq_runner
taskq.complete_job(uuid,uuid,text,jsonb,jsonb,jsonb)|p_job_id uuid, p_attempt_id uuid, p_worker_id text, p_result jsonb DEFAULT NULL::jsonb, p_stats jsonb DEFAULT NULL::jsonb, p_followups jsonb DEFAULT NULL::jsonb|taskq.settle_result|plpgsql|v|u|taskq_runner
taskq.emit_event(uuid,uuid,text,text,text,jsonb)|p_job_id uuid, p_attempt_id uuid, p_event_type text, p_actor text, p_message text, p_data jsonb DEFAULT NULL::jsonb|void|sql|v|u|
taskq.enqueue_many(text,jsonb)|p_queue text, p_jobs jsonb|TABLE(input_index integer, job_id uuid, outcome text)|plpgsql|v|u|taskq_producer
taskq.enqueue(text,text,jsonb,smallint,timestamp with time zone,text,text,text,smallint,integer,text,integer,integer,uuid[],uuid,text,uuid,jsonb)|p_queue text, p_job_type text, p_payload jsonb DEFAULT '{}'::jsonb, p_priority smallint DEFAULT NULL::smallint, p_scheduled_at timestamp with time zone DEFAULT NULL::timestamp with time zone, p_idempotency_key text DEFAULT NULL::text, p_concurrency_key text DEFAULT NULL::text, p_affinity_key text DEFAULT NULL::text, p_max_attempts smallint DEFAULT NULL::smallint, p_lease_seconds integer DEFAULT NULL::integer, p_backoff_mode text DEFAULT NULL::text, p_backoff_base integer DEFAULT NULL::integer, p_backoff_cap integer DEFAULT NULL::integer, p_depends_on uuid[] DEFAULT NULL::uuid[], p_workflow_id uuid DEFAULT NULL::uuid, p_step_key text DEFAULT NULL::text, p_parent_job_id uuid DEFAULT NULL::uuid, p_headers jsonb DEFAULT NULL::jsonb|TABLE(job_id uuid, created boolean)|plpgsql|v|u|taskq_producer
taskq.ensure_queue(text,jsonb,text)|p_name text, p_profile jsonb DEFAULT '{}'::jsonb, p_actor text DEFAULT NULL::text|TABLE(result text, profile jsonb)|plpgsql|v|u|taskq_operator
taskq.expire_job(uuid,text)|p_job_id uuid, p_actor text|text|plpgsql|v|u|taskq_operator
taskq.expire_worker_leases(text,text)|p_worker_id text, p_actor text|jsonb|plpgsql|v|u|taskq_operator
taskq.fail_job(uuid,uuid,text,text,boolean,integer,jsonb,jsonb)|p_job_id uuid, p_attempt_id uuid, p_worker_id text, p_error text, p_retryable boolean DEFAULT true, p_retry_after_seconds integer DEFAULT NULL::integer, p_progress jsonb DEFAULT NULL::jsonb, p_stats jsonb DEFAULT NULL::jsonb|taskq.settle_result|plpgsql|v|u|taskq_runner
taskq.finalize_cancel_stragglers(integer)|p_limit integer|integer|plpgsql|v|u|
taskq.get_authorization_projection(uuid)|p_job_id uuid|TABLE(job_id uuid, queue text, job_type text, status text)|sql|s|u|taskq_observer
taskq.get_contract_meta()||TABLE(contract_version text, capabilities jsonb)|sql|s|u|taskq_observer
taskq.get_job(uuid,boolean,boolean,boolean,boolean)|p_job_id uuid, p_include_error boolean DEFAULT false, p_include_result boolean DEFAULT false, p_include_progress boolean DEFAULT false, p_include_payload boolean DEFAULT false|TABLE(job_id uuid, queue text, job_type text, status text, outcome text, priority smallint, attempt_count smallint, failure_count smallint, max_attempts smallint, created_at timestamp with time zone, scheduled_at timestamp with time zone, started_at timestamp with time zone, finished_at timestamp with time zone, updated_at timestamp with time zone, error text, result jsonb, progress jsonb, payload jsonb)|sql|s|u|taskq_observer
taskq.get_queue_stats(text)|p_queue text DEFAULT NULL::text|TABLE(as_of timestamp with time zone, queue text, stats jsonb)|sql|s|u|taskq_observer
taskq.has_capability(text)|p_name text|boolean|sql|s|u|
taskq.heartbeat(uuid,uuid,text,integer,jsonb,jsonb)|p_job_id uuid, p_attempt_id uuid, p_worker_id text, p_lease_seconds integer DEFAULT NULL::integer, p_progress jsonb DEFAULT NULL::jsonb, p_stats jsonb DEFAULT NULL::jsonb|TABLE(ok boolean, cancel_requested boolean, lease_expires_at timestamp with time zone)|plpgsql|v|u|taskq_runner
taskq.janitor()||jsonb|plpgsql|v|u|taskq_housekeeper,taskq_operator
taskq.metrics()||TABLE(name text, labels jsonb, value numeric)|sql|s|u|taskq_observer
taskq.pause_queue(text,text,text)|p_name text, p_actor text, p_reason text DEFAULT NULL::text|text|plpgsql|v|u|taskq_operator
taskq.purge_queued(text,integer,text,text)|p_queue text, p_limit integer, p_actor text, p_reason text DEFAULT NULL::text|integer|plpgsql|v|u|taskq_operator
taskq.reap_expired(integer)|p_limit integer DEFAULT 100|integer|plpgsql|v|u|
taskq.reap_job(uuid)|p_job_id uuid|boolean|plpgsql|v|u|
taskq.redrive_failed(text,integer,text)|p_queue text, p_limit integer, p_actor text|TABLE(redriven integer, skipped integer)|plpgsql|v|u|taskq_operator
taskq.redrive_job(uuid,text,boolean)|p_job_id uuid, p_actor text, p_reset_progress boolean DEFAULT false|boolean|plpgsql|v|u|taskq_operator
taskq.refresh_stats_snapshot()||void|plpgsql|v|u|
taskq.release_job(uuid,uuid,text,text,integer,jsonb)|p_job_id uuid, p_attempt_id uuid, p_worker_id text, p_cause text DEFAULT 'released'::text, p_delay_seconds integer DEFAULT 0, p_progress jsonb DEFAULT NULL::jsonb|taskq.settle_result|plpgsql|v|u|taskq_runner
taskq.reprioritize(uuid,smallint,text)|p_job_id uuid, p_priority smallint, p_actor text|text|plpgsql|v|u|taskq_operator
taskq.request_worker_shutdown(text,text,text)|p_worker_id text, p_queue text, p_actor text|integer|plpgsql|v|u|taskq_operator
taskq.resume_queue(text,text)|p_name text, p_actor text|text|plpgsql|v|u|taskq_operator
taskq.run_now(uuid,text)|p_job_id uuid, p_actor text|text|plpgsql|v|u|taskq_operator
taskq.set_concurrency_limit(text,integer,text)|p_key text, p_max_running integer, p_actor text|text|plpgsql|v|u|taskq_operator
taskq.snooze_job(uuid,uuid,text,integer,text,jsonb)|p_job_id uuid, p_attempt_id uuid, p_worker_id text, p_delay_seconds integer, p_reason text DEFAULT NULL::text, p_progress jsonb DEFAULT NULL::jsonb|taskq.settle_result|plpgsql|v|u|taskq_runner
taskq.tick(integer)|p_reap_limit integer DEFAULT 200|jsonb|plpgsql|v|u|taskq_housekeeper,taskq_operator
taskq.truncate_utf8(text,integer)|p_value text, p_max_bytes integer|text|plpgsql|i|s|
taskq.uuid7()||uuid|sql|v|s|
taskq.worker_heartbeat(text,text[],text,integer,text,jsonb)|p_worker_id text, p_queues text[], p_hostname text DEFAULT NULL::text, p_pid integer DEFAULT NULL::integer, p_version text DEFAULT NULL::text, p_meta jsonb DEFAULT NULL::jsonb|TABLE(shutdown_requested boolean)|plpgsql|v|u|taskq_runner
""".strip()


def _parse_functions() -> dict[str, FunctionSpec]:
    result: dict[str, FunctionSpec] = {}
    for line in _FUNCTION_ROWS.splitlines():
        identity, arguments, returns, language, volatility, parallel, grants = line.split("|")
        result[identity] = FunctionSpec(
            identity=identity,
            arguments=arguments,
            result=returns,
            language=language,
            volatility=volatility,
            parallel=parallel,
            grants=frozenset(filter(None, grants.split(","))),
        )
    return result


FUNCTIONS = _parse_functions()
PUBLIC_FUNCTIONS = frozenset(identity for identity, spec in FUNCTIONS.items() if spec.grants)

# Closed registered-error projection from the canonical function manifest and
# Protocol v1. Empty sets are meaningful: those functions have no public TQ
# exception outcome. R3-F04's executable vectors assert this map is complete.
PUBLIC_ERRORS = {
    "taskq.cancel_job(uuid,text,text)": frozenset({"TQ001"}),
    "taskq.cancel_running_job(uuid,uuid,text,text)": frozenset(),
    "taskq.claim_jobs(text,text,integer,text[],integer,text,uuid)": frozenset({"TQ422"}),
    "taskq.complete_job(uuid,uuid,text,jsonb,jsonb,jsonb)": frozenset({"TQ422", "TQ501"}),
    "taskq.enqueue_many(text,jsonb)": frozenset({"TQ001", "TQ422", "TQ429", "TQ500"}),
    "taskq.enqueue(text,text,jsonb,smallint,timestamp with time zone,text,text,text,smallint,integer,text,integer,integer,uuid[],uuid,text,uuid,jsonb)": frozenset(
        {"TQ001", "TQ422", "TQ429", "TQ500", "TQ501"}
    ),
    "taskq.ensure_queue(text,jsonb,text)": frozenset({"TQ422"}),
    "taskq.expire_job(uuid,text)": frozenset({"TQ001"}),
    "taskq.expire_worker_leases(text,text)": frozenset(),
    "taskq.fail_job(uuid,uuid,text,text,boolean,integer,jsonb,jsonb)": frozenset({"TQ422"}),
    "taskq.get_authorization_projection(uuid)": frozenset(),
    "taskq.get_contract_meta()": frozenset(),
    "taskq.get_job(uuid,boolean,boolean,boolean,boolean)": frozenset(),
    "taskq.get_queue_stats(text)": frozenset(),
    "taskq.heartbeat(uuid,uuid,text,integer,jsonb,jsonb)": frozenset({"TQ422"}),
    "taskq.janitor()": frozenset(),
    "taskq.metrics()": frozenset(),
    "taskq.pause_queue(text,text,text)": frozenset({"TQ001"}),
    "taskq.purge_queued(text,integer,text,text)": frozenset({"TQ001", "TQ422"}),
    "taskq.redrive_failed(text,integer,text)": frozenset({"TQ422"}),
    "taskq.redrive_job(uuid,text,boolean)": frozenset({"TQ001", "TQ409"}),
    "taskq.release_job(uuid,uuid,text,text,integer,jsonb)": frozenset({"TQ422"}),
    "taskq.reprioritize(uuid,smallint,text)": frozenset({"TQ001", "TQ409", "TQ422"}),
    "taskq.request_worker_shutdown(text,text,text)": frozenset(),
    "taskq.resume_queue(text,text)": frozenset({"TQ001"}),
    "taskq.run_now(uuid,text)": frozenset({"TQ001", "TQ409"}),
    "taskq.set_concurrency_limit(text,integer,text)": frozenset({"TQ422"}),
    "taskq.snooze_job(uuid,uuid,text,integer,text,jsonb)": frozenset({"TQ422"}),
    "taskq.tick(integer)": frozenset({"TQ422"}),
    "taskq.worker_heartbeat(text,text[],text,integer,text,jsonb)": frozenset({"TQ422"}),
}

REPLAY_RULES = {
    identity: (
        "verb-aware attempt replay"
        if identity
        in {
            "taskq.cancel_running_job(uuid,uuid,text,text)",
            "taskq.complete_job(uuid,uuid,text,jsonb,jsonb,jsonb)",
            "taskq.fail_job(uuid,uuid,text,text,boolean,integer,jsonb,jsonb)",
            "taskq.release_job(uuid,uuid,text,text,integer,jsonb)",
            "taskq.snooze_job(uuid,uuid,text,integer,text,jsonb)",
        }
        else "state-derived idempotency or documented repeat"
    )
    for identity in PUBLIC_FUNCTIONS
}

# Mutable values are deliberately not frozen; only required seed identities and
# the immutable contract/capability values are verified.
CONTROL_SEED_KEYS = frozenset({"tick", "janitor_daily", "stats_snapshot"})
META_SEEDS = {"contract_version": '"0.1.1"', "capabilities": '{"active": []}'}
