PR Table :
1-request url:
 https://kentbusinesscollege.aptem.co.uk/odata/1.0/Reviews?$filter=LearnerId eq {{ $json.ID }} and ( Type eq 'Personal Support Plan' or Type eq 'Progress Review' or Type eq 'Progress Review ' or Type eq 'Progress Review (%2B Skills Radar)' or Type eq 'Progress Review (%2B Skills Radar) ') and ProgramName eq '{{ $json["Program Name"] }}'
 2-normalizing code in JS that you will convert to Python:
 // n8n Code node (JavaScript)
// Mode: Run Once for All Items
// Output: 1 item per learner:
// { ID, value:[...], LastPR, LastPRText, NextPR, NextPRText, LastActuallyCompletedPR, LastActuallyCompletedPRText }

function formatDDMMYYYY(iso) {
  if (!iso || typeof iso !== "string") return null;
  const d = iso.slice(0, 10);
  const [yyyy, mm, dd] = d.split("-");
  if (!yyyy || !mm || !dd) return null;
  return `${dd}-${mm}-${yyyy}`;
}

function prettyStatus(status) {
  if (!status || typeof status !== "string") return null;
  return status.replace(/([a-z])([A-Z])/g, "$1 $2").trim();
}

function buildComponentName(c) {
  return (c.Type || c.Name || "").trim();
}

function normalizeText(v) {
  return String(v || "").toLowerCase().replace(/\s+/g, "");
}

function isCompletedReviewStatus(statusRaw) {
  const s = normalizeText(statusRaw);
  return s === "completed";
}

function isPendingReviewStatus(statusRaw) {
  return !isCompletedReviewStatus(statusRaw);
}

function isProgressReview(c) {
  const t = (c.Type || c.Name || "").toLowerCase();
  return !t.includes("personal support plan");
}

function toMs(iso) {
  const ms = Date.parse(iso);
  return Number.isFinite(ms) ? ms : 0;
}

function normalizeStatus(c) {
  return c?.Status ?? null;
}

function toODataISO(iso) {
  if (!iso || typeof iso !== "string") return iso;
  return iso.replace(/\+/g, "%2B");
}

function buildCompletedDate(c, effectiveStatusRaw, statusLabel) {
  const completedSource = c.CompletedDate ?? c.UpdatedDate ?? null;
  const completedDate = formatDDMMYYYY(completedSource);
  const plannedDate = formatDDMMYYYY(c.PlannedDate);
  const statusNorm = normalizeText(effectiveStatusRaw);

  if (completedDate && statusLabel) {
    return `${completedDate} (${statusLabel})`;
  }

  if (completedDate) {
    return completedDate;
  }

  if (statusNorm === "scheduled" && plannedDate) {
    return `${plannedDate} (${statusLabel || "Scheduled"})`;
  }

  if (statusLabel) {
    return statusLabel;
  }

  return "Not Started";
}

function buildLastPRCompletedDate(c, effectiveStatusRaw, statusLabel) {
  const completedSource = c.CompletedDate ?? c.UpdatedDate ?? null;
  const completedDate = formatDDMMYYYY(completedSource);
  const plannedDate = formatDDMMYYYY(c.PlannedDate);
  const statusNorm = normalizeText(effectiveStatusRaw);

  if (completedDate && statusLabel) {
    return `${completedDate} (${statusLabel})`;
  }

  if (completedDate) {
    return completedDate;
  }

  if (
    (statusNorm === "scheduled" ||
      statusNorm === "notscheduled" ||
      statusNorm === "inprogress" ||
      statusNorm === "awaitingsignature") &&
    plannedDate
  ) {
    return `${plannedDate} (${statusLabel || c.Status || "Status"})`;
  }

  if (statusLabel) {
    return statusLabel;
  }

  return "Not Started";
}

const inputItems = $input.all();
const out = [];
const now = Date.now();

for (const it of inputItems) {
  const root = it.json;

  let components = [];
  if (root && Array.isArray(root.value)) {
    components = root.value;
  } else if (Array.isArray(root)) {
    components = root;
  }

  const learnerId = components[0]?.LearnerId ?? root?.LearnerId ?? null;

  components.sort((a, b) =>
    String(a?.PlannedDate || "").localeCompare(String(b?.PlannedDate || ""))
  );

  const rows = [];
  let lastPR = null;
  let bestPastDue = -1;
  let nextPR = null;

  let lastActuallyCompletedPR = null;
  let bestCompletedMs = -1;

  for (const c of components) {
    const plannedDate = formatDDMMYYYY(c.PlannedDate);
    const effectiveStatusRaw = normalizeStatus(c);
    const statusLabel = prettyStatus(effectiveStatusRaw);

    const completedDate = buildCompletedDate(c, effectiveStatusRaw, statusLabel);

    rows.push({
      component_name: buildComponentName(c),
      planned_date: plannedDate,
      completed_date: completedDate,
      status: statusLabel,
    });

    const completedMs = toMs(c.CompletedDate);

    if (isProgressReview(c) && completedMs > 0 && completedMs > bestCompletedMs) {
      bestCompletedMs = completedMs;

      lastActuallyCompletedPR = {
        component_name: buildComponentName(c),
        planned_date: formatDDMMYYYY(c.PlannedDate),
        completed_date: formatDDMMYYYY(c.CompletedDate),
        status: statusLabel,
      };
    }

    if (isProgressReview(c)) {
      const dueMs = toMs(c.PlannedDate);

      if (dueMs > 0 && dueMs < now && dueMs > bestPastDue) {
        bestPastDue = dueMs;

        lastPR = {
          component_name: buildComponentName(c),
          planned_date: formatDDMMYYYY(c.PlannedDate),
          completed_date: buildLastPRCompletedDate(c, effectiveStatusRaw, statusLabel),
          status: statusLabel,
        };
      }
    }

    if (isProgressReview(c) && isPendingReviewStatus(effectiveStatusRaw)) {
      const dueMs = toMs(c.PlannedDate);

      if (dueMs > now && (nextPR === null || dueMs < toMs(nextPR.PlannedDate))) {
        nextPR = c;
      }
    }
  }

  const LastPR = lastPR || "Not Yet";
  const LastPRText = lastPR?.completed_date || "Not Yet";

  const NextPR = nextPR
    ? {
        component_name: buildComponentName(nextPR),
        planned_date: formatDDMMYYYY(nextPR.PlannedDate),
        status: prettyStatus(normalizeStatus(nextPR)),
      }
    : "Not Found";

  const NextPRText = nextPR ? toODataISO(nextPR.PlannedDate) : "Not Found";

  const LastActuallyCompletedPR = lastActuallyCompletedPR || "Not Yet";
  const LastActuallyCompletedPRText = lastActuallyCompletedPR
    ? `${lastActuallyCompletedPR.completed_date} (${lastActuallyCompletedPR.status || "Status"})`
    : "Not Yet";

  out.push({
    json: {
      ID: learnerId,
      value: rows,
      LastPR,
      LastPRText,
      NextPR,
      NextPRText,
      LastActuallyCompletedPR,
      LastActuallyCompletedPRText,
    },
  });
}

return out;


3-table Name PR and its columns:
FullName
TEXT
Email
TEXT
Group
TEXT
CaseOwner
TEXT
Last Progress Review
TEXT
Review Planned Date1
TEXT
Review Status1
TEXT
Review Planned Date2
TEXT
Review Status2
TEXT
Review Planned Date3
TEXT
Review Status3
TEXT
Review Planned Date4
TEXT
Review Status4
TEXT
Review Planned Date5
TEXT
Review Status5
TEXT
Review Planned Date6
TEXT
Review Status6
TEXT
Review Planned Date7
TEXT
Review Status7
TEXT
Review Planned Date8
TEXT
Review Status8
TEXT
Review Planned Date9
TEXT
Review Status9
TEXT
Review Planned Date10
TEXT
Review Status10
TEXT
Review Planned Date11
TEXT
Review Status11
TEXT
Review Planned Date12
TEXT
Review Status12
TEXT
Review Planned Date13
TEXT
Review Status13
TEXT
Review Planned Date14
TEXT
Review Status14
TEXT
Review Planned Date15
TEXT
Review Status15
TEXT
Review Planned Date16
TEXT
Review Status16
TEXT
Status
TEXT
ID
INTEGER
PRIMARY KEY
case_owner_id
INTEGER
Next Review (Status)
TEXT
Last Actually Completed PR
TEXT
programme
TEXT
Manager Name
TEXT
Manager Email
TEXT