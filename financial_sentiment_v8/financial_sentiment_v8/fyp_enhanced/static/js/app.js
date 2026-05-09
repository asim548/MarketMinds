/* FinancialPulse v5 — Industrial ML Frontend */
'use strict';

// ── Socket.IO ─────────────────────────────────────────────────────────────────
const socket = io({ transports: ['websocket', 'polling'] });
let sentimentChart = null;
let _cachedAssets = [];

socket.on('connect', () => {
  document.getElementById('conn-dot').style.background = '#00e676';
  setStatus('Live');
});
socket.on('disconnect', () => {
  document.getElementById('conn-dot').style.background = '#ff4444';
  setStatus('Disconnected');
});
socket.on('price_update', d => renderPrices(d.prices));
socket.on('news_update', d => {
  if (document.querySelector('#tab-dashboard.active')) renderPriorityNews(d.articles);
  toast('📡 ' + d.articles.length + ' new market signals received', 'info');
});

function setStatus(s) {
  document.getElementById('last-update').textContent = s + ' · ' + new Date().toLocaleTimeString();
}

// ── Tabs ──────────────────────────────────────────────────────────────────────
document.querySelectorAll('.tab').forEach(t => {
  t.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
    document.querySelectorAll('.tab-panel').forEach(x => x.classList.remove('active'));
    t.classList.add('active');
    const panel = document.getElementById('tab-' + t.dataset.tab);
    if (panel) panel.classList.add('active');
    // Lazy-load
    const tab = t.dataset.tab;
    if (tab === 'signals')      loadSignals();
    if (tab === 'trade')        loadTradeSignal();
    if (tab === 'portfolio')    loadPortfolio();
    if (tab === 'social')       loadSocial();
    if (tab === 'vip')          loadVIPSignals();
    if (tab === 'news')         loadNews();
    if (tab === 'backtest')     {};
    if (tab === 'live_tracker') loadLiveTracker();
    if (tab === 'data_quality') loadDataQuality();
    if (tab === 'explainability') loadExplainability();
    if (tab === 'risk_control') loadRiskControl();
  });
});

// ── Toast ─────────────────────────────────────────────────────────────────────
function toast(msg, type = 'info') {
  const el = document.createElement('div');
  el.className = 'toast';
  el.style.borderLeftColor = type === 'error' ? '#ff4444' : type === 'success' ? '#00e676' : '#00d4ff';
  el.style.borderLeftWidth = '3px';
  el.textContent = msg;
  document.getElementById('toast-container').appendChild(el);
  setTimeout(() => el.remove(), 4000);
}

// ── Prices — Auto-Scrolling Ticker (Fix #1) ───────────────────────────────────
function renderPrices(prices) {
  const bar = document.getElementById('price-bar');
  if (!prices || !bar) return;
  const items = Object.values(prices);
  if (!items.length) {
    bar.innerHTML = '<div class="price-chip"><span class="sym">···</span><span class="val">Prices</span><span class="chg nt">loading…</span></div>';
    return;
  }

  // Build chip HTML
  const makeChip = (p) => {
    const chg = p.change_pct || 0;
    const cls = chg > 0 ? 'up' : chg < 0 ? 'dn' : 'nt';
    const arrow = chg > 0 ? '▲' : chg < 0 ? '▼' : '—';
    return `<div class="price-chip" title="${p.symbol || p.asset_key}">
      <span class="sym">${p.symbol || p.asset_key || ''}</span>
      <span class="val">${fmtPrice(p.price)}</span>
      <span class="chg ${cls}">${arrow} ${Math.abs(chg).toFixed(2)}%</span>
    </div>`;
  };

  // Duplicate chips for seamless infinite scroll
  const html = items.map(makeChip).join('');
  bar.innerHTML = html + html;   // two copies → CSS animation loops seamlessly

  // Adjust animation duration: ~80px per chip, 40s for a comfortable speed
  const trackWidth = items.length * 100;   // approx px
  const duration = Math.max(30, Math.min(90, trackWidth / 25));
  bar.style.animationDuration = duration + 's';
}

function fmtPrice(v) {
  if (!v) return '—';
  if (v >= 1000) return '$' + v.toLocaleString(undefined, {maximumFractionDigits: 2});
  if (v >= 1) return '$' + v.toFixed(4);
  return '$' + v.toFixed(6);
}

// ── Helpers ────────────────────────────────────────────────────────────────────
function sigBadge(signal, cls) {
  return `<span class="sig ${cls || signal.toLowerCase().replace(' ', '-')}">${signal}</span>`;
}

function confBar(conf) {
  const pct = Math.round((conf || 0) * 100);
  return `<div class="conf-bar"><div class="conf-fill" style="width:${pct}%"></div></div>
          <div style="font-size:10px;color:var(--muted);margin-top:3px;">${pct}% confidence</div>`;
}

function scoreColor(sc) {
  if (sc >= 0.15) return 'var(--green)';
  if (sc <= -0.15) return 'var(--red)';
  return 'var(--muted)';
}

function vipBadges(vips) {
  if (!vips || !vips.length) return '';
  return vips.map(v => `<span class="vip-tag" title="${v.title}">👤 ${v.name}</span>`).join(' ');
}

// ── Refresh ───────────────────────────────────────────────────────────────────
function triggerRefresh() {
  socket.emit('request_refresh');
  toast('↻ Refreshing data...', 'info');
  setTimeout(loadDashboard, 3000);
}

// ── Dashboard ─────────────────────────────────────────────────────────────────
async function loadDashboard() {
  try {
    const [stats, priority, vipData] = await Promise.all([
      fetch('/api/stats').then(r => r.json()),
      fetch('/api/priority_news?limit=6').then(r => r.json()),
      fetch('/api/vip_signals').then(r => r.json()),
    ]);

    if (stats.success) {
      renderAssetOverview(stats.asset_counts);
      renderSentimentChart(stats.sentiment_breakdown);
      populateAssetFilters(Object.keys(stats.asset_counts));
      _cachedAssets = Object.keys(stats.asset_counts);
    }
    if (priority.success) renderPriorityNews(priority.items);
    if (vipData.success)  renderDashVIP(vipData.items);
    if (stats.prices)     renderPrices(stats.prices);
    loadFearGreed();
    setStatus('Updated');
  } catch(e) { console.error(e); }
}

function renderSentimentChart(breakdown) {
  const ctx = document.getElementById('sentimentChart');
  if (!ctx) return;
  const { bullish = 0, bearish = 0, neutral = 0 } = breakdown || {};
  if (sentimentChart) sentimentChart.destroy();
  sentimentChart = new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels: ['Bullish', 'Neutral', 'Bearish'],
      datasets: [{ data: [bullish, neutral, bearish],
        backgroundColor: ['#00e676','#334155','#ff4444'],
        borderColor: '#111827', borderWidth: 2 }]
    },
    options: { responsive: true, maintainAspectRatio: false,
      plugins: { legend: { labels: { color: '#e2e8f0', font: { size: 12 } } } } }
  });
}

function renderAssetOverview(assets) {
  const el = document.getElementById('asset-overview');
  if (!el) return;
  el.innerHTML = Object.entries(assets).slice(0, 8).map(([k, v]) => `
    <div class="asset-card">
      <div class="icon-name">
        <span style="font-size:18px;">${v.icon}</span>
        <div>
          <div style="font-weight:700;font-size:13px;">${v.label}</div>
          <div style="font-size:10px;color:var(--muted);">${v.symbol}</div>
        </div>
      </div>
      ${sigBadge(v.signal, v.signal_class)}
      <div style="font-size:12px;margin-top:8px;color:${scoreColor(v.avg_score)};">
        Score: ${(v.avg_score >= 0 ? '+' : '') + v.avg_score.toFixed(3)}
      </div>
      <div style="font-size:11px;color:var(--muted);margin-top:4px;">
        📰 ${(v.bullish||0) + (v.bearish||0) + (v.neutral||0)} articles
      </div>
    </div>`).join('');
}

function renderPriorityNews(items) {
  const el = document.getElementById('priority-news-list');
  if (!el || !items) return;
  el.innerHTML = items.slice(0, 6).map(n => {
    const s = n.sentiment || {};
    const impact = n._impact || 0;
    const nicheTags = s.niche_tags || [];
    return `<div class="news-item">
      <div class="headline">
        <a href="${n.url}" target="_blank" style="color:var(--text);text-decoration:none;">${n.title}</a>
      </div>
      <div class="meta">
        <span class="src">${n.source}</span>
        ${sigBadge(s.signal || s.label || 'neutral', s.signal_class || s.label)}
        <span style="background:rgba(0,212,255,.1);color:var(--accent);padding:2px 5px;border-radius:4px;font-size:10px;">
          Impact: ${(impact * 100).toFixed(1)}
        </span>
        ${vipBadges(s.vip_persons || [])}
      </div>
      ${nicheTags.length ? `<div style="display:flex;flex-wrap:wrap;gap:4px;margin-top:6px;">${nicheBadges(nicheTags)}</div>` : ''}
    </div>`;
  }).join('') || '<p style="color:var(--muted);text-align:center;padding:20px;">No news loaded yet</p>';
}

function renderDashVIP(items) {
  const el = document.getElementById('dash-vip-list');
  if (!el) return;
  if (!items || !items.length) {
    el.innerHTML = '<p style="color:var(--muted);text-align:center;padding:20px;">No VIP mentions detected</p>';
    return;
  }
  el.innerHTML = items.slice(0, 5).map(item => {
    const vip = item.vip_persons[0];
    return `<div class="vip-item">
      <div class="vip-avatar">${vip.icon}</div>
      <div class="vip-info">
        <div class="vip-name">${vip.name} <span class="model-pill">${vip.impact_multiplier}x boost</span></div>
        <div class="vip-title">${vip.title}</div>
        <div style="font-size:12px;margin-top:4px;color:var(--text);opacity:.8;">${item.title.slice(0,90)}...</div>
        <div class="vip-affected">
          ${(vip.affected_assets||[]).map(a => `<span class="vip-tag">${a}</span>`).join('')}
        </div>
      </div>
    </div>`;
  }).join('');
}

async function loadFearGreed() {
  try {
    const d = await fetch('/api/social?asset=bitcoin').then(r => r.json());
    const fg = d.fear_greed || {};
    const el = document.getElementById('fg-container');
    if (el && fg.value !== undefined) {
      const col = fg.score > 0.2 ? 'var(--green)' : fg.score < -0.2 ? 'var(--red)' : 'var(--yellow)';
      el.innerHTML = `<div class="fg-gauge">
        <div class="fg-value" style="color:${col};">${Math.round(fg.value)}</div>
        <div class="fg-label">Fear & Greed · ${fg.rating || '—'}</div>
      </div>`;
    }
  } catch(e) {}
}

// ── AI Signals ─────────────────────────────────────────────────────────────────

// Niche group → display label
const NICHE_GROUP_LABELS = {
  'Crypto': '🔗 Crypto', 'Forex': '💱 Forex', 'DXY/Dollar': '💵 DXY',
  'US Indices': '📈 US Indices', 'EU Indices': '🇪🇺 EU Indices',
  'UK Indices': '🇬🇧 UK Indices', 'Asia Indices': '🗾 Asia',
  'Metals': '🥇 Metals', 'Energy': '⚡ Energy',
  'Bonds': '📜 Bonds', 'Macro/Risk': '🌍 Macro', 'Volatility': '😨 VIX',
};

function assetNicheLabel(assetKey) {
  // Map asset key → niche group label for the signal card header
  const ASSET_NICHE = {
    bitcoin:'Crypto', ethereum:'Crypto', crypto:'Crypto',
    eurusd:'Forex', gbpusd:'Forex', usdjpy:'Forex', audusd:'Forex',
    nzdusd:'Forex', usdcad:'Forex', usdchf:'Forex', usd:'DXY/Dollar',
    sp500:'US Indices', nasdaq:'US Indices', us30:'US Indices', russell2000:'US Indices',
    dax:'EU Indices', ftse:'UK Indices', nikkei:'Asia Indices',
    gold:'Metals', silver:'Metals', platinum:'Metals', copper:'Metals',
    oil:'Energy', natgas:'Energy',
    bonds:'Bonds', geopolitics:'Macro/Risk', inflation:'Macro/Risk', vix:'Volatility',
  };
  const grp = ASSET_NICHE[assetKey] || '';
  return grp ? NICHE_GROUP_LABELS[grp] || grp : '';
}

async function loadSignals() {
  const grid = document.getElementById('signals-grid');
  if (!grid) return;
  grid.innerHTML = '<div class="loader"><div class="spin"></div></div>';
  const asset = document.getElementById('sig-asset-filter')?.value || 'all';
  try {
    const d = await fetch(`/api/recommendations?asset=${asset}`).then(r => r.json());
    if (!d.success) { grid.innerHTML = '<p style="color:var(--red);">Failed to load signals</p>'; return; }
    const recs = d.recommendations;
    grid.innerHTML = Object.entries(recs).map(([k, rec]) => {
      const nicheLabel = assetNicheLabel(k);
      return `
      <div class="card" style="border-left:3px solid ${rec.signal_color};">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px;">
          <div style="display:flex;align-items:center;gap:8px;">
            <span style="font-size:20px;">${rec.icon}</span>
            <div>
              <div style="font-weight:700;">${rec.label}</div>
              <div style="display:flex;gap:5px;align-items:center;margin-top:2px;">
                <div style="font-size:10px;color:var(--muted);">${rec.symbol}</div>
                ${nicheLabel ? `<span style="font-size:9px;padding:1px 5px;border-radius:3px;background:rgba(124,58,237,.2);color:#a78bfa;">${nicheLabel}</span>` : ''}
              </div>
            </div>
          </div>
          ${sigBadge(rec.signal, rec.signal_class)}
        </div>
        ${confBar(rec.confidence)}
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:10px;">
          <div class="metric">
            <div class="val" style="font-size:16px;color:${scoreColor(rec.composite_score)};">${(rec.composite_score >= 0 ? '+' : '') + rec.composite_score.toFixed(3)}</div>
            <div class="lbl">Composite</div>
          </div>
          <div class="metric">
            <div class="val" style="font-size:16px;">${(rec.sentiment_score >= 0 ? '+' : '') + (rec.sentiment_score || 0).toFixed(3)}</div>
            <div class="lbl">Sentiment</div>
          </div>
          <div class="metric">
            <div class="val" style="font-size:16px;color:${rec.momentum >= 0 ? 'var(--green)' : 'var(--red)'};">${(rec.momentum >= 0 ? '+' : '') + (rec.momentum || 0).toFixed(3)}</div>
            <div class="lbl">Momentum</div>
          </div>
          <div class="metric">
            <div class="val" style="font-size:16px;">${Math.round((rec.win_probability || 0.5) * 100)}%</div>
            <div class="lbl">Win Prob</div>
          </div>
        </div>
        ${rec.vip_persons && rec.vip_persons.length ? `<div style="margin-top:8px;">${vipBadges(rec.vip_persons)}</div>` : ''}
        ${rec.social_score ? `<div style="font-size:11px;color:var(--muted);margin-top:6px;">🌐 Social: ${rec.social_score >= 0 ? '+' : ''}${rec.social_score.toFixed(3)} · ${rec.social_coverage} posts</div>` : ''}
        <div style="font-size:11px;color:var(--muted);margin-top:4px;">📰 ${rec.coverage} articles</div>
        ${rec.supporting_articles && rec.supporting_articles[0] ? `
          <div style="margin-top:8px;padding-top:8px;border-top:1px solid var(--border);">
            <div style="font-size:10px;color:var(--muted);margin-bottom:4px;">TOP SIGNAL:</div>
            <div style="font-size:11px;line-height:1.4;">${rec.supporting_articles[0].title.slice(0,80)}...</div>
            ${rec.supporting_articles[0].vip_persons && rec.supporting_articles[0].vip_persons.length ? `<div style="font-size:10px;color:var(--accent);margin-top:3px;">👤 ${rec.supporting_articles[0].vip_persons.join(', ')}</div>` : ''}
          </div>` : ''}
      </div>`;
    }).join('') || '<p style="color:var(--muted);">No recommendations available</p>';
  } catch(e) { grid.innerHTML = `<p style="color:var(--red);">Error: ${e.message}</p>`; }
}

// ── Trade Signals ─────────────────────────────────────────────────────────────
async function loadTradeSignal() {
  const el = document.getElementById('trade-signal-panel');
  if (!el) return;
  el.innerHTML = '<div class="loader"><div class="spin"></div></div>';
  const asset = document.getElementById('trade-asset-select')?.value || 'bitcoin';
  try {
    const d = await fetch(`/api/trade_signals?asset=${asset}`).then(r => r.json());
    if (!d.success) { el.innerHTML = `<p style="color:var(--red);">Error: ${d.error}</p>`; return; }
    const ts = d.trade_signal || {};
    const actionColor = d.action === 'BUY' ? 'var(--green)' : d.action === 'SELL' ? 'var(--red)' : 'var(--muted)';
    const fg = d.fear_greed || {};

    el.innerHTML = `
    <div class="grid-2" style="margin-bottom:16px;">
      <div class="card" style="border-top:3px solid ${actionColor};">
        <div class="card-title">🎯 ML Trade Decision</div>
        <div style="text-align:center;padding:10px 0;">
          <div style="font-size:48px;font-weight:900;color:${actionColor};">${d.action || 'HOLD'}</div>
          <div style="font-size:14px;color:var(--muted);margin-top:4px;">${d.signal || ''}</div>
        </div>
        ${confBar(d.confidence)}
        <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin-top:12px;">
          <div class="metric">
            <div class="val" style="font-size:15px;color:${scoreColor(d.composite_score || 0)};">${((d.composite_score||0) >= 0 ? '+' : '') + (d.composite_score||0).toFixed(3)}</div>
            <div class="lbl">Composite</div>
          </div>
          <div class="metric">
            <div class="val" style="font-size:15px;">${Math.round((d.win_probability||0.5)*100)}%</div>
            <div class="lbl">Win Prob</div>
          </div>
          <div class="metric">
            <div class="val" style="font-size:15px;color:${fg.score > 0 ? 'var(--green)' : fg.score < 0 ? 'var(--red)' : 'var(--yellow)'};">${Math.round(fg.value||50)}</div>
            <div class="lbl">Fear/Greed</div>
          </div>
        </div>
        ${d.vip_persons && d.vip_persons.length ? `
          <div style="margin-top:10px;padding:8px;background:rgba(124,58,237,.1);border-radius:6px;border:1px solid rgba(124,58,237,.3);">
            <div style="font-size:10px;color:#a78bfa;font-weight:700;margin-bottom:4px;">VIP SIGNAL DETECTED</div>
            ${d.vip_persons.map(v => `<div style="font-size:12px;">${v.icon} <strong>${v.name}</strong> — ${v.title} <span class="model-pill">${v.impact_multiplier}x boost</span></div>`).join('')}
          </div>` : ''}
      </div>

      <div class="card">
        <div class="card-title">📐 Entry / SL / TP Levels</div>
        ${ts.action && ts.action !== 'HOLD' ? `
          <div class="trade-box">
            <div style="display:flex;justify-content:space-between;align-items:center;">
              <span style="font-size:13px;font-weight:700;">${ts.direction} Trade</span>
              <span style="font-size:11px;color:var(--muted);">ATR: ${ts.atr || '—'}</span>
            </div>
            <div class="levels">
              <div class="level">
                <div class="lbl entry-lbl">ENTRY</div>
                <div class="val entry-lbl">${fmtPrice(ts.entry)}</div>
              </div>
              <div class="level">
                <div class="lbl sl-lbl">STOP LOSS</div>
                <div class="val sl-lbl">${fmtPrice(ts.sl)}</div>
              </div>
              <div class="level">
                <div class="lbl tp-lbl">TP1</div>
                <div class="val tp-lbl">${fmtPrice(ts.tp1)}</div>
              </div>
              <div class="level">
                <div class="lbl" style="color:var(--accent);">R:R RATIO</div>
                <div class="val" style="color:var(--accent);">1:${ts.rr_ratio}</div>
              </div>
              <div class="level">
                <div class="lbl sl-lbl">RISK</div>
                <div class="val sl-lbl">${ts.risk_pct}%</div>
              </div>
              <div class="level">
                <div class="lbl tp-lbl">TP2 (FULL)</div>
                <div class="val tp-lbl">${fmtPrice(ts.tp2)}</div>
              </div>
            </div>
            <div style="margin-top:10px;padding:8px;background:var(--bg);border-radius:6px;display:flex;justify-content:space-between;font-size:11px;">
              <span>Kelly Position Size: <strong style="color:var(--accent);">${ts.position_size_pct}% of capital</strong></span>
              <span>Reward: <strong style="color:var(--green);">+${ts.reward_pct}%</strong></span>
            </div>
          </div>
        ` : `<div style="text-align:center;padding:30px;color:var(--muted);">
          <div style="font-size:36px;">⏸</div>
          <div style="margin-top:8px;">HOLD — Insufficient signal strength or confidence</div>
          <div style="font-size:11px;margin-top:4px;">${ts.reason || ''}</div>
        </div>`}
      </div>
    </div>`;
  } catch(e) { el.innerHTML = `<p style="color:var(--red);">Error: ${e.message}</p>`; }
}

// ── Portfolio ─────────────────────────────────────────────────────────────────
async function loadPortfolio() {
  const el = document.getElementById('portfolio-panel');
  if (!el) return;
  el.innerHTML = '<div class="loader"><div class="spin"></div></div>';
  const method = document.getElementById('portfolio-method')?.value || 'risk_parity';
  try {
    const d = await fetch(`/api/portfolio?method=${method}`).then(r => r.json());
    if (!d.success) { el.innerHTML = `<p style="color:var(--red);">Error: ${d.error}</p>`; return; }

    const weights = d.weights || {};
    const assetRecs = d.asset_recommendations || {};
    const sortedWeights = Object.entries(weights).sort((a,b) => Math.abs(b[1]) - Math.abs(a[1]));

    el.innerHTML = `
    <div class="grid-2" style="margin-bottom:16px;">
      <div class="card">
        <div class="card-title">📊 Portfolio Metrics</div>
        <div class="grid-3" style="gap:8px;">
          <div class="metric">
            <div class="val" style="color:${(d.expected_return||0) >= 0 ? 'var(--green)' : 'var(--red)'};">${((d.expected_return||0) >= 0 ? '+' : '') + (d.expected_return||0).toFixed(3)}</div>
            <div class="lbl">Exp. Return</div>
          </div>
          <div class="metric">
            <div class="val">${(d.portfolio_vol||0).toFixed(3)}</div>
            <div class="lbl">Portfolio Vol</div>
          </div>
          <div class="metric">
            <div class="val" style="color:var(--accent);">${(d.sharpe_estimate||0).toFixed(2)}</div>
            <div class="lbl">Sharpe Est.</div>
          </div>
        </div>
        <div style="margin-top:12px;font-size:11px;color:var(--muted);">Method: <strong style="color:var(--accent);">${method.replace('_',' ').toUpperCase()}</strong></div>
      </div>
      <div class="card">
        <div class="card-title">🟢 Long / 🔴 Short Split</div>
        <div style="display:flex;gap:12px;font-size:13px;">
          <div><span style="color:var(--green);font-weight:700;">LONG</span>: ${(d.long_assets||[]).join(', ') || 'None'}</div>
        </div>
        <div style="display:flex;gap:12px;font-size:13px;margin-top:6px;">
          <div><span style="color:var(--red);font-weight:700;">SHORT/HEDGE</span>: ${(d.short_assets||[]).join(', ') || 'None'}</div>
        </div>
      </div>
    </div>
    <div class="card">
      <div class="card-title">⚖️ Optimal Allocation Weights</div>
      ${sortedWeights.map(([k, w]) => {
        const rec = assetRecs[k] || {};
        const isLong = w > 0;
        const color = isLong ? 'var(--green)' : 'var(--red)';
        const bar = Math.abs(w) * 100;
        return `<div class="weight-row">
          <div style="min-width:100px;font-size:12px;font-weight:600;">${k}</div>
          <div class="weight-bar-wrap">
            <div class="weight-bar-fill" style="width:${Math.min(100,bar*2)}%;background:${color};"></div>
          </div>
          <div style="min-width:60px;text-align:right;font-size:13px;font-weight:700;color:${color};">
            ${(w >= 0 ? '+' : '') + (w * 100).toFixed(1)}%
          </div>
          <div style="min-width:80px;margin-left:8px;">
            ${rec.signal ? sigBadge(rec.signal) : ''}
          </div>
          <div style="min-width:60px;font-size:11px;color:var(--muted);">
            ${rec.confidence ? Math.round(rec.confidence * 100) + '% conf' : ''}
          </div>
        </div>`;
      }).join('')}
    </div>`;
  } catch(e) { el.innerHTML = `<p style="color:var(--red);">Error: ${e.message}</p>`; }
}

// ── Social Sentiment ──────────────────────────────────────────────────────────
async function loadSocial(force = false) {
  const el = document.getElementById('social-panel');
  if (!el) return;
  el.innerHTML = '<div class="loader"><div class="spin"></div></div>';
  const asset = document.getElementById('social-asset-filter')?.value || 'all';
  try {
    const d = await fetch(`/api/social?asset=${asset}${force ? '&force=1' : ''}`).then(r => r.json());
    const fg = d.fear_greed || {};
    const sources = d.sources || {};
    const gt = d.google_trends || {};
    const fgColor = (fg.score||0) > 0.2 ? 'var(--green)' : (fg.score||0) < -0.2 ? 'var(--red)' : 'var(--yellow)';

    el.innerHTML = `
    <div class="grid-3" style="margin-bottom:16px;">
      <div class="card" style="text-align:center;">
        <div class="card-title">🌐 Social Score</div>
        <div style="font-size:36px;font-weight:800;color:${(d.weighted_score||0) >= 0 ? 'var(--green)' : 'var(--red)'};">
          ${((d.weighted_score||0) >= 0 ? '+' : '') + (d.weighted_score||0).toFixed(3)}
        </div>
        <div style="font-size:11px;color:var(--muted);margin-top:4px;">${d.post_count||0} posts analyzed</div>
        ${confBar(d.confidence)}
      </div>
      <div class="card" style="text-align:center;">
        <div class="card-title">😨 Fear & Greed Index</div>
        <div style="font-size:36px;font-weight:800;color:${fgColor};">${Math.round(fg.value||50)}</div>
        <div style="font-size:14px;color:${fgColor};margin-top:4px;">${fg.rating||'Neutral'}</div>
        <div style="font-size:10px;color:var(--muted);margin-top:4px;">CNN Market Sentiment</div>
      </div>
      <div class="card">
        <div class="card-title">📊 Sources Breakdown</div>
        ${Object.entries(sources).map(([src, info]) => `
          <div style="display:flex;justify-content:space-between;padding:5px 0;font-size:12px;border-bottom:1px solid var(--border);">
            <span class="social-chip" style="padding:2px 6px;"><span class="plat">${src}</span> ${info.count} posts</span>
            <span style="color:${(info.avg_score||0) >= 0 ? 'var(--green)' : 'var(--red)'};">${((info.avg_score||0) >= 0 ? '+' : '') + (info.avg_score||0).toFixed(3)}</span>
          </div>`).join('') || '<p style="color:var(--muted);font-size:12px;">Loading sources...</p>'}
      </div>
    </div>
    ${Object.keys(gt).length ? `
    <div class="card" style="margin-bottom:16px;">
      <div class="card-title">🔍 Google Trends (24h Interest Change)</div>
      <div style="display:flex;flex-wrap:wrap;gap:10px;">
        ${Object.entries(gt).map(([kw, trend]) => `
          <div style="padding:8px 12px;background:var(--surface2);border-radius:8px;border:1px solid var(--border);">
            <div style="font-size:11px;color:var(--muted);">${kw}</div>
            <div style="font-size:14px;font-weight:700;color:${trend >= 0 ? 'var(--green)' : 'var(--red)'};">${trend >= 0 ? '▲' : '▼'} ${Math.abs(trend*100).toFixed(1)}%</div>
          </div>`).join('')}
      </div>
    </div>` : ''}`;
  } catch(e) { el.innerHTML = `<p style="color:var(--red);">Error: ${e.message}</p>`; }
}

// ── VIP Signals ───────────────────────────────────────────────────────────────
async function loadVIPSignals() {
  const el = document.getElementById('vip-panel');
  if (!el) return;
  el.innerHTML = '<div class="loader"><div class="spin"></div></div>';
  try {
    const d = await fetch('/api/vip_signals').then(r => r.json());
    if (!d.success || !d.items.length) {
      el.innerHTML = `<div class="card"><p style="color:var(--muted);text-align:center;padding:30px;">No VIP mentions detected in current news cycle</p></div>`;
      return;
    }
    el.innerHTML = `<div class="card">
      <div class="card-title">👤 VIP Market Signals <span class="badge">Real-time ML Boosted</span></div>
      ${d.items.map(item => {
        const vip = item.vip_persons[0] || {};
        const scoreColor2 = (item.score||0) >= 0.1 ? 'var(--green)' : (item.score||0) <= -0.1 ? 'var(--red)' : 'var(--muted)';
        return `<div class="vip-item">
          <div class="vip-avatar">${vip.icon||'👤'}</div>
          <div class="vip-info" style="flex:1;">
            <div style="display:flex;gap:8px;align-items:center;">
              <span class="vip-name">${vip.name||'Unknown'}</span>
              <span class="model-pill">${vip.impact_multiplier||1}x signal boost</span>
              <span class="model-pill" style="background:rgba(0,212,255,.1);color:var(--accent);">${vip.title||''}</span>
            </div>
            <div style="font-size:12px;margin-top:4px;line-height:1.4;">
              <a href="${item.url}" target="_blank" style="color:var(--text);">${item.title}</a>
            </div>
            <div style="display:flex;gap:8px;margin-top:6px;align-items:center;">
              <span class="src" style="font-size:11px;color:var(--accent);">${item.source}</span>
              <span style="font-size:12px;font-weight:700;color:${scoreColor2};">${((item.score||0)>=0?'+':'')+(item.score||0).toFixed(3)}</span>
              ${(item.assets||[]).slice(0,4).map(a => `<span class="vip-tag">${a.symbol||a.key}</span>`).join('')}
            </div>
          </div>
        </div>`;
      }).join('')}
    </div>`;
  } catch(e) { el.innerHTML = `<p style="color:var(--red);">Error: ${e.message}</p>`; }
}

// ── News ──────────────────────────────────────────────────────────────────────
// Niche signal color map
const NICHE_COLORS = {
  'CRYPTO':  '#f59e0b', 'BTC/USD':  '#f59e0b', 'ETH/USD': '#f59e0b',
  'FOREX':   '#3b82f6', 'EUR/USD':  '#3b82f6', 'GBP/USD': '#3b82f6',
  'AUD/USD': '#3b82f6', 'NZD/USD':  '#3b82f6', 'USD/JPY': '#3b82f6',
  'DXY':     '#a855f7', 'XAU/USD':  '#eab308', 'XAG/USD': '#94a3b8',
  'WTI/OIL': '#ef4444', 'NATGAS':   '#f97316',
  'SPX':     '#10b981', 'NAS100':   '#10b981', 'US30':    '#10b981',
  'DAX':     '#06b6d4', 'UK100':    '#06b6d4', 'JP225':   '#ec4899',
  'TNX':     '#64748b', 'VIX':      '#dc2626', 'MACRO':   '#8b5cf6',
};

function nicheBadges(nicheTags) {
  if (!nicheTags || !nicheTags.length) return '';
  return nicheTags.slice(0, 4).map(nt => {
    const col = NICHE_COLORS[nt.tag] || NICHE_COLORS[nt.niche] || '#64748b';
    const sigCol = nt.signal.includes('BUY') ? '#00e676' : nt.signal.includes('SELL') ? '#ff4444' : '#94a3b8';
    return `<span title="${nt.niche}: ${nt.signal}" style="display:inline-flex;align-items:center;gap:3px;
      font-size:10px;font-weight:700;padding:2px 7px;border-radius:4px;
      background:${col}18;color:${col};border:1px solid ${col}40;white-space:nowrap;">
      ${nt.tag} <span style="color:${sigCol};">${nt.signal === 'STRONG BUY' ? '⬆⬆' : nt.signal === 'BUY' ? '⬆' : nt.signal === 'STRONG SELL' ? '⬇⬇' : nt.signal === 'SELL' ? '⬇' : '↔'}</span>
    </span>`;
  }).join('');
}

function validityBadge(item) {
  const v = item.validity_score;
  if (v === undefined) return '';
  const col = v >= 0.7 ? '#00e676' : v >= 0.4 ? '#ffd600' : '#ff4444';
  const label = v >= 0.7 ? 'High Quality' : v >= 0.4 ? 'Medium' : 'Low';
  const age = item.age_hours !== undefined ? `${item.age_hours}h old` : '';
  return `<span title="Validity: ${(v*100).toFixed(0)}% · ${age}" style="font-size:10px;padding:2px 6px;
    border-radius:4px;background:${col}18;color:${col};border:1px solid ${col}40;">${label}</span>`;
}

async function loadNews() {
  const el = document.getElementById('news-list');
  if (!el) return;
  el.innerHTML = '<div class="loader"><div class="spin"></div></div>';
  const asset = document.getElementById('news-asset-filter')?.value || 'all';
  const sent  = document.getElementById('news-sent-filter')?.value || 'all';
  const q     = document.getElementById('news-search')?.value || '';
  try {
    const d = await fetch(`/api/news?asset=${asset}&sentiment=${sent}&q=${encodeURIComponent(q)}&limit=40`).then(r => r.json());
    if (!d.success) { el.innerHTML = '<p style="color:var(--red);">Failed</p>'; return; }
    el.innerHTML = d.items.map(n => {
      const s = n.sentiment || {};
      const nicheTags = s.niche_tags || [];
      const hasNiches = nicheTags.length > 0;
      return `<div class="news-item">
        <div class="headline"><a href="${n.url}" target="_blank" style="color:var(--text);text-decoration:none;">${n.title}</a></div>
        <div class="meta" style="flex-wrap:wrap;gap:6px;">
          <span class="src">${n.source}</span>
          ${sigBadge(s.signal || s.label || 'neutral', s.signal_class || s.label)}
          <span style="color:${scoreColor(s.score||0)};font-size:11px;">${((s.score||0)>=0?'+':'')+(s.score||0).toFixed(3)}</span>
          <span style="font-size:11px;color:var(--muted);">${Math.round((s.confidence||0)*100)}% conf</span>
          ${validityBadge(n)}
          <span style="margin-left:auto;font-size:11px;color:var(--muted);">${n.published_at ? new Date(n.published_at).toLocaleTimeString() : ''}</span>
        </div>
        ${hasNiches ? `
        <div style="display:flex;flex-wrap:wrap;gap:5px;margin-top:7px;padding-top:7px;border-top:1px solid var(--border);">
          <span style="font-size:10px;color:var(--muted);font-weight:700;letter-spacing:.05em;align-self:center;">AFFECTS:</span>
          ${nicheBadges(nicheTags)}
        </div>` : ''}
        ${s.llm_reasoning ? `<div style="font-size:11px;color:var(--muted);margin-top:5px;font-style:italic;">🧠 ${s.llm_reasoning}</div>` : ''}
        ${vipBadges(s.vip_persons || []) ? `<div style="margin-top:4px;">${vipBadges(s.vip_persons||[])}</div>` : ''}
      </div>`;
    }).join('') || '<p style="color:var(--muted);text-align:center;padding:30px;">No articles found</p>';
  } catch(e) { el.innerHTML = `<p style="color:var(--red);">Error: ${e.message}</p>`; }
}

// ── Backtest ──────────────────────────────────────────────────────────────────
// ══════════════════════════════════════════════════════════════════════════════
// BACKTEST v7 — Full PnL · Drawdown · Accuracy · Monthly Heatmap · Trade Log
// ══════════════════════════════════════════════════════════════════════════════

let _btCharts = {};
function _destroyBtCharts() {
  Object.values(_btCharts).forEach(c => { try { c.destroy(); } catch(e){} });
  _btCharts = {};
}

const CHART_DEFAULTS = {
  responsive: true, maintainAspectRatio: false, animation: { duration: 600 },
  plugins: { legend: { labels: { color: '#e2e8f0', font: { size: 11 } } } },
  scales: {
    x: { ticks: { color: '#64748b', maxTicksLimit: 10, font:{size:10} }, grid: { color: '#1e2d45' } },
    y: { ticks: { color: '#64748b', font:{size:10} }, grid: { color: '#1e2d45' } }
  }
};

function mkChart(id, cfg) {
  const el = document.getElementById(id);
  if (!el) return null;
  const c = new Chart(el, cfg);
  _btCharts[id] = c;
  return c;
}

async function runBacktest() {
  _destroyBtCharts();
  const el      = document.getElementById('backtest-panel');
  const asset   = document.getElementById('bt-asset')?.value   || 'bitcoin';
  const days    = document.getElementById('bt-days')?.value    || '60';
  const capital = document.getElementById('bt-capital')?.value || '10000';
  const posSz   = (parseFloat(document.getElementById('bt-pos-size')?.value || '10') / 100).toFixed(2);
  const mode    = document.getElementById('bt-mode')?.value    || 'live';

  el.innerHTML = `<div class="loader"><div class="spin"></div>
    <p style="margin-top:10px;color:var(--muted);">
      ${mode==='csv'?'📁 Running CSV dataset backtest':'🔬 Training ML & running walk-forward backtest'}…
    </p></div>`;

  try {
    let d;
    if (mode === 'csv') {
      const fd = new FormData();
      fd.append('asset', asset); fd.append('days', days);
      fd.append('capital', capital); fd.append('pos_size', posSz);
      const csvInput = document.getElementById('csv-upload');
      if (csvInput && csvInput.files.length > 0) fd.append('csv_file', csvInput.files[0]);
      d = await fetch('/api/backtest/csv', { method:'POST', body: fd }).then(r => r.json());
    } else {
      d = await fetch(`/api/backtest?asset=${asset}&days=${days}&capital=${capital}&pos_size=${posSz}`).then(r => r.json());
    }
    if (d.error) {
      el.innerHTML = `<div class="card">
        <p style="color:var(--red);font-size:14px;">⚠️ ${d.error}</p>
        <p style="color:var(--muted);font-size:12px;margin-top:8px;">
          ${mode==='live'?'Keep the app running to accumulate news articles.':'Upload your data.csv above, then re-run.'}
        </p></div>`;
      return;
    }
    renderBacktestResults(d, asset, days, capital);
  } catch(e) {
    el.innerHTML = `<div class="card"><p style="color:var(--red);">Error: ${e.message}</p></div>`;
  }
}

function renderBacktestResults(d, asset, days, capital) {
  _destroyBtCharts();
  const el = document.getElementById('backtest-panel');
  const m  = d.metrics || {};
  const eq = d.equity_curve       || [];
  const pc = d.pnl_curve          || [];
  const dd = d.drawdown_curve     || [];
  const ac = d.accuracy_curve     || [];
  const mo = d.monthly_returns    || [];
  const td = d.trade_distribution || {};
  const sigs   = d.signals || [];
  const trades = d.trades  || [];

  const retClr = (m.total_return_pct||0) >= 0 ? 'up' : 'dn';
  const wrClr  = (m.win_rate_pct||0)    >= 50 ? 'up' : 'dn';
  const pfClr  = (m.profit_factor||0)   >= 1  ? 'up' : 'dn';
  const accClr = (m.accuracy||0)        >= 0.5? 'up' : 'dn';

  el.innerHTML = `
  <div class="grid-4" style="margin-bottom:16px;">
    <div class="metric card"><div class="val ${accClr}">${Math.round((m.accuracy||0)*100)}%</div><div class="lbl">ML Accuracy</div></div>
    <div class="metric card"><div class="val ${retClr}">${(m.total_return_pct||0)>=0?'+':''}${(m.total_return_pct||0).toFixed(2)}%</div><div class="lbl">Total Return</div></div>
    <div class="metric card"><div class="val ${wrClr}">${(m.win_rate_pct||0).toFixed(1)}%</div><div class="lbl">Win Rate</div></div>
    <div class="metric card"><div class="val ${pfClr}">${(m.profit_factor||0).toFixed(2)}x</div><div class="lbl">Profit Factor</div></div>
    <div class="metric card"><div class="val ${(m.sharpe_ratio||0)>=1?'up':(m.sharpe_ratio||0)>=0?'':'dn'}">${(m.sharpe_ratio||0).toFixed(3)}</div><div class="lbl">Sharpe</div></div>
    <div class="metric card"><div class="val ${(m.sortino_ratio||0)>=0?'up':'dn'}">${(m.sortino_ratio||0).toFixed(3)}</div><div class="lbl">Sortino</div></div>
    <div class="metric card"><div class="val ${(m.calmar_ratio||0)>=0?'up':'dn'}">${(m.calmar_ratio||0).toFixed(3)}</div><div class="lbl">Calmar</div></div>
    <div class="metric card"><div class="val dn">-${(m.max_drawdown_pct||0).toFixed(2)}%</div><div class="lbl">Max Drawdown</div></div>
  </div>

  <div class="grid-4" style="margin-bottom:16px;">
    <div class="metric card"><div class="val">$${(m.initial_capital||10000).toLocaleString()}</div><div class="lbl">Initial Capital</div></div>
    <div class="metric card"><div class="val ${(m.final_capital||0)>=(m.initial_capital||10000)?'up':'dn'}">$${(m.final_capital||0).toLocaleString(undefined,{maximumFractionDigits:0})}</div><div class="lbl">Final Capital</div></div>
    <div class="metric card"><div class="val up">$${(m.total_gross_profit||0).toFixed(0)}</div><div class="lbl">Gross Profit</div></div>
    <div class="metric card"><div class="val dn">-$${(m.total_gross_loss||0).toFixed(0)}</div><div class="lbl">Gross Loss</div></div>
    <div class="metric card"><div class="val">${d.total_trades||0}</div><div class="lbl">Total Trades</div></div>
    <div class="metric card"><div class="val up">${m.total_wins||0} W</div><div class="lbl">Wins</div></div>
    <div class="metric card"><div class="val dn">${m.total_losses||0} L</div><div class="lbl">Losses</div></div>
    <div class="metric card"><div class="val">${d.model_trained?'✅ GBM':'⚡ Fallback'} ${d.dataset_blended?'+ 📂':''}</div><div class="lbl">Model</div></div>
  </div>

  <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px;">
    <div class="card"><div class="card-title">📈 Equity Curve</div><div class="chart-wrap"><canvas id="btEquityChart"></canvas></div></div>
    <div class="card"><div class="card-title">💰 Cumulative P&amp;L</div><div class="chart-wrap"><canvas id="btPnlChart"></canvas></div></div>
  </div>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px;">
    <div class="card"><div class="card-title">📉 Drawdown (% from peak)</div><div class="chart-wrap"><canvas id="btDdChart"></canvas></div></div>
    <div class="card"><div class="card-title">🎯 Rolling Accuracy &amp; Win Rate (20-period)</div><div class="chart-wrap"><canvas id="btAccChart"></canvas></div></div>
  </div>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px;">
    <div class="card"><div class="card-title">📅 Monthly P&amp;L</div><div class="chart-wrap"><canvas id="btMonthlyChart"></canvas></div></div>
    <div class="card"><div class="card-title">📊 Trade P&amp;L Distribution</div><div class="chart-wrap"><canvas id="btDistChart"></canvas></div></div>
  </div>

  <div style="display:grid;grid-template-columns:auto 1fr;gap:12px;margin-bottom:12px;">
    <div class="card" style="min-width:260px;">
      <div class="card-title">🔢 Confusion Matrix</div>
      <div id="btConfMatrix"></div>
      <div style="margin-top:10px;font-size:11px;color:var(--muted);">
        <div>Macro F1: <b>${((m.macro_f1||0)*100).toFixed(1)}%</b></div>
        <div>Weighted F1: <b>${((m.weighted_f1||0)*100).toFixed(1)}%</b></div>
        <div>Precision: <b>${((m.precision||0)*100).toFixed(1)}%</b></div>
        <div>Recall: <b>${((m.recall||0)*100).toFixed(1)}%</b></div>
      </div>
    </div>
    <div class="card">
      <div class="card-title">📋 Trade Stats</div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;font-size:12px;">
        <div>Avg Win: <span style="color:var(--green);">+$${(m.avg_win_pnl||0).toFixed(2)}</span></div>
        <div>Avg Loss: <span style="color:var(--red);">-$${Math.abs(m.avg_loss_pnl||0).toFixed(2)}</span></div>
        <div>Best Win: <span style="color:var(--green);">+$${(td.max_win||0).toFixed(2)}</span></div>
        <div>Worst Loss: <span style="color:var(--red);">-$${Math.abs(td.max_loss||0).toFixed(2)}</span></div>
        <div>Buy Signals: <b>${td.buy_count||0}</b></div>
        <div>Sell Signals: <b>${td.sell_count||0}</b></div>
        <div>Best Win Streak: <span style="color:var(--green);"><b>${m.best_win_streak||0}</b></span></div>
        <div>Worst Loss Streak: <span style="color:var(--red);"><b>${m.worst_loss_streak||0}</b></span></div>
      </div>
    </div>
  </div>

  <div class="card" style="margin-bottom:12px;">
    <div class="card-title">📡 Signal Log (last 30)</div>
    <div style="overflow-x:auto;">
    <table class="tbl"><thead><tr>
      <th>Date</th><th>Signal</th><th>Action</th><th>Predicted</th><th>Actual</th>
      <th>Price Δ%</th><th>Trade P&amp;L</th><th>Capital</th><th>Roll Acc</th>
    </tr></thead><tbody>
      ${sigs.slice(-30).reverse().map(s => `<tr>
        <td style="font-size:11px;white-space:nowrap;">${s.date}</td>
        <td>${sigBadge(s.signal,'')}</td>
        <td style="color:${s.action==='BUY'?'var(--green)':s.action==='SELL'?'var(--red)':'var(--muted)'};font-weight:600;">${s.action}</td>
        <td style="color:${s.predicted_dir==='up'?'var(--green)':s.predicted_dir==='down'?'var(--red)':'var(--muted)'};">${s.predicted_dir}</td>
        <td style="color:${s.actual_dir==='up'?'var(--green)':s.actual_dir==='down'?'var(--red)':'var(--muted)'};">${s.actual_dir}</td>
        <td style="color:${(s.price_change_pct||0)>=0?'var(--green)':'var(--red)'};">${((s.price_change_pct||0)>=0?'+':'')+(s.price_change_pct||0).toFixed(2)}%</td>
        <td style="color:${(s.trade_pnl||0)>=0?'var(--green)':'var(--red)'};">${(s.trade_pnl||0)>=0?'+':''}$${(s.trade_pnl||0).toFixed(2)}</td>
        <td>$${(s.capital||0).toLocaleString(undefined,{maximumFractionDigits:0})}</td>
        <td>${((s.rolling_accuracy||0)*100).toFixed(0)}%</td>
      </tr>`).join('')}
    </tbody></table>
    </div>
  </div>

  ${trades.length ? `<div class="card">
    <div class="card-title">🏁 Executed Trades (last 30)</div>
    <div style="overflow-x:auto;"><table class="tbl"><thead><tr>
      <th>Date</th><th>Action</th><th>Price Δ%</th><th>P&amp;L</th><th>Capital After</th><th>Result</th>
    </tr></thead><tbody>
      ${trades.slice(-30).reverse().map(t=>`<tr>
        <td style="font-size:11px;">${t.date}</td>
        <td style="color:${t.action==='BUY'?'var(--green)':'var(--red)'};font-weight:600;">${t.action}</td>
        <td style="color:${(t.pct||0)>=0?'var(--green)':'var(--red)'};">${((t.pct||0)>=0?'+':'')+(t.pct||0).toFixed(2)}%</td>
        <td style="color:${(t.pnl||0)>=0?'var(--green)':'var(--red)'};">${(t.pnl||0)>=0?'+':''}$${(t.pnl||0).toFixed(2)}</td>
        <td>$${(t.capital_after||0).toLocaleString(undefined,{maximumFractionDigits:0})}</td>
        <td style="color:${t.correct?'var(--green)':'var(--red)'};">${t.correct?'✅ WIN':'❌ LOSS'}</td>
      </tr>`).join('')}
    </tbody></table></div>
  </div>` : ''}`;

  // Confusion matrix
  const cmLabels = m.cm_labels || ['up','flat','down'];
  const cmData   = m.confusion_matrix || [];
  if (cmData.length) {
    const cmEl = document.getElementById('btConfMatrix');
    const maxVal = Math.max(...cmData.flat(), 1);
    let html = `<table style="border-collapse:collapse;font-size:11px;margin-top:8px;">
      <thead><tr><th style="padding:4px 8px;color:var(--muted);">Act↓ Pred→</th>
      ${cmLabels.map(l=>`<th style="padding:4px 8px;color:var(--muted);">${l}</th>`).join('')}</tr></thead><tbody>`;
    cmData.forEach((row,ri) => {
      html += `<tr><td style="padding:4px 8px;color:var(--muted);font-weight:600;">${cmLabels[ri]}</td>`;
      row.forEach((val,ci) => {
        const bg = ri===ci?`rgba(0,214,112,${val/maxVal*0.6})`:val>0?`rgba(255,68,68,${val/maxVal*0.4})`:'transparent';
        html += `<td style="padding:6px 12px;text-align:center;background:${bg};border-radius:4px;font-weight:${ri===ci?'700':'400'};">${val}</td>`;
      });
      html += '</tr>';
    });
    html += '</tbody></table>';
    cmEl.innerHTML = html;
  }

  // All 6 charts
  setTimeout(() => {
    if (eq.length) mkChart('btEquityChart', {type:'line', data:{labels:eq.map(e=>e.date),
      datasets:[{label:'Portfolio Value ($)',data:eq.map(e=>e.value),borderColor:'#00d4ff',
        backgroundColor:'rgba(0,212,255,.08)',tension:0.3,pointRadius:1.5,fill:true,borderWidth:2}]},
      options:{...CHART_DEFAULTS,scales:{x:{...CHART_DEFAULTS.scales.x},
        y:{...CHART_DEFAULTS.scales.y,ticks:{...CHART_DEFAULTS.scales.y.ticks,callback:v=>'$'+v.toLocaleString()}}}}});

    if (pc.length) {
      const cols = pc.map(p=>p.pnl>=0?'rgba(0,214,112,0.75)':'rgba(255,68,68,0.75)');
      mkChart('btPnlChart',{type:'bar',data:{labels:pc.map(p=>p.date),
        datasets:[{label:'Cumulative P&L ($)',data:pc.map(p=>p.pnl),backgroundColor:cols,borderColor:cols,borderWidth:1}]},
        options:{...CHART_DEFAULTS,scales:{x:{...CHART_DEFAULTS.scales.x},
          y:{...CHART_DEFAULTS.scales.y,ticks:{...CHART_DEFAULTS.scales.y.ticks,callback:v=>(v>=0?'+':'')+v.toFixed(0)}}}}});
    }

    if (dd.length) mkChart('btDdChart',{type:'line',data:{labels:dd.map(d=>d.date),
      datasets:[{label:'Drawdown (%)',data:dd.map(d=>d.drawdown),borderColor:'#ff4444',
        backgroundColor:'rgba(255,68,68,.15)',tension:0.2,pointRadius:0,fill:true,borderWidth:1.5}]},
      options:{...CHART_DEFAULTS,scales:{x:{...CHART_DEFAULTS.scales.x},
        y:{...CHART_DEFAULTS.scales.y,ticks:{...CHART_DEFAULTS.scales.y.ticks,callback:v=>v.toFixed(1)+'%'}}}}});

    if (ac.length) mkChart('btAccChart',{type:'line',data:{labels:ac.map(a=>a.date),
      datasets:[
        {label:'Accuracy %',data:ac.map(a=>+(a.accuracy*100).toFixed(1)),borderColor:'#00d4ff',tension:0.3,pointRadius:0,fill:false,borderWidth:2},
        {label:'Win Rate %',data:ac.map(a=>a.win_rate!=null?+(a.win_rate*100).toFixed(1):null),borderColor:'#00e676',tension:0.3,pointRadius:0,fill:false,borderWidth:2,borderDash:[4,4]},
      ]},
      options:{...CHART_DEFAULTS,scales:{x:{...CHART_DEFAULTS.scales.x},
        y:{...CHART_DEFAULTS.scales.y,min:0,max:100,ticks:{...CHART_DEFAULTS.scales.y.ticks,callback:v=>v+'%'}}}}});

    if (mo.length) {
      const moBg = mo.map(m=>m.pnl>=0?'rgba(0,214,112,0.75)':'rgba(255,68,68,0.75)');
      mkChart('btMonthlyChart',{type:'bar',data:{labels:mo.map(m=>m.month),
        datasets:[{label:'Monthly P&L ($)',data:mo.map(m=>m.pnl),backgroundColor:moBg,borderColor:moBg,borderWidth:1}]},
        options:{...CHART_DEFAULTS,scales:{x:{...CHART_DEFAULTS.scales.x},
          y:{...CHART_DEFAULTS.scales.y,ticks:{...CHART_DEFAULTS.scales.y.ticks,callback:v=>'$'+v.toFixed(0)}}}}});
    }

    if (td.pnl_histogram && td.pnl_histogram.length) {
      const hBg = td.pnl_histogram.map(b=>parseFloat(b.range)>=0?'rgba(0,214,112,0.75)':'rgba(255,68,68,0.75)');
      mkChart('btDistChart',{type:'bar',data:{labels:td.pnl_histogram.map(b=>b.range),
        datasets:[{label:'Trade Count',data:td.pnl_histogram.map(b=>b.count),backgroundColor:hBg,borderColor:hBg,borderWidth:1}]},
        options:{...CHART_DEFAULTS}});
    }
  }, 150);
}

async function retrainModel() {
  const asset = document.getElementById('bt-asset')?.value || 'bitcoin';
  toast('🧠 Retraining GBM model on latest data...', 'info');
  try {
    const d = await fetch(`/api/ml_retrain?asset=${asset}&days=60`, {method:'POST'}).then(r => r.json());
    if (d.success) {
      toast(`✅ Model ${d.model_trained?'trained':'updated'} — ${d.total_samples} samples · Acc: ${Math.round((d.metrics?.accuracy||0)*100)}%`, 'success');
      const gbmEl = document.getElementById('gbm-status');
      if (gbmEl) gbmEl.textContent = d.model_trained ? 'Trained ✓' : 'Fallback';
    }
  } catch(e) { toast('Error retraining: ' + e.message, 'error'); }
}

// ══════════════════════════════════════════════════════════════════════════════
// DATASET TRAINER — CSV upload + training status polling
// ══════════════════════════════════════════════════════════════════════════════
let _trainingPoller = null;

function handleCsvUpload(input) {
  if (!input.files.length) return;
  const f = input.files[0];
  const nameEl = document.getElementById('csv-filename');
  if (nameEl) nameEl.textContent = `📄 ${f.name} (${(f.size/1024/1024).toFixed(1)} MB)`;
  const statusEl = document.getElementById('dataset-status-panel');
  if (statusEl) statusEl.innerHTML = `<div style="display:flex;gap:10px;align-items:center;margin-top:6px;">
    <button class="btn btn-primary" onclick="uploadAndTrain()" style="font-size:12px;">🚀 Upload & Train Hybrid Model</button>
    <button class="btn btn-ghost" onclick="runBacktest()" style="font-size:12px;">📁 Backtest on CSV (no training)</button>
    <span style="font-size:11px;color:var(--muted);">~100k rows takes 1-3 minutes</span>
  </div>`;
  toast(`📂 ${f.name} selected — click Upload & Train or run backtest directly`, 'info');
}

async function uploadAndTrain() {
  const csvInput = document.getElementById('csv-upload');
  const asset    = document.getElementById('dataset-target-asset')?.value || 'primary';
  const statusEl = document.getElementById('dataset-status-panel');
  if (!csvInput || !csvInput.files.length) { toast('⚠️ Select a CSV file first', 'error'); return; }
  statusEl.innerHTML = `<div style="display:flex;align-items:center;gap:10px;margin-top:8px;">
    <div class="spin" style="width:18px;height:18px;border-width:2px;"></div>
    <span style="color:var(--muted);font-size:12px;">Uploading & starting training…</span>
  </div>`;
  const fd = new FormData();
  fd.append('csv_file', csvInput.files[0]); fd.append('target_asset', asset);
  try {
    const d = await fetch('/api/dataset/upload', {method:'POST', body:fd}).then(r=>r.json());
    if (!d.success) { statusEl.innerHTML = `<p style="color:var(--red);">${d.error}</p>`; return; }
    toast('🧠 Training started! Check progress below…', 'info');
    startTrainingPoller(statusEl);
  } catch(e) { statusEl.innerHTML = `<p style="color:var(--red);">Upload failed: ${e.message}</p>`; }
}

function startTrainingPoller(statusEl) {
  if (_trainingPoller) clearInterval(_trainingPoller);
  _trainingPoller = setInterval(async () => {
    try {
      const d = await fetch('/api/dataset/status').then(r=>r.json());
      const t = d.training || {};
      const pct = t.progress || 0;
      const barColor = t.status==='done'?'#00e676':t.status==='error'?'#ff4444':'#00d4ff';
      statusEl.innerHTML = `<div style="margin-top:8px;">
        <div style="display:flex;justify-content:space-between;font-size:11px;margin-bottom:4px;">
          <span style="color:${barColor};">${t.message||'Training…'}</span>
          <span style="color:var(--muted);">${pct}%</span>
        </div>
        <div style="background:#1e2d45;border-radius:4px;height:6px;overflow:hidden;">
          <div style="background:${barColor};height:100%;width:${pct}%;transition:width 0.4s;border-radius:4px;"></div>
        </div>
        ${t.status==='done'?renderTrainingResult(d.last_model||t.result):''}
        ${t.status==='error'?`<p style="color:var(--red);font-size:11px;margin-top:4px;">❌ ${t.message}</p>`:''}
      </div>`;
      if (t.status==='done'||t.status==='error') {
        clearInterval(_trainingPoller);
        if (t.status==='done') toast('✅ Hybrid ML model trained! Switch to CSV mode and run backtest.','success');
      }
    } catch(e) {}
  }, 2000);
}

function renderTrainingResult(meta) {
  if (!meta) return '';
  const acc = Math.round((meta.accuracy||0)*100);
  const f1  = Math.round((meta.macro_f1||0)*100);
  const ld  = meta.label_distribution || {};
  const topFeats = Object.entries(meta.feature_importances||{}).sort((a,b)=>b[1]-a[1]).slice(0,6);
  const maxFeat  = topFeats.length ? topFeats[0][1] : 1;
  return `<div style="margin-top:10px;padding:10px;background:#0d1b2a;border-radius:6px;font-size:11px;">
    <div style="color:#00e676;font-weight:700;margin-bottom:6px;">✅ Training Complete — Hybrid GBM + RF + LR</div>
    <div style="display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:6px;margin-bottom:8px;">
      <div><div style="font-size:18px;font-weight:700;color:#00d4ff;">${acc}%</div><div style="color:var(--muted);">Accuracy</div></div>
      <div><div style="font-size:18px;font-weight:700;color:#00d4ff;">${f1}%</div><div style="color:var(--muted);">Macro F1</div></div>
      <div><div style="font-size:18px;font-weight:700;">${(meta.train_samples||0).toLocaleString()}</div><div style="color:var(--muted);">Train Rows</div></div>
      <div><div style="font-size:18px;font-weight:700;">${(meta.test_samples||0).toLocaleString()}</div><div style="color:var(--muted);">Test Rows</div></div>
    </div>
    <div style="margin-bottom:8px;color:var(--muted);">
      Bearish: ${ld.bearish||0} · Neutral: ${ld.neutral||0} · Bullish: ${ld.bullish||0}
    </div>
    <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px;">
      ${Object.entries(meta.per_class_acc||{}).map(([k,v])=>`
        <span style="background:#1e2d45;padding:2px 8px;border-radius:12px;color:${v>=0.5?'#00e676':'#ff4444'};">
          ${k}: ${Math.round(v*100)}%
        </span>`).join('')}
    </div>
    ${topFeats.length?`<div style="color:var(--muted);font-size:10px;margin-bottom:4px;">Top Features</div>
    ${topFeats.map(([name,val])=>`<div style="display:flex;align-items:center;gap:6px;margin-bottom:3px;">
      <div style="width:100px;font-size:10px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${name}</div>
      <div style="flex:1;background:#1e2d45;border-radius:2px;height:4px;">
        <div style="background:#00d4ff;height:100%;width:${(val/maxFeat*100).toFixed(0)}%;border-radius:2px;"></div>
      </div>
      <div style="font-size:10px;color:var(--muted);width:36px;">${(val*100).toFixed(1)}%</div>
    </div>`).join('')}`:''}
  </div>`;
}

async function loadDatasetMeta() {
  const statusEl = document.getElementById('dataset-status-panel');
  try {
    const d = await fetch('/api/dataset/meta').then(r=>r.json());
    if (!d.success) {
      statusEl.innerHTML = `<p style="color:var(--muted);font-size:12px;margin-top:6px;">No dataset model trained yet. Upload data.csv to begin.</p>`;
      return;
    }
    statusEl.innerHTML = `<div style="margin-top:8px;">${renderTrainingResult(d)}</div>`;
  } catch(e) { statusEl.innerHTML = `<p style="color:var(--red);font-size:12px;">${e.message}</p>`; }
}


// ── Asset filter population ───────────────────────────────────────────────────
function populateAssetFilters(assets) {
  ['news-asset-filter', 'sig-asset-filter'].forEach(id => {
    const sel = document.getElementById(id);
    if (!sel) return;
    sel.innerHTML = '<option value="all">All Assets</option>' +
      assets.map(k => `<option value="${k}">${k}</option>`).join('');
  });
}

async function loadTickerPrices() {
  try {
    const r = await fetch('/api/prices');
    const d = await r.json();
    if (d.success && d.prices && Object.keys(d.prices).length) renderPrices(d.prices);
  } catch (e) {
    console.warn('[FP] /api/prices:', e);
  }
}

// ── Init ──────────────────────────────────────────────────────────────────────
loadTickerPrices();
loadDashboard();
setInterval(loadDashboard, 90000);

// ═══════════════════════════════════════════════════════════════════════════
// FYP v8 — Enhanced Feature Scripts
// ═══════════════════════════════════════════════════════════════════════════

// ── Tab routing additions ─────────────────────────────────────────────────
const _origTabHandler = document.querySelectorAll('.tab');
document.querySelectorAll('.tab').forEach(t => {
  t.addEventListener('click', () => {
    const tab = t.dataset.tab;
    if (tab === 'live_tracker')  loadLiveTracker();
    if (tab === 'trade_sim')     initTradeSim();
    if (tab === 'data_quality')  loadDataQuality();
    if (tab === 'explainability') loadExplainability();
    if (tab === 'risk_control')  loadRiskControl();
  });
});

// ── Helpers ───────────────────────────────────────────────────────────────
function pct(v, decimals=1){ return (v>=0?'+':'')+v.toFixed(decimals)+'%'; }
function usd(v){ return '$'+v.toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2}); }
function gradeColor(g){ return {A:'#00e676',B:'#69f0ae',C:'#ffd600',D:'#ff4444'}[g]||'#94a3b8'; }
function dirIcon(d){ return d==='bullish'?'🟢':d==='bearish'?'🔴':'⚪'; }

// ─────────────────────────────────────────────────────────────────────────
// 1. LIVE SIGNAL TRACKER
// ─────────────────────────────────────────────────────────────────────────
async function loadLiveTracker() {
  const days  = document.getElementById('lt-days')?.value || 7;
  const asset = document.getElementById('lt-asset')?.value || '';
  const panel = document.getElementById('live-tracker-panel');
  panel.innerHTML = '<div class="loader"><div class="spin"></div></div>';
  try {
    const r = await fetch(`/api/live_tracker?days=${days}${asset?'&asset='+asset:''}`);
    const d = await r.json();
    if (!d.success) throw new Error(d.error);
    renderLiveTracker(d);
  } catch(e) { panel.innerHTML = `<div style="padding:20px;color:var(--red);">Error: ${e.message}</div>`; }
}

function renderLiveTracker(d) {
  const panel = document.getElementById('live-tracker-panel');
  const s = d.summary;
  const accColor = s.accuracy_pct >= 65 ? 'var(--green)' : s.accuracy_pct >= 50 ? 'var(--yellow)' : 'var(--red)';

  let html = `
  <div class="grid-4" style="margin-bottom:16px;">
    <div class="metric"><div class="val" style="color:${accColor};">${s.accuracy_pct}%</div><div class="lbl">Live Accuracy</div></div>
    <div class="metric"><div class="val">${s.total}</div><div class="lbl">Signals Evaluated</div></div>
    <div class="metric"><div class="val" style="color:${s.total_pnl>=0?'var(--green)':'var(--red)'}">${usd(s.total_pnl)}</div><div class="lbl">Total PnL</div></div>
    <div class="metric"><div class="val" style="color:var(--accent);">${s.win_streak}</div><div class="lbl">Current Win Streak</div></div>
  </div>
  <div class="card">
    <div class="card-title">🟢 Real Signal vs Actual Market — Last ${d.days} Days</div>`;

  if (!d.items.length) {
    html += `<div style="padding:30px;text-align:center;color:var(--muted);">
      No evaluated signals yet. Signals are evaluated 24h after generation.<br>
      <small>Run the system for 24+ hours to populate this tracker.</small></div>`;
  } else {
    html += `<div style="overflow-x:auto;"><table class="tbl">
      <thead><tr>
        <th>Asset</th><th>Predicted</th><th>Confidence</th>
        <th>Entry</th><th>Actual</th><th>Movement</th>
        <th>Result</th><th>PnL</th><th>Created</th><th>Evaluated</th>
      </tr></thead><tbody>`;
    d.items.forEach(item => {
      const correct = item.correct;
      const rowStyle = correct ? 'background:rgba(0,230,118,0.04);' : 'background:rgba(255,68,68,0.04);';
      html += `<tr style="${rowStyle}">
        <td><strong>${item.asset_name||item.asset_key}</strong></td>
        <td>${dirIcon(item.predicted)} <span style="text-transform:capitalize;">${item.predicted}</span></td>
        <td><div style="display:flex;align-items:center;gap:6px;">
          <span>${(item.confidence*100).toFixed(0)}%</span>
          <div style="width:50px;height:4px;background:var(--border);border-radius:2px;">
            <div style="width:${(item.confidence*100).toFixed(0)}%;height:100%;background:var(--accent);border-radius:2px;"></div>
          </div></div></td>
        <td>${item.entry_price.toLocaleString()}</td>
        <td>${item.actual_price.toLocaleString()}</td>
        <td style="color:${item.movement_pct>=0?'var(--green)':'var(--red)'};">${pct(item.movement_pct,2)}</td>
        <td><span style="padding:3px 8px;border-radius:4px;font-size:11px;font-weight:700;background:${correct?'rgba(0,230,118,0.15)':'rgba(255,68,68,0.15)'};color:${correct?'var(--green)':'var(--red)'};">${correct?'✅ CORRECT':'❌ WRONG'}</span></td>
        <td style="color:${item.pnl_value>=0?'var(--green)':'var(--red)'};">${usd(item.pnl_value)}</td>
        <td style="color:var(--muted);font-size:11px;">${item.created_at}</td>
        <td style="color:var(--muted);font-size:11px;">${item.evaluated_at}</td>
      </tr>`;
    });
    html += `</tbody></table></div>`;
  }
  html += `</div>`;
  panel.innerHTML = html;
}

// ─────────────────────────────────────────────────────────────────────────
// 2. TRADE SIMULATION ENGINE
// ─────────────────────────────────────────────────────────────────────────
function initTradeSim() { /* just show the panel — no auto-load */ }

async function runTradeSim() {
  const panel = document.getElementById('sim-result-panel');
  panel.innerHTML = '<div class="loader"><div class="spin"></div></div>';
  const payload = {
    asset:       document.getElementById('sim-asset').value,
    asset_class: document.getElementById('sim-class').value,
    direction:   document.getElementById('sim-direction').value,
    capital:     parseFloat(document.getElementById('sim-capital').value)||10000,
    risk_pct:    parseFloat(document.getElementById('sim-risk').value)||2,
    sl_pct:      parseFloat(document.getElementById('sim-sl').value)||1.5,
    tp_ratio:    parseFloat(document.getElementById('sim-tp').value)||2.0,
  };
  try {
    const r = await fetch('/api/trade_sim', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    const d = await r.json();
    if (!d.success) throw new Error(d.error);
    renderTradeSim(d);
  } catch(e) { panel.innerHTML = `<div style="padding:20px;color:var(--red);">Error: ${e.message}</div>`; }
}
// ── Real signal stats from ML engine ──
async function _doFillMiniStats() {
  const setTxt = (id, txt) => { const el = document.getElementById(id); if (el) el.textContent = txt; };
  const setCls = (id, cls) => {
    const el = document.getElementById(id);
    if (el) { el.classList.remove('mm-mini-pos', 'mm-mini-neg'); el.classList.add(cls); }
  };

  // Show loading state on all five cards
  ['mm-win-rate','mm-total-pnl','mm-signals-count','mm-top-conf','mm-best-pair'].forEach(id => setTxt(id, '…'));

  // Use allSettled so a single failed fetch doesn't wipe all cards
  const [btResult, sigResult] = await Promise.allSettled([
    fetch('/api/auto-backtest/summary').then(r => {
      if (!r.ok) throw new Error(`/api/auto-backtest/summary returned HTTP ${r.status}`);
      return r.json();
    }),
    fetch('/api/signals/generated?limit=200').then(r => {
      if (!r.ok) throw new Error(`/api/signals/generated returned HTTP ${r.status}`);
      return r.json();
    }),
  ]);

  const btRes  = btResult.status  === 'fulfilled' ? btResult.value  : null;
  const sigRes = sigResult.status === 'fulfilled' ? sigResult.value : null;

  if (btResult.status  === 'rejected') console.error('[fillMiniStats] auto-backtest/summary failed:', btResult.reason);
  if (sigResult.status === 'rejected') console.error('[fillMiniStats] signals/generated failed:',   sigResult.reason);
  if (btRes  && !btRes.success  && btRes.error)  console.error('[fillMiniStats] Backend error (bt):',  btRes.error);
  if (sigRes && !sigRes.success && sigRes.error) console.error('[fillMiniStats] Backend error (sig):', sigRes.error);

  if (btRes && btRes.success) {
    const evaluated = btRes.total_signals_evaluated || 0;
    const wins = btRes.wins || 0;
    const losses = btRes.losses || 0;

    // ── Signal Win Rate (DB backtests — not live news sentiment) ──
    const winPct = ((btRes.win_ratio || 0) * 100).toFixed(1);
    setTxt('mm-win-rate', winPct + '%');
    if (evaluated === 0 && wins === 0 && losses === 0) {
      setTxt('mm-win-rate-sub', 'No closed evaluations yet · scheduler evaluates ~24h after signals');
      setCls('mm-win-rate-sub', 'mm-mini-neg');
    } else {
      setTxt('mm-win-rate-sub', `${wins}W / ${losses}L evaluated`);
      setCls('mm-win-rate-sub', (btRes.win_ratio || 0) >= 0.5 ? 'mm-mini-pos' : 'mm-mini-neg');
    }

    // ── Signal PnL ──
    const pnl = btRes.total_pnl || 0;
    setTxt('mm-total-pnl', `${pnl >= 0 ? '+$' : '-$'}${Math.abs(pnl).toLocaleString(undefined, {maximumFractionDigits: 2})}`);
    const retPct = (btRes.total_return_pct || 0).toFixed(2);
    if (evaluated === 0) {
      setTxt('mm-pnl-sub', 'Backtest P&L fills after first evaluated signals');
    } else {
      setTxt('mm-pnl-sub', `Return: ${retPct}%`);
    }
    setCls('mm-pnl-sub', pnl >= 0 ? 'mm-mini-pos' : 'mm-mini-neg');

    // ── Signals Generated ──
    setTxt('mm-signals-count', String(btRes.total_signals_generated || 0));
    setTxt('mm-signals-sub', `${evaluated} evaluated · ${btRes.pending_signals || 0} pending`);
  } else {
    ['mm-win-rate','mm-total-pnl','mm-signals-count'].forEach(id => setTxt(id, 'N/A'));
  }

  // ── Top Signal Confidence + Best Performing Pair ──
  if (sigRes && sigRes.success && sigRes.items && sigRes.items.length) {
    const items = sigRes.items;

    // Top confidence card
    const top = [...items].sort((a, b) => (b.confidence_score || 0) - (a.confidence_score || 0))[0];
    const conf = ((top.confidence_score || 0) * 100).toFixed(1);
    setTxt('mm-top-conf', conf + '%');
    const dir = top.predicted_direction || top.signal || '';
    setTxt('mm-top-conf-sub', `${top.asset_name || top.asset_key || '--'} · ${dir}`);
    setCls('mm-top-conf-sub', dir.toLowerCase().includes('bull') ? 'mm-mini-pos' : dir.toLowerCase().includes('bear') ? 'mm-mini-neg' : 'mm-mini-pos');

    // Best performing pair — highest average confidence score
    const pairScores = {};
    const pairCounts = {};
    items.forEach(s => {
      const k = (s.asset_name || s.asset_key || '').toUpperCase();
      if (!k) return;
      pairScores[k] = (pairScores[k] || 0) + (s.confidence_score || 0);
      pairCounts[k] = (pairCounts[k] || 0) + 1;
    });
    const bestPair = Object.keys(pairScores).sort((a, b) => {
      return (pairScores[b] / pairCounts[b]) - (pairScores[a] / pairCounts[a]);
    })[0];
    if (bestPair) {
      const avgConf = ((pairScores[bestPair] / pairCounts[bestPair]) * 100).toFixed(1);
      setTxt('mm-best-pair', bestPair);
      setTxt('mm-best-pair-sub', `Avg confidence: ${avgConf}%`);
      setCls('mm-best-pair-sub', 'mm-mini-pos');
    } else {
      setTxt('mm-best-pair', 'N/A');
    }
  } else {
    setTxt('mm-top-conf', 'N/A');
    setTxt('mm-best-pair', 'N/A');
  }
}

// Run immediately, then retry once after 3 s in case the server is still warming up
(function fillMiniStats() {
  _doFillMiniStats().catch(err => {
    console.warn('[fillMiniStats] First attempt failed:', err, '— retrying in 3 s…');
    setTimeout(() => {
      _doFillMiniStats().catch(e => console.error('[fillMiniStats] Retry also failed:', e));
    }, 3000);
  });
})();
function renderTradeSim(d) {
  const panel = document.getElementById('sim-result-panel');
  const ex = d.execution, ri = d.risk, ou = d.outcomes, mm = d.market_microstructure;
  const dirColor = d.direction === 'bullish' ? 'var(--green)' : 'var(--red)';
  const evColor  = ou.expected_value >= 0 ? 'var(--green)' : 'var(--red)';

  panel.innerHTML = `
  <div style="width:100%;">
    <div class="card-title" style="margin-bottom:12px;">
      ⚡ Simulation: ${d.asset.toUpperCase()} — <span style="color:${dirColor};text-transform:uppercase;">${d.direction}</span>
    </div>

    <div class="grid-3" style="margin-bottom:14px;gap:8px;">
      <div class="level"><div class="lbl entry-lbl">ENTRY PRICE</div><div class="val">${ex.entry_price.toLocaleString('en-US',{maximumFractionDigits:4})}</div></div>
      <div class="level"><div class="lbl sl-lbl">STOP LOSS</div><div class="val" style="color:var(--red);">${ex.sl_price.toLocaleString('en-US',{maximumFractionDigits:4})}</div></div>
      <div class="level"><div class="lbl tp-lbl">TAKE PROFIT</div><div class="val" style="color:var(--green);">${ex.tp_price.toLocaleString('en-US',{maximumFractionDigits:4})}</div></div>
    </div>

    <div style="background:var(--surface2);border-radius:8px;padding:12px;margin-bottom:12px;">
      <div style="font-size:10px;color:var(--muted);font-weight:700;margin-bottom:8px;">📊 POSITION SIZING</div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;font-size:12px;">
        <div>Position Value: <strong>${usd(ex.position_value)}</strong></div>
        <div>Units: <strong>${ex.position_units.toFixed(6)}</strong></div>
        <div>Risk Amount: <strong style="color:var(--red);">${usd(ri.risk_amount)}</strong></div>
        <div>Kelly Fraction: <strong>${(ou.kelly_fraction*100).toFixed(1)}%</strong></div>
        <div>Recommended Size: <strong style="color:var(--accent);">${ou.recommended_size_pct}% of capital</strong></div>
        <div>Win Probability: <strong>${(ou.win_probability*100).toFixed(0)}%</strong></div>
      </div>
    </div>

    <div style="background:var(--surface2);border-radius:8px;padding:12px;margin-bottom:12px;">
      <div style="font-size:10px;color:var(--muted);font-weight:700;margin-bottom:8px;">🏭 MARKET MICROSTRUCTURE</div>
      <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:6px;font-size:11px;">
        <div>Spread: <strong>${mm.spread_pct}%</strong></div>
        <div>Slippage: <strong>${mm.slippage_pct}%</strong></div>
        <div>Latency: <strong>${mm.latency_ms}ms</strong></div>
        <div>Spread Cost: <strong>${usd(mm.spread_cost)}</strong></div>
        <div>Slippage Cost: <strong>${usd(mm.slippage)}</strong></div>
        <div>Total Cost: <strong style="color:var(--red);">${usd(ex.transaction_cost)}</strong></div>
      </div>
    </div>

    <div style="background:var(--surface2);border-radius:8px;padding:12px;">
      <div style="font-size:10px;color:var(--muted);font-weight:700;margin-bottom:8px;">💰 EXPECTED OUTCOMES</div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;font-size:12px;">
        <div>Net Profit (if TP hit): <strong style="color:var(--green);">${usd(ou.net_profit)}</strong></div>
        <div>Net Loss (if SL hit): <strong style="color:var(--red);">${usd(ou.net_loss)}</strong></div>
        <div colspan="2">Expected Value: <strong style="color:${evColor};font-size:14px;">${usd(ou.expected_value)}</strong></div>
      </div>
      <div style="margin-top:8px;padding:8px;background:${ou.expected_value>=0?'rgba(0,230,118,0.1)':'rgba(255,68,68,0.1)'};border-radius:6px;font-size:12px;">
        ${ou.expected_value>=0?'✅ Positive EV trade — statistically favorable':'⚠️ Negative EV — reconsider this trade'}
      </div>
    </div>
  </div>`;
}

// ─────────────────────────────────────────────────────────────────────────
// 3. DATA QUALITY
// ─────────────────────────────────────────────────────────────────────────
async function loadDataQuality() {
  const panel = document.getElementById('data-quality-panel');
  panel.innerHTML = '<div class="loader"><div class="spin"></div></div>';
  try {
    const r = await fetch('/api/data_quality');
    const d = await r.json();
    if (!d.success) throw new Error(d.error);
    renderDataQuality(d);
  } catch(e) { panel.innerHTML = `<div style="padding:20px;color:var(--red);">Error: ${e.message}</div>`; }
}

function renderDataQuality(d) {
  const panel = document.getElementById('data-quality-panel');
  const oqColor = d.overall_quality >= 0.7 ? 'var(--green)' : d.overall_quality >= 0.5 ? 'var(--yellow)' : 'var(--red)';

  let html = `
  <div class="grid-4" style="margin-bottom:16px;">
    <div class="metric"><div class="val" style="color:${oqColor};">${(d.overall_quality*100).toFixed(0)}%</div><div class="lbl">Overall Quality</div></div>
    <div class="metric"><div class="val">${d.total_sources}</div><div class="lbl">Total Sources</div></div>
    <div class="metric"><div class="val" style="color:var(--green);">${d.high_quality_sources}</div><div class="lbl">High Quality (A/B)</div></div>
    <div class="metric"><div class="val" style="color:var(--red);">${d.noisy_sources}</div><div class="lbl">Noisy Sources</div></div>
  </div>
  <div class="card">
    <div class="card-title">🛡️ Source Data Quality Report</div>
    <div style="overflow-x:auto;"><table class="tbl">
      <thead><tr>
        <th>Source</th><th>Grade</th><th>Articles</th><th>Avg Confidence</th>
        <th>Signal Strength</th><th>Freshness</th><th>Noise Score</th><th>Quality Score</th><th>Status</th>
      </tr></thead><tbody>`;

  d.sources.forEach(s => {
    const gc = gradeColor(s.grade);
    const noiseColor = s.noise_score > 0.5 ? 'var(--red)' : s.noise_score > 0.3 ? 'var(--yellow)' : 'var(--green)';
    html += `<tr>
      <td><strong>${s.source}</strong></td>
      <td><span style="padding:3px 10px;border-radius:4px;background:${gc}22;color:${gc};font-weight:800;font-size:13px;">${s.grade}</span></td>
      <td>${s.article_count}</td>
      <td>
        <div style="display:flex;align-items:center;gap:6px;">
          <span>${(s.avg_confidence*100).toFixed(0)}%</span>
          <div style="width:50px;height:4px;background:var(--border);border-radius:2px;">
            <div style="width:${(s.avg_confidence*100).toFixed(0)}%;height:100%;background:var(--accent);border-radius:2px;"></div>
          </div>
        </div>
      </td>
      <td>${(s.avg_signal_strength*100).toFixed(1)}%</td>
      <td>${(s.freshness_score*100).toFixed(0)}%</td>
      <td style="color:${noiseColor};">${(s.noise_score*100).toFixed(0)}%</td>
      <td>
        <div style="display:flex;align-items:center;gap:6px;">
          <span>${(s.quality_score*100).toFixed(0)}%</span>
          <div style="width:60px;height:4px;background:var(--border);border-radius:2px;">
            <div style="width:${(s.quality_score*100).toFixed(0)}%;height:100%;background:${gradeColor(s.grade)};border-radius:2px;"></div>
          </div>
        </div>
      </td>
      <td>${s.recommended?'<span style="color:var(--green);">✅ Use</span>':'<span style="color:var(--red);">⚠️ Caution</span>'}</td>
    </tr>`;
  });
  html += `</tbody></table></div></div>`;
  panel.innerHTML = html;
}

// ─────────────────────────────────────────────────────────────────────────
// 4. AI EXPLAINABILITY (SHAP-style)
// ─────────────────────────────────────────────────────────────────────────
async function loadExplainability() {
  const asset = document.getElementById('explain-asset')?.value || 'bitcoin';
  const panel = document.getElementById('explain-panel');
  panel.innerHTML = '<div class="loader"><div class="spin"></div></div>';
  try {
    const r = await fetch(`/api/explain_signal?asset=${asset}`);
    const d = await r.json();
    if (!d.success) throw new Error(d.error);
    renderExplainability(d);
  } catch(e) { panel.innerHTML = `<div style="padding:20px;color:var(--red);">Error: ${e.message}</div>`; }
}

function renderExplainability(d) {
  const panel = document.getElementById('explain-panel');
  const sigColor = d.composite_score > 0 ? 'var(--green)' : d.composite_score < 0 ? 'var(--red)' : 'var(--muted)';

  let html = `
  <div class="grid-2" style="gap:16px;margin-bottom:16px;">
    <div class="card">
      <div class="card-title">🧬 Signal Decision</div>
      <div style="text-align:center;padding:20px 0;">
        <div style="font-size:36px;font-weight:900;color:${sigColor};">${d.signal}</div>
        <div style="font-size:16px;color:var(--muted);margin-top:4px;">${d.action}</div>
        <div style="margin-top:12px;display:flex;justify-content:center;gap:16px;">
          <div><div style="font-size:20px;font-weight:700;color:var(--accent);">${(d.confidence*100).toFixed(0)}%</div><div style="font-size:10px;color:var(--muted);">CONFIDENCE</div></div>
          <div><div style="font-size:20px;font-weight:700;color:var(--green);">${(d.win_probability*100).toFixed(0)}%</div><div style="font-size:10px;color:var(--muted);">WIN PROB</div></div>
          <div><div style="font-size:20px;font-weight:700;color:var(--yellow);">${d.composite_score.toFixed(3)}</div><div style="font-size:10px;color:var(--muted);">COMPOSITE</div></div>
        </div>
      </div>
    </div>
    <div class="card">
      <div class="card-title">🔗 Reasoning Chain</div>
      <ul style="list-style:none;padding:0;">
        ${d.reasoning_chain.map(r=>`<li style="padding:6px 0;border-bottom:1px solid var(--border);font-size:12px;">${r}</li>`).join('')}
      </ul>
    </div>
  </div>

  <div class="card" style="margin-bottom:16px;">
    <div class="card-title">📊 SHAP-Style Feature Attribution <span style="font-size:10px;color:var(--muted);font-weight:400;">(% contribution to signal decision)</span></div>
    <div style="display:flex;flex-direction:column;gap:10px;margin-top:6px;">`;

  d.feature_attribution.forEach(f => {
    const barColor = f.direction === 'positive' ? 'var(--green)' : 'var(--red)';
    const valColor = f.direction === 'positive' ? 'var(--green)' : 'var(--red)';
    html += `
      <div>
        <div style="display:flex;justify-content:space-between;margin-bottom:4px;font-size:12px;">
          <span>${f.feature}</span>
          <span style="display:flex;gap:12px;">
            <span style="color:${valColor};">${f.raw_value >= 0 ? '+' : ''}${f.raw_value.toFixed(4)}</span>
            <span style="color:var(--accent);font-weight:700;">${f.contribution}%</span>
          </span>
        </div>
        <div style="height:8px;background:var(--border);border-radius:4px;overflow:hidden;">
          <div style="width:${f.contribution}%;height:100%;background:${barColor};border-radius:4px;transition:width 0.6s ease;"></div>
        </div>
      </div>`;
  });
  html += `</div></div>`;

  if (d.llm_reasoning) {
    html += `<div class="card" style="margin-bottom:16px;">
      <div class="card-title">🤖 LLM Reasoning</div>
      <div style="font-size:12px;line-height:1.6;color:var(--text);">${d.llm_reasoning}</div>
    </div>`;
  }

  if (d.top_headlines && d.top_headlines.length) {
    html += `<div class="card">
      <div class="card-title">📰 Top Supporting Headlines</div>`;
    d.top_headlines.forEach(h => {
      const hColor = h.score > 0 ? 'var(--green)' : h.score < 0 ? 'var(--red)' : 'var(--muted)';
      html += `<div style="padding:8px 0;border-bottom:1px solid var(--border);">
        <div style="font-size:12px;font-weight:500;">${h.title}</div>
        <div style="display:flex;gap:10px;margin-top:4px;font-size:11px;color:var(--muted);">
          <span class="src">${h.source}</span>
          <span style="color:${hColor};">Score: ${h.score >= 0 ? '+' : ''}${h.score.toFixed(3)}</span>
          <span style="text-transform:capitalize;">${h.label}</span>
        </div>
      </div>`;
    });
    html += `</div>`;
  }

  panel.innerHTML = html;
}

// ─────────────────────────────────────────────────────────────────────────
// 5. RISK CONTROL SYSTEM
// ─────────────────────────────────────────────────────────────────────────
async function loadRiskControl() {
  const panel = document.getElementById('risk-control-panel');
  panel.innerHTML = '<div class="loader"><div class="spin"></div></div>';
  const capital  = document.getElementById('rc-capital')?.value  || 10000;
  const maxRisk  = document.getElementById('rc-max-risk')?.value || 2;
  const maxDd    = document.getElementById('rc-max-dd')?.value   || 15;
  try {
    const r = await fetch(`/api/risk_control?capital=${capital}&max_risk_pct=${maxRisk}&max_dd_pct=${maxDd}`);
    const d = await r.json();
    if (!d.success) throw new Error(d.error);
    renderRiskControl(d);
  } catch(e) { panel.innerHTML = `<div style="padding:20px;color:var(--red);">Error: ${e.message}</div>`; }
}

function renderRiskControl(d) {
  const panel = document.getElementById('risk-control-panel');
  const ps = d.portfolio_summary;
  const utilColor = ps.utilization_pct > 90 ? 'var(--red)' : ps.utilization_pct > 70 ? 'var(--yellow)' : 'var(--green)';
  const riskColor = ps.portfolio_risk_pct > ps.max_allowed_dd_pct * 0.8 ? 'var(--red)' : 'var(--green)';

  let html = `
  <div class="grid-4" style="margin-bottom:16px;">
    <div class="metric"><div class="val">${usd(ps.total_capital)}</div><div class="lbl">Total Capital</div></div>
    <div class="metric"><div class="val" style="color:${utilColor};">${ps.utilization_pct.toFixed(1)}%</div><div class="lbl">Capital Utilization</div></div>
    <div class="metric"><div class="val" style="color:${riskColor};">${ps.portfolio_risk_pct.toFixed(2)}%</div><div class="lbl">Portfolio Risk</div></div>
    <div class="metric"><div class="val" style="color:var(--accent);">${ps.diversification_score.toFixed(2)}</div><div class="lbl">Diversification</div></div>
  </div>

  <div class="grid-2" style="gap:16px;margin-bottom:16px;">
    <div class="card">
      <div class="card-title">⚠️ Risk Warnings</div>
      ${d.risk_warnings.map(w=>`<div style="padding:8px 10px;margin-bottom:6px;border-radius:6px;font-size:12px;background:${w.startsWith('✅')?'rgba(0,230,118,0.08)':'rgba(255,214,0,0.08)'};border:1px solid ${w.startsWith('✅')?'rgba(0,230,118,0.2)':'rgba(255,214,0,0.2)'};">${w}</div>`).join('')}
    </div>
    <div class="card">
      <div class="card-title">💵 Capital Summary</div>
      <div style="display:flex;flex-direction:column;gap:8px;font-size:12px;">
        <div style="display:flex;justify-content:space-between;"><span>Deployed Capital</span><strong>${usd(ps.total_allocated)}</strong></div>
        <div style="display:flex;justify-content:space-between;"><span>Cash Reserve</span><strong style="color:var(--green);">${usd(ps.cash_reserve)}</strong></div>
        <div style="display:flex;justify-content:space-between;"><span>Risk Budget Used</span><strong style="color:${ps.risk_budget_used_pct>80?'var(--red)':'var(--yellow)'};">${ps.risk_budget_used_pct.toFixed(1)}%</strong></div>
        <div style="height:6px;background:var(--border);border-radius:3px;overflow:hidden;margin-top:4px;">
          <div style="width:${Math.min(ps.utilization_pct,100).toFixed(0)}%;height:100%;background:${utilColor};border-radius:3px;"></div>
        </div>
      </div>
    </div>
  </div>

  <div class="card">
    <div class="card-title">⚖️ Position Allocation & Risk Parameters</div>
    <div style="overflow-x:auto;"><table class="tbl">
      <thead><tr>
        <th>Asset</th><th>Class</th><th>Signal</th><th>Allocation</th>
        <th>Position Value</th><th>Stop Loss</th><th>Take Profit</th>
        <th>Risk/Trade</th><th>Max Loss</th><th>Kelly %</th>
      </tr></thead><tbody>`;

  d.allocation.forEach(a => {
    const sigColor = a.signal.includes('BUY') || a.signal === 'STRONG BUY' ? 'var(--green)' :
                     a.signal.includes('SELL') ? 'var(--red)' : 'var(--muted)';
    html += `<tr>
      <td><strong>${a.asset_name}</strong></td>
      <td><span style="font-size:10px;padding:2px 6px;background:rgba(124,58,237,0.15);color:#a78bfa;border-radius:4px;">${a.asset_class}</span></td>
      <td><span style="color:${sigColor};font-weight:700;">${a.signal}</span></td>
      <td>
        <div style="display:flex;align-items:center;gap:6px;">
          <span>${a.allocation_pct.toFixed(1)}%</span>
          <div style="width:50px;height:4px;background:var(--border);border-radius:2px;">
            <div style="width:${Math.min(a.allocation_pct*5,100).toFixed(0)}%;height:100%;background:var(--accent2);border-radius:2px;"></div>
          </div>
        </div>
      </td>
      <td>${usd(a.position_value)}</td>
      <td style="color:var(--red);">${a.stop_loss.toLocaleString('en-US',{maximumFractionDigits:4})} <span style="font-size:10px;color:var(--muted);">(${a.dynamic_sl_pct}%)</span></td>
      <td style="color:var(--green);">${a.take_profit.toLocaleString('en-US',{maximumFractionDigits:4})}</td>
      <td style="color:var(--yellow);">${usd(a.risk_per_trade)}</td>
      <td style="color:var(--red);">${usd(a.max_loss)}</td>
      <td style="color:var(--accent);">${(a.kelly_fraction*100).toFixed(1)}%</td>
    </tr>`;
  });
  html += `</tbody></table></div></div>`;
  panel.innerHTML = html;
}

// Auto-load live tracker when navigating to it
document.querySelectorAll('.tab').forEach(t => {
  t.addEventListener('click', () => {
    if (t.dataset.tab === 'live_tracker') setTimeout(loadLiveTracker, 100);
    if (t.dataset.tab === 'data_quality') setTimeout(loadDataQuality, 100);
    if (t.dataset.tab === 'explainability') setTimeout(loadExplainability, 100);
    if (t.dataset.tab === 'risk_control') setTimeout(loadRiskControl, 100);
  });
});