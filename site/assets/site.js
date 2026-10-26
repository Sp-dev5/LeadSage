(function () {
  var root = document.documentElement;
  root.classList.add("js");
  var reduceMq = matchMedia("(prefers-reduced-motion: reduce)");
  var narrow = matchMedia("(max-width: 1000px)");
  var saved = null;
  try { saved = localStorage.getItem("motion"); } catch (e) {}
  if (saved === "on") root.setAttribute("data-motion", "on");
  function reduced() { return reduceMq.matches && root.getAttribute("data-motion") !== "on"; }

  function splitWords(node, counter) {
    Array.prototype.slice.call(node.childNodes).forEach(function (child) {
      if (child.nodeType === 3) {
        var frag = document.createDocumentFragment();
        child.textContent.split(/(\s+)/).forEach(function (part) {
          if (!part) return;
          if (/^\s+$/.test(part)) { frag.appendChild(document.createTextNode(" ")); return; }
          var outer = document.createElement("span");
          outer.className = "w";
          var inner = document.createElement("span");
          inner.textContent = part;
          inner.style.setProperty("--i", counter.n++);
          outer.appendChild(inner);
          frag.appendChild(outer);
        });
        child.parentNode.replaceChild(frag, child);
      } else if (child.nodeType === 1 && child.tagName !== "MARK") {
        splitWords(child, counter);
      }
    });
  }
  var h1 = document.querySelector("[data-split]");
  var split = false;
  function ensureSplit() { if (h1 && !split && !reduced()) { split = true; splitWords(h1, { n: 0 }); } }
  ensureSplit();

  var pills = Array.prototype.slice.call(document.querySelectorAll("#pills a"));
  var spots = [["how", document.getElementById("how"), document.getElementById("pin")],
               ["proof", document.getElementById("proof"), document.querySelector(".proof")],
               ["control", document.getElementById("control"), document.querySelector(".cells")]];
  function onScrollSpy() {
    var mid = window.innerHeight * 0.45, on = "";
    spots.forEach(function (s) {
      if (s[1].getBoundingClientRect().top <= mid && s[2].getBoundingClientRect().bottom >= mid) on = s[0];
    });
    pills.forEach(function (a) { a.classList.toggle("is-on", a.getAttribute("href") === "#" + on); });
  }
  window.addEventListener("scroll", onScrollSpy, { passive: true });
  onScrollSpy();
  if ("IntersectionObserver" in window) {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) { if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); } });
    }, { threshold: 0.12 });
    document.querySelectorAll(".reveal").forEach(function (el) { io.observe(el); });
  } else {
    document.querySelectorAll(".reveal").forEach(function (el) { el.classList.add("in"); });
  }

  var stText = document.getElementById("statementText");
  var stWords = [];
  (function () {
    var words = [];
    Array.prototype.slice.call(stText.childNodes).forEach(function (n) {
      var key = n.nodeType === 1 && n.classList.contains("key");
      if (key) {
        var k = document.createElement("span");
        k.className = "sw key";
        k.textContent = n.textContent;
        words.push(k);
        return;
      }
      (n.textContent || "").split(/(\s+)/).forEach(function (part) {
        if (!part) return;
        if (/^\s+$/.test(part)) { words.push(document.createTextNode(" ")); return; }
        var s = document.createElement("span");
        s.className = "sw";
        s.textContent = part;
        words.push(s);
      });
    });
    stText.textContent = "";
    words.forEach(function (w) { stText.appendChild(w); if (w.className) stWords.push(w); });
  })();
  function onScrollStatement() {
    var r = stText.getBoundingClientRect(), vh = window.innerHeight;
    var p = reduced() ? 1 : Math.min(1, Math.max(0, (vh * 0.82 - r.top) / (r.height + vh * 0.28)));
    var n = Math.ceil(p * stWords.length * 1.04);
    stWords.forEach(function (w, i) { w.classList.toggle("lit", i < n); });
  }
  window.addEventListener("scroll", onScrollStatement, { passive: true });
  window.addEventListener("resize", onScrollStatement);
  onScrollStatement();

  var counters = Array.prototype.slice.call(document.querySelectorAll("[data-to]"));
  function runCounters() {
    counters.forEach(function (el) {
      var to = parseInt(el.getAttribute("data-to"), 10);
      if (reduced() || to === 0) { el.textContent = to; return; }
      var start = null;
      el.textContent = "0";
      function frame(ts) {
        if (start === null) start = ts;
        var k = Math.min(1, (ts - start) / 1300);
        el.textContent = Math.round(to * (1 - Math.pow(1 - k, 3)));
        if (k < 1) requestAnimationFrame(frame);
      }
      requestAnimationFrame(frame);
    });
  }
  if ("IntersectionObserver" in window) {
    var cio = new IntersectionObserver(function (entries) {
      if (entries[0].isIntersecting) { cio.disconnect(); runCounters(); }
    }, { threshold: 0.5 });
    cio.observe(document.querySelector(".nums"));
  }

  var pin = document.getElementById("pin");
  var steps = Array.prototype.slice.call(pin.querySelectorAll(".step"));
  var scenes = Array.prototype.slice.call(pin.querySelectorAll(".scene"));
  var current = 0, picked = false, inView = false, tick = null;
  function show(i) {
    current = i;
    steps.forEach(function (s, k) { s.classList.toggle("is-on", k === i); });
    scenes.forEach(function (s, k) { s.classList.toggle("on", k === i); });
  }
  function stopTour() { clearInterval(tick); tick = null; }
  function runTour() {
    stopTour();
    if (picked || reduced() || !inView || !pin.classList.contains("tabbed")) return;
    tick = setInterval(function () { show((current + 1) % steps.length); }, 6000);
  }
  function layout() {
    var tabbed = !narrow.matches;
    pin.classList.toggle("tabbed", tabbed);
    if (tabbed) show(current);
    else { scenes.forEach(function (s) { s.classList.add("on"); }); steps.forEach(function (s) { s.classList.remove("is-on"); }); }
    runTour();
  }
  steps.forEach(function (s, k) {
    s.querySelector("button").addEventListener("click", function () {
      if (!pin.classList.contains("tabbed")) return;
      picked = true;
      stopTour();
      show(k);
    });
  });
  pin.addEventListener("mouseenter", stopTour);
  pin.addEventListener("mouseleave", runTour);
  if ("IntersectionObserver" in window) {
    new IntersectionObserver(function (entries) { inView = entries[0].isIntersecting; runTour(); }, { threshold: 0.4 }).observe(pin);
  }
  window.addEventListener("resize", layout);
  reduceMq.addEventListener && reduceMq.addEventListener("change", layout);
  layout();

  var toggle = document.getElementById("motion");
  function paintToggle() {
    toggle.hidden = !reduceMq.matches;
    toggle.textContent = root.getAttribute("data-motion") === "on" ? "Animations on. Turn off" : "Your system asks for less motion. Play animations";
  }
  toggle.addEventListener("click", function () {
    var on = root.getAttribute("data-motion") === "on";
    if (on) root.removeAttribute("data-motion"); else root.setAttribute("data-motion", "on");
    try { localStorage.setItem("motion", on ? "off" : "on"); } catch (e) {}
    paintToggle();
    ensureSplit();
    layout();
  });
  paintToggle();

  if (matchMedia("(hover: hover)").matches) {
    var mx = -999, my = -999, queued = false;
    var aim = function () {
      queued = false;
      root.style.setProperty("--lx", mx + "px");
      root.style.setProperty("--ly", my + "px");
    };
    var queue = function () { if (!queued) { queued = true; requestAnimationFrame(aim); } };
    window.addEventListener("mousemove", function (e) { mx = e.clientX; my = e.clientY; queue(); }, { passive: true });
    root.addEventListener("mouseleave", function () { mx = -999; my = -999; queue(); });
  }

  var info = {
    a: { cut: false, text: '<span class="state">Kept</span><br>Quoted from <b>northwind.example/news</b>: "Northwind opens Singapore office, hiring account executives" · read 14 Oct 2026' },
    b: { cut: false, text: '<span class="state">Kept</span><br>Quoted from <b>northwind.example/careers</b>: "Account Executive (Singapore)" · read 14 Oct 2026' },
    c: { cut: true, text: '<span class="state">Cut</span><br><b>No quote found.</b> Nothing on their site mentions an award, so this sentence never reaches the email.' }
  };
  var cites = Array.prototype.slice.call(document.querySelectorAll(".cite"));
  var box = document.getElementById("source");
  var demo = document.getElementById("demo");
  var userPicked = false;
  var timer = null;
  function pick(k, fromUser) {
    if (fromUser) { userPicked = true; clearInterval(timer); }
    cites.forEach(function (c) { c.classList.toggle("on", c.getAttribute("data-k") === k); });
    box.classList.add("swap");
    setTimeout(function () {
      box.innerHTML = info[k].text;
      box.classList.toggle("is-cut", info[k].cut);
      box.classList.remove("swap");
    }, reduced() ? 0 : 180);
  }
  cites.forEach(function (c) {
    c.addEventListener("click", function () { pick(c.getAttribute("data-k"), true); });
    c.addEventListener("mouseenter", function () { if (matchMedia("(hover: hover)").matches) pick(c.getAttribute("data-k"), true); });
    c.addEventListener("focus", function () { pick(c.getAttribute("data-k"), true); });
  });
  box.innerHTML = info.a.text;
  if ("IntersectionObserver" in window && !reduced()) {
    var seen = new IntersectionObserver(function (entries) {
      if (!entries[0].isIntersecting) return;
      seen.disconnect();
      var order = ["b", "c", "a"], n = 0;
      timer = setInterval(function () {
        if (userPicked || n >= order.length) { clearInterval(timer); return; }
        pick(order[n++], false);
      }, 2600);
    }, { threshold: 0.6 });
    seen.observe(demo);
  }
})();
