// SPDX-License-Identifier: GPL-2.0

(function () {
  "use strict";

  const RESULT_ORDER = {
    "fail": 0,
    "skip": 1,
    "pass": 2,
  };

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function badge(value) {
    if (!value) {
      return "";
    }
    const safe = String(value).replace(/[^a-z0-9-]/gi, "-").toLowerCase();
    return `<span class="badge badge-${safe}">${escapeHtml(value)}</span>`;
  }

  function fmtTime(value) {
    if (value === null || value === undefined || value === "") {
      return "";
    }
    const number = Number(value);
    if (!Number.isFinite(number)) {
      return "";
    }
    return `${number.toFixed(3)}s`;
  }

  async function fetchJson(url) {
    const response = await fetch(`${url}${url.includes("?") ? "&" : "?"}t=${Date.now()}`, {
      cache: "no-store",
    });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status} for ${url}`);
    }
    return response.json();
  }

  function renderLinks(summary) {
    const links = [
      ["run dashboard", summary.dashboard_url],
      ["results.json", summary.results_manifest_url],
      [summary.detail_json_name || "detail json", summary.detail_json_relative_url],
      ["executor.log", summary.executor_log_url],
      ["http-server.log", summary.http_log_url],
    ];

    if (summary.run_link) {
      links.push(["raw run directory", summary.run_link]);
    }

    document.getElementById("summary-links").innerHTML = links
      .filter((entry) => entry[1])
      .map((entry) => `<a href="${escapeHtml(entry[1])}">${escapeHtml(entry[0])}</a>`)
      .join("");
  }

  function renderMeta(summary) {
    const items = [
      ["Executor", summary.executor],
      ["Published branch", summary.branch],
      ["Mode", summary.mode],
      ["Source branch", summary.source_branch],
      ["Source HEAD", summary.source_head],
      ["Source tree", summary.source_tree],
      ["Targets", summary.targets],
      ["Started", summary.started],
      ["Finished", summary.finished],
      ["Duration", summary.duration],
      ["Overall", summary.overall],
      ["Nested results", summary.nested_total],
      ["Retry failures", summary.retry_failures],
    ];

    document.getElementById("summary-meta").innerHTML = items
      .filter((item) => item[1] !== "" && item[1] !== undefined)
      .map((item) => `
        <div class="meta-item">
          <span class="label">${escapeHtml(item[0])}</span>
          <span class="value mono">${item[0] === "Overall" ? badge(item[1]) : escapeHtml(item[1])}</span>
        </div>
      `)
      .join("");
  }

  function renderStats(summary) {
    const counts = summary.counts || {};
    const nestedCounts = summary.nested_counts || {};
    const stats = [
      ["overall", summary.overall],
      ["retry failures", summary.retry_failures ?? 0],
      ["nested total", summary.nested_total ?? 0],
    ];

    for (const key of Object.keys(counts).sort((left, right) => (RESULT_ORDER[left] ?? 99) - (RESULT_ORDER[right] ?? 99))) {
      stats.push([`top-level ${key}`, counts[key]]);
    }
    for (const key of Object.keys(nestedCounts).sort((left, right) => (RESULT_ORDER[left] ?? 99) - (RESULT_ORDER[right] ?? 99))) {
      stats.push([`nested ${key}`, nestedCounts[key]]);
    }

    document.getElementById("summary-stats").innerHTML = stats
      .map((item) => `
        <div class="stat">
          <span class="label">${escapeHtml(item[0])}</span>
          <span class="value">${item[0] === "overall" ? badge(item[1]) : escapeHtml(item[1])}</span>
        </div>
      `)
      .join("");
  }

  function nestedHtml(results) {
    if (!results || !results.length) {
      return "";
    }

    return `
      <details class="nested-wrap">
        <summary>${results.length} nested result(s)</summary>
        <ul class="nested-list">
          ${results.map((result) => `
            <li>
              ${badge(result.result)}
              <span class="mono">${escapeHtml(result.test || "")}</span>
              <span class="table-secondary">${escapeHtml(fmtTime(result.time))}</span>
            </li>
          `).join("")}
        </ul>
      </details>
    `;
  }

  function crashHtml(result) {
    if (!result.crashes || !result.crashes.length) {
      return "";
    }

    return `
      <div class="crash-list">
        ${result.crashes.map((entry) => escapeHtml(entry)).join("<br>")}
      </div>
    `;
  }

  function renderRows(detail) {
    const body = document.getElementById("summary-results-body");
    const results = [...(detail.results || [])];

    results.sort((left, right) => {
      const resultCmp = (RESULT_ORDER[left.result] ?? 99) - (RESULT_ORDER[right.result] ?? 99);
      if (resultCmp) {
        return resultCmp;
      }
      const groupCmp = String(left.group || "").localeCompare(String(right.group || ""));
      if (groupCmp) {
        return groupCmp;
      }
      return String(left.test || "").localeCompare(String(right.test || ""));
    });

    if (!results.length) {
      body.innerHTML = "<tr><td colspan=\"6\" class=\"table-empty\">The final result detail file does not contain any tests.</td></tr>";
      return;
    }

    body.innerHTML = results.map((result) => `
      <tr>
        <td><span class="mono">${escapeHtml(result.group || "")}</span></td>
        <td>
          <strong>${escapeHtml(result.test || "")}</strong>
          ${crashHtml(result)}
          ${nestedHtml(result.results)}
        </td>
        <td>${badge(result.result)}</td>
        <td>${badge(result.retry || "")}</td>
        <td>${escapeHtml(fmtTime(result.time))}</td>
        <td>${result.link ? `<a href="${escapeHtml(result.link)}">raw log</a>` : ""}</td>
      </tr>
    `).join("");
  }

  async function main() {
    try {
      const summary = await fetchJson("./summary.json");
      const detail = await fetchJson(summary.detail_json_relative_url);

      document.title = `${summary.executor || "vmksft"} results`;
      document.getElementById("summary-title").textContent = `${summary.executor || "vmksft"} results`;
      document.getElementById("summary-subtitle").textContent =
        `Final local vmksft summary for TARGETS=${summary.targets || ""}.`;
      document.getElementById("summary-note").textContent =
        "This page is static after completion and reads its data from summary.json plus the final results detail file.";

      renderLinks(summary);
      renderMeta(summary);
      renderStats(summary);
      renderRows(detail);
    } catch (error) {
      document.getElementById("summary-title").textContent = "Summary unavailable";
      document.getElementById("summary-subtitle").textContent = error.message;
      document.getElementById("summary-note").textContent =
        "The final summary data is not readable yet. Check summary.json and executor.log.";
    }
  }

  main();
}());
