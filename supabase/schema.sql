-- Dev Profile Unifier
-- Supabase Database Schema
--
-- Purpose:
--   Stores raw public developer data, normalized source accounts,
--   canonical profiles, source link decisions, match evidence,
--   conflicts, profile facts, LLM summaries, and observability metrics.
--
-- Run this file in the Supabase SQL editor.

-- ─────────────────────────────────────────────
-- Extensions
-- ─────────────────────────────────────────────

create extension if not exists "pgcrypto";

-- ─────────────────────────────────────────────
-- Enum types
-- ─────────────────────────────────────────────

do $$
begin
    create type platform_source as enum (
        'github',
        'stackoverflow',
        'devto',
        'hackernews'
    );
exception
    when duplicate_object then null;
end $$;

do $$
begin
    create type metric_source as enum (
        'github',
        'stackoverflow',
        'devto',
        'hackernews',
        'gemini',
        'supabase'
    );
exception
    when duplicate_object then null;
end $$;

do $$
begin
    create type resolution_status as enum (
        'running',
        'resolved',
        'partial',
        'failed'
    );
exception
    when duplicate_object then null;
end $$;

do $$
begin
    create type profile_confidence_level as enum (
        'high',
        'medium',
        'low',
        'uncertain'
    );
exception
    when duplicate_object then null;
end $$;

do $$
begin
    create type match_decision as enum (
        'auto_match',
        'needs_review',
        'reject'
    );
exception
    when duplicate_object then null;
end $$;

do $$
begin
    create type source_relationship_type as enum (
        'primary',
        'secondary',
        'alias',
        'possible_alias',
        'rejected'
    );
exception
    when duplicate_object then null;
end $$;

do $$
begin
    create type verification_status as enum (
        'claimed_by_input',
        'evidence_matched',
        'reciprocal_link_verified',
        'likely_same_person',
        'needs_review',
        'rejected'
    );
exception
    when duplicate_object then null;
end $$;

do $$
begin
    create type evidence_direction as enum (
        'positive',
        'negative',
        'neutral'
    );
exception
    when duplicate_object then null;
end $$;

do $$
begin
    create type conflict_severity as enum (
        'low',
        'medium',
        'high'
    );
exception
    when duplicate_object then null;
end $$;

-- ─────────────────────────────────────────────
-- Shared trigger functions
-- ─────────────────────────────────────────────

create or replace function set_updated_at()
returns trigger
language plpgsql
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

create or replace function set_source_account_key()
returns trigger
language plpgsql
as $$
declare
    identity_value text;
begin
    identity_value := coalesce(
        nullif(btrim(new.source_user_id), ''),
        nullif(btrim(new.handle), '')
    );

    if identity_value is null then
        raise exception 'source_accounts requires either source_user_id or handle';
    end if;

    new.source_account_key := lower(new.source::text || ':' || identity_value);

    return new;
end;
$$;

-- ─────────────────────────────────────────────
-- 1. resolution_runs
-- ─────────────────────────────────────────────

create table if not exists resolution_runs (
    id                  uuid primary key default gen_random_uuid(),

    input_name          text not null check (length(trim(input_name)) > 0),
    input_payload       jsonb not null check (jsonb_typeof(input_payload) = 'object'),

    status              resolution_status not null default 'running',

    started_at          timestamptz not null default now(),
    completed_at        timestamptz,

    duration_ms         integer check (duration_ms is null or duration_ms >= 0),
    error_message       text,

    sources_attempted   text[] not null default '{}',
    sources_succeeded   text[] not null default '{}',
    sources_failed      text[] not null default '{}',

    source_errors       jsonb not null default '[]'::jsonb
                        check (jsonb_typeof(source_errors) = 'array'),

    result_summary      jsonb not null default '{}'::jsonb
                        check (jsonb_typeof(result_summary) = 'object'),

    created_at          timestamptz not null default now(),

    constraint resolution_runs_completed_after_started
        check (completed_at is null or completed_at >= started_at),

    constraint resolution_runs_completed_state_consistency
        check (
            (status = 'running' and completed_at is null)
            or
            (status in ('resolved', 'partial', 'failed') and completed_at is not null)
        )
);

comment on table resolution_runs is
'One row per POST /profiles/resolve request. Tracks input, status, duration, source success/failure, and errors.';

comment on column resolution_runs.input_payload is
'Full validated request body. Store only user-provided request fields, not secrets.';

comment on column resolution_runs.source_errors is
'Array of source error objects such as [{"source":"github","reason":"rate_limited"}].';

create index if not exists idx_resolution_runs_status
    on resolution_runs(status);

create index if not exists idx_resolution_runs_started_at
    on resolution_runs(started_at desc);

create index if not exists idx_resolution_runs_completed_at
    on resolution_runs(completed_at desc);

-- ─────────────────────────────────────────────
-- 2. raw_source_records
-- ─────────────────────────────────────────────

create table if not exists raw_source_records (
    id                  uuid primary key default gen_random_uuid(),

    resolution_run_id   uuid not null references resolution_runs(id) on delete cascade,

    source              platform_source not null,
    source_record_type  text not null check (length(trim(source_record_type)) > 0),

    source_user_id      text,
    handle              text,

    request_url         text,
    profile_url         text,

    http_status         integer check (
                            http_status is null
                            or (http_status >= 100 and http_status <= 599)
                        ),

    raw_payload         jsonb not null,

    payload_sha256      text,
    fetched_at          timestamptz not null default now(),

    constraint raw_source_records_payload_is_valid_json
        check (jsonb_typeof(raw_payload) in ('object', 'array'))
);

comment on table raw_source_records is
'Original API responses from GitHub, Stack Overflow, dev.to, and Hacker News. Stored before transformation.';

comment on column raw_source_records.payload_sha256 is
'Optional SHA-256 hash of raw payload for dedup/debugging. Calculated by application code.';

create index if not exists idx_raw_source_records_run_id
    on raw_source_records(resolution_run_id);

create index if not exists idx_raw_source_records_source
    on raw_source_records(source);

create index if not exists idx_raw_source_records_type
    on raw_source_records(source_record_type);

create index if not exists idx_raw_source_records_handle
    on raw_source_records(handle)
    where handle is not null;

create index if not exists idx_raw_source_records_source_user_id
    on raw_source_records(source_user_id)
    where source_user_id is not null;

create index if not exists idx_raw_source_records_fetched_at
    on raw_source_records(fetched_at desc);

-- ─────────────────────────────────────────────
-- 3. source_accounts
-- ─────────────────────────────────────────────

create table if not exists source_accounts (
    id                    uuid primary key default gen_random_uuid(),

    source                platform_source not null,
    source_user_id        text,
    handle                text,

    -- Stable uniqueness key set by trigger:
    --   github:12345678
    --   devto:amildev
    --   hackernews:backend_amil
    --   stackoverflow:123456
    --
    -- This is intentionally NOT a generated column because Postgres generated
    -- expressions require immutability, and enum-to-text casts can fail that rule.
    source_account_key    text not null,

    display_name          text,
    bio                   text,
    location              text,
    website_url           text,
    profile_url           text,
    avatar_url            text,

    -- Store hashed email only. Never store raw email unless the privacy model
    -- intentionally changes later.
    email_hash            text,

    company               text,

    topics                text[] not null default '{}',
    outbound_links        text[] not null default '{}',

    activity_payload      jsonb not null default '{}'::jsonb
                          check (jsonb_typeof(activity_payload) = 'object'),

    raw_source_record_id  uuid references raw_source_records(id) on delete set null,

    created_at            timestamptz not null default now(),
    updated_at            timestamptz not null default now(),

    constraint source_accounts_identity_present
        check (
            nullif(source_user_id, '') is not null
            or nullif(handle, '') is not null
        ),

    constraint source_accounts_email_hash_format
        check (
            email_hash is null
            or email_hash ~ '^[a-f0-9]{64}$'
        ),

    constraint source_accounts_unique_key_not_empty
        check (length(source_account_key) > 1),

    unique (source_account_key)
);

comment on table source_accounts is
'Normalized platform accounts. A canonical person may link to multiple accounts from the same platform.';

comment on column source_accounts.source_account_key is
'Stable key set by trigger. Avoids duplicate source accounts when source_user_id is null.';

comment on column source_accounts.activity_payload is
'Small normalized activity summary such as repo counts, followers, top languages, article count, reputation, HN item counts.';

create index if not exists idx_source_accounts_source
    on source_accounts(source);

create index if not exists idx_source_accounts_key
    on source_accounts(source_account_key);

create index if not exists idx_source_accounts_handle
    on source_accounts(lower(handle))
    where handle is not null;

create index if not exists idx_source_accounts_source_user_id
    on source_accounts(source_user_id)
    where source_user_id is not null;

create index if not exists idx_source_accounts_display_name
    on source_accounts(lower(display_name))
    where display_name is not null;

create index if not exists idx_source_accounts_website_url
    on source_accounts(lower(website_url))
    where website_url is not null;

create index if not exists idx_source_accounts_topics
    on source_accounts using gin(topics);

create index if not exists idx_source_accounts_outbound_links
    on source_accounts using gin(outbound_links);

drop trigger if exists trg_source_accounts_key on source_accounts;

create trigger trg_source_accounts_key
before insert or update of source, source_user_id, handle on source_accounts
for each row
execute function set_source_account_key();

drop trigger if exists trg_source_accounts_updated_at on source_accounts;

create trigger trg_source_accounts_updated_at
before update on source_accounts
for each row
execute function set_updated_at();

-- ─────────────────────────────────────────────
-- 4. canonical_profiles
-- ─────────────────────────────────────────────

create table if not exists canonical_profiles (
    id                    uuid primary key default gen_random_uuid(),

    resolution_run_id     uuid references resolution_runs(id) on delete set null,

    display_name          text,
    headline              text,
    location              text,
    bio                   text,

    primary_avatar_url    text,
    primary_website_url   text,

    inferred_skills       text[] not null default '{}',

    confidence_level      profile_confidence_level not null default 'uncertain',

    profile_payload       jsonb not null default '{}'::jsonb
                          check (jsonb_typeof(profile_payload) = 'object'),

    created_at            timestamptz not null default now(),
    updated_at            timestamptz not null default now()
);

comment on table canonical_profiles is
'Unified developer profile derived from trusted source accounts. This is derived data, not the raw source of truth.';

comment on column canonical_profiles.profile_payload is
'Optional structured profile snapshot for API convenience and future evolution.';

create index if not exists idx_canonical_profiles_run_id
    on canonical_profiles(resolution_run_id);

create index if not exists idx_canonical_profiles_display_name
    on canonical_profiles(lower(display_name))
    where display_name is not null;

create index if not exists idx_canonical_profiles_confidence
    on canonical_profiles(confidence_level);

create index if not exists idx_canonical_profiles_created_at
    on canonical_profiles(created_at desc);

create index if not exists idx_canonical_profiles_skills
    on canonical_profiles using gin(inferred_skills);

drop trigger if exists trg_canonical_profiles_updated_at on canonical_profiles;

create trigger trg_canonical_profiles_updated_at
before update on canonical_profiles
for each row
execute function set_updated_at();

-- ─────────────────────────────────────────────
-- 5. profile_source_links
-- ─────────────────────────────────────────────

create table if not exists profile_source_links (
    id                    uuid primary key default gen_random_uuid(),

    profile_id            uuid not null references canonical_profiles(id) on delete cascade,
    source_account_id     uuid not null references source_accounts(id) on delete cascade,

    confidence_score      numeric(5,4) not null
                          check (confidence_score >= 0 and confidence_score <= 1),

    decision              match_decision not null,

    relationship_type     source_relationship_type not null,
    verification_status   verification_status not null,

    positive_signal_count integer not null default 0
                          check (positive_signal_count >= 0),

    negative_signal_count integer not null default 0
                          check (negative_signal_count >= 0),

    has_high_conflict     boolean not null default false,

    decision_payload      jsonb not null default '{}'::jsonb
                          check (jsonb_typeof(decision_payload) = 'object'),

    created_at            timestamptz not null default now(),

    unique (profile_id, source_account_id),

    constraint profile_source_links_decision_relationship_consistency
        check (
            (
                decision = 'reject'
                and relationship_type = 'rejected'
                and verification_status = 'rejected'
            )
            or
            (
                decision = 'needs_review'
                and relationship_type in ('possible_alias', 'secondary', 'alias')
                and verification_status in ('needs_review', 'likely_same_person', 'claimed_by_input')
            )
            or
            (
                decision = 'auto_match'
                and relationship_type in ('primary', 'secondary', 'alias')
                and verification_status in (
                    'claimed_by_input',
                    'evidence_matched',
                    'reciprocal_link_verified',
                    'likely_same_person'
                )
            )
        ),

    constraint profile_source_links_auto_match_requires_evidence
        check (
            decision <> 'auto_match'
            or (
                confidence_score >= 0.85
                and has_high_conflict = false
                and (
                    positive_signal_count >= 2
                    or verification_status = 'claimed_by_input'
                )
            )
        )
);

comment on table profile_source_links is
'Decision table linking canonical profiles to source accounts with confidence and verification status.';

comment on constraint profile_source_links_auto_match_requires_evidence
on profile_source_links is
'Prevents auto_match decisions from being created without enough positive evidence.';

create index if not exists idx_profile_source_links_profile_id
    on profile_source_links(profile_id);

create index if not exists idx_profile_source_links_source_account_id
    on profile_source_links(source_account_id);

create index if not exists idx_profile_source_links_decision
    on profile_source_links(decision);

create index if not exists idx_profile_source_links_confidence
    on profile_source_links(confidence_score desc);

alter table resolution_runs
add column if not exists result_summary jsonb not null default '{}'::jsonb;

alter table profile_source_links
add column if not exists decision_payload jsonb not null default '{}'::jsonb;

-- ─────────────────────────────────────────────
-- 6. match_evidence
-- ─────────────────────────────────────────────

create table if not exists match_evidence (
    id                      uuid primary key default gen_random_uuid(),

    resolution_run_id       uuid references resolution_runs(id) on delete cascade,

    profile_source_link_id  uuid references profile_source_links(id) on delete cascade,

    -- Optional account IDs make pairwise evidence traceable.
    source_account_a_id     uuid references source_accounts(id) on delete set null,
    source_account_b_id     uuid references source_accounts(id) on delete set null,

    signal_type             text not null check (length(trim(signal_type)) > 0),
    direction               evidence_direction not null,

    -- Signed weight:
    --   positive evidence: > 0
    --   negative evidence: < 0
    --   neutral evidence: = 0
    signal_weight           numeric(6,4) not null,

    source_a                platform_source,
    source_b                platform_source,

    field_name              text,
    field_value_a           text,
    field_value_b           text,

    explanation             text not null check (length(trim(explanation)) > 0),

    created_at              timestamptz not null default now(),

    constraint match_evidence_weight_direction_consistency
        check (
            (direction = 'positive' and signal_weight > 0)
            or
            (direction = 'negative' and signal_weight < 0)
            or
            (direction = 'neutral' and signal_weight = 0)
        ),

    constraint match_evidence_audit_anchor_present
        check (
            profile_source_link_id is not null
            or resolution_run_id is not null
        )
);

comment on table match_evidence is
'Individual evidence items explaining why a source account was matched, reviewed, or rejected.';

create index if not exists idx_match_evidence_run_id
    on match_evidence(resolution_run_id);

create index if not exists idx_match_evidence_link_id
    on match_evidence(profile_source_link_id);

create index if not exists idx_match_evidence_signal_type
    on match_evidence(signal_type);

create index if not exists idx_match_evidence_direction
    on match_evidence(direction);

create index if not exists idx_match_evidence_source_accounts
    on match_evidence(source_account_a_id, source_account_b_id);

-- ─────────────────────────────────────────────
-- 7. profile_conflicts
-- ─────────────────────────────────────────────

create table if not exists profile_conflicts (
    id              uuid primary key default gen_random_uuid(),

    resolution_run_id uuid references resolution_runs(id) on delete cascade,

    profile_id      uuid references canonical_profiles(id) on delete cascade,

    field_name      text not null check (length(trim(field_name)) > 0),
    severity        conflict_severity not null,

    -- Negative impact applied to confidence.
    impact          numeric(6,4) not null check (impact <= 0),

    source_values   jsonb not null check (jsonb_typeof(source_values) = 'array'),

    explanation     text not null check (length(trim(explanation)) > 0),

    created_at      timestamptz not null default now(),

    constraint profile_conflicts_audit_anchor_present
        check (
            profile_id is not null
            or resolution_run_id is not null
        )
);

comment on table profile_conflicts is
'Conflicting field values found across linked source accounts. Used for transparency and confidence reduction.';

create index if not exists idx_profile_conflicts_run_id
    on profile_conflicts(resolution_run_id);

create index if not exists idx_profile_conflicts_profile_id
    on profile_conflicts(profile_id);

create index if not exists idx_profile_conflicts_field_name
    on profile_conflicts(field_name);

create index if not exists idx_profile_conflicts_severity
    on profile_conflicts(severity);

-- ─────────────────────────────────────────────
-- 8. profile_facts
-- ─────────────────────────────────────────────

create table if not exists profile_facts (
    id                    uuid primary key default gen_random_uuid(),

    profile_id            uuid not null references canonical_profiles(id) on delete cascade,

    source_account_id     uuid references source_accounts(id) on delete set null,
    raw_source_record_id  uuid references raw_source_records(id) on delete set null,

    source                platform_source not null,

    fact_type             text not null check (length(trim(fact_type)) > 0),
    value                 text not null check (length(trim(value)) > 0),

    confidence            numeric(5,4) not null default 1.0
                          check (confidence >= 0 and confidence <= 1),

    metadata              jsonb not null default '{}'::jsonb
                          check (jsonb_typeof(metadata) = 'object'),

    created_at            timestamptz not null default now(),

    unique (profile_id, source, fact_type, value)
);

comment on table profile_facts is
'Normalized profile facts such as skills, GitHub languages, dev.to tags, Stack Overflow tags, HN topics, repo names, and article titles.';

create index if not exists idx_profile_facts_profile_id
    on profile_facts(profile_id);

create index if not exists idx_profile_facts_source_account_id
    on profile_facts(source_account_id);

create index if not exists idx_profile_facts_fact_type
    on profile_facts(fact_type);

create index if not exists idx_profile_facts_source
    on profile_facts(source);

create index if not exists idx_profile_facts_value
    on profile_facts(lower(value));

-- ─────────────────────────────────────────────
-- 9. llm_summaries
-- ─────────────────────────────────────────────

create table if not exists llm_summaries (
    id                  uuid primary key default gen_random_uuid(),

    profile_id          uuid not null references canonical_profiles(id) on delete cascade,

    model               text not null default 'gemini-2.5-flash'
                        check (length(trim(model)) > 0),

    prompt_version      text not null default 'v1'
                        check (length(trim(prompt_version)) > 0),

    prompt_text         text not null check (length(trim(prompt_text)) > 0),
    summary             text not null check (length(trim(summary)) > 0),

    input_tokens        integer check (input_tokens is null or input_tokens >= 0),
    output_tokens       integer check (output_tokens is null or output_tokens >= 0),

    estimated_cost_usd  numeric(10,6) not null default 0
                        check (estimated_cost_usd >= 0),

    created_at          timestamptz not null default now()
);

comment on table llm_summaries is
'Gemini-generated summary plus prompt version and token/cost observability.';

create index if not exists idx_llm_summaries_profile_id
    on llm_summaries(profile_id);

create index if not exists idx_llm_summaries_created_at
    on llm_summaries(created_at desc);

create index if not exists idx_llm_summaries_model
    on llm_summaries(model);

-- ─────────────────────────────────────────────
-- 10. api_call_metrics
-- ─────────────────────────────────────────────

create table if not exists api_call_metrics (
    id                    uuid primary key default gen_random_uuid(),

    resolution_run_id     uuid references resolution_runs(id) on delete set null,

    source                metric_source not null,

    endpoint              text not null check (length(trim(endpoint)) > 0),
    http_method           text not null default 'GET'
                          check (http_method in ('GET', 'POST', 'PUT', 'PATCH', 'DELETE')),

    status_code           integer check (
                              status_code is null
                              or (status_code >= 100 and status_code <= 599)
                          ),

    duration_ms           integer check (duration_ms is null or duration_ms >= 0),

    error_message         text,

    rate_limit_remaining  integer check (
                              rate_limit_remaining is null
                              or rate_limit_remaining >= 0
                          ),

    rate_limit_total      integer check (
                              rate_limit_total is null
                              or rate_limit_total >= 0
                          ),

    rate_limit_reset_at   timestamptz,

    metadata              jsonb not null default '{}'::jsonb
                          check (jsonb_typeof(metadata) = 'object'),

    created_at            timestamptz not null default now()
);

comment on table api_call_metrics is
'One row per external API call attempt, including failures/timeouts when available. Powers /health.';

create index if not exists idx_api_call_metrics_source
    on api_call_metrics(source);

create index if not exists idx_api_call_metrics_run_id
    on api_call_metrics(resolution_run_id);

create index if not exists idx_api_call_metrics_status_code
    on api_call_metrics(status_code);

create index if not exists idx_api_call_metrics_created_at
    on api_call_metrics(created_at desc);

create index if not exists idx_api_call_metrics_source_created_at
    on api_call_metrics(source, created_at desc);

create index if not exists idx_api_call_metrics_github_rate_limit
    on api_call_metrics(created_at desc)
    where source = 'github'
      and rate_limit_remaining is not null
      and rate_limit_total is not null;

-- ─────────────────────────────────────────────
-- Health / observability views
-- ─────────────────────────────────────────────

create or replace view health_profile_metrics as
select
    count(*) filter (where status = 'resolved')::integer as resolved_total,
    count(*) filter (where status = 'partial')::integer as partial_total,
    count(*) filter (where status = 'failed')::integer as failed_total,
    coalesce(
        round(avg(duration_ms) filter (
            where status in ('resolved', 'partial')
              and duration_ms is not null
        ))::integer,
        0
    ) as average_resolution_time_ms
from resolution_runs;

create or replace view health_api_call_metrics as
with sources(source) as (
    values
        ('github'::metric_source),
        ('stackoverflow'::metric_source),
        ('devto'::metric_source),
        ('hackernews'::metric_source)
)
select
    s.source,
    coalesce(count(m.id), 0)::integer as total,
    coalesce(
        count(m.id) filter (
            where m.status_code is null
               or m.status_code >= 400
               or m.error_message is not null
        ),
        0
    )::integer as errors,
    coalesce(round(avg(m.duration_ms))::integer, 0) as average_duration_ms
from sources s
left join api_call_metrics m
    on m.source = s.source
group by s.source
order by s.source;

create or replace view health_latest_github_rate_limit as
select
    rate_limit_remaining as remaining,
    rate_limit_total as limit,
    rate_limit_reset_at as reset_at,
    created_at as observed_at
from api_call_metrics
where source = 'github'
  and rate_limit_remaining is not null
  and rate_limit_total is not null
order by created_at desc
limit 1;

create or replace view health_llm_metrics as
select
    coalesce(count(*), 0)::integer as summaries_generated,
    coalesce(sum(input_tokens), 0)::integer as total_input_tokens,
    coalesce(sum(output_tokens), 0)::integer as total_output_tokens,
    coalesce(sum(estimated_cost_usd), 0)::numeric(10,6) as estimated_cost_usd,
    coalesce(max(model), 'gemini-2.5-flash') as latest_model
from llm_summaries;

-- ─────────────────────────────────────────────
-- Row Level Security
-- ─────────────────────────────────────────────
-- Backend uses SUPABASE_SERVICE_ROLE_KEY.
-- Service role bypasses RLS, while anon/public clients have no direct access.
-- No public policies are created intentionally.

alter table resolution_runs enable row level security;
alter table raw_source_records enable row level security;
alter table source_accounts enable row level security;
alter table canonical_profiles enable row level security;
alter table profile_source_links enable row level security;
alter table match_evidence enable row level security;
alter table profile_conflicts enable row level security;
alter table profile_facts enable row level security;
alter table llm_summaries enable row level security;
alter table api_call_metrics enable row level security;


-- ─────────────────────────────────────────────
-- Explicit API role permissions
-- ─────────────────────────────────────────────
-- This project is backend-only. The FastAPI server uses the service role key.
-- anon/authenticated clients should not directly access tables or views.

revoke all on table resolution_runs from anon, authenticated;
revoke all on table raw_source_records from anon, authenticated;
revoke all on table source_accounts from anon, authenticated;
revoke all on table canonical_profiles from anon, authenticated;
revoke all on table profile_source_links from anon, authenticated;
revoke all on table match_evidence from anon, authenticated;
revoke all on table profile_conflicts from anon, authenticated;
revoke all on table profile_facts from anon, authenticated;
revoke all on table llm_summaries from anon, authenticated;
revoke all on table api_call_metrics from anon, authenticated;

revoke all on table health_profile_metrics from anon, authenticated;
revoke all on table health_api_call_metrics from anon, authenticated;
revoke all on table health_latest_github_rate_limit from anon, authenticated;
revoke all on table health_llm_metrics from anon, authenticated;

grant all on table resolution_runs to service_role;
grant all on table raw_source_records to service_role;
grant all on table source_accounts to service_role;
grant all on table canonical_profiles to service_role;
grant all on table profile_source_links to service_role;
grant all on table match_evidence to service_role;
grant all on table profile_conflicts to service_role;
grant all on table profile_facts to service_role;
grant all on table llm_summaries to service_role;
grant all on table api_call_metrics to service_role;

grant select on table health_profile_metrics to service_role;
grant select on table health_api_call_metrics to service_role;
grant select on table health_latest_github_rate_limit to service_role;
grant select on table health_llm_metrics to service_role;
