/**
 * Public Voter Search Portal - Frontend Logic
 * Handles real-time search, filters, pagination, and official voter slip modal.
 */

// Global Authenticated Fetch Interceptor
if (window.VoterAuth) {
    const origFetch = window.fetch;
    window.fetch = function(url, opts = {}) {
        const token = window.VoterAuth.getToken();
        if (token) {
            opts = opts || {};
            opts.headers = opts.headers || {};
            if (opts.headers instanceof Headers) {
                if (!opts.headers.has('Authorization')) opts.headers.set('Authorization', 'Bearer ' + token);
            } else {
                if (!opts.headers['Authorization']) opts.headers['Authorization'] = 'Bearer ' + token;
                if (!opts.headers['x-auth-token']) opts.headers['x-auth-token'] = token;
            }
        }
        return origFetch(url, opts).then(response => {
            if (response.status === 401 && !url.includes('/api/auth/')) {
                // Session expired or unauthenticated -> lock screen immediately
                if (typeof applyAuthState === 'function') {
                    applyAuthState(null);
                }
            }
            return response;
        });
    };
}

// State
const state = {
    page: 1,
    limit: 30,
    totalRecords: 0,
    totalFiltered: 0,
    totalPages: 1,
    records: [],
    currentSlip: null
};

// DOM Elements
const elements = {
    mainSearchInput: document.getElementById('mainSearchInput'),
    searchBtn: document.getElementById('searchBtn'),
    toggleFiltersBtn: document.getElementById('toggleFiltersBtn'),
    advancedPanel: document.getElementById('advancedPanel'),
    
    // Filter fields
    filterName: document.getElementById('filterName'),
    filterRelName: document.getElementById('filterRelName'),
    filterEpic: document.getElementById('filterEpic'),
    filterPart: document.getElementById('filterPart'),
    filterAssembly: document.getElementById('filterAssembly'),
    filterHouse: document.getElementById('filterHouse'),
    filterGender: document.getElementById('filterGender'),
    filterMinAge: document.getElementById('filterMinAge'),
    filterMaxAge: document.getElementById('filterMaxAge'),
    resetFiltersBtn: document.getElementById('resetFiltersBtn'),
    
    // Stats Pills
    statTotalVoters: document.getElementById('statTotalVoters'),
    statTotalParts: document.getElementById('statTotalParts'),
    
    // Results
    resultsTitle: document.getElementById('resultsTitle'),
    exportExcelBtn: document.getElementById('exportExcelBtn'),
    desktopTableBody: document.getElementById('desktopTableBody'),
    mobileCardsContainer: document.getElementById('mobileCardsContainer'),
    
    // Pagination
    paginationInfo: document.getElementById('paginationInfo'),
    pageIndicator: document.getElementById('pageIndicator'),
    prevBtn: document.getElementById('prevBtn'),
    nextBtn: document.getElementById('nextBtn'),
    
    // Slip Modal
    slipModal: document.getElementById('slipModal'),
    slipAssembly: document.getElementById('slipAssembly'),
    slipPartNo: document.getElementById('slipPartNo'),
    slipStation: document.getElementById('slipStation'),
    slipSerial: document.getElementById('slipSerial'),
    slipName: document.getElementById('slipName'),
    slipRelType: document.getElementById('slipRelType'),
    slipRelName: document.getElementById('slipRelName'),
    slipEpic: document.getElementById('slipEpic'),
    slipGenderAge: document.getElementById('slipGenderAge'),
    slipHouse: document.getElementById('slipHouse'),
    slipShareWaBtn: document.getElementById('slipShareWaBtn'),
    slipPrintBtn: document.getElementById('slipPrintBtn'),
    slipCloseBtn: document.getElementById('slipCloseBtn')
};

// Initialize
document.addEventListener('DOMContentLoaded', async () => {
    setupGateEventListener();
    setupAuthEventListeners();
    setupEventListeners();

    // Check existing session on load
    try {
        const user = await window.VoterAuth.checkMe();
        applyAuthState(user);
        if (user) {
            renderInitialState();
        }
    } catch (e) {
        applyAuthState(null);
    }
});

window.addEventListener('resize', checkDeviceDisplay);

// State applier: Toggles between locked Login Gate and full search app
function applyAuthState(user) {
    const gate = document.getElementById('portalLoginGate');
    const app = document.getElementById('portalMainApp');
    const authWidget = document.getElementById('portalAuthWidget');
    const adminAction = document.getElementById('adminHeaderAction');

    if (user) {
        // Authenticated: show search application, hide login gate
        if (gate) gate.style.display = 'none';
        if (app) app.style.display = 'block';

        const isAdmin = user.role === 'admin';
        const isOperator = user.role === 'operator';
        const canAccessPortal = isAdmin || isOperator;

        if (authWidget) {
            const roleBadgeHtml = isAdmin 
                ? `<span class="portal-role-tag admin">👑 एडमिन</span>`
                : (isOperator 
                    ? `<span class="portal-role-tag operator" style="background:#e0e7ff; color:#3730a3; border:1px solid #c7d2fe; font-weight:700;">📝 ऑपरेटर</span>` 
                    : `<span class="portal-role-tag">👤 कार्यकर्ता</span>`);

            authWidget.innerHTML = `
                <div class="portal-user-badge">
                    ${roleBadgeHtml}
                    <span class="portal-user-name" title="${user.full_name || user.username}">👤 ${user.username}</span>
                    <button class="portal-btn-icon" id="portalChangePwdBtn" title="पासवर्ड बदलें">
                        <i data-lucide="key-round" style="width: 14px; height: 14px;"></i>
                    </button>
                    <button class="portal-btn-icon" id="portalLogoutBtn" title="लॉगआउट">
                        <i data-lucide="log-out" style="width: 14px; height: 14px;"></i>
                    </button>
                </div>
            `;
            document.getElementById('portalChangePwdBtn')?.addEventListener('click', openChangePwdModal);
            document.getElementById('portalLogoutBtn')?.addEventListener('click', handleLogout);
        }

        if (adminAction) {
            adminAction.style.display = canAccessPortal ? 'inline-flex' : 'none';
            const portalNavText = document.getElementById('portalNavText');
            const adminLinkBtn = document.getElementById('adminLinkBtn');
            if (portalNavText) {
                portalNavText.innerText = isAdmin ? 'एडमिन पैनल' : 'पोर्टल में जाएँ';
            }
            if (adminLinkBtn) {
                adminLinkBtn.title = isAdmin ? 'एडमिन PDF कनवर्टर व डेटाबेस' : 'डाटा ऑपरेटर कनवर्टर व संपादन पोर्टल';
            }
        }

        fetchStats();
    } else {
        // Unauthenticated: Strictly LOCK screen! Hide app, show login gate
        if (app) app.style.display = 'none';
        if (gate) gate.style.display = 'flex';

        if (authWidget) {
            authWidget.innerHTML = '';
        }
        if (adminAction) {
            adminAction.style.display = 'none';
        }

        const gateErr = document.getElementById('gateLoginError');
        if (gateErr) gateErr.style.display = 'none';
        const gateForm = document.getElementById('portalGateForm');
        if (gateForm) gateForm.reset();
    }
    lucide.createIcons();
}

function checkDeviceDisplay() {
    const adminAction = document.getElementById('adminHeaderAction');
    const user = window.VoterAuth ? window.VoterAuth.getUser() : null;
    const canAccessPortal = Boolean(user && (user.role === 'admin' || user.role === 'operator'));

    if (adminAction) {
        adminAction.style.display = canAccessPortal ? 'inline-flex' : 'none';
        const portalNavText = document.getElementById('portalNavText');
        const adminLinkBtn = document.getElementById('adminLinkBtn');
        if (portalNavText && user) {
            portalNavText.innerText = user.role === 'admin' ? 'एडमिन पैनल' : 'पोर्टल में जाएँ';
        }
        if (adminLinkBtn && user) {
            adminLinkBtn.title = user.role === 'admin' ? 'एडमिन PDF कनवर्टर व डेटाबेस' : 'डाटा ऑपरेटर कनवर्टर व संपादन पोर्टल';
        }
    }
}

function setupGateEventListener() {
    const gateForm = document.getElementById('portalGateForm');
    if (gateForm) {
        gateForm.addEventListener('submit', handleGateLoginSubmit);
    }
}

async function handleGateLoginSubmit(e) {
    e.preventDefault();
    const uInput = document.getElementById('gateLoginUsername');
    const pInput = document.getElementById('gateLoginPassword');
    const err = document.getElementById('gateLoginError');
    const btn = document.getElementById('gateSubmitBtn');

    const username = uInput ? uInput.value.trim() : '';
    const password = pInput ? pInput.value : '';

    if (!username || !password) return;

    if (err) err.style.display = 'none';
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = `<i data-lucide="loader" class="spin"></i> लॉगिन हो रहा है...`;
        lucide.createIcons();
    }

    try {
        const data = await window.VoterAuth.login(username, password);
        applyAuthState(data.user);
        renderInitialState();
        showToast(`स्वागत है, ${data.user.full_name || data.user.username}!`, "info");
    } catch (error) {
        if (err) {
            err.innerText = error.message;
            err.style.display = 'block';
        }
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = `<i data-lucide="log-in" style="width: 18px; height: 18px;"></i> <span>लॉगिन करें</span>`;
            lucide.createIcons();
        }
    }
}

function setupAuthEventListeners() {
    // Password Change Modal
    document.getElementById('closeChangeMyPwdModalBtn')?.addEventListener('click', closeChangePwdModal);
    document.getElementById('cancelChangeMyPwdBtn')?.addEventListener('click', closeChangePwdModal);
    document.getElementById('changeMyPwdModal')?.addEventListener('click', (e) => {
        if (e.target.id === 'changeMyPwdModal') closeChangePwdModal();
    });
    document.getElementById('changeMyPwdForm')?.addEventListener('submit', handleChangePwdSubmit);
}

function openChangePwdModal() {
    const modal = document.getElementById('changeMyPwdModal');
    const err = document.getElementById('changePwdError');
    if (err) err.style.display = 'none';
    document.getElementById('changeMyPwdForm')?.reset();
    if (modal) modal.style.display = 'flex';
    document.getElementById('myOldPassword')?.focus();
    lucide.createIcons();
}

function closeChangePwdModal() {
    const modal = document.getElementById('changeMyPwdModal');
    if (modal) modal.style.display = 'none';
}

async function handleChangePwdSubmit(e) {
    e.preventDefault();
    const oldPwd = document.getElementById('myOldPassword').value;
    const newPwd = document.getElementById('myNewPassword').value;
    const confirmPwd = document.getElementById('myConfirmPassword').value;
    const err = document.getElementById('changePwdError');

    if (newPwd !== confirmPwd) {
        if (err) {
            err.innerText = "नया पासवर्ड और पुष्टि पासवर्ड मेल नहीं खाते।";
            err.style.display = 'block';
        }
        return;
    }

    try {
        await window.VoterAuth.changePassword(newPwd, oldPwd);
        closeChangePwdModal();
        showToast("पासवर्ड सफलतापूर्वक बदल गया!", "info");
    } catch (error) {
        if (err) {
            err.innerText = error.message;
            err.style.display = 'block';
        }
    }
}

async function handleLogout() {
    await window.VoterAuth.logout();
    applyAuthState(null);
    showToast("सफलतापूर्वक लॉगआउट हो गया।", "info");
}

// Event Listeners
function setupEventListeners() {
    elements.searchBtn.addEventListener('click', () => {
        state.page = 1;
        performSearch();
    });

    elements.mainSearchInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
            state.page = 1;
            performSearch();
        }
    });

    elements.toggleFiltersBtn.addEventListener('click', () => {
        const isHidden = elements.advancedPanel.style.display === 'none' || !elements.advancedPanel.style.display;
        elements.advancedPanel.style.display = isHidden ? 'grid' : 'none';
        elements.toggleFiltersBtn.innerHTML = isHidden 
            ? '<i data-lucide="chevron-up" style="width:16px;height:16px;"></i> फ़िल्टर छिपाएं' 
            : '<i data-lucide="sliders-horizontal" style="width:16px;height:16px;"></i> विस्तृत फ़िल्टर';
        lucide.createIcons();
    });

    elements.resetFiltersBtn.addEventListener('click', resetFilters);

    if (elements.exportExcelBtn) {
        elements.exportExcelBtn.addEventListener('click', exportToExcel);
    }

    elements.prevBtn.addEventListener('click', () => {
        if (state.page > 1) {
            state.page--;
            performSearch();
        }
    });

    elements.nextBtn.addEventListener('click', () => {
        if (state.page < state.totalPages) {
            state.page++;
            performSearch();
        }
    });

    // Slip Modal Actions
    elements.slipCloseBtn.addEventListener('click', closeSlipModal);
    elements.slipModal.addEventListener('click', (e) => {
        if (e.target === elements.slipModal) closeSlipModal();
    });

    elements.slipPrintBtn.addEventListener('click', () => {
        window.print();
    });

    elements.slipShareWaBtn.addEventListener('click', shareSlipOnWhatsApp);

    // Portal Refresh Button
    const portalRefreshBtn = document.getElementById('portalRefreshBtn');
    if (portalRefreshBtn) {
        portalRefreshBtn.addEventListener('click', () => {
            portalRefreshBtn.classList.add('spinning');
            showToast('पोर्टल पुनः लोड हो रहा है...', 'info');
            setTimeout(() => {
                window.location.reload();
            }, 300);
        });
    }
}

// Fetch Global Statistics
async function fetchStats() {
    try {
        const statsRes = await fetch('/api/database/stats');
        if (statsRes.ok) {
            const data = await statsRes.json();
            if (elements.statTotalVoters) elements.statTotalVoters.innerText = (data.total_voters || 0).toLocaleString('hi-IN');
            if (elements.statTotalParts) elements.statTotalParts.innerText = (data.total_parts || 0).toLocaleString('hi-IN');
        }
        if (window.lucide) lucide.createIcons();
    } catch (e) {
        console.warn("Stats fetch failed", e);
    }
}

// Build Search Query Parameters
function buildSearchParams() {
    const params = new URLSearchParams();
    params.append('page', state.page);
    params.append('limit', state.limit);

    const q = elements.mainSearchInput.value.trim();
    if (q) params.append('q', q);

    const name = elements.filterName.value.trim();
    if (name) params.append('name', name);

    const relName = elements.filterRelName.value.trim();
    if (relName) params.append('relation_name', relName);

    const epic = elements.filterEpic.value.trim();
    if (epic) params.append('epic_no', epic);

    const part = elements.filterPart.value.trim();
    if (part) params.append('part_no', part);

    const assembly = elements.filterAssembly.value.trim();
    if (assembly) params.append('assembly', assembly);

    const house = elements.filterHouse.value.trim();
    if (house) params.append('house_no', house);

    const gender = elements.filterGender.value;
    if (gender && gender !== 'all') params.append('gender', gender);

    const minAge = elements.filterMinAge.value.trim();
    if (minAge) params.append('min_age', minAge);

    const maxAge = elements.filterMaxAge.value.trim();
    if (maxAge) params.append('max_age', maxAge);

    // Explicitly identify online public portal search
    params.append('source', 'public');

    return params;
}

// Check if user has entered any search input or selected a filter
function hasSearchQuery() {
    const q = elements.mainSearchInput.value.trim();
    const name = elements.filterName ? elements.filterName.value.trim() : '';
    const relName = elements.filterRelName ? elements.filterRelName.value.trim() : '';
    const epic = elements.filterEpic ? elements.filterEpic.value.trim() : '';
    const part = elements.filterPart ? elements.filterPart.value.trim() : '';
    const assembly = elements.filterAssembly ? elements.filterAssembly.value.trim() : '';
    const house = elements.filterHouse ? elements.filterHouse.value.trim() : '';
    const gender = elements.filterGender ? elements.filterGender.value : 'all';
    const minAge = elements.filterMinAge ? elements.filterMinAge.value.trim() : '';
    const maxAge = elements.filterMaxAge ? elements.filterMaxAge.value.trim() : '';

    return Boolean(q || name || relName || epic || part || assembly || house || (gender && gender !== 'all') || minAge || maxAge);
}

// Render Initial Prompt (No voters shown until user searches)
function renderInitialState() {
    state.records = [];
    state.totalFiltered = 0;
    state.totalPages = 1;

    elements.resultsTitle.innerText = "मतदाता खोजें";

    const initialHtml = `
        <tr>
            <td colspan="8" style="text-align: center; padding: 55px 20px; color: var(--text-muted);">
                <div style="max-width: 480px; margin: 0 auto;">
                    <div style="width: 56px; height: 56px; border-radius: 50%; background: #fff7ed; display: flex; align-items: center; justify-content: center; margin: 0 auto 16px; border: 1px solid rgba(234, 88, 12, 0.25);">
                        <i data-lucide="search" style="width: 28px; height: 28px; stroke: #ea580c;"></i>
                    </div>
                    <h4 style="font-size: 1.1rem; font-weight: 700; color: #1e293b; margin-bottom: 8px;">
                        मतदाता खोजने के लिए ऊपर विवरण लिखें
                    </h4>
                    <p style="font-size: 0.9rem; color: #64748b; line-height: 1.5; margin: 0;">
                        सर्च बॉक्स में अपना नाम, पिता/पति का नाम या EPIC पहचान पत्र क्रमांक लिखें और <strong>"खोजें"</strong> बटन दबाएं।
                    </p>
                </div>
            </td>
        </tr>
    `;
    elements.desktopTableBody.innerHTML = initialHtml;

    elements.mobileCardsContainer.innerHTML = `
        <div style="text-align: center; padding: 45px 20px; background: #ffffff; border-radius: 12px; border: 1px solid var(--border-color); box-shadow: var(--shadow-sm);">
            <div style="width: 50px; height: 50px; border-radius: 50%; background: #fff7ed; display: flex; align-items: center; justify-content: center; margin: 0 auto 14px; border: 1px solid rgba(234, 88, 12, 0.25);">
                <i data-lucide="search" style="width: 24px; height: 24px; stroke: #ea580c;"></i>
            </div>
            <h4 style="font-size: 1rem; font-weight: 700; color: #1e293b; margin-bottom: 6px;">
                मतदाता खोजने के लिए विवरण लिखें
            </h4>
            <p style="font-size: 0.86rem; color: #64748b; line-height: 1.45; margin: 0;">
                नाम या EPIC नंबर लिखकर <strong>"खोजें"</strong> बटन दबाएं।
            </p>
        </div>
    `;

    if (elements.paginationInfo) {
        elements.paginationInfo.innerText = "खोज करने पर परिणाम यहाँ दिखेंगे";
    }
    if (elements.pageIndicator) {
        elements.pageIndicator.innerText = "पेज 1 / 1";
    }
    if (elements.prevBtn) elements.prevBtn.disabled = true;
    if (elements.nextBtn) elements.nextBtn.disabled = true;

    lucide.createIcons();
}

// Perform Search
async function performSearch() {
    const qVal = elements.mainSearchInput ? elements.mainSearchInput.value.trim() : '';
    const nameVal = elements.filterName ? elements.filterName.value.trim() : '';
    const relVal = elements.filterRelName ? elements.filterRelName.value.trim() : '';
    const epicVal = elements.filterEpic ? elements.filterEpic.value.trim() : '';

    const isAdvancedOpen = elements.advancedPanel && (elements.advancedPanel.style.display === 'grid' || elements.advancedPanel.style.display === 'flex' || elements.advancedPanel.style.display === 'block');
    const hasAnyAdvancedField = Boolean(
        nameVal || relVal || epicVal ||
        (elements.filterPart && elements.filterPart.value.trim()) ||
        (elements.filterAssembly && elements.filterAssembly.value.trim()) ||
        (elements.filterHouse && elements.filterHouse.value.trim()) ||
        (elements.filterGender && elements.filterGender.value !== 'all') ||
        (elements.filterMinAge && elements.filterMinAge.value.trim()) ||
        (elements.filterMaxAge && elements.filterMaxAge.value.trim())
    );

    // Rule: विस्तृत फ़िल्टर में कम से कम नाम, पिता/पति का नाम अथवा EPIC No भरा हुआ होना जरूरी है
    if (isAdvancedOpen || hasAnyAdvancedField) {
        if (!nameVal && !relVal && !epicVal) {
            showToast("विस्तृत फ़िल्टर में कम से कम मतदाता का नाम, पिता/पति का नाम अथवा EPIC No भरना अनिवार्य है।", "error");
            if (elements.filterName) elements.filterName.focus();
            elements.resultsTitle.innerText = "अनिवार्य फ़िल्टर भरें (नाम / पिता-पति / EPIC)";
            return;
        }
    } else {
        if (!qVal) {
            renderInitialState();
            showToast("कृपया खोजने के लिए नाम, पिता/पति का नाम या EPIC नंबर लिखें।", "info");
            return;
        }
    }

    elements.resultsTitle.innerText = "मतदाता खोज जारी है...";
    renderSearchTableSkeleton();
    
    try {
        const params = buildSearchParams();
        const fetchFn = (window.VoterAuth && window.VoterAuth.authFetch) ? window.VoterAuth.authFetch : fetch;
        const res = await fetchFn(`/api/database/search?${params.toString()}`);
        if (!res.ok) throw new Error("खोज परिणाम प्राप्त नहीं हो सके।");

        const data = await res.json();
        if (data.error) {
            showToast(data.error, "error");
            elements.resultsTitle.innerText = data.error;
            return;
        }

        state.records = data.records || [];
        state.totalFiltered = data.total_filtered || 0;
        state.totalRecords = data.total_records || 0;
        state.totalPages = data.total_pages || 1;

        renderResults();
        renderPagination();

    } catch (err) {
        elements.resultsTitle.innerText = "खोज में त्रुटि हुई।";
        showToast(err.message, "error");
    }
}

// Render search table and mobile card skeleton loaders
function renderSearchTableSkeleton(rowCount = 5) {
    if (elements.desktopTableBody) {
        elements.desktopTableBody.innerHTML = '';
        for (let i = 0; i < rowCount; i++) {
            const tr = document.createElement('tr');
            tr.className = 'skeleton-row';
            tr.innerHTML = `
                <td style="text-align:center;"><div class="skeleton-shimmer skeleton-badge" style="width:28px;height:20px;"></div></td>
                <td><div class="skeleton-shimmer skeleton-text" style="width:75%;"></div></td>
                <td><div class="skeleton-shimmer skeleton-text" style="width:65%;"></div></td>
                <td style="text-align:center;"><div class="skeleton-shimmer skeleton-badge" style="width:36px;height:20px;"></div></td>
                <td style="text-align:center;"><div class="skeleton-shimmer skeleton-badge" style="width:36px;height:20px;"></div></td>
                <td style="text-align:center;"><div class="skeleton-shimmer skeleton-epic"></div></td>
                <td style="text-align:center;"><div class="skeleton-shimmer skeleton-badge" style="width:40px;height:20px;"></div></td>
                <td style="text-align:center;"><div class="skeleton-shimmer skeleton-badge" style="width:70px;height:26px;border-radius:4px;"></div></td>
            `;
            elements.desktopTableBody.appendChild(tr);
        }
    }
    if (elements.mobileCardsContainer) {
        elements.mobileCardsContainer.innerHTML = '';
        for (let i = 0; i < 3; i++) {
            const card = document.createElement('div');
            card.className = 'skeleton-mobile-card';
            card.innerHTML = `
                <div style="display:flex; justify-content:space-between;">
                    <div class="skeleton-shimmer skeleton-text" style="width:60%;height:18px;"></div>
                    <div class="skeleton-shimmer skeleton-epic" style="height:18px;"></div>
                </div>
                <div class="skeleton-shimmer skeleton-text" style="width:80%;height:14px;margin-top:6px;"></div>
                <div style="display:flex; gap:8px; margin-top:6px;">
                    <div class="skeleton-shimmer skeleton-badge"></div>
                    <div class="skeleton-shimmer skeleton-badge"></div>
                </div>
            `;
            elements.mobileCardsContainer.appendChild(card);
        }
    }
}

// Render Results in Desktop Table & Mobile Cards
function renderResults() {
    const records = state.records;
    elements.desktopTableBody.innerHTML = '';
    elements.mobileCardsContainer.innerHTML = '';

    if (records.length === 0) {
        elements.resultsTitle.innerText = "कोई मतदाता रिकॉर्ड नहीं मिला";
        const emptyHtml = `
            <tr>
                <td colspan="8">
                    <div class="empty-state-wrap">
                        <div class="empty-state-icon-box">
                            <i data-lucide="user-x"></i>
                        </div>
                        <h4 class="empty-state-title">कोई मतदाता रिकॉर्ड नहीं मिला</h4>
                        <p class="empty-state-desc">
                            आपकी खोज के अनुसार कोई मतदाता नहीं मिला। कृपया नाम की वर्तनी (Spelling) या पहचान पत्र क्रमांक (EPIC) जांचें।
                        </p>
                        <div class="empty-state-actions">
                            <button class="empty-state-btn-primary" onclick="window.clearSearchForm && window.clearSearchForm()">
                                <i data-lucide="rotate-ccw"></i> खोज साफ़ करें
                            </button>
                        </div>
                    </div>
                </td>
            </tr>
        `;
        elements.desktopTableBody.innerHTML = emptyHtml;
        elements.mobileCardsContainer.innerHTML = `
            <div class="empty-state-wrap" style="margin: 20px auto;">
                <div class="empty-state-icon-box">
                    <i data-lucide="user-x"></i>
                </div>
                <h4 class="empty-state-title">कोई मतदाता रिकॉर्ड नहीं मिला</h4>
                <p class="empty-state-desc">
                    कृपया वर्तनी या पहचान पत्र क्रमांक जांचकर पुनः प्रयास करें।
                </p>
                <div class="empty-state-actions">
                    <button class="empty-state-btn-primary" onclick="window.clearSearchForm && window.clearSearchForm()">
                        <i data-lucide="rotate-ccw"></i> खोज साफ़ करें
                    </button>
                </div>
            </div>
        `;
        if (window.lucide) lucide.createIcons();
        return;
    }

    elements.resultsTitle.innerText = `कुल ${state.totalFiltered.toLocaleString('hi-IN')} मतदाता मिले`;

    records.forEach(v => {
        const genderClass = v.gender === 'महिला' ? 'gender-female-sm' : 'gender-male-sm';
        const epicHtml = v.epic_no 
            ? `<span class="epic-code-badge">${escapeHtml(v.epic_no)}</span>` 
            : `<span style="color: var(--text-muted);">--</span>`;

        const isDeleted = Boolean(v.is_deleted);
        const delBadge = isDeleted ? `<span class="badge-deleted" title="मतदाता सूची से विलोपित / DELETED">[विलोपित / DELETED]</span>` : '';
        const nameClass = isDeleted ? 'voter-name-deleted' : '';

        // 1. Desktop Row
        const tr = document.createElement('tr');
        if (isDeleted) tr.classList.add('row-deleted');
        tr.innerHTML = `
            <td><span class="serial-badge-pill">${v.serial_no || '--'}</span></td>
            <td><strong class="${nameClass}">${escapeHtml(v.name)}</strong> ${delBadge}</td>
            <td><span style="font-size: 0.78rem; color: var(--text-muted);">${escapeHtml(v.relation_type || 'पिता')}:</span> ${escapeHtml(v.relation_name || '--')}</td>
            <td style="text-align: center;">${escapeHtml(v.house_no || '--')}</td>
            <td style="text-align: center;">${v.age ? v.age + ' वर्ष' : '--'}</td>
            <td style="text-align: center;"><span class="gender-pill-sm ${genderClass}">${escapeHtml(v.gender || 'पुरुष')}</span></td>
            <td style="text-align: center;">${epicHtml}</td>
            <td style="text-align: center;">
                <button class="btn-view-slip" onclick="openSlipModal(${v.id})">
                    <i data-lucide="file-text" style="width:14px;height:14px;"></i> पर्ची देखें
                </button>
            </td>
        `;
        elements.desktopTableBody.appendChild(tr);

        // 2. Mobile Card
        const card = document.createElement('div');
        card.className = `voter-mobile-card ${isDeleted ? 'row-deleted' : ''}`;
        card.innerHTML = `
            <div class="mobile-card-top">
                <span class="serial-badge-pill">क्रम सं० ${v.serial_no || '--'}</span>
                <span class="gender-pill-sm ${genderClass}">${escapeHtml(v.gender || 'पुरुष')} • ${v.age ? v.age + ' वर्ष' : ''}</span>
            </div>
            <div class="mobile-card-name"><span class="${nameClass}">${escapeHtml(v.name)}</span> ${delBadge}</div>
            <div class="mobile-card-info-grid">
                <div class="mobile-info-item">
                    <span>${escapeHtml(v.relation_type || 'सम्बन्धी')} का नाम</span>
                    <span>${escapeHtml(v.relation_name || '--')}</span>
                </div>
                <div class="mobile-info-item">
                    <span>मकान संख्या</span>
                    <span>${escapeHtml(v.house_no || '--')}</span>
                </div>
                <div class="mobile-info-item">
                    <span>पहचान पत्र (EPIC)</span>
                    <span style="font-family: monospace;">${escapeHtml(v.epic_no || 'उपलब्ध नहीं')}</span>
                </div>
                <div class="mobile-info-item">
                    <span>भाग संख्या (बूथ)</span>
                    <span>${escapeHtml(v.part_no || '--')}</span>
                </div>
            </div>
            ${v.polling_station ? `<div class="mobile-card-station"><i data-lucide="map-pin" style="width:12px;height:12px;display:inline;"></i> ${escapeHtml(v.polling_station)}</div>` : ''}
            <button class="btn-view-slip" style="width: 100%; justify-content: center; padding: 10px;" onclick="openSlipModal(${v.id})">
                <i data-lucide="file-text" style="width:16px;height:16px;"></i> मतदाता पर्ची देखें / प्रिंट करें
            </button>
        `;
        elements.mobileCardsContainer.appendChild(card);
    });

    lucide.createIcons();
}

// Render Pagination Controls
function renderPagination() {
    const start = state.totalFiltered === 0 ? 0 : (state.page - 1) * state.limit + 1;
    const end = Math.min(state.page * state.limit, state.totalFiltered);

    elements.paginationInfo.innerText = `दिखा रहे हैं ${start} - ${end} (कुल ${state.totalFiltered} में से)`;
    elements.pageIndicator.innerText = `पेज ${state.page} / ${Math.max(1, state.totalPages)}`;

    elements.prevBtn.disabled = state.page <= 1;
    elements.nextBtn.disabled = state.page >= state.totalPages;
}

// Reset All Filters
function resetFilters() {
    elements.mainSearchInput.value = '';
    elements.filterName.value = '';
    elements.filterRelName.value = '';
    elements.filterEpic.value = '';
    elements.filterPart.value = '';
    elements.filterAssembly.value = '';
    elements.filterHouse.value = '';
    elements.filterGender.value = 'all';
    elements.filterMinAge.value = '';
    elements.filterMaxAge.value = '';
    state.page = 1;
    renderInitialState();
}

// Export Search Results to Excel
function exportToExcel() {
    if (!hasSearchQuery()) {
        showToast("कृपया पहले नाम या पहचान पत्र लिखकर खोज करें, उसके बाद Excel डाउनलोड करें।", "info");
        return;
    }
    const params = buildSearchParams();
    window.location.href = `/api/database/export?${params.toString()}`;
    showToast("खोज परिणामों की Excel फ़ाइल डाउनलोड हो रही है...", "info");
}

// =============================================================================
// OFFICIAL DIGITAL VOTER SLIP (MODAL LOGIC)
// =============================================================================

window.openSlipModal = async function(recordId) {
    try {
        const res = await fetch(`/api/database/slip/${recordId}?source=public`);
        if (!res.ok) throw new Error("मतदाता पर्ची डेटा लोड नहीं हुआ अथवा उपलब्ध नहीं है।");
        const data = await res.json();
        const v = data.slip;
        state.currentSlip = v;

        // Populate Slip Paper
        elements.slipAssembly.innerText = v.assembly || "उत्तर प्रदेश";
        elements.slipPartNo.innerText = v.part_no ? `भाग संख्या: ${v.part_no}` : "--";
        elements.slipStation.innerText = v.polling_station || "मतदान स्थल सूची अनुसार";
        elements.slipSerial.innerText = String(v.serial_no || 0).padStart(4, '0');
        if (v.is_deleted) {
            elements.slipName.innerHTML = `<span class="voter-name-deleted">${escapeHtml(v.name || "--")}</span> <span class="badge-deleted">[विलोपित / DELETED]</span>`;
        } else {
            elements.slipName.innerText = v.name || "--";
        }
        elements.slipRelType.innerText = (v.relation_type || "पिता") + " का नाम:";
        elements.slipRelName.innerText = v.relation_name || "--";
        elements.slipEpic.innerText = v.epic_no || "--";
        elements.slipGenderAge.innerText = `${v.gender || 'पुरुष'} / ${v.age ? v.age + ' वर्ष' : '--'}`;
        elements.slipHouse.innerText = v.house_no || "--";

        // Show Modal
        elements.slipModal.style.display = 'flex';
        lucide.createIcons();

    } catch (e) {
        showToast("त्रुटि: " + e.message, "error");
    }
};

function closeSlipModal() {
    elements.slipModal.style.display = 'none';
    state.currentSlip = null;
}

// Share Slip on WhatsApp
function shareSlipOnWhatsApp() {
    if (!state.currentSlip) return;
    const v = state.currentSlip;
    
    const message = `🇮🇳 *मतदाता सूचना पर्ची (Voter Information Slip)*\n` +
        `----------------------------------------\n` +
        `*मतदाता का नाम:* ${v.name}${v.is_deleted ? ' [विलोपित / DELETED]' : ''}\n` +
        `*${v.relation_type || 'पिता/पति'} का नाम:* ${v.relation_name || '--'}\n` +
        `*क्रम संख्या (Serial No):* ${v.serial_no}\n` +
        `*पहचान पत्र क्रमांक (EPIC):* ${v.epic_no || '--'}\n` +
        `*भाग संख्या (Part No):* ${v.part_no || '--'}\n` +
        `*विधानसभा क्षेत्र:* ${v.assembly || '--'}\n` +
        `*मकान संख्या:* ${v.house_no || '--'}\n` +
        `*मतदान स्थल (Polling Station):* ${v.polling_station || '--'}\n` +
        `----------------------------------------\n` +
        `🔗 *ऑनलाइन वोटर सर्च:* ${window.location.origin}/search\n` +
        `_कृपया मतदान दिवस पर पहचान पत्र साथ ले जाएं।_`;

    const waUrl = `https://api.whatsapp.com/send?text=${encodeURIComponent(message)}`;
    window.open(waUrl, '_blank');
}

// Utility: Escape HTML
function escapeHtml(str) {
    if (!str) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

// Simple Toast Notification
function showToast(msg, type = "info") {
    let toast = document.getElementById('searchToast');
    if (!toast) {
        toast = document.createElement('div');
        toast.id = 'searchToast';
        toast.style.position = 'fixed';
        toast.style.bottom = '24px';
        toast.style.left = '50%';
        toast.style.transform = 'translateX(-50%)';
        toast.style.padding = '12px 24px';
        toast.style.borderRadius = '30px';
        toast.style.fontSize = '0.9rem';
        toast.style.fontWeight = '700';
        toast.style.boxShadow = '0 10px 25px rgba(0,0,0,0.2)';
        toast.style.zIndex = '9999';
        toast.style.transition = 'all 0.3s ease';
        document.body.appendChild(toast);
    }

    if (type === 'error') {
        toast.style.background = '#ef4444';
        toast.style.color = '#ffffff';
    } else {
        toast.style.background = '#1e3a8a';
        toast.style.color = '#ffffff';
    }

    toast.innerText = msg;
    toast.style.opacity = '1';

    setTimeout(() => {
        toast.style.opacity = '0';
    }, 3500);
}
