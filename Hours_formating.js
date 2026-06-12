// n8n Code node (JavaScript)

const num = v => Number(v ?? 0);
const MS_DAY = 24 * 60 * 60 * 1000;
const toDate = v => (v ? new Date(v) : null);
const diffDays = (a, b) => (a && b) ? Math.floor((b - a) / MS_DAY) : null;

function coalesce(...vals) { for (const v of vals) if (v !== undefined && v !== null && String(v).trim() !== '') return v; }
function findByRegex(obj, regex) { for (const k of Object.keys(obj)) if (regex.test(k)) return obj[k]; }

// readers
function readStartDate(r) {
  return coalesce(r.UserProgram_StartDate, r.ProgrammeStartDate, r.ILRStartDate, findByRegex(r, /start.*date/i));
}
function readEndDate(r) {
  return coalesce(r.UserProgram_PlannedEndDate, r.PlannedEndDate, r.ILRPlannedEndDate, r.UserProgram_EndDate, findByRegex(r, /end.*date/i));
}
function readPlannedHours(r) {
  const hours = coalesce(r.UserILRSummary_PlannedHours, r.UserILRSummary_PlannedOffTheJobHours, r.PlannedHours, findByRegex(r, /planned.*hours/i));
  if (hours != null) return num(hours);
  const mins = coalesce(r.UserILRSummary_PlannedTime, r.PlannedTime, findByRegex(r, /planned.*time/i));
  return mins != null ? num(mins) / 60 : 0;
}

const fmtHM = mins => {
  const sign = mins < 0 ? "-" : "";
  const m = Math.round(Math.abs(mins));
  const h = Math.floor(m / 60);
  const mm = m % 60;
  return `${sign}${h}h ${mm}m`;
};

const convert = r => {
  const out = {
    ...r,
    UserLearningPlanSummary_ForecastTime: Math.floor(num(r.UserLearningPlanSummary_ForecastTime) / 60),
    UserLearningPlanSummary_CompletedTime: Math.floor(num(r.UserLearningPlanSummary_CompletedTime) / 60),
    UserLearningPlanSummary_ExpectedOffTheJobHours: Math.floor(num(r.UserLearningPlanSummary_ExpectedOffTheJobHours) / 60),
    UserLearningPlanSummary_SubmittedTime: Math.floor(num(r.UserLearningPlanSummary_SubmittedTime) / 60),
  };

  // days
  const start = toDate(readStartDate(r));
  const end = toDate(readEndDate(r));
  const total = diffDays(start, end);
  out.TotalDays = total;
  out.ElapsedDays = total != null ? Math.min(Math.max(diffDays(start, new Date()) ?? 0, 0), total) : null;

  // progress %
  const plannedH = readPlannedHours(r);
  const completedH = num(r.UserLearningPlanSummary_CompletedTime) / 60;
  const targetRatio = (out.TotalDays > 0 && out.ElapsedDays != null) ? out.ElapsedDays / out.TotalDays : null;

  const completedPct = plannedH > 0 ? (completedH / plannedH) * 100 : null;
  const overallPct = (completedPct != null && targetRatio != null) ? Math.round(completedPct - targetRatio * 100) : null;

  out.OverallProgress = overallPct;
  out.OverallProgressText = overallPct != null ? `${overallPct}%` : null;

  // variance minutes
  const plannedMin = plannedH * 60;
  const completedMin = num(r.UserLearningPlanSummary_CompletedTime);
  const targetMin = (targetRatio != null) ? plannedMin * targetRatio : null;
  const varianceMin = (targetMin != null) ? Math.round(completedMin - targetMin) : null;

  out.OverallVarianceMinutes = varianceMin;
  out.OverallVarianceText = (varianceMin != null) ? fmtHM(varianceMin) : null;

  // ProgressStatus بناءً على OverallProgress %
  if (overallPct == null) {
    out.ProgressStatus = null;
  } else if (overallPct >= -10) {
    out.ProgressStatus = 'On Track';
  } else if (overallPct >= -25) {
    out.ProgressStatus = 'Need Attention';
  } else {
    out.ProgressStatus = 'At Risk';
  }

  return out;
};

// لا فلترة: نرجّع كل السجلات بعد التحويل
function run(items) {
  return items.map(r => ({ json: convert(r) }));
}

// يدعم الحالتين: مصفوفة داخل item واحد أو عدة items
if ($input.all().length === 1 && Array.isArray($input.first().json.value)) {
  return run($input.first().json.value);
}
return run($input.all().map(i => i.json));