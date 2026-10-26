// Sets meter widths from data attributes (inline styles are blocked by the CSP).
document.querySelectorAll(".meter-fill[data-percent]").forEach((el) => {
  el.style.width = `${Math.min(100, Math.max(0, parseFloat(el.dataset.percent) || 0))}%`;
});
