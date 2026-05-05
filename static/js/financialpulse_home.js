/* FinancialPulse-inspired homepage behavior (uses existing MarketMinds APIs) */
(function () {
  function fmtChangePct(n) {
    if (typeof n !== "number" || Number.isNaN(n)) return { text: "--", cls: "fp-nt" };
    const cls = n > 0 ? "fp-up" : n < 0 ? "fp-dn" : "fp-nt";
    const sign = n > 0 ? "+" : "";
    return { text: `${sign}${n.toFixed(2)}%`, cls };
  }

  async function loadMarketOverviewTicker() {
    const priceBar = document.getElementById("fp-price-bar");
    if (!priceBar) return;

    priceBar.innerHTML = "";
    try {
      const res = await fetch("/api/market_overview");
      const data = await res.json();

      const chips = (Array.isArray(data) ? data : []).map((row) => {
        const chg = fmtChangePct(row.change_percent);
        return `
          <div class="fp-chip">
            <div class="fp-sym">${row.symbol ?? "--"}</div>
            <div class="fp-val">${row.price != null ? `$${row.price}` : "--"}</div>
            <div class="fp-chg ${chg.cls}">${chg.text}</div>
          </div>
        `;
      }).join("");

      // Duplicate content once for seamless scroll
      priceBar.innerHTML = chips + chips;
    } catch (e) {
      priceBar.innerHTML = `
        <div class="fp-chip">
          <div class="fp-sym">MARKET</div>
          <div class="fp-val">--</div>
          <div class="fp-chg fp-dn">API ERROR</div>
        </div>
      `;
    }
  }

  async function loadAIPicksPreview() {
    const box = document.getElementById("fp-ai-picks-preview");
    if (!box) return;

    box.innerHTML = `<div class="fp-muted">Loading AI Picks…</div>`;
    try {
      const res = await fetch("/api/ai_picks_ticker");
      const data = await res.json();
      if (!Array.isArray(data) || data.length === 0) {
        box.innerHTML = `<div class="fp-muted">No strong BUY signals right now.</div>`;
        return;
      }

      const rows = data.slice(0, 5).map((x) => {
        const scoreCls = typeof x.score === "string" && x.score.trim().startsWith("-") ? "fp-dn" : "fp-up";
        return `
          <div style="display:flex;justify-content:space-between;gap:10px;padding:10px 0;border-bottom:1px solid var(--border-color);">
            <div style="display:flex;flex-direction:column;gap:2px;">
              <div style="font-weight:800;">${x.symbol}</div>
              <div class="fp-muted">${x.price} · Conf ${x.confidence}</div>
            </div>
            <div style="display:flex;align-items:flex-end;flex-direction:column;gap:2px;">
              <div class="${scoreCls}" style="font-weight:800;">${x.score}</div>
              <div class="fp-muted">${x.signal}</div>
            </div>
          </div>
        `;
      }).join("");

      box.innerHTML = rows + `<div style="padding-top:10px;"><a class="fp-btn fp-btn-primary" href="/ai_picks"><i class="fas fa-medal"></i> Open AI Picks</a></div>`;
    } catch (e) {
      box.innerHTML = `<div class="fp-muted">Failed to load AI Picks preview.</div>`;
    }
  }

  function initTabs() {
    const tabs = Array.from(document.querySelectorAll(".fp-tab"));
    const panels = Array.from(document.querySelectorAll(".fp-panel"));
    if (!tabs.length || !panels.length) return;

    function activate(tabId) {
      tabs.forEach(t => t.classList.toggle("active", t.dataset.tab === tabId));
      panels.forEach(p => p.classList.toggle("active", p.id === `fp-tab-${tabId}`));
    }

    tabs.forEach((t) => {
      t.addEventListener("click", () => activate(t.dataset.tab));
    });
  }

  async function boot() {
    initTabs();
    await Promise.all([
      loadMarketOverviewTicker(),
      loadAIPicksPreview()
    ]);
    // refresh ticker periodically without changing API behavior
    setInterval(loadMarketOverviewTicker, 60000);
  }

  document.addEventListener("DOMContentLoaded", boot);
})();

