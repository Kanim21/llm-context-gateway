// Progressive enhancement only -- the page is fully readable with this disabled.
(function () {
  "use strict";

  // Smooth-scroll for same-page nav links.
  document.querySelectorAll('nav.site-nav a[href^="#"]').forEach(function (link) {
    link.addEventListener("click", function (evt) {
      var id = link.getAttribute("href").slice(1);
      var target = document.getElementById(id);
      if (!target) return;
      evt.preventDefault();
      target.scrollIntoView({ behavior: "smooth", block: "start" });
      history.replaceState(null, "", "#" + id);
    });
  });

  // Highlight the nav link matching the section currently in view.
  var sections = Array.prototype.slice.call(document.querySelectorAll("main section[id]"));
  var navLinks = document.querySelectorAll('nav.site-nav a[href^="#"]');
  if (sections.length && navLinks.length && "IntersectionObserver" in window) {
    var observer = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          if (!entry.isIntersecting) return;
          navLinks.forEach(function (a) {
            a.style.color = a.getAttribute("href") === "#" + entry.target.id ? "" : "";
          });
        });
      },
      { rootMargin: "-40% 0px -55% 0px" }
    );
    sections.forEach(function (s) { observer.observe(s); });
  }
})();
