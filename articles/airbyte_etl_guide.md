# Replacing Custom Python ETL Scripts with Airbyte: A Practical Migration Guide

*When hand-rolled data pipelines become a maintenance liability*

---

Every data engineer has been here: a Python script that started as a weekend project to pull data from one API and load it into PostgreSQL. Six months later, that script is 600 lines long, handles twelve edge cases, has three environment variables nobody documented, and breaks whenever the source API releases a new version.

I built exactly this kind of script for HN Startup Hunter — a service that extracts startup job postings from Hacker News "Who is Hiring" threads and structures them for analysis. The initial script was 80 lines. By the time it was handling pagination, rate limits, incremental updates, and multiple thread formats, it was nearly 400 lines of bespoke ETL code that I was personally responsible for maintaining.

This guide walks through migrating that kind of hand-rolled Python ETL to Airbyte, covering the practical decisions, the trade-offs, and the cases where custom code still wins.

## The Anatomy of a Hand-Rolled Python ETL

Before migrating anything, it helps to be honest about what you've built. A typical hand-written Python ETL has five concerns:

```python
# 1. Authentication & rate limiting
session = requests.Session()
session.headers["Authorization"] = f"Bearer {os.environ['API_KEY']}"

# 2. Fetching with pagination
def fetch_all_pages(endpoint, params):
    results = []
    page = 1
    while True:
        r = session.get(endpoint, params={**params, "page": page})
        r.raise_for_status()
        data = r.json()
        results.extend(data["items"])
        if not data.get("next_page"):
            break
        page += 1
    return results

# 3. Transformation
def transform_record(raw):
    return {
        "id": raw["objectID"],
        "company": extract_company(raw["text"]),
        "location": extract_location(raw["text"]),
        "created_at": datetime.fromtimestamp(raw["created_at"]),
    }

# 4. State management (incremental sync)
def load_checkpoint():
    try:
        return json.load(open("checkpoint.json"))
    except FileNotFoundError:
        return {"last_updated": None}

def save_checkpoint(state):
    json.dump(state, open("checkpoint.json", "w"))

# 5. Loading
def load_to_postgres(records):
    with psycopg2.connect(os.environ["DATABASE_URL"]) as conn:
        with conn.cursor() as cur:
            for r in records:
                cur.execute("""
                    INSERT INTO jobs (...) VALUES (%s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET ...
                """, list(r.values()))
```

This is maintainable code in isolation. The problem is that you write it for every new data source. Your third pipeline is still 80% boilerplate, and now you maintain three checkpointing systems, three rate limiters, and three upsert patterns.

## What Airbyte Handles For You

Airbyte is an ELT platform that standardizes the five concerns above:

- **Authentication**: OAuth2, API key, basic auth — configured in the UI, credentials stored securely
- **Pagination**: cursor, offset, limit — configured declaratively, not coded
- **State management**: Airbyte handles incremental sync state between runs automatically
- **Schema**: Airbyte infers schema from source and syncs it to destination
- **Loading**: the destination connector handles upserts, schema migrations, and batching

The core abstraction is **streams** — named collections of records with a primary key and optional cursor field for incremental sync.

## Setting Up a Source: HN Algolia API

The Hacker News "Who is Hiring" data is available via the Algolia HN API. Here's how the same extraction looks as an Airbyte custom connector vs. the hand-written version.

### The Hand-Written Version

```python
BASE_URL = "https://hn.algolia.com/api/v1"

def fetch_hn_jobs(thread_id: int, checkpoint: dict) -> list[dict]:
    last_seen = checkpoint.get("last_created_at", 0)
    jobs = []
    page = 0
    
    while True:
        r = requests.get(f"{BASE_URL}/search", params={
            "tags": f"comment,story_{thread_id}",
            "hitsPerPage": 100,
            "page": page,
            "numericFilters": f"created_at_i>{last_seen}"
        })
        data = r.json()
        jobs.extend(data["hits"])
        
        if page >= data["nbPages"] - 1:
            break
        page += 1
    
    return jobs
```

### The Airbyte Connector Version

Using the Connector Builder, you define the same logic declaratively in YAML:

```yaml
version: "0.29.0"
type: DeclarativeSource

streams:
  - type: DeclarativeStream
    name: hn_job_comments
    primary_key: objectID
    
    incremental_sync:
      type: DatetimeBasedCursor
      cursor_field: created_at_i
      datetime_format: "%s"
      start_datetime:
        type: MinMaxDatetime
        datetime: "{{ config['start_timestamp'] }}"
      end_datetime:
        type: MinMaxDatetime
        datetime: "{{ now_utc() }}"
    
    retriever:
      type: SimpleRetriever
      requester:
        type: HttpRequester
        url_base: "https://hn.algolia.com/api/v1/"
        path: "search"
        http_method: GET
        request_parameters:
          tags: "comment,story_{{ config['thread_id'] }}"
          hitsPerPage: "100"
          numericFilters: "created_at_i>{{ stream_slice.start_time }}"
      
      paginator:
        type: PageIncrement
        page_size: 100
        
      record_selector:
        type: RecordSelector
        extractor:
          type: DpathExtractor
          field_path: ["hits"]

check:
  type: CheckStream
  stream_names: ["hn_job_comments"]
```

The YAML version is more verbose, but Airbyte handles the execution: retry logic, rate limit backoff, state persistence between runs, and destination loading.

## When to Migrate vs. When to Keep Custom Code

Not every Python ETL is worth migrating. Here's the practical breakdown:

### Migrate to Airbyte when:

**You're pulling from multiple sources into the same warehouse.** Airbyte's catalog approach shines when you have 5+ sources. Managing incremental sync state for HubSpot, Stripe, and a custom API in separate Python scripts creates compounding maintenance overhead. Airbyte centralizes this.

**Your team isn't Python-fluent.** Airbyte's UI lets non-engineers inspect sync history, replay failed syncs, and update credentials. A Python script in a cron job is opaque to everyone except its author.

**You need schema evolution handled automatically.** When source APIs add new fields, Airbyte can auto-propagate schema changes to the destination. With custom scripts, you catch this in production when the INSERT fails.

**The source has an existing Airbyte connector.** Postgres, Stripe, HubSpot, Salesforce, Shopify — there are 350+ production connectors. Using a maintained connector means you get bug fixes without doing anything.

### Keep custom Python when:

**Your transformation logic is complex.** Airbyte is ELT — extract and load first, then transform in the destination (usually with dbt). If your business logic has to happen during extraction, you're fighting the architecture.

**Your source requires custom authentication.** OAuth flows with non-standard token refresh, HMAC-signed requests, session cookies — these can be implemented in the Connector Builder, but complex flows take time. A Python script might be faster.

**You need sub-minute latency.** Airbyte syncs run on a schedule (minimum 5-minute intervals in most deployments). If you need event-driven ingestion on 10-second intervals, a custom consumer is the right tool.

**The data volume is tiny.** For a single small table synced once a day, Airbyte's infrastructure overhead isn't worth it. A 30-line Python script in a GitHub Actions cron is simpler.

## The Migration Process

If you've decided to migrate, here's a practical sequence:

### 1. Inventory your custom scripts

Before touching Airbyte, document what each script does:

```
pipeline_name | source | destination | schedule | primary_key | incremental? | transforms?
hn_jobs       | HN API | postgres    | hourly   | objectID    | yes          | parse text
stripe_subs   | Stripe | postgres    | daily    | subscription_id | yes     | none
```

Scripts without complex transforms and with a defined primary key are migration candidates.

### 2. Find or build the connector

Check the [Airbyte connector catalog](https://airbyte.com/connectors) first. If no connector exists, the Connector Builder handles REST APIs with standard auth and pagination patterns in under an hour.

### 3. Run both in parallel

Before decommissioning the custom script, run both and compare outputs:

```python
# Validation script
import pandas as pd
import psycopg2

# Compare record counts
script_count = pd.read_sql("SELECT COUNT(*) FROM hn_jobs_custom", conn)
airbyte_count = pd.read_sql("SELECT COUNT(*) FROM hn_jobs_airbyte", conn)

# Compare field values on a sample
merged = pd.merge(
    pd.read_sql("SELECT objectID, company FROM hn_jobs_custom LIMIT 1000", conn),
    pd.read_sql("SELECT objectID, company FROM hn_jobs_airbyte LIMIT 1000", conn),
    on="objectID",
    suffixes=("_custom", "_airbyte")
)
discrepancies = merged[merged.company_custom != merged.company_airbyte]
print(f"{len(discrepancies)} discrepancies in 1000 records")
```

### 4. Migrate incrementally

Don't migrate everything at once. Pick one low-risk pipeline, run parallel for a week, validate, decommission the custom script, then move to the next.

## Practical Trade-Offs After Six Months

After migrating the HN job extraction to Airbyte and leaving the text parsing in a dbt transformation, the practical impact was:

**Reduced maintenance overhead.** The connection between HN API and PostgreSQL became Airbyte's problem. When the API added a new field, Airbyte detected the schema change and proposed a migration. Previously, the INSERT would silently fail at 2am.

**Better visibility.** Airbyte's sync history UI shows every run's record count, bytes transferred, and any errors. The Python script showed up as a line in cron logs that nobody read.

**Increased complexity in one area.** The Connector Builder YAML has a learning curve. Complex pagination or multi-step authentication requires time to model correctly. The first custom connector took three hours; subsequent ones took 30 minutes.

**Lost flexibility in transforms.** Moving transforms to dbt meant a longer feedback loop during development. A Python script with a `print()` statement is faster to debug than a failing dbt model.

## Conclusion

Hand-rolled Python ETL scripts aren't wrong — they're often the fastest path to a working pipeline. The migration question is maintenance: when the third version of the same connection pattern hits your backlog, that's when Airbyte's connector catalog and declarative configuration start paying off.

The sweet spot: Airbyte for well-structured REST APIs with defined schemas and incremental cursors. Custom Python for complex transforms, unusual auth flows, or data sources with non-standard pagination. The two approaches are complementary, not competing — most mature data stacks use both.

---

*Brad is a Python automation engineer who builds data pipelines and developer tools. His current project, HN Startup Hunter (hn-startup-hunter.onrender.com), uses Algolia's HN API for real-time startup lead generation.*
