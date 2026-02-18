(function () {
  // Progress bar width from data attribute
  const bar = document.querySelector("[data-progress]");
  if (bar) {
    const p = Number(bar.dataset.progress || "0");
    const clamped = Math.max(0, Math.min(100, p));
    bar.style.width = clamped + "%";
  }

  const el = document.getElementById("trendChart");
  if (!el) return;

  const weeks = JSON.parse(el.dataset.weeks || "[]");
  const verts = JSON.parse(el.dataset.verts || "[]");
  const packs = JSON.parse(el.dataset.packs || "[]");
  const stresses = JSON.parse(el.dataset.stresses || "[]");

  new Chart(el, {
    type: "line",
    data: {
      labels: weeks,
      datasets: [
        { label: "Vertical (ft)", data: verts, yAxisID: "y" },
        { label: "Avg pack (lb)", data: packs, yAxisID: "y1" },
        { label: "Stress", data: stresses, yAxisID: "y2" },
      ],
    },
    options: {
      responsive: true,
      interaction: { mode: "index", intersect: false },
      plugins: { legend: { labels: { color: "#cbd5e1" } } },
      scales: {
        x: { ticks: { color: "#94a3b8" }, grid: { color: "rgba(148,163,184,0.12)" } },
        y: { position: "left", ticks: { color: "#94a3b8" }, grid: { color: "rgba(148,163,184,0.12)" } },
        y1: { position: "right", ticks: { color: "#94a3b8" }, grid: { drawOnChartArea: false } },
        y2: { position: "right", ticks: { color: "#94a3b8" }, grid: { drawOnChartArea: false } },
      },
    },
  });
})();
