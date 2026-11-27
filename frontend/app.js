const reportInput = document.querySelector("#report-file");
const resultContainer = document.querySelector("#report-content");
const emptyState = document.querySelector("#report-empty");
const errorBox = document.querySelector("#report-error");
let reportCount = 0;

async function loadPolicy() {
  try {
    const response = await fetch("./policy.json");
    if (!response.ok) throw new Error("Policy file could not be loaded.");
    const policy = await response.json();
    renderPolicy(policy);
    return policy;
  } catch (error) {
    document.querySelector("#allowed-list").textContent = "Policy unavailable";
    document.querySelector("#denied-list").textContent = "Policy unavailable";
    document.querySelector("#policy-foot").textContent = "Could not load policy.json. Serve this folder over HTTP and keep the policy file alongside the frontend.";
    throw error;
  }
}

function renderPolicy(policy) {
  renderLicenseList("allowed-list", policy.allowed_licenses || []);
  renderLicenseList("denied-list", policy.denied_licenses || []);
  document.querySelector("#allowed-count").textContent = (policy.allowed_licenses || []).length;
  document.querySelector("#denied-count").textContent = (policy.denied_licenses || []).length;
  document.querySelector("#policy-foot").textContent = `Unlicensed files are ${policy.allow_unlicensed ? "allowed" : "flagged for review"} by this policy.`;
}

function renderLicenseList(id, values) {
  const list = document.getElementById(id);
  list.replaceChildren();
  values.forEach((value) => {
    const chip = document.createElement("span");
    chip.className = "license-chip";
    chip.textContent = value;
    list.append(chip);
  });
}

function parseCsv(text) {
  const rows = [];
  let row = [];
  let cell = "";
  let quoted = false;
  for (let i = 0; i < text.length; i += 1) {
    const char = text[i];
    if (char === '"' && quoted && text[i + 1] === '"') { cell += '"'; i += 1; }
    else if (char === '"') quoted = !quoted;
    else if (char === "," && !quoted) { row.push(cell); cell = ""; }
    else if ((char === "\n" || char === "\r") && !quoted) {
      if (char === "\r" && text[i + 1] === "\n") i += 1;
      row.push(cell); if (row.some((value) => value.trim())) rows.push(row); row = []; cell = "";
    } else cell += char;
  }
  if (cell || row.length) { row.push(cell); if (row.some((value) => value.trim())) rows.push(row); }
  if (quoted) throw new Error("CSV contains an unclosed quoted field.");
  if (!rows.length) throw new Error("CSV file is empty.");
  const headers = rows.shift().map((name) => name.trim().toLowerCase().replace(/[ _]/g, ""));
  const findColumn = (names) => headers.findIndex((name) => names.includes(name));
  const pathCol = findColumn(["filepath", "filename", "file"]);
  const scannerCol = findColumn(["scanner", "licenseinfoinfile", "license", "licenses", "scannerfindings"]);
  const conclusionCol = findColumn(["conclusion", "licenseconcluded", "concluded", "conclusions"]);
  if (pathCol < 0) throw new Error("CSV needs a filePath or FileName column.");
  const split = (value) => value.trim() ? value.split(/[;,]/).map((item) => item.trim()).filter(Boolean) : [];
  const items = rows.map((values) => {
    const filePath = (values[pathCol] || "").trim();
    if (!filePath) return null;
    const conclusions = conclusionCol >= 0 ? split(values[conclusionCol] || "") : [];
    return { filePath, findings: { scanner: scannerCol >= 0 ? split(values[scannerCol] || "") : [], conclusion: conclusions.length ? conclusions : null } };
  }).filter(Boolean);
  if (!items.length) throw new Error("No valid scan entries found in CSV.");
  return items;
}

function parseReport(raw) {
  if (Array.isArray(raw)) return raw;
  if (raw && typeof raw === "object") {
    for (const key of ["licenses", "findings", "files"]) if (Array.isArray(raw[key])) return raw[key];
    if (raw.filePath) return [raw];
  }
  throw new Error("JSON report must be a list of findings or contain a licenses, findings, or files list.");
}

function evaluate(item, policy) {
  if (!item || typeof item !== "object" || Array.isArray(item)) throw new Error("Every report finding must be an object.");
  const findings = item.findings && typeof item.findings === "object" ? item.findings : {};
  const conclusion = findings.conclusion;
  const rawLicenses = Array.isArray(conclusion) && conclusion.length ? conclusion : Array.isArray(findings.scanner) ? findings.scanner : [];
  const licenses = rawLicenses.filter((license) => license != null && String(license).trim()).map(String);
  const cleanLicenses = licenses.filter((license) => license !== "No_license_found");
  const allowed = new Set(policy.allowed_licenses || []);
  const denied = new Set(policy.denied_licenses || []);
  let status; let reason; let shownLicenses = cleanLicenses;
  if (!cleanLicenses.length) {
    status = policy.allow_unlicensed ? "PASS" : "NEEDS_REVIEW";
    shownLicenses = licenses.length ? licenses : ["No_license_found"];
    reason = policy.allow_unlicensed ? "No license detected (permitted by policy)" : "Missing license statement; requires compliance review";
  } else {
    const prohibited = cleanLicenses.filter((license) => denied.has(license));
    const unapproved = cleanLicenses.filter((license) => !allowed.has(license));
    if (prohibited.length) { status = "DENIED"; reason = `Prohibited license(s) detected: ${prohibited.join(", ")}`; }
    else if (unapproved.length) { status = "NEEDS_REVIEW"; reason = `Unapproved license(s) require review: ${unapproved.join(", ")}`; }
    else { status = "PASS"; reason = `Compliant: ${cleanLicenses.join(", ")}`; }
  }
  return { filePath: String(item.filePath || "Unknown File"), licenses: shownLicenses, status, reason };
}

function showReport(fileName, rows) {
  const counts = { PASS: 0, DENIED: 0, NEEDS_REVIEW: 0 };
  rows.forEach((row) => { counts[row.status] += 1; });
  const overall = counts.DENIED + counts.NEEDS_REVIEW ? "FAIL" : "PASS";
  document.querySelector("#result-summary").replaceChildren();
  const resultLabel = document.createElement("div");
  resultLabel.className = `imported-status ${overall === "PASS" ? "passed" : "failed"}`;
  const isSample = /^sample_report_/i.test(fileName);
  resultLabel.innerHTML = `<span>${overall === "PASS" ? "✓" : "!"}</span><div><strong>${isSample ? "Sample report" : "Imported report"} · ${overall}</strong><small>${isSample ? "Example fixture · not a live compliance result" : "Local evaluation · manually selected"} · ${escapeHtml(fileName)}</small></div>`;
  document.querySelector("#result-summary").append(resultLabel);
  const metrics = document.createElement("div");
  metrics.className = "result-metrics";
  metrics.innerHTML = `<span><strong>${rows.length}</strong> files</span><span><strong>${counts.PASS}</strong> passed</span><span><strong>${counts.DENIED}</strong> denied</span><span><strong>${counts.NEEDS_REVIEW}</strong> review</span>`;
  document.querySelector("#result-summary").append(metrics);
  const body = document.querySelector("#findings-body");
  body.replaceChildren();
  rows.forEach((row) => {
    const tr = document.createElement("tr");
    const path = document.createElement("td"); path.textContent = row.filePath; path.className = "file-path";
    const licenses = document.createElement("td"); licenses.textContent = row.licenses.join(", ") || "—";
    const status = document.createElement("td"); status.innerHTML = `<span class="status-pill ${row.status === "PASS" ? "status-pass" : row.status === "DENIED" ? "status-denied" : "status-review"}"><i></i>${row.status.replace("_", " ")}</span>`;
    const detail = document.createElement("td"); detail.textContent = row.reason;
    tr.append(path, licenses, status, detail); body.append(tr);
  });
  emptyState.hidden = true; resultContainer.hidden = false; errorBox.hidden = true;
  reportCount += 1;
  document.querySelector("#report-count").textContent = reportCount;
  document.querySelector("#latest-result").textContent = overall;
  document.querySelector("#latest-result").classList.toggle("muted-value", false);
  document.querySelector("#latest-time").textContent = `${isSample ? "Sample fixture" : "Imported just now"} · ${fileName}`;
}

function escapeHtml(value) { return value.replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]); }

reportInput.addEventListener("change", async () => {
  const file = reportInput.files[0];
  if (!file) return;
  try {
    const policy = await loadPolicy();
    const text = await file.text();
    const raw = file.name.toLowerCase().endsWith(".csv") ? parseCsv(text) : JSON.parse(text);
    const rows = parseReport(raw).map((item) => evaluate(item, policy));
    showReport(file.name, rows);
  } catch (error) {
    errorBox.textContent = error.message || "Could not read this report.";
    errorBox.hidden = false;
  }
  reportInput.value = "";
});

loadPolicy().catch(() => {});
