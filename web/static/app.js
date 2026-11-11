// Small page helpers. Every page still works without this file.
document.addEventListener("click", function (e) {
  var b = e.target.closest("[data-copy]");
  if (!b) return;
  var text = b.getAttribute("data-copy"), label = b.textContent;
  var done = function () { b.textContent = "Copied"; setTimeout(function () { b.textContent = label; }, 1500); };
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(done);
  } else {   // older browsers
    var t = document.createElement("textarea");
    t.value = text; document.body.appendChild(t); t.select();
    try { document.execCommand("copy"); done(); } catch (err) {}
    t.remove();
  }
});

// SKU generator: the "Reason details" box only shows for Misc
(function () {
  var reason = document.querySelector("select[name=reason]"), box = document.getElementById("reason-note");
  if (!reason || !box) return;
  var input = box.querySelector("input");
  var sync = function () {
    var misc = reason.value === "X";
    box.hidden = !misc; input.required = misc;
    if (!misc) input.value = "";
  };
  reason.addEventListener("change", sync);
  sync();
})();
