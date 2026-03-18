function colorify_str(value)
{
    if (value == "pass") {
	ret = '<span style="color:green">';
    } else if (value == "skip") {
	ret = '<span style="color:#809fff">';
    } else if (value == "warn") {
	ret = '<span style="color:#d99a00">';
    } else {
	ret = '<span style="color:red">';
    }
    return ret + value + '</span>';
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
		return sort_ord * (b.r[sort_key] < a.r[sort_key] ? 1 : -1);
	    });
	}
    }
}

function load_result_table(data_raw)
{
    var table = document.getElementById("results");
    var result_filter = {
	"pass": document.getElementById("pass").checked,
	"skip": document.getElementById("skip").checked,
	"warn": document.getElementById("warn").checked,
	"fail": document.getElementById("fail").checked
    };
    var branch_filter = document.getElementById("branch").value;
    var exec_filter = document.getElementById("executor").value;
    var remote_filter = document.getElementById("remote").value;
    var test_filter = document.getElementById("test").value;
    var pw_n = document.getElementById("pw-n").checked;
    var pw_y = document.getElementById("pw-y").checked;

    $("#results tr").slice(1).remove();

    let warn_box = document.getElementById("fl-warn-box");
    warn_box.innerHTML = "";

    let form = "";
    if (document.getElementById("ld-cases").checked)
	form = "&ld-cases=1";

    let rows = [];
    let total_results = 0;
    let filtered_results = 0;

    $.each(data_raw, function(i, v) {
	if (rows.length >= 5000) {
	    warn_box.innerHTML = "Reached 5000 rows. Set an executor, branch or test filter. Otherwise this page will set your browser on fire...";
	    return 0;
	}

	let branch_matches = !branch_filter || branch_filter == v.branch;
	let exec_matches = !exec_filter || exec_filter == v.executor;
	let remote_matches = !remote_filter || remote_filter == v.remote;

	$.each(v.results, function(j, r) {
	    total_results++;

	    if (!branch_matches || !exec_matches || !remote_matches)
		return 1;

	    if (test_filter && r.test != test_filter)
		return 1;
	    if (result_filter[r.result] == false)
		return 1;
	    if (pw_y == false && nipa_pw_reported(v, r) == true)
		return 1;
	    if (pw_n == false && nipa_pw_reported(v, r) == false)
		return 1;

	    filtered_results++;
	    rows.push({"v": v, "r": r});
	});
    });

    let filter_info_elem = document.getElementById("filter-info");
    if (total_results > 0) {
	let filtered_out = total_results - filtered_results;
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

	var row = table.insertRow();

	var date = row.insertCell(0);
	var branch = row.insertCell(1);
	var remote = row.insertCell(2);
	var exe = row.insertCell(3);
	var group = row.insertCell(4);
	var test = row.insertCell(5);
	var res = row.insertCell(6);
	let row_id = 7;
	var retry = row.insertCell(row_id++);
	var time = row.insertCell(row_id++);
	var outputs = row.insertCell(row_id++);
	var hist = row.insertCell(row_id++);
	var run = row.insertCell(row_id++);

	const branch_url = branch_urls[v.branch] || v.branch_url || "#";
	const run_url = v.branch_url || branch_url || "#";

	date.innerHTML = v.end.toLocaleString();
	branch.innerHTML = "<a href=\"" + branch_url + "\">" + v.branch + "</a>";
	remote.innerHTML = v.remote;
	exe.innerHTML = v.executor;
	group.innerHTML = r.group;
	test.innerHTML = "<b>" + r.test + "</b>";
	if ("retry" in r)
	    retry.innerHTML = colorify_str(r.retry);
	if ("time" in r)
	    time.innerHTML = nipa_msec_to_str(r.time * 1000);
	res.innerHTML = colorify_str(r.result);
	outputs.innerHTML = "<a href=\"" + r.link + "\">outputs</a>";
	hist.innerHTML = "<a href=\"contest.html?test=" + encodeURIComponent(r.test) + form + "\">history</a>";
	run.innerHTML = "<a href=\"" + run_url + "\">run</a>";
    }
}

function find_branch_urls(loaded_data)
{
    branch_urls = {};
    $.each(loaded_data, function(i, v) {
	if (v.branch_url)
	    branch_urls[v.branch] = v.branch_url;
	else if (v.remote == "brancher")
	    branch_urls[v.branch] = v.results[0].link;
    });
}

function results_update()
{
    load_result_table(loaded_data);
}

function clone_result(r)
{
    let result = Object.assign({}, r);

    if ("results" in result)
	result.results = result.results.map(clone_result);
    return result;
}

function flatten_l2_results(entry)
{
    let copy = Object.assign({}, entry);
    let flat = [];

    for (const l1 of entry.results) {
	if (!("results" in l1)) {
	    flat.push(clone_result(l1));
	    continue;
	}

	for (const case_result of l1.results) {
	    let data = clone_result(l1);
	    delete data.results;
	    delete data.time;
	    delete data.retry;
	    Object.assign(data, case_result);
	    data.test = l1.test + "." + case_result.test;
	    flat.push(data);
	}
    }

    copy.results = flat;
    return copy;
}

function clone_entry(v)
{
    let copy = Object.assign({}, v);

    copy.start = new Date(v.start);
    copy.end = new Date(v.end);
    copy.results = v.results.map(clone_result);
    return copy;
}

function select_loaded_results()
{
    const format_l2 = document.getElementById("ld-cases");
    const br_cnt = document.getElementById("ld_cnt");
    const br_name = document.getElementById("ld_branch");

    let rows = loaded_data_all || [];
    let selected = [];
    let limit = parseInt(br_cnt.value, 10);

    if (!limit || limit < 1)
	limit = 1;

    if (br_name.value) {
	selected = rows.filter(function(v) {
	    return v.branch == br_name.value;
	});
    } else {
	selected = rows.slice(0, limit);
    }

    selected = selected.map(clone_entry);
    if (format_l2.checked) {
	selected = selected.map(flatten_l2_results);
    }

    return selected;
}

let xfr_todo = 2;
let branch_urls = {};
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
	"branch": new Set(),
	"executor": new Set(),
	"remote": new Set()
    };

    $.each(data_raw || [], function(i, v) {
	values.branch.add(v.branch);
	values.executor.add(v.executor);
	values.remote.add(v.remote);
    });

    return JSON.stringify({
	"branch": Array.from(values.branch).sort(),
	"executor": Array.from(values.executor).sort(),
	"remote": Array.from(values.remote).sort()
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
    
    let old_values = new Object();

    for (const elem_id of ["branch", "executor", "remote"]) {
	var elem = document.getElementById(elem_id);
	old_values[elem_id] = elem.value;
    	while (elem.options.length)
    	    elem.remove(0);
    }

    nipa_filter_add_options(loaded_data, "branch", "branch");
    nipa_filter_add_options(loaded_data, "executor", "executor");
    nipa_filter_add_options(loaded_data, "remote", "remote");

    if (first_load)
	nipa_filters_set_from_url();

    for (const elem_id of ["branch", "executor", "remote"]) {
	var elem = document.getElementById(elem_id);

	if (!first_load)
	    elem.value = old_values[elem_id];
	if (elem.selectedIndex == -1)
	    elem.selectedIndex = 0;
    }
    loaded_filters_signature = new_signature;
}

function loaded_one()
{
    if (--xfr_todo)
	return;

    let headers = document.getElementsByTagName("th");
    for (const th of headers) {
	th.addEventListener("click", nipa_sort_key_set);
    }
    reload_select_filters(true);
    nipa_filters_enable(reload_data, "ld-pw");
    nipa_filters_enable(results_update, "fl-pw");

    results_update();
}

function filters_loaded(data_raw)
{
    nipa_set_filters_json(data_raw);
    loaded_one();
}

function apply_loaded_results(first_load)
{
    if (!loaded_data_all)
	return;

    loaded_data = select_loaded_results();
    find_branch_urls(loaded_data);

    if (xfr_todo > 0)
	return;

    reload_select_filters(first_load);
    results_update();
    nipa_filters_enable(null, ["ld-pw", "fl-pw"]);
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
	loaded_data = select_loaded_results();
	find_branch_urls(loaded_data);
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

function reload_data(event)
{
    const br_cnt = document.getElementById("ld_cnt");
    const br_name = document.getElementById("ld_branch");

    if (event) {
	if (event.target == br_name)
	    br_cnt.value = 1;
	else if (event.target == br_cnt)
	    br_name.value = "";
    }

    apply_loaded_results(false);
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
    const exec_filter = document.getElementById("executor").value;
    const remote_filter = document.getElementById("remote").value;
    const test_filter = document.getElementById("test").value;
    const pw_n = document.getElementById("pw-n").checked;
    const pw_y = document.getElementById("pw-y").checked;
    const ld_cases = document.getElementById("ld-cases").checked;
    const ld_branch = document.getElementById("ld_branch").value;
    const ld_cnt = document.getElementById("ld_cnt").value;

    const currentUrl = new URL(window.location.href);

    const filterParams = ['pass', 'skip', 'warn', 'fail', 'branch', 'executor',
			  'remote', 'test', 'pw-n', 'pw-y', 'ld-cases',
			  'ld_branch', 'ld_cnt'];
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
    if (exec_filter)
	currentUrl.searchParams.set('executor', exec_filter);
    if (remote_filter)
	currentUrl.searchParams.set('remote', remote_filter);
    if (test_filter)
	currentUrl.searchParams.set('test', test_filter);

    if (!pw_n)
	currentUrl.searchParams.set('pw-n', '0');
    if (!pw_y)
	currentUrl.searchParams.set('pw-y', '0');

    if (ld_cases)
	currentUrl.searchParams.set('ld-cases', '1');
    if (ld_branch)
	currentUrl.searchParams.set('ld_branch', ld_branch);
    if (ld_cnt)
	currentUrl.searchParams.set('ld_cnt', ld_cnt);

    window.history.pushState({}, '', currentUrl.toString());
}

function embedded_mode() {
    $('#loading-fieldset').hide();
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
    const urlParams = new URLSearchParams(window.location.search);

    if (urlParams.get("embed") === "1") {
        embedded_mode();
    }

    nipa_input_set_from_url("ld-pw");
    if (urlParams.get("branch") && !urlParams.get("ld_branch")) {
	document.getElementById("ld_branch").value = urlParams.get("branch");
	document.getElementById("ld_cnt").value = 1;
    }

    $('#update-url-button').on('click', function (e) {
        e.preventDefault();
        update_url_from_filters();
    });
    install_refresh_pause_hooks();
    nipa_sort_cb = results_update;

    $(document).ready(function() {
        $.get("contest/filters.json", filters_loaded);
    });
    fetch_results_data();
    schedule_refresh();
}
