import os
import json
import datetime
import requests
import psycopg2
from psycopg2.extras import execute_values
from django.http import JsonResponse
from dotenv import load_dotenv

load_dotenv()

APTEM_TOKEN = os.getenv("aptem_X-API-Token")
DATABASE_URL = os.getenv("database_string")

_STATUS_FILTER = (
    "UserProgram_Status eq 'Onboarding'"
    " or UserProgram_Status eq 'Active'"
    " or UserProgram_Status eq 'OnBreak'"
    " or UserProgram_Status eq 'Withdrawn'"
    " or UserProgram_Status eq 'UnderReview'"
    " or UserProgram_Status eq 'ReadyToEnrol'"
)

API_URL = (
    "https://kentbusinesscollege.aptem.co.uk/odata/1.0/users"
    "?$select=Id,FullName,Email,UserILRSummary_MinimumRequiredHours,"
    "UserILRSummary_PlannedHours,UserLearningPlanSummary_SubmittedTime,"
    "UserLearningPlanSummary_CompletedTime,UserLearningPlanSummary_ForecastTime,"
    "UserLearningPlanSummary_ExpectedOffTheJobHours,UserProgram_StartDate,"
    "UserProgram_PlannedEndDate,UserILRSummary_PrimaryHealthProblem,"
    "UserProgram_CurrentProgramme,UserProgram_Status,SubscriptionStatus,"
    "UserReviews_ADET_GRModel_rag,UserILRSummary_TNPSum,UserPersonalDetails_Gender,"
    "UserPersonalDetails_Address,UserPersonalDetails_PostCode,UserPersonalDetails_Mobile,"
    "UserEmployer_Organization,UserPersonalDetails_OwnerId,UserEmployer_ManagerPhone,"
    "UserEmployer_ManagerName,UserEmployer_ManagerEmail,UserEmployer_EmployerId,"
    "UserLearningPlanSummary_ComponentsCount,"
    "UserLearningPlanSummary_CompletedComponentsCount,"
    "UserLearningPlanSummary_LearningPlanProgress,"
    "UserLearningPlanSummary_ExpectedLearningPlanProgress,"
    "UserLearningPlanSummary_OnTime,UserPersonalDetails_OwnerFullName,"
    "UserEmployer_MentorName,ComplianceDocuments_ADET_GRModel_apprenticeshipagreement,"
    "ComplianceDocuments_ADET_GRModel_trainingplan,"
    "ComplianceDocuments_ADET_GRModel_individuallearningrecord,"
    "ComplianceDocuments_ADET_GRModel_contractforservice,"
    "ComplianceDocuments_ADET_GRModel_writtenagreement,UserGroups_GroupLevel0,"
    "UserILRSummary_EmploymentWeeklyHours,UserEmployer_LevyPayer"
    f"&$filter=({_STATUS_FILTER}) and SubscriptionStatus eq 'FullUser'"
)

# Sub-programmes and markers only populate via $expand, and the server rejects
# a long $select combined with $expand. So they are fetched in a second pass
# keyed on user Id ($select=Id + $expand) and merged into each user record.
EXPAND_URL = (
    "https://kentbusinesscollege.aptem.co.uk/odata/1.0/users"
    "?$select=Id"
    "&$expand=UserProgram_SubPrograms,Markers_Markers,UserComponents_Components"
    f"&$filter=({_STATUS_FILTER}) and SubscriptionStatus eq 'FullUser'"
)

# Per-component detail (names, type, status, hours) comes from the
# LearningPlanComponents entity set, fetched once and aggregated by LearnerId.
COMPONENTS_URL = (
    "https://kentbusinesscollege.aptem.co.uk/odata/1.0/LearningPlanComponents"
    "?$select=LearnerId,ComponentName,ComponentType,Status,ActualHours"
)

USER_GROUPS_URL = (
    "https://kentbusinesscollege.aptem.co.uk/odata/1.0/UserGroups"
    "?$select=UserId,GroupId"
)

GROUPS_URL = (
    "https://kentbusinesscollege.aptem.co.uk/odata/1.0/Groups"
    "?$select=Id,Name"
)

# Map a LearningPlanComponent ComponentType to one of our evidence buckets.
_COMPONENT_CATEGORY = {
    "Assignment": "assignment",
    "OnlineLearning": "lms",
    "Miscellaneous": "extra",
}

HEADERS = {
    "X-API-Token": APTEM_TOKEN,
    "Accept": "application/json",
}


def _fetch_paged(start_url, timeout=120):
    rows = []
    url = start_url
    while url:
        response = requests.get(url, headers=HEADERS, timeout=timeout)
        response.raise_for_status()
        data = response.json()
        rows.extend(data.get("value", []))
        url = data.get("@odata.nextLink")
    return rows


# Collection navigation properties merged from the EXPAND_URL pass.
_COLLECTION_FIELDS = (
    "UserProgram_SubPrograms",
    "Markers_Markers",
    "UserComponents_Components",
)

# Component statuses that count as "completed" for KSB coverage.
_KSB_COMPLETED_STATUSES = {"Completed", "QAVerified", "QACompleted"}


def _aggregate_components():
    """Fetch all LearningPlanComponents once and aggregate per LearnerId into
    component names, completed counts and actual hours per evidence bucket."""
    rows = _fetch_paged(COMPONENTS_URL, timeout=180)
    by_learner = {}
    for c in rows:
        lid = c.get("LearnerId")
        agg = by_learner.get(lid)
        if agg is None:
            agg = by_learner[lid] = {
                "components": [],
                "assignment_cnt": 0, "assignment_hrs": 0.0,
                "lms_cnt": 0, "lms_hrs": 0.0,
                "extra_cnt": 0, "extra_hrs": 0.0,
            }
        if c.get("ComponentName"):
            agg["components"].append({
                "name": c.get("ComponentName"),
                "type": c.get("ComponentType"),
                "status": c.get("Status"),
                "hours": c.get("ActualHours"),
            })
        bucket = _COMPONENT_CATEGORY.get(c.get("ComponentType"))
        if bucket and c.get("Status") == "Completed":
            agg[f"{bucket}_cnt"] += 1
            try:
                agg[f"{bucket}_hrs"] += float(c.get("ActualHours") or 0)
            except (ValueError, TypeError):
                pass
    return by_learner


def _fetch_owners(owner_ids):
    """Look up case owners (themselves users) by Id, in batches, to get their
    name / email / phone for the OwnerName, OwnerEmail and OwnerPhone columns."""
    owners = {}
    ids = [i for i in owner_ids if i is not None]
    base = "https://kentbusinesscollege.aptem.co.uk/odata/1.0/users"
    for i in range(0, len(ids), 50):
        batch = ids[i:i + 50]
        id_list = ",".join(str(x) for x in batch)
        url = (f"{base}?$select=Id,FullName,Email,UserPersonalDetails_Mobile"
               f"&$filter=Id in ({id_list})")
        for row in _fetch_paged(url):
            owners[row.get("Id")] = row
    return owners


def _fetch_user_group_names():
    """Match n8n: pick the highest GroupId for each UserId, then map it to Name."""
    user_groups = _fetch_paged(USER_GROUPS_URL, timeout=180)
    groups = _fetch_paged(GROUPS_URL, timeout=180)
    name_by_id = {}
    for row in groups:
        try:
            group_id = int(row.get("Id"))
        except (ValueError, TypeError):
            continue
        name_by_id[group_id] = row.get("Name")

    best_by_user = {}
    for row in user_groups:
        user_id = row.get("UserId")
        try:
            group_id = int(row.get("GroupId"))
        except (ValueError, TypeError):
            continue
        if user_id is None:
            continue

        previous = best_by_user.get(user_id)
        if previous is None or group_id > previous:
            best_by_user[user_id] = group_id

    return {
        user_id: name_by_id.get(group_id)
        for user_id, group_id in best_by_user.items()
    }


def _fetch_all_users():
    users = _fetch_paged(API_URL)

    # Second pass: sub-programmes and markers, keyed on Id.
    expanded = _fetch_paged(EXPAND_URL, timeout=180)
    collections_by_id = {row.get("Id"): row for row in expanded}

    # Third pass: per-component aggregates from LearningPlanComponents.
    components_by_id = _aggregate_components()

    # Fourth pass: case owner details, looked up by OwnerId.
    owner_ids = {u.get("UserPersonalDetails_OwnerId") for u in users}
    owners_by_id = _fetch_owners(owner_ids)

    # Fifth pass: user group names, using the same highest-GroupId rule as n8n.
    group_names_by_user_id = _fetch_user_group_names()

    for user in users:
        uid = user.get("Id")
        extra = collections_by_id.get(uid, {})
        for field in _COLLECTION_FIELDS:
            user[field] = extra.get(field, [])
        user["_components_agg"] = components_by_id.get(uid)
        user["_owner"] = owners_by_id.get(user.get("UserPersonalDetails_OwnerId"))
        user["_group_name"] = group_names_by_user_id.get(uid)

    return users


def _map_user(u):
    def safe_date(val):
        return val[:10] if isinstance(val, str) and val else None

    def safe_numeric(val):
        try:
            return float(val) if val is not None else None
        except (ValueError, TypeError):
            return None

    def safe_int(val):
        try:
            return int(val) if val is not None else None
        except (ValueError, TypeError):
            return None

    def safe_str(val):
        return str(val) if val is not None else None

    # --- Sub-programmes & markers (merged via the EXPAND_URL pass) ---
    subprograms = u.get("UserProgram_SubPrograms") or []
    markers = u.get("Markers_Markers") or []
    subprogram_names = "; ".join(s.get("Name") for s in subprograms if s.get("Name")) or None
    marker_names = "; ".join(m.get("Name") for m in markers if m.get("Name")) or None

    # --- KSB coverage from component CriteriaJson (each is a list of KSB ids) ---
    ksb_target, ksb_completed = set(), set()
    for c in (u.get("UserComponents_Components") or []):
        cj = c.get("CriteriaJson")
        if not cj:
            continue
        try:
            ids = json.loads(cj)
        except (ValueError, TypeError):
            continue
        if not isinstance(ids, list):
            continue
        ksb_target.update(ids)
        if c.get("Status") in _KSB_COMPLETED_STATUSES:
            ksb_completed.update(ids)
    total_target_ksb = len(ksb_target) or None
    total_completed_ksb = len(ksb_completed) if ksb_target else None
    if not ksb_target:
        ksb_status = None
    elif ksb_completed >= ksb_target:
        ksb_status = "Completed"
    elif ksb_completed:
        ksb_status = "In Progress"
    else:
        ksb_status = "Not Started"
        

    # --- Component names, completed counts & actual hours per evidence bucket,
    #     aggregated from the LearningPlanComponents entity set by LearnerId. ---
    agg = u.get("_components_agg") or {}
    component_list = agg.get("components", [])
    component_names = json.dumps(component_list) if component_list else None
    assignment_evd = agg.get("assignment_cnt") or 0
    assignment_hrs = round(agg.get("assignment_hrs", 0.0), 2) or None
    lms_evd = agg.get("lms_cnt") or 0
    lms_hrs = round(agg.get("lms_hrs", 0.0), 2) or None
    extra_evd = agg.get("extra_cnt") or 0
    extra_hrs = round(agg.get("extra_hrs", 0.0), 2) or None

    # --- Case owner details (looked up by OwnerId) ---
    owner = u.get("_owner") or {}
    owner_name = owner.get("FullName") or u.get("UserPersonalDetails_OwnerFullName")
    owner_email = owner.get("Email")
    # OwnerPhone column is NUMERIC, so keep only the digits (drop +, spaces, etc.)
    _owner_mobile = owner.get("UserPersonalDetails_Mobile") or ""
    _owner_digits = "".join(ch for ch in _owner_mobile if ch.isdigit())
    owner_phone = int(_owner_digits) if _owner_digits else None

    # --- Derived progress / timeline columns ---
    start = u.get("UserProgram_StartDate")
    end = u.get("UserProgram_PlannedEndDate")
    start_d = datetime.date.fromisoformat(start[:10]) if start else None
    end_d = datetime.date.fromisoformat(end[:10]) if end else None
    today = datetime.date.today()

    total_days = (end_d - start_d).days if start_d and end_d else None
    elapsed_days = max((today - start_d).days, 0) if start_d else None

    actual_pct = safe_numeric(u.get("UserLearningPlanSummary_LearningPlanProgress"))
    expected_pct = safe_numeric(u.get("UserLearningPlanSummary_ExpectedLearningPlanProgress"))
    target_comp_pct = f"{round(expected_pct)}%" if expected_pct is not None else None
    completed_comp_pct = f"{round(actual_pct)}%" if actual_pct is not None else None
    if actual_pct is not None and expected_pct is not None:
        comp_status = "On Track" if actual_pct >= expected_pct else "Behind"
    else:
        comp_status = None

    completed_min = safe_numeric(u.get("UserLearningPlanSummary_CompletedTime"))
    submitted_min = safe_numeric(u.get("UserLearningPlanSummary_SubmittedTime"))
    forecast_min = safe_numeric(u.get("UserLearningPlanSummary_ForecastTime"))
    expected_min = safe_numeric(u.get("UserLearningPlanSummary_ExpectedOffTheJobHours"))

    # --- OTJ hours progress (mirrors Hours_formating.js logic) ---
    planned_hours = safe_numeric(u.get("UserILRSummary_PlannedHours"))
    _planned_h = planned_hours or 0.0
    _completed_h = (completed_min or 0.0) / 60.0
    _target_ratio = (
        min(elapsed_days, total_days) / total_days
        if total_days and total_days > 0 and elapsed_days is not None
        else None
    )
    if _planned_h > 0 and _target_ratio is not None:
        _completed_pct = (_completed_h / _planned_h) * 100.0
        otj_overall_progress = round(_completed_pct - _target_ratio * 100.0)
    else:
        otj_overall_progress = None

    otj_overall_progress_text = f"{otj_overall_progress}%" if otj_overall_progress is not None else None

    if _target_ratio is not None:
        _target_min = _planned_h * 60.0 * _target_ratio
        _variance_min = round((completed_min or 0.0) - _target_min)
        _sign = "-" if _variance_min < 0 else ""
        _abs = abs(_variance_min)
        otj_overall_variance = f"{_sign}{_abs // 60}h {_abs % 60}m"
    else:
        otj_overall_variance = None

    # OTJHoursStatus based on OTJ-specific fields (ExpectedOffTheJobHours vs SubmittedTime)
    _otj_planned_h = (expected_min or 0.0) / 60.0
    _otj_completed_h = (submitted_min or 0.0) / 60.0
    if _otj_planned_h > 0 and _target_ratio is not None:
        _otj_completed_pct = (_otj_completed_h / _otj_planned_h) * 100.0
        _otj_progress = round(_otj_completed_pct - _target_ratio * 100.0)
    else:
        _otj_progress = None

    if _otj_progress is None:
        otj_status = None
    elif _otj_progress >= -10:
        otj_status = "On Track"
    elif _otj_progress >= -25:
        otj_status = "Need Attention"
    else:
        otj_status = "At Risk"

    return (
        safe_int(u.get("Id")),
        u.get("FullName"),
        u.get("Email"),
        safe_numeric(u.get("UserILRSummary_MinimumRequiredHours")),
        safe_numeric(u.get("UserILRSummary_PlannedHours")),
        int(submitted_min // 60) if submitted_min is not None else None,
        int(completed_min // 60) if completed_min is not None else None,
        int(forecast_min // 60) if forecast_min is not None else None,
        int(expected_min // 60) if expected_min is not None else None,
        otj_overall_progress_text,  # ProgressVariance
        otj_overall_variance,  # Progress-Hours
        otj_status,  # OTJHoursStatus
        total_target_ksb,  # TotalTargetKSB
        total_completed_ksb,  # TotalCompletedKSB
        ksb_status,  # KSBStatus
        safe_date(u.get("UserProgram_StartDate")),
        safe_date(u.get("UserProgram_PlannedEndDate")),
        total_days,  # Total Days
        elapsed_days,  # Elapsed-Days
        u.get("UserProgram_CurrentProgramme"),
        u.get("UserProgram_Status"),
        safe_numeric(u.get("UserILRSummary_TNPSum")),
        safe_int(u.get("UserLearningPlanSummary_ComponentsCount")),           # TotalCompCount
        safe_int(u.get("UserLearningPlanSummary_ComponentsCount")),           # TargetCompCount
        str(u.get("UserLearningPlanSummary_CompletedComponentsCount"))
        if u.get("UserLearningPlanSummary_CompletedComponentsCount") is not None else None,  # CompletedCompCount
        target_comp_pct,  # TargetComp%
        completed_comp_pct,  # CompletedComp%
        comp_status,  # CompStatus
        owner_name,   # OwnerName
        owner_email,  # OwnerEmail
        owner_phone,  # OwnerPhone
        safe_str(u.get("UserReviews_ADET_GRModel_rag")),
        u.get("UserEmployer_Organization"),
        u.get("UserEmployer_ManagerName"),
        u.get("UserEmployer_ManagerEmail"),
        u.get("UserEmployer_MentorName"),                                          # Employer Repsentative
        safe_str(u.get("ComplianceDocuments_ADET_GRModel_apprenticeshipagreement")),   # apprenticeship-agreement
        safe_str(u.get("ComplianceDocuments_ADET_GRModel_trainingplan")),              # trainingplan
        safe_str(u.get("ComplianceDocuments_ADET_GRModel_individuallearningrecord")),  # individual-learning-record
        safe_str(u.get("ComplianceDocuments_ADET_GRModel_contractforservice")),        # contract-for-service
        safe_str(u.get("ComplianceDocuments_ADET_GRModel_writtenagreement")),          # written-agreement
        assignment_evd,  # Assignment Evidence (completed Assignment components)
        assignment_hrs,  # AssignEvdHours (sum of ActualHours)
        lms_evd,  # LMS Evidence (completed OnlineLearning components)
        lms_hrs,  # LMSEvdHours (sum of ActualHours)
        extra_evd,  # ExtraAct-Evidence (completed Miscellaneous components)
        extra_hrs,  # ExtrEvdHours (sum of ActualHours)
        u.get("_group_name") or u.get("UserGroups_GroupLevel0"),              # Group
        u.get("UserILRSummary_PrimaryHealthProblem"),
        safe_int(u.get("UserPersonalDetails_OwnerId")),
        u.get("UserPersonalDetails_Gender"),
        subprogram_names,  # subprogramme
        str(u.get("UserEmployer_ManagerPhone")) if u.get("UserEmployer_ManagerPhone") else None,
        u.get("UserPersonalDetails_Mobile"),
        u.get("UserPersonalDetails_Address"),
        u.get("UserPersonalDetails_PostCode"),
        marker_names,  # Markers_Markers
        component_names,  # components
        u.get("UserEmployer_ManagerEmail"),
        str(u.get("UserILRSummary_EmploymentWeeklyHours"))
        if u.get("UserILRSummary_EmploymentWeeklyHours") is not None else None,  # Working hours
        u.get("SubscriptionStatus"),
        "Levy" if u.get("UserEmployer_LevyPayer") is True
        else ("Non-Levy" if u.get("UserEmployer_LevyPayer") is False else None),  # Levy or Not
    )


INSERT_SQL = """
INSERT INTO "LMS"."Aptem_users" (
    "ID", "FullName", "Email", "Minimum", "Planned", "Submitted", "Completed",
    "Forecast", "Exepected", "ProgressVariance", "Progress-Hours", "OTJHoursStatus",
    "TotalTargetKSB", "TotalCompletedKSB", "KSBStatus", "Start-Date", "End-Date",
    "Total Days", "Elapsed-Days", "Program Name", "Program-Status", "Price",
    "TotalCompCount", "TargetCompCount", "CompletedCompCount", "TargetComp%%",
    "CompletedComp%%", "CompStatus", "OwnerName", "OwnerEmail", "OwnerPhone",
    "Coach-RAG", "OrganizationName", "ManagerName", "ManagerEmail",
    "Employer Repsentative", "apprenticeship-agreement", "trainingplan",
    "individual-learning-record", "contract-for-service", "written-agreement",
    "Assignment Evidence", "AssignEvdHours", "LMS Evidence", "LMSEvdHours",
    "ExtraAct-Evidence", "ExtrEvdHours", "Group", "Disability", "case_owner_id",
    "Gender", "subprogramme", "Manager Phone", "Learner Phone", "Address",
    "post code", "Markers_Markers", "components", "Employer Email",
    "Working hours", "Subscription Status", "Levy or Not"
)
VALUES %s
ON CONFLICT ("ID") DO UPDATE SET
    "FullName" = EXCLUDED."FullName",
    "Email" = EXCLUDED."Email",
    "Minimum" = EXCLUDED."Minimum",
    "Planned" = EXCLUDED."Planned",
    "Submitted" = EXCLUDED."Submitted",
    "Completed" = EXCLUDED."Completed",
    "Forecast" = EXCLUDED."Forecast",
    "Exepected" = EXCLUDED."Exepected",
    "ProgressVariance" = EXCLUDED."ProgressVariance",
    "Progress-Hours" = EXCLUDED."Progress-Hours",
    "OTJHoursStatus" = EXCLUDED."OTJHoursStatus",
    "TotalTargetKSB" = EXCLUDED."TotalTargetKSB",
    "TotalCompletedKSB" = EXCLUDED."TotalCompletedKSB",
    "KSBStatus" = EXCLUDED."KSBStatus",
    "Start-Date" = EXCLUDED."Start-Date",
    "End-Date" = EXCLUDED."End-Date",
    "Total Days" = EXCLUDED."Total Days",
    "Elapsed-Days" = EXCLUDED."Elapsed-Days",
    "Program Name" = EXCLUDED."Program Name",
    "Program-Status" = EXCLUDED."Program-Status",
    "Price" = EXCLUDED."Price",
    "TotalCompCount" = EXCLUDED."TotalCompCount",
    "TargetCompCount" = EXCLUDED."TargetCompCount",
    "CompletedCompCount" = EXCLUDED."CompletedCompCount",
    "TargetComp%%" = EXCLUDED."TargetComp%%",
    "CompletedComp%%" = EXCLUDED."CompletedComp%%",
    "CompStatus" = EXCLUDED."CompStatus",
    "OwnerName" = EXCLUDED."OwnerName",
    "OwnerEmail" = EXCLUDED."OwnerEmail",
    "OwnerPhone" = EXCLUDED."OwnerPhone",
    "Coach-RAG" = EXCLUDED."Coach-RAG",
    "OrganizationName" = EXCLUDED."OrganizationName",
    "ManagerName" = EXCLUDED."ManagerName",
    "ManagerEmail" = EXCLUDED."ManagerEmail",
    "Employer Repsentative" = EXCLUDED."Employer Repsentative",
    "apprenticeship-agreement" = EXCLUDED."apprenticeship-agreement",
    "trainingplan" = EXCLUDED."trainingplan",
    "individual-learning-record" = EXCLUDED."individual-learning-record",
    "contract-for-service" = EXCLUDED."contract-for-service",
    "written-agreement" = EXCLUDED."written-agreement",
    "Assignment Evidence" = EXCLUDED."Assignment Evidence",
    "AssignEvdHours" = EXCLUDED."AssignEvdHours",
    "LMS Evidence" = EXCLUDED."LMS Evidence",
    "LMSEvdHours" = EXCLUDED."LMSEvdHours",
    "ExtraAct-Evidence" = EXCLUDED."ExtraAct-Evidence",
    "ExtrEvdHours" = EXCLUDED."ExtrEvdHours",
    "Group" = EXCLUDED."Group",
    "Disability" = EXCLUDED."Disability",
    "case_owner_id" = EXCLUDED."case_owner_id",
    "Gender" = EXCLUDED."Gender",
    "subprogramme" = EXCLUDED."subprogramme",
    "Manager Phone" = EXCLUDED."Manager Phone",
    "Learner Phone" = EXCLUDED."Learner Phone",
    "Address" = EXCLUDED."Address",
    "post code" = EXCLUDED."post code",
    "Markers_Markers" = EXCLUDED."Markers_Markers",
    "components" = EXCLUDED."components",
    "Employer Email" = EXCLUDED."Employer Email",
    "Working hours" = EXCLUDED."Working hours",
    "Subscription Status" = EXCLUDED."Subscription Status",
    "Levy or Not" = EXCLUDED."Levy or Not"
"""


def run_sync():
    """Fetch all users from Aptem, upsert them, and delete any rows in the
    table whose Id was NOT returned by the API this run. Returns the counts.

    Raised exceptions propagate to the caller. As a safety guard, if the API
    returns no users at all (e.g. a transient failure) the table is left
    untouched rather than wiping every row.
    """
    users = _fetch_all_users()
    rows = [_map_user(u) for u in users]
    current_ids = [r[0] for r in rows]  # r[0] is the Id (first tuple element)

    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            if rows:
                execute_values(cur, INSERT_SQL, rows)
            # Remove learners no longer returned by the endpoint. Skip when the
            # fetch came back empty so an API hiccup never empties the table.
            deleted = 0
            deleted_emails = []
            if current_ids:
                cur.execute(
                    'SELECT "Email" FROM "LMS"."Aptem_users" WHERE "ID" <> ALL(%s)',
                    (current_ids,),
                )
                deleted_emails = [row[0] for row in cur.fetchall() if row[0]]
                cur.execute(
                    'DELETE FROM "LMS"."Aptem_users" WHERE "ID" <> ALL(%s)',
                    (current_ids,),
                )
                deleted = cur.rowcount
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {"upserted": len(rows), "deleted": deleted, "deleted_emails": deleted_emails}


def sync_aptem_users(_request):
    try:
        result = run_sync()
        return JsonResponse({"status": "ok", **result})
    except requests.HTTPError as e:
        return JsonResponse({"status": "error", "detail": f"Aptem API error: {e}"}, status=502)
    except Exception as e:
        return JsonResponse({"status": "error", "detail": str(e)}, status=500)
