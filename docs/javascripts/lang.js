// Language switch: open the same page in the other language. Both sites have the same page names:
// /agentic-sim-world-gen/<page> (English) and /agentic-sim-world-gen/ja/<page> (Japanese).
document.addEventListener(
  "click",
  (event) => {
    const link = event.target.closest("a[hreflang]");
    if (!link) return;
    const base = "/agentic-sim-world-gen/";
    let page = location.pathname.startsWith(base) ? location.pathname.slice(base.length) : "";
    if (page.startsWith("ja/")) page = page.slice("ja/".length);
    link.href = base + (link.getAttribute("hreflang") === "ja" ? "ja/" : "") + page;
  },
  true,
);
