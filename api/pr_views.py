"""Build the PR (Progress Review) table.

For every learner in "LMS"."Aptem_users" we fetch their Progress Review /
Personal Support Plan reviews from the Aptem Reviews OData endpoint (scoped to
the learner's programme), normalise them (a Python port of the n8n JavaScript
node in PR.md) and upsert one row per learner into "LMS"."PR". Rows for
learners no longer present are deleted.

Exposed as the /api/sync-pr/ endpoint and also called by the scheduler.
"""
import datetime

import psycopg2
import requests
from psycopg2.extras import execute_values
from django.http import JsonResponse

from .views import DATABASE_URL, _fetch_paged

REVIEWS_BASE = "https://kentbusinesscollege.aptem.co.uk/odata/1.0/Reviews"

# Number of "Review Planned Date<n>"/"Review Status<n>" column pairs.
REVIEW_SLOTS = 16

# The review Types this table covers (mirrors the $filter in PR.md). The
# "%2B" sequences in PR.md are the URL-encoded "+" of "(+ Skills Radar)".
_PR_TYPES = (
    "Personal Support Plan",
    "Progress Review",
    "Progress Review ",
    "Progress Review (+ Skills Radar)",
    "Progress Review (+ Skills Radar) ",
)


# --- Port of the JS helpers in PR.md --------------------------------------

def _format_ddmmyyyy(iso):
    if not iso or not isinstance(iso, str):
        return None
    d = iso[:10]
    parts = d.split("-")
    if len(parts) != 3:
        return None
    yyyy, mm, dd = parts
    if not (yyyy and mm and dd):
        return None
    return f"{dd}-{mm}-{yyyy}"


def _pretty_status(status):
    if not status or not isinstance(status, str):
        return None
    out = []
    for i, ch in enumerate(status):
        if i > 0 and ch.isupper() and status[i - 1].islower():
            out.append(" ")
        out.append(ch)
    return "".join(out).strip()


def _build_component_name(c):
    return (c.get("Type") or c.get("Name") or "").strip()


def _normalize_text(v):
    return "".join(str(v or "").lower().split())


def _is_completed_review_status(status_raw):
    return _normalize_text(status_raw) == "completed"


def _is_pending_review_status(status_raw):
    return not _is_completed_review_status(status_raw)


def _is_progress_review(c):
    t = (c.get("Type") or c.get("Name") or "").lower()
    return "personal support plan" not in t


def _to_ms(iso):
    if not iso or not isinstance(iso, str):
        return 0
    try:
        dt = datetime.datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.timestamp() * 1000.0
    except (ValueError, TypeError):
        return 0


def _normalize_status(c):
    return c.get("Status")


def _to_odata_iso(iso):
    if not iso or not isinstance(iso, str):
        return iso
    return iso.replace("+", "%2B")


def _build_completed_date(c, effective_status_raw, status_label):
    completed_source = c.get("CompletedDate") or c.get("UpdatedDate")
    completed_date = _format_ddmmyyyy(completed_source)
    planned_date = _format_ddmmyyyy(c.get("PlannedDate"))
    status_norm = _normalize_text(effective_status_raw)

    if completed_date and status_label:
        return f"{completed_date} ({status_label})"
    if completed_date:
        return completed_date
    if status_norm == "scheduled" and planned_date:
        return f"{planned_date} ({status_label or 'Scheduled'})"
    if status_label:
        return status_label
    return "Not Started"


def _build_last_pr_completed_date(c, effective_status_raw, status_label):
    completed_source = c.get("CompletedDate") or c.get("UpdatedDate")
    completed_date = _format_ddmmyyyy(completed_source)
    planned_date = _format_ddmmyyyy(c.get("PlannedDate"))
    status_norm = _normalize_text(effective_status_raw)

    if completed_date and status_label:
        return f"{completed_date} ({status_label})"
    if completed_date:
        return completed_date
    if status_norm in ("scheduled", "notscheduled", "inprogress", "awaitingsignature") and planned_date:
        return f"{planned_date} ({status_label or c.get('Status') or 'Status'})"
    if status_label:
        return status_label
    return "Not Started"


def _normalize_learner(components, now_ms):
    """Port of the per-learner loop in the PR.md JS node."""
    components = list(components)
    components.sort(key=lambda c: str(c.get("PlannedDate") or ""))

    rows = []
    last_pr = None
    best_past_due = -1
    next_pr = None
    last_actually_completed = None
    best_completed_ms = -1

    for c in components:
        planned_date = _format_ddmmyyyy(c.get("PlannedDate"))
        effective_status_raw = _normalize_status(c)
        status_label = _pretty_status(effective_status_raw)
        completed_date = _build_completed_date(c, effective_status_raw, status_label)

        rows.append({
            "component_name": _build_component_name(c),
            "planned_date": planned_date,
            "completed_date": completed_date,
            "status": status_label,
        })

        completed_ms = _to_ms(c.get("CompletedDate"))
        if _is_progress_review(c) and completed_ms > 0 and completed_ms > best_completed_ms:
            best_completed_ms = completed_ms
            last_actually_completed = {
                "component_name": _build_component_name(c),
                "planned_date": _format_ddmmyyyy(c.get("PlannedDate")),
                "completed_date": _format_ddmmyyyy(c.get("CompletedDate")),
                "status": status_label,
            }

        if _is_progress_review(c):
            due_ms = _to_ms(c.get("PlannedDate"))
            if due_ms > 0 and due_ms < now_ms and due_ms > best_past_due:
                best_past_due = due_ms
                last_pr = {
                    "component_name": _build_component_name(c),
                    "planned_date": _format_ddmmyyyy(c.get("PlannedDate")),
                    "completed_date": _build_last_pr_completed_date(c, effective_status_raw, status_label),
                    "status": status_label,
                }

        if _is_progress_review(c) and _is_pending_review_status(effective_status_raw):
            due_ms = _to_ms(c.get("PlannedDate"))
            if due_ms > now_ms and (next_pr is None or due_ms < _to_ms(next_pr.get("PlannedDate"))):
                next_pr = c

    last_pr_text = (last_pr or {}).get("completed_date") or "Not Yet"

    if next_pr is not None:
        next_pr_summary = {
            "component_name": _build_component_name(next_pr),
            "planned_date": _format_ddmmyyyy(next_pr.get("PlannedDate")),
            "status": _pretty_status(_normalize_status(next_pr)),
        }
        next_pr_text = _to_odata_iso(next_pr.get("PlannedDate"))
    else:
        next_pr_summary = "Not Found"
        next_pr_text = "Not Found"

    if last_actually_completed is not None:
        last_completed_text = (
            f"{last_actually_completed['completed_date']} "
            f"({last_actually_completed.get('status') or 'Status'})"
        )
    else:
        last_completed_text = "Not Yet"

    return {
        "rows": rows,
        "last_pr": last_pr,
        "last_pr_text": last_pr_text,
        "next_pr": next_pr_summary,
        "next_pr_text": next_pr_text,
        "last_actually_completed": last_actually_completed,
        "last_actually_completed_text": last_completed_text,
    }


# --- DB access -------------------------------------------------------------

def _fetch_learners():
    """Read the learner list and the per-learner columns PR reuses from
    "LMS"."Aptem_users"."""
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                'SELECT "ID", "FullName", "Email", "Group", "OwnerName", '
                '"Program-Status", "case_owner_id", "Program Name", '
                '"ManagerName", "ManagerEmail" '
                'FROM "LMS"."Aptem_users"'
            )
            cols = ("ID", "FullName", "Email", "Group", "CaseOwner",
                    "Status", "case_owner_id", "programme",
                    "Manager Name", "Manager Email")
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def _fetch_reviews_by_learner():
    """Fetch all Progress Review / PSP reviews in one paged sweep and group them
    by LearnerId. One bulk call replaces one request per learner; the
    per-learner programme scoping is applied in memory in run_sync()."""
    type_clause = " or ".join(f"Type eq '{t}'" for t in _PR_TYPES)
    flt = f"({type_clause})"
    safe_chars = "()' "
    url = f"{REVIEWS_BASE}?$filter={requests.utils.quote(flt, safe=safe_chars)}"
    by_learner = {}
    for row in _fetch_paged(url, timeout=180):
        by_learner.setdefault(row.get("LearnerId"), []).append(row)
    return by_learner


def _build_row(learner, norm):
    """Build the ordered tuple matching INSERT_SQL's column list."""
    rows = norm["rows"]
    last_pr = norm["last_pr"]
    nxt = norm["next_pr"]

    last_progress_review = last_pr["completed_date"] if last_pr else norm["last_pr_text"]
    if nxt != "Not Found":
        next_review_status = f"{nxt['planned_date']} ({nxt['status'] or 'Status'})"
    else:
        next_review_status = "Not Found"

    values = [
        learner["FullName"],
        learner["Email"],
        learner["Group"],
        learner["CaseOwner"],
        last_progress_review,  # Last Progress Review
    ]

    # Review Planned Date<n> / Review Status<n>, sequential by planned date.
    for i in range(REVIEW_SLOTS):
        if i < len(rows):
            values.append(rows[i]["planned_date"])
            values.append(rows[i]["status"])
        else:
            values.append(None)
            values.append(None)

    values.extend([
        learner["Status"],                          # Status
        learner["ID"],                              # ID (PK)
        learner["case_owner_id"],                   # case_owner_id
        next_review_status,                         # Next Review (Status)
        norm["last_actually_completed_text"],       # Last Actually Completed PR
        learner["programme"],                       # programme
        learner["Manager Name"],
        learner["Manager Email"],
    ])
    return tuple(values)


def _build_insert_sql():
    review_cols = []
    review_updates = []
    for i in range(1, REVIEW_SLOTS + 1):
        review_cols.append(f'"Review Planned Date{i}"')
        review_cols.append(f'"Review Status{i}"')
        review_updates.append(f'"Review Planned Date{i}" = EXCLUDED."Review Planned Date{i}"')
        review_updates.append(f'"Review Status{i}" = EXCLUDED."Review Status{i}"')

    columns = (
        ['"FullName"', '"Email"', '"Group"', '"CaseOwner"', '"Last Progress Review"']
        + review_cols
        + ['"Status"', '"ID"', '"case_owner_id"', '"Next Review (Status)"',
           '"Last Actually Completed PR"', '"programme"',
           '"Manager Name"', '"Manager Email"']
    )

    updates = (
        ['"FullName" = EXCLUDED."FullName"',
         '"Email" = EXCLUDED."Email"',
         '"Group" = EXCLUDED."Group"',
         '"CaseOwner" = EXCLUDED."CaseOwner"',
         '"Last Progress Review" = EXCLUDED."Last Progress Review"']
        + review_updates
        + ['"Status" = EXCLUDED."Status"',
           '"case_owner_id" = EXCLUDED."case_owner_id"',
           '"Next Review (Status)" = EXCLUDED."Next Review (Status)"',
           '"Last Actually Completed PR" = EXCLUDED."Last Actually Completed PR"',
           '"programme" = EXCLUDED."programme"',
           '"Manager Name" = EXCLUDED."Manager Name"',
           '"Manager Email" = EXCLUDED."Manager Email"']
    )

    return (
        'INSERT INTO "LMS"."PR" (\n  '
        + ", ".join(columns)
        + "\n) VALUES %s\nON CONFLICT (\"ID\") DO UPDATE SET\n  "
        + ",\n  ".join(updates)
    )


INSERT_SQL = _build_insert_sql()


def run_sync():
    """Fetch Progress Reviews for every learner, upsert one PR row each, and
    delete rows for learners no longer present. Returns counts."""
    learners = _fetch_learners()
    reviews_by_learner = _fetch_reviews_by_learner()
    now_ms = datetime.datetime.now(datetime.timezone.utc).timestamp() * 1000.0

    rows = []
    for learner in learners:
        if learner["ID"] is None:
            continue
        components = reviews_by_learner.get(learner["ID"], [])
        # Scope to the learner's programme in memory (the original OData filter
        # added "and ProgramName eq '<programme>'").
        program_name = learner["programme"]
        if program_name:
            components = [c for c in components if c.get("ProgramName") == program_name]
        norm = _normalize_learner(components, now_ms)
        rows.append(_build_row(learner, norm))

    # The ID column is the 7th element of each tuple (see _build_row order).
    id_index = 5 + REVIEW_SLOTS * 2 + 1
    current_ids = [r[id_index] for r in rows]

    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            if rows:
                execute_values(cur, INSERT_SQL, rows)
            deleted = 0
            if current_ids:
                cur.execute(
                    'DELETE FROM "LMS"."PR" WHERE "ID" <> ALL(%s)',
                    (current_ids,),
                )
                deleted = cur.rowcount
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {"upserted": len(rows), "deleted": deleted}


def sync_pr(_request):
    try:
        result = run_sync()
        return JsonResponse({"status": "ok", **result})
    except requests.HTTPError as e:
        return JsonResponse({"status": "error", "detail": f"Aptem API error: {e}"}, status=502)
    except Exception as e:
        return JsonResponse({"status": "error", "detail": str(e)}, status=500)
