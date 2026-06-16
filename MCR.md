MCR table creation :
1- requesting url:
https://kentbusinesscollege.aptem.co.uk/odata/1.0/Reviews?$filter=LearnerId eq {{ $json.ID }} and Type eq 'Monthly Coaching Meeting'
 2-normalizing code in JS that you will convert to Python:
// n8n Code node (JavaScript)
// Mode: Run Once for All Items
// Output: 1 item per learner:
// { ID, value:[...], LastCoaching, LastCoachingText, NextCoaching, NextCoachingText, LastActuallyCompletedCoaching, LastActuallyCompletedCoachingText }

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

function isCompletedStatus(c) {
  if (c.Completed === true) return true;
  const s = normalizeText(c.Status);
  return s === "completed";
}

function isPendingStatus(c) {
  return !isCompletedStatus(c);
}

function isMonthlyCoaching(c) {
  const t = (c.Type || c.Name || "").toLowerCase();
  return t.includes("monthly coaching");
}

function toMs(iso) {
  const ms = Date.parse(iso);
  return Number.isFinite(ms) ? ms : 0;
}

function normalizeStatus(c) {
  if (c.Completed === true) return "Completed";
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

function buildLastCoachingCompletedDate(c, effectiveStatusRaw, statusLabel) {
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

  const coachingComponents = components.filter(isMonthlyCoaching);

  coachingComponents.sort((a, b) =>
    String(a?.PlannedDate || "").localeCompare(String(b?.PlannedDate || ""))
  );

  const rows = [];
  let lastCoaching = null;
  let bestPastDue = -1;
  let nextCoaching = null;

  let lastActuallyCompletedCoaching = null;
  let bestCompletedMs = -1;

  for (const c of coachingComponents) {
    const plannedDate = formatDDMMYYYY(c.PlannedDate);
    const effectiveStatusRaw = normalizeStatus(c);
    const statusLabel = prettyStatus(effectiveStatusRaw);

    const completedDate = buildCompletedDate(c, effectiveStatusRaw, statusLabel);

    rows.push({
      component_name: c.Name || buildComponentName(c),
      planned_date: plannedDate,
      completed_date: completedDate,
      status: statusLabel,
      ownerName: c.OwnerName || null,
      programName: c.ProgramName || null,
    });

    const completedMs = toMs(c.CompletedDate);

    if (completedMs > 0 && completedMs > bestCompletedMs) {
      bestCompletedMs = completedMs;
      lastActuallyCompletedCoaching = {
        component_name: c.Name || buildComponentName(c),
        planned_date: formatDDMMYYYY(c.PlannedDate),
        completed_date: formatDDMMYYYY(c.CompletedDate),
        status: statusLabel,
        ownerName: c.OwnerName || null,
      };
    }

    const dueMs = toMs(c.PlannedDate);

    if (dueMs > 0 && dueMs < now && dueMs > bestPastDue) {
      bestPastDue = dueMs;
      lastCoaching = {
        component_name: c.Name || buildComponentName(c),
        planned_date: formatDDMMYYYY(c.PlannedDate),
        completed_date: buildLastCoachingCompletedDate(c, effectiveStatusRaw, statusLabel),
        status: statusLabel,
        ownerName: c.OwnerName || null,
      };
    }

    if (isPendingStatus(c)) {
      if (dueMs > now && (nextCoaching === null || dueMs < toMs(nextCoaching.PlannedDate))) {
        nextCoaching = c;
      }
    }
  }

  const LastCoaching = lastCoaching || "Not Yet";
  const LastCoachingText = lastCoaching?.completed_date || "Not Yet";

  const NextCoaching = nextCoaching
    ? {
        component_name: nextCoaching.Name || buildComponentName(nextCoaching),
        planned_date: formatDDMMYYYY(nextCoaching.PlannedDate),
        status: prettyStatus(normalizeStatus(nextCoaching)),
        ownerName: nextCoaching.OwnerName || null,
      }
    : "Not Found";

  const NextCoachingText = nextCoaching
    ? toODataISO(nextCoaching.PlannedDate)
    : "Not Found";

  const LastActuallyCompletedCoaching = lastActuallyCompletedCoaching || "Not Yet";
  const LastActuallyCompletedCoachingText = lastActuallyCompletedCoaching
    ? `${lastActuallyCompletedCoaching.completed_date} (${lastActuallyCompletedCoaching.status || "Status"})`
    : "Not Yet";

  out.push({
    json: {
      ID: learnerId,
      value: rows,
      LastCoaching,
      LastCoachingText,
      NextCoaching,
      NextCoachingText,
      LastActuallyCompletedCoaching,
      LastActuallyCompletedCoachingText,
    },
  });
}

return out;

3- table Named MCR and it columns :ID
BIGINT
PRIMARY KEY
FullName
TEXT
Email
TEXT
Status
TEXT
Subscription Status
TEXT
CaseOwner
TEXT
Last MCM
TEXT
Next MCM
TEXT
MCM1
TEXT
Status1
TEXT
MCM2
TEXT
Status2
TEXT
MCM3
TEXT
Status3
TEXT
MCM4
TEXT
Status4
TEXT
MCM5
TEXT
Status5
TEXT
MCM6
TEXT
Status6
TEXT
MCM7
TEXT
Status7
TEXT
MCM8
TEXT
Status8
TEXT
MCM9
TEXT
Status9
TEXT
MCM10
TEXT
Status10
TEXT
MCM11
TEXT
Status11
TEXT
MCM12
TEXT
Status12
TEXT
MCM13
TEXT
Status13
TEXT
MCM14
TEXT
Status14
TEXT
MCM15
TEXT
Status15
TEXT
MCM16
TEXT
Status16
TEXT
MCM17
TEXT
Status17
TEXT
MCM18
TEXT
Status18
TEXT
MCM19
TEXT
Status19
TEXT
MCM20
TEXT
Status20
TEXT
MCM21
TEXT
Status21
TEXT
MCM22
TEXT
Status22
TEXT
Last Actually Completed MCM
TEXT
Manager Name
TEXT
Manager Email
TEXT