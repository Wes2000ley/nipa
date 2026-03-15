// SPDX-License-Identifier: GPL-2.0

(function () {
  "use strict";

  const STATUS_ORDER = {
    "running": 0,
    "retry-running": 1,
    "retry-queued": 2,
    "queued": 3,
    "fail": 4,
    "skip": 5,
    "pass": 6,
  };
  const FILTER_STATUSES = [
    "running",
    "retry-running",
    "retry-queued",
    "queued",
    "fail",
    "skip",
    "pass",
  ];

  const state = {
    config: loadConfig(),
    history: null,
    meta: null,
    live: null,
    summary: null,
    search: "",
    filters: new Set(FILTER_STATUSES),
  };

  function loadConfig() {
    const element = document.getElementById("page-config");
    if (!element) {
      throw new Error("missing page-config element");
    }
    return JSON.parse(element.textContent);
  }

  function statusClass(value) {
    return (value || "unknown").replace(/[^a-z0-9-]/g, "-");
  }

  function badge(value) {
    if (!value) {
      return "";
    }
    return `<span class="badge badge-${statusClass(value)}">${escapeHtml(value)}</span>`;
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
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

  function fmtDate(value) {
    if (!value) {
      return "";
    }
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) {
      return escapeHtml(value);
    }
    return escapeHtml(parsed.toLocaleString());
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

  function deriveCounts(live, summary) {
    if (live && live.counts) {
      return { ...live.counts };
    }
    if (summary && summary.counts) {
      return { ...summary.counts };
    }
    return {};
  }

  function deriveOverall(live, summary) {
    if (summary && summary.overall) {
      return summary.overall;
    }
    if (!live) {
      return "pending";
    }
    if (!live.finished || live.status === "building" || live.status === "running") {
      return "pending";
    }
    const counts = deriveCounts(live, summary);
    if ((counts.fail || 0) > 0) {
      return "fail";
    }
    if ((counts.pass || 0) > 0) {
      return "pass";
    }
    if ((counts.skip || 0) > 0) {
      return "skip";
    }
    return "pending";
  }

  function runStatus(live) {
    if (!live) {
      return "pending";
    }
    if (live.status) {
      return live.status;
    }
    return live.finished ? "complete" : "pending";
  }

  function topLinksHtml(config) {
    const links = [
      ["Home", config.homeUrl],
      ["Summary", config.summaryPageUrl],
      ["results.json", config.resultsManifestUrl],
      ["Artifacts", config.artifactsUrl],
      ["executor.log", config.executorLogUrl],
      ["http-server.log", config.httpLogUrl],
      ["config", config.configUrl],
      ["branches.json", config.branchesUrl],
    ];

    return links
      .filter((entry) => entry[1])
      .map((entry) => `<a href="${escapeHtml(entry[1])}">${escapeHtml(entry[0])}</a>`)
      .join("");
  }

  function renderShell() {
    document.body.innerHTML = `
      <div class="shell">
        <header class="hero">
          <div>
            <p class="eyebrow">Local NIPA vmksft</p>
            <h1>${escapeHtml(state.config.title || "Local vmksft dashboard")}</h1>
            <p class="subtitle">${escapeHtml(state.config.subtitle || "")}</p>
          </div>
          <nav class="hero-links" id="hero-links"></nav>
        </header>
        <div class="layout">
          <div class="column">
            <section class="panel">
              <h2>Run Overview</h2>
              <div id="run-meta" class="meta-grid"></div>
            </section>
            <section class="panel">
              <h2>Current Status</h2>
              <div id="run-stats" class="stats"></div>
              <p id="run-note" class="note">Loading run status.</p>
            </section>
            <section class="panel">
              <h2>Per-Test Status</h2>
              <div class="controls">
                <input id="search-box" class="search-box" type="search" placeholder="Filter tests by name, group, program, or status">
                <div id="filter-boxes" class="toggle-row"></div>
              </div>
              <div class="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Started</th>
                      <th>Group</th>
                      <th>Test</th>
                      <th>Status</th>
                      <th>Result</th>
                      <th>Retry</th>
                      <th>Attempts</th>
                      <th>Time</th>
                      <th>Links</th>
                    </tr>
                  </thead>
                  <tbody id="tests-body">
                    <tr><td colspan="9" class="table-empty">Waiting for test data.</td></tr>
                  </tbody>
                </table>
              </div>
            </section>
          </div>
          <div class="column">
            <section class="panel">
              <h2>Recent Runs</h2>
              <div class="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Date</th>
                      <th>Branch</th>
                      <th>Mode</th>
                      <th>Status</th>
                      <th>Tests</th>
                      <th>Result</th>
                      <th>Links</th>
                    </tr>
                  </thead>
                  <tbody id="history-body">
                    <tr><td colspan="7" class="history-empty">Waiting for run history.</td></tr>
                  </tbody>
                </table>
              </div>
            </section>
            <section class="panel">
              <h2>Selected Run</h2>
              <div id="selected-run-links" class="sidebar-list"></div>
            </section>
          </div>
        </div>
      </div>
    `;

    document.getElementById("hero-links").innerHTML = topLinksHtml(state.config);
    renderFilterControls();
  }

  function renderFilterControls() {
    const filterBox = document.getElementById("filter-boxes");
    const searchBox = document.getElementById("search-box");

    searchBox.value = state.search;
    searchBox.addEventListener("input", () => {
      state.search = searchBox.value.toLowerCase();
      renderTests();
    });

    filterBox.innerHTML = FILTER_STATUSES.map((status) => {
      const checked = state.filters.has(status) ? "checked" : "";
      return `
        <label class="toggle-chip">
          <input type="checkbox" value="${status}" ${checked}>
          <span>${escapeHtml(status)}</span>
        </label>
      `;
    }).join("");

    for (const input of filterBox.querySelectorAll("input")) {
      input.addEventListener("change", () => {
        if (input.checked) {
          state.filters.add(input.value);
        } else {
          state.filters.delete(input.value);
        }
        renderTests();
      });
    }
  }

  function renderMeta() {
    const meta = state.meta || {};
    const live = state.live || {};
    const summary = state.summary || {};
    const metaItems = [
      ["Mode", meta.mode],
      ["Published branch", meta.published_branch || live.branch],
      ["Source branch", meta.source_branch],
      ["Source HEAD", meta.source_head],
      ["Targets", meta.targets || (live.targets || []).join(" ")],
      ["Started", live.start || meta.branch_date],
      ["Updated", live.updated],
      ["Finished", live.finished ? (typeof live.finished === "string" ? live.finished : "yes") : ""],
      ["Top-level result", deriveOverall(live, summary)],
      ["Nested results", summary.nested_total ?? ""],
    ];

    const element = document.getElementById("run-meta");
    element.innerHTML = metaItems
      .filter((item) => item[1])
      .map((item) => `
        <div class="meta-item">
          <span class="label">${escapeHtml(item[0])}</span>
          <span class="value mono">${item[0].includes("result") ? badge(item[1]) : escapeHtml(item[1])}</span>
        </div>
      `)
      .join("");
  }

  function renderStats() {
    const live = state.live || {};
    const counts = deriveCounts(state.live, state.summary);
    const tests = live.tests || [];
    const stats = [
      ["run", runStatus(live)],
      ["build", live.build ? live.build.status : ""],
      ["tests", tests.length || Object.values(counts).reduce((acc, value) => acc + value, 0)],
      ["updated", live.updated || ""],
    ];

    const orderedCountKeys = Object.keys(counts).sort((left, right) => {
      return (STATUS_ORDER[left] ?? 99) - (STATUS_ORDER[right] ?? 99);
    });
    for (const key of orderedCountKeys) {
      stats.push([key, counts[key]]);
    }

    document.getElementById("run-stats").innerHTML = stats
      .filter((item) => item[1] !== "" && item[1] !== undefined)
      .map((item) => `
        <div class="stat">
          <span class="label">${escapeHtml(item[0])}</span>
          <span class="value">${item[0] === "run" || item[0] === "build" || item[0] === "updated" ? escapeHtml(String(item[1])) : escapeHtml(String(item[1]))}</span>
        </div>
      `)
      .join("");

    const note = document.getElementById("run-note");
    if (!state.live) {
      note.textContent = "Waiting for live-status.json from the executor.";
    } else if (state.live.finished) {
      note.textContent = "This run is complete. The table below is frozen at the final state, and raw artifacts remain browsable in the browser.";
    } else if (state.live.status === "building") {
      note.textContent = "The executor is still building the kernel and selftests. The test table will populate once vmksft-p.py publishes the test list.";
    } else {
      note.textContent = "The run is active. Completed tests link directly to raw output while retries and queued items remain visible.";
    }
  }

  function testSearchText(test) {
    return [
      test.group,
      test.test,
      test.prog,
      test.status,
      test.result,
      test.retry,
    ].join(" ").toLowerCase();
  }

  function filteredTests() {
    const tests = [...((state.live && state.live.tests) || [])];
    tests.sort((left, right) => {
      const statusCmp = (STATUS_ORDER[left.status] ?? 99) - (STATUS_ORDER[right.status] ?? 99);
      if (statusCmp) {
        return statusCmp;
      }
      return (left.tid || 0) - (right.tid || 0);
    });

    return tests.filter((test) => {
      const status = test.status || "queued";
      if (!state.filters.has(status)) {
        return false;
      }
      if (!state.search) {
        return true;
      }
      return testSearchText(test).includes(state.search);
    });
  }

  function testLinks(test) {
    const links = [];
    if (test.log_url) {
      links.push(`<a href="${escapeHtml(test.log_url)}">raw log</a>`);
    }
    if (!test.log_url && state.config.summaryPageUrl) {
      links.push(`<a href="${escapeHtml(state.config.summaryPageUrl)}">summary</a>`);
    }
    return `<div class="link-row">${links.join("")}</div>`;
  }

  function renderTests() {
    const body = document.getElementById("tests-body");
    if (!state.live || !state.live.tests || !state.live.tests.length) {
      body.innerHTML = "<tr><td colspan=\"9\" class=\"table-empty\">The executor is still building or has not published its test list yet.</td></tr>";
      return;
    }

    const tests = filteredTests();
    if (!tests.length) {
      body.innerHTML = "<tr><td colspan=\"9\" class=\"table-empty\">No tests match the current filter selection.</td></tr>";
      return;
    }

    body.innerHTML = tests.map((test) => {
      return `
        <tr>
          <td>${fmtDate(test.started || test.finished || state.live.updated)}</td>
          <td><span class="mono">${escapeHtml(test.group || "")}</span></td>
          <td>
            <strong>${escapeHtml(test.test || "")}</strong>
            <div class="table-secondary mono">${escapeHtml(test.prog || "")}</div>
          </td>
          <td>${badge(test.status || "queued")}</td>
          <td>${badge(test.result || "")}</td>
          <td>${badge(test.retry || "")}</td>
          <td>${escapeHtml(String(test.attempts ?? 0))}</td>
          <td>${escapeHtml(fmtTime(test.time))}</td>
          <td>${testLinks(test)}</td>
        </tr>
      `;
    }).join("");
  }

  function renderHistory() {
    const body = document.getElementById("history-body");
    const sidebar = document.getElementById("selected-run-links");
    const runs = (state.history && state.history.runs) || [];

    if (!runs.length) {
      body.innerHTML = "<tr><td colspan=\"7\" class=\"history-empty\">No prior local runs are available yet.</td></tr>";
      sidebar.innerHTML = "<div class=\"sidebar-item\"><strong>No selected run</strong><p>History will appear here once the first run has written its metadata.</p></div>";
      return;
    }

    body.innerHTML = runs.map((run) => {
      const counts = run.counts || {};
      const countText = Object.keys(counts).sort((left, right) => {
        return (STATUS_ORDER[left] ?? 99) - (STATUS_ORDER[right] ?? 99);
      }).map((key) => `${key}:${counts[key]}`).join(" ");

      return `
        <tr>
          <td>${fmtDate(run.updated || run.branch_date)}</td>
          <td>
            <strong>${escapeHtml(run.branch || "")}</strong>
            <div class="table-secondary mono">${escapeHtml(run.source_branch || "")}</div>
          </td>
          <td>${escapeHtml(run.mode || "")}</td>
          <td>
            ${badge(run.status || "pending")}
            <div class="table-secondary">${badge(run.overall || "pending")}</div>
          </td>
          <td>
            <span class="mono">${escapeHtml(String(run.test_total || 0))}</span>
            <div class="table-secondary mono">${escapeHtml(countText)}</div>
          </td>
          <td>${badge(run.overall || "pending")}</td>
          <td>
            <div class="link-row">
              <a href="${escapeHtml(run.dashboard_url)}">dashboard</a>
              <a href="${escapeHtml(run.summary_url)}">summary</a>
              <a href="${escapeHtml(run.results_url)}">artifacts</a>
              <a href="${escapeHtml(run.executor_log_url)}">executor.log</a>
            </div>
          </td>
        </tr>
      `;
    }).join("");

    const selected = runs[0];
    sidebar.innerHTML = `
      <div class="sidebar-item">
        <strong>${escapeHtml(selected.branch || selected.run_id)}</strong>
        <p>Mode: <span class="mono">${escapeHtml(selected.mode || "")}</span></p>
        <p>Source branch: <span class="mono">${escapeHtml(selected.source_branch || "")}</span></p>
        <p>Targets: <span class="mono">${escapeHtml(selected.targets || "")}</span></p>
        <div class="link-row" style="margin-top: 10px;">
          <a href="${escapeHtml(selected.dashboard_url)}">open dashboard</a>
          <a href="${escapeHtml(selected.summary_url)}">open summary</a>
          <a href="${escapeHtml(selected.manifest_url)}">open results.json</a>
        </div>
      </div>
    `;
  }

  async function refresh() {
    try {
      const [history, meta, live, summary] = await Promise.all([
        fetchJson(state.config.historyUrl).catch(() => null),
        fetchJson(state.config.runMetaUrl).catch(() => null),
        fetchJson(state.config.liveStatusUrl).catch(() => null),
        fetchJson(state.config.summaryUrl).catch(() => null),
      ]);

      state.history = history;
      state.meta = meta;
      state.live = live;
      state.summary = summary;

      renderMeta();
      renderStats();
      renderTests();
      renderHistory();
    } catch (error) {
      const note = document.getElementById("run-note");
      if (note) {
        note.textContent = `Waiting for dashboard data (${error.message}).`;
      }
    } finally {
      window.setTimeout(refresh, 2000);
    }
  }

  renderShell();
  refresh();
}());
