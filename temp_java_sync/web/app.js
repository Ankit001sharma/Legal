const $ = (id) => document.getElementById(id);

function setStatus(text, cls = "") {
  const el = $("status");
  el.textContent = text;
  el.className = "status " + cls;
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...options.headers },
    ...options,
  });
  const text = await res.text();
  let data;
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    data = { raw: text };
  }
  if (!res.ok) {
    const detail = data.detail || data.error || text || res.statusText;
    throw new Error(typeof detail === "object" ? JSON.stringify(detail, null, 2) : detail);
  }
  return data;
}

async function saveConfig() {
  await api("/api/config", {
    method: "POST",
    body: JSON.stringify({
      document_server_url: $("docUrl").value.trim(),
      platform_url: $("platformUrl").value.trim(),
      tenant_id: $("tenantId").value.trim(),
    }),
  });
  setStatus("Config saved", "ok");
}

async function checkHealth() {
  setStatus("Checking health…", "running");
  $("healthOut").textContent = "";
  try {
    const data = await api("/api/health");
    $("healthOut").textContent = JSON.stringify(data, null, 2);
    const docOk = data.document_mcp?.db === "ok";
    const caps = data.mcp_capabilities || [];
    const multiPid = (data.port_listener_count || 0) > 1;
    const missingCap = docOk && !caps.includes("search_request_metadata");
    if (multiPid) {
      setStatus("WARNING: multiple processes on document-mcp port", "err");
    } else if (missingCap) {
      setStatus("WARNING: MCP missing search_request_metadata (stale?)", "err");
    } else {
      setStatus(docOk ? "document-mcp OK" : "document-mcp not ready (need pgvector)", docOk ? "ok" : "err");
    }
  } catch (e) {
    $("healthOut").textContent = e.message;
    setStatus("Health check failed", "err");
  }
}

function showContractId(id) {
  const el = $("contractId");
  el.textContent = "contract_document_id: " + id;
  el.classList.remove("hidden");
}

function renderFindings(findings) {
  if (!findings?.length) {
    $("findingsTable").innerHTML = "<p>No findings.</p>";
    return;
  }
  const primary = primaryFindings(findings);
  const rows = primary
    .map(
      (f) => `<tr>
      <td>${esc(f.contract_section_id || "—")}</td>
      <td><span class="badge ${esc(f.status)}">${esc(f.status)}</span></td>
      <td>${esc(f.dimension_label || "—")}</td>
      <td>${esc(f.metadata?.policy_title || "—")}</td>
      <td class="quote-cell">${esc(f.contract_quote || "—")}</td>
      <td class="quote-cell">${esc(f.policy_quote || "—")}</td>
      <td>${esc((f.rationale || "").slice(0, 160))}</td>
    </tr>`
    )
    .join("");
  $("findingsTable").innerHTML = `<table>
    <thead><tr>
      <th>§</th><th>Status</th><th>Dimension</th><th>Playbook</th>
      <th>Contract text</th><th>Policy text</th><th>Rationale</th>
    </tr></thead>
    <tbody>${rows}</tbody></table>`;
}

function primaryFindings(findings) {
  const byKey = new Map();
  for (const f of findings) {
    const key = `${f.contract_section_id || ""}:${f.dimension_label || ""}`;
    const existing = byKey.get(key);
    const source = f.metadata?.source || "";
    if (existing) {
      if (source === "playbook_compare" && existing.metadata?.source !== "playbook_compare") {
        byKey.set(key, f);
      }
      continue;
    }
    byKey.set(key, f);
  }
  return [...byKey.values()].sort((a, b) => {
    const sec = (a.contract_section_id || "").localeCompare(b.contract_section_id || "");
    if (sec !== 0) return sec;
    return severityRank(b.severity) - severityRank(a.severity);
  });
}

function severityRank(sev) {
  const order = { critical: 3, important: 2, info: 1 };
  return order[String(sev || "").toLowerCase()] || 0;
}

function renderViolations(findings) {
  const panel = $("violationsPanel");
  if (!findings?.length) {
    panel.innerHTML = "<p>No findings.</p>";
    return;
  }
  const violations = primaryFindings(findings).filter(
    (f) =>
      f.status === "NON_COMPLIANT" &&
      (f.contract_quote || f.policy_quote) &&
      (f.metadata?.source || "") !== "section_first_final"
  );
  if (!violations.length) {
    panel.innerHTML =
      "<p class='violations-intro'>No non-compliant findings with contract + policy quotes. Check <strong>All findings</strong> or <strong>Summary</strong>.</p>";
    return;
  }
  panel.innerHTML =
    `<p class="violations-intro">${violations.length} violation(s) — contract language vs playbook standard (side by side).</p>` +
    violations.map(violationCardHtml).join("");
}

function violationCardHtml(f) {
  const policyTitle = f.metadata?.policy_title || "Policy playbook";
  const section = f.contract_section_id ? `§${f.contract_section_id}` : "—";
  return `<article class="violation-card">
    <div class="vc-header">
      <span class="vc-title">${esc(section)} — ${esc(f.dimension_label || "Violation")}</span>
      <span class="badge ${esc(f.status)}">${esc(f.status)}</span>
      <span class="badge">${esc(f.severity || "—")}</span>
    </div>
    <div class="vc-playbook">Violated playbook: <strong>${esc(policyTitle)}</strong></div>
    <div class="quote-compare">
      <div class="quote-box contract">
        <div class="quote-label contract">Contract (violates policy)</div>
        <div class="quote-text">${esc(f.contract_quote || "— no quote —")}</div>
      </div>
      <div class="quote-box policy">
        <div class="quote-label policy">Policy / playbook (required standard)</div>
        <div class="quote-text">${esc(f.policy_quote || "— no quote —")}</div>
      </div>
    </div>
    <div class="violation-rationale"><strong>Why:</strong> ${esc(f.rationale || "")}</div>
  </article>`;
}

function esc(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function parseReviewOutput(data) {
  const findings =
    data.findings ??
    data.artifacts?.report?.findings ??
    data.report?.findings ??
    [];
  const count = data.finding_count ?? findings.length;
  const md =
    data.summary_markdown ??
    data.output ??
    data.artifacts?.report?.summary_markdown ??
    "(no summary)";
  const artifact =
    data.artifact ??
    data.artifacts?.audit ??
    data.artifacts?.report?.metadata?.artifact ??
    {};
  return { findings, count, md, artifact };
}

function renderReview(data) {
  const { findings, count, md, artifact } = parseReviewOutput(data);
  $("summaryMd").textContent = md;
  renderViolations(findings);
  renderFindings(findings);
  $("artifactJson").textContent = JSON.stringify(artifact || {}, null, 2);
  $("rawJson").textContent = JSON.stringify(data, null, 2);
  const violations = primaryFindings(findings).filter(
    (f) => f.status === "NON_COMPLIANT" && (f.metadata?.source || "") !== "section_first_final"
  ).length;
  setStatus(
    `Review done — ${count} finding(s), ${violations} violation(s) with quotes`,
    "ok"
  );
}

async function runSync() {
  setStatus("Syncing NDA + policies (Java stub)…", "running");
  disableButtons(true);
  try {
    const data = await api("/api/sync", { method: "POST", body: "{}" });
    showContractId(data.contract.document_id);
    $("rawJson").textContent = JSON.stringify(data, null, 2);
    setStatus(
      `Sync OK — ${data.verify.section_count} sections, ${data.policies.length} policies`,
      "ok"
    );
  } catch (e) {
    setStatus("Sync failed: " + e.message, "err");
    $("rawJson").textContent = e.message;
  } finally {
    disableButtons(false);
  }
}

async function runReview(usePlatform) {
  setStatus(usePlatform ? "Review via platform…" : "Review (direct)…", "running");
  disableButtons(true);
  try {
    const data = await api("/api/review", {
      method: "POST",
      body: JSON.stringify({
        contract_title: "Mutual NDA (Dev UI)",
        contract_type: "nda",
        use_platform: usePlatform,
      }),
    });
    renderReview(data);
  } catch (e) {
    setStatus("Review failed: " + e.message, "err");
    $("rawJson").textContent = e.message;
  } finally {
    disableButtons(false);
  }
}

async function runTombstone() {
  setStatus("Tombstone smoke…", "running");
  disableButtons(true);
  try {
    const data = await api("/api/tombstone", { method: "POST", body: "{}" });
    $("rawJson").textContent = JSON.stringify(data, null, 2);
    setStatus(
      data.deleted_policy_in_hits ? "FAIL — deleted policy still in search" : "Tombstone OK",
      data.deleted_policy_in_hits ? "err" : "ok"
    );
  } catch (e) {
    setStatus("Tombstone failed: " + e.message, "err");
  } finally {
    disableButtons(false);
  }
}

async function runFullE2e() {
  setStatus("Full E2E running (may take several minutes)…", "running");
  disableButtons(true);
  try {
    const data = await api("/api/full-e2e", { method: "POST", body: "{}" });
    $("rawJson").textContent = JSON.stringify(data, null, 2);
    const allOk = (data.steps || []).every((s) => s.ok);
    setStatus(allOk ? "Full E2E passed" : "E2E had failures — see Raw JSON", allOk ? "ok" : "err");
    try {
      const review = await api("/api/outputs/review_result.json");
      renderReview(review);
    } catch {
      /* review output optional */
    }
  } catch (e) {
    setStatus("E2E failed: " + e.message, "err");
    $("rawJson").textContent = e.message;
  } finally {
    disableButtons(false);
  }
}

function disableButtons(on) {
  document.querySelectorAll("button").forEach((b) => (b.disabled = on));
}

// --- Custom paste-text panel ---

const SAMPLE = {
  contract: {
    title: "Mutual Non-Disclosure Agreement",
    contract_type: "nda",
    sections: [
      {
        section_id: "1",
        title: "Confidential Information",
        text: "Confidential Information means all non-public information disclosed by either party in written, oral, or visual form. The receiving party shall protect Confidential Information using the same degree of care it uses for its own confidential information, but no less than reasonable care.",
      },
      {
        section_id: "2",
        title: "Term",
        text: "This Agreement shall remain in effect for three (3) years from the Effective Date unless terminated earlier by either party upon thirty (30) days written notice.",
      },
      {
        section_id: "3",
        title: "Limitation of Liability",
        text: "Except for breaches of confidentiality obligations, the total liability of either party under this Agreement shall not exceed one hundred thousand dollars ($100,000). Neither party shall be liable for indirect, incidental, or consequential damages.",
      },
      {
        section_id: "4",
        title: "Indemnification",
        text: "Vendor shall indemnify, defend, and hold harmless Customer from third-party claims arising from Vendor's gross negligence, willful misconduct, or material breach of this Agreement.",
      },
    ],
  },
  policies: [
    {
      title: "Standard Confidentiality Playbook",
      categories: "confidentiality",
      review_guidance: "Receiving party must use at least reasonable care. Term should be at least 2 years for vendor NDAs.",
      text: "The receiving party shall protect Confidential Information using no less than reasonable care and industry-standard safeguards. NDA term shall be no less than two (2) years from the Effective Date.",
    },
    {
      title: "Liability Cap Playbook",
      categories: "liability",
      review_guidance: "Vendor liability cap should not be below $500k for enterprise deals.",
      text: "Total aggregate liability shall not be less than five hundred thousand dollars ($500,000) for vendor NDAs.",
    },
    {
      title: "Indemnification Standard",
      categories: "indemnification",
      review_guidance: "Indemnification must be mutual for both parties.",
      text: "Each party shall indemnify, defend, and hold harmless the other party from third-party claims arising from that party's gross negligence, willful misconduct, or material breach.",
    },
  ],
};

function sectionRowHtml(section, index) {
  const sid = section.section_id ?? String(index + 1);
  const canRemove = index > 0;
  return `<div class="section-row" data-index="${index}">
    <div class="section-row-head">
      <input class="section-id-input" type="text" value="${escAttr(sid)}" placeholder="§" title="Section ID" />
      <input class="section-title-input" type="text" value="${escAttr(section.title || "")}" placeholder="Section title" />
      ${canRemove ? '<button type="button" class="danger btn-remove-section">Remove</button>' : ""}
    </div>
    <textarea class="section-text-input" rows="4" placeholder="Paste contract section text here…">${esc(section.text || "")}</textarea>
  </div>`;
}

function policyCardHtml(policy, index) {
  const canRemove = index > 0;
  return `<div class="policy-card" data-index="${index}">
    <div class="policy-card-head">
      <input class="policy-title-input" type="text" value="${escAttr(policy.title || "")}" placeholder="Policy title" />
      ${canRemove ? '<button type="button" class="danger btn-remove-policy">Remove</button>' : ""}
    </div>
    <label class="compact-label">Categories (comma-separated)
      <input class="policy-categories-input" type="text" value="${escAttr(policy.categories || "general")}" placeholder="confidentiality, liability" />
    </label>
    <label class="compact-label">Review guidance (optional)
      <input class="policy-guidance-input" type="text" value="${escAttr(policy.review_guidance || "")}" placeholder="What the reviewer should check for" />
    </label>
    <label class="compact-label">Policy text
      <textarea class="policy-text-input" rows="5" placeholder="Paste playbook / policy standard text here…">${esc(policy.text || "")}</textarea>
    </label>
  </div>`;
}

function escAttr(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/"/g, "&quot;")
    .replace(/</g, "&lt;");
}

function renderContractSections(sections) {
  $("contractSections").innerHTML = sections.map(sectionRowHtml).join("");
  bindSectionButtons();
}

function renderPolicyBlocks(policies) {
  $("policyBlocks").innerHTML = policies.map(policyCardHtml).join("");
  bindPolicyButtons();
}

function bindSectionButtons() {
  $("contractSections").querySelectorAll(".btn-remove-section").forEach((btn) => {
    btn.onclick = () => {
      btn.closest(".section-row")?.remove();
      reindexSectionIds();
    };
  });
}

function bindPolicyButtons() {
  $("policyBlocks").querySelectorAll(".btn-remove-policy").forEach((btn) => {
    btn.onclick = () => btn.closest(".policy-card")?.remove();
  });
}

function reindexSectionIds() {
  $("contractSections").querySelectorAll(".section-row").forEach((row, i) => {
    const idInput = row.querySelector(".section-id-input");
    if (idInput && !idInput.dataset.userEdited) {
      idInput.value = String(i + 1);
    }
  });
}

function addContractSection() {
  const container = $("contractSections");
  const index = container.querySelectorAll(".section-row").length;
  container.insertAdjacentHTML(
    "beforeend",
    sectionRowHtml({ section_id: String(index + 1), title: "", text: "" }, index)
  );
  bindSectionButtons();
}

function addPolicyBlock() {
  const container = $("policyBlocks");
  const index = container.querySelectorAll(".policy-card").length;
  container.insertAdjacentHTML(
    "beforeend",
    policyCardHtml({ title: "", categories: "general", review_guidance: "", text: "" }, index)
  );
  bindPolicyButtons();
}

function collectCustomPayload() {
  const sections = [...$("contractSections").querySelectorAll(".section-row")].map((row) => ({
    section_id: row.querySelector(".section-id-input")?.value.trim() || "1",
    title: row.querySelector(".section-title-input")?.value.trim() || "",
    text: row.querySelector(".section-text-input")?.value || "",
  }));

  const policies = [...$("policyBlocks").querySelectorAll(".policy-card")].map((card) => ({
    title: card.querySelector(".policy-title-input")?.value.trim() || "Policy",
    categories: card.querySelector(".policy-categories-input")?.value.trim() || "general",
    review_guidance: card.querySelector(".policy-guidance-input")?.value.trim() || "",
    policy_type: $("customContractType").value.trim() || "nda",
    text: card.querySelector(".policy-text-input")?.value || "",
  }));

  return {
    contract: {
      title: $("customContractTitle").value.trim() || "My Contract",
      contract_type: $("customContractType").value.trim() || "nda",
      sections,
    },
    policies,
    run_review: true,
  };
}

function loadSampleDocs() {
  $("customContractTitle").value = SAMPLE.contract.title;
  $("customContractType").value = SAMPLE.contract.contract_type;
  renderContractSections(SAMPLE.contract.sections);
  renderPolicyBlocks(SAMPLE.policies);
  setStatus("Sample NDA + policies loaded — edit text or click Sync & review", "ok");
}

async function runCustomSync(reviewAfter) {
  const payload = collectCustomPayload();
  const hasContractText = payload.contract.sections.some((s) => s.text.trim());
  const hasPolicyText = payload.policies.some((p) => p.text.trim());
  if (!hasContractText) {
    setStatus("Add at least one contract section with text", "err");
    return;
  }
  if (!hasPolicyText) {
    setStatus("Add at least one policy with text", "err");
    return;
  }

  setStatus(reviewAfter ? "Syncing custom docs + running review…" : "Syncing custom docs…", "running");
  disableButtons(true);
  try {
    const path = reviewAfter ? "/api/custom-review" : "/api/sync-custom";
    const data = await api(path, { method: "POST", body: JSON.stringify(payload) });
    showContractId(
      data.contract_document_id ||
        data.contract?.document_id ||
        data.sync?.contract?.document_id
    );
    if (reviewAfter) {
      renderReview(data);
    } else {
      $("rawJson").textContent = JSON.stringify(data, null, 2);
      setStatus(
        `Custom sync OK — ${data.verify?.section_count ?? "?"} sections, ${data.policies?.length ?? 0} policies`,
        "ok"
      );
    }
  } catch (e) {
    setStatus((reviewAfter ? "Custom review" : "Custom sync") + " failed: " + e.message, "err");
    $("rawJson").textContent = e.message;
  } finally {
    disableButtons(false);
  }
}

document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    document.querySelectorAll(".tab-pane").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    $("tab-" + btn.dataset.tab).classList.add("active");
  });
});

$("btnSaveConfig").onclick = saveConfig;
$("btnHealth").onclick = checkHealth;
$("btnSync").onclick = runSync;
$("btnReview").onclick = () => runReview(false);
$("btnReviewPlatform").onclick = () => runReview(true);
$("btnTombstone").onclick = runTombstone;
$("btnFullE2e").onclick = runFullE2e;
$("btnAddSection").onclick = addContractSection;
$("btnAddPolicy").onclick = addPolicyBlock;
$("btnCustomReview").onclick = () => runCustomSync(true);
$("btnCustomSync").onclick = () => runCustomSync(false);
$("btnLoadSample").onclick = loadSampleDocs;

(async function init() {
  renderContractSections([{ section_id: "1", title: "", text: "" }]);
  renderPolicyBlocks([{ title: "", categories: "general", review_guidance: "", text: "" }]);
  try {
    const cfg = await api("/api/config");
    $("docUrl").value = cfg.document_server_url;
    $("platformUrl").value = cfg.platform_url;
    $("tenantId").value = cfg.tenant_id;
    if (!cfg.llm_configured) {
      setStatus("Warning: LLM_API_KEY not set — review will fail", "err");
    }
  } catch {
    setStatus("Dev UI loaded", "");
  }
})();
