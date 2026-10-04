/**
 * Public Panchayat & Local Body Voter Search Portal - Frontend Logic
 * Handles real-time search, multi-field filters, pagination, Excel export, and official voter slip modal.
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
    currentSlip: null,
    filterOptions: null
};

// Toast notification helper
function showToast(message, type = 'info') {
    const container = document.getElementById('toastContainer');
    if (!container) {
        alert(message);
        return;
    }
    const toast = document.createElement('div');
    const bg = type === 'success' ? '#059669' : (type === 'error' ? '#DC2626' : (type === 'warning' ? '#D97706' : '#4338CA'));
    toast.style.cssText = `background: ${bg}; color: white; padding: 12px 18px; border-radius: 8px; font-size: 0.88rem; box-shadow: 0 4px 12px rgba(0,0,0,0.18); font-weight: 600; display: flex; align-items: center; gap: 8px; transition: opacity 0.3s;`;
    toast.innerText = message;
    container.appendChild(toast);
    setTimeout(() => {
        toast.style.opacity = '0';
        setTimeout(() => toast.remove(), 300);
    }, 3500);
}

// DOM Elements
const elements = {
    mainSearchInput: document.getElementById('mainSearchInput'),
    searchBtn: document.getElementById('searchBtn'),
    toggleFiltersBtn: document.getElementById('toggleFiltersBtn'),
    advancedPanel: document.getElementById('advancedPanel'),
    
    // Filter fields
    filterBody: document.getElementById('filterBody'),
    filterWard: document.getElementById('filterWard'),
    filterPart: document.getElementById('filterPart'),
    filterName: document.getElementById('filterName'),
    filterRelName: document.getElementById('filterRelName'),
    filterMohalla: document.getElementById('filterMohalla'),
    filterHouse: document.getElementById('filterHouse'),
    filterGender: document.getElementById('filterGender'),
    filterMinAge: document.getElementById('filterMinAge'),
    filterMaxAge: document.getElementById('filterMaxAge'),
    resetFiltersBtn: document.getElementById('resetFiltersBtn'),
    exportExcelBtn: document.getElementById('exportExcelBtn'),
    
    // Stats Pills
    statTotalVoters: document.getElementById('statTotalVoters'),
    statTotalBodies: document.getElementById('statTotalBodies'),
    statTotalWards: document.getElementById('statTotalWards'),
    
    // Results
    resultsTitle: document.getElementById('resultsTitle'),
    resultsFilterTag: document.getElementById('resultsFilterTag'),
    desktopTableBody: document.getElementById('desktopTableBody'),
    mobileCardsContainer: document.getElementById('mobileCardsContainer'),
    
    // Pagination
    paginationInfo: document.getElementById('paginationInfo'),
    pageIndicator: document.getElementById('pageIndicator'),
    prevBtn: document.getElementById('prevBtn'),
    nextBtn: document.getElementById('nextBtn'),
    
    // Slip Modal
    slipModal: document.getElementById('slipModal'),
    slipBodyName: document.getElementById('slipBodyName'),
    slipWardNo: document.getElementById('slipWardNo'),
    slipPartNo: document.getElementById('slipPartNo'),
    slipStation: document.getElementById('slipStation'),
    slipSerial: document.getElementById('slipSerial'),
    slipName: document.getElementById('slipName'),
    slipRelType: document.getElementById('slipRelType'),
    slipRelName: document.getElementById('slipRelName'),
    slipPartNoDetail: document.getElementById('slipPartNoDetail'),
    slipGenderAge: document.getElementById('slipGenderAge'),
    slipHouse: document.getElementById('slipHouse'),
    slipMohalla: document.getElementById('slipMohalla'),
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
            await fetchFilterOptionsAndStats();
            performSearch();
        }
    } catch (e) {
        applyAuthState(null);
    }
});

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

        const isAdmin = user.role === 'admin' || (user.username || '').toLowerCase() === 'harshsamrat';
        const isOperator = user.role === 'operator';
        const canAccessPortal = isAdmin || isOperator;

        if (authWidget) {
            const roleBadgeHtml = isAdmin 
                ? `<span class="portal-role-tag admin" style="background:#EEF2FF; color:#4338CA; border:1px solid #C7D2FE; font-weight:700;">👑 एडमिन</span>`
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
            if (portalNavText) {
                portalNavText.innerText = isAdmin ? 'एडमिन पैनल' : 'स्कैनर पोर्टल';
            }
        }

        if (window.lucide) lucide.createIcons();

    } else {
        // Unauthenticated: show login gate, hide search application
        if (gate) gate.style.display = 'flex';
        if (app) app.style.display = 'none';
        if (adminAction) adminAction.style.display = 'none';

        if (authWidget) {
            authWidget.innerHTML = `
                <button class="portal-login-btn" id="openLoginModalBtn" style="background: #4338CA;">
                    <i data-lucide="log-in" style="width: 14px; height: 14px;"></i>
                    <span>लॉगिन</span>
                </button>
            `;
            document.getElementById('openLoginModalBtn')?.addEventListener('click', () => {
                if (gate) gate.style.display = 'flex';
            });
        }
        if (window.lucide) lucide.createIcons();
    }
}

// Setup Gate Login
function setupGateEventListener() {
    const gateForm = document.getElementById('portalGateForm');
    if (!gateForm) return;

    gateForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const u = document.getElementById('gateLoginUsername').value.trim();
        const p = document.getElementById('gateLoginPassword').value;
        const err = document.getElementById('gateLoginError');
        const btn = document.getElementById('gateSubmitBtn');

        if (err) err.style.display = 'none';
        if (btn) {
            btn.disabled = true;
            btn.innerHTML = `<i data-lucide="loader" class="spin" style="width:18px;height:18px;"></i> लॉगिन हो रहा है...`;
            if (window.lucide) lucide.createIcons();
        }

        try {
            const res = await window.VoterAuth.login(u, p);
            applyAuthState(res.user);
            await fetchFilterOptionsAndStats();
            performSearch();
            showToast("लॉगिन सफल!", "success");
        } catch (error) {
            if (err) {
                err.innerText = error.message;
                err.style.display = 'block';
            }
        } finally {
            if (btn) {
                btn.disabled = false;
                btn.innerHTML = `<i data-lucide="log-in" style="width: 18px; height: 18px;"></i> <span>लॉगिन करें</span>`;
                if (window.lucide) lucide.createIcons();
            }
        }
    });
}

// Setup Change Password Modals
function setupAuthEventListeners() {
    const changePwdModal = document.getElementById('changeMyPwdModal');
    const closeBtn = document.getElementById('closeChangeMyPwdModalBtn');
    const cancelBtn = document.getElementById('cancelChangeMyPwdBtn');
    const changeForm = document.getElementById('changeMyPwdForm');

    const closeModal = () => {
        if (changePwdModal) changePwdModal.style.display = 'none';
    };

    closeBtn?.addEventListener('click', closeModal);
    cancelBtn?.addEventListener('click', closeModal);

    changeForm?.addEventListener('submit', async (e) => {
        e.preventDefault();
        const oldP = document.getElementById('myOldPassword').value;
        const newP = document.getElementById('myNewPassword').value;
        const confP = document.getElementById('myConfirmPassword').value;
        const errDiv = document.getElementById('changePwdError');

        if (newP !== confP) {
            if (errDiv) {
                errDiv.innerText = "नया पासवर्ड और पुष्टि पासवर्ड मेल नहीं खाते।";
                errDiv.style.display = 'block';
            }
            return;
        }

        try {
            await window.VoterAuth.changePassword(newP, oldP);
            closeModal();
            showToast("पासवर्ड सफलतापूर्वक बदल गया!", "success");
            changeForm.reset();
        } catch (err) {
            if (errDiv) {
                errDiv.innerText = err.message;
                errDiv.style.display = 'block';
            }
        }
    });
}

function openChangePwdModal() {
    const modal = document.getElementById('changeMyPwdModal');
    const err = document.getElementById('changePwdError');
    if (err) err.style.display = 'none';
    if (modal) modal.style.display = 'flex';
    if (window.lucide) lucide.createIcons();
}

async function handleLogout() {
    await window.VoterAuth.logout();
    applyAuthState(null);
    showToast("सफलतापूर्वक लॉगआउट हो गया।", "info");
}

// Event Listeners for Search & Filters
function setupEventListeners() {
    elements.searchBtn?.addEventListener('click', () => {
        state.page = 1;
        performSearch();
    });

    elements.mainSearchInput?.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
            state.page = 1;
            performSearch();
        }
    });

    elements.toggleFiltersBtn?.addEventListener('click', () => {
        const isHidden = elements.advancedPanel.style.display === 'none' || !elements.advancedPanel.style.display;
        elements.advancedPanel.style.display = isHidden ? 'grid' : 'none';
        elements.toggleFiltersBtn.innerHTML = isHidden 
            ? '<i data-lucide="chevron-up" style="width:16px;height:16px;"></i> फ़िल्टर छिपाएं' 
            : '<i data-lucide="sliders-horizontal" style="width:16px;height:16px;"></i> विस्तृत फ़िल्टर';
        if (window.lucide) lucide.createIcons();
    });

    elements.resetFiltersBtn?.addEventListener('click', resetFilters);
    elements.exportExcelBtn?.addEventListener('click', exportToExcel);

    elements.filterBody?.addEventListener('change', () => {
        state.page = 1;
        updateWardsDropdown();
        performSearch();
    });

    elements.filterWard?.addEventListener('change', () => {
        state.page = 1;
        performSearch();
    });

    elements.filterGender?.addEventListener('change', () => {
        state.page = 1;
        performSearch();
    });

    elements.prevBtn?.addEventListener('click', () => {
        if (state.page > 1) {
            state.page--;
            performSearch();
        }
    });

    elements.nextBtn?.addEventListener('click', () => {
        if (state.page < state.totalPages) {
            state.page++;
            performSearch();
        }
    });

    // Slip Modal Actions
    elements.slipCloseBtn?.addEventListener('click', closeSlipModal);
    elements.slipModal?.addEventListener('click', (e) => {
        if (e.target === elements.slipModal) closeSlipModal();
    });

    elements.slipPrintBtn?.addEventListener('click', () => {
        window.print();
    });

    elements.slipShareWaBtn?.addEventListener('click', shareSlipOnWhatsApp);

    // Refresh Button
    document.getElementById('portalRefreshBtn')?.addEventListener('click', () => {
        showToast('पोर्टल पुनः लोड हो रहा है...', 'info');
        setTimeout(() => window.location.reload(), 300);
    });
}

// Fetch filter options (bodies & wards) + global statistics
async function fetchFilterOptionsAndStats() {
    try {
        const res = await fetch('/api/panchayat/search/filter-options');
        if (!res.ok) return;
        const data = await res.json();
        state.filterOptions = data;

        // Update Stats Pills
        if (elements.statTotalVoters) elements.statTotalVoters.innerText = (data.total_voters || 0).toLocaleString('hi-IN');
        if (elements.statTotalBodies) elements.statTotalBodies.innerText = (data.total_bodies || 0).toLocaleString('hi-IN');
        if (elements.statTotalWards) elements.statTotalWards.innerText = (data.total_wards || 0).toLocaleString('hi-IN');

        // Populate Bodies Dropdown
        if (elements.filterBody && data.bodies) {
            elements.filterBody.innerHTML = '<option value="">सभी निकाय / पंचायत</option>' + 
                data.bodies.map(b => `<option value="${escapeHtml(b)}">${escapeHtml(b)}</option>`).join('');
        }

        updateWardsDropdown();

        if (window.lucide) lucide.createIcons();
    } catch (e) {
        console.warn('fetchFilterOptionsAndStats error:', e);
    }
}

function updateWardsDropdown() {
    if (!elements.filterWard || !state.filterOptions) return;
    const selectedBody = elements.filterBody?.value || '';
    const bodyWardsMap = state.filterOptions.body_wards || {};

    let wardsList = [];
    if (selectedBody && bodyWardsMap[selectedBody]) {
        wardsList = bodyWardsMap[selectedBody];
    } else {
        wardsList = state.filterOptions.wards || [];
    }

    elements.filterWard.innerHTML = '<option value="">सभी वार्ड</option>' +
        wardsList.map(w => `<option value="${escapeHtml(w)}">वार्ड ${escapeHtml(w)}</option>`).join('');
}

// Perform Search API Call
async function performSearch() {
    const params = new URLSearchParams();
    params.append('page', state.page);
    params.append('page_size', state.limit);

    const q = elements.mainSearchInput?.value.trim() || '';
    if (q) params.append('query', q);

    const bodyName = elements.filterBody?.value || '';
    if (bodyName) params.append('body_name', bodyName);

    const wardNo = elements.filterWard?.value || '';
    if (wardNo) params.append('ward_no', wardNo);

    const partNo = elements.filterPart?.value.trim() || '';
    if (partNo) params.append('part_no', partNo);

    const name = elements.filterName?.value.trim() || '';
    if (name) params.append('name', name);

    const relName = elements.filterRelName?.value.trim() || '';
    if (relName) params.append('relation_name', relName);

    const mohalla = elements.filterMohalla?.value.trim() || '';
    if (mohalla) params.append('mohalla', mohalla);

    const house = elements.filterHouse?.value.trim() || '';
    if (house) params.append('house_no', house);

    const gender = elements.filterGender?.value || 'all';
    if (gender && gender !== 'all') params.append('gender', gender);

    const minAge = elements.filterMinAge?.value || '';
    if (minAge) params.append('min_age', minAge);

    const maxAge = elements.filterMaxAge?.value || '';
    if (maxAge) params.append('max_age', maxAge);

    // Show loading indicator
    if (elements.desktopTableBody) {
        elements.desktopTableBody.innerHTML = `
            <tr>
                <td colspan="11" style="text-align: center; padding: 45px 20px; color: #64748B;">
                    <div class="loading-spinner" style="margin: 0 auto 12px auto;"></div>
                    डेटा खोजा जा रहा है, कृपया प्रतीक्षा करें...
                </td>
            </tr>
        `;
    }
    if (elements.mobileCardsContainer) {
        elements.mobileCardsContainer.innerHTML = `
            <div style="text-align: center; padding: 40px; color: #64748B;">
                <div class="loading-spinner" style="margin: 0 auto 10px auto;"></div>
                खोज जारी है...
            </div>
        `;
    }

    try {
        const res = await fetch(`/api/panchayat/search?${params.toString()}`);
        if (!res.ok) throw new Error('खोज परिणाम प्राप्त नहीं हो सके');

        const data = await res.json();
        state.records = data.records || [];
        state.totalRecords = data.total || 0;
        state.totalPages = Math.max(1, Math.ceil(state.totalRecords / state.limit));

        renderResults();

    } catch (err) {
        console.error('Search error:', err);
        if (elements.desktopTableBody) {
            elements.desktopTableBody.innerHTML = `
                <tr>
                    <td colspan="11" style="text-align: center; padding: 40px; color: #DC2626;">
                        त्रुटि: ${escapeHtml(err.message)}
                    </td>
                </tr>
            `;
        }
    }
}

// Render Results Table & Cards
function renderResults() {
    const records = state.records;
    const total = state.totalRecords;
    const page = state.page;
    const limit = state.limit;

    // Update Result Header Title
    if (elements.resultsTitle) {
        elements.resultsTitle.innerText = `पंचायत मतदाता खोज परिणाम (${total.toLocaleString('hi-IN')} रिकॉर्ड्स मिले)`;
    }

    const startIdx = records.length > 0 ? (page - 1) * limit + 1 : 0;
    const endIdx = (page - 1) * limit + records.length;

    if (elements.paginationInfo) {
        elements.paginationInfo.innerText = `दिखा रहे हैं ${startIdx} - ${endIdx} (कुल ${total} में से)`;
    }
    if (elements.pageIndicator) {
        elements.pageIndicator.innerText = `पेज ${page} / ${state.totalPages}`;
    }
    if (elements.prevBtn) elements.prevBtn.disabled = page <= 1;
    if (elements.nextBtn) elements.nextBtn.disabled = page >= state.totalPages;

    if (records.length === 0) {
        const emptyHtml = `
            <tr>
                <td colspan="11" style="text-align: center; padding: 60px 20px; color: #64748B;">
                    <div style="font-size: 40px; margin-bottom: 12px;">📭</div>
                    <div style="font-weight: 700; font-size: 1.05rem; color: #1E293B;">कोई मतदाता रिकॉर्ड नहीं मिला</div>
                    <p style="margin: 6px 0 0 0; font-size: 0.88rem;">कृपया नाम, EPIC या वार्ड बदलकर पुनः प्रयास करें।</p>
                </td>
            </tr>
        `;
        if (elements.desktopTableBody) elements.desktopTableBody.innerHTML = emptyHtml;
        if (elements.mobileCardsContainer) {
            elements.mobileCardsContainer.innerHTML = `
                <div style="text-align: center; padding: 40px 20px; color: #64748B; background: white; border-radius: 12px; border: 1px dashed #CBD5E1;">
                    <div style="font-size: 36px; margin-bottom: 8px;">📭</div>
                    <strong>कोई मतदाता रिकॉर्ड नहीं मिला</strong>
                </div>
            `;
        }
        return;
    }

    // Desktop Table Rows
    if (elements.desktopTableBody) {
        elements.desktopTableBody.innerHTML = records.map((r, i) => {
            const rowIdx = (page - 1) * limit + i + 1;
            const bodyBadge = r.body_type === 'gram_panchayat'
                ? '<span class="body-type-badge-gram">🌾 ग्राम</span>'
                : '<span class="body-type-badge-nagar">🏙️ नगर</span>';

            const genderBadge = r.gender === 'F'
                ? '<span style="color: #DB2777; font-weight: 600;">महिला</span>'
                : '<span style="color: #2563EB; font-weight: 600;">पुरुष</span>';

            const locText = r.polling_booth || r.polling_station || '-';
            const boothHtml = `
                <div style="font-size: 0.84rem; font-weight: 700; color: #1E293B;">${escapeHtml(locText)}</div>
                ${r.mohalla ? `<div style="font-size: 0.74rem; color: #64748B;">${escapeHtml(r.mohalla)}</div>` : ''}
            `;

            return `
                <tr style="border-bottom: 1px solid #F1F5F9; transition: background 0.15s ease;" onmouseover="this.style.background='#F8FAFC'" onmouseout="this.style.background='transparent'">
                    <td style="padding: 10px 12px; text-align: center; color: #64748B; font-size: 0.84rem;">${rowIdx}</td>
                    <td style="padding: 10px 12px;">
                        <div style="font-weight: 700; color: #1E293B; font-size: 0.9rem;">${escapeHtml(r.body_name || '-')}</div>
                        <div style="margin-top: 2px;">${bodyBadge}</div>
                    </td>
                    <td style="padding: 10px 12px; text-align: center; font-weight: 600; color: #4338CA;">${escapeHtml(r.ward_no || '-')}</td>
                    <td style="padding: 10px 12px; text-align: center; font-size: 0.86rem; color: #475569;">${escapeHtml(r.part_no || '-')}</td>
                    <td style="padding: 10px 12px;">
                        <div style="font-weight: 700; color: #0F172A; font-size: 0.92rem;">${escapeHtml(r.name || '-')}</div>
                    </td>
                    <td style="padding: 10px 12px;">
                        <div style="font-size: 0.86rem; color: #334155;">
                            <span style="color: #64748B; font-size: 0.78rem;">${escapeHtml(r.relation_type || 'संबंधी')}:</span>
                            ${escapeHtml(r.relation_name || '-')}
                        </div>
                    </td>
                    <td style="padding: 10px 12px; text-align: center; font-size: 0.88rem; color: #334155;">${escapeHtml(r.house_no || '-')}</td>
                    <td style="padding: 10px 12px; text-align: center; font-size: 0.88rem;">${r.age || '-'}</td>
                    <td style="padding: 10px 12px; text-align: center;">${genderBadge}</td>
                    <td style="padding: 10px 12px; text-align: center;">${boothHtml}</td>
                    <td style="padding: 10px 12px; text-align: center;">
                        <button onclick="viewVoterSlip(${i});" class="btn-slip-view" style="padding: 6px 12px; border-radius: 6px; font-size: 0.82rem; font-weight: 600; background: #EEF2FF; color: #4338CA; border: 1px solid #C7D2FE; cursor: pointer; display: inline-flex; align-items: center; gap: 4px; transition: all 0.2s;" onmouseover="this.style.background='#4338CA'; this.style.color='#FFFFFF';" onmouseout="this.style.background='#EEF2FF'; this.style.color='#4338CA';">
                            <i data-lucide="file-text" style="width: 14px; height: 14px;"></i> पर्ची देखें
                        </button>
                    </td>
                </tr>
            `;
        }).join('');
    }

    // Mobile View Cards
    if (elements.mobileCardsContainer) {
        elements.mobileCardsContainer.innerHTML = records.map((r, i) => {
            const bodyBadge = r.body_type === 'gram_panchayat'
                ? '<span class="body-type-badge-gram">🌾 ग्राम</span>'
                : '<span class="body-type-badge-nagar">🏙️ नगर</span>';

            const locText = r.polling_booth || r.polling_station || '-';

            return `
                <div class="voter-card-mobile" style="background: white; border: 1px solid #E2E8F0; border-radius: 12px; padding: 16px; margin-bottom: 12px; box-shadow: 0 1px 3px rgba(0,0,0,0.05);">
                    <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 8px;">
                        <div>
                            <div style="font-size: 1.05rem; font-weight: 800; color: #0F172A;">${escapeHtml(r.name || '-')}</div>
                            <div style="font-size: 0.85rem; color: #475569; margin-top: 2px;">
                                ${escapeHtml(r.relation_type || 'संबंधी')}: <strong>${escapeHtml(r.relation_name || '-')}</strong>
                            </div>
                        </div>
                        <div>${bodyBadge}</div>
                    </div>

                    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 6px; font-size: 0.82rem; margin: 10px 0; background: #F8FAFC; padding: 10px; border-radius: 8px;">
                        <div><span style="color: #64748B;">निकाय:</span> <strong>${escapeHtml(r.body_name || '-')}</strong></div>
                        <div><span style="color: #64748B;">वार्ड:</span> <strong>वार्ड ${escapeHtml(r.ward_no || '-')}</strong></div>
                        <div><span style="color: #64748B;">भाग सं०:</span> <strong>${escapeHtml(r.part_no || '-')}</strong></div>
                        <div><span style="color: #64748B;">मकान:</span> <strong>${escapeHtml(r.house_no || '-')}</strong></div>
                        <div><span style="color: #64748B;">आयु/लिंग:</span> <strong>${r.age || '-'} वर्ष (${r.gender === 'F' ? 'महिला' : 'पुरुष'})</strong></div>
                        <div><span style="color: #64748B;">मतदान स्थल:</span> <strong>${escapeHtml(locText)}</strong></div>
                    </div>

                    <div style="display: flex; justify-content: flex-end; margin-top: 10px;">
                        <button onclick="viewVoterSlip(${i});" style="width: 100%; padding: 9px; border-radius: 8px; font-size: 0.88rem; font-weight: 700; background: #4338CA; color: white; border: none; cursor: pointer; display: flex; align-items: center; justify-content: center; gap: 6px;">
                            <i data-lucide="file-text" style="width: 15px; height: 15px;"></i> डिजिटल पर्ची देखें व शेयर करें
                        </button>
                    </div>
                </div>
            `;
        }).join('');
    }

    if (window.lucide) lucide.createIcons();
}

// Reset Filters
function resetFilters() {
    if (elements.mainSearchInput) elements.mainSearchInput.value = '';
    if (elements.filterBody) elements.filterBody.value = '';
    if (elements.filterWard) elements.filterWard.value = '';
    if (elements.filterPart) elements.filterPart.value = '';
    if (elements.filterName) elements.filterName.value = '';
    if (elements.filterRelName) elements.filterRelName.value = '';
    if (elements.filterMohalla) elements.filterMohalla.value = '';
    if (elements.filterHouse) elements.filterHouse.value = '';
    if (elements.filterGender) elements.filterGender.value = 'all';
    if (elements.filterMinAge) elements.filterMinAge.value = '';
    if (elements.filterMaxAge) elements.filterMaxAge.value = '';

    updateWardsDropdown();
    state.page = 1;
    performSearch();
}

// Open Digital Voter Information Slip
window.viewVoterSlip = function(idx) {
    const voter = state.records[idx];
    if (!voter) return;
    state.currentSlip = voter;

    if (elements.slipBodyName) {
        const typeStr = voter.body_type === 'nagar_panchayat' ? ' (नगर पंचायत)' : ' (ग्राम पंचायत)';
        elements.slipBodyName.innerText = (voter.body_name || 'उत्तर प्रदेश स्थानीय निकाय') + typeStr;
    }
    if (elements.slipWardNo) {
        elements.slipWardNo.innerText = `वार्ड सं०: ${voter.ward_no || '-'}${voter.ward_name ? ' (' + voter.ward_name + ')' : ''}`;
    }
    if (elements.slipPartNo) {
        elements.slipPartNo.innerText = `भाग सं०: ${voter.part_no || 'लागू नहीं'}`;
    }
    if (elements.slipSerial) {
        elements.slipSerial.innerText = voter.serial_no || '-';
    }
    if (elements.slipName) {
        elements.slipName.innerText = voter.name || '-';
    }
    if (elements.slipRelType) {
        elements.slipRelType.innerText = (voter.relation_type || 'पिता/पति') + ' का नाम';
    }
    if (elements.slipRelName) {
        elements.slipRelName.innerText = voter.relation_name || '-';
    }
    if (elements.slipPartNoDetail) {
        elements.slipPartNoDetail.innerText = voter.part_no ? `भाग सं० ${voter.part_no}` : '-';
    }
    if (elements.slipGenderAge) {
        const gStr = voter.gender === 'F' ? 'महिला' : (voter.gender === 'M' ? 'पुरुष' : 'अन्य');
        elements.slipGenderAge.innerText = `${gStr} / ${voter.age || '-'} वर्ष`;
    }
    if (elements.slipHouse) {
        elements.slipHouse.innerText = voter.house_no || '-';
    }
    if (elements.slipMohalla) {
        elements.slipMohalla.innerText = voter.mohalla || '-';
    }
    if (elements.slipStation) {
        const station = voter.polling_station || '';
        const booth = voter.polling_booth || '';
        let fullLoc = station;
        if (booth && !station.includes(booth)) {
            fullLoc = station ? `${station} (कक्ष/बूथ: ${booth})` : `कक्ष/बूथ: ${booth}`;
        }
        elements.slipStation.innerText = fullLoc || 'निर्वाचन अधिकारी द्वारा निर्धारित स्थल';
    }

    if (elements.slipModal) {
        elements.slipModal.style.display = 'flex';
    }
    if (window.lucide) lucide.createIcons();
};

function closeSlipModal() {
    if (elements.slipModal) elements.slipModal.style.display = 'none';
}

// Share Slip on WhatsApp
function shareSlipOnWhatsApp() {
    const v = state.currentSlip;
    if (!v) return;

    const bodyTypeStr = v.body_type === 'nagar_panchayat' ? 'नगर पंचायत' : 'ग्राम पंचायत';
    const stationStr = (v.polling_station || '') + (v.polling_booth ? ` (${v.polling_booth})` : '');

    const text = 
`🇮🇳 *मतदाता सूचना पर्ची (Voter Information Slip)*
🏛️ *${escapeHtml(v.body_name || '')} (${bodyTypeStr})*
---------------------------------------
👤 *मतदाता का नाम:* ${v.name || '-'}
👨‍👦 *${v.relation_type || 'पिता/पति'}:* ${v.relation_name || '-'}
🔢 *क्रम संख्या (Serial No):* ${v.serial_no || '-'}
📍 *वार्ड सं०:* ${v.ward_no || '-'} ${v.ward_name ? '(' + v.ward_name + ')' : ''}
📂 *भाग संख्या:* ${v.part_no || '-'}
🏠 *मकान सं०:* ${v.house_no || '-'}
🏘️ *मोहल्ला:* ${v.mohalla || '-'}
👥 *आयु/लिंग:* ${v.age || '-'} वर्ष / ${v.gender === 'F' ? 'महिला' : 'पुरुष'}
🏫 *मतदान केंद्र:* ${stationStr || 'निर्वाचन नामावली अनुसार'}
---------------------------------------
⚠️ *नोट:* मतदान के समय मान्य सरकारी पहचान पत्र (आधार/पहचान पत्र) अवश्य साथ लाएं।`;

    const encoded = encodeURIComponent(text);
    window.open(`https://wa.me/?text=${encoded}`, '_blank');
}

// Export Matching Results to Excel
async function exportToExcel() {
    const btn = elements.exportExcelBtn;
    const origHtml = btn ? btn.innerHTML : '';
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<span>⏳ एक्सेल तैयार हो रहा है...</span>';
    }

    try {
        const reqBody = {
            query: elements.mainSearchInput?.value.trim() || null,
            body_name: elements.filterBody?.value || null,
            ward_no: elements.filterWard?.value || null,
            part_no: elements.filterPart?.value.trim() || null
        };

        const res = await fetch('/api/panchayat/search/export-excel', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(reqBody)
        });

        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || 'एक्सेल फ़ाइल डाउनलोड में विफल।');
        }

        const blob = await res.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        const bodyTag = reqBody.body_name ? `_${reqBody.body_name.replace(/\s+/g, '_')}` : '';
        a.download = `पंचायत_मतदाता_सूची${bodyTag}.xlsx`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        window.URL.revokeObjectURL(url);
        showToast('📊 एक्सेल रिपोर्ट सफलतापूर्वक डाउनलोड हो गई!', 'success');

    } catch (err) {
        showToast('त्रुटि: ' + err.message, 'error');
        alert('एक्सेल डाउनलोड त्रुटि: ' + err.message);
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = origHtml;
            if (window.lucide) lucide.createIcons();
        }
    }
}

// Utility: HTML Escape
function escapeHtml(str) {
    if (!str) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}
