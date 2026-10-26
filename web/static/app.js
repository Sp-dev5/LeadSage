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
      fetch("/icp/suggest", { method: "POST", headers: { "Content-Type": "application/json" },
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
