(function () {
  "use strict";

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
    icp.querySelectorAll("[data-suggest]").forEach(function (button) {
      button.addEventListener("click", function () {
        var field = button.dataset.suggest;
        var context = {};
        icp.querySelectorAll("textarea[data-field]").forEach(function (t) { context[t.dataset.field] = t.value; });
        var label = button.textContent;
        button.disabled = true;
        button.textContent = "Writing a suggestion with the local model...";
        fetch("/icp/suggest", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ field: field, context: context }) })
          .then(function (r) { return r.json(); })
          .then(function (j) {
            var b = box(field);
            b.querySelector("p").textContent = j.text || j.error;
            b.querySelector("[data-use]").hidden = !j.text;
            b.hidden = false;
          })
          .finally(function () { button.disabled = false; button.textContent = label; });
      });
    });
    icp.querySelectorAll("[data-use]").forEach(function (button) {
      button.addEventListener("click", function () {
        var field = button.dataset.use;
        area(field).value = box(field).querySelector("p").textContent;
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
