/* Lore2 UI — the dark "system" chrome + parchment reader, ported from the lore2
 * redesign into our stack (vanilla JS, no build step), wired to the real Lore
 * JSON API. The design file (lore2/Lore.html) is the visual source of truth; its
 * dc-runtime/React implementation is NOT reused. Inline event handlers are
 * forbidden by the app CSP (script-src 'self'), so all interaction goes through
 * event delegation keyed on data-act / data-id attributes.
 */
(() => {
  'use strict';

  // ---------- visual constants (from the redesign) ----------
  const KINDS = {
    project:   { label: 'Projects',   color: '#7ed98e' },
    service:   { label: 'Services',   color: '#6ea8fb' },
    decision:  { label: 'Decisions',  color: '#b58cf0' },
    runbook:   { label: 'Runbooks',   color: '#f0945e' },
    procedure: { label: 'Procedures', color: '#f5d76e' },
    concept:   { label: 'Concepts',   color: '#5ec8c2' },
    page:      { label: 'Pages',      color: '#9aa0aa' },
  };
  const TYPES = {
    page:        { label: 'Page',         color: '#ff8fbe' },
    capture:     { label: 'Capture',      color: '#8b93a7' },
    claim:       { label: 'Claim',        color: '#5ec8c2' },
    invalidation:{ label: 'Invalidation', color: '#f87171' },
    entity:      { label: 'Entity',       color: '#e0b252' },
    plan:        { label: 'Plan',         color: '#b58cf0' },
    trace:       { label: 'Trace',        color: '#6ea8fb' },
    actor:       { label: 'Actor',        color: '#7ed98e' },
    task:        { label: 'Task',         color: '#f0945e' },
    policy:      { label: 'Policy',       color: '#f5d76e' },
    source:      { label: 'Source',       color: '#9aa0aa' },
    tool:        { label: 'Tool',         color: '#c9a0f0' },
  };
  const EDGE_LABEL = {
    mentions: 'links to', supports: 'supports', contradicts: 'contradicts', generated: 'generated',
    applied: 'applied', 'used-policy': 'uses policy', 'source-of': 'source of', 'task-related': 'task',
    authored: 'authored by', 'used-tool': 'uses tool', provenance: 'provenance', supersedes: 'supersedes',
    parent: 'parent of',
  };
  const PROVIDERS = [
    ['openai', 'OpenAI'], ['anthropic', 'Anthropic'], ['ollama', 'Ollama'], ['azure-openai', 'Azure OpenAI'],
    ['openrouter', 'OpenRouter'], ['google', 'Google'], ['mistral', 'Mistral'], ['groq', 'Groq'], ['none', 'None'],
  ];
  // Graph scale guards. The server caps the payload to the highest-degree nodes
  // (stats.truncated reports it); the client mirrors that cap on the fetch and
  // limits the O(n^2) force repulsion to the top-degree nodes so a large vault
  // can't freeze the tab — remaining nodes still get edge-spring + drift forces.
  // GRAPH_NODE_LIMIT must stay in sync with DEFAULT_CONTEXT_GRAPH_NODES in
  // lore_app/context_graph.py (the server's default cap for the same endpoint).
  const GRAPH_NODE_LIMIT = 1500;
  const GRAPH_SIM_CAP = 700;

  // ---------- small helpers ----------
  const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ESC[c]);
  const enc = (id) => String(id).split('/').map(encodeURIComponent).join('/');
  const kc = (kind) => KINDS[kind] || KINDS.page;
  const tc = (type) => TYPES[type] || { label: type, color: '#9aa0aa' };
  const chipBg = (c) => `color-mix(in srgb,${c} 16%,transparent)`;
  const chipBd = (c) => `color-mix(in srgb,${c} 34%,transparent)`;
  function titleOf(id) {
    const seg = String(id).split('/').pop().replace(/-/g, ' ');
    return seg.replace(/\b\w/g, (ch) => ch.toUpperCase());
  }
  function ago(iso) {
    if (!iso) return '—';
    const d = (Date.now() - new Date(iso).getTime()) / 86400000;
    if (isNaN(d)) return '—';
    if (d < 1) return 'today'; if (d < 2) return 'yesterday';
    if (d < 14) return Math.floor(d) + 'd ago';
    if (d < 60) return Math.floor(d / 7) + 'w ago';
    return Math.floor(d / 30) + 'mo ago';
  }
  function fmtDate(iso) {
    try { return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }); }
    catch (e) { return iso; }
  }
  function maskSecret(s) { s = String(s || ''); if (!s) return ''; return s.length <= 8 ? '****' : '****' + s.slice(-4); }
  const fmt = (v) => Array.isArray(v) ? v.join(', ') : String(v);

  // ============================================================
  class LoreApp {
    constructor(root) {
      this.root = root;
      this.s = {
        pages: [], byId: {}, detailCache: {},
        gnodes: [], gedges: [], gmap: {}, deg: {},
        query: '', listView: 'recent', openId: null,
        labelMode: 'smart', readerFont: 'serif',
        settingsOpen: false, openSelect: null,
        // settings/model
        llmSaved: null, llm: null, llmToast: '',
        editApiKey: false, apiKeyInput: '', editEscKey: false, escKeyInput: '',
        // settings/keys
        apiKeys: [], newKey: { name: '', description: '', role: 'writer' }, showKeyGen: false,
        createdToken: null, copyLabel: 'Copy', confirmRevoke: null, sessionKey: '',
      };
      this.reader = null;             // assembled reader view-model (page) or node VM
      this.extHover = null;           // page id hovered in reader/sidebar -> graph highlight
      // Legend type toggles. The human knowledge map defaults to curated Pages
      // only — the internal memory nodes (captures, claims, entities, traces,
      // plans, sources, tools, policies, …) are what made the map unreadable.
      // They stay in the data and one legend click away, but the default human
      // surface is the clean page graph. loadPrefs() may override from storage.
      this.typeOff = {};
      Object.keys(TYPES).forEach((t) => { if (t !== 'page') this.typeOff[t] = true; });
      this._openToken = 0;            // guards async reader fills against rapid re-opens
      // The settings form must be non-null before the first render: openSettings()
      // calls renderSettings() synchronously (counter()/dropdowns read this.s.llm)
      // before its network load resolves. Seed it with env defaults.
      this.s.llm = this.llmFormFrom(this.envLlm());
      this.loadPrefs();
      this.buildShell();
      this.bindEvents();
      this.loadAll();
    }

    // ---------- prefs ----------
    loadPrefs() {
      try {
        const p = JSON.parse(localStorage.getItem('lore.settings') || '{}');
        if (p.labelMode) this.s.labelMode = p.labelMode;
        if (p.readerFont) this.s.readerFont = p.readerFont;
        if (p.typeOff && typeof p.typeOff === 'object') this.typeOff = p.typeOff;
      } catch (e) {}
      this.root.setAttribute('data-font', this.s.readerFont);
    }
    savePref(patch) {
      try {
        const cur = JSON.parse(localStorage.getItem('lore.settings') || '{}');
        Object.assign(cur, patch); localStorage.setItem('lore.settings', JSON.stringify(cur));
      } catch (e) {}
    }

    // ---------- data ----------
    async fetchJSON(url, opts) {
      const r = await fetch(url, Object.assign({ headers: { Accept: 'application/json' } }, opts || {}));
      if (!r.ok) { const e = new Error(url + ' ' + r.status); e.status = r.status; throw e; }
      if (r.status === 204) return null;
      return r.json();
    }
    async fetchDetail(id) {
      if (this.s.detailCache[id]) return this.s.detailCache[id];
      const d = await this.fetchJSON('/api/pages/' + enc(id));
      this.s.detailCache[id] = d;
      return d;
    }
    async loadAll() {
      // 1) page list -> sidebar (fast first paint)
      try {
        const pages = await this.fetchJSON('/api/pages?limit=200');
        this.s.pages = pages;
        this.s.byId = {};
        pages.forEach((p) => { this.s.byId[p.id] = p; });
      } catch (e) { console.error('pages load failed', e); }
      this.renderList();
      // 2) typed context graph -> canvas
      try {
        const g = await this.fetchJSON('/api/context-graph?limit=' + GRAPH_NODE_LIMIT);
        this.buildGraph(g);
        this.startGraph();
        this.renderLegend();
        this.updateGraphHeader();
      } catch (e) { console.error('graph load failed', e); }
      // 3) details in the background -> flags/warnings (guarded for large vaults)
      if (this.s.pages.length && this.s.pages.length <= 300) {
        Promise.allSettled(this.s.pages.map((p) => this.fetchDetail(p.id))).then(() => {
          this.renderList();
          if (this.s.openId && this.reader && this.reader.kind === 'page') this.renderReader();
        });
      }
      // 4) deep-link open
      this.openFromPath();
    }

    // ---------- shell ----------
    buildShell() {
      this.root.innerHTML = `
        <aside class="lore-scroll" style="display:flex;flex-direction:column;min-width:0;min-height:0;border-right:1px solid var(--separator);background:var(--bg-surface)">
          <div style="padding:18px 16px 13px;border-bottom:1px solid var(--separator)">
            <div style="display:flex;align-items:center;gap:10px">
              <button data-act="home" title="Home" style="display:flex;align-items:center;gap:9px;background:none;border:none;padding:0;cursor:pointer;min-width:0">
                <span style="width:9px;height:9px;border-radius:50%;flex-shrink:0;background:var(--rose);box-shadow:0 0 9px 1px var(--rose-glow);animation:navPulse 2s ease-in-out infinite"></span>
                <span style="font-family:'IBM Plex Mono',monospace;font-size:14px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:var(--text-heading)">Lore</span>
              </button>
              <button data-act="settings-open" title="Settings" class="l2-icon-btn" style="margin-left:auto;flex-shrink:0;display:inline-flex;align-items:center;justify-content:center;width:32px;height:32px;border-radius:var(--radius-md);border:1px solid var(--separator);background:var(--bg-surface);color:var(--text-muted);cursor:pointer;transition:all .14s">
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"></circle><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"></path></svg>
              </button>
            </div>
            <div style="display:flex;align-items:center;gap:7px;margin-top:9px">
              <span style="font-family:'IBM Plex Mono',monospace;font-size:9.5px;letter-spacing:.14em;text-transform:uppercase;color:var(--text-dim)">Axis Memory</span>
              <span style="width:2px;height:2px;border-radius:50%;flex-shrink:0;background:var(--text-dimmer)"></span>
              <span id="l2-pagecount" style="font-family:'IBM Plex Mono',monospace;font-size:9.5px;letter-spacing:.04em;color:var(--text-dimmer)"></span>
            </div>
            <div class="lore-search" style="display:flex;align-items:center;height:36px;margin-top:14px;background:var(--bg-surface);border:1px solid var(--separator);border-radius:12px;padding:8px 12px;gap:8px">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--text-dim)" stroke-width="2" style="flex-shrink:0"><circle cx="11" cy="11" r="7"></circle><path d="m21 21-4.3-4.3"></path></svg>
              <input id="l2-search" data-act="search" value="" placeholder="Search…" spellcheck="false" style="flex:1;min-width:0;background:none;border:none;outline:none;color:var(--text-body);font-family:inherit;font-size:13px">
            </div>
          </div>
          <div id="l2-list" class="lore-scroll" style="flex:1;min-height:0;overflow-y:auto;padding:8px 10px 22px"></div>
        </aside>
        <div class="stage" style="position:relative;min-width:0;min-height:0;display:flex;overflow:hidden">
          <div class="graphpane" id="l2-graphpane" style="position:relative;flex:1 1 0;min-width:0;min-height:0;overflow:hidden;background:radial-gradient(circle at 50% 38%,rgba(255,143,190,.07),transparent 60%),#000">
            <canvas id="loreGraphCanvas"></canvas>
            <div style="position:absolute;top:14px;left:18px;display:flex;align-items:center;gap:9px;pointer-events:none;z-index:2">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="var(--rose)" stroke-width="2"><circle cx="5" cy="6" r="2.4"></circle><circle cx="19" cy="7" r="2.4"></circle><circle cx="12" cy="18" r="2.4"></circle><path d="M7 7.2 10.4 16M16.8 8.4 13.6 16"></path></svg>
              <span style="display:flex;flex-direction:column;line-height:1.2">
                <span id="l2-maptitle" style="font-family:'IBM Plex Mono',monospace;font-size:10px;font-weight:600;letter-spacing:.12em;text-transform:uppercase;color:var(--text-muted)">Knowledge map</span>
                <span id="l2-mapsub" style="font-family:'IBM Plex Mono',monospace;font-size:9px;letter-spacing:.05em;color:var(--text-dimmer)"></span>
              </span>
            </div>
            <div style="position:absolute;top:13px;right:16px;display:flex;gap:7px;align-items:center;z-index:2">
              <button data-act="cycle-labels" class="l2-glass-btn" title="Cycle label density — Smart / All / Off" style="display:inline-flex;align-items:center;gap:7px;height:32px;padding:0 12px;border-radius:var(--radius-md);border:1px solid var(--separator);background:rgba(29,29,31,.82);backdrop-filter:blur(16px);color:var(--text-muted);cursor:pointer;font-family:'IBM Plex Mono',monospace;font-size:9.5px;font-weight:600;letter-spacing:.05em;text-transform:uppercase;transition:all .14s">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7h16M4 12h16M4 17h10"></path></svg>
                <span id="l2-labeltext">Labels: Smart</span>
              </button>
              <button data-act="reset-view" class="l2-glass-btn" title="Fit the view" style="display:inline-flex;align-items:center;gap:6px;height:32px;padding:0 12px;border-radius:var(--radius-md);border:1px solid var(--separator);background:rgba(29,29,31,.82);backdrop-filter:blur(16px);color:var(--text-muted);cursor:pointer;font-family:'IBM Plex Mono',monospace;font-size:9.5px;font-weight:600;letter-spacing:.05em;text-transform:uppercase;transition:all .14s">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 9V5a1 1 0 0 1 1-1h4M20 9V5a1 1 0 0 0-1-1h-4M4 15v4a1 1 0 0 0 1 1h4M20 15v4a1 1 0 0 1-1 1h-4"></path></svg>
                Fit
              </button>
            </div>
            <div id="l2-legend" style="position:absolute;left:0;right:0;bottom:0;display:flex;flex-wrap:wrap;align-items:center;gap:5px 7px;padding:30px 16px 12px;background:linear-gradient(transparent,rgba(7,7,8,.94) 58%)"></div>
          </div>
          <div id="l2-reader" style="display:none;min-width:0"></div>
        </div>
        <div id="l2-modal" style="display:contents"></div>`;
      this.cv = this.root.querySelector('#loreGraphCanvas');
      this.cv.style.display = 'block';
      this.cv.style.position = 'absolute';
      this.cv.style.top = '0';
      this.cv.style.left = '0';
    }

    // ---------- events ----------
    bindEvents() {
      this.root.addEventListener('click', (e) => this.onClick(e));
      this.root.addEventListener('input', (e) => this.onInput(e));
      this.root.addEventListener('mouseover', (e) => this.onHover(e, true));
      this.root.addEventListener('mouseout', (e) => this.onHover(e, false));
      document.addEventListener('keydown', (e) => this.onKey(e));
      window.addEventListener('popstate', () => this.openFromPath());
      const pane = this.root.querySelector('#l2-graphpane');
      if (window.ResizeObserver) {
        this._ro = new ResizeObserver(() => this.sizeCanvas());
        this._ro.observe(pane);
      }
      window.addEventListener('resize', () => this.sizeCanvas());
    }
    data(el, name) { return el && el.getAttribute('data-' + name); }
    onClick(e) {
      // in-reader article links
      const a = e.target.closest && e.target.closest('a');
      if (a && this.root.querySelector('#l2-reader').contains(a)) {
        if (a.classList.contains('wl')) {
          e.preventDefault();
          if (a.classList.contains('broken')) return;
          const t = a.getAttribute('data-page'); if (t) this.open(t);
          return;
        }
        if (a.classList.contains('heading-anchor')) { e.preventDefault(); this.scrollHeading(a.getAttribute('href').slice(1)); return; }
        if (a.classList.contains('ext')) return; // open in new tab
      }
      const el = e.target.closest && e.target.closest('[data-act]');
      if (!el) return;
      const act = this.data(el, 'act');
      const id = this.data(el, 'id');
      switch (act) {
        case 'home': this.goHome(); break;
        case 'open': if (id) this.open(id); break;
        case 'settings-open': this.openSettings(); break;
        case 'settings-close': this.closeSettings(); break;
        case 'panel-stop': e.stopPropagation(); if (this.s.openSelect) { this.s.openSelect = null; this.renderSettings(); } break;
        case 'reader-close': this.closeReader(); break;
        case 'reader-focus': this.focusInMap(); break;
        case 'view-recent': this.s.listView = 'recent'; this.renderList(); break;
        case 'view-flagged': this.s.listView = 'flagged'; this.renderList(); break;
        case 'cycle-labels': this.cycleLabels(); break;
        case 'reset-view': this.resetView(); break;
        case 'legend-toggle': this.toggleType(this.data(el, 'type')); break;
        // settings
        case 'sec': this.scrollToSection(this.data(el, 'sec')); break;
        case 'llm-save': this.saveLlm(); break;
        case 'llm-reset': this.resetLlm(); break;
        case 'api-edit': this.s.editApiKey = true; this.s.apiKeyInput = ''; this.renderSettings(); break;
        case 'api-cancel': this.s.editApiKey = false; this.s.apiKeyInput = ''; this.renderSettings(); break;
        case 'esc-edit': this.s.editEscKey = true; this.s.escKeyInput = ''; this.renderSettings(); break;
        case 'esc-cancel': this.s.editEscKey = false; this.s.escKeyInput = ''; this.renderSettings(); break;
        case 'keygen-toggle': this.s.showKeyGen = !this.s.showKeyGen; this.renderSettings(); break;
        case 'key-create': this.createKey(); break;
        case 'key-ask': this.s.confirmRevoke = id; this.renderSettings(); break;
        case 'key-cancel': this.s.confirmRevoke = null; this.renderSettings(); break;
        case 'key-confirm': this.revokeKey(id); break;
        case 'token-copy': this.copyToken(); break;
        case 'token-dismiss': this.s.createdToken = null; this.s.copyLabel = 'Copy'; this.renderSettings(); break;
        case 'session-login': this.sessionLogin(); break;
        case 'bump': this.bumpLlm(this.data(el, 'field'), parseFloat(this.data(el, 'delta')), this.data(el, 'dp')); break;
        case 'select-toggle': { const sel = this.data(el, 'sel'); e.stopPropagation(); this.s.openSelect = this.s.openSelect === sel ? null : sel; this.renderSettings(); break; }
        case 'select-pick': this.selectPick(this.data(el, 'sel'), this.data(el, 'val')); break;
        default: break;
      }
    }
    onInput(e) {
      const el = e.target.closest && e.target.closest('[data-act]');
      if (!el) return;
      const act = this.data(el, 'act');
      const v = el.value;
      switch (act) {
        case 'search': this.s.query = v; this.renderList(); break;
        case 'llm-field': this.s.llm[this.data(el, 'field')] = v; this.s.llmToast = ''; this.refreshSettingsActions(); break;
        case 'api-key-input': this.s.apiKeyInput = v; this.s.llmToast = ''; break;
        case 'esc-key-input': this.s.escKeyInput = v; this.s.llmToast = ''; break;
        case 'newkey-name': this.s.newKey.name = v; this.refreshKeygenActions(); break;
        case 'newkey-desc': this.s.newKey.description = v; break;
        case 'session-key': this.s.sessionKey = v; break;
        default: break;
      }
    }
    onHover(e, on) {
      const row = e.target.closest && e.target.closest('[data-hover-id]');
      if (row) { this.extHover = on ? this.data(row, 'hover-id') : null; return; }
      const a = e.target.closest && e.target.closest('a.wl[data-page]');
      if (a && this.root.querySelector('#l2-reader').contains(a) && !a.classList.contains('broken')) {
        const t = a.getAttribute('data-page'); this.extHover = on ? (this.s.gmap && this.s.gmap[t] ? t : null) : null;
      }
    }
    onKey(e) {
      if (e.key !== 'Escape') return;
      if (this.s.settingsOpen) { e.preventDefault(); if (this.s.openSelect) { this.s.openSelect = null; this.renderSettings(); } else this.closeSettings(); }
      else if (this.s.openId) { e.preventDefault(); this.closeReader(); }
      else if (this.s.query) { this.s.query = ''; const i = this.root.querySelector('#l2-search'); if (i) i.value = ''; this.renderList(); }
    }

    // ---------- navigation ----------
    open(id) {
      const node = this.s.gmap && this.s.gmap[id];
      const isPage = (!node && this.s.byId[id]) || (node && node.type === 'page');
      this.s.openId = id; this.wantFit = true; this.centerOnId = id; this.reheat();
      this.pushPath(id);
      if (isPage) this.openPage(id);
      else if (node) this.openNode(node);
      this.renderList(); this.updateGraphHeader();
    }
    async openPage(id) {
      const token = ++this._openToken;
      this.reader = { kind: 'page', loading: true, id };
      this.renderReader();
      try {
        const [detail, rendered, links] = await Promise.all([
          this.fetchDetail(id),
          this.fetchJSON('/api/pages/' + enc(id) + '/rendered'),
          this.fetchJSON('/api/pages/' + enc(id) + '/links'),
        ]);
        if (token !== this._openToken) return;
        this.reader = this.buildPageReader(detail, rendered, links);
      } catch (e) {
        if (token !== this._openToken) return;
        this.reader = { kind: 'page', error: true, id };
      }
      this.renderReader();
    }
    openNode(node) {
      this._openToken++;
      this.reader = this.buildNodeReader(node);
      this.renderReader();
    }
    goHome() {
      this._openToken++; this.s.openId = null; this.s.query = '';
      const i = this.root.querySelector('#l2-search'); if (i) i.value = '';
      this.reader = null; this.wantFit = true; this.reheat();
      this.pushPath(null);
      this.renderList(); this.renderReader(); this.updateGraphHeader();
    }
    closeReader() {
      this._openToken++; this.s.openId = null; this.reader = null; this.wantFit = true; this.reheat();
      this.pushPath(null); this.renderList(); this.renderReader(); this.updateGraphHeader();
    }
    focusInMap() { if (!this.s.openId) return; this.wantFit = true; this.centerOnId = this.s.openId; this.reheat(); }
    pushPath(id) {
      try { const url = id ? '/' + id : '/'; if (location.pathname !== url) history.pushState({ id }, '', url); }
      catch (e) {}
    }
    openFromPath() {
      let path = '';
      try { path = decodeURIComponent(location.pathname || '/'); } catch (e) { path = location.pathname || '/'; }
      path = path.replace(/^\/+/, '').replace(/^pages\//, '');
      if (!path) { if (this.s.openId) { this.s.openId = null; this.reader = null; this.renderReader(); this.renderList(); this.updateGraphHeader(); } return; }
      if (this.s.byId[path] || (this.s.gmap && this.s.gmap[path])) {
        if (path !== this.s.openId) { this.s.openId = path; this.wantFit = true; this.centerOnId = path; this.reheat();
          const node = this.s.gmap && this.s.gmap[path];
          if (node && node.type !== 'page') this.openNode(node); else this.openPage(path);
          this.renderList(); this.updateGraphHeader(); }
      }
    }

    // ---------- sidebar list ----------
    warningOf(p) {
      const d = this.s.detailCache[p.id];
      const fm = d ? (d.frontmatter || {}) : null;
      if (!fm) return null;
      const stale = fm.stale_after, conf = fm.confidence;
      if (stale && new Date(stale).getTime() < Date.now())
        return { title: 'This page is stale', text: 'Past its review-by date of ' + stale + '. Some statements may no longer hold.' };
      if (conf === 'low' || conf === 'unknown')
        return { title: 'Low confidence', text: 'This page is an unreviewed draft (' + conf + ' confidence). Verify before relying on it.' };
      return null;
    }
    pageTitle(p) { return p.title || titleOf(p.id); }
    allByRecent() { return this.s.pages.slice().sort((a, b) => new Date(b.updated_at) - new Date(a.updated_at)); }
    searchHits(q) {
      q = q.toLowerCase().trim();
      const terms = q.split(/\s+/).filter(Boolean);
      return this.s.pages.map((p) => {
        const hay = (this.pageTitle(p) + ' ' + (p.summary || '') + ' ' + p.id + ' ' + (p.tags || []).join(' ')).toLowerCase();
        let score = 0;
        terms.forEach((t) => { if (this.pageTitle(p).toLowerCase().includes(t)) score += 40; else if (hay.includes(t)) score += 18; });
        return { p, score };
      }).filter((h) => h.score > 0).sort((a, b) => b.score - a.score);
    }
    rowFor(p, withTime) {
      const k = kc(p.kind), w = this.warningOf(p), active = this.s.openId === p.id;
      const warn = w ? `<span style="color:var(--warning);font-size:11px;flex-shrink:0" title="Needs review">⚠</span>` : '';
      const bg = active ? 'var(--rose-bg)' : 'transparent';
      const border = active ? 'var(--rose-border-soft)' : 'transparent';
      const titleColor = active ? 'var(--rose)' : 'var(--text-heading)';
      if (withTime) {
        return `<button data-act="open" data-id="${esc(p.id)}" data-hover-id="${esc(p.id)}" class="l2-row" style="display:flex;align-items:center;gap:10px;width:100%;text-align:left;margin-bottom:2px;padding:9px 10px;border-radius:var(--radius-sm);cursor:pointer;border:1px solid ${border};background:${bg};transition:background .14s,border-color .14s">
          <span style="width:7px;height:7px;border-radius:50%;flex-shrink:0;background:${k.color};box-shadow:0 0 7px ${k.color}66"></span>
          <span style="flex:1;min-width:0;font-weight:600;font-size:13px;color:${titleColor};overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(this.pageTitle(p))}</span>
          ${warn}
          <span style="flex-shrink:0;font-family:'IBM Plex Mono',monospace;font-size:9.5px;color:var(--text-dimmer)">${esc(ago(p.updated_at))}</span>
        </button>`;
      }
      return `<button data-act="open" data-id="${esc(p.id)}" data-hover-id="${esc(p.id)}" class="l2-row" style="display:flex;align-items:center;gap:10px;width:100%;text-align:left;margin-bottom:2px;padding:9px 10px;border-radius:var(--radius-sm);cursor:pointer;border:1px solid ${border};background:${bg};transition:background .14s,border-color .14s">
        <span style="width:7px;height:7px;border-radius:50%;flex-shrink:0;background:${k.color};box-shadow:0 0 7px ${k.color}66"></span>
        <span style="display:flex;flex-direction:column;min-width:0;flex:1">
          <span style="font-weight:600;font-size:13px;color:${titleColor};overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(this.pageTitle(p))}</span>
          <span style="font-family:'IBM Plex Mono',monospace;font-size:9px;color:var(--text-dimmer);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(p.id)}</span>
        </span>
        ${warn}
      </button>`;
    }
    renderList() {
      const host = this.root.querySelector('#l2-list');
      const pc = this.root.querySelector('#l2-pagecount');
      if (pc) pc.textContent = this.s.pages.length + (this.s.pages.length === 1 ? ' page' : ' pages');
      const searching = this.s.query.trim().length > 0;
      let html = '';
      if (searching) {
        const hits = this.searchHits(this.s.query);
        html += `<div style="display:flex;align-items:center;gap:7px;padding:10px 8px 8px">
          <span style="font-family:'IBM Plex Mono',monospace;font-size:9.5px;font-weight:600;letter-spacing:.12em;text-transform:uppercase;color:var(--text-dim)">Results</span>
          <span style="flex:1;height:1px;background:var(--separator)"></span>
          <span style="font-family:'IBM Plex Mono',monospace;font-size:9.5px;color:var(--text-dimmer)">${hits.length}</span></div>`;
        if (!hits.length) {
          html += `<div style="padding:24px 12px;text-align:center;color:var(--text-dim);font-size:13px;line-height:1.5">No pages match <span style="color:var(--text-body)">${esc(this.s.query)}</span></div>`;
        } else {
          html += hits.map((h) => this.rowFor(h.p, false)).join('');
        }
      } else {
        const flagged = this.allByRecent().filter((p) => this.warningOf(p));
        const flaggedBadge = flagged.length ? `<span style="min-width:17px;height:16px;padding:0 4px;border-radius:8px;background:var(--warning-bg);color:var(--warning);font-family:'IBM Plex Mono',monospace;font-size:9.5px;font-weight:700;display:inline-grid;place-items:center">${flagged.length}</span>` : '';
        const seg = (active) => ({ bg: active ? 'var(--rose-bg-strong)' : 'transparent', color: active ? 'var(--rose)' : 'var(--text-muted)', border: active ? 'var(--rose-border)' : 'var(--separator-strong)' });
        const vr = seg(this.s.listView === 'recent'), vf = seg(this.s.listView === 'flagged');
        html += `<div style="display:flex;gap:4px;padding:8px 6px 10px">
          <button data-act="view-recent" style="flex:1;height:30px;border-radius:8px;border:1px solid ${vr.border};cursor:pointer;font-family:inherit;font-size:12px;font-weight:600;background:${vr.bg};color:${vr.color};transition:all .14s">Recent</button>
          <button data-act="view-flagged" style="flex:1;height:30px;border-radius:8px;border:1px solid ${vf.border};cursor:pointer;font-family:inherit;font-size:12px;font-weight:600;background:${vf.bg};color:${vf.color};display:inline-flex;align-items:center;justify-content:center;gap:6px;transition:all .14s">Flagged${flaggedBadge}</button>
        </div>`;
        const rows = this.s.listView === 'flagged' ? flagged : this.allByRecent();
        if (this.s.listView === 'flagged' && !rows.length) {
          html += `<div style="display:flex;align-items:center;gap:10px;margin:6px 4px;padding:16px;border:1px solid var(--separator);border-radius:var(--radius-md);background:var(--bg-inset);color:var(--text-muted);font-size:12.5px;line-height:1.45;text-wrap:pretty">
            <span style="width:7px;height:7px;border-radius:50%;background:var(--positive);flex-shrink:0;box-shadow:0 0 8px rgba(34,197,94,.5)"></span>Nothing flagged — every page is reviewed.</div>`;
        } else {
          html += rows.map((p) => this.rowFor(p, true)).join('');
        }
      }
      host.innerHTML = html;
    }

    // ---------- reader: page ----------
    buildPageReader(detail, rendered, links) {
      const p = detail, k = kc(p.kind), fm = p.frontmatter || {};
      const w = this.warningOf(p);
      const meta = [];
      const mr = (a, b) => { if (b != null && b !== '') meta.push({ k: a, v: fmt(b) }); };
      mr('status', p.status); mr('updated', ago(p.updated_at)); mr('confidence', fm.confidence);
      mr('epistemic', (p.epistemic_status || '').replace(/_/g, ' '));
      const prov = [];
      const pr = (a, b) => { if (b != null && b !== '' && !(Array.isArray(b) && !b.length)) prov.push({ k: a, v: fmt(b) }); };
      pr('owner', fm.owner); pr('actor', fm.actor); pr('reviewed', fm.reviewed_at);
      pr('source task', p.source_task); pr('decision', p.decision_id); pr('policies', p.policies_applied); pr('sources', p.sources);
      // connections
      const conn = [];
      const colorOf = (pid) => { const sp = this.s.byId[pid]; return sp ? kc(sp.kind).color : KINDS.page.color; };
      (links.outgoing || []).filter((e) => e.exists && e.target).forEach((e) =>
        conn.push({ id: e.target, label: e.target_title || titleOf(e.target), rel: 'links to', dot: colorOf(e.target) }));
      (links.backlinks || []).forEach((e) =>
        conn.push({ id: e.source, label: e.source_title || titleOf(e.source), rel: 'linked from', dot: colorOf(e.source) }));
      // typed graph neighbors (non-page)
      (this.s.gedges || []).forEach((e) => {
        let other = null, dir = 'out';
        if (e.s === p.id) { other = e.t; dir = 'out'; } else if (e.t === p.id) { other = e.s; dir = 'in'; }
        if (other && this.s.gmap[other] && this.s.gmap[other].type !== 'page') {
          const on = this.s.gmap[other];
          conn.push({ id: other, label: on.label, rel: (EDGE_LABEL[e.type] || e.type) + (dir === 'in' ? ' ←' : ''), dot: tc(on.type).color });
        }
      });
      return {
        kind: 'page', id: p.id, title: this.pageTitle(p), kindLabel: k.label.replace(/s$/, ''),
        dot: k.color, dotBg: chipBg(k.color), dotBorder: chipBd(k.color), visibility: p.visibility,
        summary: p.summary || '', hasWarning: !!w, warnTitle: w ? w.title : '', warnText: w ? w.text : '',
        meta, prov, conn, html: rendered.html, links: rendered.links || [], missing: rendered.missing_links || [],
        readerColor: k.color, readerKind: k.label.replace(/s$/, ''),
      };
    }
    // ---------- reader: non-page node ----------
    buildNodeReader(node) {
      const t = tc(node.type), m = node.meta || {};
      const attrs = [];
      Object.keys(m).forEach((key) => {
        if (key === 'placeholder') return;
        const v = m[key]; if (v != null && v !== '') attrs.push({ k: key.replace(/_/g, ' '), v: String(v) });
      });
      const neighbors = [];
      (this.s.gedges || []).forEach((e) => {
        let other = null, dir = 'out';
        if (e.s === node.id) { other = e.t; dir = 'out'; } else if (e.t === node.id) { other = e.s; dir = 'in'; }
        if (other && this.s.gmap[other]) {
          const on = this.s.gmap[other];
          neighbors.push({ id: other, label: on.label, rel: (EDGE_LABEL[e.type] || e.type) + (dir === 'in' ? ' ←' : ''), dot: tc(on.type).color });
        }
      });
      return {
        kind: 'node', id: node.id, typeLabel: t.label, color: t.color, colorBg: chipBg(t.color), colorBorder: chipBd(t.color),
        label: node.label, summary: m.summary || node.summary || '', attrs, neighbors,
        readerColor: t.color, readerKind: t.label,
      };
    }

    connCard(c) {
      return `<button data-act="open" data-id="${esc(c.id)}" data-hover-id="${esc(c.id)}" class="l2-conn" style="display:flex;align-items:center;gap:9px;text-align:left;padding:10px 12px;border-radius:var(--radius-md);cursor:pointer;border:1px solid var(--reader-line);background:var(--reader-card);transition:background .14s,border-color .14s">
        <span style="width:8px;height:8px;border-radius:50%;flex-shrink:0;background:${c.dot}"></span>
        <span style="display:flex;flex-direction:column;min-width:0">
          <span style="font-size:13px;font-weight:600;color:var(--reader-ink);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(c.label)}</span>
          <span style="font-family:'IBM Plex Mono',monospace;font-size:9px;letter-spacing:.04em;text-transform:uppercase;color:var(--reader-dim)">${esc(c.rel)}</span>
        </span>
      </button>`;
    }
    renderReader() {
      const mount = this.root.querySelector('#l2-reader');
      if (!this.reader) { mount.style.display = 'none'; mount.innerHTML = ''; return; }
      const r = this.reader;
      const header = `<div style="flex-shrink:0;display:flex;align-items:center;gap:10px;height:50px;padding:0 10px 0 8px;border-bottom:1px solid var(--reader-line);background:color-mix(in srgb,var(--reader-bg) 86%,#000)">
          <button data-act="reader-close" class="l2-reader-btn" title="Close (Esc)" style="display:inline-flex;align-items:center;justify-content:center;width:34px;height:34px;border-radius:var(--radius-md);border:1px solid var(--reader-line);background:rgba(255,255,255,.04);color:var(--reader-muted);cursor:pointer;flex-shrink:0;transition:background .14s,color .14s">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M18 6 6 18M6 6l12 12"></path></svg></button>
          <span style="display:inline-flex;align-items:center;gap:8px;min-width:0">
            <span style="width:8px;height:8px;border-radius:50%;flex-shrink:0;background:${r.readerColor}"></span>
            <span style="font-family:'IBM Plex Mono',monospace;font-size:10px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:var(--reader-muted);flex-shrink:0">${esc(r.readerKind)}</span>
            <span style="font-family:'IBM Plex Mono',monospace;font-size:11px;color:var(--reader-dim);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(r.id)}</span></span>
          <button data-act="reader-focus" class="l2-reader-btn" title="Locate in the map" style="margin-left:auto;flex-shrink:0;display:inline-flex;align-items:center;gap:7px;height:34px;padding:0 13px;border-radius:var(--radius-md);border:1px solid var(--reader-line);background:rgba(255,255,255,.04);color:var(--reader-muted);cursor:pointer;font-family:'IBM Plex Mono',monospace;font-size:9.5px;font-weight:600;letter-spacing:.05em;text-transform:uppercase;white-space:nowrap;transition:background .14s,color .14s">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="3"></circle><path d="M12 2v3M12 19v3M2 12h3M19 12h3"></path></svg>Locate</button>
        </div>`;
      let body = '';
      if (r.loading) body = `<div style="padding:60px 44px;color:var(--reader-dim);font-family:'IBM Plex Mono',monospace;font-size:12px">Loading…</div>`;
      else if (r.error) body = `<div style="padding:60px 44px;color:var(--error);font-family:'IBM Plex Mono',monospace;font-size:12px">Could not load ${esc(r.id)}.</div>`;
      else if (r.kind === 'page') body = this.pageBody(r);
      else body = this.nodeBody(r);
      mount.innerHTML = `<aside class="panelin" style="width:clamp(440px,46%,760px);flex:0 0 auto;min-width:0;display:flex;flex-direction:column;border-left:1px solid var(--separator);background:var(--reader-bg);box-shadow:-26px 0 70px -34px rgba(0,0,0,.78)">
        ${header}
        <div class="paper-scroll" id="readerScroll" style="flex:1;min-width:0;min-height:0;overflow-y:auto">${body}</div>
      </aside>`;
      // display:contents so .panelin becomes a direct flex child of .stage (flush
      // to the right edge); a wrapping flex box here left a dead strip beside it.
      mount.style.display = 'contents';
      // post-process article links
      if (r.kind === 'page' && !r.loading && !r.error) {
        const hostEl = mount.querySelector('#proseHost');
        if (hostEl) this.classifyLinks(hostEl, r.links, r.missing);
      }
    }
    pageBody(r) {
      const metaHtml = r.meta.map((m) => `<span style="display:inline-flex;align-items:baseline;gap:6px;border:1px solid var(--reader-line);border-radius:8px;padding:5px 10px;background:var(--reader-card)">
        <span style="font-family:'IBM Plex Mono',monospace;font-size:8.5px;font-weight:600;letter-spacing:.07em;text-transform:uppercase;color:var(--reader-dim)">${esc(m.k)}</span>
        <span style="font-family:'IBM Plex Mono',monospace;font-size:11px;color:var(--reader-ink)">${esc(m.v)}</span></span>`).join('');
      const warnHtml = r.hasWarning ? `<div style="display:flex;gap:11px;align-items:flex-start;margin:0 0 26px;padding:13px 15px;border-radius:var(--radius-md);background:rgba(250,204,21,.1);border:1px solid rgba(250,204,21,.3)">
        <span style="font-size:15px;line-height:1.2;flex-shrink:0">⚠</span>
        <div><div style="font-weight:600;font-size:13px;color:var(--warning);margin-bottom:2px">${esc(r.warnTitle)}</div>
        <div style="font-size:12.5px;color:var(--reader-muted);line-height:1.5;text-wrap:pretty">${esc(r.warnText)}</div></div></div>` : '';
      const provHtml = r.prov.length ? `<div style="margin-top:42px;padding-top:22px;border-top:1px solid var(--reader-line)">
        <div style="font-family:'IBM Plex Mono',monospace;font-size:10.5px;font-weight:600;letter-spacing:.1em;text-transform:uppercase;color:var(--reader-muted);margin-bottom:12px">Provenance</div>
        <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:7px">
        ${r.prov.map((v) => `<div style="display:flex;flex-direction:column;gap:3px;padding:9px 12px;border:1px solid var(--reader-line);border-radius:var(--radius-md);background:var(--reader-card)">
          <span style="font-family:'IBM Plex Mono',monospace;font-size:8.5px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:var(--reader-dim)">${esc(v.k)}</span>
          <span style="font-family:'IBM Plex Mono',monospace;font-size:11.5px;color:var(--reader-ink);min-width:0;overflow-wrap:anywhere">${esc(v.v)}</span></div>`).join('')}
        </div></div>` : '';
      const connHtml = r.conn.length ? `<div style="margin-top:34px">
        <div style="display:flex;align-items:center;gap:8px;font-family:'IBM Plex Mono',monospace;font-size:10.5px;font-weight:600;letter-spacing:.1em;text-transform:uppercase;color:var(--reader-muted);margin-bottom:12px">
          <span>Connections</span><span style="color:var(--reader-dim)">${r.conn.length}</span></div>
        <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:8px">${r.conn.map((c) => this.connCard(c)).join('')}</div></div>` : '';
      return `<div class="fadein" style="max-width:720px;margin:0 auto;padding:36px 44px 88px">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:15px;flex-wrap:wrap">
          <span style="display:inline-flex;align-items:center;gap:6px;font-family:'IBM Plex Mono',monospace;font-size:10px;font-weight:600;letter-spacing:.05em;text-transform:uppercase;color:${r.dot};background:${r.dotBg};border:1px solid ${r.dotBorder};border-radius:999px;padding:4px 11px">
            <span style="width:6px;height:6px;border-radius:50%;background:${r.dot}"></span>${esc(r.kindLabel)}</span>
          <span style="font-family:'IBM Plex Mono',monospace;font-size:10px;letter-spacing:.04em;text-transform:uppercase;color:var(--reader-muted);border:1px solid var(--reader-line);border-radius:999px;padding:4px 10px">${esc(r.visibility)}</span>
        </div>
        <h1 style="margin:0 0 13px;font-family:var(--reader-font);font-size:39px;line-height:1.06;letter-spacing:-.02em;color:var(--reader-ink);font-weight:600;text-wrap:balance">${esc(r.title)}</h1>
        ${r.summary ? `<p style="margin:0 0 21px;font-family:var(--reader-font);font-size:20px;line-height:1.5;color:var(--reader-muted);font-style:italic;text-wrap:pretty">${esc(r.summary)}</p>` : ''}
        ${metaHtml ? `<div style="display:flex;flex-wrap:wrap;gap:6px;margin:0 0 26px">${metaHtml}</div>` : ''}
        ${warnHtml}
        <div class="lore-prose" id="proseHost">${r.html}</div>
        ${provHtml}
        ${connHtml}
      </div>`;
    }
    nodeBody(r) {
      const attrsHtml = r.attrs.length ? `<div style="margin:0 0 30px">
        <div style="font-family:'IBM Plex Mono',monospace;font-size:10.5px;font-weight:600;letter-spacing:.1em;text-transform:uppercase;color:var(--reader-muted);margin-bottom:12px">Attributes</div>
        <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:7px">
        ${r.attrs.map((v) => `<div style="display:flex;flex-direction:column;gap:3px;padding:9px 12px;border:1px solid var(--reader-line);border-radius:var(--radius-md);background:var(--reader-card)">
          <span style="font-family:'IBM Plex Mono',monospace;font-size:8.5px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:var(--reader-dim)">${esc(v.k)}</span>
          <span style="font-family:'IBM Plex Mono',monospace;font-size:11.5px;color:var(--reader-ink);min-width:0;overflow-wrap:anywhere">${esc(v.v)}</span></div>`).join('')}
        </div></div>` : '';
      const neiHtml = r.neighbors.length ? `<div style="display:flex;align-items:center;gap:8px;font-family:'IBM Plex Mono',monospace;font-size:10.5px;font-weight:600;letter-spacing:.1em;text-transform:uppercase;color:var(--reader-muted);margin-bottom:12px">
          <span>Connected</span><span style="color:var(--reader-dim)">${r.neighbors.length}</span></div>
        <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:8px">${r.neighbors.map((c) => this.connCard(c)).join('')}</div>` : '';
      return `<div class="fadein" style="max-width:680px;margin:0 auto;padding:36px 44px 88px">
        <div style="display:inline-flex;align-items:center;gap:7px;font-family:'IBM Plex Mono',monospace;font-size:10px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:${r.color};background:${r.colorBg};border:1px solid ${r.colorBorder};border-radius:999px;padding:4px 12px;margin-bottom:15px">
          <span style="width:6px;height:6px;border-radius:50%;background:${r.color}"></span>${esc(r.typeLabel)}</div>
        <h1 style="margin:0 0 7px;font-family:var(--reader-font);font-size:31px;line-height:1.1;letter-spacing:-.02em;color:var(--reader-ink);font-weight:600;text-wrap:balance">${esc(r.label)}</h1>
        <div style="font-family:'IBM Plex Mono',monospace;font-size:11px;color:var(--reader-dim);margin-bottom:24px;overflow-wrap:anywhere">${esc(r.id)}</div>
        ${r.summary ? `<p style="margin:0 0 26px;font-family:var(--reader-font);font-size:18px;line-height:1.55;color:var(--reader-muted);text-wrap:pretty">${esc(r.summary)}</p>` : ''}
        ${attrsHtml}${neiHtml}
      </div>`;
    }
    classifyLinks(host, links, missing) {
      // The renderer (markdown_render.py) emits internal links as class="wiki-link"
      // (or "wiki-link wiki-link--missing" when broken) with href="/<page_id>", and
      // headings carry class="heading-anchor". We translate those into the design's
      // wl / wl broken / ext classes (which lore2.css styles) and attach data-page
      // so the delegated click handler can open pages in-app. We key primarily off
      // the renderer's authoritative classes, with the links/missing arrays + href
      // as the source of the canonical page id.
      const map = {};
      (links || []).forEach((l) => { map[l.href] = l; });
      (missing || []).forEach((l) => { if (!map[l.href]) map[l.href] = { href: l.href, page_id: l.page_id, exists: false, external: false }; });
      const pidFromHref = (href) => { try { return decodeURIComponent(href.slice(1).split('#')[0]); } catch (e) { return href.slice(1).split('#')[0]; } };
      host.querySelectorAll('a').forEach((a) => {
        if (a.classList.contains('heading-anchor')) return;
        const href = a.getAttribute('href') || '';
        const info = map[href];
        const isWiki = a.classList.contains('wiki-link');
        const isMissing = a.classList.contains('wiki-link--missing');
        const external = info ? info.external : /^(https?:|\/\/|mailto:)/.test(href);
        if (external && !isWiki) {
          a.classList.add('ext'); a.setAttribute('target', '_blank'); a.setAttribute('rel', 'noopener noreferrer'); return;
        }
        if (isWiki || (info && !info.external) || (href.startsWith('/') && this.s.byId[pidFromHref(href)])) {
          const pageId = (info && info.page_id) || (href.startsWith('/') ? pidFromHref(href) : null);
          const exists = info ? info.exists : !isMissing;
          a.classList.add('wl');
          if (pageId) a.setAttribute('data-page', pageId);
          if (!exists) { a.classList.add('broken'); if (!a.getAttribute('title')) a.setAttribute('title', 'Lore page not found'); }
        }
      });
    }
    scrollHeading(hid) {
      const host = this.root.querySelector('#proseHost'); const sc = this.root.querySelector('#readerScroll');
      if (!host || !sc) return;
      let el; try { el = host.querySelector('#' + (window.CSS && CSS.escape ? CSS.escape(hid) : hid)); } catch (e) { el = null; }
      if (el) sc.scrollTo({ top: el.offsetTop + host.offsetTop - 24, behavior: 'smooth' });
    }

    // ---------- graph header / legend ----------
    updateGraphHeader() {
      const o = this.s.openId, reader = !!this.reader;
      const t = this.root.querySelector('#l2-maptitle'), sub = this.root.querySelector('#l2-mapsub');
      if (t) t.textContent = reader ? 'Connections' : 'Knowledge map';
      if (sub) {
        if (reader && o) { const n = Math.max(0, Object.keys(this.neighborhood(o)).length - 1); sub.textContent = n + ' connected'; }
        else {
          const shown = this.s.gnodes ? this.s.gnodes.length : 0;
          const links = this.s.gedges ? this.s.gedges.length : 0;
          sub.textContent = this.s.gtruncated
            ? 'top ' + shown + ' of ' + this.s.gtotal + ' nodes · ' + links + ' links'
            : shown + ' nodes · ' + links + ' links';
        }
      }
      const lt = this.root.querySelector('#l2-labeltext');
      if (lt) lt.textContent = 'Labels: ' + ({ smart: 'Smart', all: 'All', off: 'Off' }[this.s.labelMode] || 'Smart');
    }
    renderLegend() {
      const host = this.root.querySelector('#l2-legend');
      if (!host || !this.s.gnodes) return;
      const counts = {}; this.s.gnodes.forEach((n) => { counts[n.type] = (counts[n.type] || 0) + 1; });
      const plural = (s) => s.endsWith('y') ? s.slice(0, -1) + 'ies' : s + 's';
      const items = Object.keys(TYPES).filter((t) => counts[t]).map((t) => {
        const off = !!this.typeOff[t];
        return `<button data-act="legend-toggle" data-type="${t}" style="display:inline-flex;align-items:center;gap:6px;height:23px;padding:0 9px;border-radius:999px;border:1px solid ${off ? 'var(--separator)' : 'var(--separator-strong)'};background:${off ? 'transparent' : 'rgba(255,255,255,.045)'};cursor:pointer;opacity:${off ? 0.38 : 1};transition:all .14s">
          <span style="width:8px;height:8px;border-radius:50%;background:${TYPES[t].color}"></span>
          <span style="font-family:'Inter',sans-serif;font-size:11px;font-weight:500;color:var(--text-body)">${plural(TYPES[t].label)}</span>
          <span style="font-family:'IBM Plex Mono',monospace;font-size:9px;color:var(--text-dim)">${counts[t]}</span></button>`;
      }).join('');
      host.innerHTML = `<span style="font-family:'IBM Plex Mono',monospace;font-size:9px;font-weight:600;letter-spacing:.1em;text-transform:uppercase;color:var(--text-dimmer);margin-right:2px">Legend</span>${items}`;
    }
    cycleLabels() { const o = ['smart', 'all', 'off']; const i = o.indexOf(this.s.labelMode); this.s.labelMode = o[(i + 1) % o.length]; this.savePref({ labelMode: this.s.labelMode }); this.updateGraphHeader(); }
    resetView() { this.wantFit = true; this.reheat(); }
    toggleType(t) { if (!t) return; this.typeOff[t] = !this.typeOff[t]; this.savePref({ typeOff: this.typeOff }); this.reheat(); this.renderLegend(); }

    // ======================================================
    // GRAPH (canvas force layout, ported from the redesign)
    // ======================================================
    buildGraph(g) {
      const nodes = (g.nodes || []).filter((n) => !(n.metadata && n.metadata.placeholder));
      this.s.gmap = {};
      this.s.gnodes = nodes.map((n) => ({ id: n.id, type: n.type, label: n.label || titleOf(n.id), kind: (n.metadata || {}).kind, summary: (n.metadata || {}).summary || null, meta: n.metadata || {} }));
      this.s.gnodes.forEach((n) => { this.s.gmap[n.id] = n; });
      this.s.gedges = (g.edges || []).filter((e) => this.s.gmap[e.source] && this.s.gmap[e.target]).map((e) => ({ s: e.source, t: e.target, type: e.type }));
      this.s.deg = {}; this.s.gnodes.forEach((n) => { this.s.deg[n.id] = 0; });
      this.s.gedges.forEach((e) => { this.s.deg[e.s]++; this.s.deg[e.t]++; });
      // Highest-degree-first order drives the O(n^2) repulsion cap in simStep().
      this.s.degOrder = this.s.gnodes.map((n) => n.id).sort((a, b) => (this.s.deg[b] - this.s.deg[a]) || (a < b ? -1 : 1));
      // Truncation provenance from the server (stats.truncated when the vault
      // exceeds the node cap) so the header can flag a partial view.
      const st = (g && g.stats) || {};
      this.s.gtruncated = !!st.truncated;
      this.s.gtotal = st.total_nodes || this.s.gnodes.length;
      // (re)seed positions
      this.pos = {};
      const N = this.s.gnodes.length || 1;
      this.s.gnodes.forEach((n, i) => {
        const a = (i / N) * Math.PI * 2, r = 230 + (this.s.deg[n.id] || 0) * 4;
        this.pos[n.id] = { x: Math.cos(a) * r * (0.5 + Math.random() * 0.5), y: Math.sin(a) * r * (0.5 + Math.random() * 0.5), vx: 0, vy: 0, pin: false };
      });
      this._alpha = 1;
    }
    startGraph() {
      if (this._graphStarted) { this.wantFit = true; return; }
      this._graphStarted = true;
      this.ctx = this.cv.getContext('2d');
      this.tf = { s: 1, ox: 0, oy: 0 };
      this.drag = null; this.hoverId = null;
      this.cv.onpointerdown = (e) => this.gDown(e);
      this.cv.onpointermove = (e) => this.gMove(e);
      this.cv.onpointerup = (e) => this.gUp(e);
      this.cv.onpointerleave = () => { this.hoverId = null; };
      this.cv.onwheel = (e) => this.gWheel(e);
      this.sizeCanvas();
      this.wantFit = true; this._running = true;
      const loop = () => { if (!this._running) return; this.simStep(); this.applyView(); this.draw(); this._raf = requestAnimationFrame(loop); };
      loop();
    }
    applyView() {
      if (this.wantFit) { this.wantFit = false; this.fitView(); }
      if (this.centerOnId) { const id = this.centerOnId; this.centerOnId = null; if (this.pos && this.pos[id]) this.centerNode(id); this.reheat(); }
    }
    sizeCanvas() {
      const cv = this.cv; if (!cv || !cv.parentElement) return;
      const r = cv.parentElement.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      cv.width = Math.max(1, r.width * dpr); cv.height = Math.max(1, r.height * dpr);
      cv.style.width = r.width + 'px'; cv.style.height = r.height + 'px';
      if (this.ctx) this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      this.vw = r.width; this.vh = r.height;
      if (this.pos) this.draw();
    }
    reheat() { this._alpha = Math.max(this._alpha || 0, 0.5); }
    neighborhood(id) {
      const set = {}; if (!id) return set; set[id] = true;
      (this.s.gedges || []).forEach((e) => { if (e.s === id) set[e.t] = true; else if (e.t === id) set[e.s] = true; });
      return set;
    }
    visibleSet() {
      const off = this.typeOff, vis = {};
      this.s.gnodes.forEach((n) => { if (!off[n.type]) vis[n.id] = true; });
      return vis;
    }
    fitView() {
      const o = this.s.openId, off = this.typeOff;
      let ids = [], focused = false;
      if (o && this.s.gmap[o]) {
        const nb = this.neighborhood(o);
        ids = this.s.gnodes.filter((n) => nb[n.id] && (!off[n.type] || n.id === o)).map((n) => n.id);
        focused = ids.length > 0;
      }
      if (!focused) ids = this.s.gnodes.filter((n) => !off[n.type]).map((n) => n.id);
      if (!ids.length) { this.tf = { s: 0.82, ox: this.vw / 2, oy: this.vh / 2 }; this._alpha = 1; return; }
      let mnx = Infinity, mny = Infinity, mxx = -Infinity, mxy = -Infinity;
      ids.forEach((id) => { const p = this.pos[id]; mnx = Math.min(mnx, p.x); mny = Math.min(mny, p.y); mxx = Math.max(mxx, p.x); mxy = Math.max(mxy, p.y); });
      const w = (mxx - mnx) || 1, h = (mxy - mny) || 1, cx = (mnx + mxx) / 2, cy = (mny + mxy) / 2;
      const pad = Math.min(focused ? 120 : 90, Math.max(16, Math.min(this.vw, this.vh) * 0.16)), cap = focused ? 1.7 : 1.15;
      let s = Math.min((this.vw - pad * 2) / w, (this.vh - pad * 2) / h, cap); s = Math.max(0.4, Math.min(2.2, s));
      this.tf = { s, ox: this.vw / 2 - cx * s, oy: this.vh / 2 - cy * s }; this._alpha = Math.max(this._alpha, 0.25);
    }
    centerNode(id) {
      const p = this.pos[id]; if (!p) return;
      this.tf.s = Math.max(this.tf.s, 1.0); this.tf.ox = this.vw / 2 - p.x * this.tf.s; this.tf.oy = this.vh / 2 - p.y * this.tf.s;
    }
    simStep() {
      const vis = this.visibleSet();
      const ids = this.s.gnodes.filter((n) => vis[n.id]).map((n) => n.id);
      const a = this._alpha;
      if (a > 0.02) {
        // All-pairs repulsion is O(n^2)/frame; above the cap, only the highest-degree
        // visible nodes repel each other. Everything else keeps its seeded position and
        // still moves under the edge-spring + drift passes below, so large vaults stay
        // interactive instead of pinning a core for the whole cooldown.
        const rep = ids.length > GRAPH_SIM_CAP
          ? (this.s.degOrder || ids).filter((id) => vis[id]).slice(0, GRAPH_SIM_CAP)
          : ids;
        for (let i = 0; i < rep.length; i++) {
          const pi = this.pos[rep[i]];
          for (let j = i + 1; j < rep.length; j++) {
            const pj = this.pos[rep[j]];
            let dx = pi.x - pj.x, dy = pi.y - pj.y, d2 = dx * dx + dy * dy + 0.01;
            const f = 5200 / d2; const d = Math.sqrt(d2);
            const ux = dx / d, uy = dy / d;
            pi.vx += ux * f * a; pi.vy += uy * f * a; pj.vx -= ux * f * a; pj.vy -= uy * f * a;
          }
        }
        this.s.gedges.forEach((e) => {
          if (!vis[e.s] || !vis[e.t]) return;
          const ps = this.pos[e.s], pt = this.pos[e.t];
          let dx = pt.x - ps.x, dy = pt.y - ps.y, d = Math.sqrt(dx * dx + dy * dy) + 0.01;
          const target = 124, f = (d - target) * 0.012 * a;
          const ux = dx / d, uy = dy / d;
          ps.vx += ux * f; ps.vy += uy * f; pt.vx -= ux * f; pt.vy -= uy * f;
        });
        ids.forEach((id) => { const p = this.pos[id]; p.vx -= p.x * 0.0016 * a; p.vy -= p.y * 0.0016 * a; });
        ids.forEach((id) => { const p = this.pos[id]; if (p.pin) { p.vx = 0; p.vy = 0; return; } p.vx *= 0.86; p.vy *= 0.86; p.x += p.vx; p.y += p.vy; });
        this._alpha *= 0.992;
      }
    }
    nodeColor(n) { return tc(n.type).color; }
    nodeR(n) { return 6 + Math.min(13, (this.s.deg[n.id] || 0) * 1.7); }
    draw() {
      const ctx = this.ctx; if (!ctx) return;
      ctx.clearRect(0, 0, this.vw, this.vh);
      const tf = this.tf, vis = this.visibleSet(), focus = this.s.openId;
      const active = this.hoverId || this.extHover || focus;
      const hl = active ? this.neighborhood(active) : null;
      const labelMode = this.s.labelMode || 'smart';
      ctx.save(); ctx.translate(tf.ox, tf.oy); ctx.scale(tf.s, tf.s);
      this.s.gedges.forEach((e) => {
        if (!vis[e.s] || !vis[e.t]) return;
        const a = this.pos[e.s], b = this.pos[e.t];
        const hot = active && (e.s === active || e.t === active);
        const inHl = hl ? (hl[e.s] && hl[e.t]) : true;
        ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y);
        ctx.strokeStyle = hot ? 'rgba(255,143,190,.78)' : inHl ? 'rgba(255,255,255,.24)' : 'rgba(255,255,255,.08)';
        ctx.lineWidth = (hot ? 2 : 1.2) / tf.s; ctx.stroke();
      });
      this.s.gnodes.forEach((n) => {
        if (!vis[n.id]) return;
        const p = this.pos[n.id], r = this.nodeR(n), col = this.nodeColor(n);
        const sel = focus === n.id, hov = this.hoverId === n.id || this.extHover === n.id;
        const dim = hl && !hl[n.id];
        if (sel || hov) { ctx.globalAlpha = 1; ctx.beginPath(); ctx.arc(p.x, p.y, r + (sel ? 6 : 4), 0, 7); ctx.fillStyle = col + '33'; ctx.fill(); }
        ctx.globalAlpha = 1; ctx.beginPath(); ctx.arc(p.x, p.y, r, 0, 7); ctx.fillStyle = '#0a0a0c'; ctx.fill();
        ctx.globalAlpha = dim ? 0.34 : 1;
        ctx.beginPath(); ctx.arc(p.x, p.y, r, 0, 7); ctx.fillStyle = col; ctx.fill();
        if (sel) { ctx.lineWidth = 2 / tf.s; ctx.strokeStyle = '#fff'; ctx.stroke(); }
        const showLabel = sel || hov || labelMode === 'all' || (labelMode === 'smart' && ((this.s.deg[n.id] || 0) >= 3 || (hl && hl[n.id])));
        if (showLabel) {
          ctx.font = (sel ? 600 : 500) + ' ' + (12 / tf.s) + "px Inter, sans-serif";
          ctx.fillStyle = sel ? '#fff' : (dim ? 'rgba(255,255,255,.38)' : 'rgba(255,255,255,.82)');
          ctx.textAlign = 'center'; ctx.textBaseline = 'top';
          let lab = n.label; if (lab.length > 26) lab = lab.slice(0, 24) + '…';
          ctx.fillText(lab, p.x, p.y + r + 4 / tf.s);
        }
        ctx.globalAlpha = 1;
      });
      ctx.restore();
    }
    toWorld(e) {
      const r = this.cv.getBoundingClientRect();
      const px = e.clientX - r.left, py = e.clientY - r.top;
      return { x: (px - this.tf.ox) / this.tf.s, y: (py - this.tf.oy) / this.tf.s, px, py };
    }
    hit(w) {
      const vis = this.visibleSet();
      for (let i = this.s.gnodes.length - 1; i >= 0; i--) {
        const n = this.s.gnodes[i]; if (!vis[n.id]) continue;
        const p = this.pos[n.id], r = this.nodeR(n) + 5;
        if ((w.x - p.x) ** 2 + (w.y - p.y) ** 2 <= r * r) return n;
      }
      return null;
    }
    gDown(e) {
      try { this.cv.setPointerCapture(e.pointerId); } catch (err) {}
      const w = this.toWorld(e); const n = this.hit(w);
      if (n) { this.drag = { id: n.id, sx: w.x, sy: w.y, moved: false }; this.pos[n.id].pin = true; }
      else { this.drag = { pan: true, lx: w.px, ly: w.py }; this.cv.classList.add('grabbing'); }
    }
    gMove(e) {
      const w = this.toWorld(e);
      if (this.drag && this.drag.id) {
        if (!this.drag.moved && Math.hypot(w.x - this.drag.sx, w.y - this.drag.sy) > 3) this.drag.moved = true;
        if (this.drag.moved) { const p = this.pos[this.drag.id]; p.x = w.x; p.y = w.y; p.vx = p.vy = 0; this._alpha = Math.max(this._alpha, 0.3); }
      } else if (this.drag && this.drag.pan) {
        this.tf.ox += w.px - this.drag.lx; this.tf.oy += w.py - this.drag.ly; this.drag.lx = w.px; this.drag.ly = w.py;
      } else { const n = this.hit(w); this.hoverId = n ? n.id : null; this.cv.classList.toggle('pointing', !!n); }
    }
    gUp() {
      const d = this.drag; this.drag = null; this.cv.classList.remove('grabbing');
      if (d && d.id) { this.pos[d.id].pin = false; if (!d.moved) this.open(d.id); }
    }
    gWheel(e) {
      e.preventDefault();
      const w = this.toWorld(e); const old = this.tf.s;
      let s = old * (e.deltaY < 0 ? 1.12 : 0.89); s = Math.max(0.35, Math.min(2.6, s));
      this.tf.ox = w.px - (w.px - this.tf.ox) * (s / old); this.tf.oy = w.py - (w.py - this.tf.oy) * (s / old);
      this.tf.s = s;
    }

    // ======================================================
    // SETTINGS MODAL
    // ======================================================
    envLlm() {
      return { provider: 'none', model: '', embedding_model: '', embeddings_enabled: false, base_url: '',
        api_key_configured: false, api_key_hint: '', escalation_model: '', escalation_api_key_configured: false,
        escalation_api_key_hint: '', max_tokens: 4096, temperature: 0.3, timeout_seconds: 60, max_retries: 3 };
    }
    llmFormFrom(s) {
      return { provider: s.provider, model: s.model, embedding_model: s.embedding_model || '', base_url: s.base_url || '',
        escalation_model: s.escalation_model || '', max_tokens: String(s.max_tokens), temperature: String(s.temperature),
        timeout_seconds: String(s.timeout_seconds), max_retries: String(s.max_retries) };
    }
    async openSettings() {
      this.s.settingsOpen = true; this.s.openSelect = null; this.s.llmToast = '';
      this.renderSettings(); // immediate (shows skeleton)
      try {
        const [llm, keys] = await Promise.all([this.fetchJSON('/api/settings/llm'), this.fetchJSON('/api/api-keys').catch(() => [])]);
        this.s.llmSaved = llm; this.s.llm = this.llmFormFrom(llm);
        this.s.apiKeys = Array.isArray(keys) ? keys : [];
      } catch (e) {
        if (!this.s.llmSaved) { this.s.llmSaved = this.envLlm(); this.s.llm = this.llmFormFrom(this.s.llmSaved); }
      }
      if (this.s.settingsOpen) this.renderSettings();
    }
    closeSettings() {
      Object.assign(this.s, { settingsOpen: false, openSelect: null, createdToken: null, confirmRevoke: null,
        editApiKey: false, apiKeyInput: '', editEscKey: false, escKeyInput: '', llmToast: '', showKeyGen: false });
      this.renderSettings();
    }
    scrollToSection(id) {
      this.s.activeSection = id; this.renderSettings();
      const cont = this.root.querySelector('#lsetContent'); const el = this.root.querySelector('#lset-' + id);
      if (cont && el) cont.scrollTo({ top: Math.max(0, el.offsetTop - 8), behavior: 'smooth' });
    }
    selectPick(sel, val) {
      if (sel === 'provider') { this.s.llm.provider = val; this.s.llmToast = ''; }
      else if (sel === 'role') { this.s.newKey.role = val; }
      else if (sel === 'labelMode') { this.s.labelMode = val; this.savePref({ labelMode: val }); this.updateGraphHeader(); }
      else if (sel === 'readerFont') { this.s.readerFont = val; this.root.setAttribute('data-font', val); this.savePref({ readerFont: val }); }
      this.s.openSelect = null; this.renderSettings();
    }
    bumpLlm(field, delta, dp) {
      let n = parseFloat(this.s.llm[field]); if (isNaN(n)) n = 0; n += delta;
      const lim = { max_tokens: [1, null], temperature: [0, 2], timeout_seconds: [1, null], max_retries: [0, 10] }[field] || [null, null];
      if (lim[0] != null) n = Math.max(lim[0], n); if (lim[1] != null) n = Math.min(lim[1], n);
      this.s.llm[field] = dp ? n.toFixed(parseInt(dp, 10)) : String(Math.round(n));
      this.s.llmToast = ''; this.renderSettings();
    }
    llmDirty() {
      const f = this.s.llm, s = this.s.llmSaved; if (!f || !s) return false;
      const eq = (a, b) => String(a) === String(b);
      const same = f.provider === s.provider && f.model === s.model && (f.embedding_model || '') === (s.embedding_model || '')
        && (f.base_url || '') === (s.base_url || '') && (f.escalation_model || '') === (s.escalation_model || '')
        && eq(f.max_tokens, s.max_tokens) && eq(f.temperature, s.temperature) && eq(f.timeout_seconds, s.timeout_seconds) && eq(f.max_retries, s.max_retries);
      return !same || !!this.s.apiKeyInput.trim() || !!this.s.escKeyInput.trim();
    }
    async saveLlm() {
      const f = this.s.llm;
      const body = {
        provider: f.provider, model: f.model, embedding_model: (f.embedding_model || '').trim(), base_url: (f.base_url || '').trim(),
        escalation_model: (f.escalation_model || '').trim(), max_tokens: parseInt(f.max_tokens, 10) || 0,
        temperature: parseFloat(f.temperature) || 0, timeout_seconds: parseFloat(f.timeout_seconds) || 0, max_retries: parseInt(f.max_retries, 10) || 0,
      };
      if (this.s.apiKeyInput.trim()) body.api_key = this.s.apiKeyInput.trim();
      if (this.s.escKeyInput.trim()) body.escalation_api_key = this.s.escKeyInput.trim();
      try {
        const saved = await this.fetchJSON('/api/settings/llm', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
        this.s.llmSaved = saved; this.s.llm = this.llmFormFrom(saved);
        Object.assign(this.s, { editApiKey: false, apiKeyInput: '', editEscKey: false, escKeyInput: '', llmToast: 'saved' });
      } catch (e) { this.s.llmToast = e.status === 401 || e.status === 403 ? 'forbidden' : 'error'; }
      this.renderSettings();
    }
    async resetLlm() {
      try {
        const env = await this.fetchJSON('/api/settings/llm', { method: 'DELETE' });
        this.s.llmSaved = env; this.s.llm = this.llmFormFrom(env);
        Object.assign(this.s, { editApiKey: false, apiKeyInput: '', editEscKey: false, escKeyInput: '', llmToast: 'reset' });
      } catch (e) { this.s.llmToast = e.status === 401 || e.status === 403 ? 'forbidden' : 'error'; }
      this.renderSettings();
    }
    async createKey() {
      const n = this.s.newKey; if (!n.name.trim()) return;
      try {
        const rec = await this.fetchJSON('/api/api-keys', { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: n.name.trim(), description: n.description.trim(), role: n.role }) });
        this.s.apiKeys = [rec, ...this.s.apiKeys];
        this.s.createdToken = rec; this.s.copyLabel = 'Copy'; this.s.newKey = { name: '', description: '', role: 'writer' }; this.s.showKeyGen = false;
      } catch (e) { this.s.llmToast = e.status === 401 || e.status === 403 ? 'forbidden' : 'error'; }
      this.renderSettings();
    }
    copyToken() {
      const t = this.s.createdToken; if (!t) return;
      try { navigator.clipboard.writeText(t.api_key); } catch (e) {}
      this.s.copyLabel = 'Copied ✓'; this.renderSettings();
    }
    async revokeKey(id) {
      try {
        const rec = await this.fetchJSON('/api/api-keys/' + enc(id) + '/revoke', { method: 'POST' });
        this.s.apiKeys = this.s.apiKeys.map((k) => k.id === id ? rec : k);
      } catch (e) {}
      this.s.confirmRevoke = null; this.renderSettings();
    }
    async sessionLogin() {
      const key = this.s.sessionKey.trim(); if (!key) return;
      try { await this.fetchJSON('/api/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ api_key: key }) }); this.s.sessionKey = ''; this.renderSettings(); } catch (e) {}
    }
    refreshSettingsActions() {
      const btn = this.root.querySelector('#lset-save'); if (btn) btn.disabled = !this.llmDirty();
    }
    refreshKeygenActions() {
      const btn = this.root.querySelector('#lset-create'); if (btn) btn.disabled = !this.s.newKey.name.trim();
    }
    dd(id, value, options) {
      const open = this.s.openSelect === id;
      const cur = options.find((o) => o.value === value) || options[0] || { label: '' };
      const menu = open ? `<div class="flow-select-menu">${options.map((o) => `<button type="button" class="flow-select-option ${o.value === value ? 'is-selected' : ''}" data-act="select-pick" data-sel="${id}" data-val="${esc(o.value)}">
          <span class="flow-select-value">${esc(o.label)}</span>
          <svg class="chk" viewBox="0 0 12 12" fill="none"><path d="M2.5 6.5 5 9l4.5-5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"></path></svg></button>`).join('')}</div>` : '';
      return `<div class="flow-select ${open ? 'is-open' : ''}">
        <button type="button" class="flow-select-trigger" data-act="select-toggle" data-sel="${id}">
          <span class="flow-select-value">${esc(cur.label)}</span>
          <svg class="flow-select-caret" viewBox="0 0 12 12" fill="none"><path d="M2.5 4.5 6 8l3.5-3.5" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"></path></svg>
        </button>${menu}</div>`;
    }
    counter(field, step, dp) {
      const minus = `<svg viewBox="0 0 14 14" fill="none"><path d="M3 7h8" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"></path></svg>`;
      const plus = `<svg viewBox="0 0 14 14" fill="none"><path d="M3 7h8M7 3v8" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"></path></svg>`;
      const stepAttr = dp ? ` step="${step}"` : '';
      return `<div class="priority-counter">
        <button type="button" data-act="bump" data-field="${field}" data-delta="${-step}" data-dp="${dp || ''}" aria-label="Decrease">${minus}</button>
        <input type="number"${stepAttr} value="${esc(this.s.llm[field])}" data-act="llm-field" data-field="${field}">
        <button type="button" data-act="bump" data-field="${field}" data-delta="${step}" data-dp="${dp || ''}" aria-label="Increase">${plus}</button>
      </div>`;
    }
    renderSettings() {
      const host = this.root.querySelector('#l2-modal');
      if (!this.s.settingsOpen) { host.innerHTML = ''; return; }
      const st = this.s;
      const loading = !st.llmSaved;
      const saved = st.llmSaved || this.envLlm();
      const f = st.llm || this.llmFormFrom(saved);
      const A = (id) => (st.activeSection || 'model') === id ? 'active' : '';
      const keyOn = saved.api_key_configured;
      const hasEmb = !!(f.embedding_model || '').trim();
      const liveKey = saved.api_key_configured || !!st.apiKeyInput.trim();
      const embEnabled = hasEmb && liveKey;
      const embChip = embEnabled ? 'chip-positive' : (hasEmb ? 'chip-warning' : 'chip-neutral');
      const embLabel = embEnabled ? 'Enabled' : (hasEmb ? 'Needs API key' : 'Disabled');
      let provs = PROVIDERS.slice();
      if (!provs.some((p) => p[0] === f.provider)) provs.unshift([f.provider, f.provider]);
      const provOpts = provs.map((p) => ({ value: p[0], label: p[1] }));
      const providerShort = (provs.find((p) => p[0] === f.provider) || [f.provider, f.provider])[1];
      const roleHelp = { reader: 'Read-only — reader HTML and read tools. Any write returns 403.', writer: 'Read + write — captures, pages, recall and ack.', admin: 'Full access including key management and model settings.' }[st.newKey.role];
      const keyRows = st.apiKeys.map((k) => {
        const revoked = !!k.revoked_at;
        const actions = revoked ? `<span style="color:var(--text-dim)">—</span>`
          : (st.confirmRevoke === k.id
            ? `<button class="btn btn-ghost btn-sm" data-act="key-cancel">Cancel</button><button class="btn btn-danger btn-sm" data-act="key-confirm" data-id="${esc(k.id)}">Confirm</button>`
            : `<button class="btn btn-danger btn-sm" data-act="key-ask" data-id="${esc(k.id)}">Revoke</button>`);
        return `<tr>
          <td class="cell-name">${esc(k.name)}</td>
          <td><span class="api-key-role ${esc(k.role)}">${esc(k.role)}</span></td>
          <td><code>${esc(k.key_prefix)}</code></td>
          <td><span class="flow-chip ${revoked ? 'chip-error' : 'chip-positive'}">${revoked ? 'Revoked' : 'Active'}</span></td>
          <td><code>${esc(fmtDate(k.created_at))}</code></td>
          <td class="actions">${actions}</td></tr>`;
      }).join('');
      const activeKeys = st.apiKeys.filter((k) => !k.revoked_at).length;
      const revokedN = st.apiKeys.length - activeKeys;
      const toast = st.llmToast === 'saved' ? '✓ Saved — the LLM client hot-reloaded.'
        : st.llmToast === 'reset' ? '↺ Reverted to deploy (env) defaults.'
        : st.llmToast === 'forbidden' ? '✗ Forbidden — admin credentials required for this change.'
        : st.llmToast === 'error' ? '✗ Could not save. Check the server logs.' : '';
      const ct = st.createdToken;
      const apiKeyView = !st.editApiKey, escKeyView = !st.editEscKey;

      host.innerHTML = `<div data-act="settings-close" style="position:fixed;inset:0;z-index:300;display:flex;align-items:center;justify-content:center;padding:24px;background:rgba(0,0,0,.62);backdrop-filter:blur(5px)">
        <div data-act="panel-stop" class="lset fadein">
          <button data-act="settings-close" class="lset-close" title="Close (Esc)" aria-label="Close settings">×</button>
          <nav class="sidebar" aria-label="Settings sections">
            <div class="sidebar-brand"><span class="dot"></span><span class="wm">Lore</span></div>
            <div class="sidebar-section"><div class="sidebar-label">Configuration</div>
              <button class="sidebar-link ${A('model')}" data-act="sec" data-sec="model"><span class="icon">◆</span> Model</button>
              <button class="sidebar-link ${A('api-keys')}" data-act="sec" data-sec="api-keys"><span class="icon">◇</span> API Keys</button></div>
            <div class="sidebar-section"><div class="sidebar-label">Preferences</div>
              <button class="sidebar-link ${A('appearance')}" data-act="sec" data-sec="appearance"><span class="icon">◐</span> Appearance</button></div>
            <div class="sidebar-section"><div class="sidebar-label">System</div>
              <button class="sidebar-link ${A('overview')}" data-act="sec" data-sec="overview"><span class="icon">▣</span> Overview</button></div>
          </nav>
          <main class="content" id="lsetContent">
            <div class="content-inner">
              <div class="page-head"><h1>Settings</h1><p>Admin configuration for this Lore deployment. Model &amp; keys are stored server-side and hot-reload; appearance is local to this browser.</p></div>

              <section id="lset-model">
                <div class="section-head"><div>
                  <div class="sec-kicker">01 — Model</div><h3 class="sec-title">Provider &amp; model</h3>
                  <p class="sec-sub">The LLM Lore uses for extraction, recall scoring and embeddings. Overrides the deploy (env) defaults.</p></div>
                  <div class="section-actions">
                    <button class="btn btn-ghost" data-act="llm-reset">Reset to defaults</button>
                    <button class="btn btn-primary" id="lset-save" data-act="llm-save" ${this.llmDirty() ? '' : 'disabled'}>Save changes</button></div></div>
                <div class="system-table">
                  <div class="system-row"><span class="system-label">Status</span><span class="status-dot ${keyOn ? 'is-positive' : 'is-warning'}">${keyOn ? 'Runtime override active' : 'Using deploy (env) defaults'}</span></div>
                  <div class="system-row"><span class="system-label">Provider</span><span class="system-value mono">${esc(f.provider)}</span></div>
                  <div class="system-row"><span class="system-label">Model</span><span class="system-value mono">${esc(f.model || '—')}</span></div>
                  <div class="system-row"><span class="system-label">Embeddings</span><span class="flow-chip ${embChip}">${embLabel}</span></div></div>
                <div class="subhead">Connection</div>
                <div class="form-grid">
                  <div class="field"><label class="form-label">Provider</label>${this.dd('provider', f.provider, provOpts)}</div>
                  <div class="field"><label class="form-label">Model</label><input class="form-input" value="${esc(f.model)}" data-act="llm-field" data-field="model" spellcheck="false" placeholder="gpt-4o-mini"></div>
                  <div class="field full"><label class="form-label">Base URL</label><input class="form-input mono" value="${esc(f.base_url)}" data-act="llm-field" data-field="base_url" spellcheck="false" placeholder="Default for provider"><div class="form-hint">Leave blank to use the provider's standard endpoint.</div></div></div>
                <div class="subhead">Authentication</div>
                <div class="field"><label class="form-label">Primary API key</label>
                  ${apiKeyView
                    ? `<div class="input-row"><input class="form-input mono" readonly value="${esc(keyOn ? (saved.api_key_hint || '****') : 'Not set')}"><button class="btn btn-secondary" data-act="api-edit">${keyOn ? 'Replace' : 'Set key'}</button></div>`
                    : `<div class="input-row"><input class="form-input mono" value="${esc(st.apiKeyInput)}" data-act="api-key-input" spellcheck="false" autocomplete="off" placeholder="Paste new key"><button class="btn btn-ghost" data-act="api-cancel">Cancel</button></div>`}
                  <div class="form-hint">Write-only — the stored key is never shown. A new value replaces it; leave unchanged to keep the current one.</div></div>
                <div class="subhead">Embeddings</div>
                <div class="field"><label class="form-label">Embedding model</label><input class="form-input" value="${esc(f.embedding_model)}" data-act="llm-field" data-field="embedding_model" spellcheck="false" placeholder="text-embedding-3-small"><div class="form-hint">Dense embeddings activate only when an embedding model and an API key are both set.</div></div>
                <div class="subhead">Escalation</div>
                <div class="form-grid">
                  <div class="field full"><label class="form-label">Escalation model</label><input class="form-input" value="${esc(f.escalation_model)}" data-act="llm-field" data-field="escalation_model" spellcheck="false" placeholder="Stronger model, e.g. gpt-4o"><div class="form-hint">Used only when a task escalates to a stronger model.</div></div>
                  <div class="field full"><label class="form-label">Escalation API key</label>
                  ${escKeyView
                    ? `<div class="input-row"><input class="form-input mono" readonly value="${esc(saved.escalation_api_key_configured ? (saved.escalation_api_key_hint || '****') : 'Not set')}"><button class="btn btn-secondary" data-act="esc-edit">${saved.escalation_api_key_configured ? 'Replace' : 'Set key'}</button></div>`
                    : `<div class="input-row"><input class="form-input mono" value="${esc(st.escKeyInput)}" data-act="esc-key-input" spellcheck="false" autocomplete="off" placeholder="Paste new key"><button class="btn btn-ghost" data-act="esc-cancel">Cancel</button></div>`}
                  </div></div>
                <div class="subhead">Generation</div>
                <div class="form-grid">
                  <div class="field"><label class="form-label">Max tokens</label>${this.counter('max_tokens', 256, 0)}</div>
                  <div class="field"><label class="form-label">Temperature</label>${this.counter('temperature', 0.1, 1)}</div>
                  <div class="field"><label class="form-label">Timeout (s)</label>${this.counter('timeout_seconds', 5, 0)}</div>
                  <div class="field"><label class="form-label">Max retries</label>${this.counter('max_retries', 1, 0)}</div></div>
                ${toast ? `<div class="note note-accent">${esc(toast)}</div>` : ''}
                <div class="note">Saved changes hot-reload the LLM client — no restart. <strong>Reset to defaults</strong> clears your runtime overrides and falls back to the deploy (env) configuration.</div>
              </section>
              <hr class="section-divider">

              <section id="lset-api-keys">
                <div class="section-head"><div>
                  <div class="sec-kicker">02 — API Keys</div><h3 class="sec-title">Access &amp; roles</h3>
                  <p class="sec-sub">Bearer keys agents use to reach Lore. Roles scope what a key may do.</p></div>
                  <div class="section-actions"><button class="btn btn-primary" data-act="keygen-toggle">${st.showKeyGen ? 'Close' : 'Generate API key'}</button></div></div>
                ${st.showKeyGen ? `<div class="panel"><div class="panel-title">Generate a key</div>
                  <div class="form-grid">
                    <div class="field"><label class="form-label">Name</label><input class="form-input" value="${esc(st.newKey.name)}" data-act="newkey-name" maxlength="120" placeholder="e.g. nyx-agent" spellcheck="false"></div>
                    <div class="field"><label class="form-label">Role</label>${this.dd('role', st.newKey.role, [{ value: 'reader', label: 'reader' }, { value: 'writer', label: 'writer' }, { value: 'admin', label: 'admin' }])}</div>
                    <div class="field full"><label class="form-label">Description</label><textarea class="form-input" data-act="newkey-desc" maxlength="500" placeholder="What it's for">${esc(st.newKey.description)}</textarea></div></div>
                  <div class="form-hint">${esc(roleHelp)}</div>
                  <div class="row-actions"><button class="btn btn-ghost" data-act="keygen-toggle">Cancel</button><button class="btn btn-primary" id="lset-create" data-act="key-create" ${st.newKey.name.trim() ? '' : 'disabled'}>Generate key</button></div></div>` : ''}
                ${ct ? `<div class="panel" style="border-color:var(--rose-border)"><div class="panel-title">One-time key — ${esc(ct.name)}</div>
                  <div class="note note-accent">Shown once. Copy it now — it can't be retrieved later. Afterward only the prefix identifies this key.</div>
                  <div class="field" style="margin-top:var(--space-4)"><label class="form-label">Key</label><div class="input-row"><input class="form-input mono" readonly value="${esc(ct.api_key)}"><button class="btn btn-secondary" data-act="token-copy">${esc(st.copyLabel)}</button></div></div>
                  <div class="row-actions"><button class="btn btn-ghost" data-act="token-dismiss">Done</button></div></div>` : ''}
                <div class="table-wrap"><table class="data-table"><thead><tr><th>Name</th><th>Role</th><th>Key prefix</th><th>State</th><th>Created</th><th class="actions">Actions</th></tr></thead>
                  <tbody>${keyRows || `<tr><td colspan="6" style="color:var(--text-dim);text-align:center;padding:20px">${loading ? 'Loading…' : 'No API keys yet.'}</td></tr>`}</tbody></table></div>
                <div class="subhead">Browser session</div>
                <div class="field"><label class="form-label">Sign in with a key</label><div class="input-row"><input class="form-input mono" value="${esc(st.sessionKey)}" data-act="session-key" spellcheck="false" autocomplete="off" placeholder="lore_"><button class="btn btn-secondary" data-act="session-login">Sign in</button></div>
                  <div class="form-hint">Paste a <code>lore_</code> key for read-only browser access. Writes always stay token-only.</div></div>
              </section>
              <hr class="section-divider">

              <section id="lset-appearance">
                <div class="section-head"><div><div class="sec-kicker">03 — Appearance</div><h3 class="sec-title">View preferences</h3>
                  <p class="sec-sub">Local to this browser — these don't change the server configuration or affect other users.</p></div></div>
                <div class="form-grid">
                  <div class="field"><label class="form-label">Map label density</label>${this.dd('labelMode', st.labelMode, [{ value: 'smart', label: 'Smart' }, { value: 'all', label: 'All' }, { value: 'off', label: 'Off' }])}<div class="form-hint">How many node labels the knowledge map shows.</div></div>
                  <div class="field"><label class="form-label">Reader typeface</label>${this.dd('readerFont', st.readerFont, [{ value: 'serif', label: 'Serif' }, { value: 'sans', label: 'Sans' }])}<div class="form-hint">Font for long-form page reading.</div></div></div>
              </section>
              <hr class="section-divider">

              <section id="lset-overview">
                <div class="section-head"><div><div class="sec-kicker">04 — Overview</div><h3 class="sec-title">At a glance</h3><p class="sec-sub">A quick read on what's configured right now.</p></div></div>
                <div class="grid-3">
                  <div class="stat-card"><div class="stat-label">Pages</div><div class="stat-value">${this.s.pages.length}</div><div class="stat-sub">in the knowledge base</div></div>
                  <div class="stat-card"><div class="stat-label">Active keys</div><div class="stat-value">${activeKeys}</div><div class="stat-sub">${st.apiKeys.length} total · ${revokedN} revoked</div></div>
                  <div class="stat-card"><div class="stat-label">Provider</div><div class="stat-value" style="font-size:20px">${esc(providerShort)}</div><div class="stat-sub">${esc(f.model || '—')}</div></div></div>
                <div class="system-table">
                  <div class="system-row"><span class="system-label">Runtime override</span><span class="flow-chip ${keyOn ? 'chip-positive' : 'chip-neutral'}">${keyOn ? 'On' : 'Off'}</span></div>
                  <div class="system-row"><span class="system-label">Embeddings</span><span class="flow-chip ${embChip}">${embLabel}</span></div>
                  <div class="system-row"><span class="system-label">Escalation model</span><span class="system-value mono">${esc((saved.escalation_model || '').trim() || '—')}</span></div></div>
              </section>
            </div></main></div></div>`;
    }
  }

  function boot() {
    const root = document.getElementById('loreRoot');
    if (root) window.__lore = new LoreApp(root);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
