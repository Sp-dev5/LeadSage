(function () {
  "use strict";

  var motion = document.getElementById("motion");
  if (motion) {
    var root = document.documentElement;
    var reduceMq = matchMedia("(prefers-reduced-motion: reduce)");
    var paintMotion = function () {
      motion.hidden = !reduceMq.matches;
      motion.textContent = root.getAttribute("data-motion") === "on" ? "Animations on. Turn off" : "Your system asks for less motion. Play animations";
    };
    motion.addEventListener("click", function () {
      var on = root.getAttribute("data-motion") === "on";
      if (on) { root.removeAttribute("data-motion"); } else { root.setAttribute("data-motion", "on"); }
      try { localStorage.setItem("motion", on ? "off" : "on"); } catch (e) {}
      paintMotion();
    });
    paintMotion();
  }

  document.addEventListener("click", function (e) {
    var row = e.target.closest("tr[data-href]");
    if (row && !e.target.closest("a, button, input, select, textarea, label")) { location.href = row.dataset.href; }
  });

  var job = document.querySelector("[data-job]");
  if (job) {
    var log = job.querySelector("[data-job-log]");
    var state = job.querySelector("[data-job-state]");
    var dots = job.querySelector("[data-job-dots]");
    var poll = function () {
      fetch("/jobs/" + job.dataset.job).then(function (r) { return r.json(); }).then(function (j) {
        log.textContent = j.lines.join("\n");
        log.scrollTop = log.scrollHeight;
        if (!j.done) { setTimeout(poll, 1500); return; }
        dots.remove();
        state.textContent = j.failed ? "Failed" : "Finished";
        state.className = j.failed ? "pill pen" : "pill hl";
        if (j.failed) { log.textContent += "\n" + j.failed; }
        setTimeout(function () { location.reload(); }, j.failed ? 4000 : 1200);
      }).catch(function () { setTimeout(poll, 3000); });
    };
    log.scrollTop = log.scrollHeight;
    setTimeout(poll, 1000);
  }

  document.querySelectorAll("form[data-busy]").forEach(function (form) {
    form.addEventListener("submit", function (e) {
      var button = e.submitter || form.querySelector("button[type=submit]");
      if (!button) { return; }
      button.textContent = form.dataset.busy + "...";
      button.classList.add("busy");
      setTimeout(function () { button.disabled = true; }, 0);
    });
  });

  document.querySelectorAll("form[data-autosubmit]").forEach(function (form) {
    var range = form.querySelector("[data-range]");
    var out = form.querySelector("[data-range-out]");
    if (range && out) { range.addEventListener("input", function () { out.textContent = range.value; }); }
    form.querySelectorAll("input[type=checkbox], input[type=range], select").forEach(function (el) {
      el.addEventListener("change", function () { form.requestSubmit(); });
    });
  });

  document.querySelectorAll("[data-toggle]").forEach(function (button) {
    button.addEventListener("click", function () {
      var target = document.getElementById(button.dataset.toggle);
      target.hidden = !target.hidden;
      if (!target.hidden) {
        var area = target.querySelector("textarea");
        if (area) { area.focus(); }
      }
    });
  });

  var scoring = document.getElementById("scoring-form");
  if (scoring) {
    var weightInputs = scoring.querySelectorAll("[data-weight]");
    var shares = scoring.querySelectorAll("[data-share]");
    var bars = scoring.querySelectorAll("[data-bar]");
    var paintShares = function () {
      var values = Array.prototype.map.call(weightInputs, function (i) { return Math.max(parseFloat(i.value) || 0, 0); });
      var total = values.reduce(function (a, b) { return a + b; }, 0) || 1;
      values.forEach(function (v, n) {
        var pct = Math.round(100 * v / total);
        shares[n].textContent = pct;
        bars[n].style.width = pct + "%";
      });
    };
    weightInputs.forEach(function (i) { i.addEventListener("input", paintShares); });
  }

  var rules = document.getElementById("rules-form");
  if (rules) {
    var facts = JSON.parse(rules.dataset.facts);
    var table = document.getElementById("rules-table");
    var fill = function (select, options, chosen) {
      select.textContent = "";
      options.forEach(function (o) {
        var opt = document.createElement("option");
        opt.value = o;
        opt.textContent = o;
        if (o === chosen) { opt.selected = true; }
        select.appendChild(opt);
      });
    };
    var cell = function (child) {
      var td = document.createElement("td");
      td.appendChild(child);
      return td;
    };
    var pick = function (name, label) {
      var select = document.createElement("select");
      select.name = name;
      select.setAttribute("aria-label", label);
      return select;
    };
    var newRule = function () {
      var tr = document.createElement("tr");
      tr.setAttribute("data-rule", "");
      var fact = pick("rule_fact", "Fact");
      var value = pick("rule_value", "Value");
      var action = pick("rule_action", "Then");
      var why = document.createElement("input");
      why.type = "text";
      why.name = "rule_label";
      why.required = true;
      why.setAttribute("aria-label", "Why");
      var remove = document.createElement("button");
      remove.type = "button";
      remove.className = "b ghost";
      remove.setAttribute("data-remove-rule", "");
      remove.textContent = "Remove";
      fill(fact, Object.keys(facts), Object.keys(facts)[0]);
      fill(value, facts[fact.value], null);
      fill(action, ["review", "disqualify"], "review");
      Array.prototype.forEach.call(action.options, function (o) { o.textContent = o.value.charAt(0).toUpperCase() + o.value.slice(1); });
      var end = cell(remove);
      end.className = "r";
      [cell(fact), cell(value), cell(action), cell(why), end].forEach(function (td) { tr.appendChild(td); });
      table.appendChild(tr);
      why.focus();
    };
    rules.addEventListener("change", function (e) {
      if (e.target.name === "rule_fact") {
        fill(e.target.closest("tr").querySelector("[name=rule_value]"), facts[e.target.value], null);
      }
    });
    rules.addEventListener("click", function (e) {
      if (e.target.matches("[data-remove-rule]")) { e.target.closest("tr").remove(); }
    });
    document.getElementById("add-rule").addEventListener("click", newRule);
  }

  var match = document.getElementById("match-form");
  if (match) {
    var industry = document.getElementById("industry-table");
    match.addEventListener("click", function (e) {
      if (e.target.matches("[data-remove-row]")) { e.target.closest("tr").remove(); }
    });
    document.getElementById("add-industry").addEventListener("click", function () {
      var rows = industry.querySelectorAll("tr[data-ind]");
      if (!rows.length) { return; }
      var row = rows[rows.length - 1].cloneNode(true);
      row.querySelectorAll("select").forEach(function (s) { s.value = ""; });
      row.querySelector("input").value = "0";
      industry.appendChild(row);
      row.querySelector("select").focus();
    });
  }

  var icp = document.getElementById("icp-form");
  if (icp) {
    var area = function (field) { return icp.querySelector("textarea[data-field='" + field + "']"); };
    var box = function (field) { return icp.querySelector("[data-sugg='" + field + "']"); };
    var status = function (field) { return icp.querySelector("[data-status='" + field + "']"); };
    var DELAY = 2000, MIN_CHARS = 3;
    var asked = {}, timers = {}, running = null;

    var titleBox = function (field) { return icp.querySelector("[data-titles='" + field + "']"); };
    var showTitles = function (field, titles) {
      var holder = titleBox(field);
      if (!holder) { return; }
      var list = holder.querySelector("div");
      list.textContent = "";
      titles.forEach(function (title) {
        var chip = document.createElement("button");
        chip.type = "button";
        chip.textContent = title;
        chip.addEventListener("click", function () {
          if (chip.classList.contains("added")) { return; }
          var t = area(field);
          var lines = t.value.replace(/\s+$/, "").split("\n");
          var last = lines[lines.length - 1];
          if (/^Titles: /.test(last)) { lines[lines.length - 1] = last + ", " + title; }
          else { lines.push("Titles: " + title); }
          t.value = lines.join("\n").replace(/^\n/, "");
          asked[field] = t.value.trim();
          chip.classList.add("added");
        });
        list.appendChild(chip);
      });
      holder.hidden = titles.length === 0;
    };

    var ask = function (field, manual) {
      var button = icp.querySelector("[data-suggest='" + field + "']");
      var context = {};
      icp.querySelectorAll("textarea[data-field]").forEach(function (t) { context[t.dataset.field] = t.value; });
      var sent = area(field).value;
      asked[field] = sent.trim();
      running = field;
      button.disabled = true;
      status(field).textContent = "Writing a suggestion with the local model. This takes about 15 seconds.";
      fetch("/icp/suggest", { method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": document.querySelector("meta[name=csrf]").content },
        body: JSON.stringify({ field: field, context: context }) })
        .then(function (r) { return r.json(); })
        .catch(function () { return { error: "The app did not answer. Is it still running?" }; })
        .then(function (j) {
          status(field).textContent = "";
          if (!manual && area(field).value !== sent) { return; }
          if (!manual && !j.text) { status(field).textContent = j.error; return; }
          var b = box(field);
          b.querySelector("p").textContent = j.text || j.error;
          b.querySelector("[data-use]").hidden = !j.text;
          b.hidden = false;
          showTitles(field, j.titles || []);
        })
        .then(function () { running = null; button.disabled = false; });
    };

    var fire = function (field) {
      var text = area(field).value.trim();
      if (text.length < MIN_CHARS || text === asked[field]) { return; }
      if (running) { timers[field] = setTimeout(function () { fire(field); }, 1000); return; }
      ask(field, false);
    };

    icp.querySelectorAll("[data-suggest]").forEach(function (button) {
      button.addEventListener("click", function () {
        var field = button.dataset.suggest;
        clearTimeout(timers[field]);
        if (running) { status(field).textContent = "Still writing another suggestion. Try again in a moment."; return; }
        ask(field, true);
      });
    });

    icp.querySelectorAll("textarea[data-field]").forEach(function (t) {
      t.addEventListener("input", function () {
        var field = t.dataset.field;
        clearTimeout(timers[field]);
        box(field).hidden = true;
        showTitles(field, []);
        if (!running) { status(field).textContent = ""; }
        timers[field] = setTimeout(function () { fire(field); }, DELAY);
      });
    });
    icp.querySelectorAll("[data-use]").forEach(function (button) {
      button.addEventListener("click", function () {
        var field = button.dataset.use;
        var kept = area(field).value.split("\n").filter(function (l) { return /^Titles: /.test(l); });
        area(field).value = [box(field).querySelector("p").textContent].concat(kept).join("\n");
        box(field).hidden = true;
      });
    });
    icp.querySelectorAll("[data-dismiss]").forEach(function (button) {
      button.addEventListener("click", function () { box(button.dataset.dismiss).hidden = true; });
    });

    var chips = icp.querySelector("[data-chips]");
    var input = chips.querySelector("input[type=text]");
    var known = Array.prototype.map.call(document.querySelectorAll("#country-list option"), function (o) { return o.value; });
    var have = function () {
      return Array.prototype.map.call(chips.querySelectorAll("input[type=hidden]"), function (i) { return i.value; });
    };
    var add = function () {
      var name = known.find(function (k) { return k.toLowerCase() === input.value.trim().toLowerCase(); });
      if (!name || have().indexOf(name) >= 0) { return; }
      var chip = document.createElement("span");
      chip.textContent = name;
      var x = document.createElement("button");
      x.type = "button"; x.textContent = "×"; x.setAttribute("aria-label", "Remove " + name);
      var hidden = document.createElement("input");
      hidden.type = "hidden"; hidden.name = "countries"; hidden.value = name;
      chip.append(x, hidden);
      chips.insertBefore(chip, input);
      input.value = "";
    };
    input.addEventListener("change", add);
    input.addEventListener("keydown", function (e) { if (e.key === "Enter") { e.preventDefault(); add(); } });
    chips.addEventListener("click", function (e) {
      if (e.target.tagName === "BUTTON") { e.target.parentElement.remove(); }
    });
  }
})();
