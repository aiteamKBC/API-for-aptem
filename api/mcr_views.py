"""Build the MCR (Monthly Coaching Meeting) table.

For every learner in "LMS"."Aptem_users" we fetch their Monthly Coaching
Meeting reviews from the Aptem Reviews OData endpoint, normalise them (a Python
port of the n8n JavaScript node in MCR.md) and upsert one row per learner into
"LMS"."MCR". Rows for learners no longer present are deleted.

Exposed as the /api/sync-mcr/ endpoint and also called by the scheduler.
"""
import datetime

import psycopg2
import requests
from psycopg2.extras import execute_values
from django.http import JsonResponse

from .views import DATABASE_URL, HEADERS, _fetch_paged

REVIEWS_BASE = "https://kentbusinesscollege.aptem.co.uk/odata/1.0/Reviews"

# Number of MCM<n>/Status<n> column pairs in the MCR table.
MCM_SLOTS = 22


# --- Port of the JS helpers in MCR.md -------------------------------------

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


def _is_completed_status(c):
    if c.get("Completed") is True:
        return True
    return _normalize_text(c.get("Status")) == "completed"


def _is_pending_status(c):
    return not _is_completed_status(c)


def _is_monthly_coaching(c):
    t = (c.get("Type") or c.get("Name") or "").lower()
    return "monthly coaching" in t


def _to_ms(iso):
    if not iso or not isinstance(iso, str):
        return 0
    try:
        # Aptem dates are ISO 8601; tolerate a trailing Z.
        dt = datetime.datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.timestamp() * 1000.0
    except (ValueError, TypeError):
        return 0


def _normalize_status(c):
    if c.get("Completed") is True:
        return "Completed"
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


def _build_last_coaching_completed_date(c, effective_status_raw, status_label):
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
    """Port of the per-learner loop in the MCR.md JS node.

    Returns a dict with rows + the Last/Next/LastActuallyCompleted summaries.
    """
    coaching = [c for c in components if _is_monthly_coaching(c)]
    coaching.sort(key=lambda c: str(c.get("PlannedDate") or ""))

    rows = []
    last_coaching = None
    best_past_due = -1
    next_coaching = None
    last_actually_completed = None
    best_completed_ms = -1

    for c in coaching:
        planned_date = _format_ddmmyyyy(c.get("PlannedDate"))
        effective_status_raw = _normalize_status(c)
        status_label = _pretty_status(effective_status_raw)
        completed_date = _build_completed_date(c, effective_status_raw, status_label)

        rows.append({
            "component_name": c.get("Name") or _build_component_name(c),
            "planned_date": planned_date,
            "completed_date": completed_date,
            "status": status_label,
            "ownerName": c.get("OwnerName"),
            "programName": c.get("ProgramName"),
        })

        completed_ms = _to_ms(c.get("CompletedDate"))
        if completed_ms > 0 and completed_ms > best_completed_ms:
            best_completed_ms = completed_ms
            last_actually_completed = {
                "component_name": c.get("Name") or _build_component_name(c),
                "planned_date": _format_ddmmyyyy(c.get("PlannedDate")),
                "completed_date": _format_ddmmyyyy(c.get("CompletedDate")),
                "status": status_label,
                "ownerName": c.get("OwnerName"),
            }

        due_ms = _to_ms(c.get("PlannedDate"))
        if due_ms > 0 and due_ms < now_ms and due_ms > best_past_due:
            best_past_due = due_ms
            last_coaching = {
                "component_name": c.get("Name") or _build_component_name(c),
                "planned_date": _format_ddmmyyyy(c.get("PlannedDate")),
                "completed_date": _build_last_coaching_completed_date(c, effective_status_raw, status_label),
                "status": status_label,
                "ownerName": c.get("OwnerName"),
            }

        if _is_pending_status(c):
            if due_ms > now_ms and (next_coaching is None or due_ms < _to_ms(next_coaching.get("PlannedDate"))):
                next_coaching = c

    last_coaching_text = (last_coaching or {}).get("completed_date") or "Not Yet"

    if next_coaching is not None:
        next_coaching_summary = {
            "component_name": next_coaching.get("Name") or _build_component_name(next_coaching),
            "planned_date": _format_ddmmyyyy(next_coaching.get("PlannedDate")),
            "status": _pretty_status(_normalize_status(next_coaching)),
            "ownerName": next_coaching.get("OwnerName"),
        }
        next_coaching_text = _to_odata_iso(next_coaching.get("PlannedDate"))
    else:
        next_coaching_summary = "Not Found"
        next_coaching_text = "Not Found"

    if last_actually_completed is not None:
        last_completed_text = (
            f"{last_actually_completed['completed_date']} "
            f"({last_actually_completed.get('status') or 'Status'})"
        )
    else:
        last_completed_text = "Not Yet"

    return {
        "rows": rows,
        "last_coaching": last_coaching,
        "last_coaching_text": last_coaching_text,
        "next_coaching": next_coaching_summary,
        "next_coaching_text": next_coaching_text,
        "last_actually_completed": last_actually_completed,
        "last_actually_completed_text": last_completed_text,
    }


# --- DB access -------------------------------------------------------------

def _fetch_learners():
    """Read the learner list and the per-learner columns MCR reuses from
    "LMS"."Aptem_users"."""
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                'SELECT "ID", "FullName", "Email", "Program-Status", '
                '"Subscription Status", "OwnerName", "ManagerName", "ManagerEmail" '
                'FROM "LMS"."Aptem_users"'
            )
            cols = ("ID", "FullName", "Email", "Status",
                    "Subscription Status", "CaseOwner", "Manager Name", "Manager Email")
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def _fetch_reviews_by_learner():
    """Fetch all Monthly Coaching Meeting reviews in one paged sweep and group
    them by LearnerId. One bulk call replaces one request per learner."""
    flt = "Type eq 'Monthly Coaching Meeting'"
    safe_chars = "()' "
    url = f"{REVIEWS_BASE}?$filter={requests.utils.quote(flt, safe=safe_chars)}"
    by_learner = {}
    for row in _fetch_paged(url, timeout=180):
        by_learner.setdefault(row.get("LearnerId"), []).append(row)
    return by_learner


def _build_row(learner, norm):
    """Build the ordered tuple matching INSERT_SQL's column list."""
    rows = norm["rows"]
    last = norm["last_coaching"]
    nxt = norm["next_coaching"]

    last_mcm = last["completed_date"] if last else norm["last_coaching_text"]
    next_mcm = norm["next_coaching_text"] if nxt != "Not Found" else "Not Found"

    values = [
        learner["ID"],
        learner["FullName"],
        learner["Email"],
        learner["Status"],
        learner["Subscription Status"],
        learner["CaseOwner"],
        last_mcm,    # Last MCM
        next_mcm,    # Next MCM
    ]

    # MCM<n> = planned date text, Status<n> = status label. Sequential by date.
    for i in range(MCM_SLOTS):
        if i < len(rows):
            values.append(rows[i]["planned_date"])
            values.append(rows[i]["status"])
        else:
            values.append(None)
            values.append(None)

    values.append(norm["last_actually_completed_text"])  # Last Actually Completed MCM
    values.append(learner["Manager Name"])
    values.append(learner["Manager Email"])
    return tuple(values)


def _build_insert_sql():
    mcm_cols = []
    mcm_updates = []
    for i in range(1, MCM_SLOTS + 1):
        mcm_cols.append(f'"MCM{i}"')
        mcm_cols.append(f'"Status{i}"')
        mcm_updates.append(f'"MCM{i}" = EXCLUDED."MCM{i}"')
        mcm_updates.append(f'"Status{i}" = EXCLUDED."Status{i}"')

    columns = (
        ['"ID"', '"FullName"', '"Email"', '"Status"', '"Subscription Status"',
         '"CaseOwner"', '"Last MCM"', '"Next MCM"']
        + mcm_cols
        + ['"Last Actually Completed MCM"', '"Manager Name"', '"Manager Email"']
    )

    updates = (
        ['"FullName" = EXCLUDED."FullName"',
         '"Email" = EXCLUDED."Email"',
         '"Status" = EXCLUDED."Status"',
         '"Subscription Status" = EXCLUDED."Subscription Status"',
         '"CaseOwner" = EXCLUDED."CaseOwner"',
         '"Last MCM" = EXCLUDED."Last MCM"',
         '"Next MCM" = EXCLUDED."Next MCM"']
        + mcm_updates
        + ['"Last Actually Completed MCM" = EXCLUDED."Last Actually Completed MCM"',
           '"Manager Name" = EXCLUDED."Manager Name"',
           '"Manager Email" = EXCLUDED."Manager Email"']
    )

    return (
        'INSERT INTO "LMS"."MCR" (\n  '
        + ", ".join(columns)
        + "\n) VALUES %s\nON CONFLICT (\"ID\") DO UPDATE SET\n  "
        + ",\n  ".join(updates)
    )


INSERT_SQL = _build_insert_sql()


def run_sync():
    """Fetch MCM reviews for every learner, upsert one MCR row each, and delete
    rows for learners no longer present. Returns counts."""
    learners = _fetch_learners()
    reviews_by_learner = _fetch_reviews_by_learner()
    now_ms = datetime.datetime.now(datetime.timezone.utc).timestamp() * 1000.0

    rows = []
    for learner in learners:
        if learner["ID"] is None:
            continue
        components = reviews_by_learner.get(learner["ID"], [])
        norm = _normalize_learner(components, now_ms)
        rows.append(_build_row(learner, norm))

    current_ids = [r[0] for r in rows]

    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            if rows:
                execute_values(cur, INSERT_SQL, rows)
            deleted = 0
            if current_ids:
                cur.execute(
                    'DELETE FROM "LMS"."MCR" WHERE "ID" <> ALL(%s)',
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


def sync_mcr(_request):
    try:
        result = run_sync()
        return JsonResponse({"status": "ok", **result})
    except requests.HTTPError as e:
        return JsonResponse({"status": "error", "detail": f"Aptem API error: {e}"}, status=502)
    except Exception as e:
        return JsonResponse({"status": "error", "detail": str(e)}, status=500)
