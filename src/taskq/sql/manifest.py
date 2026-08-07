"""Machine-readable PostgreSQL catalog manifest for SQL contract 0.6.3.

The canonical prose contract remains ``docs/Task Queue 0.1 Function
Manifest.md``.  This module is its executable catalog projection: the verifier
compares these closed sets and attributes with ``pg_catalog`` without deriving
expectations from the live installation or from migration SQL.
"""

from __future__ import annotations

from dataclasses import dataclass

CONTRACT_VERSION = "0.6.3"
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
        "admissions",
        "concurrency_limits",
        "flow_limits",
        "flow_state",
        "control_state",
        "job_attempts",
        "job_deps",
        "job_events",
        "jobs",
        "meta",
        "queue_counters",
        "queue_flow",
        "queues",
        "schedule_decisions",
        "schedule_occurrences",
        "schedules",
        "schema_migrations",
        "target_binding_events",
        "target_identity",
        "workers",
        "workflow_member_counts",
        "workflows",
    }
)
VIEWS = frozenset({"dead_jobs", "queue_stats", "worker_status"})
SEQUENCES = frozenset({"job_events_id_seq"})

# relname -> (column count, digest of ordered name/type/nullability/default rows)
TABLE_SHAPES = {
    "admissions": (16, "0315e8754989dc0eaf57c71e62b13b9c"),
    "concurrency_limits": (4, "75911ee35d8add1f962fa83dc55fb7b0"),
    "control_state": (5, "d4b439e011e97384b2e2ae638dcc570e"),
    "job_attempts": (10, "c77285741656eeebe691db4f3e40ae29"),
    "job_deps": (3, "9d2a532798d70fa2514644b7a61da3c7"),
    "job_events": (8, "fee387eec268693cd507c443a58e1322"),
    "jobs": (43, "3400624aa6584a1babd1d0c88280d1fb"),
    "meta": (3, "6b0aa3a5745ebdd662479daa8c766d1d"),
    "flow_limits": (5, "56fcabc78fdf06b02c62be3182bbcccf"),
    "flow_state": (3, "60f056634167b3b624b194567ca2a477"),
    "queue_counters": (10, "c2e43315895c6ddbac94394bd398a563"),
    "queue_flow": (19, "bfac51350b933bc3882c050c4697686e"),
    "queues": (23, "3a8dfd613184b7308c8e8fc53973e89b"),
    "schedule_decisions": (13, "ebb5c380a87c00d53e626db04a89f82c"),
    "schedule_occurrences": (7, "5ec90a35ba0383db811bf88020522004"),
    "schedules": (37, "c666ae211b1a176a7af3695f8a9b16b1"),
    "schema_migrations": (4, "69a0d325516891e9b309ec0d42be5f05"),
    "target_binding_events": (10, "94d73ea696dd9eb3f57f029eeca79698"),
    "target_identity": (9, "9c9e666464edd9c079c34f7737adfbe6"),
    "workers": (9, "25f0d3e2a63909dd4c52719c1f53bae4"),
    "workflow_member_counts": (8, "f79bc1d967e8eca2cabd56a7d4bdb132"),
    "workflows": (18, "6bc37874b900f701e146cda14552835a"),
}

# relname -> (constraint count, digest of ordered name/type/definition rows).
# PostgreSQL 18 also projects NOT NULL through pg_constraint while PostgreSQL
# 16 does not; exact column nullability is already closed by TABLE_SHAPES, so
# this axis deliberately covers the portable structural constraint kinds.
CONSTRAINTS = {
    "admissions": (11, "8f397fa6d17e508614a2979615086cf2"),
    "concurrency_limits": (3, "66936c03f772ba78965bf60edcc5dd5c"),
    "control_state": (1, "65d3c0be64a45faf70dc5ecfc465bb71"),
    "job_attempts": (3, "1b3551d273ad80b3a1914494ac750057"),
    "job_deps": (3, "fa4b0e4d226160305724e3e9fd330390"),
    "job_events": (2, "190355e5ad2160ab5d9d5adf84016b61"),
    "jobs": (21, "e92c8c5a806bf5c77211fc96bcabfd2d"),
    "meta": (1, "b8a6f433ca275e289861f29159c6d4f3"),
    "flow_limits": (4, "998303160c2c6264e4e28c4e348dbfda"),
    "flow_state": (1, "16791e93a3b47960ea20095c4abb0f10"),
    "queue_counters": (2, "44835b2b8bfcc389728ec65520ee5e7c"),
    "queue_flow": (10, "7cb7337f2f9808452741eb1a2f305496"),
    "queues": (16, "46680a8ecb4146dc9f2f6f90b3ff0959"),
    "schedule_decisions": (9, "21f272d0a93325771bf28c016c167f91"),
    "schedule_occurrences": (6, "7d7431666eaf79edefa9c3a26d0a122e"),
    "schedules": (21, "c20e70ddf516bb88d89d20a44bade146"),
    "schema_migrations": (1, "9a70b629e02d9c9c4c87285047e4c5fa"),
    "target_binding_events": (7, "f88384b3fa51c9ecbdd7de53d09ae920"),
    "target_identity": (6, "a85be9ae99c9b946792dfdec1134e0c0"),
    "workers": (1, "21a9c8f0ac7e4e770db780e76f5c2909"),
    "workflow_member_counts": (8, "a189ce4052298ca2208e6f7518a0f5a3"),
    "workflows": (11, "b35b364d2ab059ce10e9248c5dd9abfc"),
}

# ADR-034: pg_dump/pg_restore may reassociate the equivalent three-term
# conjunction in schedules_name_ck. This is the sole accepted alternate; all
# identities and the relation's constraint count remain exact.
CONSTRAINT_EQUIVALENT_DIGESTS: dict[str, frozenset[str]] = {}

INDEXES = {
    "admissions_cancelled_cleanup_idx": "027dfe0b40e8c3fc717ced0dd50cf37f",
    "admissions_pkey": "56452661b9570cc9b8582b5394288ab2",
    "admissions_queue_key_uq": "1b6398a305f52362efc9341da1b65c51",
    "admissions_receipt_cleanup_idx": "bf5e2c97a410692ca5d77b2023d2fc18",
    "admissions_reservation_cleanup_idx": "3a23e0123c6bad7de55de7114615d33d",
    "flow_limits_pkey": "702de8132b2affb2b26861d03ebd4cb5",
    "flow_state_pkey": "7b89bec16b43d992923391f484f5dcfa",
    "concurrency_limits_pkey": "4451cd41ab2a31b52ae1a34d69435e19",
    "control_state_pkey": "43b5b6e33a824e152859f4ef9ccbb046",
    "job_attempts_job_idx": "ba54a88c3bf510cc36006927db2537cc",
    "job_attempts_pkey": "92b24894eba1fd618bc2ee26a429986e",
    "job_deps_pkey": "8a56437cde35ae91cdd9f30a658744aa",
    "job_deps_reverse_idx": "c5ebc0d4bb7005fa51d011f3ed7205eb",
    "job_events_job_idx": "aa405717d334ce25891c6ca9d870ecfc",
    "job_events_pkey": "f7e772aee8a50604aa5d3b102b6cea7d",
    "job_events_time_brin": "e60dbd8f51af7980da0a9a7e76b1ecdd",
    "jobs_affinity_policy_idx": "71f8d3c1a6b25063b3c1e9bd7094689e",
    "jobs_admission_id_uq": "0865c3b722eec545a5f1ebfcae989d0f",
    "jobs_claim_idx": "d7a7f8cdbdf8d939a0aec58bf770b829",
    "jobs_claim_policy_idx": "46eb01bf1ce48d2ead1147847a637939",
    "jobs_finished_idx": "bad49c3e9743bc3636d9ab5e08192bdf",
    "jobs_idem_uq": "f98d23c969575471f8495ad15cf52e7c",
    "jobs_pkey": "b59e69add87d0884c846718de43ad608",
    "jobs_running_idx": "afaae7903e591ffc4b37aa0803909d8e",
    "taskq_jobs_blocked_page_idx": "68331e28cf2619c4113190b63b3854f5",
    "taskq_jobs_cancel_requested_page_idx": "52c47a56f9efe2b919ccec254d14f85d",
    "taskq_jobs_finished_page_idx": "57c29b5584e219f10a116a547cb8ca35",
    "taskq_jobs_running_page_idx": "8bccca5a06abf38572eeefbd1c1651af",
    "taskq_jobs_scheduled_page_idx": "96a38dd9db411728ecb45920b4d10174",
    "taskq_jobs_workflow_page_idx": "087d5ee07fab975ca1dd1683087bd2bb",
    "taskq_jobs_worker_running_idx": "c914badb47275d4314b6e6e483cbfc6b",
    "taskq_schedules_active_page_idx": "73c4500cba5185c79c554672fb21ab6f",
    "taskq_schedules_paused_page_idx": "96c4a7c5ab77e14439088e845d75ef0b",
    "taskq_schedules_retired_page_idx": "e481d68f7f36618a9a9d1558c3f75df0",
    "taskq_workflows_finished_page_idx": "b914de1f07d5988037f25ffb5d002ae2",
    "taskq_workflows_running_page_idx": "ca9143323cd280202816a1d78cf28559",
    "jobs_ttl_idx": "aedd0a99c5f811bb5a95778f1f716ea4",
    "jobs_workflow_cancel_idx": "092a4c56bd382e8444bd4b41125eb3df",
    "jobs_workflow_state_idx": "5b4b0d89b781ae404916db187cf80ba5",
    "jobs_workflow_step_uq": "7eeaab0df8faf0900e8ed4f24fc763d4",
    "meta_pkey": "0d779a67c6f4038a1c416b7775e6c96e",
    "queue_flow_pkey": "f233cb4f46bfd12d26c98204f12146cc",
    "queue_counters_pkey": "88df3d3cb7086757e2a8a2ae75def7bd",
    "queues_pkey": "afbb7fc868e58dcae6742808a3d01d91",
    "schedule_decisions_pkey": "95ff69e8ccb44704678ddd4b6ec70f60",
    "schedule_decisions_retention_idx": "d6d31be91d72e84c0e282002fc43f7af",
    "schedule_decisions_schedule_id_action_token_key": "3f5d2f56afcc6369ce27d846ea6d7516",
    "schedule_occurrences_id_key": "7ba6a69c2c8feccbb95db6ad5b909f21",
    "schedule_occurrences_pkey": "76763c65afff61f8d950037577eda8b1",
    "schedule_occurrences_retention_idx": "c3b6a37d3309b7f8616c7519e5f7a1ff",
    "schedules_due_idx": "bba121e2f729b581d4a53a43d0ca809b",
    "schedules_manifest_owner_idx": "f410c2b1ae6277f6fe3de9b4d3b6f913",
    "schedules_name_key": "5d8cd04fb9b1c0d04672afd153c405b8",
    "schedules_pkey": "33ee4ba106b1ccf4848cd53cf216614f",
    "schema_migrations_pkey": "c72ebe664c34fb56088d702ab3bb8864",
    "target_binding_events_binding_version_key": "b2dffb6074ed8c879f1dab21cd1e1574",
    "target_binding_events_pkey": "b78e17186e95d8802a15e2a4ba5d93af",
    "target_identity_pkey": "a21f70691301cb2fea526aabb99abf25",
    "uq_job_attempts_running": "dc6b831d4b3259c15d2a6c7f68b6794a",
    "workers_pkey": "c800776a247ce583b0e856c87493c7c4",
    "workers_presence_page_idx": "e33056539ac43fdd41ea349954b45aa3",
    "workers_seen_idx": "c5414b98a1d4a1df241eacf53433412e",
    "workflow_member_counts_pkey": "62678a23481a47eb004c57b4f2d3239a",
    "workflows_cancel_idx": "8ebdccd99ee8872c4fda1107449608ae",
    "workflows_finalize_idx": "fd3c52cd8e700836ff8210954a5f2a32",
    "workflows_pkey": "0c296a3e7b13c6006a18b95cd1c8a451",
    "workflows_workflow_key_key": "53dcbddde181e0115456404141f742d7",
}

VIEW_DEFINITIONS = {
    "dead_jobs": "a1d7c075defc79dd3863aed346024101",
    "queue_stats": "76c6cf76aa0accc11b8c9a1b07a54d9a",
    "worker_status": "7e1a77b0bc8380895aaf512c29e6f1d1",
}

TRIGGERS = {
    "jobs_queue_counters_trg": "e4d01cdb14fe881204b1813b9c400206",
    "jobs_breaker_trg": "70e109c5684045d7c8d716670874c6ae",
    "jobs_workflow_member_counts_trg": "e205009f6b176d0896964355ac52b416",
    "workflows_member_counts_lifecycle_trg": "b320cf77e24f9a929354b960dfdd54d2",
}

COMPOSITES = {
    "admission_cancel_result": (
        ("outcome", "text"),
        ("job_id", "uuid"),
        ("receipt", "jsonb"),
        ("receipt_expires_at", "timestamp with time zone"),
    ),
    "admission_finish_result": (
        ("outcome", "text"),
        ("job_id", "uuid"),
        ("receipt", "jsonb"),
        ("receipt_expires_at", "timestamp with time zone"),
    ),
    "admission_reservation": (
        ("outcome", "text"),
        ("handle", "uuid"),
        ("job_id", "uuid"),
        ("reservation_expires_at", "timestamp with time zone"),
        ("retry_after_seconds", "integer"),
        ("receipt", "jsonb"),
        ("receipt_expires_at", "timestamp with time zone"),
    ),
    "claim_batch": (
        ("state", "text"),
        ("jobs", "taskq.claimed_job[]"),
        ("retry_after_seconds", "integer"),
    ),
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
        ("lease_seconds", "integer"),
        ("continuation_policy_hash", "text"),
    ),
    "job_list_item": (
        ("job_id", "uuid"),
        ("job_type", "text"),
        ("status", "text"),
        ("outcome", "text"),
        ("priority", "smallint"),
        ("attempt_count", "smallint"),
        ("failure_count", "smallint"),
        ("max_attempts", "smallint"),
        ("created_at", "timestamp with time zone"),
        ("scheduled_at", "timestamp with time zone"),
        ("started_at", "timestamp with time zone"),
        ("finished_at", "timestamp with time zone"),
        ("updated_at", "timestamp with time zone"),
    ),
    "job_page": (
        ("as_of", "timestamp with time zone"),
        ("items", "taskq.job_list_item[]"),
        ("next_after", "jsonb"),
    ),
    "job_event_list_item": (
        ("event_id", "bigint"),
        ("event_type", "text"),
        ("actor", "text"),
        ("created_at", "timestamp with time zone"),
        ("message", "text"),
        ("data", "jsonb"),
    ),
    "job_event_page": (
        ("as_of", "timestamp with time zone"),
        ("items", "taskq.job_event_list_item[]"),
        ("next_after", "bigint"),
    ),
    "queue_profile": (
        ("name", "text"),
        ("profile_version", "bigint"),
        ("default_priority", "smallint"),
        ("default_lease_seconds", "integer"),
        ("default_max_attempts", "smallint"),
        ("default_backoff_mode", "text"),
        ("default_backoff_base", "integer"),
        ("default_backoff_cap", "integer"),
        ("retention_hours", "integer"),
        ("failed_retention_hours", "integer"),
        ("max_depth", "integer"),
        ("notify_enabled", "boolean"),
        ("paused", "boolean"),
        ("max_running", "integer"),
        ("claim_rate_per_minute", "integer"),
        ("claim_burst", "integer"),
        ("ramp_seconds", "integer"),
        ("default_ttl_seconds", "integer"),
        ("backpressure_retry_seconds", "integer"),
        ("notify_mode", "text"),
    ),
    "queue_profile_update": (
        ("result", "text"),
        ("profile", "taskq.queue_profile"),
        ("current_version", "bigint"),
    ),
    "schedule_action_result": (
        ("outcome", "text"),
        ("replayed", "boolean"),
        ("schedule_id", "uuid"),
        ("jobs_enqueued", "integer"),
        ("next_fire_at", "timestamp with time zone"),
        ("state", "text"),
        ("version", "bigint"),
    ),
    "schedule_auth_projection": (("name", "text"), ("queue", "text")),
    "schedule_claim": (
        ("schedule_id", "uuid"),
        ("name", "text"),
        ("definition_version", "bigint"),
        ("as_of", "timestamp with time zone"),
        ("target", "jsonb"),
        ("recurrence", "jsonb"),
        ("catchup_policy", "text"),
        ("max_catchup", "integer"),
        ("initialized", "boolean"),
        ("next_fire_at", "timestamp with time zone"),
        ("token", "uuid"),
        ("lease_seconds", "integer"),
        ("smear_seconds", "integer"),
    ),
    "schedule_claim_batch": (
        ("state", "text"),
        ("schedules", "taskq.schedule_claim[]"),
    ),
    "schedule_profile": (
        ("schedule_id", "uuid"),
        ("name", "text"),
        ("target", "jsonb"),
        ("recurrence", "jsonb"),
        ("catchup_policy", "text"),
        ("max_catchup", "integer"),
        ("state", "text"),
        ("next_fire_at", "timestamp with time zone"),
        ("last_fire_at", "timestamp with time zone"),
        ("version", "bigint"),
    ),
    "schedule_list_item": (
        ("schedule_id", "uuid"),
        ("name", "text"),
        ("target", "jsonb"),
        ("recurrence", "jsonb"),
        ("catchup_policy", "text"),
        ("max_catchup", "integer"),
        ("state", "text"),
        ("next_fire_at", "timestamp with time zone"),
        ("last_fire_at", "timestamp with time zone"),
        ("version", "bigint"),
    ),
    "schedule_list_page": (
        ("as_of", "timestamp with time zone"),
        ("items", "taskq.schedule_list_item[]"),
        ("next_after", "text"),
    ),
    "schedule_write_result": (
        ("outcome", "text"),
        ("profile", "taskq.schedule_profile"),
    ),
    "settle_result": (
        ("result", "text"),
        ("job_status", "text"),
        ("scheduled_at", "timestamp with time zone"),
    ),
    "target_identity_profile": (
        ("installation_id", "uuid"),
        ("environment", "text"),
        ("binding_version", "bigint"),
        ("bound_at", "timestamp with time zone"),
        ("bound_by", "text"),
        ("contract_version", "text"),
        ("capabilities", "jsonb"),
    ),
    "workflow_auth_projection": (
        ("workflow_id", "uuid"),
        ("declared_queues", "text[]"),
    ),
    "workflow_result": (
        ("outcome", "text"),
        ("workflow_id", "uuid"),
        ("status", "text"),
    ),
    "workflow_member_projection": (
        ("job_id", "uuid"),
        ("queue", "text"),
        ("job_type", "text"),
        ("step_key", "text"),
        ("status", "text"),
        ("outcome", "text"),
        ("pending_deps", "integer"),
        ("attempt_count", "integer"),
        ("failure_count", "integer"),
        ("created_at", "timestamp with time zone"),
        ("scheduled_at", "timestamp with time zone"),
        ("started_at", "timestamp with time zone"),
        ("finished_at", "timestamp with time zone"),
        ("updated_at", "timestamp with time zone"),
    ),
    "workflow_page": (
        ("as_of", "timestamp with time zone"),
        ("profile", "taskq.workflow_read_profile"),
        ("counts", "taskq.workflow_state_counts"),
        ("items", "taskq.workflow_member_projection[]"),
        ("next_after", "uuid"),
    ),
    "workflow_list_item": (
        ("workflow_id", "uuid"),
        ("workflow_key", "text"),
        ("kind", "text"),
        ("status", "text"),
        ("sealed", "boolean"),
        ("cancel_requested", "boolean"),
        ("declared_queues", "text[]"),
        ("created_at", "timestamp with time zone"),
        ("updated_at", "timestamp with time zone"),
        ("finished_at", "timestamp with time zone"),
    ),
    "workflow_list_page": (
        ("as_of", "timestamp with time zone"),
        ("items", "taskq.workflow_list_item[]"),
        ("next_after", "jsonb"),
    ),
    "workflow_read_profile": (
        ("workflow_id", "uuid"),
        ("kind", "text"),
        ("status", "text"),
        ("sealed", "boolean"),
        ("cancel_requested", "boolean"),
        ("declared_queues", "text[]"),
        ("created_at", "timestamp with time zone"),
        ("updated_at", "timestamp with time zone"),
        ("finished_at", "timestamp with time zone"),
        ("member_limit", "integer"),
        ("admitted_total", "bigint"),
        ("remaining_capacity", "bigint"),
        ("continuation_policy_hash", "text"),
    ),
    "workflow_state_counts": (
        ("blocked", "bigint"),
        ("queued", "bigint"),
        ("running", "bigint"),
        ("succeeded", "bigint"),
        ("failed", "bigint"),
        ("cancelled", "bigint"),
    ),
    "worker_presence_item": (
        ("worker_id", "text"),
        ("declared_queues", "text[]"),
        ("version", "text"),
        ("started_at", "timestamp with time zone"),
        ("last_seen_at", "timestamp with time zone"),
        ("online", "boolean"),
        ("running_jobs", "bigint"),
        ("shutdown_requested", "boolean"),
    ),
    "worker_presence_page": (
        ("as_of", "timestamp with time zone"),
        ("items", "taskq.worker_presence_item[]"),
        ("next_last_seen_at", "timestamp with time zone"),
        ("next_worker_id", "text"),
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
taskq._claim_jobs_unattested(text,text,integer,text[],integer,text,uuid,boolean)|p_queue text, p_worker_id text, p_batch integer DEFAULT 1, p_job_types text[] DEFAULT NULL::text[], p_lease_seconds integer DEFAULT NULL::integer, p_affinity_key text DEFAULT NULL::text, p_job_id uuid DEFAULT NULL::uuid, p_accept_throttled boolean DEFAULT false|taskq.claim_batch|plpgsql|v|u|
taskq._claim_jobs_unattested(text,text,integer,text[],integer,text,uuid,text[],boolean)|p_queue text, p_worker_id text, p_batch integer, p_job_types text[], p_lease_seconds integer, p_affinity_key text, p_job_id uuid, p_continuation_policy_hashes text[], p_accept_throttled boolean DEFAULT false|taskq.claim_batch|plpgsql|v|u|
taskq._claim_schedules_unattested(text,integer,integer)|p_worker_id text, p_limit integer DEFAULT 10, p_lease_seconds integer DEFAULT 60|taskq.schedule_claim_batch|plpgsql|v|u|
taskq._enqueue_followup(uuid,text,jsonb,integer)|p_parent_job_id uuid, p_parent_queue text, p_spec jsonb, p_spec_index integer|TABLE(job_id uuid, created boolean)|plpgsql|v|u|
taskq._janitor_unattested()||jsonb|plpgsql|v|u|
taskq._put_schedule_unattested(text,jsonb,text,bigint)|p_name text, p_definition jsonb, p_actor text, p_expected_version bigint DEFAULT NULL::bigint|taskq.schedule_write_result|plpgsql|v|u|
taskq._retire_schedule_unattested(text,bigint,text)|p_name text, p_expected_version bigint, p_actor text|taskq.schedule_write_result|plpgsql|v|u|
taskq._reserve_workflow_members(uuid,integer,text)|p_workflow_id uuid, p_count integer, p_continuation_policy_hash text|bigint|plpgsql|v|u|
taskq._schedule_error_unattested(uuid,uuid,bigint,text,integer)|p_schedule_id uuid, p_token uuid, p_definition_version bigint, p_error text, p_retry_seconds integer DEFAULT 30|taskq.schedule_action_result|plpgsql|v|u|
taskq._target_attestation_mac()||text|sql|v|u|
taskq._tick_unattested(integer)|p_reap_limit integer DEFAULT 200|jsonb|plpgsql|v|u|
taskq.advance_workflow_cancellations(integer)|p_limit integer DEFAULT 100|integer|plpgsql|v|u|
taskq.attest_target(text,uuid,boolean)|p_expected_environment text, p_expected_installation_id uuid DEFAULT NULL::uuid, p_allow_production boolean DEFAULT false|taskq.target_identity_profile|plpgsql|v|u|taskq_housekeeper,taskq_operator,taskq_runner
taskq.backoff_seconds(text,integer,integer,integer)|p_mode text, p_base integer, p_cap integer, p_failures integer|integer|sql|v|u|
taskq.bind_target_identity(uuid,text,text,bigint,boolean,text)|p_expected_installation_id uuid, p_environment text, p_actor text, p_expected_binding_version bigint, p_rotate boolean DEFAULT false, p_reason text DEFAULT NULL::text|taskq.target_identity_profile|plpgsql|v|u|
taskq.cancel_admission(text,text,uuid)|p_queue text, p_idempotency_key text, p_handle uuid|taskq.admission_cancel_result|plpgsql|v|u|taskq_producer
taskq.cancel_dependents(uuid,text,integer)|p_job_id uuid, p_reason text, p_limit integer DEFAULT 100|integer|plpgsql|v|u|
taskq.cancel_job(uuid,text,text)|p_job_id uuid, p_actor text, p_reason text DEFAULT NULL::text|TABLE(result text, job_status text)|plpgsql|v|u|taskq_operator
taskq.cancel_running_job(uuid,uuid,text,text)|p_job_id uuid, p_attempt_id uuid, p_worker_id text, p_reason text|taskq.settle_result|plpgsql|v|u|taskq_runner
taskq.cancel_workflow(uuid,text,text)|p_workflow_id uuid, p_actor text, p_reason text|taskq.workflow_result|plpgsql|v|u|taskq_operator
taskq.claim_janitor_due()||boolean|plpgsql|v|u|
taskq._flow_consume_queue(text,integer,integer,numeric,integer)|p_queue text, p_rate_per_minute integer, p_burst integer, p_scale numeric, p_want integer|TABLE(granted integer, retry_after integer)|plpgsql|v|u|
taskq._flow_scale(text,integer)|p_queue text, p_ramp_seconds integer|numeric|plpgsql|v|u|
taskq._throttled_or_empty(boolean,integer)|p_accept boolean, p_retry_after integer|taskq.claim_batch|sql|v|u|
taskq._breaker_gate(text)|p_queue text|integer|plpgsql|v|u|
taskq._breaker_on_settle()||trigger|plpgsql|v|u|
taskq.claim_jobs(text,text,integer,text[],integer,text,uuid,boolean)|p_queue text, p_worker_id text, p_batch integer DEFAULT 1, p_job_types text[] DEFAULT NULL::text[], p_lease_seconds integer DEFAULT NULL::integer, p_affinity_key text DEFAULT NULL::text, p_job_id uuid DEFAULT NULL::uuid, p_accept_throttled boolean DEFAULT false|taskq.claim_batch|plpgsql|v|u|taskq_runner
taskq.claim_jobs(text,text,integer,text[],integer,text,uuid,text[],boolean)|p_queue text, p_worker_id text, p_batch integer, p_job_types text[], p_lease_seconds integer, p_affinity_key text, p_job_id uuid, p_continuation_policy_hashes text[], p_accept_throttled boolean DEFAULT false|taskq.claim_batch|plpgsql|v|u|taskq_runner
taskq.claim_schedules(text,integer,integer)|p_worker_id text, p_limit integer DEFAULT 10, p_lease_seconds integer DEFAULT 60|taskq.schedule_claim_batch|plpgsql|v|u|taskq_housekeeper
taskq.complete_job(uuid,uuid,text,jsonb,jsonb,jsonb)|p_job_id uuid, p_attempt_id uuid, p_worker_id text, p_result jsonb DEFAULT NULL::jsonb, p_stats jsonb DEFAULT NULL::jsonb, p_followups jsonb DEFAULT NULL::jsonb|taskq.settle_result|plpgsql|v|u|taskq_runner
taskq.complete_job(uuid,uuid,text,jsonb,jsonb,jsonb,text)|p_job_id uuid, p_attempt_id uuid, p_worker_id text, p_result jsonb, p_stats jsonb, p_followups jsonb, p_continuation_policy_hash text|taskq.settle_result|plpgsql|v|u|taskq_runner
taskq.create_workflow(text,text,jsonb,text[],text)|p_workflow_key text, p_kind text, p_params jsonb, p_declared_queues text[], p_actor text|taskq.workflow_result|plpgsql|v|u|taskq_producer
taskq.create_workflow(text,text,jsonb,text[],text,integer,text)|p_workflow_key text, p_kind text, p_params jsonb, p_declared_queues text[], p_actor text, p_member_limit integer, p_continuation_policy_hash text|taskq.workflow_result|plpgsql|v|u|taskq_producer
taskq.emit_event(uuid,uuid,text,text,text,jsonb)|p_job_id uuid, p_attempt_id uuid, p_event_type text, p_actor text, p_message text, p_data jsonb DEFAULT NULL::jsonb|void|plpgsql|v|u|
taskq.enqueue_many(text,jsonb)|p_queue text, p_jobs jsonb|TABLE(input_index integer, job_id uuid, outcome text)|plpgsql|v|u|taskq_producer
taskq.enqueue(text,text,jsonb,smallint,timestamp with time zone,text,text,text,smallint,integer,text,integer,integer,uuid[],uuid,text,uuid,jsonb,integer,text)|p_queue text, p_job_type text, p_payload jsonb DEFAULT '{}'::jsonb, p_priority smallint DEFAULT NULL::smallint, p_scheduled_at timestamp with time zone DEFAULT NULL::timestamp with time zone, p_idempotency_key text DEFAULT NULL::text, p_concurrency_key text DEFAULT NULL::text, p_affinity_key text DEFAULT NULL::text, p_max_attempts smallint DEFAULT NULL::smallint, p_lease_seconds integer DEFAULT NULL::integer, p_backoff_mode text DEFAULT NULL::text, p_backoff_base integer DEFAULT NULL::integer, p_backoff_cap integer DEFAULT NULL::integer, p_depends_on uuid[] DEFAULT NULL::uuid[], p_workflow_id uuid DEFAULT NULL::uuid, p_step_key text DEFAULT NULL::text, p_parent_job_id uuid DEFAULT NULL::uuid, p_headers jsonb DEFAULT NULL::jsonb, p_ttl_seconds integer DEFAULT NULL::integer, p_flow_key text DEFAULT NULL::text|TABLE(job_id uuid, created boolean)|plpgsql|v|u|taskq_producer
taskq.ensure_queue(text,jsonb,text)|p_name text, p_profile jsonb DEFAULT '{}'::jsonb, p_actor text DEFAULT NULL::text|TABLE(result text, profile jsonb)|plpgsql|v|u|taskq_operator
taskq.expire_ttl(integer)|p_limit integer DEFAULT 200|integer|plpgsql|v|u|
taskq.expire_job(uuid,text)|p_job_id uuid, p_actor text|text|plpgsql|v|u|taskq_operator
taskq.expire_worker_leases(text,text)|p_worker_id text, p_actor text|jsonb|plpgsql|v|u|taskq_operator
taskq.fail_job(uuid,uuid,text,text,boolean,integer,jsonb,jsonb)|p_job_id uuid, p_attempt_id uuid, p_worker_id text, p_error text, p_retryable boolean DEFAULT true, p_retry_after_seconds integer DEFAULT NULL::integer, p_progress jsonb DEFAULT NULL::jsonb, p_stats jsonb DEFAULT NULL::jsonb|taskq.settle_result|plpgsql|v|u|taskq_runner
taskq.fire_schedule(uuid,uuid,bigint,timestamp with time zone[],timestamp with time zone)|p_schedule_id uuid, p_token uuid, p_definition_version bigint, p_occurrences timestamp with time zone[], p_next_fire_at timestamp with time zone|taskq.schedule_action_result|plpgsql|v|u|taskq_housekeeper
taskq.finish_admission(text,text,uuid,jsonb,jsonb)|p_queue text, p_idempotency_key text, p_handle uuid, p_job jsonb, p_receipt jsonb DEFAULT '{}'::jsonb|taskq.admission_finish_result|plpgsql|v|u|taskq_producer
taskq.finalize_cancel_stragglers(integer)|p_limit integer|integer|plpgsql|v|u|
taskq.finalize_dep_stragglers(integer)|p_limit integer DEFAULT 100|integer|plpgsql|v|u|
taskq.finalize_workflows(integer)|p_limit integer DEFAULT 100|integer|plpgsql|v|u|
taskq.flow_key_admit(text)|p_key text|boolean|plpgsql|v|u|
taskq.get_authorization_projection(uuid)|p_job_id uuid|TABLE(job_id uuid, queue text, job_type text, status text)|sql|s|u|taskq_observer
taskq.get_contract_meta()||TABLE(contract_version text, capabilities jsonb)|sql|s|u|taskq_observer
taskq.get_job(uuid,boolean,boolean,boolean,boolean)|p_job_id uuid, p_include_error boolean DEFAULT false, p_include_result boolean DEFAULT false, p_include_progress boolean DEFAULT false, p_include_payload boolean DEFAULT false|TABLE(job_id uuid, queue text, job_type text, status text, outcome text, priority smallint, attempt_count smallint, failure_count smallint, max_attempts smallint, created_at timestamp with time zone, scheduled_at timestamp with time zone, started_at timestamp with time zone, finished_at timestamp with time zone, updated_at timestamp with time zone, error text, result jsonb, progress jsonb, payload jsonb)|sql|s|u|taskq_observer
taskq.get_queue_profile(text)|p_queue text|taskq.queue_profile|sql|s|u|taskq_observer
taskq.get_queue_stats(text)|p_queue text DEFAULT NULL::text|TABLE(as_of timestamp with time zone, queue text, stats jsonb)|sql|s|u|taskq_observer
taskq.get_schedule(text)|p_name text|taskq.schedule_profile|plpgsql|s|u|taskq_operator
taskq.get_schedule_authorization_projection(text)|p_name text|taskq.schedule_auth_projection|plpgsql|s|u|taskq_operator
taskq.get_scheduler_health()||TABLE(database_time timestamp with time zone, active_schedules bigint, due_schedules bigint, oldest_due_at timestamp with time zone, last_decision_at timestamp with time zone, auto_paused_schedules bigint)|sql|s|u|taskq_housekeeper,taskq_observer,taskq_operator
taskq.get_target_identity()||taskq.target_identity_profile|sql|s|u|taskq_housekeeper,taskq_observer,taskq_operator,taskq_producer,taskq_runner
taskq.get_workflow_authorization_projection(uuid)|p_workflow_id uuid|taskq.workflow_auth_projection|plpgsql|s|u|taskq_observer
taskq.get_workflow_page(uuid,integer,uuid)|p_workflow_id uuid, p_limit integer DEFAULT 50, p_after uuid DEFAULT NULL::uuid|taskq.workflow_page|plpgsql|s|u|taskq_observer
taskq.has_capability(text)|p_name text|boolean|sql|s|u|
taskq.heartbeat(uuid,uuid,text,integer,jsonb,jsonb)|p_job_id uuid, p_attempt_id uuid, p_worker_id text, p_lease_seconds integer DEFAULT NULL::integer, p_progress jsonb DEFAULT NULL::jsonb, p_stats jsonb DEFAULT NULL::jsonb|TABLE(ok boolean, cancel_requested boolean, lease_expires_at timestamp with time zone)|plpgsql|v|u|taskq_runner
taskq.janitor()||jsonb|plpgsql|v|u|taskq_housekeeper,taskq_operator
taskq.list_job_events(uuid,integer,bigint,boolean)|p_job_id uuid, p_limit integer DEFAULT 50, p_after bigint DEFAULT NULL::bigint, p_include_details boolean DEFAULT false|taskq.job_event_page|plpgsql|s|u|taskq_observer
taskq.list_jobs(text,text,integer,jsonb)|p_queue text, p_view text, p_limit integer DEFAULT 50, p_after jsonb DEFAULT NULL::jsonb|taskq.job_page|plpgsql|s|u|taskq_observer
taskq.list_managed_schedules(text,text,integer,text)|p_namespace text, p_source text, p_limit integer DEFAULT 100, p_after_name text DEFAULT NULL::text|TABLE(name text, manifest_key text, display_name text, definition_hash text, target jsonb, recurrence jsonb, catchup_policy text, max_catchup integer, overlap_policy text, max_lateness_seconds integer, state text, version bigint)|plpgsql|s|u|taskq_observer,taskq_operator
taskq.list_schedules(text,integer,text)|p_view text, p_limit integer DEFAULT 50, p_after text DEFAULT NULL::text|taskq.schedule_list_page|plpgsql|s|u|taskq_operator
taskq.list_worker_presence(integer,timestamp with time zone,text)|p_limit integer DEFAULT 50, p_after_last_seen_at timestamp with time zone DEFAULT NULL::timestamp with time zone, p_after_worker_id text DEFAULT NULL::text|taskq.worker_presence_page|plpgsql|s|u|taskq_observer
taskq.list_workflows(text,integer,jsonb)|p_view text, p_limit integer DEFAULT 50, p_after jsonb DEFAULT NULL::jsonb|taskq.workflow_list_page|plpgsql|s|u|taskq_observer
taskq.lock_active_effect_attempt(uuid,uuid,text,text,text)|p_job_id uuid, p_attempt_id uuid, p_worker_id text, p_queue text, p_job_type text|TABLE(payload jsonb, workflow_id uuid, workflow_counts jsonb)|plpgsql|v|u|taskq_producer
taskq.manage_workflow_member_counts()||trigger|plpgsql|v|u|
taskq.metrics()||TABLE(name text, labels jsonb, value numeric)|sql|s|u|taskq_observer
taskq.pause_queue(text,text,text)|p_name text, p_actor text, p_reason text DEFAULT NULL::text|text|plpgsql|v|u|taskq_operator
taskq.purge_queued(text,integer,text,text)|p_queue text, p_limit integer, p_actor text, p_reason text DEFAULT NULL::text|integer|plpgsql|v|u|taskq_operator
taskq.put_managed_schedule(text,jsonb,text,text,text,text,text,text,integer,text,bigint)|p_name text, p_definition jsonb, p_namespace text, p_source text, p_manifest_key text, p_display_name text, p_definition_hash text, p_overlap_policy text, p_max_lateness_seconds integer, p_actor text, p_expected_version bigint DEFAULT NULL::bigint|taskq.schedule_write_result|plpgsql|v|u|taskq_operator
taskq.put_schedule(text,jsonb,text,bigint)|p_name text, p_definition jsonb, p_actor text, p_expected_version bigint DEFAULT NULL::bigint|taskq.schedule_write_result|plpgsql|v|u|taskq_operator
taskq.reap_expired(integer)|p_limit integer DEFAULT 100|integer|plpgsql|v|u|
taskq.reap_job(uuid)|p_job_id uuid|boolean|plpgsql|v|u|
taskq.queue_health(text)|p_queue text DEFAULT NULL::text|TABLE(queue text, verdict text, detail jsonb)|plpgsql|s|u|taskq_observer
taskq.queue_health_internal()||TABLE(queue text, verdict text, detail jsonb)|plpgsql|s|u|
taskq.redrive_failed(text,integer,text,integer)|p_queue text, p_limit integer, p_actor text, p_smear_seconds integer DEFAULT 0|TABLE(redriven integer, skipped integer)|plpgsql|v|u|taskq_operator
taskq.redrive_job(uuid,text,boolean)|p_job_id uuid, p_actor text, p_reset_progress boolean DEFAULT false|boolean|plpgsql|v|u|taskq_operator
taskq.refresh_stats_snapshot()||void|plpgsql|v|u|
taskq.release_job(uuid,uuid,text,text,integer,jsonb)|p_job_id uuid, p_attempt_id uuid, p_worker_id text, p_cause text DEFAULT 'released'::text, p_delay_seconds integer DEFAULT 0, p_progress jsonb DEFAULT NULL::jsonb|taskq.settle_result|plpgsql|v|u|taskq_runner
taskq.reprioritize(uuid,smallint,text)|p_job_id uuid, p_priority smallint, p_actor text|text|plpgsql|v|u|taskq_operator
taskq.retire_schedule(text,bigint,text)|p_name text, p_expected_version bigint, p_actor text|taskq.schedule_write_result|plpgsql|v|u|taskq_operator
taskq.reserve_admission(text,text,text,uuid,integer,integer)|p_queue text, p_idempotency_key text, p_intent_hash text, p_handle uuid, p_reservation_ttl_seconds integer DEFAULT 300, p_receipt_ttl_seconds integer DEFAULT 2592000|taskq.admission_reservation|plpgsql|v|u|taskq_producer
taskq.request_worker_shutdown(text,text,text)|p_worker_id text, p_queue text, p_actor text|integer|plpgsql|v|u|taskq_operator
taskq.require_target_attestation()||void|plpgsql|v|u|
taskq.resume_queue(text,text)|p_name text, p_actor text|text|plpgsql|v|u|taskq_operator
taskq.run_now(uuid,text)|p_job_id uuid, p_actor text|text|plpgsql|v|u|taskq_operator
taskq.schedule_error(uuid,uuid,bigint,text,integer)|p_schedule_id uuid, p_token uuid, p_definition_version bigint, p_error text, p_retry_seconds integer DEFAULT 30|taskq.schedule_action_result|plpgsql|v|u|taskq_housekeeper
taskq.schedule_error(uuid,uuid,bigint,text,integer,boolean)|p_schedule_id uuid, p_token uuid, p_definition_version bigint, p_error text, p_retry_seconds integer, p_deterministic boolean|taskq.schedule_action_result|plpgsql|v|u|taskq_housekeeper
taskq.seal_workflow(uuid,text)|p_workflow_id uuid, p_actor text|taskq.workflow_result|plpgsql|v|u|taskq_producer
taskq.set_flow_limit(text,integer,integer,text)|p_key text, p_rate_per_minute integer, p_burst integer DEFAULT NULL::integer, p_actor text DEFAULT NULL::text|text|plpgsql|v|u|taskq_operator
taskq.set_breaker_config(text,integer,integer,integer,text)|p_queue text, p_failure_threshold integer, p_cooldown_seconds integer DEFAULT 30, p_half_open_successes integer DEFAULT 1, p_actor text DEFAULT NULL::text|text|plpgsql|v|u|taskq_operator
taskq.set_breaker_rate(text,numeric,integer,integer,text)|p_queue text, p_failure_ratio numeric, p_window_seconds integer DEFAULT 60, p_min_volume integer DEFAULT 10, p_actor text DEFAULT NULL::text|text|plpgsql|v|u|taskq_operator
taskq.set_priority_aging(text,integer,text)|p_queue text, p_aging_seconds integer, p_actor text DEFAULT NULL::text|text|plpgsql|v|u|taskq_operator
taskq.trip_breaker(text,text)|p_queue text, p_actor text DEFAULT NULL::text|text|plpgsql|v|u|taskq_operator
taskq.force_close_breaker(text,text)|p_queue text, p_actor text DEFAULT NULL::text|text|plpgsql|v|u|taskq_operator
taskq.set_schedule_smear(text,integer,text)|p_name text, p_smear_seconds integer, p_actor text DEFAULT NULL::text|text|plpgsql|v|u|taskq_operator
taskq.set_concurrency_limit(text,integer,text)|p_key text, p_max_running integer, p_actor text|text|plpgsql|v|u|taskq_operator
taskq.set_schedule_state(text,text,bigint,text,text)|p_name text, p_state text, p_expected_version bigint, p_actor text, p_reason text|taskq.schedule_write_result|plpgsql|v|u|taskq_operator
taskq.snooze_job(uuid,uuid,text,integer,text,jsonb)|p_job_id uuid, p_attempt_id uuid, p_worker_id text, p_delay_seconds integer, p_reason text DEFAULT NULL::text, p_progress jsonb DEFAULT NULL::jsonb|taskq.settle_result|plpgsql|v|u|taskq_runner
taskq.tick(integer)|p_reap_limit integer DEFAULT 200|jsonb|plpgsql|v|u|taskq_housekeeper,taskq_operator
taskq.try_enqueue(text,text,jsonb,smallint,timestamp with time zone,text,text,text,smallint,integer,text,integer,integer,uuid[],uuid,text,uuid,jsonb,integer,text)|p_queue text, p_job_type text, p_payload jsonb DEFAULT '{}'::jsonb, p_priority smallint DEFAULT NULL::smallint, p_scheduled_at timestamp with time zone DEFAULT NULL::timestamp with time zone, p_idempotency_key text DEFAULT NULL::text, p_concurrency_key text DEFAULT NULL::text, p_affinity_key text DEFAULT NULL::text, p_max_attempts smallint DEFAULT NULL::smallint, p_lease_seconds integer DEFAULT NULL::integer, p_backoff_mode text DEFAULT NULL::text, p_backoff_base integer DEFAULT NULL::integer, p_backoff_cap integer DEFAULT NULL::integer, p_depends_on uuid[] DEFAULT NULL::uuid[], p_workflow_id uuid DEFAULT NULL::uuid, p_step_key text DEFAULT NULL::text, p_parent_job_id uuid DEFAULT NULL::uuid, p_headers jsonb DEFAULT NULL::jsonb, p_ttl_seconds integer DEFAULT NULL::integer, p_flow_key text DEFAULT NULL::text|TABLE(outcome text, job_id uuid, retry_after_seconds integer)|plpgsql|v|u|taskq_producer
taskq.truncate_utf8(text,integer)|p_value text, p_max_bytes integer|text|plpgsql|i|s|
taskq.update_queue_profile(text,jsonb,text,bigint)|p_name text, p_profile jsonb, p_actor text, p_expected_version bigint|taskq.queue_profile_update|plpgsql|v|u|taskq_operator
taskq.update_queue_counters()||trigger|plpgsql|v|u|
taskq.update_workflow_member_counts()||trigger|plpgsql|v|u|
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
    "taskq.attest_target(text,uuid,boolean)": frozenset({"TQ422"}),
    "taskq.cancel_admission(text,text,uuid)": frozenset({"TQ001", "TQ409", "TQ422"}),
    "taskq.cancel_workflow(uuid,text,text)": frozenset({"TQ001", "TQ422"}),
    "taskq.cancel_job(uuid,text,text)": frozenset({"TQ001"}),
    "taskq.cancel_running_job(uuid,uuid,text,text)": frozenset(),
    "taskq.claim_jobs(text,text,integer,text[],integer,text,uuid,boolean)": frozenset({"TQ422"}),
    "taskq.claim_jobs(text,text,integer,text[],integer,text,uuid,text[],boolean)": frozenset(
        {"TQ422", "TQ501"}
    ),
    "taskq.claim_schedules(text,integer,integer)": frozenset({"TQ422"}),
    "taskq.complete_job(uuid,uuid,text,jsonb,jsonb,jsonb)": frozenset({"TQ422", "TQ501"}),
    "taskq.complete_job(uuid,uuid,text,jsonb,jsonb,jsonb,text)": frozenset(
        {"TQ409", "TQ422", "TQ500", "TQ501"}
    ),
    "taskq.create_workflow(text,text,jsonb,text[],text)": frozenset({"TQ001", "TQ409", "TQ422"}),
    "taskq.create_workflow(text,text,jsonb,text[],text,integer,text)": frozenset(
        {"TQ001", "TQ409", "TQ422", "TQ501"}
    ),
    "taskq.enqueue_many(text,jsonb)": frozenset({"TQ001", "TQ422", "TQ429", "TQ500"}),
    "taskq.enqueue(text,text,jsonb,smallint,timestamp with time zone,text,text,text,smallint,integer,text,integer,integer,uuid[],uuid,text,uuid,jsonb,integer,text)": frozenset(
        {"TQ001", "TQ409", "TQ422", "TQ429", "TQ500"}
    ),
    "taskq.ensure_queue(text,jsonb,text)": frozenset({"TQ422"}),
    "taskq.expire_job(uuid,text)": frozenset({"TQ001"}),
    "taskq.expire_worker_leases(text,text)": frozenset(),
    "taskq.fail_job(uuid,uuid,text,text,boolean,integer,jsonb,jsonb)": frozenset({"TQ422"}),
    "taskq.fire_schedule(uuid,uuid,bigint,timestamp with time zone[],timestamp with time zone)": frozenset(
        {"TQ001", "TQ422", "TQ500"}
    ),
    "taskq.finish_admission(text,text,uuid,jsonb,jsonb)": frozenset(
        {"TQ001", "TQ409", "TQ422", "TQ429", "TQ500"}
    ),
    "taskq.get_authorization_projection(uuid)": frozenset(),
    "taskq.get_contract_meta()": frozenset(),
    "taskq.get_job(uuid,boolean,boolean,boolean,boolean)": frozenset(),
    "taskq.get_queue_profile(text)": frozenset(),
    "taskq.get_queue_stats(text)": frozenset(),
    "taskq.get_schedule(text)": frozenset({"TQ001"}),
    "taskq.get_schedule_authorization_projection(text)": frozenset({"TQ001"}),
    "taskq.get_workflow_authorization_projection(uuid)": frozenset({"TQ001"}),
    "taskq.get_workflow_page(uuid,integer,uuid)": frozenset({"TQ001", "TQ422", "TQ500", "TQ501"}),
    "taskq.heartbeat(uuid,uuid,text,integer,jsonb,jsonb)": frozenset({"TQ422"}),
    "taskq.get_scheduler_health()": frozenset(),
    "taskq.get_target_identity()": frozenset(),
    "taskq.janitor()": frozenset({"TQ422"}),
    "taskq.list_job_events(uuid,integer,bigint,boolean)": frozenset({"TQ001", "TQ422", "TQ501"}),
    "taskq.list_jobs(text,text,integer,jsonb)": frozenset({"TQ001", "TQ422", "TQ501"}),
    "taskq.list_managed_schedules(text,text,integer,text)": frozenset({"TQ422"}),
    "taskq.list_schedules(text,integer,text)": frozenset({"TQ422", "TQ501"}),
    "taskq.list_worker_presence(integer,timestamp with time zone,text)": frozenset(
        {"TQ422", "TQ501"}
    ),
    "taskq.list_workflows(text,integer,jsonb)": frozenset({"TQ422", "TQ501"}),
    "taskq.lock_active_effect_attempt(uuid,uuid,text,text,text)": frozenset({"TQ422"}),
    "taskq.metrics()": frozenset(),
    "taskq.pause_queue(text,text,text)": frozenset({"TQ001"}),
    "taskq.purge_queued(text,integer,text,text)": frozenset({"TQ001", "TQ422"}),
    "taskq.put_managed_schedule(text,jsonb,text,text,text,text,text,text,integer,text,bigint)": frozenset(
        {"TQ001", "TQ409", "TQ422"}
    ),
    "taskq.put_schedule(text,jsonb,text,bigint)": frozenset({"TQ001", "TQ409", "TQ422"}),
    "taskq.queue_health(text)": frozenset({"TQ001", "TQ501"}),
    "taskq.redrive_failed(text,integer,text,integer)": frozenset({"TQ422"}),
    "taskq.set_flow_limit(text,integer,integer,text)": frozenset({"TQ422", "TQ501"}),
    "taskq.set_breaker_config(text,integer,integer,integer,text)": frozenset(
        {"TQ422", "TQ501", "TQ001"}
    ),
    "taskq.set_priority_aging(text,integer,text)": frozenset({"TQ422", "TQ001"}),
    "taskq.trip_breaker(text,text)": frozenset({"TQ501", "TQ001"}),
    "taskq.force_close_breaker(text,text)": frozenset({"TQ501", "TQ001"}),
    "taskq.set_breaker_rate(text,numeric,integer,integer,text)": frozenset(
        {"TQ422", "TQ501", "TQ001"}
    ),
    "taskq.set_schedule_smear(text,integer,text)": frozenset({"TQ422", "TQ001"}),
    "taskq.try_enqueue(text,text,jsonb,smallint,timestamp with time zone,text,text,text,smallint,integer,text,integer,integer,uuid[],uuid,text,uuid,jsonb,integer,text)": frozenset(
        {"TQ422", "TQ501"}
    ),
    "taskq.redrive_job(uuid,text,boolean)": frozenset({"TQ001", "TQ409"}),
    "taskq.release_job(uuid,uuid,text,text,integer,jsonb)": frozenset({"TQ422"}),
    "taskq.reprioritize(uuid,smallint,text)": frozenset({"TQ001", "TQ409", "TQ422"}),
    "taskq.retire_schedule(text,bigint,text)": frozenset({"TQ001", "TQ409", "TQ422"}),
    "taskq.reserve_admission(text,text,text,uuid,integer,integer)": frozenset(
        {"TQ001", "TQ409", "TQ422"}
    ),
    "taskq.request_worker_shutdown(text,text,text)": frozenset(),
    "taskq.resume_queue(text,text)": frozenset({"TQ001"}),
    "taskq.run_now(uuid,text)": frozenset({"TQ001", "TQ409"}),
    "taskq.schedule_error(uuid,uuid,bigint,text,integer)": frozenset({"TQ001", "TQ422"}),
    "taskq.schedule_error(uuid,uuid,bigint,text,integer,boolean)": frozenset({"TQ001", "TQ422"}),
    "taskq.seal_workflow(uuid,text)": frozenset({"TQ001"}),
    "taskq.set_concurrency_limit(text,integer,text)": frozenset({"TQ422"}),
    "taskq.set_schedule_state(text,text,bigint,text,text)": frozenset({"TQ001", "TQ409", "TQ422"}),
    "taskq.snooze_job(uuid,uuid,text,integer,text,jsonb)": frozenset({"TQ422"}),
    "taskq.tick(integer)": frozenset({"TQ422"}),
    "taskq.update_queue_profile(text,jsonb,text,bigint)": frozenset({"TQ001", "TQ422"}),
    "taskq.worker_heartbeat(text,text[],text,integer,text,jsonb)": frozenset({"TQ422"}),
}

REPLAY_RULES = {
    identity: (
        "verb-aware attempt replay"
        if identity
        in {
            "taskq.cancel_running_job(uuid,uuid,text,text)",
            "taskq.complete_job(uuid,uuid,text,jsonb,jsonb,jsonb)",
            "taskq.complete_job(uuid,uuid,text,jsonb,jsonb,jsonb,text)",
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
META_SEEDS = {
    "contract_version": '"0.6.3"',
    "capabilities": (
        '{"active": ["admission_reservations", "circuit_breaker", "dependencies_workflows", '
        '"flow_control", "followups", "operator_schedule_list", "queue_counters", '
        '"read_model_job_events", "read_model_job_views_v2", '
        '"read_model_list_finished", "read_model_list_ready", '
        '"read_model_list_running", "read_model_workflow", '
        '"read_model_workflow_list", "scheduler_v2", "schedules", '
        '"target_attestation", "worker_presence", "workflow_continuations"]}'
    ),
}
SCHEDULE_SEED = {
    "name": "taskq-janitor-daily",
    "target": '{"kind": "maintenance", "maintenance": "janitor"}',
    "recurrence": '{"kind": "cron", "timezone": "UTC", "expression": "0 3 * * *"}',
    "catchup_policy": "fire_once",
    "max_catchup": 1,
    "state": "active",
    "version": 1,
}
