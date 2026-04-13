// API : window.__MEME_API_BASE__ (voir api-config.js) sinon même origine que la page ; local = localhost:8000
const BASE_URL = (() => {
    if (typeof window !== 'undefined' && window.__MEME_API_BASE__) {
        return String(window.__MEME_API_BASE__).replace(/\/$/, '');
    }
    if (typeof window === 'undefined') return 'http://localhost:8000';
    const { protocol, hostname, port } = window.location;
    if (!hostname || protocol === 'file:') return 'http://localhost:8000';
    const p = port ? `:${port}` : '';
    return `${protocol}//${hostname}${p}`;
})();
const API_URL = `${BASE_URL}/api`;
let walletAddress = null;
let portfolioChart = null;
let distributionChart = null;
let gainsChart = null;
/** Graphique dans la modale « Graphique » par token */
let tokenDetailChart = null;
let transactionsToShow = 5;  // État du nombre de transactions à afficher
let allTransactions = [];    // Cache de toutes les transactions

// Cache stale-while-revalidate : dernière réponse initial-load pour affichage instantané
let _cachedLoadData = null;
/** Wallet pour lequel _cachedLoadData est valide (évite d’afficher le cache d’une autre adresse). */
let _cachedForWallet = null;
/** True seulement après « Recalculer HIFO » + rechargement complet (dashboard + tx avec coûts HIFO). */
let _hifoDetailLoaded = false;
/** Dernier HIFO valide affiché (pendant import historique en fond, le serveur invalide le cache sans recalcul). */
let _lastStableHifo = null;
/** Réponse API brute (gain/perte HIFO) pendant un sync — appliquée à l’écran seulement en fin d’actualisation. */
let _pendingHifoTruth = null;

// --- Compte local (pseudo + mdp) + adresses enregistrées ---
function getAuthToken() {
    return localStorage.getItem('authToken');
}

function authHeaders(includeJsonContentType = true) {
    const h = {};
    if (includeJsonContentType) h['Content-Type'] = 'application/json';
    const t = getAuthToken();
    if (t) h['Authorization'] = `Bearer ${t}`;
    return h;
}

function updateAuthBar() {
    const out = document.getElementById('auth-logged-out');
    const inn = document.getElementById('auth-logged-in');
    const nameEl = document.getElementById('auth-display-name');
    if (!out || !inn) return;
    const t = getAuthToken();
    const un = localStorage.getItem('authUsername');
    if (t && un) {
        out.classList.add('hidden');
        inn.classList.remove('hidden');
        if (nameEl) nameEl.textContent = un;
    } else {
        out.classList.remove('hidden');
        inn.classList.add('hidden');
    }
}

function _escAttr(s) {
    return String(s)
        .replace(/&/g, '&amp;')
        .replace(/"/g, '&quot;')
        .replace(/</g, '&lt;');
}

function populateSavedWalletsDropdown(wallets, activeAddr) {
    const sel = document.getElementById('saved-wallets-select');
    if (!sel) return;
    const followed = (wallets || []).filter((w) => w.follows !== false && w.follows !== 0);
    if (!getAuthToken() || followed.length === 0) {
        sel.classList.add('hidden');
        sel.innerHTML = '<option value="">— Mes adresses —</option>';
        return;
    }
    sel.classList.remove('hidden');
    let html = '<option value="">— Mes adresses —</option>';
    const cur = activeAddr || walletAddress || '';
    for (const w of followed) {
        const a = w.address || '';
        if (!a) continue;
        const lab = w.label || `${a.slice(0, 4)}…${a.slice(-4)}`;
        const selected = cur && a === cur ? ' selected' : '';
        html += `<option value="${_escAttr(a)}"${selected}>${_escAttr(lab)}</option>`;
    }
    sel.innerHTML = html;
}

function _formatSyncDate(iso) {
    if (!iso) return '—';
    try {
        const s = String(iso).replace(' ', 'T');
        const d = new Date(s);
        if (Number.isNaN(d.getTime())) return iso;
        return d.toLocaleString('fr-FR', { dateStyle: 'short', timeStyle: 'short' });
    } catch {
        return iso;
    }
}

async function loadWalletFromDbOnly() {
    if (!walletAddress) return;
    await dbOnlyRefresh(true, true, false, true);
    const noW = document.getElementById('helius-no-wallet');
    const res = document.getElementById('helius-result');
    if (noW) noW.classList.add('hidden');
    if (res) res.classList.remove('hidden');
    applyChartLazyStateAndMaybeLoad();
}

/** Synchronise une adresse (Helius + soldes + prix), sans changer l’adresse affichée si ce n’est pas celle-ci. */
async function refreshSavedAddressOnChain(addr) {
    const a = (addr || '').trim();
    if (a.length < 32) {
        showNotification('Adresse invalide', 'error');
        return;
    }
    const ok = confirm(
        `Synchroniser la chaîne pour cette adresse ?\n\n${a.slice(0, 8)}…${a.slice(-6)}\n\nImport Helius, soldes et prix — cela peut prendre une minute.`
    );
    if (!ok) return;
    try {
        showNotification('Synchronisation en cours…', 'info');
        const checkRes = await fetch(`${API_URL}/helius/needs-import/${encodeURIComponent(a)}`);
        const check = checkRes.ok ? await checkRes.json() : { needs_import: true };
        const doImport = !!check.needs_import;
        if (doImport) {
            const iq = new URLSearchParams({ max_pages: '18', skip_post_import_prices: '1' });
            const ir = await fetch(`${API_URL}/helius/import-swaps/${encodeURIComponent(a)}?${iq}`, { method: 'POST' });
            const payload = ir.ok ? await ir.json() : {};
            if ((payload.imported_buys || 0) + (payload.imported_sales || 0) > 0) {
                showNotification(
                    `Import : ${payload.imported_buys || 0} achats, ${payload.imported_sales || 0} ventes`,
                    'success'
                );
            }
        }
        await fetch(`${API_URL}/sync-balances/${encodeURIComponent(a)}`, { method: 'POST' }).catch(() => {});
        const priceQs = new URLSearchParams({ wallet: a });
        if (!doImport) priceQs.set('quick', '1');
        await fetch(`${API_URL}/update-prices?${priceQs}`, { method: 'POST' }).catch(() => {});
        if (getAuthToken()) {
            await fetch(`${API_URL}/auth/wallets/sync-done`, {
                method: 'POST',
                headers: authHeaders(),
                body: JSON.stringify({ address: a }),
            }).catch(() => {});
        }
        await refreshAuthWalletsUi();
        if (document.getElementById('addresses-page') && !document.getElementById('addresses-page').classList.contains('hidden')) {
            await renderAddressesManagerPage();
        }
        if (walletAddress === a) {
            await dbOnlyRefresh(true, true, false, true);
        }
        showNotification('Synchronisation terminée pour cette adresse.', 'success');
    } catch (e) {
        console.error(e);
        showNotification('Erreur lors de la synchronisation', 'error');
    }
}

function showAddressesPage() {
    const ap = document.getElementById('addresses-page');
    const main = document.getElementById('main-content');
    const wel = document.getElementById('welcome-page');
    if (ap) ap.classList.remove('hidden');
    if (main) main.classList.add('hidden');
    if (wel) wel.classList.add('hidden');
    document.getElementById('nav-my-addresses-btn')?.classList.add('hidden');
    document.getElementById('nav-back-dashboard-btn')?.classList.remove('hidden');
    void renderAddressesManagerPage();
}

function showMainDashboardView() {
    const ap = document.getElementById('addresses-page');
    const main = document.getElementById('main-content');
    if (ap) ap.classList.add('hidden');
    document.getElementById('nav-back-dashboard-btn')?.classList.add('hidden');
    document.getElementById('nav-my-addresses-btn')?.classList.remove('hidden');
    if (walletAddress) {
        if (main) main.classList.remove('hidden');
        document.getElementById('welcome-page')?.classList.add('hidden');
        hideWelcomePage();
    } else {
        showWelcomePage();
    }
}

async function renderAddressesManagerPage() {
    const wrap = document.getElementById('addresses-page-content');
    if (!wrap) return;
    if (!getAuthToken()) {
        wrap.innerHTML = '<p class="text-amber-800 font-medium">Connectez-vous pour gérer vos adresses.</p>';
        return;
    }
    const me = await fetchAuthMe();
    if (!me) {
        wrap.innerHTML = '<p class="text-rose-700">Session invalide — reconnectez-vous.</p>';
        return;
    }
    const list = me.wallets || [];
    const rows =
        list.length === 0
            ? '<tr><td colspan="5" class="p-6 text-center text-teal-600">Aucune adresse enregistrée. Ajoutez-en une ci-dessous.</td></tr>'
            : list
                  .map((w) => {
                      const a = w.address || '';
                      const lab = w.label || '';
                      const fl = w.follows !== false && w.follows !== 0;
                      const sync = _formatSyncDate(w.last_synced_at);
                      const safe = _escAttr(a);
                      return `<tr class="border-b border-teal-100/80 align-top">
                <td class="py-3 pr-2"><input type="text" data-addr-label="${safe}" class="w-full max-w-[220px] px-2 py-1.5 border border-teal-200 rounded-lg text-sm" value="${_escAttr(lab)}" placeholder="Ex. Mon adresse" /></td>
                <td class="py-3 pr-2 font-mono text-[11px] sm:text-xs break-all text-teal-900">${safe}</td>
                <td class="py-3 pr-2 text-center"><input type="checkbox" data-addr-follow="${safe}" ${fl ? 'checked' : ''} title="Afficher dans le menu du haut" class="w-4 h-4 accent-teal-600" /></td>
                <td class="py-3 pr-2 text-sm text-teal-700 whitespace-nowrap">${_escAttr(sync)}</td>
                <td class="py-3 text-right">
                    <div class="flex flex-wrap justify-end gap-1.5">
                        <button type="button" class="text-xs bg-teal-600 hover:bg-teal-500 text-white px-2.5 py-1 rounded-lg font-medium" data-addr-open="${safe}">Ouvrir</button>
                        <button type="button" class="text-xs bg-sky-600 hover:bg-sky-500 text-white px-2.5 py-1 rounded-lg font-medium" data-addr-sync="${safe}">Actualiser la chaîne</button>
                        <button type="button" class="text-xs bg-violet-700 hover:bg-violet-600 text-white px-2.5 py-1 rounded-lg font-medium" data-addr-save="${safe}">Enregistrer nom</button>
                        <button type="button" class="text-xs bg-rose-600 hover:bg-rose-500 text-white px-2.5 py-1 rounded-lg font-medium" data-addr-del="${safe}">Retirer</button>
                    </div>
                </td>
            </tr>`;
                  })
                  .join('');

    wrap.innerHTML = `
    <div class="overflow-x-auto rounded-xl border border-teal-200/60 mb-6 bg-white/40">
      <table class="w-full text-left text-sm min-w-[640px]">
        <thead class="bg-teal-50 text-teal-900 font-semibold">
          <tr>
            <th class="p-3">Nom affiché</th>
            <th class="p-3">Adresse</th>
            <th class="p-3 text-center w-24">Suivie</th>
            <th class="p-3 w-40">Dernière synchro</th>
            <th class="p-3 text-right min-w-[200px]">Actions</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
    <div class="glass rounded-xl p-5 border border-cyan-200/50 bg-white/30">
      <h3 class="font-bold text-teal-800 mb-3 text-lg">Ajouter une adresse</h3>
      <div class="flex flex-col lg:flex-row gap-4 items-start lg:items-end flex-wrap">
        <div class="flex-1 min-w-[200px]">
          <label class="block text-xs text-teal-600 mb-1 font-medium">Adresse Solana</label>
          <input type="text" id="addr-add-address" class="px-3 py-2 border border-teal-200 rounded-lg font-mono text-sm w-full" placeholder="Collez l’adresse publique…" />
        </div>
        <div class="w-full sm:w-56">
          <label class="block text-xs text-teal-600 mb-1 font-medium">Nom (optionnel)</label>
          <input type="text" id="addr-add-label" class="px-3 py-2 border border-teal-200 rounded-lg text-sm w-full" placeholder="Mon wallet…" />
        </div>
        <label class="flex items-center gap-2 text-sm text-teal-800 cursor-pointer select-none">
          <input type="checkbox" id="addr-add-follow" checked class="w-4 h-4 accent-teal-600" /> Suivre dans le menu
        </label>
        <button type="button" id="addr-add-submit" class="btn-teal px-5 py-2.5 rounded-xl font-semibold text-sm shadow">Ajouter</button>
      </div>
    </div>`;

    const labelInputFor = (addr) =>
        [...wrap.querySelectorAll('input[data-addr-label]')].find((i) => i.getAttribute('data-addr-label') === addr);

    wrap.querySelectorAll('input[data-addr-follow]').forEach((cb) => {
        cb.addEventListener('change', async () => {
            const addr = cb.getAttribute('data-addr-follow');
            const r = await fetch(`${API_URL}/auth/wallets`, {
                method: 'PATCH',
                headers: authHeaders(),
                body: JSON.stringify({ address: addr, follows: cb.checked }),
            });
            if (!r.ok) {
                showNotification('Mise à jour impossible', 'error');
                cb.checked = !cb.checked;
                return;
            }
            await refreshAuthWalletsUi();
            showNotification(cb.checked ? 'Adresse suivie dans le menu' : 'Masquée du menu (toujours dans cette liste)', 'info');
        });
    });

    wrap.querySelectorAll('button[data-addr-save]').forEach((btn) => {
        btn.addEventListener('click', async () => {
            const addr = btn.getAttribute('data-addr-save');
            const inp = labelInputFor(addr);
            const label = inp ? inp.value.trim() : '';
            const r = await fetch(`${API_URL}/auth/wallets`, {
                method: 'PATCH',
                headers: authHeaders(),
                body: JSON.stringify({ address: addr, label }),
            });
            if (!r.ok) {
                showNotification('Sauvegarde impossible', 'error');
                return;
            }
            await refreshAuthWalletsUi();
            showNotification('Nom enregistré', 'success');
        });
    });

    wrap.querySelectorAll('button[data-addr-open]').forEach((btn) => {
        btn.addEventListener('click', async () => {
            const addr = btn.getAttribute('data-addr-open');
            const wi = document.getElementById('wallet-input');
            const ww = document.getElementById('welcome-wallet-input');
            if (wi) wi.value = addr;
            if (ww) ww.value = addr;
            showMainDashboardView();
            await applyWallet(addr, { preferDbOnly: true });
        });
    });

    wrap.querySelectorAll('button[data-addr-sync]').forEach((btn) => {
        btn.addEventListener('click', () => refreshSavedAddressOnChain(btn.getAttribute('data-addr-sync')));
    });

    wrap.querySelectorAll('button[data-addr-del]').forEach((btn) => {
        btn.addEventListener('click', async () => {
            const addr = btn.getAttribute('data-addr-del');
            if (!confirm(`Retirer cette adresse de votre compte ?\n\nLes données déjà importées restent sur ce serveur.`)) return;
            const r = await fetch(`${API_URL}/auth/wallets?address=${encodeURIComponent(addr)}`, {
                method: 'DELETE',
                headers: authHeaders(false),
            });
            if (!r.ok) {
                showNotification('Suppression impossible', 'error');
                return;
            }
            if (walletAddress === addr) {
                document.getElementById('change-wallet')?.click();
            }
            await refreshAuthWalletsUi();
            await renderAddressesManagerPage();
            showNotification('Adresse retirée du compte', 'success');
        });
    });

    document.getElementById('addr-add-submit')?.addEventListener('click', async () => {
        const addr = document.getElementById('addr-add-address')?.value?.trim() || '';
        const label = document.getElementById('addr-add-label')?.value?.trim() || '';
        const follows = !!document.getElementById('addr-add-follow')?.checked;
        if (addr.length < 32) {
            showNotification('Adresse trop courte', 'error');
            return;
        }
        const r = await fetch(`${API_URL}/auth/wallets`, {
            method: 'POST',
            headers: authHeaders(),
            body: JSON.stringify({ address: addr, label: label || null, follows }),
        });
        if (!r.ok) {
            const d = await r.json().catch(() => ({}));
            showNotification(d.detail || 'Ajout impossible', 'error');
            return;
        }
        document.getElementById('addr-add-address').value = '';
        document.getElementById('addr-add-label').value = '';
        await refreshAuthWalletsUi();
        await renderAddressesManagerPage();
        showNotification('Adresse ajoutée — utilisez « Actualiser la chaîne » pour importer les transactions.', 'success');
    });
}

async function fetchAuthMe() {
    const t = getAuthToken();
    if (!t) return null;
    const r = await fetch(`${API_URL}/auth/me`, { headers: authHeaders(false) });
    if (!r.ok) return null;
    return r.json();
}

async function refreshAuthWalletsUi() {
    const me = await fetchAuthMe();
    if (!me) {
        populateSavedWalletsDropdown([], null);
        return;
    }
    if (me.username) localStorage.setItem('authUsername', me.username);
    updateAuthBar();
    populateSavedWalletsDropdown(me.wallets || [], me.active_wallet || walletAddress);
}

async function authLogin() {
    const u = document.getElementById('auth-username')?.value?.trim();
    const p = document.getElementById('auth-password')?.value || '';
    if (!u || !p) {
        showNotification('Pseudo et mot de passe requis', 'error');
        return;
    }
    try {
        const r = await fetch(`${API_URL}/auth/login`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username: u, password: p }),
        });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) {
            const det = d.detail;
            showNotification(typeof det === 'string' ? det : 'Connexion impossible', 'error');
            return;
        }
        localStorage.setItem('authToken', d.token);
        localStorage.setItem('authUsername', d.username);
        updateAuthBar();
        const pw = document.getElementById('auth-password');
        if (pw) pw.value = '';
        await refreshAuthWalletsUi();
        showNotification(`Connecté : ${d.username}`, 'success');
    } catch (e) {
        console.error(e);
        showNotification('Erreur réseau', 'error');
    }
}

async function authRegister() {
    const u = document.getElementById('auth-username')?.value?.trim();
    const p = document.getElementById('auth-password')?.value || '';
    if (!u || !p) {
        showNotification('Pseudo et mot de passe requis', 'error');
        return;
    }
    try {
        const r = await fetch(`${API_URL}/auth/register`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username: u, password: p }),
        });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) {
            const det = d.detail;
            showNotification(typeof det === 'string' ? det : 'Inscription refusée', 'error');
            return;
        }
        localStorage.setItem('authToken', d.token);
        localStorage.setItem('authUsername', d.username);
        updateAuthBar();
        const pw = document.getElementById('auth-password');
        if (pw) pw.value = '';
        await refreshAuthWalletsUi();
        showNotification('Compte créé — vous êtes connecté', 'success');
    } catch (e) {
        console.error(e);
        showNotification('Erreur réseau', 'error');
    }
}

function authLogout() {
    const t = getAuthToken();
    if (t) {
        fetch(`${API_URL}/auth/logout`, { method: 'POST', headers: authHeaders(false) }).catch(() => {});
    }
    localStorage.removeItem('authToken');
    localStorage.removeItem('authUsername');
    updateAuthBar();
    populateSavedWalletsDropdown([], null);
    showNotification('Déconnecté', 'info');
}

// Graphiques dashboard : génération à la demande (préf. par wallet)
function _chartPrefsStorageKey() {
    return walletAddress ? `meme_tracker_charts_unlock_v1_${walletAddress}` : null;
}
function getChartUnlockState() {
    const k = _chartPrefsStorageKey();
    if (!k) return { gains: false, portfolio: false, distribution: false };
    try {
        const j = JSON.parse(localStorage.getItem(k) || '{}');
        return {
            gains: !!j.gains,
            portfolio: !!j.portfolio,
            distribution: !!j.distribution,
        };
    } catch {
        return { gains: false, portfolio: false, distribution: false };
    }
}
function setChartUnlock(which, value = true) {
    const k = _chartPrefsStorageKey();
    if (!k) return;
    const s = getChartUnlockState();
    if (which === 'gains' || which === 'portfolio' || which === 'distribution') s[which] = value;
    localStorage.setItem(k, JSON.stringify(s));
}

function _setChartPanelVisible(which, visible) {
    const map = {
        gains: { lazy: 'gains-chart-lazy', canvas: 'gainsChart' },
        portfolio: { lazy: 'portfolio-chart-lazy', canvas: 'portfolioChart' },
        distribution: { lazy: 'distribution-chart-lazy', canvas: 'distributionChart' },
    };
    const m = map[which];
    if (!m) return;
    const lazyEl = document.getElementById(m.lazy);
    const cv = document.getElementById(m.canvas);
    if (lazyEl) lazyEl.classList.toggle('hidden', visible);
    if (cv) cv.classList.toggle('hidden', !visible);
}

function _chartTokens() {
    const all = _cachedLoadData?.tokens || [];
    const active = all.filter(
        (t) => (t.current_tokens || 0) > 0 || (t.current_value || 0) >= 0.01
    );
    return sortTokensByCurrentValue(active);
}

/** Période glissante pour les graphiques évolution (gain net + portfolio). `null` = tout l'historique. */
const CHART_PERIOD_STORAGE_KEY = 'meme_tracker_chart_period_v1';

function chartPeriodDaysForApi() {
    const sel = document.getElementById('chart-period-select');
    const v = (sel && sel.value) || 'all';
    if (v === 'all') return null;
    const n = parseInt(v, 10);
    return Number.isFinite(n) && n > 0 ? n : null;
}

function initChartPeriodFromStorage() {
    const sel = document.getElementById('chart-period-select');
    if (!sel) return;
    try {
        const saved = localStorage.getItem(CHART_PERIOD_STORAGE_KEY);
        if (saved && ['all', '1', '7', '365'].includes(saved)) sel.value = saved;
    } catch (_) {
        /* ignore */
    }
}

function persistChartPeriod() {
    const sel = document.getElementById('chart-period-select');
    if (!sel) return;
    try {
        localStorage.setItem(CHART_PERIOD_STORAGE_KEY, sel.value);
    } catch (_) {
        /* ignore */
    }
}

function chartHistoryDaysQuery() {
    const d = chartPeriodDaysForApi();
    return d == null ? '' : `days=${d}`;
}

/** Affiche placeholders ou canvas selon les préférences ; recharge les graphiques déjà débloqués (après Actualiser / HIFO). */
function applyChartLazyStateAndMaybeLoad() {
    if (!walletAddress) return;
    const st = getChartUnlockState();
    ['gains', 'portfolio', 'distribution'].forEach((w) => _setChartPanelVisible(w, !!st[w]));
    const parts = [];
    if (st.gains) parts.push('gains');
    if (st.portfolio) parts.push('portfolio');
    if (st.distribution) parts.push('distribution');
    if (!parts.length) return;
    void updateCharts(_chartTokens(), parts).catch(() => {});
}

async function userGenerateDashboardChart(which) {
    if (!walletAddress) {
        showNotification('Connectez ou validez une adresse wallet.', 'error');
        return;
    }
    setChartUnlock(which, true);
    _setChartPanelVisible(which, true);
    try {
        await updateCharts(_chartTokens(), [which]);
    } catch (e) {
        console.error('Graphique:', e);
        showNotification('Erreur lors du chargement du graphique.', 'error');
    }
}

// === Barre de progression (globale + détaillée) + estimation temps restant ===
let _progressEtaSession = null; // { startMs: number }

function _formatEtaFr(seconds) {
    if (!Number.isFinite(seconds) || seconds <= 0) return '';
    const capSec = 90 * 60;
    const s = Math.min(seconds, capSec);
    if (s < 8) return 'quelques secondes';
    if (s < 55) return `${Math.round(s)} s`;
    const m = Math.floor(s / 60);
    const rs = Math.round(s - m * 60);
    if (rs <= 2 || m >= 15) return `${m} min`;
    return `${m} min ${rs} s`;
}

function _clearProgressEtaUi() {
    const etaEl = document.getElementById('sync-progress-eta');
    const etaOv = document.getElementById('first-load-eta');
    if (etaEl) {
        etaEl.textContent = '';
        etaEl.classList.add('hidden');
    }
    if (etaOv) {
        etaOv.textContent = '';
        etaOv.classList.add('hidden');
    }
}

function _setProgressEtaUi(etaPhrase) {
    const etaEl = document.getElementById('sync-progress-eta');
    const etaOv = document.getElementById('first-load-eta');
    const line = etaPhrase ? `Temps restant estimé : ${etaPhrase}` : '';
    if (etaEl) {
        etaEl.textContent = line;
        etaEl.classList.toggle('hidden', !line);
    }
    if (etaOv) {
        etaOv.textContent = line;
        etaOv.classList.toggle('hidden', !line);
    }
}

/**
 * @param {number} pct 0–100
 * @param {string} label
 * @param {boolean} [skipEta] true = pas d’estimation (ex. faux % pendant « Recalcul HIFO »)
 */
function _showProgress(pct, label, skipEta = false) {
    const pRounded = Math.min(100, Math.max(0, Number(pct) || 0));
    const now = Date.now();

    if (skipEta) {
        _progressEtaSession = null;
        _clearProgressEtaUi();
    } else {
        if (pRounded <= 0) {
            _progressEtaSession = { startMs: now };
            _clearProgressEtaUi();
        } else if (_progressEtaSession === null) {
            _progressEtaSession = { startMs: now };
        }
        if (pRounded >= 100) {
            _clearProgressEtaUi();
            _progressEtaSession = null;
        } else if (pRounded > 0) {
            const elapsedSec = (now - _progressEtaSession.startMs) / 1000;
            let etaPhrase = '';
            if (elapsedSec >= 1.8 && pRounded >= 3) {
                const remainingSec = ((100 - pRounded) / pRounded) * elapsedSec;
                if (remainingSec >= 2 && remainingSec < 95 * 60) {
                    etaPhrase = _formatEtaFr(remainingSec);
                }
            }
            if (etaPhrase) _setProgressEtaUi(etaPhrase);
            else _clearProgressEtaUi();
        }
    }

    const c = document.getElementById('sync-progress-container');
    const b = document.getElementById('sync-progress-bar');
    const p = document.getElementById('sync-progress-pct');
    const l = document.getElementById('sync-progress-label');
    if (c && b && p && l) {
        c.classList.remove('hidden');
        b.style.width = pRounded + '%';
        p.textContent = Math.round(pRounded) + '%';
        l.textContent = label || 'Chargement...';
    }
    const overlayBar = document.getElementById('first-load-progress-bar');
    const overlayStep = document.getElementById('first-load-step');
    if (overlayBar) overlayBar.style.width = pRounded + '%';
    if (overlayStep) overlayStep.textContent = label || 'Chargement...';
    const globalBar = document.getElementById('global-loading-bar');
    const globalFill = document.getElementById('global-loading-bar-fill');
    if (globalBar && globalFill) {
        globalBar.classList.remove('hidden');
        globalFill.style.width = pRounded + '%';
    }
}
function _hideProgress() {
    _progressEtaSession = null;
    _clearProgressEtaUi();
    const c = document.getElementById('sync-progress-container');
    if (c) c.classList.add('hidden');
    const globalBar = document.getElementById('global-loading-bar');
    if (globalBar) globalBar.classList.add('hidden');
}

// === Sync : import d'abord pour nouvelles adresses, puis chargement complet ===
let _syncInProgress = false;
let _syncAbortController = null;
/** Si une sync est déjà en cours, réutiliser la même promesse (évite applyWallet qui finit sans données). */
let _syncPromise = null;

/** Import historique Helius en plusieurs petits appels après l’aperçu initial (nouvelle adresse). */
let _historyImportInProgress = false;
let _historyImportAbortController = null;

const QUICK_FIRST_IMPORT_PAGES = 5;
const HISTORY_CHUNK_PAGES = 10;
const MAX_HISTORY_CHUNKS = 28;

function cancelLoadAndGoHome() {
    _historyImportAbortController?.abort();
    if (!_syncInProgress) return;
    _hifoDetailLoaded = false;
    _lastStableHifo = null;
    _pendingHifoTruth = null;
    _syncAbortController?.abort();
    _syncInProgress = false;
    _syncPromise = null;
    walletAddress = null;
    _cachedLoadData = null;
    _cachedForWallet = null;
    localStorage.removeItem('walletAddress');
    fetch(`${API_URL}/settings/wallet`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ wallet_address: '' })
    }).catch(() => {});
    document.getElementById('wallet-input').value = '';
    document.getElementById('wallet-info').classList.add('hidden');
    const connectBtn = document.getElementById('connect-wallet');
    if (connectBtn) {
        connectBtn.innerHTML = '<i class="fas fa-wallet mr-2"></i>Phantom';
        connectBtn.disabled = false;
    }
    showFirstLoadOverlay(false);
    _hideProgress();
    const validateBtn = document.getElementById('validate-wallet');
    const refreshBtn = document.getElementById('refresh-prices');
    if (validateBtn) { validateBtn.disabled = false; validateBtn.innerHTML = '<i class="fas fa-check mr-2"></i>Valider'; }
    if (refreshBtn) { refreshBtn.disabled = false; refreshBtn.innerHTML = '<i class="fas fa-sync-alt mr-2"></i>Actualiser'; }
    showWelcomePage();
    const tokensGrid = document.getElementById('tokens-grid');
    if (tokensGrid) tokensGrid.innerHTML = `
        <div class="col-span-3 glass rounded-2xl p-10 text-center text-gray-500">
            <i class="fas fa-wallet text-5xl mb-4 block"></i>
            <p class="text-lg">Connectez un wallet pour voir vos tokens.</p>
        </div>`;
    displayEmptyDashboard();
    displayEmptyCharts();
    showNotification('Chargement annulé', 'info');
}

function handleCancelBarClick() {
    if (_historyImportInProgress) {
        _historyImportAbortController?.abort();
        showNotification('Import d’historique arrêté (données déjà chargées conservées)', 'info');
        return;
    }
    cancelLoadAndGoHome();
}

function _startHistoryImportContinuation(walletAtStart) {
    if (!walletAtStart || walletAtStart !== walletAddress) return;
    _historyImportAbortController = new AbortController();
    _historyImportInProgress = true;
    const sig = _historyImportAbortController.signal;
    void (async () => {
        try {
            let chunk = 0;
            let more = true;
            while (more && chunk < MAX_HISTORY_CHUNKS && walletAddress === walletAtStart && !sig.aborted) {
                chunk += 1;
                _showProgress(Math.min(97, 72 + chunk * 1.5), `Historique blockchain — étape ${chunk} (petits lots)…`);
                const q = new URLSearchParams({
                    max_pages: String(HISTORY_CHUNK_PAGES),
                    resume_history: '1',
                    skip_post_import_prices: '1',
                });
                const ir = await fetch(
                    `${API_URL}/helius/import-swaps/${encodeURIComponent(walletAtStart)}?${q}`,
                    { method: 'POST', signal: sig }
                );
                const j = ir.ok ? await ir.json() : {};
                more = !!j.may_have_more_history;
                if (j.skipped) break;
                await fetch(`${API_URL}/sync-balances/${walletAtStart}`, { method: 'POST', signal: sig }).catch(() => {});
                if (chunk % 2 === 0) {
                    await fetch(`${API_URL}/update-prices?wallet=${encodeURIComponent(walletAtStart)}`, {
                        method: 'POST',
                        signal: sig,
                    }).catch(() => {});
                }
                if (walletAddress === walletAtStart) {
                    await dbOnlyRefresh(true, true, false, true);
                }
            }
            _historyImportInProgress = false;
            if (walletAddress === walletAtStart && !sig.aborted) {
                await fetch(`${API_URL}/update-prices?wallet=${encodeURIComponent(walletAtStart)}`, {
                    method: 'POST',
                    signal: sig,
                }).catch(() => {});
                await dbOnlyRefresh(true, true, false, true);
            }
            if (walletAddress === walletAtStart && !sig.aborted) {
                showNotification('Import d’historique terminé', 'success');
            }
        } catch (e) {
            if (e?.name !== 'AbortError') console.error('Historique Helius :', e);
        } finally {
            _historyImportInProgress = false;
            _historyImportAbortController = null;
            if (walletAddress === walletAtStart) {
                _hideProgress();
                heliusTransfers().catch(() => {});
            }
        }
    })();
}

// mode: 'valider' = si pas en BDD → import complet, sinon juste valeurs | 'actualiser' = compare dernière tx, import si delta
async function syncWithProgress(isFirstConnection = false, mode = 'valider') {
    if (!walletAddress) return;
    if (_syncPromise) return _syncPromise;

    _syncPromise = (async () => {
    _syncInProgress = true;
    const validateBtn = document.getElementById('validate-wallet');
    const refreshBtn = document.getElementById('refresh-prices');
    const origValidate = validateBtn ? validateBtn.innerHTML : '';
    const origRefresh = refreshBtn ? refreshBtn.innerHTML : '';
    if (validateBtn) { validateBtn.disabled = true; validateBtn.innerHTML = '<i class="fas fa-spinner fa-spin mr-2"></i>Chargement...'; }
    if (refreshBtn) { refreshBtn.disabled = true; refreshBtn.innerHTML = '<i class="fas fa-spinner fa-spin mr-2"></i>Chargement...'; }

    _showProgress(0, 'Connexion au serveur...');
    _syncAbortController = new AbortController();
    const signal = _syncAbortController.signal;

    try {
        const hasCache = !!_cachedLoadData && (_cachedLoadData.tokens?.length || 0) > 0 && _cachedForWallet === walletAddress;
        let doImport = false;
        let dejaEnBdd = false;

        if (mode === 'valider') {
            const tokensRes = await fetch(`${API_URL}/tokens?wallet=${encodeURIComponent(walletAddress)}`);
            const tokens = await tokensRes.json();
            dejaEnBdd = tokens && tokens.length > 0;
            doImport = !dejaEnBdd;
            if (dejaEnBdd) {
                _showProgress(10, 'Adresse déjà en base — affichage enregistré…');
            } else {
                _showProgress(10, 'Import de toutes les transactions et bénéfices...');
            }
        } else {
            const checkRes = await fetch(`${API_URL}/helius/needs-import/${walletAddress}`);
            const check = checkRes.ok ? await checkRes.json() : { needs_import: true };
            doImport = check.needs_import;
            if (!doImport) {
                _showProgress(10, 'Dernière tx identique — pas de nouvel import');
            } else {
                _showProgress(10, 'Nouvelles transactions — import en cours...');
            }
        }

        if (isFirstConnection && !(mode === 'valider' && dejaEnBdd)) {
            showFirstLoadOverlay(true);
        }

        /** Valider + wallet déjà connu : BDD / cache uniquement — pas de chaîne, pas de prix API (cf. Actualiser). */
        if (mode === 'valider' && dejaEnBdd) {
            if (hasCache) {
                _showProgress(15, 'Restauration de l’affichage…');
                allTransactions = _cachedLoadData.transactions || [];
                renderDashboard(_cachedLoadData.dashboard, allTransactions);
                await renderTokens(_cachedLoadData.tokens);
                renderTransactions(allTransactions);
            } else {
                _showProgress(20, 'Lecture base de données…');
                await dbOnlyRefresh(false, true, false, true);
            }
            document.getElementById('helius-no-wallet').classList.add('hidden');
            document.getElementById('helius-result').classList.remove('hidden');
            _showProgress(95, 'Prêt…');
            applyChartLazyStateAndMaybeLoad();
            _showProgress(100, 'Prêt');
            showNotification(
                'Données affichées depuis la base. Utilisez « Actualiser » pour synchroniser la chaîne, les prix et les nouvelles transactions.',
                'info'
            );
            if (isFirstConnection) showFirstLoadOverlay(false);
            return { dbOnly: true };
        }

        if (hasCache) {
            _showProgress(5, 'Affichage des données en cache...');
            allTransactions = _cachedLoadData.transactions || [];
            renderDashboard(_cachedLoadData.dashboard, allTransactions);
            await renderTokens(_cachedLoadData.tokens);
            renderTransactions(allTransactions);
        }

        let importPayload = null;
        /** Nouvelle adresse (pas de cache UI) : petit import puis complément en arrière-plan. */
        const useQuickFirstImport = doImport && mode === 'valider' && !hasCache;

        if (doImport) {
            const isFirstImport = !hasCache;
            if (useQuickFirstImport) {
                _showProgress(
                    24,
                    `Import rapide (${QUICK_FIRST_IMPORT_PAGES} pages max) — la suite se fera en petites étapes…`
                );
                const qs = new URLSearchParams({
                    max_pages: String(QUICK_FIRST_IMPORT_PAGES),
                    skip_post_import_prices: '1',
                });
                const importRes = await fetch(
                    `${API_URL}/helius/import-swaps/${walletAddress}?${qs}`,
                    { method: 'POST', signal }
                );
                importPayload = importRes.ok ? await importRes.json() : {};
                if (!importPayload.skipped && !importPayload.detail) {
                    if (importPayload.imported_buys > 0 || importPayload.imported_sales > 0) {
                        showNotification(
                            `${importPayload.imported_buys || 0} achats, ${importPayload.imported_sales || 0} ventes (aperçu)`,
                            'success'
                        );
                    }
                    if (importPayload.may_have_more_history) {
                        showNotification(
                            'L’historique complet se charge en arrière-plan (barre de progression en haut). Vous pouvez déjà naviguer.',
                            'info'
                        );
                    }
                }
            } else {
                const maxPages = isFirstImport ? 22 : 15;
                _showProgress(25, `Import blockchain (max ${maxPages} pages Helius)…`);
                const iq = new URLSearchParams({
                    max_pages: String(maxPages),
                    skip_post_import_prices: '1',
                });
                const importRes = await fetch(
                    `${API_URL}/helius/import-swaps/${walletAddress}?${iq}`,
                    { method: 'POST', signal }
                );
                importPayload = importRes.ok ? await importRes.json() : {};
                if (!importPayload.skipped && !importPayload.detail) {
                    if (importPayload.imported_buys > 0 || importPayload.imported_sales > 0) {
                        showNotification(
                            `${importPayload.imported_buys || 0} achats, ${importPayload.imported_sales || 0} ventes importés`,
                            'success'
                        );
                    }
                }
            }
        } else {
            _showProgress(45, 'Pas d’import — soldes & prix…');
        }

        _showProgress(50, 'Synchronisation des soldes on-chain…');
        await fetch(`${API_URL}/sync-balances/${walletAddress}`, { method: 'POST', signal }).catch(() => {});

        const quickPriceUpdate = !doImport;
        _showProgress(
            55,
            quickPriceUpdate
                ? 'Prix (mode rapide : seulement les positions ouvertes)…'
                : 'Prix des tokens (Jupiter / DexScreener)…'
        );
        const priceQs = new URLSearchParams({ wallet: walletAddress });
        if (quickPriceUpdate) priceQs.set('quick', '1');
        await fetch(`${API_URL}/update-prices?${priceQs}`, { method: 'POST', signal }).catch(() => {});

        _showProgress(68, 'Chargement du dashboard…');
        const preserveTxList = !doImport;
        // Pas d’import → pas de no_cache : cache serveur long si HIFO en BDD, chiffres stables sans recalcul permanent
        await dbOnlyRefresh(doImport, true, preserveTxList, true);

        if (useQuickFirstImport) {
            showFirstLoadOverlay(false);
        }

        document.getElementById('helius-no-wallet').classList.add('hidden');
        document.getElementById('helius-result').classList.remove('hidden');

        if (useQuickFirstImport && importPayload?.may_have_more_history) {
            _startHistoryImportContinuation(walletAddress);
        }

        _showProgress(92, 'Finalisation…');
        void heliusTransfers().catch(() => {});

        applyChartLazyStateAndMaybeLoad();

        if (importPayload?.may_have_more_history && useQuickFirstImport) {
            _showProgress(100, 'Prêt — historique encore en cours (voir la barre ci-dessus)');
        } else {
            _showProgress(100, 'C\'est prêt !');
        }
        if (!importPayload?.may_have_more_history || !useQuickFirstImport) {
            showNotification('Synchronisation terminée', 'success');
        }
        return { dbOnly: false };
    } catch (error) {
        if (error?.name === 'AbortError') return undefined;
        console.error('Erreur sync:', error);
        showNotification('Erreur lors de la synchronisation', 'error');
        // Charger quand même les données BDD existantes
        try {
            await dbOnlyRefresh(true, true);
        } catch (e) {
            console.error('Erreur dbOnlyRefresh après sync:', e);
        }
    } finally {
        _syncInProgress = false;
        _syncPromise = null;
        _flushPendingHifoTruthAfterSync();
        setTimeout(() => {
            if (!_historyImportInProgress) _hideProgress();
        }, 500);
        if (isFirstConnection) showFirstLoadOverlay(false);
        if (validateBtn) { validateBtn.disabled = false; validateBtn.innerHTML = origValidate; }
        if (refreshBtn) { refreshBtn.disabled = false; refreshBtn.innerHTML = origRefresh; }
    }
    })();

    return _syncPromise;
}

// === Overlay première connexion + stats éphémères ===
let _ephemeralStats = [];
function _pushEphemeralStat(text) {
    if (!text) return;
    _ephemeralStats.push(text);
    const el = document.getElementById('ephemeral-stats');
    if (el) {
        const div = document.createElement('div');
        div.className = 'ephemeral-stat-item';
        div.textContent = '✓ ' + text;
        el.appendChild(div);
        setTimeout(() => div.classList.add('ephemeral-fade-out'), 3000);
        setTimeout(() => div.remove(), 4500);
    }
}
function showFirstLoadOverlay(show) {
    const overlay = document.getElementById('first-load-overlay');
    if (!overlay) return;
    if (show) {
        overlay.classList.remove('hidden');
        document.getElementById('ephemeral-stats').innerHTML = '';
        _ephemeralStats = [];
    } else {
        overlay.classList.add('fade-out');
        setTimeout(() => {
            overlay.classList.add('hidden');
            overlay.classList.remove('fade-out');
        }, 400);
    }
}

// === VALIDER : si pas en BDD → import complet. Sinon → BDD/cache seulement (chaîne & prix via Actualiser) ===
async function importFromBlockchain() {
    if (!walletAddress) return;
    const r = await syncWithProgress(!_cachedLoadData, 'valider');
    if (r && r.dbOnly) return;
    fetch(`${API_URL}/helius/balances/${walletAddress}`)
        .then(res => (res.ok ? res.json() : null))
        .then(data => {
            if (data && !data.detail) {
                showNotification(`Solana connecté — ${(data.sol_balance || 0).toFixed(4)} SOL`, 'success');
            }
        })
        .catch(() => {});
}

// === ACTUALISER : compare dernière tx BDD vs Helius → import si delta → nouveaux prix ===
async function actualiser() {
    await syncWithProgress(false, 'actualiser');
}

function _applyStableHifoOverlay(dashboard) {
    if (!dashboard || !walletAddress) return dashboard;
    const keepStableDuringLoad = _historyImportInProgress || _syncInProgress;
    if (
        keepStableDuringLoad &&
        _lastStableHifo &&
        _lastStableHifo.wallet === walletAddress &&
        dashboard.hifo_pending
    ) {
        const z = (dashboard.realized_gain ?? 0) === 0 && (dashboard.realized_loss ?? 0) === 0;
        if (z) {
            return {
                ...dashboard,
                realized_gain: _lastStableHifo.rg,
                realized_loss: _lastStableHifo.rl,
                hifo_pending: false,
            };
        }
    }
    return dashboard;
}

/** Après Valider/Actualiser/auto-refresh : réaffiche les chiffres HIFO réels du serveur (sans flash pendant le sync). */
function _flushPendingHifoTruthAfterSync() {
    if (!_pendingHifoTruth || !walletAddress || !_cachedLoadData?.dashboard) {
        _pendingHifoTruth = null;
        return;
    }
    const d = _cachedLoadData.dashboard;
    let rg = _pendingHifoTruth.realized_gain;
    let rl = _pendingHifoTruth.realized_loss;
    let pend = _pendingHifoTruth.hifo_pending;
    // Ne pas écraser un HIFO déjà affiché par des zéros « pending » (bug visuel après ~45 s / fin de sync).
    if (
        pend &&
        (rg ?? 0) === 0 &&
        (rl ?? 0) === 0 &&
        _lastStableHifo &&
        _lastStableHifo.wallet === walletAddress &&
        ((_lastStableHifo.rg ?? 0) !== 0 || (_lastStableHifo.rl ?? 0) !== 0)
    ) {
        rg = _lastStableHifo.rg;
        rl = _lastStableHifo.rl;
    }
    d.realized_gain = rg;
    d.realized_loss = rl;
    d.hifo_pending = pend;
    _pendingHifoTruth = null;
    renderDashboard(d, allTransactions);
}

// Chargement depuis la BDD — 1 seul appel API (initial-load) au lieu de 3
// forceRefresh: bypass cache dashboard (SOL, etc.) — true seulement après import / Actualiser explicite
// skipHifo: true = pas de simulation HIFO (rapide, Valider/Actualiser) ; false = après « Recalculer HIFO »
// preserveTxList: si true et pas d’import, skip_txs=1 (gain net sur « Actualiser » sans nouvelles tx)
// skipHeliusTransfers: évite doublon quand l’appelant refait heliusTransfers() juste après
async function dbOnlyRefresh(forceRefresh = false, skipHifo = true, preserveTxList = false, skipHeliusTransfers = false) {
    if (!walletAddress) return;
    try {
        const params = new URLSearchParams();
        params.set('wallet', walletAddress);
        params.set('tx_limit', '500');
        if (forceRefresh) params.set('no_cache', '1');
        if (skipHifo) params.set('skip_hifo', '1');
        if (preserveTxList) params.set('skip_txs', '1');
        const url = `${API_URL}/initial-load?${params}`;
        const res = await fetch(url);
        const data = await res.json();
        if (data.detail) throw new Error(data.detail);

        if (data.dashboard) {
            if (_syncInProgress) {
                _pendingHifoTruth = {
                    realized_gain: data.dashboard.realized_gain,
                    realized_loss: data.dashboard.realized_loss,
                    hifo_pending: data.dashboard.hifo_pending,
                };
            }
            data.dashboard = _applyStableHifoOverlay({ ...data.dashboard });
            const rgNow = data.dashboard.realized_gain ?? 0;
            const rlNow = data.dashboard.realized_loss ?? 0;
            if (!data.dashboard.hifo_pending) {
                _lastStableHifo = { wallet: walletAddress, rg: rgNow, rl: rlNow };
            } else if (rgNow !== 0 || rlNow !== 0) {
                // Cache HIFO présent mais empreinte obsolète : garder les montants affichés côté client
                _lastStableHifo = { wallet: walletAddress, rg: rgNow, rl: rlNow };
            }
        }

        // HIFO en BDD (après « Recalculer HIFO ») : skip_hifo=1 suffit si hifo_pending est faux
        _hifoDetailLoaded = !skipHifo || !!(data.dashboard && !data.dashboard.hifo_pending);
        if (preserveTxList && (!data.transactions || data.transactions.length === 0)) {
            _cachedLoadData = { ...data, transactions: allTransactions };
            renderDashboard(data.dashboard, allTransactions);
        } else {
            allTransactions = data.transactions || [];
            _cachedLoadData = data;
            renderDashboard(data.dashboard, allTransactions);
        }
        _cachedForWallet = walletAddress;
        await renderTokens(data.tokens);
        renderTransactions(allTransactions);

        document.getElementById('helius-no-wallet').classList.add('hidden');
        document.getElementById('helius-result').classList.remove('hidden');
        if (!skipHeliusTransfers) heliusTransfers().catch(() => {});
        queueMicrotask(() => applyChartLazyStateAndMaybeLoad());
    } catch (error) {
        console.error('Erreur dbOnlyRefresh :', error);
        showNotification('Erreur lors du chargement depuis la BDD', 'error');
    }
}

// Mise à jour rapide : prix + UI, sans ré-import Helius
// doSyncBalances: true = appeler sync-balances (après import), false = skip (plus rapide)
async function quickRefresh(doSyncBalances = true) {
    try {
        const priceParams = new URLSearchParams();
        priceParams.set('quick', '1');
        if (walletAddress) priceParams.set('wallet', walletAddress);
        const updatePromises = [
            fetch(`${API_URL}/update-prices?${priceParams}`, { method: 'POST' }).catch(e =>
                console.warn('Update-prices ignoré :', e.message)
            )
        ];
        if (doSyncBalances) {
            updatePromises.push(
                fetch(`${API_URL}/sync-balances/${walletAddress}`, { method: 'POST' })
                    .catch(e => console.warn('Sync balances ignoré :', e.message))
            );
        }
        await Promise.all(updatePromises);

        await dbOnlyRefresh(true, true, false, true);
        await loadTokens();

        document.getElementById('helius-no-wallet').classList.add('hidden');
        document.getElementById('helius-result').classList.remove('hidden');
        heliusTransfers().catch(() => {});
    } catch (error) {
        console.error('Erreur quickRefresh :', error);
        showNotification('Erreur lors de la mise à jour', 'error');
    }
}

// Afficher/masquer page d'accueil vs contenu principal
function showWelcomePage() {
    const w = document.getElementById('welcome-page');
    const m = document.getElementById('main-content');
    if (w) w.classList.remove('hidden');
    if (m) m.classList.add('hidden');
}
function hideWelcomePage() {
    const w = document.getElementById('welcome-page');
    const m = document.getElementById('main-content');
    if (w) w.classList.add('hidden');
    if (m) m.classList.remove('hidden');
}

// Applique l'adresse wallet (UI). Par défaut : import/sync si besoin. preferDbOnly : lecture BDD seulement (changement de liste, « Ouvrir » depuis Mes adresses).
async function applyWallet(address, options = {}) {
    _cachedLoadData = null;
    _cachedForWallet = null;
    _hifoDetailLoaded = false;
    _lastStableHifo = null;
    _pendingHifoTruth = null;
    walletAddress = address;
    localStorage.setItem('walletAddress', address);
    hideWelcomePage();
    if (getAuthToken()) {
        const h = authHeaders();
        fetch(`${API_URL}/auth/set-active-wallet`, {
            method: 'POST',
            headers: h,
            body: JSON.stringify({ address }),
        }).catch(() => {});
        fetch(`${API_URL}/auth/wallets`, {
            method: 'POST',
            headers: h,
            body: JSON.stringify({ address }),
        }).catch(() => {});
        void refreshAuthWalletsUi();
    }
    fetch(`${API_URL}/settings/wallet`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ wallet_address: address })
    }).catch(err => console.error('Erreur sauvegarde wallet BDD:', err));
    document.getElementById('wallet-address').textContent =
        address.substring(0, 4) + '...' + address.substring(address.length - 4);
    document.getElementById('wallet-info').classList.remove('hidden');

    if (options.preferDbOnly) {
        await loadWalletFromDbOnly();
        return;
    }
    await importFromBlockchain();
}

// === WALLET CONNECTION ===

// Valider : charge les données depuis la BDD uniquement (instantané)
document.getElementById('validate-wallet').addEventListener('click', async () => {
    const address = document.getElementById('wallet-input').value.trim();
    if (!address) {
        showNotification('Veuillez entrer une adresse wallet', 'error');
        return;
    }
    if (address.length < 20) {
        showNotification('Adresse wallet trop courte', 'error');
        return;
    }
    await applyWallet(address);
});

document.getElementById('auth-login-btn')?.addEventListener('click', () => void authLogin());
document.getElementById('auth-register-btn')?.addEventListener('click', () => void authRegister());
document.getElementById('auth-logout-btn')?.addEventListener('click', () => authLogout());
document.getElementById('saved-wallets-select')?.addEventListener('change', async (e) => {
    const v = e.target?.value?.trim();
    if (!v) return;
    const wi = document.getElementById('wallet-input');
    const ww = document.getElementById('welcome-wallet-input');
    if (wi) wi.value = v;
    if (ww) ww.value = v;
    showMainDashboardView();
    await applyWallet(v, { preferDbOnly: true });
});

// Bouton pour changer d'adresse
document.getElementById('change-wallet').addEventListener('click', () => {
    _historyImportAbortController?.abort();
    _hideProgress();
    walletAddress = null;
    _cachedLoadData = null;
    _cachedForWallet = null;
    _hifoDetailLoaded = false;
    _lastStableHifo = null;
    _pendingHifoTruth = null;
    localStorage.removeItem('walletAddress');
    // Effacer en BDD aussi
    fetch(`${API_URL}/settings/wallet`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ wallet_address: '' })
    }).catch(err => console.error('Erreur effacement wallet BDD:', err));
    document.getElementById('wallet-input').value = '';
    document.getElementById('wallet-info').classList.add('hidden');
    document.getElementById('connect-wallet').innerHTML = '<i class="fas fa-wallet mr-2"></i>Phantom';
    document.getElementById('connect-wallet').disabled = false;
    document.getElementById('wallet-input').focus();
    showWelcomePage();
    // Vider la grille de tokens
    document.getElementById('tokens-grid').innerHTML = `
        <div class="col-span-3 glass rounded-2xl p-10 text-center text-gray-500">
            <i class="fas fa-wallet text-5xl mb-4 block"></i>
            <p class="text-lg">Connectez un wallet pour voir vos tokens.</p>
        </div>`;
    displayEmptyDashboard();
    displayEmptyCharts();
    showNotification('Adresse effacée. Entrez une nouvelle adresse.', 'info');
});

// Boutons annuler pendant le chargement
document.getElementById('cancel-load-btn')?.addEventListener('click', cancelLoadAndGoHome);
document.getElementById('cancel-load-btn-bar')?.addEventListener('click', handleCancelBarClick);

// Connexion via Phantom Wallet
document.getElementById('connect-wallet').addEventListener('click', async () => {
    try {
        if (window.solana && window.solana.isPhantom) {
            const response = await window.solana.connect();
            walletAddress = response.publicKey.toString();
            localStorage.setItem('walletAddress', walletAddress);

            document.getElementById('wallet-input').value = walletAddress;
            document.getElementById('connect-wallet').innerHTML = 
                '<i class="fas fa-check-circle mr-2"></i>Connecté';
            document.getElementById('connect-wallet').disabled = true;
            applyWallet(walletAddress);
            showNotification('Wallet connecté et sauvegardé!', 'success');
        } else {
            showNotification('Phantom Wallet non détecté. Installez l\'extension!', 'error');
            window.open('https://phantom.app/', '_blank');
        }
    } catch (err) {
        console.error('Erreur de connexion:', err);
        showNotification('Erreur lors de la connexion au wallet', 'error');
    }
});

// =============================================================================
// === HELIUS — Fonctions frontend ===
// =============================================================================

function _checkWalletForHelius() {
    const noWalletMsg = document.getElementById('helius-no-wallet');
    const resultDiv   = document.getElementById('helius-result');
    if (!walletAddress) {
        noWalletMsg.classList.remove('hidden');
        resultDiv.classList.add('hidden');
        return false;
    }
    noWalletMsg.classList.add('hidden');
    return true;
}

function _heliusStartLoading() {
    const resultDiv = document.getElementById('helius-result');
    const loading   = document.getElementById('helius-loading');
    const content   = document.getElementById('helius-content');
    resultDiv.classList.remove('hidden');
    loading.classList.remove('hidden');
    content.innerHTML = '';
}

function _heliusStopLoading() {
    document.getElementById('helius-loading').classList.add('hidden');
}

// ── Soldes SOL + tokens ───────────────────────────────────────────────────
async function heliusBalances() {
    if (!_checkWalletForHelius()) return;
    _heliusStartLoading();
    try {
        const res  = await fetch(`${API_URL}/helius/balances/${walletAddress}`);
        const data = await res.json();
        _heliusStopLoading();
        if (data.detail) { showNotification(data.detail, 'error'); return; }

        const tokens = (data.tokens || []).slice(0, 30);
        document.getElementById('helius-content').innerHTML = `
            <h3 class="text-lg font-bold text-teal-700 mb-3">
                <i class="fas fa-wallet mr-2"></i>Soldes — ${walletAddress.substring(0,6)}...${walletAddress.slice(-4)}
            </h3>
            <div class="mb-4 bg-indigo-50 rounded-xl px-5 py-4 inline-flex items-center gap-4">
                <i class="fas fa-sun text-amber-500 text-2xl"></i>
                <div>
                    <p class="text-sm text-gray-500">Solde SOL</p>
                    <p class="text-2xl font-bold text-indigo-700">${data.sol_balance.toFixed(4)} SOL</p>
                </div>
            </div>
            ${ tokens.length === 0 ? '<p class="text-gray-400 mt-2">Aucun token SPL dans ce wallet.</p>' : `
            <div class="overflow-x-auto mt-2">
                <table class="w-full text-sm">
                    <thead class="table-header-teal">
                        <tr>
                            <th class="px-4 py-2 text-left">Mint</th>
                            <th class="px-4 py-2 text-right">Montant</th>
                            <th class="px-4 py-2 text-right">Décimales</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${tokens.map((t,i) => `
                        <tr class="${i%2===0?'bg-white':'bg-teal-50'}">
                            <td class="px-4 py-2 font-mono text-xs">${t.mint}</td>
                            <td class="px-4 py-2 text-right font-semibold">${(t.amount/(10**(t.decimals||0))).toFixed(4)}</td>
                            <td class="px-4 py-2 text-right text-gray-400">${t.decimals}</td>
                        </tr>`).join('')}
                    </tbody>
                </table>
            </div>`}
        `;
    } catch(e) {
        _heliusStopLoading();
        showNotification('Erreur Helius balances: ' + e.message, 'error');
    }
}

// ── Activité récente ──────────────────────────────────────────────────────
async function heliusActivity() {
    if (!_checkWalletForHelius()) return;
    _heliusStartLoading();
    try {
        const res  = await fetch(`${API_URL}/helius/activity/${walletAddress}?limit=20`);
        const data = await res.json();
        _heliusStopLoading();
        if (data.detail) { showNotification(data.detail, 'error'); return; }

        const txs = data.transactions || [];
        const typeColor = t => ({ SWAP:'indigo', TRANSFER:'sky', NFT_SALE:'amber', BURN:'rose' }[t] || 'gray');
        document.getElementById('helius-content').innerHTML = `
            <h3 class="text-lg font-bold text-teal-700 mb-3">
                <i class="fas fa-stream mr-2"></i>Dernières transactions (${txs.length})
            </h3>
            ${ txs.length === 0 ? '<p class="text-gray-400">Aucune transaction trouvée.</p>' : `
            <div class="space-y-2 max-h-[420px] overflow-y-auto pr-1">
                ${txs.map(tx => `
                <div class="flex items-start gap-3 bg-white rounded-xl px-4 py-3 shadow-sm border border-gray-100">
                    <span class="mt-1 px-2 py-0.5 rounded-full text-xs font-bold bg-${typeColor(tx.type)}-100 text-${typeColor(tx.type)}-700 shrink-0">${tx.type||'?'}</span>
                    <div class="flex-1 min-w-0">
                        <p class="text-sm font-medium text-gray-700 truncate">${tx.description || tx.signature?.substring(0,24)+'…'}</p>
                        <p class="text-xs text-gray-400 mt-0.5">${formatDateFr(tx.date || (tx.timestamp ? tx.timestamp * 1000 : null))} &bull; Frais: ${(tx.fee_sol||0).toFixed(5)} SOL &bull; Source: ${tx.source||'?'}</p>
                    </div>
                    <a href="https://solscan.io/tx/${tx.signature}" target="_blank" class="text-teal-400 hover:text-teal-600 shrink-0 mt-1" title="Voir sur Solscan">
                        <i class="fas fa-external-link-alt text-xs"></i>
                    </a>
                </div>`).join('')}
            </div>`}
        `;
    } catch(e) {
        _heliusStopLoading();
        showNotification('Erreur Helius activité: ' + e.message, 'error');
    }
}

// ── Import swaps → BDD ───────────────────────────────────────────────────
async function heliusImportSwaps() {
    if (!_checkWalletForHelius()) return;
    _heliusStartLoading();
    document.getElementById('helius-content').innerHTML = `
        <p class="text-teal-600 font-medium animate-pulse">
            <i class="fas fa-sync-alt fa-spin mr-2"></i>
            Récupération de vos swaps en cours (peut prendre ~10s)...
        </p>
    `;
    try {
        const res  = await fetch(`${API_URL}/helius/import-swaps/${walletAddress}?max_pages=5`, { method: 'POST' });
        const data = await res.json();
        _heliusStopLoading();
        if (data.detail) { showNotification(data.detail, 'error'); return; }

        document.getElementById('helius-content').innerHTML = `
            <h3 class="text-lg font-bold text-emerald-600 mb-4">
                <i class="fas fa-check-circle mr-2"></i>Import terminé !
            </h3>
            <div class="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-4">
                <div class="bg-indigo-50 rounded-xl p-4 text-center">
                    <p class="text-xs text-gray-500 mb-1">Swaps analysés</p>
                    <p class="text-3xl font-bold text-indigo-700">${data.swaps_analysed}</p>
                </div>
                <div class="bg-emerald-50 rounded-xl p-4 text-center">
                    <p class="text-xs text-gray-500 mb-1">Achats importés</p>
                    <p class="text-3xl font-bold text-emerald-600">${data.imported_buys}</p>
                </div>
                <div class="bg-amber-50 rounded-xl p-4 text-center">
                    <p class="text-xs text-gray-500 mb-1">Ventes importées</p>
                    <p class="text-3xl font-bold text-amber-600">${data.imported_sales}</p>
                </div>
            </div>
            ${ data.errors && data.errors.length > 0 ? `
            <details class="mt-2">
                <summary class="text-sm text-rose-400 cursor-pointer">⚠️ ${data.errors.length} erreur(s) mineures</summary>
                <ul class="mt-2 text-xs text-gray-400 space-y-1">
                    ${data.errors.map(e => `<li>${e.signature}: ${e.error}</li>`).join('')}
                </ul>
            </details>` : ''}
        `;
        await dbOnlyRefresh(true, true);
        await loadTokens();
        showNotification(`Import OK — ${data.imported_buys} achats, ${data.imported_sales} ventes, ${data.prices_updated ?? 0} prix mis à jour`, 'success');
    } catch(e) {
        _heliusStopLoading();
        showNotification('Erreur import swaps: ' + e.message, 'error');
    }
}

/** Réécrit les achats déjà importés (repair_imported_buys) — nécessaire si les montants étaient faux avant correctif serveur. */
async function heliusRepairPurchases() {
    if (!_checkWalletForHelius()) return;
    const ok = confirm(
        'Corriger les prix d’achat (SOL dépensé) des achats déjà importés ?\n\n' +
            'Sans cette étape, la base garde les anciens montants : un simple déploiement serveur ne change rien.\n\n' +
            'Le serveur parcourt jusqu’à 40 pages Helius, supprime les lignes d’achat concernées et les réimporte avec la logique actuelle (dont plafond par solde wallet).\n\n' +
            'Durée : environ 1 à 2 minutes.'
    );
    if (!ok) return;
    _heliusStartLoading();
    document.getElementById('helius-content').innerHTML = `
        <p class="text-amber-200 font-medium animate-pulse">
            <i class="fas fa-wrench fa-spin mr-2"></i>
            Réimport des achats en cours (plusieurs pages Helius)…
        </p>
    `;
    try {
        const qs = new URLSearchParams({
            max_pages: '40',
            resume_history: '1',
            repair_imported_buys: '1',
            skip_post_import_prices: '1',
        });
        const res = await fetch(`${API_URL}/helius/import-swaps/${encodeURIComponent(walletAddress)}?${qs}`, {
            method: 'POST',
        });
        const data = await res.json();
        _heliusStopLoading();
        if (data.detail) {
            showNotification(String(data.detail), 'error');
            return;
        }
        const rep = data.repaired_buy_transactions ?? 0;
        const buys = data.imported_buys ?? 0;
        document.getElementById('helius-content').innerHTML = `
            <h3 class="text-lg font-bold text-amber-200 mb-3">
                <i class="fas fa-check-circle mr-2"></i>Réparation terminée
            </h3>
            <p class="text-gray-200 mb-2">Transactions d’achat réouvertes puis réimportées : <strong>${rep}</strong></p>
            <p class="text-gray-200 mb-4">Nouvelles lignes d’achat écrites ce passage : <strong>${buys}</strong></p>
        `;
        await fetch(`${API_URL}/update-prices?wallet=${encodeURIComponent(walletAddress)}`, { method: 'POST' }).catch(() => {});
        await fetch(`${API_URL}/recalculate-history?wallet=${encodeURIComponent(walletAddress)}`, {
            method: 'POST',
        }).catch(() => {});
        await dbOnlyRefresh(true, true);
        await loadTokens();
        showNotification(
            `Montants : ${rep} tx réparées, ${buys} achats réécrits — pensez à vérifier vos positions.`,
            'success'
        );
    } catch (e) {
        _heliusStopLoading();
        showNotification('Erreur réparation achats: ' + e.message, 'error');
    }
}

// ── Toutes transactions (brutes) ─────────────────────────────────────────
async function heliusTransactions() {
    if (!_checkWalletForHelius()) return;
    _heliusStartLoading();
    try {
        const res  = await fetch(`${API_URL}/helius/transactions/${walletAddress}?limit=50`);
        const data = await res.json();
        _heliusStopLoading();
        if (data.detail) { showNotification(data.detail, 'error'); return; }

        const txs = Array.isArray(data) ? data : [];
        const typeColor = t => ({ SWAP:'indigo', TRANSFER:'sky', NFT_SALE:'amber', BURN:'rose' }[t] || 'gray');
        document.getElementById('helius-content').innerHTML = `
            <h3 class="text-lg font-bold text-teal-700 mb-3">
                <i class="fas fa-list-alt mr-2"></i>Toutes les transactions (${txs.length})
            </h3>
            ${ txs.length === 0 ? '<p class="text-gray-400">Aucune transaction trouvée.</p>' : `
            <div class="overflow-x-auto max-h-[500px] overflow-y-auto">
                <table class="w-full text-xs">
                    <thead class="table-header-teal sticky top-0">
                        <tr>
                            <th class="px-3 py-2 text-left">Date</th>
                            <th class="px-3 py-2 text-left">Type</th>
                            <th class="px-3 py-2 text-left">Description</th>
                            <th class="px-3 py-2 text-right">Frais SOL</th>
                            <th class="px-3 py-2 text-center">Explorer</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${txs.map((tx,i) => {
                            const ts = tx.timestamp;
                            const d = ts ? formatDateFr(ts * 1000) : '-';
                            const fee = tx.fee ? (tx.fee/1e9).toFixed(5) : '0';
                            const col = typeColor(tx.type);
                            return `<tr class="${i%2===0?'bg-white':'bg-teal-50'} hover:bg-indigo-50 transition-colors">
                                <td class="px-3 py-2 whitespace-nowrap text-gray-500">${d}</td>
                                <td class="px-3 py-2"><span class="px-2 py-0.5 rounded-full bg-${col}-100 text-${col}-700 font-bold">${tx.type||'?'}</span></td>
                                <td class="px-3 py-2 max-w-xs truncate text-gray-600">${tx.description||'—'}</td>
                                <td class="px-3 py-2 text-right text-gray-500">${fee}</td>
                                <td class="px-3 py-2 text-center">
                                    <a href="https://solscan.io/tx/${tx.signature}" target="_blank" class="text-teal-500 hover:text-teal-700">
                                        <i class="fas fa-external-link-alt"></i>
                                    </a>
                                </td>
                            </tr>`;
                        }).join('')}
                    </tbody>
                </table>
            </div>`}
        `;
    } catch(e) {
        _heliusStopLoading();
        showNotification('Erreur transactions: ' + e.message, 'error');
    }
}

// ── Envois & Reçus (transfers SOL + tokens) ──────────────────────────────
async function heliusTransfers() {
    if (!_checkWalletForHelius()) return;
    _heliusStartLoading();
    try {
        const res  = await fetch(`${API_URL}/helius/transfers/${walletAddress}?limit=50`);
        const data = await res.json();
        _heliusStopLoading();
        if (data.detail) { showNotification(data.detail, 'error'); return; }

        const s         = data.summary || {};
        const events    = data.events  || [];
        const solRecu   = (s.sol_recu   || 0).toFixed(4);
        const solEnvoye = (s.sol_envoye || 0).toFixed(4);
        const netSol    = ((s.sol_recu || 0) - (s.sol_envoye || 0)).toFixed(4);
        const netColor  = parseFloat(netSol) >= 0 ? 'emerald' : 'rose';
        const netIcon   = parseFloat(netSol) >= 0 ? '📈' : '📉';

        const tokRecu   = Object.entries(s.tokens_recus   || {});
        const tokEnvoye = Object.entries(s.tokens_envoyes || {});
        const mintShort = m => m ? m.substring(0,6)+'…'+m.slice(-4) : '';

        // ── Cartes visibles en haut du dashboard ──────────────────────────
        const summaryDiv = document.getElementById('transfers-summary');
        const cardsDiv   = document.getElementById('transfers-cards');
        if (summaryDiv && cardsDiv) {
            summaryDiv.classList.remove('hidden');
            cardsDiv.innerHTML = `
                <div class="glass rounded-xl p-5 shadow-md card-hover transition-all">
                    <div class="flex items-center justify-between">
                        <div>
                            <p class="text-teal-500 text-sm font-medium">📥 SOL Reçu</p>
                            <p class="text-2xl font-bold text-emerald-700 mt-1">+${solRecu} SOL</p>
                            <p class="text-xs text-gray-400 mt-1">${data.tx_analysed} tx analysées</p>
                        </div>
                        <div class="bg-emerald-100 p-3 rounded-full text-2xl">📥</div>
                    </div>
                </div>
                <div class="glass rounded-xl p-5 shadow-md card-hover transition-all">
                    <div class="flex items-center justify-between">
                        <div>
                            <p class="text-teal-500 text-sm font-medium">📤 SOL Envoyé</p>
                            <p class="text-2xl font-bold text-rose-700 mt-1">-${solEnvoye} SOL</p>
                            <p class="text-xs text-gray-400 mt-1">sorties nettes</p>
                        </div>
                        <div class="bg-rose-100 p-3 rounded-full text-2xl">📤</div>
                    </div>
                </div>
                <div class="glass rounded-xl p-5 shadow-md card-hover transition-all">
                    <div class="flex items-center justify-between">
                        <div>
                            <p class="text-teal-500 text-sm font-medium">${netIcon} Flux Net SOL</p>
                            <p class="text-2xl font-bold text-${netColor}-700 mt-1">${parseFloat(netSol) >= 0 ? '+' : ''}${netSol} SOL</p>
                            <p class="text-xs text-gray-400 mt-1">reçu − envoyé</p>
                        </div>
                        <div class="bg-${netColor}-100 p-3 rounded-full text-2xl">${netIcon}</div>
                    </div>
                </div>
                <div class="glass rounded-xl p-5 shadow-md card-hover transition-all">
                    <div class="flex items-center justify-between">
                        <div>
                            <p class="text-teal-500 text-sm font-medium">🔀 Tokens échangés</p>
                            <p class="text-2xl font-bold text-teal-800 mt-1">${tokRecu.length} reçus</p>
                            <p class="text-xs text-gray-400 mt-1">${tokEnvoye.length} envoyés</p>
                        </div>
                        <div class="bg-sky-100 p-3 rounded-full text-2xl">🔀</div>
                    </div>
                </div>
            `;
        }

        // ── Détail dans la zone Blockchain (bas de page) ──────────────────
        const tokRecuHtml = tokRecu.length
            ? tokRecu.map(([m,v]) => `<li class="text-sm text-emerald-700"><span class="font-mono text-xs text-gray-400">${mintShort(m)}</span> <b>+${v.toLocaleString('fr-FR')}</b></li>`).join('')
            : '<li class="text-sm text-gray-400">Aucun token reçu</li>';
        const tokEnvoyeHtml = tokEnvoye.length
            ? tokEnvoye.map(([m,v]) => `<li class="text-sm text-rose-700"><span class="font-mono text-xs text-gray-400">${mintShort(m)}</span> <b>-${v.toLocaleString('fr-FR')}</b></li>`).join('')
            : '<li class="text-sm text-gray-400">Aucun token envoyé</li>';

        const eventsHtml = events.length === 0
            ? '<p class="text-gray-400 text-sm">Aucun mouvement détecté.</p>'
            : `<div class="overflow-x-auto max-h-[380px] overflow-y-auto mt-4">
                <table class="w-full text-xs">
                    <thead class="table-header-teal sticky top-0">
                        <tr>
                            <th class="px-3 py-2 text-left">Date</th>
                            <th class="px-3 py-2 text-center">Dir.</th>
                            <th class="px-3 py-2 text-left">Asset</th>
                            <th class="px-3 py-2 text-right">Montant</th>
                            <th class="px-3 py-2 text-left">Avec</th>
                            <th class="px-3 py-2 text-center">Tx</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${events.map((e,i) => {
                            const col = e.direction === 'recu' ? 'emerald' : 'rose';
                            const icon = e.direction === 'recu' ? '📥' : '📤';
                            const cp  = e.counterpart ? e.counterpart.substring(0,6)+'…'+e.counterpart.slice(-4) : '—';
                            return `<tr class="${i%2===0?'bg-white':'bg-teal-50'} hover:bg-indigo-50 transition-colors">
                                <td class="px-3 py-2 whitespace-nowrap text-gray-500">${formatDateFr(e.date)}</td>
                                <td class="px-3 py-2 text-center">
                                    <span class="px-2 py-0.5 rounded-full bg-${col}-100 text-${col}-700 font-bold text-xs">${icon} ${e.direction === 'recu' ? 'Reçu' : 'Envoyé'}</span>
                                </td>
                                <td class="px-3 py-2 font-semibold text-gray-700">${e.asset}</td>
                                <td class="px-3 py-2 text-right font-bold text-${col}-700">${e.amount.toLocaleString('fr-FR', {maximumFractionDigits:6})}</td>
                                <td class="px-3 py-2 font-mono text-gray-400">${cp}</td>
                                <td class="px-3 py-2 text-center">
                                    <a href="https://solscan.io/tx/${e.signature}" target="_blank" class="text-teal-500 hover:text-teal-700">
                                        <i class="fas fa-external-link-alt"></i>
                                    </a>
                                </td>
                            </tr>`;
                        }).join('')}
                    </tbody>
                </table>
            </div>`;

        document.getElementById('helius-content').innerHTML = `
            <h3 class="text-lg font-bold text-teal-700 mb-4">
                <i class="fas fa-exchange-alt mr-2"></i>Envois &amp; Reçus — ${walletAddress.substring(0,6)}…${walletAddress.slice(-4)}
                <span class="text-sm font-normal text-gray-400 ml-2">(${data.tx_analysed} tx analysées)</span>
            </h3>
            <div class="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-4">
                <div class="rounded-xl p-4 bg-emerald-50 border border-emerald-200 flex items-center gap-4">
                    <div class="bg-emerald-100 rounded-full p-3 text-2xl">📥</div>
                    <div>
                        <p class="text-xs text-gray-500 mb-0.5">SOL reçu</p>
                        <p class="text-2xl font-bold text-emerald-700">+${solRecu} SOL</p>
                        <ul class="mt-1 space-y-0.5">${tokRecuHtml}</ul>
                    </div>
                </div>
                <div class="rounded-xl p-4 bg-rose-50 border border-rose-200 flex items-center gap-4">
                    <div class="bg-rose-100 rounded-full p-3 text-2xl">📤</div>
                    <div>
                        <p class="text-xs text-gray-500 mb-0.5">SOL envoyé</p>
                        <p class="text-2xl font-bold text-rose-700">-${solEnvoye} SOL</p>
                        <ul class="mt-1 space-y-0.5">${tokEnvoyeHtml}</ul>
                    </div>
                </div>
            </div>
            ${eventsHtml}
        `;
    } catch(e) {
        _heliusStopLoading();
        showNotification('Erreur transfers: ' + e.message, 'error');
    }
}

// =============================================================================
function buildDashboardCardTableBack(filter, txs) {
    const sells = (txs || []).filter(t => t.tx_type === 'sell' && t.pnl_usd != null);
    let filtered = [];
    if (filter === 'gains' || filter === 'realized_gain') filtered = sells.filter(t => t.pnl_usd > 0);
    else if (filter === 'losses' || filter === 'realized_loss') filtered = sells.filter(t => t.pnl_usd < 0);
    else if (filter === 'net') filtered = sells;
    // Aperçu : Date, Token, Vente, P/L en plus gros — Nb / Vendu / PnL uniquement dans la modale (Agrandir)
    const rows = filtered.length === 0
        ? `<tr><td colspan="4" class="text-center py-3 px-2 text-gray-400 text-xs">Aucune transaction</td></tr>`
        : filtered.map(tx => {
            const date = formatDateFr(tx.tx_timestamp || tx.tx_date, false);
            const pnl = tx.pnl_usd ?? 0;
            const pnlColor = pnl >= 0 ? 'emerald-600' : 'rose-500';
            const pnlPctStr = formatRealizedPnlVsHifoCostPct(pnl, tx.cost_usd);
            const pnlPctHtml = pnlPctStr
                ? ` <span class="text-[10px] text-gray-500 font-semibold" title="P/L ÷ coût HIFO">(${pnlPctStr})</span>`
                : '';
            const sigLink = tx.transaction_signature
                ? `<a href="https://solscan.io/tx/${tx.transaction_signature}" target="_blank" rel="noopener" class="text-teal-600 hover:text-teal-800 ml-1 inline-flex align-middle" title="Solscan" onclick="event.stopPropagation()"><i class="fas fa-external-link-alt text-xs"></i></a>`
                : '';
            const safeName = (tx.token_name || '—').replace(/"/g, '&quot;');
            const addr = tx.token_address || '';
            const dexQs = walletAddress ? `?maker=${encodeURIComponent(walletAddress)}` : '';
            const dexHref = addr ? `https://dexscreener.com/solana/${addr}${dexQs}` : '';
            const dexLink = addr && dexHref
                ? `<a href="${dexHref}" target="_blank" rel="noopener" class="inline-flex shrink-0 text-teal-600 hover:text-teal-800 ml-0.5 align-middle" title="${walletAddress ? 'DexScreener (vos trades)' : 'DexScreener'}" onclick="event.stopPropagation()"><i class="fas fa-external-link-alt text-[9px]"></i></a>`
                : '';
            const addrRow = addr
                ? `<div class="flex items-center gap-0.5 mt-0.5 min-w-0 leading-none">
                    <span class="text-[9px] font-mono text-teal-600/90 truncate">${addr.substring(0, 6)}…</span>${dexLink}
                </div>`
                : '';
            return `<tr class="border-t border-teal-100/70 hover:bg-teal-50/35">
                <td class="py-1.5 pr-1 text-gray-600 whitespace-nowrap text-xs align-middle">${date}${sigLink}</td>
                <td class="py-1.5 pr-1 text-teal-900 text-xs align-top min-w-0">
                    <div class="font-semibold truncate" title="${safeName}">${tx.token_name || '—'}</div>
                    ${addrRow}
                </td>
                <td class="py-1.5 pr-1 text-right font-bold text-teal-800 text-xs leading-tight align-middle">${formatCurrency(tx.amount_usd || 0)}</td>
                <td class="py-1.5 text-right font-bold text-${pnlColor} text-sm leading-tight align-middle">${pnl >= 0 ? '+' : ''}${formatCurrency(pnl)}${pnlPctHtml}</td>
            </tr>`;
        }).join('');
    const escFilter = String(filter).replace(/"/g, '&quot;');
    return `<div class="flex flex-col flex-1 min-h-0 w-full overflow-hidden px-2 pt-1.5 pb-1">
        <div class="dashboard-flip-preview-scroll w-full min-w-0">
            <table class="w-full table-fixed">
                <colgroup>
                    <col class="w-[28%]" />
                    <col class="w-[32%]" />
                    <col class="w-[20%]" />
                    <col class="w-[20%]" />
                </colgroup>
                <thead class="sticky top-0 bg-white/92 backdrop-blur-sm z-[1] border-b border-teal-200/80">
                    <tr class="text-gray-600">
                        <th class="text-left py-1 pr-1 text-xs font-bold uppercase tracking-wide">Date</th>
                        <th class="text-left py-1 pr-1 text-xs font-bold uppercase tracking-wide">Token</th>
                        <th class="text-right py-1 pr-1 text-xs font-bold uppercase tracking-wide">Vente</th>
                        <th class="text-right py-1 text-xs font-bold uppercase tracking-wide">P/L</th>
                    </tr>
                </thead>
                <tbody>${rows}</tbody>
            </table>
        </div>
        <button type="button" class="dashboard-flip-expand-btn mt-auto shrink-0 self-center inline-flex items-center gap-1 px-2.5 py-0.5 rounded-md text-[10px] font-semibold text-teal-800 bg-teal-100/80 hover:bg-teal-200/90 border border-teal-300/40 transition"
            data-tx-expand="${escFilter}" title="Voir Nb, totaux et tableau complet">
            <i class="fas fa-expand-alt text-[9px] opacity-90"></i>Agrandir
        </button>
    </div>`;
}

/** Montant > 0 avec « + » devant (formatCurrency ne le fait pas). */
function _signedCurrency(amt) {
    const n = Number(amt) || 0;
    if (n > 0) return '+' + formatCurrency(n);
    return formatCurrency(n);
}

/**
 * % du total dépensé (achats meme coins), pour afficher à côté des montants.
 * @param {string} spanClass classes Tailwind pour le span du pourcentage
 * @param {'signed'|'loss_mag'} mode — signed : montant algébrique / inv. loss_mag : perte affichée en USD positive (API) → % négatif du même ordre que les gains.
 */
function _pctOfInvestedHtml(
    amountUsd,
    investedUsd,
    spanClass = 'text-sm font-semibold text-teal-600/90 ml-1',
    mode = 'signed'
) {
    const inv = Number(investedUsd) || 0;
    if (inv <= 0.01) return '';
    let pct;
    if (mode === 'loss_mag') {
        pct = -((Math.abs(Number(amountUsd) || 0) / inv) * 100);
    } else {
        pct = ((Number(amountUsd) || 0) / inv) * 100;
    }
    const ps = pct > 0 ? '+' : '';
    return `<span class="${spanClass}">(${ps}${pct.toFixed(1)}%)</span>`;
}

/** Performance globale : (patrimoine / capital dépensé − 1), en %. titleAttr : infobulle (ex. préciser numérateur/dénominateur). */
function _totalReturnVsInvestedHtml(
    patrimoineUsd,
    investedUsd,
    spanClass = 'text-sm font-semibold text-teal-600/90 ml-1',
    titleAttr = ''
) {
    const inv = Number(investedUsd) || 0;
    if (inv <= 0.01) return '';
    const pct = ((Number(patrimoineUsd) || 0) / inv - 1) * 100;
    const ps = pct > 0 ? '+' : '';
    const t =
        titleAttr && String(titleAttr).trim()
            ? ` title="${String(titleAttr).replace(/"/g, '&quot;')}"`
            : '';
    return `<span class="${spanClass}"${t}>(${ps}${pct.toFixed(1)}%)</span>`;
}

/** Part du patrimoine total (ex. SOL). */
function _pctOfPatrimoineHtml(partUsd, patrimoineUsd, spanClass = 'text-xs font-semibold text-sky-600/90 ml-1') {
    const p = Number(patrimoineUsd) || 0;
    if (p <= 0.01) return '';
    const pct = ((Number(partUsd) || 0) / p) * 100;
    return `<span class="${spanClass}">(${pct.toFixed(1)}% patrim.)</span>`;
}

function renderDashboard(data, txs = []) {
    if (!data) return;
    window._solPriceUsd = data.sol_price_usd || 150;
        const pricePerSol = data.sol_price_usd || 150;
        let solBalance = data.wallet_sol_balance ?? (window._lastSolBalance || 0);
        let solBalanceStale = !data.wallet_sol_balance && !!window._lastSolBalance;
        if (solBalance > 0) window._lastSolBalance = solBalance;
        const solValue = solBalance * pricePerSol;
        const totalPatrimoine = (data.current_amount ?? 0) + solValue;
        window._lastPatrimoineUsd = totalPatrimoine;
        const flowNetUsd =
            data.flow_net_usd != null
                ? data.flow_net_usd
                : (data.current_amount ?? 0) + (data.withdrawn_amount ?? 0) - (data.total_risked ?? 0);
        const flowNetDrift = Math.abs(flowNetUsd - (data.net_total ?? 0));
        const investedForRoi = data.total_risked ?? 0;
        const trackedPurchasesUsd =
            data.tracked_purchases_usd != null ? data.tracked_purchases_usd : investedForRoi;
        const hasManualTotal = (data.reference_capital_usd ?? 0) > 0.01;
        const spentCardTitle = hasManualTotal ? '💰 Total dépensé' : '💰 Total dépensé (à définir)';
        const spentCardHelp = hasManualTotal
            ? 'Montant fixe que vous avez enregistré : tout l’argent que vous considérez avoir mis sur ce wallet. Il ne change pas tout seul : ajoutez un dépôt ou modifiez le total dans la fenêtre ci-dessous.'
            : 'Renseignez le montant total que vous avez investi (ex. égal au patrimoine actuel aujourd’hui, puis +50 $ quand vous rechargez). Rien n’est calculé automatiquement depuis la chaîne.';
        const unsetHintHtml = !hasManualTotal
            ? `<p class="text-[10px] text-amber-800/90 leading-snug border-t border-amber-100/80 pt-1.5 mt-1.5">Patrimoine actuel (indication) : ${formatCurrency(totalPatrimoine)} — vous pouvez l’utiliser comme premier total dépensé via le bouton dans la fenêtre de saisie.</p>`
            : '';
        const patrimoinePctTitle = hasManualTotal
            ? 'Valeur tokens + SOL comparée au total dépensé que vous avez saisi.'
            : 'Définissez d’abord le total dépensé pour activer le comparatif en %.';

        const dashboard = document.getElementById('dashboard');
        dashboard.className = 'flex flex-col gap-6 lg:grid lg:grid-cols-12 lg:gap-6 mb-6 items-stretch';
        const cardStd = 'glass rounded-xl p-4 sm:p-6 shadow-md card-hover transition-all';
        const flipFront = 'dashboard-flip-front glass rounded-xl p-3 sm:p-4 shadow-md card-hover transition-all flex items-center';
        const flipBack = 'dashboard-flip-back glass rounded-xl p-1 shadow-md overflow-hidden';
        const netPos = (data.net_total ?? 0) >= 0;
        const pnlFigedNet = (data.realized_gain ?? 0) - (data.realized_loss ?? 0);
        const pnlLatentNet = (data.total_gain ?? 0) - (data.total_loss ?? 0);
        const pnlFigedPos = pnlFigedNet >= 0;
        const pnlLatentPos = pnlLatentNet >= 0;
        dashboard.innerHTML = `
            <!-- Colonne gauche (mobile: après le centre) -->
            <div class="order-3 lg:order-none lg:col-span-3 lg:row-span-2 lg:col-start-1 lg:row-start-1 flex flex-col gap-6">
                <div class="dashboard-flip">
                    <div class="${cardStd}">
                        <div class="flex items-center justify-between gap-2">
                            <div class="min-w-0 flex-1 pr-2">
                                <p class="text-teal-500 text-sm font-medium">💎 Solde SOL</p>
                                <p class="text-2xl sm:text-3xl font-bold text-teal-800 mt-2 flex flex-wrap items-baseline gap-x-1">${formatCurrency(solValue)}${_pctOfPatrimoineHtml(solValue, totalPatrimoine)}</p>
                                <p class="text-xs mt-1 ${solBalanceStale ? 'text-amber-400' : 'text-teal-400'}">
                                    ${solBalance.toFixed(4)} SOL${solBalanceStale ? ' ⚠️ cache' : ''}
                                </p>
                            </div>
                            <div class="bg-sky-100 p-4 rounded-full shrink-0">
                                <i class="fas fa-gem text-sky-500 text-2xl"></i>
                            </div>
                        </div>
                    </div>
                </div>
                <div class="dashboard-flip">
                    <div class="${cardStd}">
                        <div class="flex items-center justify-between">
                            <div class="w-full min-w-0 pr-2">
                                <p class="text-teal-500 text-sm font-medium">${spentCardTitle}</p>
                                <p class="text-2xl sm:text-3xl font-bold text-teal-800 mt-2 flex flex-wrap items-baseline gap-x-1">${formatCurrency(data.total_risked ?? 0)}${investedForRoi > 0.01 ? '<span class="text-sm font-semibold text-teal-500/80 ml-1">(base des %)</span>' : ''}</p>
                                <div class="mt-2 space-y-1">
                                    <p class="text-[11px] text-slate-600 leading-snug">${spentCardHelp}</p>
                                    <p class="text-xs text-slate-500">Achats importés (chaîne, info) : ${formatCurrency(trackedPurchasesUsd)}</p>
                                    ${unsetHintHtml}
                                    <button type="button" data-action="open-ref-capital" class="mt-1 text-left text-xs font-semibold text-cyan-700 hover:text-cyan-900 underline decoration-cyan-400/80">${hasManualTotal ? 'Modifier le total dépensé ou ajouter un dépôt…' : 'Définir le total dépensé…'}</button>
                                    <p class="text-xs text-emerald-600">📥 Reçu des ventes (brut, USD) : ${formatCurrency(data.withdrawn_amount ?? 0)}${_pctOfInvestedHtml(data.withdrawn_amount ?? 0, investedForRoi, 'text-xs font-semibold text-emerald-700/90 ml-1')}</p>
                                    <p class="text-[10px] text-teal-600/90 leading-snug">Flux (vs cette base) : valeur tokens + ventes − ce total → ${formatCurrency(data.current_amount ?? 0)}${_pctOfInvestedHtml(data.current_amount ?? 0, investedForRoi, 'text-[10px] font-semibold text-teal-700 ml-0.5')} + ${formatCurrency(data.withdrawn_amount ?? 0)}${_pctOfInvestedHtml(data.withdrawn_amount ?? 0, investedForRoi, 'text-[10px] font-semibold text-teal-700 ml-0.5')} − ${formatCurrency(data.total_risked ?? 0)} = ${formatCurrency(flowNetUsd)}${_pctOfInvestedHtml(flowNetUsd, investedForRoi, 'text-[10px] font-semibold text-teal-800 ml-0.5')}</p>
                                </div>
                            </div>
                            <div class="bg-teal-100 p-4 rounded-full shrink-0">
                                <i class="fas fa-coins text-teal-500 text-2xl"></i>
                            </div>
                        </div>
                    </div>
                </div>
                <div class="dashboard-flip">
                    <div class="${cardStd}">
                        <div class="flex items-center justify-between gap-2">
                            <div class="min-w-0 flex-1 pr-2">
                                <p class="text-teal-500 text-sm font-medium">📊 Valeur Actuelle</p>
                                <p class="text-2xl sm:text-3xl font-bold text-teal-800 mt-2 flex flex-wrap items-baseline gap-x-1">${formatCurrency(data.current_amount ?? 0)}${_pctOfInvestedHtml(data.current_amount ?? 0, investedForRoi, 'text-lg font-semibold text-teal-600/90')}</p>
                            </div>
                            <div class="bg-cyan-100 p-4 rounded-full shrink-0">
                                <i class="fas fa-chart-line text-cyan-500 text-2xl"></i>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <!-- Centre : Total Patrimoine (hero) -->
            <div class="order-1 lg:order-none lg:col-span-6 lg:col-start-4 lg:row-start-1">
                <div class="dashboard-flip h-full">
                    <div class="glass dash-hero-card rounded-2xl p-5 sm:p-8 shadow-lg card-hover transition-all border border-amber-200/40 h-full min-h-[11rem] flex items-center">
                        <div class="flex items-center justify-between w-full gap-3 sm:gap-4">
                            <div class="min-w-0 flex-1">
                                <p class="text-teal-600 text-sm sm:text-base font-semibold tracking-wide">👑 Total Patrimoine</p>
                                <p class="text-2xl sm:text-3xl md:text-4xl lg:text-5xl font-extrabold text-teal-900 mt-2 leading-tight flex flex-wrap items-baseline gap-x-2">${formatCurrency(totalPatrimoine)}${_totalReturnVsInvestedHtml(totalPatrimoine, investedForRoi, 'text-base sm:text-xl lg:text-2xl font-bold text-teal-700/90', patrimoinePctTitle)}</p>
                                <p class="text-xs sm:text-sm text-teal-500 mt-2">Tokens + SOL (valeur marché)</p>
                            </div>
                            <div class="bg-amber-100 p-3 sm:p-5 rounded-full shrink-0">
                                <i class="fas fa-crown text-amber-500 text-2xl sm:text-3xl lg:text-4xl"></i>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <!-- Centre : Résultat Net (hero + flip) -->
            <div class="order-2 lg:order-none lg:col-span-6 lg:col-start-4 lg:row-start-2 dashboard-flip dashboard-flip-flipable dash-hero-flip cursor-pointer" data-tx-filter="net" title="Résultat net = latent + figé — survol : détail des ventes">
                <div class="dashboard-flip-inner">
                    <div class="${flipFront} hover:ring-2 hover:ring-teal-400/50 min-h-0 h-full border border-teal-200/30">
                        <div class="flex items-center justify-between w-full gap-3 px-2">
                            <div class="min-w-0 flex-1">
                                <p class="text-teal-600 text-sm sm:text-base font-semibold">✨ Résultat net <span class="text-teal-500 font-normal text-xs sm:text-sm">(latent + figé)</span></p>
                                <p class="text-2xl sm:text-3xl md:text-4xl lg:text-5xl font-extrabold ${netPos ? 'text-emerald-600' : 'text-rose-600'} mt-1 leading-tight flex flex-wrap items-baseline gap-x-2">
                                    ${_signedCurrency(data.net_total ?? 0)}${_pctOfInvestedHtml(data.net_total ?? 0, investedForRoi, `text-lg sm:text-2xl lg:text-3xl font-bold ml-1 ${netPos ? 'text-emerald-700' : 'text-rose-600'}`)}
                                </p>
                                <p class="text-xs text-teal-600/90 mt-3 leading-snug max-w-md">
                                    <span class="font-semibold text-emerald-700">Gains totaux</span> ${formatCurrency(data.total_gain ?? 0)}${_pctOfInvestedHtml(data.total_gain ?? 0, investedForRoi, 'text-xs font-semibold text-emerald-700 ml-1')}
                                    <span class="text-teal-400 mx-1">−</span>
                                    <span class="font-semibold text-rose-600">Pertes totales</span> ${formatCurrency(data.total_loss ?? 0)}${_pctOfInvestedHtml(data.total_loss ?? 0, investedForRoi, 'text-xs font-semibold text-rose-600 ml-1', 'loss_mag')}
                                    <span class="block text-[10px] text-teal-500/80 mt-1">Résultat net = <strong>(gains latents − pertes latentes) + (gain figé − perte figée)</strong>, tout en HIFO. Les cartes « Gains / Pertes totaux » restent le latent seul. Flux « valeur + ventes − total dépensé » à part (${formatCurrency(flowNetUsd)}${_pctOfInvestedHtml(flowNetUsd, investedForRoi, 'text-[10px] font-semibold text-teal-600 ml-0.5')}).</span>
                                    ${flowNetDrift > 25 ? (!hasManualTotal ? `<span class="block text-[10px] text-sky-800/90 mt-1">Sans total dépensé enregistré, le flux affiché ne vise pas à égaler le résultat net : définissez d’abord votre total dépensé.</span>` : `<span class="block text-[10px] text-sky-800/90 mt-1">Écart résultat net vs flux : normal si votre total dépensé saisi ne colle pas exactement aux achats importés ; sinon « Actualiser » et « Recalculer HIFO ».</span>`) : ''}
                                </p>
                                <p class="text-[11px] text-teal-500/85 mt-1 leading-snug max-w-md">
                                    Composante <strong>figée</strong> (ventes) déjà dans le net :
                                    <span class="font-semibold text-emerald-600">+${formatCurrency(data.realized_gain ?? 0)}${_pctOfInvestedHtml(data.realized_gain ?? 0, investedForRoi, 'text-[10px] font-semibold text-emerald-700 ml-0.5')}</span>
                                    <span class="text-teal-400 mx-0.5">/</span>
                                    <span class="font-semibold text-rose-500">−${formatCurrency(data.realized_loss ?? 0)}${_pctOfInvestedHtml(data.realized_loss ?? 0, investedForRoi, 'text-[10px] font-semibold text-rose-600 ml-0.5', 'loss_mag')}</span>
                                    ${data.hifo_pending ? '<span class="text-amber-600 ml-1">— lancer « Recalculer HIFO »</span>' : ''}
                                </p>
                            </div>
                            <div class="bg-${netPos ? 'emerald' : 'rose'}-100 p-4 rounded-full shrink-0">
                                <i class="fas fa-balance-scale text-${netPos ? 'emerald' : 'rose'}-500 text-3xl"></i>
                            </div>
                        </div>
                    </div>
                    <div class="${flipBack}">
                        ${buildDashboardCardTableBack('net', txs)}
                    </div>
                </div>
            </div>

            <!-- Colonne droite -->
            <div class="order-4 lg:order-none lg:col-span-3 lg:col-start-10 lg:row-span-2 lg:row-start-1 flex flex-col gap-6">
                <div class="dashboard-flip cursor-pointer" data-tx-filter="gains" title="P/L latent sur ce que vous détenez — clic : historique des ventes gagnantes">
                    <div class="${cardStd} hover:ring-2 hover:ring-emerald-400/50">
                        <div class="flex items-center justify-between gap-2">
                            <div class="min-w-0 flex-1 pr-2">
                                <p class="text-teal-500 text-sm font-medium">📈 Gains Totaux</p>
                                <p class="text-[10px] text-teal-600/80 -mt-0.5 mb-1">latent (détenus)</p>
                                <p class="text-2xl sm:text-3xl font-bold text-emerald-600 mt-2 flex flex-wrap items-baseline gap-x-1">${formatCurrency(data.total_gain ?? 0)}${_pctOfInvestedHtml(data.total_gain ?? 0, investedForRoi, 'text-lg font-bold text-emerald-700')}</p>
                            </div>
                            <div class="bg-emerald-100 p-4 rounded-full shrink-0">
                                <i class="fas fa-arrow-up text-emerald-500 text-2xl"></i>
                            </div>
                        </div>
                    </div>
                </div>
                <div class="dashboard-flip cursor-pointer" data-tx-filter="losses" title="P/L latent sur ce que vous détenez — clic : historique des ventes perdantes">
                    <div class="${cardStd} hover:ring-2 hover:ring-rose-400/50">
                        <div class="flex items-center justify-between gap-2">
                            <div class="min-w-0 flex-1 pr-2">
                                <p class="text-teal-500 text-sm font-medium">📉 Pertes Totales</p>
                                <p class="text-[10px] text-teal-600/80 -mt-0.5 mb-1">latent (détenus)</p>
                                <p class="text-2xl sm:text-3xl font-bold text-rose-500 mt-2 flex flex-wrap items-baseline gap-x-1">${formatCurrency(data.total_loss ?? 0)}${_pctOfInvestedHtml(data.total_loss ?? 0, investedForRoi, 'text-lg font-bold text-rose-600', 'loss_mag')}</p>
                            </div>
                            <div class="bg-rose-100 p-4 rounded-full shrink-0">
                                <i class="fas fa-arrow-down text-rose-400 text-2xl"></i>
                            </div>
                        </div>
                    </div>
                </div>
                <div class="dashboard-flip dashboard-flip-flipable cursor-pointer" data-tx-filter="realized_gain" title="Survoler pour le détail">
                    <div class="dashboard-flip-inner">
                        <div class="${flipFront} hover:ring-2 hover:ring-amber-400/50 min-h-0 h-full">
                            <div class="flex items-center justify-between w-full gap-2 px-1">
                                <div class="min-w-0">
                                    <p class="text-teal-500 text-sm font-medium">🔒 Gain Figé</p>
                                    <p class="text-2xl sm:text-3xl font-bold ${(data.realized_gain ?? 0) >= 0 ? 'text-emerald-600' : 'text-rose-500'} mt-1 leading-tight flex flex-wrap items-baseline gap-x-1">
                                        ${formatCurrency(data.realized_gain ?? 0)}${_pctOfInvestedHtml(data.realized_gain ?? 0, investedForRoi, `text-lg font-bold ml-1 ${(data.realized_gain ?? 0) >= 0 ? 'text-emerald-700' : 'text-rose-600'}`)}
                                    </p>
                                    <p class="text-xs text-teal-400 mt-0.5 leading-snug">Gains réalisés (ventes)${data.hifo_pending ? ' — <span class="text-amber-500">HIFO : cliquez sur « Recalculer HIFO »</span>' : ' — <span class="text-teal-500/80">figés en base, ne suivent pas le cours du jour</span>'}</p>
                                </div>
                                <div class="bg-amber-100 p-3 rounded-full shrink-0">
                                    <i class="fas fa-lock text-amber-500 text-xl"></i>
                                </div>
                            </div>
                        </div>
                        <div class="${flipBack}">
                            ${buildDashboardCardTableBack('realized_gain', txs)}
                        </div>
                    </div>
                </div>
                <div class="dashboard-flip dashboard-flip-flipable cursor-pointer" data-tx-filter="realized_loss" title="Survoler pour le détail">
                    <div class="dashboard-flip-inner">
                        <div class="${flipFront} hover:ring-2 hover:ring-rose-400/50 min-h-0 h-full">
                            <div class="flex items-center justify-between w-full gap-2 px-1">
                                <div class="min-w-0">
                                    <p class="text-teal-500 text-sm font-medium">🔓 Perte Figée</p>
                                    <p class="text-2xl sm:text-3xl font-bold text-rose-500 mt-1 leading-tight flex flex-wrap items-baseline gap-x-1">${formatCurrency(data.realized_loss ?? 0)}${_pctOfInvestedHtml(data.realized_loss ?? 0, investedForRoi, 'text-lg font-bold text-rose-600 ml-1', 'loss_mag')}</p>
                                    <p class="text-xs text-teal-400 mt-0.5 leading-snug">Pertes réalisées (ventes)${data.hifo_pending ? ' — <span class="text-amber-500">HIFO : voir « Recalculer HIFO »</span>' : ' — <span class="text-teal-500/80">figées en base, ne suivent pas le cours du jour</span>'}</p>
                                </div>
                                <div class="bg-rose-100 p-3 rounded-full shrink-0">
                                    <i class="fas fa-unlock text-rose-400 text-xl"></i>
                                </div>
                            </div>
                        </div>
                        <div class="${flipBack}">
                            ${buildDashboardCardTableBack('realized_loss', txs)}
                        </div>
                    </div>
                </div>
            </div>

            <!-- PnL net figé vs PnL net actuel (latent) -->
            <div class="order-5 lg:order-none lg:col-span-12 lg:row-start-3 grid grid-cols-1 md:grid-cols-2 gap-4">
                <div class="${cardStd} border border-amber-300/35 bg-gradient-to-br from-amber-50/90 to-white/80">
                    <div class="flex items-start justify-between gap-3">
                        <div class="min-w-0 flex-1">
                            <p class="text-amber-800 text-sm font-semibold flex items-center gap-2">
                                <i class="fas fa-anchor text-amber-600"></i>
                                PnL figé (réalisé)
                            </p>
                            <p class="text-[11px] text-amber-900/70 mt-1 leading-snug">Sur les ventes déjà passées — figé en base après « Recalculer HIFO » (ne bouge pas avec le cours du jour).</p>
                            <p class="text-2xl sm:text-3xl font-extrabold mt-3 ${pnlFigedPos ? 'text-emerald-600' : 'text-rose-600'} flex flex-wrap items-baseline gap-x-1">
                                ${_signedCurrency(pnlFigedNet)}${_pctOfInvestedHtml(pnlFigedNet, investedForRoi, `text-lg font-bold ml-1 ${pnlFigedPos ? 'text-emerald-700' : 'text-rose-600'}`)}
                            </p>
                            <p class="text-xs text-amber-900/80 mt-2 leading-relaxed">
                                <span class="text-emerald-700 font-semibold">+${formatCurrency(data.realized_gain ?? 0)}</span>
                                <span class="text-amber-600/80 mx-1">−</span>
                                <span class="text-rose-600 font-semibold">${formatCurrency(data.realized_loss ?? 0)}</span>
                                ${data.hifo_pending ? '<span class="block text-[10px] text-amber-600 mt-1">Estimation tant que le HIFO n’est pas recalculé.</span>' : ''}
                            </p>
                        </div>
                        <div class="bg-amber-100/90 p-3 sm:p-4 rounded-full shrink-0">
                            <i class="fas fa-lock text-amber-600 text-xl sm:text-2xl"></i>
                        </div>
                    </div>
                </div>
                <div class="${cardStd} border border-sky-300/35 bg-gradient-to-br from-sky-50/90 to-white/80">
                    <div class="flex items-start justify-between gap-3">
                        <div class="min-w-0 flex-1">
                            <p class="text-sky-900 text-sm font-semibold flex items-center gap-2">
                                <i class="fas fa-bolt text-sky-600"></i>
                                PnL actuel (latent)
                            </p>
                            <p class="text-[11px] text-sky-900/70 mt-1 leading-snug">Sur les tokens encore en portefeuille — évolue avec les prix du marché.</p>
                            <p class="text-2xl sm:text-3xl font-extrabold mt-3 ${pnlLatentPos ? 'text-emerald-600' : 'text-rose-600'} flex flex-wrap items-baseline gap-x-1">
                                ${_signedCurrency(pnlLatentNet)}${_pctOfInvestedHtml(pnlLatentNet, investedForRoi, `text-lg font-bold ml-1 ${pnlLatentPos ? 'text-emerald-700' : 'text-rose-600'}`)}
                            </p>
                            <p class="text-xs text-sky-900/80 mt-2 leading-relaxed">
                                <span class="text-emerald-700 font-semibold">+${formatCurrency(data.total_gain ?? 0)}</span>
                                <span class="text-sky-600/80 mx-1">−</span>
                                <span class="text-rose-600 font-semibold">${formatCurrency(data.total_loss ?? 0)}</span>
                            </p>
                        </div>
                        <div class="bg-sky-100/90 p-3 sm:p-4 rounded-full shrink-0">
                            <i class="fas fa-chart-area text-sky-600 text-xl sm:text-2xl"></i>
                        </div>
                    </div>
                </div>
            </div>
        `;
    // Clic sur la carte (pas sur le bouton Agrandir — géré à part avec stopPropagation)
    dashboard.querySelectorAll('.dashboard-flip[data-tx-filter]').forEach(el => {
        el.addEventListener('click', () => showDashboardCardTransactions(el.dataset.txFilter));
    });
    dashboard.querySelectorAll('.dashboard-flip-expand-btn').forEach(btn => {
        btn.addEventListener('click', e => {
            e.stopPropagation();
            e.preventDefault();
            const f = btn.getAttribute('data-tx-expand');
            if (f) showDashboardCardTransactions(f);
        });
    });
}

async function loadDashboard() {
    if (!walletAddress) return;
    try {
        const wq = encodeURIComponent(walletAddress);
        const skip = !_hifoDetailLoaded;
        const hifoQs = skip ? '&skip_hifo=1' : '';
        const txParams = `?wallet=${wq}&limit=200${skip ? '&skip_hifo=1' : ''}`;
        const [dashRes, txRes] = await Promise.all([
            fetch(`${API_URL}/dashboard?wallet=${wq}${hifoQs}`),
            fetch(`${API_URL}/all-transactions${txParams}`)
        ]);
        if (!dashRes.ok) {
            console.warn('loadDashboard:', dashRes.status);
            return;
        }
        let data = await dashRes.json();
        if (data.detail) {
            showNotification(String(data.detail), 'error');
            return;
        }
        data = _applyStableHifoOverlay(data);
        if (!data.hifo_pending) {
            _hifoDetailLoaded = true;
            _lastStableHifo = { wallet: walletAddress, rg: data.realized_gain ?? 0, rl: data.realized_loss ?? 0 };
        }
        let txs = [];
        if (txRes.ok) try { txs = await txRes.json(); } catch (_) {}
        allTransactions = txs;
        renderDashboard(data, txs);
    } catch (error) {
        console.error('Erreur chargement dashboard:', error);
        showNotification('Erreur de chargement des données', 'error');
    }
}

function showDashboardCardTransactions(filter) {
    const sells = (allTransactions || []).filter(t => t.tx_type === 'sell' && t.pnl_usd != null);
    let txs = [];
    const titles = {
        gains: '📈 Ventes avec gain (réalisé HIFO — historique)',
        losses: '📉 Ventes avec perte (réalisé HIFO — historique)',
        realized_gain: '🔒 Gain Figé — Gains réalisés',
        realized_loss: '🔓 Perte Figée — Pertes réalisées',
        net: '✨ Résultat net — Détail des ventes (réalisé)'
    };
    if (filter === 'gains' || filter === 'realized_gain') {
        txs = sells.filter(t => t.pnl_usd > 0);
    } else if (filter === 'losses' || filter === 'realized_loss') {
        txs = sells.filter(t => t.pnl_usd < 0);
    } else if (filter === 'net') {
        txs = sells;
    }
    const totalPnl = sumUsdAsDisplayed(txs, 'pnl_usd');
    const totalAmount = sumUsdAsDisplayed(txs, 'amount_usd');
    const allHaveHifoCost =
        txs.length > 0 &&
        txs.every((t) => t.cost_usd != null && Number.isFinite(Number(t.cost_usd)) && Number(t.cost_usd) > 1e-9);
    const totalHifoCost = allHaveHifoCost
        ? txs.reduce((s, t) => s + Math.round(Number(t.cost_usd) * 100) / 100, 0)
        : 0;
    const totalPnlVsCostPct =
        allHaveHifoCost && totalHifoCost > 1e-6
            ? formatRealizedPnlVsHifoCostPct(totalPnl, totalHifoCost)
            : null;
    document.getElementById('dashboard-tx-modal-title').textContent = titles[filter] || 'Transactions';
    let subNet = '';
    if (filter === 'net') {
        subNet = `Tableau = P/L réalisé HIFO par vente. La carte « Résultat net » du dashboard inclut aussi le P/L latent (positions ouvertes).`;
    } else if (filter === 'gains' || filter === 'losses') {
        subNet =
            'Les cartes « Gains / Pertes totaux » du dashboard = P/L latent (positions ouvertes). Ce tableau = ventes déjà passées.';
    }
    document.getElementById('dashboard-tx-modal-subtitle').textContent = subNet
        ? `${subNet} — ${txs.length} vente${txs.length !== 1 ? 's' : ''}`
        : `${txs.length} vente${txs.length !== 1 ? 's' : ''}`;
    document.getElementById('dashboard-tx-summary').innerHTML = `
        <div class="bg-sky-50 rounded-xl p-4 text-center">
            <p class="text-sm text-sky-500 mb-1">Nombre</p>
            <p class="font-bold text-sky-800 text-xl">${txs.length}</p>
        </div>
        <div class="bg-teal-50 rounded-xl p-4 text-center">
            <p class="text-sm text-teal-500 mb-1">Total vendu</p>
            <p class="font-bold text-teal-800 text-lg">${formatCurrency(totalAmount)}</p>
        </div>
        <div class="bg-${totalPnl >= 0 ? 'emerald' : 'rose'}-50 rounded-xl p-4 text-center">
            <p class="text-sm text-${totalPnl >= 0 ? 'emerald' : 'rose'}-500 mb-1">PnL total</p>
            <p class="font-bold text-${totalPnl >= 0 ? 'emerald' : 'rose'}-800 text-lg">${totalPnl >= 0 ? '+' : ''}${formatCurrency(totalPnl)}</p>
            ${
                totalPnlVsCostPct
                    ? `<p class="text-xs text-gray-600 mt-1.5 font-medium leading-tight" title="PnL total ÷ somme des coûts HIFO (ventes listées)">(${totalPnlVsCostPct} du coût HIFO total)</p>`
                    : ''
            }
        </div>`;
    const tbody = document.getElementById('dashboard-tx-body');
    if (txs.length === 0) {
        tbody.innerHTML = `<tr><td colspan="7" class="text-center py-10 text-gray-400 text-base">Aucune transaction correspondante</td></tr>`;
    } else {
        tbody.innerHTML = txs.map(tx => {
            const date = formatDateFr(tx.tx_timestamp || tx.tx_date);
            const pnl = tx.pnl_usd ?? 0;
            const pnlColor = pnl >= 0 ? 'emerald-600' : 'rose-500';
            const pnlPctStr = formatRealizedPnlVsHifoCostPct(pnl, tx.cost_usd);
            const pnlPctHtml = pnlPctStr
                ? ` <span class="text-gray-600 font-semibold text-base" title="P/L ÷ coût HIFO de la vente">(${pnlPctStr})</span>`
                : '';
            const costHint = tx.cost_usd != null ? `<br><span class="text-gray-500 text-sm leading-tight">coût HIFO ${formatCurrency(tx.cost_usd)}</span>` : '';
            const sigLink = tx.transaction_signature ? `<a href="https://solscan.io/tx/${tx.transaction_signature}" target="_blank" rel="noopener" class="text-teal-500 hover:text-teal-700 ml-1.5 inline-flex align-middle" title="Solscan"><i class="fas fa-external-link-alt text-sm"></i></a>` : '';
            return `<tr class="border-t border-teal-100/80 hover:bg-teal-50/50 transition">
                <td class="py-3.5 px-2 sm:px-3 text-gray-600 whitespace-nowrap align-top">${date}${sigLink}</td>
                <td class="py-3.5 px-2 sm:px-3 align-top"><span class="inline-flex px-2.5 py-1 bg-rose-100 text-rose-700 rounded-full text-sm font-semibold">📤 Vente</span></td>
                <td class="py-3.5 px-2 sm:px-3 font-semibold text-teal-900 align-top">${tx.token_name || '—'}</td>
                <td class="py-3.5 px-2 sm:px-3 text-right font-mono text-sky-800 text-[0.95rem] align-top">${formatNumber(tx.token_amount || 0)}</td>
                <td class="py-3.5 px-2 sm:px-3 text-right font-mono text-teal-700 text-[0.95rem] align-top">${formatPrice(tx.price_usd || 0)}</td>
                <td class="py-3.5 px-2 sm:px-3 text-right font-bold text-teal-900 align-top">${formatCurrency(tx.amount_usd || 0)}</td>
                <td class="py-3.5 px-2 sm:px-3 text-right font-bold text-${pnlColor} text-lg align-top">${pnl >= 0 ? '+' : ''}${formatCurrency(pnl)}${pnlPctHtml}${costHint}</td>
            </tr>`;
        }).join('');
    }
    document.getElementById('dashboard-tx-modal').classList.remove('hidden');
    document.body.style.overflow = 'hidden';
}

function closeDashboardTxModal() {
    document.getElementById('dashboard-tx-modal').classList.add('hidden');
    document.body.style.overflow = '';
}

function openReferenceCapitalModal() {
    if (!walletAddress) {
        showNotification('Validez d’abord une adresse wallet.', 'error');
        return;
    }
    const m = document.getElementById('reference-capital-modal');
    const inp = document.getElementById('reference-capital-input');
    const addInp = document.getElementById('reference-capital-add-input');
    if (!m || !inp) return;
    if (addInp) addInp.value = '';
    void (async () => {
        try {
            const r = await fetch(
                `${API_URL}/settings/reference-capital?wallet=${encodeURIComponent(walletAddress)}`
            );
            const d = r.ok ? await r.json() : {};
            inp.value = d.amount_usd > 0 ? String(d.amount_usd) : '';
        } catch {
            inp.value = '';
        }
        m.classList.remove('hidden');
        document.body.style.overflow = 'hidden';
        inp.focus();
    })();
}

function closeReferenceCapitalModal() {
    const m = document.getElementById('reference-capital-modal');
    if (m) m.classList.add('hidden');
    document.body.style.overflow = '';
}

async function saveReferenceCapitalFromModal() {
    const inp = document.getElementById('reference-capital-input');
    const raw = inp?.value?.trim();
    if (!walletAddress) return;
    const v = raw === '' ? 0 : parseFloat(String(raw).replace(',', '.'));
    if (Number.isNaN(v) || v < 0) {
        showNotification('Montant invalide', 'error');
        return;
    }
    try {
        const r = await fetch(`${API_URL}/settings/reference-capital`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ wallet_address: walletAddress, amount_usd: v }),
        });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) {
            showNotification(typeof d.detail === 'string' ? d.detail : 'Erreur', 'error');
            return;
        }
        closeReferenceCapitalModal();
        showNotification(v > 0 ? 'Total dépensé enregistré' : 'Total dépensé remis à 0', 'success');
        await loadDashboard();
    } catch (e) {
        console.error(e);
        showNotification('Erreur réseau', 'error');
    }
}

async function clearReferenceCapitalFromModal() {
    if (!walletAddress) return;
    try {
        const r = await fetch(`${API_URL}/settings/reference-capital`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ wallet_address: walletAddress, amount_usd: 0 }),
        });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) {
            showNotification(typeof d.detail === 'string' ? d.detail : 'Erreur', 'error');
            return;
        }
        closeReferenceCapitalModal();
        showNotification('Total dépensé remis à 0', 'success');
        await loadDashboard();
    } catch (e) {
        console.error(e);
        showNotification('Erreur réseau', 'error');
    }
}

function fillReferenceCapitalFromPatrimoine() {
    const inp = document.getElementById('reference-capital-input');
    if (!inp) return;
    const p = Number(window._lastPatrimoineUsd);
    if (!Number.isFinite(p) || p <= 0) {
        showNotification('Patrimoine actuel inconnu : actualisez le dashboard puis réessayez.', 'error');
        return;
    }
    inp.value = String(Math.round(p * 100) / 100);
    showNotification('Champ rempli avec le patrimoine actuel — enregistrez pour fixer le total.', 'success');
}

async function addReferenceCapitalDepositFromModal() {
    const addInp = document.getElementById('reference-capital-add-input');
    if (!walletAddress || !addInp) return;
    const raw = addInp.value?.trim();
    const v = raw === '' ? NaN : parseFloat(String(raw).replace(',', '.'));
    if (Number.isNaN(v) || v <= 0) {
        showNotification('Indiquez un montant > 0 à ajouter', 'error');
        return;
    }
    try {
        const r = await fetch(`${API_URL}/settings/reference-capital/add`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ wallet_address: walletAddress, add_usd: v }),
        });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) {
            showNotification(typeof d.detail === 'string' ? d.detail : 'Erreur', 'error');
            return;
        }
        addInp.value = '';
        closeReferenceCapitalModal();
        showNotification(`+${formatCurrency(v)} ajouté — total : ${formatCurrency(d.amount_usd ?? 0)}`, 'success');
        await loadDashboard();
    } catch (e) {
        console.error(e);
        showNotification('Erreur réseau', 'error');
    }
}

let showArchivedTokens = false;

const TOKENS_GRID_PAGE = 6;
let _tokensGridVisibleCount = TOKENS_GRID_PAGE;
/** Dernière liste passée à renderTokens (pour « Afficher plus » sans refetch). */
let _lastAllTokensForGrid = null;

function _investedUsdApprox(t) {
    if (t.sol_usd_at_buy) return (t.invested_amount || 0) * t.sol_usd_at_buy;
    return t.invested_amount || 0;
}

/** Tri : valeur actuelle USD décroissante, puis montant investi (proxy « plus d’argent »). */
function sortTokensByCurrentValue(tokens) {
    return [...tokens].sort((a, b) => {
        const va = (Number(b.current_value) || 0) - (Number(a.current_value) || 0);
        if (va !== 0) return va;
        return _investedUsdApprox(b) - _investedUsdApprox(a);
    });
}

async function renderTokens(allTokens, options = {}) {
    if (!allTokens) return;
    if (!Array.isArray(allTokens)) {
        console.error('renderTokens: réponse inattendue (pas un tableau)', allTokens);
        allTokens = [];
    }
    const resetPaging = options.resetPaging !== false;
    try {
        if (resetPaging) _tokensGridVisibleCount = TOKENS_GRID_PAGE;
        _lastAllTokensForGrid = allTokens;

        // Sépare les tokens actifs (solde > 0 ou valeur > 0.01$) des archivés (rug/soldout)
        const activeTokens   = allTokens.filter(t => (t.current_tokens || 0) > 0 || (t.current_value || 0) >= 0.01);
        const archivedTokens = allTokens.filter(t => (t.current_tokens || 0) <= 0 && (t.current_value || 0) < 0.01);
        const tokens = showArchivedTokens ? allTokens : activeTokens;
        const sortedTokens = sortTokensByCurrentValue(tokens);
        const activeSorted = sortTokensByCurrentValue(activeTokens);

        const grid = document.getElementById('tokens-grid');
        if (allTokens.length === 0) {
            grid.innerHTML = `
                <div class="md:col-span-2 xl:col-span-3 glass rounded-2xl p-10 text-center text-gray-500">
                    <i class="fas fa-inbox text-5xl mb-4 block"></i>
                    <p class="text-lg">Aucun token pour le moment. Ajoutez-en un !</p>
                </div>
            `;
            return;
        }

        // Bouton toggle tokens archivés
        const toggleBtn = archivedTokens.length > 0 ? `
            <div class="md:col-span-2 xl:col-span-3 text-center mt-2">
                <button type="button" onclick="showArchivedTokens=!showArchivedTokens;loadTokens()" class="px-4 py-2 text-sm rounded-full border border-gray-300 text-gray-500 hover:bg-gray-100 transition">
                    ${showArchivedTokens ? '🙈 Masquer' : '👁️ Afficher'} les tokens archivés (${archivedTokens.length} token${archivedTokens.length > 1 ? 's' : ''} à 0)
                </button>
            </div>` : '';

        if (sortedTokens.length === 0) {
            grid.innerHTML = `
                <div class="md:col-span-2 xl:col-span-3 glass rounded-2xl p-10 text-center text-gray-500">
                    <i class="fas fa-inbox text-5xl mb-4 block"></i>
                    <p class="text-lg">Tous les tokens ont été vendus.</p>
                </div>
            ` + toggleBtn;
            return;
        }

        const visibleTokens = sortedTokens.slice(0, _tokensGridVisibleCount);
        const remaining = sortedTokens.length - visibleTokens.length;
        const nextStep = remaining > 0 ? Math.min(TOKENS_GRID_PAGE, remaining) : 0;

        const showMoreRow =
            remaining > 0
                ? `<div class="md:col-span-2 xl:col-span-3 flex justify-center mt-2 mb-2">
                <button type="button" id="tokens-show-more-btn" class="px-6 py-3 rounded-xl font-semibold bg-teal-600 text-white hover:bg-teal-500 shadow transition">
                    Afficher ${nextStep} de plus <span class="opacity-90 font-normal">(${remaining} restant${remaining > 1 ? 's' : ''})</span>
                </button>
            </div>`
                : '';

        grid.innerHTML = visibleTokens.map(token => {
            const addr = (token.address && String(token.address)) || '';
            const addrShort =
                addr.length >= 14 ? `${addr.substring(0, 8)}...${addr.substring(addr.length - 6)}` : addr || '—';
            const dexHref = addr
                ? `https://dexscreener.com/solana/${addr}${walletAddress ? '?maker=' + encodeURIComponent(walletAddress) : ''}`
                : '#';
            const current = token.current_price || 0;
            const buy    = token.purchase_price_usd ?? token.purchase_price ?? 0;
            const currentValue = token.current_value || 0;

            // Gain vs prix d'achat — tout en USD (même net que le dashboard / P/L latent HIFO)
            const profitLoss = (token.gain || 0) - (token.loss || 0);
            const hasHifoPct = token.latent_pnl_pct != null && Number.isFinite(Number(token.latent_pnl_pct));
            const hifoPctNum = hasHifoPct ? Number(token.latent_pnl_pct) : null;
            // Helius : invested_amount en SOL → multiplier par sol_usd_at_buy ; Manuel : déjà en USD
            const invested_usd = token.sol_usd_at_buy
                ? (token.invested_amount || 0) * token.sol_usd_at_buy
                : (token.invested_amount || 0);
            // % vs coût HIFO des lots détenus (aligné carte P/L latent), pas vs total historique investi
            let profitLossPct;
            if (hasHifoPct && hifoPctNum != null) {
                profitLossPct = hifoPctNum.toFixed(2);
            } else if (invested_usd > 0) {
                profitLossPct = ((profitLoss / invested_usd) * 100).toFixed(2);
            } else {
                const ct = Number(token.current_tokens) || 0;
                const costOpen = buy > 0 && ct > 0 ? buy * ct : 0;
                profitLossPct = costOpen > 0 ? ((profitLoss / costOpen) * 100).toFixed(2) : '0.00';
            }

            // Gain vs 24h
            const p24h = token.price_24h_ago;
            const has24h = p24h !== null && p24h > 0;
            const change24h = has24h ? ((current - p24h) / p24h * 100).toFixed(2) : null;

            const gainColor = profitLoss >= 0 ? 'green' : 'red';
            const sign = profitLoss >= 0 ? '+' : '';
            const sign24 = change24h >= 0 ? '+' : '';
            const color24 = change24h >= 0 ? 'green' : 'red';

            const hifoNet = (token.gain || 0) - (token.loss || 0);
            const hifoNetTextClass =
                hifoNet > 0 ? 'text-emerald-600' : hifoNet < 0 ? 'text-rose-600' : 'text-slate-700';
            const hifoNetSign = hifoNet > 0 ? '+' : '';
            const hifoPctTextClass = !hasHifoPct
                ? 'text-slate-400'
                : hifoPctNum > 0
                  ? 'text-emerald-700'
                  : hifoPctNum < 0
                    ? 'text-rose-600'
                    : 'text-slate-600';

            return `
                <div class="glass rounded-2xl shadow-xl p-4 sm:p-6 card-hover transition-all flex flex-col gap-4 min-w-0">
                    <!-- En-tête -->
                    <div class="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between min-w-0">
                        <div class="min-w-0 flex-1">
                            <h3 class="text-base sm:text-xl font-bold text-teal-800 break-words leading-snug">${token.name}</h3>
                            <p class="text-xs text-teal-400 font-mono mt-1 break-all">${addrShort}</p>
                            <a href="${dexHref}" target="_blank" rel="noopener" class="inline-flex items-center gap-1 mt-1.5 text-xs text-teal-500 hover:text-teal-700 font-medium" title="${walletAddress ? 'Voir sur DexScreener avec vos achats/ventes' : 'Voir sur DexScreener'}">
                                <i class="fas fa-chart-line"></i> DexScreener
                            </a>
                        </div>
                        <div class="flex flex-wrap gap-1.5 sm:gap-2 items-center justify-start sm:justify-end w-full sm:w-auto min-w-0">
                            <button type="button" onclick="showTokenTransactions(${token.id}, '${token.name.replace(/'/g, "\\'")}')"
                                class="text-xs font-semibold px-2 py-1 rounded-lg bg-violet-100 text-violet-700 hover:bg-violet-200 transition"
                                title="Voir le tableau des transactions">
                                <i class="fas fa-table mr-1"></i>Tableau
                            </button>
                            <button type="button" onclick="viewChart(${token.id})"
                                class="text-xs font-semibold px-2 py-1 rounded-lg bg-teal-100 text-teal-800 hover:bg-teal-200 transition"
                                title="Prix enregistrés + P/L actuel du token">
                                <i class="fas fa-chart-line mr-1"></i>Graphique
                            </button>
                            <button type="button" onclick="editToken(${token.id})" class="text-sky-400 hover:text-sky-600 text-lg p-0.5" title="Modifier"><i class="fas fa-edit"></i></button>
                            <button type="button" onclick="deleteToken(${token.id}, '${token.name.replace(/'/g, "\\'")}')" class="text-rose-400 hover:text-rose-600 text-lg p-0.5" title="Supprimer"><i class="fas fa-trash"></i></button>
                        </div>
                    </div>

                    <!-- Prix -->
                    <div class="grid grid-cols-2 gap-2 sm:gap-3 min-w-0">
                        <div class="bg-teal-50 rounded-xl p-2.5 sm:p-3 text-center min-w-0">
                            <p class="text-xs text-teal-500 mb-1">Prix d'achat</p>
                            <p class="font-bold text-teal-700 text-sm sm:text-base break-all">${formatPrice(buy)}</p>
                        </div>
                        <div class="bg-cyan-50 rounded-xl p-2.5 sm:p-3 text-center min-w-0">
                            <p class="text-xs text-cyan-500 mb-1">Prix actuel</p>
                            <p class="font-bold text-cyan-700 text-sm sm:text-base break-all">${formatPrice(current)}</p>
                            ${token.price_is_stale ? `<p class="text-xs text-orange-600 mt-1">⚠️ Ancien prix</p>` : ''}
                        </div>
                    </div>
                    
                    ${token.price_warning ? `
                    <div class="bg-orange-50 border-l-4 border-orange-400 rounded-lg p-3">
                        <p class="text-xs text-orange-700"><i class="fas fa-exclamation-triangle mr-1"></i>${token.price_warning}</p>
                    </div>
                    ` : ''}

                    <!-- Tokens & Valeur -->
                    <div class="grid grid-cols-2 gap-2 sm:gap-3 min-w-0">
                        <div class="bg-sky-50 rounded-xl p-2.5 sm:p-3 text-center min-w-0">
                            <p class="text-xs text-sky-500 mb-1">Tokens</p>
                            <p class="font-bold text-sky-700 text-sm sm:text-base break-all">${formatNumber(token.current_tokens || 0)}</p>
                        </div>
                        <div class="bg-emerald-50 rounded-xl p-2.5 sm:p-3 text-center min-w-0">
                            <p class="text-xs text-emerald-500 mb-1">Valeur actuelle</p>
                            <p class="font-bold text-emerald-700 text-sm sm:text-base break-all">${formatCurrency(currentValue)}</p>
                        </div>
                    </div>

                    <!-- Gain vs achat -->
                    <div class="bg-${gainColor === 'green' ? 'emerald' : 'rose'}-50 rounded-xl p-2.5 sm:p-3 flex items-center justify-between gap-2 min-w-0">
                        <div class="min-w-0 flex-1">
                            <p class="text-xs text-teal-500">📈 Gain vs achat</p>
                            <p class="font-bold text-${gainColor === 'green' ? 'emerald-600' : 'rose-500'} text-base sm:text-xl">${sign}${profitLossPct}%</p>
                            <p class="text-xs text-${gainColor === 'green' ? 'emerald-500' : 'rose-400'} mt-0.5 break-all">${sign}${formatCurrency(profitLoss)}</p>
                        </div>
                        <div class="text-right text-[10px] sm:text-xs text-gray-400 shrink-0 max-w-[45%]">
                            <p>${formatPrice(buy)}</p>
                            <p class="text-gray-300">↓</p>
                            <p class="font-semibold text-${gainColor === 'green' ? 'emerald-600' : 'rose-400'}">${formatPrice(current)}</p>
                        </div>
                    </div>

                    <!-- P/L latent HIFO — net mis en avant (aligné dashboard) -->
                    <div class="rounded-xl px-2.5 sm:px-3 py-3 sm:py-4 bg-slate-50 border border-slate-200/90 text-center min-w-0">
                        <p class="text-[10px] sm:text-xs text-slate-500 font-medium mb-2">📊 P/L latent (HIFO)</p>
                        <p class="text-2xl sm:text-4xl font-extrabold tracking-tight leading-tight break-all ${hifoNetTextClass}">
                            ${hifoNetSign}${formatCurrency(hifoNet)}
                        </p>
                        <p class="text-[10px] text-slate-400 mt-1.5 mb-2">Net position ouverte</p>
                        <div class="border-t border-slate-200/80 pt-2.5">
                            <p class="text-[10px] sm:text-[11px] text-slate-500 leading-snug">vs coût HIFO des lots détenus</p>
                            <p class="text-lg sm:text-xl font-bold mt-0.5 ${hifoPctTextClass}">${formatSignedPercent(token.latent_pnl_pct)}</p>
                        </div>
                    </div>

                    <!-- Gain vs 24h -->
                    <div class="bg-${has24h ? (color24 === 'green' ? 'emerald' : 'rose') : 'gray'}-50 rounded-xl p-2.5 sm:p-3 flex items-center justify-between gap-2 min-w-0">
                        <div class="min-w-0 flex-1">
                            <p class="text-xs text-teal-500">🕐 Gain vs 24h</p>
                            <p class="font-bold text-${has24h ? (color24 === 'green' ? 'emerald-600' : 'rose-500') : 'gray-400'} text-base sm:text-xl">
                                ${has24h ? `${sign24}${change24h}%` : 'Pas encore de données 24h'}
                            </p>
                        </div>
                        ${has24h ? `
                        <div class="text-right text-[10px] sm:text-xs text-gray-400 shrink-0 max-w-[45%]">
                            <p>${formatPrice(p24h)}</p>
                            <p class="text-gray-300">↓</p>
                            <p class="font-semibold text-${color24 === 'green' ? 'emerald-600' : 'rose-400'}">${formatPrice(current)}</p>
                        </div>` : ''}
                    </div>
                </div>
            `;
        }).join('') + showMoreRow + toggleBtn;

        document.getElementById('tokens-show-more-btn')?.addEventListener('click', () => {
            _tokensGridVisibleCount += TOKENS_GRID_PAGE;
            if (_lastAllTokensForGrid) renderTokens(_lastAllTokensForGrid, { resetPaging: false });
        });
    } catch (error) {
        console.error('Erreur renderTokens:', error);
    }
}

async function loadTokens() {
    if (!walletAddress) {
        await renderTokens([]);
        return;
    }
    try {
        const url = `${API_URL}/tokens?wallet=${encodeURIComponent(walletAddress)}`;
        const res = await fetch(url);
        const allTokens = await res.json();
        await renderTokens(allTokens);
    } catch (error) {
        showNotification('Erreur de chargement des tokens', 'error');
    }
}

// === RENDER / LOAD TRANSACTIONS ===
function renderTransactions(txs) {
    if (!txs) return;
    allTransactions = txs;
    const tbody = document.getElementById('transactions-body');
    if (!tbody) return;
    if (!allTransactions || allTransactions.length === 0) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="7" class="text-center py-8 text-gray-500">
                        <i class="fas fa-history text-4xl mb-4"></i>
                        <p>Aucune transaction pour le moment</p>
                    </td>
                </tr>
            `;
        return;
    }

    // Afficher seulement les premières N transactions
        const displayedTxs = allTransactions.slice(0, transactionsToShow);
        const hasMore = allTransactions.length > transactionsToShow;

        tbody.innerHTML = displayedTxs.map(tx => {
            const date = formatDateFr(tx.tx_timestamp || tx.tx_date);
            const sig = tx.transaction_signature || tx.signature;
            const sigLink = sig
                ? `<a href="https://solscan.io/tx/${sig}" target="_blank" class="text-teal-400 hover:text-teal-600 ml-1" title="Voir sur Solscan"><i class="fas fa-external-link-alt text-xs"></i></a>`
                : '';
            const isSell = tx.tx_type === 'sell';
            const typeBadge = isSell
                ? '<span class="px-2 py-0.5 bg-rose-100 text-rose-600 rounded-full text-xs font-semibold">📤 Vente</span>'
                : '<span class="px-2 py-0.5 bg-sky-100 text-sky-600 rounded-full text-xs font-semibold">🛒 Achat</span>';

            // Colonne Gain / Perte (ventes uniquement) — même format que la modale token
            let pnlCell = '<td class="px-4 py-3 text-right text-gray-300">—</td>';
            if (isSell && tx.pnl_usd !== null && tx.pnl_usd !== undefined) {
                const gain = tx.pnl_usd;
                const sign = gain >= 0 ? '+' : '';
                const color = gain >= 0 ? 'emerald-600' : 'rose-500';
                const pnlPctStr = formatRealizedPnlVsHifoCostPct(gain, tx.cost_usd);
                const pnlPctHtml = pnlPctStr
                    ? ` <span class="text-gray-500 font-semibold" style="font-size:0.85rem" title="P/L ÷ coût HIFO">(${pnlPctStr})</span>`
                    : '';
                const costHint = tx.cost_usd !== null
                    ? `<br><span class="text-gray-400" style="font-size:0.68rem">coût HIFO ${formatCurrency(tx.cost_usd)}</span>`
                    : '';
                pnlCell = `<td class="px-4 py-3 text-right font-semibold text-${color}">${sign}${formatCurrency(gain)}${pnlPctHtml}${costHint}</td>`;
            }

            return `
                <tr class="border-b hover:bg-teal-50/40 transition-colors">
                    <td class="px-4 py-3 text-gray-500 text-sm whitespace-nowrap">${date}${sigLink}</td>
                    <td class="px-4 py-3">${typeBadge}</td>
                    <td class="px-4 py-3">
                        <div class="font-semibold text-gray-800">${tx.token_name || 'N/A'}</div>
                        ${tx.token_address ? `<a href="https://solscan.io/token/${tx.token_address}" target="_blank" class="text-xs text-teal-400 hover:text-teal-600 font-mono">${tx.token_address.substring(0,8)}… <i class="fas fa-external-link-alt"></i></a>` : ''}
                    </td>
                    <td class="px-4 py-3 text-right font-mono text-sky-700">${formatNumber(tx.token_amount || 0)}</td>
                    <td class="px-4 py-3 text-right font-mono text-teal-600">${formatPrice(tx.price_usd || 0)}</td>
                    <td class="px-4 py-3 text-right font-semibold text-teal-800">${formatCurrency(tx.amount_usd || 0)}</td>
                    ${pnlCell}
                </tr>
            `;
        }).join('');

        // Ajouter le bouton "Show more / Show less"
        if (hasMore || transactionsToShow > 5) {
            const toggleBtn = document.createElement('tr');
            const isExpanded = transactionsToShow >= allTransactions.length;
            const btnText = isExpanded ? 'Voir moins' : `Voir plus (${allTransactions.length - transactionsToShow} autres)`;
            const btnIcon = isExpanded ? 'fa-chevron-up' : 'fa-chevron-down';
            
            toggleBtn.innerHTML = `
                <td colspan="7" class="text-center py-4">
                    <button onclick="toggleTransactions()" class="px-6 py-2 bg-teal-500 hover:bg-teal-600 text-white rounded-lg font-semibold transition-all">
                        <i class="fas ${btnIcon} mr-2"></i>${btnText}
                    </button>
                </td>
            `;
            tbody.appendChild(toggleBtn);
        }
}

async function loadTransactions() {
    if (!walletAddress) {
        renderTransactions([]);
        return;
    }
    try {
        const sh = _hifoDetailLoaded ? '' : '&skip_hifo=1';
        const res = await fetch(
            `${API_URL}/all-transactions?wallet=${encodeURIComponent(walletAddress)}${sh}`
        );
        const txs = await res.json();
        renderTransactions(txs);
    } catch (error) {
        console.error('Erreur chargement transactions:', error);
    }
}

function toggleTransactions() {
    if (transactionsToShow >= allTransactions.length) {
        // Replier
        transactionsToShow = 5;
    } else {
        // Déplier complètement
        transactionsToShow = allTransactions.length;
    }
    loadTransactions();
}

// === CHARTS ===
async function updateCharts(tokens, parts = 'all') {
    const p =
        parts === 'all'
            ? ['gains', 'portfolio', 'distribution']
            : Array.isArray(parts)
              ? parts.filter((x) => ['gains', 'portfolio', 'distribution'].includes(x))
              : [];
    if (!p.length) return;
    const chartTokens = Array.isArray(tokens) ? tokens : [];

    // Évolution du gain net du portefeuille (snapshots alignés sur le dashboard)
    if (p.includes('gains')) try {
        const ctxGains = document.getElementById('gainsChart');
        if (gainsChart) gainsChart.destroy();

        let pnlSeries = [];
        if (walletAddress) {
            const dq = chartHistoryDaysQuery();
            const sep = dq ? '&' : '';
            const gainsResp = await fetch(
                `${API_URL}/wallet-pnl-history?wallet=${encodeURIComponent(walletAddress)}${sep}${dq}`
            );
            if (gainsResp.ok) pnlSeries = await gainsResp.json();
        }

        if (!walletAddress || !Array.isArray(pnlSeries) || pnlSeries.length === 0) {
            const fallback =
                chartTokens.length > 0
                    ? chartTokens.reduce((s, t) => s + (Number(t.gain) || 0) - (Number(t.loss) || 0), 0)
                    : 0;
            const lbl = walletAddress ? ["Aujourd'hui"] : ['Connectez un wallet'];
            gainsChart = new Chart(ctxGains, {
                type: 'line',
                data: {
                    labels: lbl,
                    datasets: [
                        {
                            label: walletAddress ? 'Gain / perte net (USD)' : 'Gain / perte net',
                            data: [fallback],
                            borderColor: 'rgb(13, 148, 136)',
                            backgroundColor: 'rgba(13, 148, 136, 0.12)',
                            tension: 0.35,
                            fill: true,
                            pointRadius: 5,
                            pointHoverRadius: 7,
                        },
                    ],
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: true,
                    plugins: {
                        legend: { position: 'bottom' },
                        tooltip: {
                            callbacks: {
                                label: (ctx) => ctx.dataset.label + ': ' + formatCurrency(ctx.parsed.y),
                            },
                        },
                    },
                    scales: {
                        y: {
                            beginAtZero: false,
                            ticks: { callback: (v) => formatCurrency(v) },
                        },
                    },
                },
            });
        } else {
            const labels = pnlSeries.map((d) => formatDateFr(d.date, true));
            const values = pnlSeries.map((d) => Number(d.net_pnl_usd) || 0);
            gainsChart = new Chart(ctxGains, {
                type: 'line',
                data: {
                    labels,
                    datasets: [
                        {
                            label: 'Gain / perte net (USD)',
                            data: values,
                            borderColor: 'rgb(13, 148, 136)',
                            backgroundColor: 'rgba(13, 148, 136, 0.14)',
                            tension: 0.35,
                            fill: true,
                            pointRadius: values.length > 60 ? 0 : 4,
                            pointHoverRadius: 6,
                            segment: {
                                borderColor: (ctx) =>
                                    ctx.p1.parsed.y < 0 ? 'rgb(244, 63, 94)' : 'rgb(16, 185, 129)',
                            },
                        },
                    ],
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: true,
                    plugins: {
                        legend: { position: 'bottom' },
                        tooltip: {
                            callbacks: {
                                label: (ctx) => {
                                    const i = ctx.dataIndex;
                                    const row = pnlSeries[i];
                                    const lines = [
                                        ctx.dataset.label + ': ' + formatCurrency(ctx.parsed.y),
                                    ];
                                    if (row && row.total_invested_usd != null) {
                                        lines.push(
                                            'Investi (ref.): ' + formatCurrency(row.total_invested_usd)
                                        );
                                        lines.push(
                                            'Valeur tokens: ' + formatCurrency(row.current_value_usd || 0)
                                        );
                                        lines.push('Ventes USD: ' + formatCurrency(row.withdrawn_usd || 0));
                                    }
                                    return lines;
                                },
                            },
                        },
                    },
                    scales: {
                        y: {
                            beginAtZero: false,
                            ticks: { callback: (v) => formatCurrency(v) },
                        },
                    },
                },
            });
        }
    } catch (e) {
        console.error('Erreur chargement wallet P/L history:', e);
    }

    // Portfolio Evolution Chart - Récupère l'historique réel
    if (p.includes('portfolio')) try {
        const dq = chartHistoryDaysQuery();
        const portfolioParams = new URLSearchParams();
        if (dq.length > 0) {
            const i = dq.indexOf('=');
            if (i > 0) portfolioParams.set(dq.slice(0, i), dq.slice(i + 1));
        }
        if (walletAddress) portfolioParams.set('wallet', walletAddress);
        const portfolioQs = portfolioParams.toString();
        const portfolioUrl =
            portfolioQs.length > 0
                ? `${API_URL}/portfolio-history?${portfolioQs}`
                : `${API_URL}/portfolio-history`;
        const historyResponse = await fetch(portfolioUrl);
        const historyData = await historyResponse.json();
        
        const ctxPortfolio = document.getElementById('portfolioChart');
        if (portfolioChart) portfolioChart.destroy();
        
        // Si pas d'historique, afficher la valeur actuelle seulement
        if (historyData.length === 0) {
            const currentTotal = chartTokens.reduce((sum, t) => sum + (t.current_value || 0), 0);
            portfolioChart = new Chart(ctxPortfolio, {
                type: 'line',
                data: {
                    labels: ['Aujourd\'hui'],
                    datasets: [{
                        label: 'Valeur du Portfolio ($)',
                        data: [currentTotal],
                        borderColor: 'rgb(13, 148, 136)',
                        backgroundColor: 'rgba(13, 148, 136, 0.15)',
                        tension: 0.4,
                        fill: true
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: true,
                    plugins: {
                        legend: {
                            position: 'bottom'
                        }
                    },
                    scales: {
                        y: {
                            beginAtZero: true,
                            ticks: {
                                callback: value => formatCurrency(value)
                            }
                        }
                    }
                }
            });
        } else {
            // Afficher l'historique réel
            portfolioChart = new Chart(ctxPortfolio, {
                type: 'line',
                data: {
                    labels: historyData.map(h => formatDateFr(h.date, false)),
                    datasets: [{
                        label: 'Valeur du Portfolio ($)',
                        data: historyData.map(h => h.value || 0),
                        borderColor: 'rgb(13, 148, 136)',
                        backgroundColor: 'rgba(13, 148, 136, 0.15)',
                        tension: 0.4,
                        fill: true,
                        pointRadius: historyData.length > 60 ? 0 : 4,
                        pointHoverRadius: 6
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: true,
                    plugins: {
                        legend: {
                            position: 'bottom'
                        },
                        tooltip: {
                            callbacks: {
                                label: (context) => 'Valeur: ' + formatCurrency(context.parsed.y)
                            }
                        }
                    },
                    scales: {
                        y: {
                            beginAtZero: true,
                            ticks: {
                                callback: value => formatCurrency(value)
                            }
                        }
                    }
                }
            });
        }
    } catch (error) {
        console.error('Erreur chargement historique:', error);
    }

    if (p.includes('distribution')) {
        const ctxDistribution = document.getElementById('distributionChart');
        if (distributionChart) distributionChart.destroy();

        const colors = [
            'rgb(13, 148, 136)', 'rgb(8, 145, 178)', 'rgb(5, 150, 105)',
            'rgb(2, 132, 199)', 'rgb(14, 116, 144)', 'rgb(15, 118, 110)'
        ];

        const labels =
            chartTokens.length > 0
                ? chartTokens.map((t) => t.name)
                : ['Aucune donnée'];
        const dataVals =
            chartTokens.length > 0
                ? chartTokens.map((t) =>
                      t.sol_usd_at_buy
                          ? (t.invested_amount || 0) * t.sol_usd_at_buy
                          : t.invested_amount || 0
                  )
                : [1];
        const bg =
            chartTokens.length > 0
                ? colors
                : ['rgb(200, 200, 200)'];

        distributionChart = new Chart(ctxDistribution, {
            type: 'doughnut',
            data: {
                labels,
                datasets: [
                    {
                        data: dataVals,
                        backgroundColor: bg,
                        borderWidth: 2,
                        borderColor: '#fff',
                    },
                ],
            },
            options: {
                responsive: true,
                maintainAspectRatio: true,
                plugins: {
                    legend: { position: 'bottom' },
                    tooltip: {
                        callbacks: {
                            label: function (context) {
                                const value = context.parsed;
                                const total = context.dataset.data.reduce((a, b) => a + b, 0);
                                if (!chartTokens.length || total <= 0)
                                    return context.label || '';
                                const percentage = ((value / total) * 100).toFixed(1);
                                return `${context.label}: ${formatCurrency(value)} (${percentage}%)`;
                            },
                        },
                    },
                },
            },
        });
    }
}

/**
 * USD / token pour la ligne de référence du graphique : coût moyen pondéré sur les achats importés
 * (total SOL dépensé × taux du lot, sommé, divisé par les tokens achetés). Sans lots : même idée que la fiche token,
 * avec taux SOL/USD de secours si besoin (évite une ligne à 0 quand purchase_price est en SOL).
 */
function _chartWeightedAvgBuyUsdPerToken(token, purchases) {
    const solRateFallback =
        (Number(token?.sol_usd_at_buy) > 0 ? Number(token.sol_usd_at_buy) : null) ||
        (typeof window._solPriceUsd === 'number' && window._solPriceUsd > 0 ? window._solPriceUsd : null) ||
        150;
    let totalUsd = 0;
    let totalTok = 0;
    if (Array.isArray(purchases) && purchases.length > 0) {
        for (const p of purchases) {
            const tb = Number(p.tokens_bought) || 0;
            if (tb <= 0) continue;
            const r = Number(p.sol_usd_at_buy) > 0 ? Number(p.sol_usd_at_buy) : solRateFallback;
            const sol = Number(p.sol_spent) || 0;
            totalUsd += sol * r;
            totalTok += tb;
        }
    }
    if (totalTok > 0 && totalUsd > 0) return totalUsd / totalTok;
    const pp = Number(token?.purchase_price) || 0;
    if (pp <= 0) return 0;
    const rate = Number(token?.sol_usd_at_buy) > 0 ? Number(token.sol_usd_at_buy) : solRateFallback;
    return pp * rate;
}

/** Modale : courbe de prix (historique BDD) + rappel P/L du token */
async function viewChart(tokenId) {
    const modal = document.getElementById('token-chart-modal');
    const titleEl = document.getElementById('token-chart-modal-title');
    const subtitleEl = document.getElementById('token-chart-modal-subtitle');
    const noteEl = document.getElementById('token-chart-modal-note');
    const canvas = document.getElementById('token-detail-chart');
    if (!modal || !canvas || !titleEl) return;

    modal.classList.remove('hidden');
    titleEl.textContent = 'Chargement…';
    if (subtitleEl) subtitleEl.textContent = '';
    if (noteEl) {
        noteEl.textContent =
            'Les points suivent le prix USD du token à chaque actualisation. La ligne jaune = coût moyen pondéré de vos achats importés (USD/token), plus proche de la réalité qu’un simple « prix max ». Sans lots détaillés, on utilise la moyenne du token avec le taux SOL/USD connu. Le P/L net inclut ventes et coûts réels.';
    }

    if (tokenDetailChart) {
        tokenDetailChart.destroy();
        tokenDetailChart = null;
    }

    try {
        const [histRes, tokRes, purRes] = await Promise.all([
            fetch(`${API_URL}/history/${tokenId}?limit=400`),
            fetch(`${API_URL}/tokens/${tokenId}`),
            fetch(`${API_URL}/tokens/${tokenId}/purchases`),
        ]);
        const history = histRes.ok ? await histRes.json() : [];
        let token = null;
        if (tokRes.ok) {
            token = await tokRes.json();
            if (token && token.detail) throw new Error(String(token.detail));
        } else {
            const err = await tokRes.json().catch(() => ({}));
            throw new Error(err.detail || `Erreur ${tokRes.status}`);
        }
        let purchases = purRes.ok ? await purRes.json().catch(() => []) : [];
        if (!Array.isArray(purchases)) purchases = [];

        const name = token && token.name ? String(token.name) : `Token #${tokenId}`;
        titleEl.textContent = `Graphique — ${name}`;

        const buyUsd = _chartWeightedAvgBuyUsdPerToken(token, purchases);
        const netPnl =
            token ? (Number(token.gain) || 0) - (Number(token.loss) || 0) : 0;
        const pnlPos = netPnl >= 0;
        if (subtitleEl && token) {
            const pctPart =
                token.latent_pnl_pct != null && Number.isFinite(Number(token.latent_pnl_pct))
                    ? ` · vs coût lots ${formatSignedPercent(token.latent_pnl_pct)}`
                    : '';
            subtitleEl.innerHTML = `P/L latent (HIFO) : <span class="font-bold ${pnlPos ? 'text-emerald-600' : 'text-rose-600'}">${pnlPos ? '+' : ''}${formatCurrency(netPnl)}</span>${pctPart} · Prix ${formatPrice(token.current_price || 0)}`;
        }

        let labels = [];
        let prices = [];
        if (Array.isArray(history) && history.length > 0) {
            labels = history.map((h) => formatDateFr(h.timestamp, true));
            prices = history.map((h) => Number(h.price) || 0);
        } else if (token && Number(token.current_price) > 0) {
            labels = ['Dernier prix connu'];
            prices = [Number(token.current_price)];
        } else {
            labels = ['—'];
            prices = [0];
        }

        const datasets = [
            {
                label: 'Prix (USD / token)',
                data: prices,
                borderColor: 'rgb(13, 148, 136)',
                backgroundColor: 'rgba(13, 148, 136, 0.14)',
                fill: true,
                tension: 0.25,
                pointRadius: prices.length > 100 ? 0 : 3,
                pointHoverRadius: 5,
            },
        ];

        if (buyUsd > 0 && prices.length > 0) {
            datasets.push({
                label: 'Coût moyen achat (USD / token)',
                data: prices.map(() => buyUsd),
                borderColor: 'rgb(202, 138, 4)',
                backgroundColor: 'transparent',
                borderDash: [7, 5],
                fill: false,
                pointRadius: 0,
                tension: 0,
            });
        }

        tokenDetailChart = new Chart(canvas, {
            type: 'line',
            data: { labels, datasets },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: { mode: 'index', intersect: false },
                plugins: {
                    legend: { position: 'bottom' },
                    tooltip: {
                        callbacks: {
                            label: (ctx) =>
                                `${ctx.dataset.label}: ${formatPrice(ctx.parsed.y)}`,
                        },
                    },
                },
                scales: {
                    y: {
                        beginAtZero: false,
                        ticks: { callback: (v) => formatPrice(v) },
                    },
                },
            },
        });
    } catch (e) {
        console.error('viewChart:', e);
        titleEl.textContent = 'Graphique indisponible';
        if (subtitleEl) subtitleEl.textContent = e?.message || String(e);
    }
}

function closeTokenChartModal() {
    document.getElementById('token-chart-modal')?.classList.add('hidden');
    if (tokenDetailChart) {
        tokenDetailChart.destroy();
        tokenDetailChart = null;
    }
}

// === TOKEN CRUD ===
document.getElementById('add-token-btn').addEventListener('click', () => {
    document.getElementById('modal-title').textContent = 'Ajouter un Token';
    document.getElementById('token-form').reset();
    document.getElementById('token-id').value = '';
    document.getElementById('token-modal').classList.remove('hidden');
});

document.getElementById('close-modal').addEventListener('click', closeModal);
document.getElementById('cancel-modal').addEventListener('click', closeModal);

function closeModal() {
    document.getElementById('token-modal').classList.add('hidden');
}

document.getElementById('token-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    
    const tokenId = document.getElementById('token-id').value;
    const purchasedTokens = parseFloat(document.getElementById('token-purchased').value) || 0;
    const purchasePrice = parseFloat(document.getElementById('token-price').value) || 0;
    
    const tokenData = {
        name: document.getElementById('token-name').value,
        address: document.getElementById('token-address').value,
        event: document.getElementById('token-event').value,
        purchase_date: document.getElementById('token-purchase-date').value,
        purchased_tokens: purchasedTokens,
        current_tokens: purchasedTokens,
        purchase_price: purchasePrice,
        current_price: purchasePrice,
        mcap_target: document.getElementById('token-mcap').value,
        detection_date: document.getElementById('token-detection').value,
        comments: document.getElementById('token-comments').value,
        invested_amount: purchasedTokens * purchasePrice,
        current_value: purchasedTokens * purchasePrice,
        gain: 0,
        loss: 0,
        sold_tokens: 0
    };
    
    try {
        const url = tokenId ? `${API_URL}/tokens/${tokenId}` : `${API_URL}/tokens`;
        const method = tokenId ? 'PUT' : 'POST';
        
        const response = await fetch(url, {
            method: method,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(tokenData)
        });
        
        if (response.ok) {
            showNotification(tokenId ? 'Token mis à jour!' : 'Token ajouté!', 'success');
            closeModal();
            await dbOnlyRefresh(true, true);
            await loadTokens();
        } else {
            const error = await response.json();
            showNotification(error.detail || 'Erreur lors de l\'enregistrement', 'error');
        }
    } catch (error) {
        console.error('Erreur:', error);
        showNotification('Erreur de connexion au serveur', 'error');
    }
});

// === TABLEAU DES TRANSACTIONS PAR TOKEN ===
async function showTokenTransactions(tokenId, tokenName) {
    const modal = document.getElementById('token-tx-modal');
    const title = document.getElementById('token-tx-modal-title');
    const subtitle = document.getElementById('token-tx-modal-subtitle');
    const summary = document.getElementById('token-tx-summary');
    const tbody = document.getElementById('token-tx-body');

    title.textContent = `Transactions — ${tokenName}`;
    subtitle.textContent = '';
    summary.innerHTML = '';
    tbody.innerHTML = '<tr><td colspan="6" class="text-center py-8 text-gray-400"><i class="fas fa-spinner fa-spin mr-2"></i>Chargement…</td></tr>';
    modal.classList.remove('hidden');
    document.body.style.overflow = 'hidden';

    try {
        const res = await fetch(`${API_URL}/tokens/${tokenId}/transactions`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const txs = await res.json();

        if (!txs || txs.length === 0) {
            tbody.innerHTML = '<tr><td colspan="6" class="text-center py-8 text-gray-400">Aucune transaction enregistrée.</td></tr>';
            return;
        }

        const buys = txs.filter(t => t.tx_type === 'buy');
        const sells = txs.filter(t => t.tx_type === 'sell');
        const sellCount = sells.length;
        const totalInvested = sumUsdAsDisplayed(buys, 'amount_usd');
        const totalSold = sumUsdAsDisplayed(sells, 'amount_usd');
        const totalPnl = sumUsdAsDisplayed(
            sells.filter(t => t.pnl_usd != null),
            'pnl_usd'
        );

        const pnlColor = totalPnl >= 0 ? 'emerald' : 'rose';
        const pnlSign  = totalPnl >= 0 ? '+' : '';
        summary.innerHTML = `
            <div class="bg-sky-50 rounded-xl p-3 text-center">
                <p class="text-xs text-sky-500 mb-1">💰 Total investi</p>
                <p class="font-bold text-sky-700">${formatCurrency(totalInvested)}</p>
            </div>
            <div class="bg-teal-50 rounded-xl p-3 text-center">
                <p class="text-xs text-teal-500 mb-1">📤 Total vendu</p>
                <p class="font-bold text-teal-700">${formatCurrency(totalSold)}</p>
                <p class="text-xs text-gray-400">${sellCount} vente${sellCount > 1 ? 's' : ''}</p>
            </div>
            <div class="bg-${pnlColor}-50 rounded-xl p-3 text-center">
                <p class="text-xs text-${pnlColor}-500 mb-1">📊 PnL réalisé (HIFO)</p>
                <p class="font-bold text-${pnlColor}-700">${pnlSign}${formatCurrency(totalPnl)}</p>
            </div>`;

        subtitle.textContent = `${txs.length} transaction${txs.length > 1 ? 's' : ''}`;

        // Rendu du tableau
        tbody.innerHTML = txs.map(tx => {
            const date = formatDateFr(tx.tx_timestamp || tx.tx_date);
            const isSell = tx.tx_type === 'sell';
            const typeBadge = isSell
                ? '<span class="px-2 py-0.5 bg-rose-100 text-rose-600 rounded-full text-xs font-semibold">📤 Vente</span>'
                : '<span class="px-2 py-0.5 bg-sky-100 text-sky-600 rounded-full text-xs font-semibold">🛒 Achat</span>';

            let pnlCell = '<td class="py-2 text-right text-gray-300">—</td>';
            if (isSell && tx.pnl_usd !== null && tx.pnl_usd !== undefined) {
                const gain = tx.pnl_usd;
                const sign = gain >= 0 ? '+' : '';
                const color = gain >= 0 ? 'emerald-600' : 'rose-500';
                const pnlPctStr = formatRealizedPnlVsHifoCostPct(gain, tx.cost_usd);
                const pnlPctHtml = pnlPctStr
                    ? ` <span class="text-gray-500 font-semibold text-xs" title="P/L ÷ coût HIFO">(${pnlPctStr})</span>`
                    : '';
                const costHint = tx.cost_usd !== null
                    ? `<br><span class="text-gray-400" style="font-size:0.68rem">coût HIFO ${formatCurrency(tx.cost_usd)}</span>`
                    : '';
                pnlCell = `<td class="py-2 text-right font-semibold text-${color}">${sign}${formatCurrency(gain)}${pnlPctHtml}${costHint}</td>`;
            }

            const sigLink = tx.signature
                ? `<a href="https://solscan.io/tx/${tx.signature}" target="_blank" class="text-teal-400 hover:text-teal-600 ml-1" title="Voir sur Solscan"><i class="fas fa-external-link-alt text-xs"></i></a>`
                : '';

            return `<tr class="border-t border-teal-50 hover:bg-teal-50/40 transition">
                <td class="py-2 pr-3 text-gray-500 whitespace-nowrap">${date}${sigLink}</td>
                <td class="py-2 pr-3">${typeBadge}</td>
                <td class="py-2 pr-3 text-right font-mono text-sky-700">${formatNumber(tx.token_amount || 0)}</td>
                <td class="py-2 pr-3 text-right font-mono text-teal-600">${formatPrice(tx.price_usd || 0)}</td>
                <td class="py-2 pr-3 text-right font-semibold text-teal-800">${formatCurrency(tx.amount_usd || 0)}</td>
                ${pnlCell}
            </tr>`;
        }).join('');

    } catch (err) {
        console.error('Erreur chargement transactions token:', err);
        tbody.innerHTML = `<tr><td colspan="6" class="text-center py-8 text-rose-400">Erreur : ${err.message}</td></tr>`;
    }
}

function closeTokenTxModal() {
    document.getElementById('token-tx-modal').classList.add('hidden');
    document.body.style.overflow = '';
}

async function editToken(id) {
    try {
        const response = await fetch(`${API_URL}/tokens/${id}`);
        const token = await response.json();
        
        document.getElementById('modal-title').textContent = 'Modifier le Token';
        document.getElementById('token-id').value = token.id;
        document.getElementById('token-name').value = token.name || '';
        document.getElementById('token-address').value = token.address || '';
        document.getElementById('token-event').value = token.event || '';
        document.getElementById('token-purchase-date').value = token.purchase_date || '';
        document.getElementById('token-purchased').value = token.purchased_tokens || '';
        document.getElementById('token-price').value = token.purchase_price || '';
        document.getElementById('token-mcap').value = token.mcap_target || '';
        document.getElementById('token-detection').value = token.detection_date || '';
        document.getElementById('token-comments').value = token.comments || '';
        
        document.getElementById('token-modal').classList.remove('hidden');
    } catch (error) {
        console.error('Erreur:', error);
        showNotification('Erreur de chargement du token', 'error');
    }
}

async function deleteToken(id, name) {
    if (!confirm(`Êtes-vous sûr de vouloir supprimer "${name}"?`)) return;
    
    try {
        const response = await fetch(`${API_URL}/tokens/${id}`, { method: 'DELETE' });
        if (response.ok) {
            showNotification('Token supprimé!', 'success');
            await dbOnlyRefresh(true, true);
            await loadTokens();
        }
    } catch (error) {
        console.error('Erreur:', error);
        showNotification('Erreur lors de la suppression', 'error');
    }
}

// === REFRESH PRICES ===
document.getElementById('refresh-prices').addEventListener('click', async () => {
    await actualiser();
});

document.getElementById('recalculate-history').addEventListener('click', async () => {
    const btn = document.getElementById('recalculate-history');
    const originalHtml = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin mr-2"></i>Recalcul...';
    let progressTimer = null;
    let fakePct = 10;
    try {
        _showProgress(10, 'Recalcul HIFO — lecture achats & ventes…', true);
        progressTimer = setInterval(() => {
            fakePct = Math.min(fakePct + 4, 82);
            _showProgress(fakePct, 'Recalcul HIFO — coûts des lots & gains/pertes…', true);
        }, 500);

        const params = walletAddress ? `?wallet=${encodeURIComponent(walletAddress)}` : '';
        const res = await fetch(`${API_URL}/recalculate-history${params}`, { method: 'POST' });
        const data = await res.json();

        if (progressTimer) {
            clearInterval(progressTimer);
            progressTimer = null;
        }

        if (res.ok) {
            _showProgress(90, 'Chargement dashboard & liste des transactions (HIFO)…', true);
            showNotification(`✅ ${data.message}`, 'success');
            await dbOnlyRefresh(true, false, false, true);
            _showProgress(100, 'HIFO à jour — gains, pertes et net affichés', true);
            applyChartLazyStateAndMaybeLoad();
            void heliusTransfers().catch(() => {});
        } else {
            showNotification('Erreur : ' + (data.detail || 'inconnue'), 'error');
        }
    } catch (e) {
        if (progressTimer) clearInterval(progressTimer);
        showNotification('Erreur réseau : ' + e.message, 'error');
    } finally {
        if (progressTimer) clearInterval(progressTimer);
        btn.disabled = false;
        btn.innerHTML = originalHtml;
        setTimeout(() => {
            if (!_syncInProgress && !_historyImportInProgress) _hideProgress();
        }, 900);
    }
});

// === UTILITIES ===
/** Somme alignée sur l’affichage 2 décimales (évite 0,01 $ d’écart vs addition manuelle des lignes). */
function sumUsdAsDisplayed(rows, field) {
    return rows.reduce((s, t) => {
        const v = Number(t[field]);
        if (Number.isNaN(v)) return s;
        return s + Math.round(v * 100) / 100;
    }, 0);
}

function formatCurrency(value) {
    return new Intl.NumberFormat('fr-FR', {
        style: 'currency',
        currency: 'USD',
        minimumFractionDigits: 2,
        maximumFractionDigits: 2
    }).format(value);
}

/** Pourcentage signé (fr-FR), pour P/L vs coût HIFO restant. */
function formatSignedPercent(value) {
    if (value == null || value === '') return '—';
    const n = Number(value);
    if (!Number.isFinite(n)) return '—';
    const fmt = new Intl.NumberFormat('fr-FR', {
        maximumFractionDigits: 2,
        minimumFractionDigits: 2,
    });
    const body = fmt.format(Math.abs(n)) + ' %';
    if (n > 0) return '+' + body;
    if (n < 0) return '−' + body;
    return fmt.format(n) + ' %';
}

/**
 * P/L réalisé d’une vente / coût HIFO de cette vente (×100), aligné sur pnl_usd et cost_usd du backend.
 * null si coût absent ou nul.
 */
function formatRealizedPnlVsHifoCostPct(pnlUsd, costUsd) {
    const c = Number(costUsd);
    const p = Number(pnlUsd);
    if (costUsd == null || !Number.isFinite(c) || c <= 1e-9 || !Number.isFinite(p)) return null;
    return formatSignedPercent((100 * p) / c);
}

function formatPrice(value) {
    if (value < 0.000001) {
        return value.toExponential(4);
    }
    return new Intl.NumberFormat('fr-FR', {
        style: 'currency',
        currency: 'USD',
        minimumFractionDigits: 8,
        maximumFractionDigits: 8
    }).format(value);
}

function formatNumber(value) {
    return new Intl.NumberFormat('fr-FR').format(value);
}

function formatDateFr(dateStrOrTimestamp, withTime = true) {
    if (dateStrOrTimestamp == null) return '—';
    // Si c'est un timestamp Unix (secondes), convertir en ms
    const d = typeof dateStrOrTimestamp === 'number' && dateStrOrTimestamp > 0
        ? new Date(dateStrOrTimestamp * (dateStrOrTimestamp < 1e12 ? 1000 : 1))
        : new Date(dateStrOrTimestamp);
    if (isNaN(d.getTime())) return '—';
    const opts = { day: '2-digit', month: '2-digit', year: 'numeric' };
    if (withTime) Object.assign(opts, { hour: '2-digit', minute: '2-digit' });
    return d.toLocaleDateString('fr-FR', opts);
}

function showNotification(message, type = 'info') {
    const colors = {
        success: 'bg-green-500',
        error: 'bg-red-500',
        info: 'bg-blue-500'
    };
    
    const notification = document.createElement('div');
    notification.className = `fixed top-[max(0.75rem,env(safe-area-inset-top))] left-3 right-3 sm:left-auto sm:right-4 sm:max-w-md ${colors[type]} text-white px-4 py-3 sm:px-6 sm:py-4 rounded-lg shadow-lg z-[200] text-sm sm:text-base animate-pulse-slow`;
    notification.innerHTML = `
        <div class="flex items-center gap-3">
            <i class="fas fa-${type === 'success' ? 'check-circle' : type === 'error' ? 'exclamation-circle' : 'info-circle'}"></i>
            <span>${message}</span>
        </div>
    `;
    
    document.body.appendChild(notification);
    
    setTimeout(() => {
        notification.remove();
    }, 3000);
}

// Afficher un dashboard vide au démarrage
function displayEmptyDashboard() {
    const dashboard = document.getElementById('dashboard');
    dashboard.className = 'grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6 mb-6';
    const emptyCard = (icon, iconBg, iconColor, label, sub = '') => `
        <div class="glass rounded-xl p-4 sm:p-6 shadow-md card-hover transition-all">
            <div class="flex items-center justify-between gap-3">
                <div class="min-w-0">
                    <p class="text-teal-500 text-sm font-medium">${label}</p>
                    <p class="text-2xl sm:text-3xl font-bold text-teal-200 mt-2">--</p>
                    ${sub ? `<p class="text-xs text-teal-300 mt-1">${sub}</p>` : ''}
                </div>
                <div class="${iconBg} p-3 sm:p-4 rounded-full shrink-0">
                    <i class="${icon} ${iconColor} text-xl sm:text-2xl"></i>
                </div>
            </div>
        </div>`;
    dashboard.innerHTML =
        emptyCard('fas fa-crown',        'bg-amber-100',   'text-amber-400',  '👑 Total Patrimoine',      'Tokens + SOL') +
        emptyCard('fas fa-gem',          'bg-sky-100',     'text-sky-400',    '💎 Solde SOL',             '-- SOL') +
        emptyCard('fas fa-coins',        'bg-teal-100',    'text-teal-400',   '💰 Total dépensé', 'Achats historiques') +
        emptyCard('fas fa-chart-line',   'bg-cyan-100',    'text-cyan-400',   '📊 Valeur Actuelle') +
        emptyCard('fas fa-arrow-up',     'bg-emerald-100', 'text-emerald-400','📈 Gains Totaux') +
        emptyCard('fas fa-arrow-down',   'bg-rose-100',    'text-rose-400',   '📉 Pertes Totales') +
        emptyCard('fas fa-balance-scale','bg-teal-100',    'text-teal-400',   '✨ Résultat Net') +
        emptyCard('fas fa-lock',         'bg-amber-100',   'text-amber-400',  '🔒 Gain Figé',             'Gains réalisés lors des ventes') +
        emptyCard('fas fa-unlock',       'bg-rose-100',    'text-rose-400',   '🔓 Perte Figée',           'Pertes réalisées lors des ventes');
}

// Pas de Chart.js au démarrage : placeholders + boutons « Générer »
function displayEmptyCharts() {
    if (portfolioChart) portfolioChart.destroy();
    if (distributionChart) distributionChart.destroy();
    if (gainsChart) gainsChart.destroy();
    portfolioChart = null;
    distributionChart = null;
    gainsChart = null;

    ['gains', 'portfolio', 'distribution'].forEach((w) => {
        _setChartPanelVisible(w, false);
        const lazy = document.getElementById(`${w}-chart-lazy`);
        if (lazy) lazy.classList.remove('hidden');
    });
}

// === INIT ===
document.addEventListener('DOMContentLoaded', async () => {
    displayEmptyDashboard();
    displayEmptyCharts();
    initChartPeriodFromStorage();
    document.getElementById('chart-period-select')?.addEventListener('change', () => {
        persistChartPeriod();
        applyChartLazyStateAndMaybeLoad();
    });
    ['gains', 'portfolio', 'distribution'].forEach((w) => {
        document
            .getElementById(`${w}-chart-generate-btn`)
            ?.addEventListener('click', () => userGenerateDashboardChart(w));
    });

    // Boutons page d'accueil
    const welcomeValidate = document.getElementById('welcome-validate');
    const welcomePhantom = document.getElementById('welcome-phantom');
    const welcomeInput = document.getElementById('welcome-wallet-input');
    if (welcomeValidate && welcomeInput) {
        welcomeValidate.addEventListener('click', async () => {
            const addr = welcomeInput.value.trim();
            if (!addr) { showNotification('Entrez une adresse wallet', 'error'); return; }
            if (addr.length < 20) { showNotification('Adresse trop courte', 'error'); return; }
            document.getElementById('wallet-input').value = addr;
            await applyWallet(addr);
        });
    }
    if (welcomePhantom) {
        welcomePhantom.addEventListener('click', () => document.getElementById('connect-wallet').click());
    }
    const walletInput = document.getElementById('wallet-input');
    if (welcomeInput && walletInput) {
        welcomeInput.addEventListener('input', () => { walletInput.value = welcomeInput.value; });
        walletInput.addEventListener('input', () => { welcomeInput.value = walletInput.value; });
    }

    updateAuthBar();

    document.getElementById('nav-my-addresses-btn')?.addEventListener('click', () => showAddressesPage());
    document.getElementById('nav-back-dashboard-btn')?.addEventListener('click', () => showMainDashboardView());

    let walletRestored = false;
    const token = getAuthToken();
    if (token) {
        const me = await fetchAuthMe();
        if (!me) {
            localStorage.removeItem('authToken');
            localStorage.removeItem('authUsername');
            updateAuthBar();
        } else {
            localStorage.setItem('authUsername', me.username);
            updateAuthBar();
            populateSavedWalletsDropdown(me.wallets || [], me.active_wallet);
            const followed = (me.wallets || []).filter((x) => x.follows !== false && x.follows !== 0);
            let w = me.active_wallet || localStorage.getItem('walletAddress');
            if (!w && followed.length > 0) w = followed[0].address;
            if (w && w.length >= 20) {
                document.getElementById('wallet-input').value = w;
                if (document.getElementById('welcome-wallet-input')) {
                    document.getElementById('welcome-wallet-input').value = w;
                }
                await applyWallet(w, { preferDbOnly: true });
                walletRestored = true;
            }
        }
    }

    if (!walletRestored) {
        const savedWallet = localStorage.getItem('walletAddress');
        if (savedWallet && savedWallet.length >= 20) {
            document.getElementById('wallet-input').value = savedWallet;
            if (document.getElementById('welcome-wallet-input')) {
                document.getElementById('welcome-wallet-input').value = savedWallet;
            }
            await applyWallet(savedWallet);
            walletRestored = true;
        } else {
            try {
                const r = await fetch(`${API_URL}/settings/wallet`);
                const d = await r.json();
                if (d.wallet_address && d.wallet_address.length >= 20) {
                    document.getElementById('wallet-input').value = d.wallet_address;
                    if (document.getElementById('welcome-wallet-input')) {
                        document.getElementById('welcome-wallet-input').value = d.wallet_address;
                    }
                    await applyWallet(d.wallet_address);
                    walletRestored = true;
                }
            } catch (e) {
                console.warn('Impossible de restaurer le wallet:', e);
            }
        }
    }
    if (!walletRestored) showWelcomePage();

    document.getElementById('dashboard').addEventListener('click', (e) => {
        if (e.target.closest('[data-action="open-ref-capital"]')) {
            e.preventDefault();
            openReferenceCapitalModal();
        }
    });
    document.getElementById('reference-capital-close')?.addEventListener('click', closeReferenceCapitalModal);
    document.getElementById('reference-capital-save')?.addEventListener('click', () => void saveReferenceCapitalFromModal());
    document.getElementById('reference-capital-clear')?.addEventListener('click', () => void clearReferenceCapitalFromModal());
    document.getElementById('reference-capital-fill-patrimoine')?.addEventListener('click', () => fillReferenceCapitalFromPatrimoine());
    document.getElementById('reference-capital-add-btn')?.addEventListener('click', () => void addReferenceCapitalDepositFromModal());
    document.getElementById('reference-capital-modal')?.addEventListener('click', (e) => {
        if (e.target.id === 'reference-capital-modal') closeReferenceCapitalModal();
    });

    // Pas d’actualisation automatique : évite re-sync silencieux et confusions avec les chiffres HIFO.
    // L’utilisateur utilise « Actualiser » quand il le souhaite.
});
