create table if not exists buyer_task_comparisons (
  task_key text primary key,
  query_category text not null,
  query_preview text not null,
  seller_a jsonb not null,
  seller_b jsonb not null,
  preferred_seller_url text not null default '',
  minimum_acceptable_score numeric not null default 6,
  needs_rebrowse boolean not null default false,
  updated_at timestamptz not null default now()
);

create index if not exists buyer_task_comparisons_category_idx
  on buyer_task_comparisons (query_category);

create index if not exists buyer_task_comparisons_updated_idx
  on buyer_task_comparisons (updated_at desc);
