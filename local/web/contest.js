function result_color(value)
{
    if (value == "pass") {
	return "green";
    } else if (value == "skip") {
	return "#809fff";
    } else if (value == "warn") {
	return "#d99a00";
    }
    return "red";
}

function set_text_cell(cell, value)
{
    cell.textContent = value ?? "";
}

function set_strong_text_cell(cell, value)
{
    const strong = document.createElement("b");
    strong.textContent = value ?? "";
    cell.replaceChildren(strong);
}

function set_result_cell(cell, value)
{
    if (!value) {
	cell.textContent = "";
	return;
    }

    const span = document.createElement("span");
    span.style.color = result_color(value);
    span.textContent = value;
    cell.replaceChildren(span);
}

function safe_href(value)
{
    if (!value)
	return "#";

    const href = String(value).trim();
    if (!href)
	return "#";
    if (href.startsWith("/") || href.startsWith("./") || href.startsWith("../") ||
	href.startsWith("?") || href.startsWith("#")) {
	return href;
    }

    try {
	const parsed = new URL(href, window.location.href);
	if (parsed.protocol == "http:" || parsed.protocol == "https:")
	    return parsed.toString();
    } catch (_err) {
    }

    return "#";
}

function set_link_cell(cell, href, label)
{
    const link = document.createElement("a");
    link.href = safe_href(href);
    link.textContent = label;
    cell.replaceChildren(link);
}

function set_optional_link_cell(cell, href, label)
{
    if (!href) {
	cell.textContent = "";
	return;
    }
    set_link_cell(cell, href, label);
}

function sort_results(rows)
{
    for (const sort_key of nipa_sort_keys) {
	let sort_ord = nipa_sort_get(sort_key);

	if (sort_key === "date") {
	    rows.sort(function(a, b) {
		return sort_ord * (b.v.end - a.v.end);
	    });
	} else if (sort_key === "time") {
	    rows.sort(function(a, b) {
		if (a.r[sort_key] === undefined && b.r[sort_key] === undefined)
		    return 0;
		if (a.r[sort_key] === undefined)
		    return 1;
		if (b.r[sort_key] === undefined)
		    return -1;
		return sort_ord * (b.r[sort_key] - a.r[sort_key]);
	    });
	} else {
	    rows.sort(function(a, b) {
		const left = a.r[sort_key] || "";
		const right = b.r[sort_key] || "";

		if (left == right)
		    return 0;
		return sort_ord * (right < left ? 1 : -1);
	    });
	}
    }
}

function load_result_table(data_raw)
{
    const table = document.getElementById("results");
    const result_filter = {
	"pass": document.getElementById("pass").checked,
	"skip": document.getElementById("skip").checked,
	"warn": document.getElementById("warn").checked,
	"fail": document.getElementById("fail").checked
    };
    const branch_filter = document.getElementById("branch").value;
    const test_filter = document.getElementById("test").value;

    $("#results tr").slice(1).remove();

    const warn_box = document.getElementById("fl-warn-box");
    warn_box.innerHTML = "";

    const rows = [];
    let total_results = 0;
    let filtered_results = 0;

    $.each(data_raw, function(i, v) {
	if (rows.length >= 5000) {
	    warn_box.innerHTML = "Reached 5000 rows. Set a branch filter or open a single-test history view.";
	    return 0;
	}

	const branch_matches = !branch_filter || branch_filter == v.branch;

	$.each(v.results, function(j, r) {
	    total_results++;

	    if (!branch_matches)
		return 1;
	    if (test_filter && r.test != test_filter)
		return 1;
	    if (result_filter[r.result] == false)
		return 1;

	    filtered_results++;
	    rows.push({"v": v, "r": r});
	});
    });

    const filter_info_elem = document.getElementById("filter-info");
    if (total_results > 0) {
	const filtered_out = total_results - filtered_results;
	if (filtered_out > 0) {
	    filter_info_elem.innerHTML = `${total_results} results<br />(${filtered_out} filtered out)`;
	} else {
	    filter_info_elem.innerHTML = `${total_results} results`;
	}
    } else {
	filter_info_elem.innerHTML = "";
    }

    for (const result of rows) {
	if (result.r.time)
	    result.r.time = Math.round(result.r.time);
    }

    sort_results(rows);

    for (const result of rows) {
	const r = result.r;
	const v = result.v;

	const row = table.insertRow();
	const date = row.insertCell(0);
	const branch = row.insertCell(1);
	const remote = row.insertCell(2);
	const exe = row.insertCell(3);
	const group = row.insertCell(4);
	const test = row.insertCell(5);
	const res = row.insertCell(6);
	let row_id = 7;
	const retry = row.insertCell(row_id++);
	const time = row.insertCell(row_id++);
	const outputs = row.insertCell(row_id++);
	const hist = row.insertCell(row_id++);
	const retry_output = row.insertCell(row_id++);

	set_text_cell(date, v.end.toLocaleString());
	set_link_cell(branch, v.branch_url || "#", v.branch);
	set_text_cell(remote, v.remote);
	set_text_cell(exe, v.executor);
	set_text_cell(group, r.group);
	set_strong_text_cell(test, r.test);
	if ("retry" in r)
	    set_result_cell(retry, r.retry);
	if ("time" in r)
	    set_text_cell(time, nipa_msec_to_str(r.time * 1000));
	set_result_cell(res, r.result);
	set_link_cell(outputs, r.link, "outputs");
	set_link_cell(hist, "contest.html?test=" + encodeURIComponent(r.test), "history");
	set_optional_link_cell(retry_output, r.retry_output_url, "retry_output");
    }
}

function results_update()
{
    load_result_table(loaded_data);
}

let xfr_todo = 1;
let loaded_data = null;
let loaded_data_all = null;
let refresh_in_flight = false;
let refresh_timer = null;
let pending_results_data = null;
let pending_results_fingerprint = null;
let applied_results_fingerprint = null;
let loaded_filters_signature = null;
let pointer_is_down = false;
let refresh_resume_timer = null;

function set_refresh_status(message)
{
    const status = document.getElementById("refresh-status");

    if (status)
	status.textContent = message;
}

function compute_results_fingerprint(data_raw)
{
    return JSON.stringify(data_raw);
}

function compute_filter_signature(data_raw)
{
    const values = {
	"branch": new Set()
    };

    $.each(data_raw || [], function(i, v) {
	values.branch.add(v.branch);
    });

    return JSON.stringify({
	"branch": Array.from(values.branch).sort()
    });
}

function has_active_text_selection()
{
    const selection = window.getSelection ? window.getSelection() : null;

    return !!(selection && !selection.isCollapsed && selection.toString());
}

function has_active_form_focus()
{
    const active = document.activeElement;

    if (!active || active === document.body)
	return false;

    return active.matches("input, textarea, select");
}

function page_busy_for_refresh()
{
    return pointer_is_down || has_active_text_selection() || has_active_form_focus();
}

function flush_pending_results()
{
    if (!pending_results_data || page_busy_for_refresh())
	return;

    loaded_data_all = pending_results_data;
    applied_results_fingerprint = pending_results_fingerprint;
    pending_results_data = null;
    pending_results_fingerprint = null;

    apply_loaded_results(false);
    set_refresh_status("");
}

function schedule_refresh_resume(delay)
{
    if (refresh_resume_timer)
	window.clearTimeout(refresh_resume_timer);

    refresh_resume_timer = window.setTimeout(function() {
	refresh_resume_timer = null;
	flush_pending_results();
    }, delay);
}

function install_refresh_pause_hooks()
{
    document.addEventListener("mousedown", function() {
	pointer_is_down = true;
    });
    document.addEventListener("mouseup", function() {
	pointer_is_down = false;
	schedule_refresh_resume(150);
    });

    document.addEventListener("touchstart", function() {
	pointer_is_down = true;
    }, {passive: true});
    document.addEventListener("touchend", function() {
	pointer_is_down = false;
	schedule_refresh_resume(150);
    }, {passive: true});
    document.addEventListener("touchcancel", function() {
	pointer_is_down = false;
	schedule_refresh_resume(150);
    }, {passive: true});

    document.addEventListener("selectionchange", function() {
	schedule_refresh_resume(150);
    });
    document.addEventListener("focusout", function() {
	schedule_refresh_resume(0);
    });

    window.addEventListener("blur", function() {
	pointer_is_down = false;
    });
}

function reload_select_filters(first_load)
{
    const new_signature = compute_filter_signature(loaded_data);

    if (!first_load && new_signature === loaded_filters_signature)
	return;

    const elem = document.getElementById("branch");
    const old_value = elem.value;
    while (elem.options.length)
	elem.remove(0);

    nipa_filter_add_options(loaded_data, "branch", "branch");

    if (first_load)
	nipa_filters_set_from_url();
    else
	elem.value = old_value;

    if (elem.selectedIndex == -1)
	elem.selectedIndex = 0;

    loaded_filters_signature = new_signature;
}

function loaded_one()
{
    if (--xfr_todo)
	return;

    const headers = document.getElementsByTagName("th");
    for (const th of headers) {
	th.addEventListener("click", nipa_sort_key_set);
    }
    reload_select_filters(true);
    nipa_filters_enable(results_update, "fl-pw");

    results_update();
}

function apply_loaded_results(first_load)
{
    if (!loaded_data_all)
	return;

    loaded_data = loaded_data_all;

    if (xfr_todo > 0)
	return;

    reload_select_filters(first_load);
    results_update();
    nipa_filters_enable(null, "fl-pw");
}

function normalize_result_state(result, status)
{
    if (result == "pass" || result == "skip" || result == "warn" || result == "fail")
	return result;
    if (!status)
	return "warn";
    if (status == "pass" || status == "skip" || status == "fail")
	return status;
    return "warn";
}

function normalize_loaded_results(data_raw)
{
    $.each(data_raw, function(i, v) {
	v.start = new Date(v.start);
	v.end = new Date(v.end);

	$.each(v.results, function(j, r) {
	    r.result = normalize_result_state(r.result, r.status);
	    if ("retry" in r)
		r.retry = normalize_result_state(r.retry, r.retry);
	});
    });

    data_raw.sort(function(a, b){return b.end - a.end;});
}

function results_loaded(data_raw)
{
    const fingerprint = compute_results_fingerprint(data_raw);

    if (fingerprint === applied_results_fingerprint ||
	fingerprint === pending_results_fingerprint)
	return;

    normalize_loaded_results(data_raw);

    if (xfr_todo > 0) {
	loaded_data_all = data_raw;
	applied_results_fingerprint = fingerprint;
	loaded_data = loaded_data_all;
	pending_results_data = null;
	pending_results_fingerprint = null;
	set_refresh_status("");
	loaded_one();
	return;
    }
    if (page_busy_for_refresh()) {
	pending_results_data = data_raw;
	pending_results_fingerprint = fingerprint;
	set_refresh_status("New results are ready. The table will update when you finish selecting text or editing filters.");
	return;
    }

    loaded_data_all = data_raw;
    applied_results_fingerprint = fingerprint;
    apply_loaded_results(false);
    set_refresh_status("");
}

function fetch_results_data()
{
    if (refresh_in_flight)
	return;

    refresh_in_flight = true;
    $(document).ready(function() {
	$.get("contest/all-results.json", results_loaded)
	    .always(function() {
		refresh_in_flight = false;
	    });
    });
}

function schedule_refresh()
{
    refresh_timer = window.setInterval(function() {
	if (!document.hidden)
	    fetch_results_data();
    }, 5000);
}

function update_url_from_filters()
{
    const result_filter = {
	"pass": document.getElementById("pass").checked,
	"skip": document.getElementById("skip").checked,
	"warn": document.getElementById("warn").checked,
	"fail": document.getElementById("fail").checked
    };
    const branch_filter = document.getElementById("branch").value;
    const test_filter = document.getElementById("test").value;

    const currentUrl = new URL(window.location.href);

    const filterParams = ['pass', 'skip', 'warn', 'fail', 'branch', 'test'];
    filterParams.forEach(param => currentUrl.searchParams.delete(param));

    if (!result_filter.pass)
	currentUrl.searchParams.set('pass', '0');
    if (!result_filter.skip)
	currentUrl.searchParams.set('skip', '0');
    if (!result_filter.warn)
	currentUrl.searchParams.set('warn', '0');
    if (!result_filter.fail)
	currentUrl.searchParams.set('fail', '0');

    if (branch_filter)
	currentUrl.searchParams.set('branch', branch_filter);
    if (test_filter)
	currentUrl.searchParams.set('test', test_filter);

    window.history.pushState({}, '', currentUrl.toString());
}

function embedded_mode() {
    $('#sitemap').hide();

    $('#open-full-page').show();

    $('#open-full-page-link').on('click', function(e) {
        e.preventDefault();

        const currentUrl = new URL(window.location.href);
        currentUrl.searchParams.delete('embed');

        window.open(currentUrl.toString(), '_blank');
    });
}

function do_it()
{
    nipa_load_sitemap();
    nipa_load_sponsors();

    const urlParams = new URLSearchParams(window.location.search);

    if (urlParams.get("embed") === "1") {
        embedded_mode();
    }

    document.getElementById("test").value = urlParams.get("test") || "";

    $('#update-url-button').on('click', function (e) {
        e.preventDefault();
        update_url_from_filters();
    });
    install_refresh_pause_hooks();
    nipa_sort_cb = results_update;

    fetch_results_data();
    schedule_refresh();
}
