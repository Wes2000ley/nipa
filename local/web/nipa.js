function nipa_msec_to_str(msec) {
    const convs = [
        [1, "ms"],
        [1000, "s"],
        [60, "m"],
        [60, "h"],
        [24, "d"],
        [7, "w"]
    ];

    if (msec <= 0)
	return msec.toString();

    for (let i = 0; i < convs.length; i++) {
        if (msec < convs[i][0]) {
            let full = Math.floor(msec) + convs[i - 1][1];
            if (i > 1) {
                const frac = Math.round(msec * convs[i - 1][0] % convs[i - 1][0]);
                if (frac)
                    full += " " + frac + convs[i - 2][1];
            }
            return full;
        }
        msec /= convs[i][0];
    }

    return "TLE";
}

function nipa_br_pfx_get(name)
{
    return name.substring(0, name.length - 18);
}

function __nipa_filters_set(update_cb, set_name, enabled)
{
    if (set_name.constructor === Array) {
	for (const name of set_name)
	    __nipa_filters_set(update_cb, name, enabled);
	return;
    }

    const fl_pw = document.querySelectorAll("[name=" + set_name + "]");
    for (const one of fl_pw) {
	if (update_cb)
	    one.addEventListener("change", update_cb);
	one.disabled = enabled;
    }
}

function nipa_filters_enable(update_cb, set_name)
{
    let warn_box = document.getElementById("fl-warn-box");
    warn_box.innerHTML = "";

    __nipa_filters_set(update_cb, set_name, false);
}

function nipa_filters_disable(set_name)
{
    let warn_box = document.getElementById("fl-warn-box");
    warn_box.innerHTML = "Loading...";

    __nipa_filters_set(null, set_name, true);
}

function nipa_input_set_from_url(name)
{
    const urlParams = new URLSearchParams(window.location.search);
    const filters = document.querySelectorAll("[name="+ name + "]");

    for (const elem of filters) {
	let url_val = urlParams.get(elem.id);

	if (!url_val)
	    continue;

	if (elem.hasAttribute("checked") ||
	    elem.type == "radio" || elem.type == "checkbox") {
	    if (url_val == "0")
		elem.checked = false;
	    else if (url_val == "1")
		elem.checked = true;
	} else if (elem.type == "select-one") {
	    let option = null;
	    for (const candidate of elem.options) {
		if (candidate.value == url_val) {
		    option = candidate;
		    break;
		}
	    }

	    if (!option) {
		const opt = document.createElement('option');
		opt.value = url_val;
		opt.textContent = url_val;
		opt.setAttribute("style", "display: none;");
		elem.appendChild(opt);
	    }
	    elem.value = url_val;
	} else {
	    elem.value = url_val;
	}
    }
}

function nipa_filters_set_from_url()
{
    nipa_input_set_from_url("fl-pw");
}

function nipa_select_add_option(select_elem, show_str, value)
{
    const opt = document.createElement('option');
    opt.value = value;
    opt.textContent = show_str;
    select_elem.appendChild(opt);
}

function nipa_filter_add_options(data_raw, elem_id, field)
{
    var elem = document.getElementById(elem_id);
    var values = new Set();

    nipa_select_add_option(elem, "-- all --", "");

    $.each(data_raw, function(i, v) {
	if (field)
	    values.add(v[field]);
	else
	    values.add(v);
    });
    for (const value of values) {
	nipa_select_add_option(elem, value, value);
    }
}

function nipa_load_sitemap()
{
    const sitemap = document.getElementById("sitemap");

    if (!sitemap)
	return;

    sitemap.innerHTML = `
      <nav>
        <img src="/favicon-contest.png" style="height: 1em;"> <a href="/contest.html">Result log</a> |
        <img src="/favicon-status.png" style="height: 1em;"> <a href="/latest/index.html">Latest run</a> |
        <img src="/favicon-flakes.png" style="height: 1em;"> <a href="/history.json">Run history</a>
      </nav>
    `;
}

function nipa_load_sponsors()
{
}

var nipa_sort_cb = null;
let nipa_sort_keys = [];
let nipa_sort_polarity = [];

function nipa_sort_key_set(event)
{
    let elem = event.target;
    let what = elem.innerText.toLowerCase().replace(/[^a-z0-9]/g, '');
    const index = nipa_sort_keys.indexOf(what);
    let polarity = 1;

    if (index != -1) {
	polarity = nipa_sort_polarity[index];

	let main_key = index == nipa_sort_keys.length - 1;
	if (main_key)
	    polarity *= -1;

	nipa_sort_keys.splice(index, 1);
	nipa_sort_polarity.splice(index, 1);
	elem.innerText = elem.innerText.slice(0, -2);

	if (main_key && polarity == 1) {
	    elem.classList.remove('column-sorted');
	    nipa_sort_cb();
	    return;
	}
    } else {
	elem.classList.add('column-sorted');
    }

    if (polarity == 1) {
	elem.innerHTML = elem.innerText + " &#11206;";
    } else {
	elem.innerHTML = elem.innerText + " &#11205;";
    }

    nipa_sort_keys.push(what);
    nipa_sort_polarity.push(polarity);

    nipa_sort_cb();
}

function nipa_sort_get(what)
{
    const index = nipa_sort_keys.indexOf(what);

    if (index == -1)
	return 0;
    return nipa_sort_polarity[index];
}
