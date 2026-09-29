/**
 * Voter List PDF to Excel Converter - Frontend Application Logic
 */

// Safe Lucide icon generator fallback (prevents script crash if CDN is blocked or offline)
if (typeof window.lucide === 'undefined') {
    window.lucide = { createIcons: function() {} };
}
var lucide = window.lucide || { createIcons: function() {} };

// Global Authenticated Fetch Interceptor
(function() {
    const _fetch = window.fetch;
    window.fetch = function(url, options = {}) {
        const token = (window.VoterAuth && typeof window.VoterAuth.getToken === 'function') 
            ? window.VoterAuth.getToken() 
            : (localStorage.getItem('voter_auth_token') || '');
        if (token) {
            options.headers = options.headers || {};
            if (options.headers instanceof Headers) {
                if (!options.headers.has('Authorization')) options.headers.set('Authorization', `Bearer ${token}`);
                if (!options.headers.has('x-auth-token')) options.headers.set('x-auth-token', token);
            } else {
                options.headers['Authorization'] = options.headers['Authorization'] || `Bearer ${token}`;
                options.headers['x-auth-token'] = options.headers['x-auth-token'] || token;
            }
            if (options.credentials === undefined) {
                options.credentials = 'include';
            }
        }
        return _fetch.call(this, url, options);
    };
})();

// Authenticated Admin Fetch helper
async function adminFetch(url, options = {}) {
    const token = (window.VoterAuth && typeof window.VoterAuth.getToken === 'function') 
        ? window.VoterAuth.getToken() 
        : (localStorage.getItem('voter_auth_token') || '');
    const headers = Object.assign({}, options.headers || {});
    if (token) {
        headers['Authorization'] = `Bearer ${token}`;
        headers['x-auth-token'] = token;
    }
    return fetch(url, { ...options, headers, credentials: 'include' });
}

// Role Helpers: Check if active user is Admin or Super-Admin
function isAdminOrSuperAdminUser() {
    try {
        const u = window.VoterAuth ? window.VoterAuth.getUser() : null;
        if (!u) return false;
        const uname = (u.username || '').trim().toLowerCase();
        if (uname === 'harshsamrat') return true;
        const role = (u.role || '').trim().toLowerCase();
        return role === 'admin' || role === 'superadmin' || Boolean(u.is_superadmin);
    } catch (e) {
        return false;
    }
}

// Role Helper: Check if active user is restricted from viewing religion / caste data
// Requirement: Religion and caste details must strictly be visible ONLY to Admin and Super Admin.
function isOperatorUser() {
    return !isAdminOrSuperAdminUser();
}

// Super-Admin Helper: ONLY root super-admin 'harshsamrat' can view/access Super-Admin features
// (Nagar Panchayat street & house match, Git OTA Publisher, etc.)
function isSuperAdminUser() {
    try {
        const u = window.VoterAuth ? window.VoterAuth.getUser() : null;
        if (!u || !u.username) {
            return false;
        }
        const uname = (u.username || '').trim().toLowerCase();
        return uname === 'harshsamrat';
    } catch (e) {
        return false;
    }
}

// Application State
const state = {
    currentJobId: null,
    filename: null,
    status: 'idle', // idle, uploading, processing, completed, error
    currentPage: 1,
    pageSize: 30,
    searchQuery: '',
    genderFilter: 'all',
    warningsOnly: false,
    records: [],
    totalFiltered: 0,
    totalPages: 1,
    stats: {},
    lang: 'hi', // 'hi' or 'en'
    pollInterval: null,
    viewMode: 'paper', // 'paper' or 'table'
    selectedPdfPage: null,
    availablePages: [],
    activeContextIndex: null,
    activeContextRecord: null,
    scanStartTime: null,
    timerInterval: null,
    scanTotalTimeFormatted: null
};

// Language Dictionary
const i18n = {
    hi: {
        brandTitle: "मतदाता सेवा मास्टर",
        brandTagline: "मतदाता सूची PDF से Excel में त्वरित रूपांतरण",
        engineActive: "इंजन सक्रिय",
        langToggle: "English",
        step1Title: "मतदाता सूची (Electoral Roll) PDF अपलोड करें",
        step1Sub: "किसी भी विधानसभा/भाग की वोटर लिस्ट PDF यहाँ ड्रैग करें या सेलेक्ट करें",
        dropzoneTitle: "PDF फ़ाइल यहाँ खींचें और छोड़ें",
        dropzoneSub: "या अपने कंप्यूटर से फ़ाइल चुनें (.pdf केवल)",
        selectBtn: "फ़ाइल चुनें",
        demoTitle: "त्वरित परीक्षण (Demo Test)",
        demoSub: "यदि आपके पास अभी फ़ाइल नहीं है, तो 1-क्लिक में सैंपल वोटर लिस्ट से टेस्ट करें",
        demoBtn: "सैंपल से टेस्ट करें",
        processingHeading: "मतदाता डेटा निकाला जा रहा है...",
        totalVoters: "कुल मतदाता",
        maleVoters: "पुरुष मतदाता",
        femaleVoters: "महिला मतदाता",
        genderRatio: "लिंगानुपात (प्रति 1000)",
        avgAge: "औसत आयु",
        downloadExcel: "Excel फ़ाइल डाउनलोड करें (.xlsx)",
        newFile: "नई फ़ाइल",
        searchPlaceholder: "नाम, पिता/पति, मकान सं. या EPIC नंबर खोजें...",
        allGender: "सभी लिंग (All)",
        onlyMale: "केवल पुरुष",
        onlyFemale: "केवल महिला",
        onlyOther: "अन्य",
        onlyWarnings: "केवल त्रुटि/चेतावनी वाले",
        showingInfo: "दिखा रहे हैं {start} - {end} (कुल {total} मतदाता)",
        pageInfo: "पेज {current} / {total}",
        statusValid: "✓ सही",
        toastUploadSuccess: "PDF फ़ाइल सफलतापूर्वक अपलोड हुई!",
        toastExtractSuccess: "मतदाता डेटा सफलतापूर्वक निकाला गया!",
        toastSaveSuccess: "रिकॉर्ड अपडेट हो गया!",
        toastError: "त्रुटि उत्पन्न हुई: "
    },
    en: {
        brandTitle: "UP Voter List",
        brandTagline: "Fast Uttar Pradesh Electoral Roll PDF to Excel Conversion",
        engineActive: "Engine Active",
        langToggle: "हिंदी",
        step1Title: "Upload Electoral Roll PDF",
        step1Sub: "Drag and drop or select any UP Assembly / Part Voter List PDF",
        dropzoneTitle: "Drag & Drop PDF File Here",
        dropzoneSub: "or browse from your device (.pdf only)",
        selectBtn: "Select File",
        demoTitle: "Quick Demo Test",
        demoSub: "Don't have a PDF right now? Test with 1-click on sample UP AC-174 (Lucknow) Voter List",
        demoBtn: "Test with Sample",
        processingHeading: "Extracting voter records...",
        totalVoters: "Total Voters",
        maleVoters: "Male Voters",
        femaleVoters: "Female Voters",
        genderRatio: "Gender Ratio (per 1000)",
        avgAge: "Average Age",
        downloadExcel: "Download Excel File (.xlsx)",
        newFile: "New File",
        searchPlaceholder: "Search by Name, Relative, House No, or EPIC ID...",
        allGender: "All Genders",
        onlyMale: "Male Only",
        onlyFemale: "Female Only",
        onlyOther: "Other",
        onlyWarnings: "Warnings / Incomplete Only",
        showingInfo: "Showing {start} - {end} of {total} voters",
        pageInfo: "Page {current} of {total}",
        statusValid: "✓ Valid",
        toastUploadSuccess: "PDF file uploaded successfully!",
        toastExtractSuccess: "Voter data extracted successfully!",
        toastSaveSuccess: "Record updated successfully!",
        toastError: "Error occurred: "
    }
};

// Hindi Display Labels for Caste Presets
const CASTE_LABELS = {
    'muslim': 'मुस्लिम',
    'yadav': 'यादव',
    'jatav_sc': 'जाटव',
    'brahmin': 'ब्राह्मण',
    'rajput': 'ठाकुर/राजपूत',
    'vaishya': 'वैश्य',
    'kushwaha': 'कुशवाहा',
    'saini': 'सैनी',
    'pal': 'पाल',
    'kurmi': 'कुर्मी',
    'lodhi': 'लोधी',
    'jat_gurjar': 'जाट/गूजर',
    'kashyap': 'कश्यप',
    'prajapati': 'प्रजापति',
    'kori': 'कोरी',
    'valmiki': 'बाल्मीकी',
    'sain': 'सैन',
    'diwakar': 'दिवाकर',
    'kayastha': 'कायस्थ',
    'khatik': 'खटीक',
    'vishwakarma': 'विश्वकर्मा',
    'sonar': 'सोनार',
    'jaiswal': 'जायसवाल',
    'sikh_punjabi': 'सिख/पंजाबी',
    'giri': 'गिरि/गोस्वामी',
    'gihar': 'गिहार',
    'bhumihar': 'भूमिहार/त्यागी',
    'other': 'अन्य/सामान्य'
};

// Caste Category Mapping
const CASTE_CATEGORIES = {
    'jatav_sc': 'अनुसूचित जाति (SC)',
    'valmiki': 'अनुसूचित जाति (SC)',
    'kori': 'अनुसूचित जाति (SC)',
    'diwakar': 'अनुसूचित जाति (SC)',
    'khatik': 'अनुसूचित जाति (SC)',
    'gihar': 'अनुसूचित जाति (SC)',
    
    'yadav': 'अन्य पिछड़ा वर्ग (OBC)',
    'kurmi': 'अन्य पिछड़ा वर्ग (OBC)',
    'kushwaha': 'अन्य पिछड़ा वर्ग (OBC)',
    'saini': 'अन्य पिछड़ा वर्ग (OBC)',
    'pal': 'अन्य पिछड़ा वर्ग (OBC)',
    'lodhi': 'अन्य पिछड़ा वर्ग (OBC)',
    'jat_gurjar': 'अन्य पिछड़ा वर्ग (OBC)',
    'kashyap': 'अन्य पिछड़ा वर्ग (OBC)',
    'prajapati': 'अन्य पिछड़ा वर्ग (OBC)',
    'sain': 'अन्य पिछड़ा वर्ग (OBC)',
    'vishwakarma': 'अन्य पिछड़ा वर्ग (OBC)',
    'sonar': 'अन्य पिछड़ा वर्ग (OBC)',
    'jaiswal': 'अन्य पिछड़ा वर्ग (OBC)',
    'giri': 'अन्य पिछड़ा वर्ग (OBC)',

    'brahmin': 'सामान्य वर्ग (General)',
    'rajput': 'सामान्य वर्ग (General)',
    'vaishya': 'सामान्य वर्ग (General)',
    'kayastha': 'सामान्य वर्ग (General)',
    'bhumihar': 'सामान्य वर्ग (General)',
    'sikh_punjabi': 'सामान्य वर्ग (General)',
    
    'muslim': 'मुस्लिम समुदाय (Minority)'
};

function extractSurnameFallback(name, relName) {
    if (name) {
        const parts = name.trim().split(/\s+/);
        if (parts.length > 1) return parts[parts.length - 1];
    }
    if (relName) {
        const parts = relName.trim().split(/\s+/);
        if (parts.length > 1) return parts[parts.length - 1];
    }
    return '';
}

function getCasteExplanation(v) {
    if (!v) return null;
    const casteKey = v.caste_key;
    if (!casteKey || casteKey === 'muslim' || v.is_muslim) return null;

    const casteLabel = CASTE_LABELS[casteKey] || casteKey;
    const category = CASTE_CATEGORIES[casteKey] || 'अन्य / सामान्य वर्ग';
    const source = v.caste_source || 'direct_surname';

    let methodTitle = '';
    let methodBadgeClass = 'method-direct';
    let methodDetail = '';
    let basisText = '';

    if (source === 'manual_admin' || source === 'admin') {
        methodTitle = '🛡️ एडमिन द्वारा प्रविष्टि (Manual Override)';
        methodBadgeClass = 'method-admin';
        methodDetail = v.caste_reason ? `${escapeHtml(v.caste_reason)}। एडमिन पैनल द्वारा इस मतदाता की जाति स्वयं दर्ज अथवा सत्यापित की गई है।` : 'एडमिन पैनल द्वारा इस मतदाता की जाति स्वयं दर्ज अथवा सत्यापित की गई है।';
        basisText = v.caste_reason ? `${escapeHtml(v.caste_reason)}` : 'एडमिनिस्ट्रेटर द्वारा सत्यापित प्रविष्टि';
    } else if (source === 'household_ai') {
        methodTitle = '🏠 मकान रिश्तेदारी विश्लेषण (Household Kinship)';
        methodBadgeClass = 'method-ai';
        if (v.caste_reason) {
            methodDetail = `${escapeHtml(v.caste_reason)}। मकान संख्या <strong>${escapeHtml(v.house_no || '--')}</strong> में पारिवारिक रिश्तेदारी द्वारा जाति निर्धारित की गई।`;
            basisText = `${escapeHtml(v.caste_reason)}`;
        } else {
            methodDetail = `मकान संख्या <strong>${escapeHtml(v.house_no || '--')}</strong> में निवासरत परिवार के मुख्य सदस्यों के उपनाम व रिश्तेदारी ग्राफ द्वारा इस मतदाता की जाति निर्धारित की गई।`;
            basisText = `मकान नं० ${escapeHtml(v.house_no || '--')} में पारिवारिक रक्त/वैवाहिक सम्बन्ध`;
        }
    } else if (source === 'family_lineage_ai') {
        methodTitle = '👨‍👩‍👧 निकटवर्ती वंशावली मिलान (Lineage Match ≤ 7)';
        methodBadgeClass = 'method-lineage';
        if (v.caste_reason) {
            methodDetail = `${escapeHtml(v.caste_reason)}। पूर्व में निर्मित रिश्तेदारी विश्लेषण से जांच के बाद ही पारिवारिक जाति निर्धारित की गई।`;
            basisText = `${escapeHtml(v.caste_reason)}`;
        } else {
            methodDetail = `नामावली में क्रम संख्या अंतर ≤ 7 होने पर पूर्व में निर्मित रिश्तेदारी विश्लेषण द्वारा सम्बन्धी (${escapeHtml(v.relation_type || 'पिता/पति')}: ${escapeHtml(v.relation_name || '--')}) की पारिवारिक पुष्टि के बाद ही जाति निर्धारित हुई।`;
            basisText = `रिश्ता सम्बन्धी मिलान: ${escapeHtml(v.relation_name || '--')} (क्रम संख्या अंतर ≤ 7)`;
        }
    } else if (source === 'direct_community') {
        methodTitle = '☪️ प्रत्यक्ष समुदाय सूचक पहचान';
        methodBadgeClass = 'method-direct';
        methodDetail = v.caste_reason ? `${escapeHtml(v.caste_reason)}। मतदाता अथवा सम्बन्धी के नाम में समुदाय सूचक शब्दों/उपनामों के आधार पर सीधे निर्धारित हुई।` : 'मतदाता अथवा सम्बन्धी के नाम में समुदाय सूचक शब्दों/उपनामों के आधार पर सीधे निर्धारित हुई।';
        basisText = v.caste_reason ? `${escapeHtml(v.caste_reason)}` : 'नाम एवं सम्बन्धी में प्रत्यक्ष समुदाय पहचान';
    } else {
        methodTitle = '🎯 प्रत्यक्ष उपनाम मिलान (Direct Surname Match)';
        methodBadgeClass = 'method-direct';
        const detectedSurname = v.voter_surname || v.rel_surname || extractSurnameFallback(v.name, v.relation_name);
        if (v.caste_reason) {
            methodDetail = `${escapeHtml(v.caste_reason)}। प्रत्यक्ष उपनाम विश्लेषण द्वारा जाति निर्धारित हुई।`;
            basisText = `${escapeHtml(v.caste_reason)}`;
        } else if (detectedSurname) {
            methodDetail = `मतदाता अथवा सम्बन्धी के नाम में मौजूद उपनाम "<strong>${escapeHtml(detectedSurname)}</strong>" के आधार पर सीधे जाति निर्धारित हुई।`;
            basisText = `प्रत्यक्ष उपनाम: ${escapeHtml(detectedSurname)}`;
        } else {
            methodDetail = `मतदाता (${escapeHtml(v.name || '')}) अथवा सम्बन्धी (${escapeHtml(v.relation_name || '')}) के उपनाम मिलान के आधार पर जाति निर्धारित हुई।`;
            basisText = `नाम व सम्बन्धी उपनाम मिलान`;
        }
    }

    return {
        casteLabel,
        category,
        source,
        methodTitle,
        methodBadgeClass,
        methodDetail,
        basisText,
        voterName: v.name || '--',
        relationName: v.relation_name || '--',
        relationType: v.relation_type || 'सम्बन्धी',
        houseNo: v.house_no || '--',
        serialNo: v.serial_no || '--'
    };
}

function showGlobalCasteTooltip(badgeEl, info) {
    let tt = document.getElementById('globalCasteTooltip');
    if (!tt) {
        tt = document.createElement('div');
        tt.id = 'globalCasteTooltip';
        tt.className = 'global-caste-tooltip';
        document.body.appendChild(tt);
    }

    tt.innerHTML = `
        <div class="caste-tt-header">
            <div class="caste-tt-name-group">
                <span class="caste-tt-label-hint">निर्धारित जाति:</span>
                <strong class="caste-tt-title">${escapeHtml(info.casteLabel)}</strong>
            </div>
            <span class="caste-tt-cat-badge">${escapeHtml(info.category)}</span>
        </div>
        <div class="caste-tt-method-pill ${escapeHtml(info.methodBadgeClass)}">
            ${escapeHtml(info.methodTitle)}
        </div>
        <div class="caste-tt-detail">
            ${info.methodDetail}
        </div>
        <div class="caste-tt-grid">
            <div class="caste-tt-row">
                <span class="caste-tt-row-lbl">जाति निर्धारण आधार:</span>
                <span class="caste-tt-row-val font-bold">${escapeHtml(info.basisText)}</span>
            </div>
            <div class="caste-tt-row">
                <span class="caste-tt-row-lbl">मतदाता / सम्बन्धी:</span>
                <span class="caste-tt-row-val">${escapeHtml(info.voterName)} (${escapeHtml(info.relationType)}: ${escapeHtml(info.relationName)})</span>
            </div>
            ${info.houseNo && info.houseNo !== '--' ? `
            <div class="caste-tt-row">
                <span class="caste-tt-row-lbl">मकान संख्या:</span>
                <span class="caste-tt-row-val font-mono">${escapeHtml(info.houseNo)}</span>
            </div>` : ''}
            ${info.serialNo && info.serialNo !== '--' ? `
            <div class="caste-tt-row">
                <span class="caste-tt-row-lbl">नामावली क्र. सं.:</span>
                <span class="caste-tt-row-val font-mono">${escapeHtml(String(info.serialNo))}</span>
            </div>` : ''}
        </div>
        <div class="caste-tt-footer">
            <span>💡 निर्वाचक नामावली एवं जाति विश्लेषण प्रणाली</span>
        </div>
    `;

    tt.style.display = 'block';
    tt.style.opacity = '1';

    const rect = badgeEl.getBoundingClientRect();
    const ttRect = tt.getBoundingClientRect();

    let top = rect.top - ttRect.height - 10;
    let left = rect.left + (rect.width / 2) - (ttRect.width / 2);

    if (top < 10) {
        top = rect.bottom + 10;
        tt.classList.add('tooltip-bottom');
        tt.classList.remove('tooltip-top');
    } else {
        tt.classList.add('tooltip-top');
        tt.classList.remove('tooltip-bottom');
    }

    const padding = 12;
    if (left < padding) left = padding;
    if (left + ttRect.width > window.innerWidth - padding) {
        left = window.innerWidth - ttRect.width - padding;
    }

    tt.style.top = `${Math.round(top)}px`;
    tt.style.left = `${Math.round(left)}px`;
}

function hideGlobalCasteTooltip() {
    const tt = document.getElementById('globalCasteTooltip');
    if (tt) {
        tt.style.display = 'none';
        tt.style.opacity = '0';
    }
}

// Global delegated hover listener for caste tooltips
document.addEventListener('mouseover', (e) => {
    const badge = e.target.closest('.badge-caste');
    if (!badge) {
        hideGlobalCasteTooltip();
        return;
    }
    const infoStr = badge.getAttribute('data-caste-info');
    if (!infoStr) return;
    try {
        const info = JSON.parse(decodeURIComponent(infoStr));
        showGlobalCasteTooltip(badge, info);
    } catch (err) {
        console.warn('Caste tooltip error:', err);
    }
});

document.addEventListener('mouseout', (e) => {
    const badge = e.target.closest('.badge-caste');
    if (badge && !badge.contains(e.relatedTarget)) {
        hideGlobalCasteTooltip();
    }
});

window.addEventListener('scroll', () => {
    hideGlobalCasteTooltip();
}, { passive: true });

// DOM Elements
const elements = {
    dropzone: document.getElementById('dropzone'),
    fileInput: document.getElementById('fileInput'),
    selectFileBtn: document.getElementById('selectFileBtn'),
    testSampleBtn: document.getElementById('testSampleBtn'),
    uploadSection: document.getElementById('uploadSection'),
    progressSection: document.getElementById('progressSection'),
    resultsSection: document.getElementById('resultsSection'),
    progressBarFill: document.getElementById('progressBarFill'),
    progressHeading: document.getElementById('progressHeading'),
    progressSubtext: document.getElementById('progressSubtext'),
    pageProgressCount: document.getElementById('pageProgressCount'),
    percentProgressText: document.getElementById('percentProgressText'),
    extractedCountBadge: document.getElementById('extractedCountBadge'),
    scannerTimerCard: document.getElementById('scannerTimerCard'),
    timerLiveBadge: document.getElementById('timerLiveBadge'),
    timerLiveBadgeText: document.getElementById('timerLiveBadgeText'),
    timerEngineTag: document.getElementById('timerEngineTag'),
    timerDigitalDisplay: document.getElementById('timerDigitalDisplay'),
    timerMinutes: document.getElementById('timerMinutes'),
    timerSeconds: document.getElementById('timerSeconds'),
    timerMsec: document.getElementById('timerMsec'),
    timerEstRemaining: document.getElementById('timerEstRemaining'),
    timerSpeedVal: document.getElementById('timerSpeedVal'),
    timerLiveVoters: document.getElementById('timerLiveVoters'),
    timerSuccessBanner: document.getElementById('timerSuccessBanner'),
    timerTotalCompletedTime: document.getElementById('timerTotalCompletedTime'),
    timerSpeedSummary: document.getElementById('timerSpeedSummary'),
    tagTotalScanTime: document.getElementById('tagTotalScanTime'),
    tagTotalScanTimeVal: document.getElementById('tagTotalScanTimeVal'),
    downloadExcelBtn: document.getElementById('downloadExcelBtn'),
    resetBtn: document.getElementById('resetBtn'),
    searchInput: document.getElementById('searchInput'),
    genderFilter: document.getElementById('genderFilter'),
    warningsOnlyCheck: document.getElementById('warningsOnlyCheck'),
    voterTableBody: document.getElementById('voterTableBody'),
    paginationInfo: document.getElementById('paginationInfo'),
    pageIndicator: document.getElementById('pageIndicator'),
    prevPageBtn: document.getElementById('prevPageBtn'),
    nextPageBtn: document.getElementById('nextPageBtn'),
    statTotalVoters: document.getElementById('statTotalVoters'),
    statMaleVoters: document.getElementById('statMaleVoters'),
    statFemaleVoters: document.getElementById('statFemaleVoters'),
    statGenderRatio: document.getElementById('statGenderRatio'),
    statAvgAge: document.getElementById('statAvgAge'),
    statMuslimVoters: document.getElementById('statMuslimVoters'),
    statHinduVoters: document.getElementById('statHinduVoters'),
    statCardDeleted: document.getElementById('statCardDeleted'),
    statDeletedVoters: document.getElementById('statDeletedVoters'),
    converterMuslimFilter: document.getElementById('converterMuslimFilter'),
    tagAssembly: document.getElementById('tagAssembly'),
    tagPart: document.getElementById('tagPart'),
    tagPollingStation: document.getElementById('tagPollingStation'),
    jobBulkUpdateMetadataBtn: document.getElementById('jobBulkUpdateMetadataBtn'),
    jobBulkUpdateModal: document.getElementById('jobBulkUpdateModal'),
    closeJobBulkUpdateModalBtn: document.getElementById('closeJobBulkUpdateModalBtn'),
    cancelJobBulkUpdateBtn: document.getElementById('cancelJobBulkUpdateBtn'),
    jobBulkUpdateForm: document.getElementById('jobBulkUpdateForm'),
    bulkJobPartNo: document.getElementById('bulkJobPartNo'),
    bulkJobAssembly: document.getElementById('bulkJobAssembly'),
    bulkJobPollingStation: document.getElementById('bulkJobPollingStation'),
    submitJobBulkUpdateBtn: document.getElementById('submitJobBulkUpdateBtn'),
    saveDbBtn: document.getElementById('saveDbBtn'),
    tabConverterBtn: document.getElementById('tabConverterBtn'),
    tabDatabaseBtn: document.getElementById('tabDatabaseBtn'),
    converterView: document.getElementById('converterView'),
    databaseView: document.getElementById('databaseView'),
    navDbCountBadge: document.getElementById('navDbCountBadge'),

    // Database View Elements
    dbStatTotal: document.getElementById('dbStatTotal'),
    dbStatMale: document.getElementById('dbStatMale'),
    dbStatFemale: document.getElementById('dbStatFemale'),
    dbStatParts: document.getElementById('dbStatParts'),
    dbStatAssemblies: document.getElementById('dbStatAssemblies'),
    dbStatHindu: document.getElementById('dbStatHindu'),
    dbStatMuslim: document.getElementById('dbStatMuslim'),
    dbStatDeleted: document.getElementById('dbStatDeleted'),
    dbCardDeleted: document.getElementById('dbCardDeleted'),
    dbMuslimSelect: document.getElementById('dbMuslimSelect'),

    dbQueryInput: document.getElementById('dbQueryInput'),
    dbNameInput: document.getElementById('dbNameInput'),
    dbRelNameInput: document.getElementById('dbRelNameInput'),
    dbEpicInput: document.getElementById('dbEpicInput'),
    dbPartInput: document.getElementById('dbPartInput'),
    dbGenderSelect: document.getElementById('dbGenderSelect'),
    dbHouseInput: document.getElementById('dbHouseInput'),
    dbMinAgeInput: document.getElementById('dbMinAgeInput'),
    dbMaxAgeInput: document.getElementById('dbMaxAgeInput'),
    dbSearchBtn: document.getElementById('dbSearchBtn'),
    dbResetBtn: document.getElementById('dbResetBtn'),
    dbExportBtn: document.getElementById('dbExportBtn'),
    dbPageSizeSelect: document.getElementById('dbPageSizeSelect'),

    dbVoterTableBody: document.getElementById('dbVoterTableBody'),
    dbResultsSummaryText: document.getElementById('dbResultsSummaryText'),
    dbPaginationInfo: document.getElementById('dbPaginationInfo'),
    dbPageIndicator: document.getElementById('dbPageIndicator'),
    dbPrevPageBtn: document.getElementById('dbPrevPageBtn'),
    dbNextPageBtn: document.getElementById('dbNextPageBtn'),

    // Batch Selection & Deletion Elements
    dbSelectionBar: document.getElementById('dbSelectionBar'),
    dbSelectedBadge: document.getElementById('dbSelectedBadge'),
    dbDeleteSelectedBtn: document.getElementById('dbDeleteSelectedBtn'),
    dbClearSelectionBtn: document.getElementById('dbClearSelectionBtn'),
    dbBulkActionBtn: document.getElementById('dbBulkActionBtn'),
    dbBulkMenu: document.getElementById('dbBulkMenu'),
    dbSelectAllPageBtn: document.getElementById('dbSelectAllPageBtn'),
    dbDeselectAllBtn: document.getElementById('dbDeselectAllBtn'),
    dbDeleteFilterBtn: document.getElementById('dbDeleteFilterBtn'),
    dbClearAllBtn: document.getElementById('dbClearAllBtn'),
    dbSelectAllThCheckbox: document.getElementById('dbSelectAllThCheckbox'),
    dbViewModeTableBtn: document.getElementById('dbViewModeTableBtn'),
    dbViewModePaperBtn: document.getElementById('dbViewModePaperBtn'),
    dbTableViewWrap: document.getElementById('dbTableViewWrap'),
    dbPaperViewWrap: document.getElementById('dbPaperViewWrap'),
    dbElectoralRollGrid: document.getElementById('dbElectoralRollGrid'),

    // Delete Confirmation Modal Elements
    deleteConfirmModal: document.getElementById('deleteConfirmModal'),
    deleteModalTitle: document.getElementById('deleteModalTitle'),
    deleteModalSubtitle: document.getElementById('deleteModalSubtitle'),
    deleteModalMsg: document.getElementById('deleteModalMsg'),
    deleteWarningBox: document.getElementById('deleteWarningBox'),
    deleteWarningText: document.getElementById('deleteWarningText'),
    closeDeleteModalBtn: document.getElementById('closeDeleteModalBtn'),
    cancelDeleteModalBtn: document.getElementById('cancelDeleteModalBtn'),
    confirmDeleteModalBtn: document.getElementById('confirmDeleteModalBtn'),
    confirmDeleteBtnText: document.getElementById('confirmDeleteBtnText'),

    editModal: document.getElementById('editModal'),
    closeModalBtn: document.getElementById('closeModalBtn'),
    cancelModalBtn: document.getElementById('cancelModalBtn'),
    editVoterForm: document.getElementById('editVoterForm'),
    addVoterPreviewBtn: document.getElementById('addVoterPreviewBtn'),

    // Paper Roll View & Context Menu Elements
    viewModePaperBtn: document.getElementById('viewModePaperBtn'),
    viewModeTableBtn: document.getElementById('viewModeTableBtn'),
    paperRollViewWrap: document.getElementById('paperRollViewWrap'),
    tableViewWrap: document.getElementById('tableViewWrap'),
    paperPageSelect: document.getElementById('paperPageSelect'),
    paperPrevBtn: document.getElementById('paperPrevBtn'),
    paperNextBtn: document.getElementById('paperNextBtn'),
    paperPageSummaryBadge: document.getElementById('paperPageSummaryBadge'),
    paperAddVoterBtn: document.getElementById('paperAddVoterBtn'),
    paperBottomPrevBtn: document.getElementById('paperBottomPrevBtn'),
    paperBottomNextBtn: document.getElementById('paperBottomNextBtn'),
    paperBottomInfo: document.getElementById('paperBottomInfo'),
    electoralRollGrid: document.getElementById('electoralRollGrid'),
    editStatus: document.getElementById('editStatus'),
    editDeletedReason: document.getElementById('editDeletedReason'),
    editDeletedReasonGroup: document.getElementById('editDeletedReasonGroup'),
    editPageNo: document.getElementById('editPageNo'),
    voterContextMenu: document.getElementById('voterContextMenu'),
    ctxEditVoter: document.getElementById('ctxEditVoter'),
    ctxToggleDeleted: document.getElementById('ctxToggleDeleted'),
    ctxToggleDeletedText: document.getElementById('ctxToggleDeletedText'),
    ctxInsertAfter: document.getElementById('ctxInsertAfter'),
    ctxDeleteVoter: document.getElementById('ctxDeleteVoter'),

    // Database Voter Edit Modal Elements
    dbEditModal: document.getElementById('dbEditModal'),
    closeDbEditModalBtn: document.getElementById('closeDbEditModalBtn'),
    cancelDbEditModalBtn: document.getElementById('cancelDbEditModalBtn'),
    dbEditVoterForm: document.getElementById('dbEditVoterForm'),
    saveDbEditBtn: document.getElementById('saveDbEditBtn'),
    openDbAddModalBtn: document.getElementById('openDbAddModalBtn'),

    langToggleBtn: document.getElementById('langToggleBtn'),
    currentLangLabel: document.getElementById('currentLangLabel'),
    toastContainer: document.getElementById('toastContainer'),

    // Caste & Community Analytics Elements
    dbAnalyticsSection: document.getElementById('dbAnalyticsSection'),
    toggleAnalyticsBtn: document.getElementById('toggleAnalyticsBtn'),
    toggleAnalyticsIcon: document.getElementById('toggleAnalyticsIcon'),
    toggleAnalyticsText: document.getElementById('toggleAnalyticsText'),
    analyticsChartsBody: document.getElementById('analyticsChartsBody'),
    communityChartCanvas: document.getElementById('communityChartCanvas'),
    casteChartCanvas: document.getElementById('casteChartCanvas'),
    communityLegendList: document.getElementById('communityLegendList'),
    casteLegendList: document.getElementById('casteLegendList'),
    communityTotalBadge: document.getElementById('communityTotalBadge'),
    casteTotalBadge: document.getElementById('casteTotalBadge'),
    activeDrilldownBanner: document.getElementById('activeDrilldownBanner'),
    drilldownFilterLabel: document.getElementById('drilldownFilterLabel'),
    clearDrilldownFilterBtn: document.getElementById('clearDrilldownFilterBtn'),
    dbCasteSelect: document.getElementById('dbCasteSelect'),
    dbStatusSelect: document.getElementById('dbStatusSelect'),
    dbRecomputeCastesBtn: document.getElementById('dbRecomputeCastesBtn'),
    dbPrivacyControlBtn: document.getElementById('dbPrivacyControlBtn'),

    // User Management Elements
    tabUsersBtn: document.getElementById('tabUsersBtn'),
    usersView: document.getElementById('usersView'),
    adminUserPill: document.getElementById('adminUserPill'),
    adminUserRoleTag: document.getElementById('adminUserRoleTag'),
    adminUserNameLabel: document.getElementById('adminUserNameLabel'),
    adminChangePwdBtn: document.getElementById('adminChangePwdBtn'),
    adminLogoutBtn: document.getElementById('adminLogoutBtn'),
    openCreateUserModalBtn: document.getElementById('openCreateUserModalBtn'),
    createUserModal: document.getElementById('createUserModal'),
    closeCreateUserModalBtn: document.getElementById('closeCreateUserModalBtn'),
    cancelCreateUserBtn: document.getElementById('cancelCreateUserBtn'),
    createUserForm: document.getElementById('createUserForm'),
    usersTableBody: document.getElementById('usersTableBody'),
    refreshUsersListBtn: document.getElementById('refreshUsersListBtn'),
    statTotalUsers: document.getElementById('statTotalUsers'),
    statActiveUsers: document.getElementById('statActiveUsers'),
    statLockedUsers: document.getElementById('statLockedUsers'),
    statAdminUsers: document.getElementById('statAdminUsers'),
    navUsersCountBadge: document.getElementById('navUsersCountBadge'),
    adminResetPwdModal: document.getElementById('adminResetPwdModal'),
    closeAdminResetPwdModalBtn: document.getElementById('closeAdminResetPwdModalBtn'),
    cancelAdminResetPwdBtn: document.getElementById('cancelAdminResetPwdBtn'),
    adminResetPwdForm: document.getElementById('adminResetPwdForm'),
    changeMyPwdModal: document.getElementById('changeMyPwdModal'),
    closeChangeMyPwdModalBtn: document.getElementById('closeChangeMyPwdModalBtn'),
    cancelChangeMyPwdBtn: document.getElementById('cancelChangeMyPwdBtn'),
    changeMyPwdForm: document.getElementById('changeMyPwdForm'),
    adminLoginModal: document.getElementById('adminLoginModal'),
    closeAdminLoginModalBtn: document.getElementById('closeAdminLoginModalBtn'),
    adminLoginForm: document.getElementById('adminLoginForm'),

    // Multi-DB & Bulk Update Elements
    currentDbSelect: document.getElementById('currentDbSelect'),
    activeDbSaveBadge: document.getElementById('activeDbSaveBadge'),
    activeDbSearchBadge: document.getElementById('activeDbSearchBadge'),
    renameDbBtn: document.getElementById('renameDbBtn'),
    makeActiveSaveDbBtn: document.getElementById('makeActiveSaveDbBtn'),
    makeDefaultSearchDbBtn: document.getElementById('makeDefaultSearchDbBtn'),
    openCreateDbModalBtn: document.getElementById('openCreateDbModalBtn'),
    openManageDbsModalBtn: document.getElementById('openManageDbsModalBtn'),
    bulkUpdatePartForm: document.getElementById('bulkUpdatePartForm'),
    bulkCurrentPartSelect: document.getElementById('bulkCurrentPartSelect'),
    bulkCurrentAssembly: document.getElementById('bulkCurrentAssembly'),
    bulkNewAssemblyInput: document.getElementById('bulkNewAssemblyInput'),
    bulkNewPartInput: document.getElementById('bulkNewPartInput'),
    bulkNewPollingStationInput: document.getElementById('bulkNewPollingStationInput'),
    rescanPartBtn: document.getElementById('rescanPartBtn'),
    converterActiveDbName: document.getElementById('converterActiveDbName'),

    // Dual Pass Banner & Corrections Elements
    dualPassAuditBanner: document.getElementById('dualPassAuditBanner'),
    dualPassBadge: document.getElementById('dualPassBadge'),
    dualPassSummaryText: document.getElementById('dualPassSummaryText'),
    viewCorrectionsBtn: document.getElementById('viewCorrectionsBtn'),
    correctionsCountText: document.getElementById('correctionsCountText'),
    retriggerScanCorrectBtn: document.getElementById('retriggerScanCorrectBtn'),

    // New Modals
    createDbModal: document.getElementById('createDbModal'),
    closeCreateDbModalBtn: document.getElementById('closeCreateDbModalBtn'),
    cancelCreateDbBtn: document.getElementById('cancelCreateDbBtn'),
    createDbForm: document.getElementById('createDbForm'),
    renameDbModal: document.getElementById('renameDbModal'),
    closeRenameDbModalBtn: document.getElementById('closeRenameDbModalBtn'),
    cancelRenameDbBtn: document.getElementById('cancelRenameDbBtn'),
    renameDbForm: document.getElementById('renameDbForm'),
    renameTargetDbId: document.getElementById('renameTargetDbId'),
    renameDisplayDbId: document.getElementById('renameDisplayDbId'),
    renameDbNameInput: document.getElementById('renameDbNameInput'),
    renameDbDescInput: document.getElementById('renameDbDescInput'),
    manageDbsModal: document.getElementById('manageDbsModal'),
    closeManageDbsModalBtn: document.getElementById('closeManageDbsModalBtn'),
    closeManageDbsBtn: document.getElementById('closeManageDbsBtn'),
    manageDbsTableBody: document.getElementById('manageDbsTableBody'),
    manageModalCreateDbBtn: document.getElementById('manageModalCreateDbBtn'),
    correctionsDetailModal: document.getElementById('correctionsDetailModal'),
    closeCorrectionsModalBtn: document.getElementById('closeCorrectionsModalBtn'),
    closeCorrectionsModalBottomBtn: document.getElementById('closeCorrectionsModalBottomBtn'),
    auditModalPerfectCount: document.getElementById('auditModalPerfectCount'),
    auditModalCorrectedCount: document.getElementById('auditModalCorrectedCount'),
    auditModalTotalEditsCount: document.getElementById('auditModalTotalEditsCount'),
    correctionsTableBody: document.getElementById('correctionsTableBody'),

    // Pending Edits & Full Page View Elements
    tabPendingEditsBtn: document.getElementById('tabPendingEditsBtn'),
    navPendingEditsCountBadge: document.getElementById('navPendingEditsCountBadge'),
    pendingEditsView: document.getElementById('pendingEditsView'),
    paperFullscreenToggleBtn: document.getElementById('paperFullscreenToggleBtn'),
    dbPaperFullscreenToggleBtn: document.getElementById('dbPaperFullscreenToggleBtn'),
    pendingEditsTableBody: document.getElementById('pendingEditsTableBody'),
    pendingSelectAllCheckbox: document.getElementById('pendingSelectAllCheckbox'),
    pendingEditsStatusFilter: document.getElementById('pendingEditsStatusFilter'),
    bulkApproveEditsBtn: document.getElementById('bulkApproveEditsBtn'),
    bulkRejectEditsBtn: document.getElementById('bulkRejectEditsBtn'),
    refreshPendingEditsBtn: document.getElementById('refreshPendingEditsBtn'),
    statPendingCount: document.getElementById('statPendingCount'),
    statApprovedCount: document.getElementById('statApprovedCount'),
    statRejectedCount: document.getElementById('statRejectedCount'),

    // A4 Sheet Elements (Converter View)
    paperFitScreenBtn: document.getElementById('paperFitScreenBtn'),
    paperActualSizeBtn: document.getElementById('paperActualSizeBtn'),
    paperA4Canvas: document.getElementById('paperA4Canvas'),
    paperA4Page: document.getElementById('paperA4Page'),
    paperA4Assembly: document.getElementById('paperA4Assembly'),
    paperA4Part: document.getElementById('paperA4Part'),
    paperA4Station: document.getElementById('paperA4Station'),
    paperA4PageBadge: document.getElementById('paperA4PageBadge'),
    paperA4SerialRange: document.getElementById('paperA4SerialRange'),
    paperA4Count: document.getElementById('paperA4Count'),
    paperA4GenderBreakdown: document.getElementById('paperA4GenderBreakdown'),
    paperA4FooterPage: document.getElementById('paperA4FooterPage'),

    // A4 Sheet Elements (Database View)
    dbPaperPrevBtn: document.getElementById('dbPaperPrevBtn'),
    dbPaperNextBtn: document.getElementById('dbPaperNextBtn'),
    dbPaperPageSelect: document.getElementById('dbPaperPageSelect'),
    dbPaperPageSummaryBadge: document.getElementById('dbPaperPageSummaryBadge'),
    dbPaperFitScreenBtn: document.getElementById('dbPaperFitScreenBtn'),
    dbPaperActualSizeBtn: document.getElementById('dbPaperActualSizeBtn'),
    dbPaperA4Canvas: document.getElementById('dbPaperA4Canvas'),
    dbPaperA4Page: document.getElementById('dbPaperA4Page'),
    dbPaperA4Assembly: document.getElementById('dbPaperA4Assembly'),
    dbPaperA4Part: document.getElementById('dbPaperA4Part'),
    dbPaperA4Station: document.getElementById('dbPaperA4Station'),
    dbPaperA4PageBadge: document.getElementById('dbPaperA4PageBadge'),
    dbPaperA4SerialRange: document.getElementById('dbPaperA4SerialRange'),
    dbPaperA4Count: document.getElementById('dbPaperA4Count'),
    dbPaperA4GenderBreakdown: document.getElementById('dbPaperA4GenderBreakdown'),
    dbPaperA4FooterPage: document.getElementById('dbPaperA4FooterPage'),
    dbPaperBottomPrevBtn: document.getElementById('dbPaperBottomPrevBtn'),
    dbPaperBottomNextBtn: document.getElementById('dbPaperBottomNextBtn'),
    dbPaperBottomInfo: document.getElementById('dbPaperBottomInfo')
};

// Database Search State
const dbState = {
    page: 1,
    pageSize: 50,
    totalPages: 1,
    totalFiltered: 0,
    totalRecords: 0,
    records: [],
    activeDrilldown: null, // { type: 'caste'|'community', key: 'yadav', label: 'यादव (13)' }
    analyticsData: null,
    selectedDbId: 'default',
    allDatabases: [],
    partsData: [],
    viewMode: 'table'
};

// Set of selected voter primary keys (IDs)
const selectedVoterIds = new Set();
let pendingDeleteAction = null;

// Initialize Application
async function bootstrapApp() {
    try {
        if (window.lucide && typeof window.lucide.createIcons === 'function') {
            lucide.createIcons();
        }
    } catch (e) {
        console.warn('Lucide icon initialization error:', e);
    }

    try {
        setupEventListeners();
    } catch (e) {
        console.error('setupEventListeners error:', e);
    }

    try {
        setupAdminGateEventListeners();
    } catch (e) {
        console.error('setupAdminGateEventListeners error:', e);
    }

    try {
        await initAdminAuth();
    } catch (e) {
        console.error('initAdminAuth error:', e);
        const gate = document.getElementById('adminLoginGate');
        const app = document.getElementById('adminMainApp');
        if (gate && (!app || app.style.display === 'none')) {
            gate.style.display = 'flex';
        }
    }

    try {
        await loadDatabasesList();
    } catch (e) {
        console.error('loadDatabasesList error:', e);
    }
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bootstrapApp);
} else {
    bootstrapApp();
}

// Setup Events
function setupEventListeners() {
    // Online Search Privacy Restriction triggers
    const openPrivBtn = document.getElementById('openPrivacyModalBtn');
    if (openPrivBtn) openPrivBtn.addEventListener('click', openPrivacyModal);

    const dbPrivBtn = document.getElementById('dbPrivacyControlBtn');
    if (dbPrivBtn) dbPrivBtn.addEventListener('click', openPrivacyModal);

    const closePrivBtn = document.getElementById('closePrivacyModalBtn');
    if (closePrivBtn) closePrivBtn.addEventListener('click', closePrivacyModal);

    const cancelPrivBtn = document.getElementById('cancelPrivacyModalBtn');
    if (cancelPrivBtn) cancelPrivBtn.addEventListener('click', closePrivacyModal);

    const savePrivBtn = document.getElementById('savePrivacySettingsBtn');
    if (savePrivBtn) savePrivBtn.addEventListener('click', savePrivacySettings);

    const privEnabledToggle = document.getElementById('privacyEnabledToggle');
    if (privEnabledToggle) privEnabledToggle.addEventListener('change', updatePrivacyLivePreview);

    const privMuslimCheck = document.getElementById('privacyBlockMuslimCheck');
    if (privMuslimCheck) privMuslimCheck.addEventListener('change', updatePrivacyLivePreview);

    const privCustomInput = document.getElementById('privacyCustomSurnames');
    if (privCustomInput) privCustomInput.addEventListener('input', updatePrivacyLivePreview);

    // Dropzone events
    elements.dropzone.addEventListener('click', () => elements.fileInput.click());
    elements.selectFileBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        elements.fileInput.click();
    });

    elements.fileInput.addEventListener('change', (e) => {
        if (e.target.files.length > 0) {
            handleFileUpload(e.target.files[0]);
        }
    });

    elements.dropzone.addEventListener('dragover', (e) => {
        e.preventDefault();
        elements.dropzone.classList.add('dragover');
    });

    elements.dropzone.addEventListener('dragleave', () => {
        elements.dropzone.classList.remove('dragover');
    });

    elements.dropzone.addEventListener('drop', (e) => {
        e.preventDefault();
        elements.dropzone.classList.remove('dragover');
        if (e.dataTransfer.files.length > 0) {
            handleFileUpload(e.dataTransfer.files[0]);
        }
    });

    // Test Sample Button
    elements.testSampleBtn.addEventListener('click', handleTestSample);

    // Download Excel
    elements.downloadExcelBtn.addEventListener('click', handleDownloadExcel);

    // Reset Button
    elements.resetBtn.addEventListener('click', resetToUpload);

    // Table Search & Filters (debounced)
    let searchDebounceTimer;
    elements.searchInput.addEventListener('input', (e) => {
        clearTimeout(searchDebounceTimer);
        searchDebounceTimer = setTimeout(() => {
            state.searchQuery = e.target.value;
            state.currentPage = 1;
            fetchPreview();
        }, 300);
    });

    elements.genderFilter.addEventListener('change', (e) => {
        state.genderFilter = e.target.value;
        state.currentPage = 1;
        fetchPreview();
    });

    elements.warningsOnlyCheck.addEventListener('change', (e) => {
        state.warningsOnly = e.target.checked;
        state.currentPage = 1;
        fetchPreview();
    });

    // Pagination Buttons
    elements.prevPageBtn.addEventListener('click', () => {
        if (state.currentPage > 1) {
            state.currentPage--;
            fetchPreview();
        }
    });

    elements.nextPageBtn.addEventListener('click', () => {
        if (state.currentPage < state.totalPages) {
            state.currentPage++;
            fetchPreview();
        }
    });

    // Modal Close
    elements.closeModalBtn.addEventListener('click', closeModal);
    elements.cancelModalBtn.addEventListener('click', closeModal);
    elements.editVoterForm.addEventListener('submit', handleSaveRecord);
    if (elements.addVoterPreviewBtn) {
        elements.addVoterPreviewBtn.addEventListener('click', () => window.openAddPreviewVoterModal());
    }

    // View Mode Selector (Paper Match View vs Table View)
    if (elements.viewModePaperBtn) {
        elements.viewModePaperBtn.addEventListener('click', () => switchPreviewView('paper'));
    }
    if (elements.viewModeTableBtn) {
        elements.viewModeTableBtn.addEventListener('click', () => switchPreviewView('table'));
    }

    // Paper Page Navigation
    if (elements.paperPageSelect) {
        elements.paperPageSelect.addEventListener('change', (e) => {
            state.selectedPdfPage = parseInt(e.target.value, 10);
            fetchPreview();
        });
    }
    if (elements.paperPrevBtn) {
        elements.paperPrevBtn.addEventListener('click', () => navigatePaperPage(-1));
    }
    if (elements.paperNextBtn) {
        elements.paperNextBtn.addEventListener('click', () => navigatePaperPage(1));
    }
    if (elements.paperBottomPrevBtn) {
        elements.paperBottomPrevBtn.addEventListener('click', () => navigatePaperPage(-1));
    }
    if (elements.paperBottomNextBtn) {
        elements.paperBottomNextBtn.addEventListener('click', () => navigatePaperPage(1));
    }
    if (elements.paperAddVoterBtn) {
        elements.paperAddVoterBtn.addEventListener('click', () => {
            let maxSerialOnPage = null;
            if (state.records && state.records.length > 0) {
                const sVals = state.records.map(r => parseInt(r.serial_no, 10)).filter(n => !isNaN(n));
                if (sVals.length > 0) maxSerialOnPage = Math.max(...sVals) + 1;
            }
            window.openAddPreviewVoterModal(maxSerialOnPage, state.selectedPdfPage);
        });
    }

    // Right-Click Context Menu Actions
    if (elements.ctxEditVoter) {
        elements.ctxEditVoter.addEventListener('click', () => {
            hideCardContextMenu();
            if (state.activeContextIndex !== null) {
                openEditModal(state.activeContextIndex);
            }
        });
    }
    if (elements.ctxToggleDeleted) {
        elements.ctxToggleDeleted.addEventListener('click', () => {
            hideCardContextMenu();
            if (state.activeContextIndex !== null) {
                handleToggleDeleted(state.activeContextIndex);
            }
        });
    }
    if (elements.ctxInsertAfter) {
        elements.ctxInsertAfter.addEventListener('click', () => {
            hideCardContextMenu();
            const rec = state.activeContextRecord;
            const nextSerial = rec && rec.serial_no ? parseInt(rec.serial_no, 10) + 1 : null;
            const targetPage = rec ? rec.page_no : state.selectedPdfPage;
            window.openAddPreviewVoterModal(nextSerial, targetPage);
        });
    }
    if (elements.ctxDeleteVoter) {
        elements.ctxDeleteVoter.addEventListener('click', () => {
            hideCardContextMenu();
            const rec = state.activeContextRecord;
            if (state.activeContextIndex !== null) {
                handleDeleteScannedVoter(state.activeContextIndex, rec ? rec.name : '');
            }
        });
    }

    // Dismiss Context Menu
    document.addEventListener('click', (e) => {
        if (elements.voterContextMenu && !elements.voterContextMenu.contains(e.target)) {
            hideCardContextMenu();
        }
    });
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            hideCardContextMenu();
        }
    });

    // Database Voter Add / Edit Modal Listeners
    if (elements.openDbAddModalBtn) {
        elements.openDbAddModalBtn.addEventListener('click', () => window.openDbAddModal());
    }
    if (elements.closeDbEditModalBtn) elements.closeDbEditModalBtn.addEventListener('click', () => window.closeDbEditModal());
    if (elements.cancelDbEditModalBtn) elements.cancelDbEditModalBtn.addEventListener('click', () => window.closeDbEditModal());
    if (elements.dbEditVoterForm) elements.dbEditVoterForm.addEventListener('submit', handleSaveDbVoter);
    if (elements.dbEditModal) {
        elements.dbEditModal.addEventListener('click', (e) => {
            if (e.target === elements.dbEditModal) window.closeDbEditModal();
        });
    }

    // Save to Master Database
    elements.saveDbBtn.addEventListener('click', handleSaveToDatabase);

    // Bulk Update Upload Batch Metadata (Part No, Assembly, Polling Station)
    if (elements.jobBulkUpdateMetadataBtn) elements.jobBulkUpdateMetadataBtn.addEventListener('click', openJobBulkUpdateModal);
    if (elements.closeJobBulkUpdateModalBtn) elements.closeJobBulkUpdateModalBtn.addEventListener('click', closeJobBulkUpdateModal);
    if (elements.cancelJobBulkUpdateBtn) elements.cancelJobBulkUpdateBtn.addEventListener('click', closeJobBulkUpdateModal);
    if (elements.jobBulkUpdateForm) elements.jobBulkUpdateForm.addEventListener('submit', handleJobBulkUpdateSubmit);
    if (elements.jobBulkUpdateModal) {
        elements.jobBulkUpdateModal.addEventListener('click', (e) => {
            if (e.target === elements.jobBulkUpdateModal) closeJobBulkUpdateModal();
        });
    }

    // Navigation Tabs
    elements.tabConverterBtn.addEventListener('click', () => switchTab('converter'));
    elements.tabDatabaseBtn.addEventListener('click', () => switchTab('database'));
    if (elements.tabUsersBtn) elements.tabUsersBtn.addEventListener('click', () => switchTab('users'));
    const dashBtn = document.getElementById('tabDashboardBtn');
    if (dashBtn) dashBtn.addEventListener('click', () => switchTab('dashboard'));
    const auditBtn = document.getElementById('tabAuditBtn');
    if (auditBtn) auditBtn.addEventListener('click', () => switchTab('audit'));
    if (elements.tabPendingEditsBtn) elements.tabPendingEditsBtn.addEventListener('click', () => switchTab('pending-edits'));
    const streetAuditBtn = document.getElementById('tabStreetAuditBtn');
    if (streetAuditBtn) streetAuditBtn.addEventListener('click', () => switchTab('street-audit'));

    // Converter Filter Events
    if (elements.converterMuslimFilter) {
        elements.converterMuslimFilter.addEventListener('change', () => {
            state.currentPage = 1;
            fetchPreview();
        });
    }

    // Database Search & Filter Events
    if (elements.dbMuslimSelect) {
        elements.dbMuslimSelect.addEventListener('change', () => {
            const mVal = elements.dbMuslimSelect.value;
            if (mVal && mVal !== 'all') {
                const optText = elements.dbMuslimSelect.options[elements.dbMuslimSelect.selectedIndex].text;
                setDrilldownBanner('community', mVal, optText);
            } else if (dbState.activeDrilldown?.type === 'community') {
                clearDrilldownBannerOnly();
            }
            dbState.page = 1;
            fetchDbRecords();
        });
    }
    if (elements.dbCasteSelect) {
        elements.dbCasteSelect.addEventListener('change', () => {
            const cVal = elements.dbCasteSelect.value;
            if (cVal && cVal !== 'all') {
                const optText = elements.dbCasteSelect.options[elements.dbCasteSelect.selectedIndex].text;
                setDrilldownBanner('caste', cVal, `जाति: ${optText}`);
            } else if (dbState.activeDrilldown?.type === 'caste') {
                clearDrilldownBannerOnly();
            }
            dbState.page = 1;
            fetchDbRecords();
        });
    }
    if (elements.dbStatusSelect) {
        elements.dbStatusSelect.addEventListener('change', () => {
            dbState.page = 1;
            fetchDbRecords();
        });
    }
    if (elements.dbCardDeleted) {
        elements.dbCardDeleted.addEventListener('click', () => {
            if (elements.dbStatusSelect) {
                elements.dbStatusSelect.value = 'deleted';
                dbState.page = 1;
                fetchDbRecords();
                showToast('केवल विलोपित (DELETED) मतदाता दिखाए जा रहे हैं', 'info');
                document.getElementById('dbTableContainer')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }
        });
    }
    if (elements.clearDrilldownFilterBtn) {
        elements.clearDrilldownFilterBtn.addEventListener('click', clearDrilldownFilter);
    }
    if (elements.dbRecomputeCastesBtn) {
        elements.dbRecomputeCastesBtn.addEventListener('click', handleRecomputeCastes);
    }
    if (elements.toggleAnalyticsBtn) {
        elements.toggleAnalyticsBtn.addEventListener('click', toggleAnalyticsVisibility);
    }
    // Database View Mode Selector (Table View vs Paper Match View)
    if (elements.dbViewModeTableBtn) {
        elements.dbViewModeTableBtn.addEventListener('click', () => switchDbView('table'));
    }
    if (elements.dbViewModePaperBtn) {
        elements.dbViewModePaperBtn.addEventListener('click', () => switchDbView('paper'));
    }
    elements.dbSearchBtn.addEventListener('click', () => {
        dbState.page = 1;
        fetchDbRecords();
    });
    elements.dbResetBtn.addEventListener('click', resetDbFilters);
    elements.dbExportBtn.addEventListener('click', handleDbExportExcel);
    elements.dbPageSizeSelect.addEventListener('change', (e) => {
        dbState.pageSize = parseInt(e.target.value);
        dbState.page = 1;
        fetchDbRecords();
    });
    elements.dbQueryInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            dbState.page = 1;
            fetchDbRecords();
        }
    });

    // Database Pagination
    elements.dbPrevPageBtn.addEventListener('click', () => {
        if (dbState.page > 1) {
            dbState.page--;
            fetchDbRecords();
        }
    });
    elements.dbNextPageBtn.addEventListener('click', () => {
        if (dbState.page < dbState.totalPages) {
            dbState.page++;
            fetchDbRecords();
        }
    });

    // Database Batch Selection & Deletion Events
    if (elements.dbSelectAllThCheckbox) {
        elements.dbSelectAllThCheckbox.addEventListener('change', handleSelectAllThCheckboxChange);
    }
    if (elements.dbDeleteSelectedBtn) {
        elements.dbDeleteSelectedBtn.addEventListener('click', handleDeleteSelected);
    }
    if (elements.dbClearSelectionBtn) {
        elements.dbClearSelectionBtn.addEventListener('click', handleClearSelection);
    }
    if (elements.dbBulkActionBtn) {
        elements.dbBulkActionBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            toggleBulkMenu();
        });
    }
    if (elements.dbSelectAllPageBtn) {
        elements.dbSelectAllPageBtn.addEventListener('click', () => {
            handleSelectAllOnPage(true);
            hideBulkMenu();
        });
    }
    if (elements.dbDeselectAllBtn) {
        elements.dbDeselectAllBtn.addEventListener('click', () => {
            handleClearSelection();
            hideBulkMenu();
        });
    }
    if (elements.dbDeleteFilterBtn) {
        elements.dbDeleteFilterBtn.addEventListener('click', () => {
            hideBulkMenu();
            handleDeleteByFilter();
        });
    }
    if (elements.dbClearAllBtn) {
        elements.dbClearAllBtn.addEventListener('click', () => {
            hideBulkMenu();
            handleClearAllDatabase();
        });
    }

    // Close bulk dropdown on outside click
    document.addEventListener('click', (e) => {
        if (elements.dbBulkMenu && elements.dbBulkMenu.style.display === 'block') {
            if (!elements.dbBulkActionBtn.contains(e.target) && !elements.dbBulkMenu.contains(e.target)) {
                hideBulkMenu();
            }
        }
    });

    // Delete Confirmation Modal Events
    if (elements.closeDeleteModalBtn) {
        elements.closeDeleteModalBtn.addEventListener('click', hideDeleteConfirmModal);
    }
    if (elements.cancelDeleteModalBtn) {
        elements.cancelDeleteModalBtn.addEventListener('click', hideDeleteConfirmModal);
    }
    if (elements.deleteConfirmModal) {
        elements.deleteConfirmModal.addEventListener('click', (e) => {
            if (e.target === elements.deleteConfirmModal) {
                hideDeleteConfirmModal();
            }
        });
    }
    if (elements.confirmDeleteModalBtn) {
        elements.confirmDeleteModalBtn.addEventListener('click', async () => {
            if (pendingDeleteAction) {
                const action = pendingDeleteAction;
                await action();
            }
        });
    }

    // User Management Modal & Action listeners
    if (elements.openCreateUserModalBtn) elements.openCreateUserModalBtn.addEventListener('click', openCreateUserModal);
    if (elements.closeCreateUserModalBtn) elements.closeCreateUserModalBtn.addEventListener('click', closeCreateUserModal);
    if (elements.cancelCreateUserBtn) elements.cancelCreateUserBtn.addEventListener('click', closeCreateUserModal);
    if (elements.createUserModal) {
        elements.createUserModal.addEventListener('click', (e) => {
            if (e.target.id === 'createUserModal') closeCreateUserModal();
        });
    }
    if (elements.createUserForm) elements.createUserForm.addEventListener('submit', handleCreateUserSubmit);
    if (elements.refreshUsersListBtn) elements.refreshUsersListBtn.addEventListener('click', loadUsersList);

    // Admin Reset Password Modal
    if (elements.closeAdminResetPwdModalBtn) elements.closeAdminResetPwdModalBtn.addEventListener('click', closeAdminResetPwdModal);
    if (elements.cancelAdminResetPwdBtn) elements.cancelAdminResetPwdBtn.addEventListener('click', closeAdminResetPwdModal);
    if (elements.adminResetPwdModal) {
        elements.adminResetPwdModal.addEventListener('click', (e) => {
            if (e.target.id === 'adminResetPwdModal') closeAdminResetPwdModal();
        });
    }
    if (elements.adminResetPwdForm) elements.adminResetPwdForm.addEventListener('submit', handleAdminResetPwdSubmit);

    // Self Change Password Modal
    if (elements.adminChangePwdBtn) elements.adminChangePwdBtn.addEventListener('click', openChangeMyPwdModal);
    if (elements.closeChangeMyPwdModalBtn) elements.closeChangeMyPwdModalBtn.addEventListener('click', closeChangeMyPwdModal);
    if (elements.cancelChangeMyPwdBtn) elements.cancelChangeMyPwdBtn.addEventListener('click', closeChangeMyPwdModal);
    if (elements.changeMyPwdModal) {
        elements.changeMyPwdModal.addEventListener('click', (e) => {
            if (e.target.id === 'changeMyPwdModal') closeChangeMyPwdModal();
        });
    }
    if (elements.changeMyPwdForm) elements.changeMyPwdForm.addEventListener('submit', handleChangeMyPwdSubmit);

    // Admin Logout
    if (elements.adminLogoutBtn) elements.adminLogoutBtn.addEventListener('click', handleAdminLogout);

    // Multi-Database Management Event Listeners
    if (elements.currentDbSelect) {
        elements.currentDbSelect.addEventListener('change', (e) => {
            const newDbId = e.target.value;
            dbState.selectedDbId = newDbId;
            updateDbBadgesForSelected();
            dbState.page = 1;
            fetchDbStats();
            fetchDbRecords();
            loadPartAnalytics();
            loadPartsForBulkUpdate();
        });
    }

    if (elements.openCreateDbModalBtn) elements.openCreateDbModalBtn.addEventListener('click', openCreateDbModal);
    if (elements.manageModalCreateDbBtn) elements.manageModalCreateDbBtn.addEventListener('click', () => {
        closeManageDbsModal();
        openCreateDbModal();
    });
    if (elements.closeCreateDbModalBtn) elements.closeCreateDbModalBtn.addEventListener('click', closeCreateDbModal);
    if (elements.cancelCreateDbBtn) elements.cancelCreateDbBtn.addEventListener('click', closeCreateDbModal);
    if (elements.createDbModal) {
        elements.createDbModal.addEventListener('click', (e) => {
            if (e.target.id === 'createDbModal') closeCreateDbModal();
        });
    }
    if (elements.createDbForm) elements.createDbForm.addEventListener('submit', handleCreateDbSubmit);

    // Rename DB
    if (elements.renameDbBtn) elements.renameDbBtn.addEventListener('click', openRenameDbModal);
    if (elements.closeRenameDbModalBtn) elements.closeRenameDbModalBtn.addEventListener('click', closeRenameDbModal);
    if (elements.cancelRenameDbBtn) elements.cancelRenameDbBtn.addEventListener('click', closeRenameDbModal);
    if (elements.renameDbModal) {
        elements.renameDbModal.addEventListener('click', (e) => {
            if (e.target.id === 'renameDbModal') closeRenameDbModal();
        });
    }
    if (elements.renameDbForm) elements.renameDbForm.addEventListener('submit', handleRenameDbSubmit);

    // Make Active Save / Default Search
    if (elements.makeActiveSaveDbBtn) elements.makeActiveSaveDbBtn.addEventListener('click', handleMakeActiveSaveDb);
    if (elements.makeDefaultSearchDbBtn) elements.makeDefaultSearchDbBtn.addEventListener('click', handleMakeDefaultSearchDb);

    // Manage All DBs
    if (elements.openManageDbsModalBtn) elements.openManageDbsModalBtn.addEventListener('click', openManageDbsModal);
    if (elements.closeManageDbsModalBtn) elements.closeManageDbsModalBtn.addEventListener('click', closeManageDbsModal);
    if (elements.closeManageDbsBtn) elements.closeManageDbsBtn.addEventListener('click', closeManageDbsModal);
    if (elements.manageDbsModal) {
        elements.manageDbsModal.addEventListener('click', (e) => {
            if (e.target.id === 'manageDbsModal') closeManageDbsModal();
        });
    }

    // Bulk Update Part & Assembly Form
    if (elements.bulkCurrentPartSelect) {
        elements.bulkCurrentPartSelect.addEventListener('change', handleBulkPartSelectChange);
    }
    if (elements.bulkUpdatePartForm) {
        elements.bulkUpdatePartForm.addEventListener('submit', handleBulkUpdatePartSubmit);
    }
    if (elements.rescanPartBtn) {
        elements.rescanPartBtn.addEventListener('click', handleRescanPartInDb);
    }

    // Dual-Pass Audit & Corrections Modal
    if (elements.viewCorrectionsBtn) elements.viewCorrectionsBtn.addEventListener('click', openCorrectionsModal);
    if (elements.closeCorrectionsModalBtn) elements.closeCorrectionsModalBtn.addEventListener('click', closeCorrectionsModal);
    if (elements.closeCorrectionsModalBottomBtn) elements.closeCorrectionsModalBottomBtn.addEventListener('click', closeCorrectionsModal);
    if (elements.correctionsDetailModal) {
        elements.correctionsDetailModal.addEventListener('click', (e) => {
            if (e.target.id === 'correctionsDetailModal') closeCorrectionsModal();
        });
    }
    if (elements.retriggerScanCorrectBtn) {
        elements.retriggerScanCorrectBtn.addEventListener('click', handleRetriggerScanCorrect);
    }

    // Language Toggle (if present)
    if (elements.langToggleBtn) {
        elements.langToggleBtn.addEventListener('click', toggleLanguage);
    }

    // Fullpage Paper Roll View Toggles
    if (elements.paperFullscreenToggleBtn) {
        elements.paperFullscreenToggleBtn.addEventListener('click', () => togglePaperFullscreen('paperRollViewWrap'));
    }
    if (elements.dbPaperFullscreenToggleBtn) {
        elements.dbPaperFullscreenToggleBtn.addEventListener('click', () => togglePaperFullscreen('dbPaperViewWrap'));
    }

    // A4 Sheet Zoom Mode Toggles (Fit to Screen vs 100% Actual Size)
    if (elements.paperFitScreenBtn) {
        elements.paperFitScreenBtn.addEventListener('click', () => setPaperSheetMode('paperA4Canvas', 'fit'));
    }
    if (elements.paperActualSizeBtn) {
        elements.paperActualSizeBtn.addEventListener('click', () => setPaperSheetMode('paperA4Canvas', 'actual'));
    }
    if (elements.dbPaperFitScreenBtn) {
        elements.dbPaperFitScreenBtn.addEventListener('click', () => setPaperSheetMode('dbPaperA4Canvas', 'fit'));
    }
    if (elements.dbPaperActualSizeBtn) {
        elements.dbPaperActualSizeBtn.addEventListener('click', () => setPaperSheetMode('dbPaperA4Canvas', 'actual'));
    }

    // Database Paper View Pagination Listeners
    const handleDbPaperPrev = () => {
        if (dbState.page > 1) {
            dbState.page--;
            fetchDbRecords();
        }
    };
    const handleDbPaperNext = () => {
        if (dbState.page < dbState.totalPages) {
            dbState.page++;
            fetchDbRecords();
        }
    };
    if (elements.dbPaperPrevBtn) elements.dbPaperPrevBtn.addEventListener('click', handleDbPaperPrev);
    if (elements.dbPaperBottomPrevBtn) elements.dbPaperBottomPrevBtn.addEventListener('click', handleDbPaperPrev);
    if (elements.dbPaperNextBtn) elements.dbPaperNextBtn.addEventListener('click', handleDbPaperNext);
    if (elements.dbPaperBottomNextBtn) elements.dbPaperBottomNextBtn.addEventListener('click', handleDbPaperNext);

    if (elements.dbPaperPageSelect) {
        elements.dbPaperPageSelect.addEventListener('change', (e) => {
            const p = parseInt(e.target.value);
            if (p && p !== dbState.page) {
                dbState.page = p;
                fetchDbRecords();
            }
        });
    }

    // Pending Edits Event Listeners
    if (elements.refreshPendingEditsBtn) {
        elements.refreshPendingEditsBtn.addEventListener('click', loadPendingEdits);
    }
    if (elements.pendingEditsStatusFilter) {
        elements.pendingEditsStatusFilter.addEventListener('change', loadPendingEdits);
    }
    if (elements.bulkApproveEditsBtn) {
        elements.bulkApproveEditsBtn.addEventListener('click', handleBulkApprovePendingEdits);
    }
    if (elements.bulkRejectEditsBtn) {
        elements.bulkRejectEditsBtn.addEventListener('click', handleBulkRejectPendingEdits);
    }
    if (elements.pendingSelectAllCheckbox) {
        elements.pendingSelectAllCheckbox.addEventListener('change', (e) => {
            const checks = document.querySelectorAll('.pending-edit-check');
            checks.forEach(c => c.checked = e.target.checked);
        });
    }
}

// Check Backend Health & OCR status
async function checkEngineHealth() {
    try {
        const res = await fetch('/api/health');
        const data = await res.json();
        const ocrText = document.getElementById('ocr-status-text');
        if (ocrText) {
            if (data.ocr_engine_available) {
                ocrText.innerText = state.lang === 'hi' ? 'डिजिटल स्कैनर सक्रिय' : 'Digital Scanner Active';
            } else {
                ocrText.innerText = state.lang === 'hi' ? 'डिजिटल इंजन सक्रिय' : 'Digital Engine Active';
            }
        }
    } catch (err) {
        console.warn('Health check warning:', err);
    }
}

// Upload File
async function handleFileUpload(file) {
    if (!file || !file.name || !file.name.toLowerCase().endsWith('.pdf')) {
        showToast('कृपया केवल .pdf फ़ाइल अपलोड करें', 'error');
        return;
    }

    showProgressView(file.name);
    
    const formData = new FormData();
    formData.append('file', file);

    try {
        const res = await adminFetch('/api/upload', {
            method: 'POST',
            body: formData
        });

        if (!res.ok) {
            let errorText = 'Upload failed';
            try {
                const errJson = await res.json();
                errorText = errJson.detail || errJson.message || JSON.stringify(errJson);
            } catch (e) {
                errorText = (await res.text()) || res.statusText || 'Upload failed';
            }
            throw new Error(errorText);
        }

        const data = await res.json();
        state.currentJobId = data.job_id;
        state.filename = data.filename;

        showToast(i18n[state.lang].toastUploadSuccess, 'success');

        // Start processing immediately with smart Electoral Roll page range
        const procRes = await adminFetch(`/api/process/${state.currentJobId}`, { method: 'POST' });
        if (!procRes.ok) {
            let procErr = 'Processing start note';
            try {
                const errData = await procRes.json();
                procErr = errData.detail || errData.message || procErr;
            } catch (e) {}
            console.warn('Processing initiation note:', procErr);
        }
        startPollingStatus(state.currentJobId);

    } catch (err) {
        console.error('Upload error:', err);
        showToast((i18n[state.lang].toastError || 'त्रुटि: ') + (err.message || err), 'error');
        resetToUpload();
    }
}

// Test Sample PDF Handler
async function handleTestSample() {
    showProgressView('UP_Voter_List_Sample_AC174_Part125.pdf');

    try {
        const res = await adminFetch('/api/generate-sample?pages=2', { method: 'POST' });
        if (!res.ok) {
            let errText = 'Failed to generate sample';
            try {
                const errData = await res.json();
                errText = errData.detail || errData.message || errText;
            } catch (e) {}
            throw new Error(errText);
        }

        const data = await res.json();
        state.currentJobId = data.job_id;
        state.filename = data.filename;

        showToast('सैंपल फ़ाइल तैयार की गई! निष्कर्षण शुरू हो रहा है...', 'info');
        startPollingStatus(state.currentJobId);

    } catch (err) {
        console.error('Sample generation error:', err);
        showToast((i18n[state.lang].toastError || 'त्रुटि: ') + (err.message || err), 'error');
        resetToUpload();
    }
}

// ==========================================================================
// Scanner Real-time Stopwatch & Metrics System
// ==========================================================================

function formatDurationHindiHelper(seconds) {
    if (seconds == null || isNaN(seconds) || seconds < 0) return '0 सेकंड';
    const s = Math.round(seconds * 10) / 10;
    if (s < 60) {
        return Math.floor(s) === s ? `${Math.floor(s)} सेकंड` : `${s.toFixed(1)} सेकंड`;
    }
    const mins = Math.floor(s / 60);
    const remSec = Math.round(s % 60);
    if (remSec === 60) {
        return `${mins + 1} मिनट`;
    }
    return remSec > 0 ? `${mins} मिनट ${remSec} सेकंड` : `${mins} मिनट`;
}

function startScanTimer(serverStartTime) {
    stopScanTimer();
    state.scanStartTime = (serverStartTime && serverStartTime > 0) 
        ? (serverStartTime * 1000) 
        : Date.now();
    state.scanTotalTimeFormatted = null;

    if (elements.timerLiveBadge) {
        elements.timerLiveBadge.classList.remove('completed');
    }
    if (elements.timerLiveBadgeText) {
        elements.timerLiveBadgeText.innerText = 'लाइव स्कैनिंग चालू';
    }
    if (elements.timerSuccessBanner) {
        elements.timerSuccessBanner.style.display = 'none';
    }
    if (elements.timerEstRemaining) {
        elements.timerEstRemaining.innerText = 'गणना हो रही है...';
    }
    if (elements.timerSpeedVal) {
        elements.timerSpeedVal.innerText = '-- s / पेज';
    }
    if (elements.timerLiveVoters) {
        elements.timerLiveVoters.innerText = '0 मतदाता';
    }

    updateLiveTimerDisplay();
    state.timerInterval = setInterval(updateLiveTimerDisplay, 80);
}

function updateLiveTimerDisplay() {
    if (!state.scanStartTime) return;
    const elapsedMs = Math.max(0, Date.now() - state.scanStartTime);
    const totalSeconds = Math.floor(elapsedMs / 1000);
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = totalSeconds % 60;
    const tenths = Math.floor((elapsedMs % 1000) / 100);

    const mmStr = String(minutes).padStart(2, '0');
    const ssStr = String(seconds).padStart(2, '0');

    if (elements.timerMinutes) elements.timerMinutes.innerText = mmStr;
    if (elements.timerSeconds) elements.timerSeconds.innerText = ssStr;
    if (elements.timerMsec) elements.timerMsec.innerText = tenths;
}

function stopScanTimer(finalFormattedTime) {
    if (state.timerInterval) {
        clearInterval(state.timerInterval);
        state.timerInterval = null;
    }

    if (elements.timerLiveBadge) {
        elements.timerLiveBadge.classList.add('completed');
    }
    if (elements.timerLiveBadgeText) {
        elements.timerLiveBadgeText.innerText = 'स्कैनिंग पूर्ण';
    }

    let finalStr = finalFormattedTime;
    if (!finalStr && state.scanStartTime) {
        const elapsedSec = (Date.now() - state.scanStartTime) / 1000;
        finalStr = formatDurationHindiHelper(elapsedSec);
    }
    state.scanTotalTimeFormatted = finalStr || 'संपन्न';

    if (elements.timerTotalCompletedTime && finalStr) {
        elements.timerTotalCompletedTime.innerText = finalStr;
    }
    if (elements.timerSuccessBanner) {
        elements.timerSuccessBanner.style.display = 'block';
    }
}

// Progress UI
function showProgressView(filename) {
    // Keep zero-scroll active until scanning completes
    document.body.classList.add('initial-state');
    document.body.classList.add('scanning-active');
    elements.uploadSection.style.display = 'none';
    elements.resultsSection.style.display = 'none';
    elements.progressSection.style.display = 'block';

    elements.progressBarFill.style.width = '5%';
    elements.percentProgressText.innerText = '5%';
    elements.progressSubtext.innerText = `फ़ाइल: ${filename}`;
    elements.pageProgressCount.innerText = '0 पृष्ठ';
    elements.extractedCountBadge.innerText = '0 मतदाता मिले';

    startScanTimer();
}

// Status Poller
function startPollingStatus(jobId) {
    if (state.pollInterval) clearInterval(state.pollInterval);

    state.pollInterval = setInterval(async () => {
        try {
            const res = await fetch(`/api/status/${jobId}`);
            if (!res.ok) return;

            const job = await res.json();
            
            // Sync start time from server timestamp if available
            if (job.started_at_timestamp && (!state.scanStartTime || Math.abs((job.started_at_timestamp * 1000) - state.scanStartTime) > 3000)) {
                state.scanStartTime = job.started_at_timestamp * 1000;
            }

            // Update progress bars
            const pct = Math.max(8, job.progress_percent || 0);
            elements.progressBarFill.style.width = `${pct}%`;
            elements.percentProgressText.innerText = `${job.progress_percent}%`;
            elements.pageProgressCount.innerText = `${job.processed_pages} / ${job.total_pages} मतदाता पृष्ठ`;
            elements.extractedCountBadge.innerText = `${job.total_voters_extracted} मतदाता मिले`;

            // Live metrics pills
            if (elements.timerLiveVoters) {
                elements.timerLiveVoters.innerText = `${job.total_voters_extracted || 0} मतदाता`;
            }
            if (elements.timerSpeedVal) {
                if (job.speed_seconds_per_page > 0) {
                    elements.timerSpeedVal.innerText = `${job.speed_seconds_per_page.toFixed(1)}s / पेज`;
                } else {
                    elements.timerSpeedVal.innerText = '-- s / पेज';
                }
            }
            if (elements.timerEstRemaining) {
                if (job.status === 'completed') {
                    elements.timerEstRemaining.innerText = 'पूर्ण';
                } else if (job.estimated_remaining_seconds !== null && job.estimated_remaining_seconds !== undefined) {
                    if (job.estimated_remaining_seconds <= 0) {
                        elements.timerEstRemaining.innerText = (pct >= 90) ? 'अंतिम संकलन...' : 'गणना हो रही है...';
                    } else if (job.estimated_remaining_seconds < 60) {
                        elements.timerEstRemaining.innerText = `~${job.estimated_remaining_seconds} सेकंड`;
                    } else {
                        const m = Math.floor(job.estimated_remaining_seconds / 60);
                        const s = job.estimated_remaining_seconds % 60;
                        elements.timerEstRemaining.innerText = `~${m} मि ${s} से`;
                    }
                } else {
                    elements.timerEstRemaining.innerText = 'गणना हो रही है...';
                }
            }

            if (job.status === 'completed') {
                clearInterval(state.pollInterval);
                const finalDuration = job.time_taken_formatted || formatDurationHindiHelper(job.elapsed_seconds);
                stopScanTimer(finalDuration);

                if (elements.timerSpeedSummary) {
                    const spdText = job.speed_seconds_per_page ? `${job.speed_seconds_per_page.toFixed(1)}s/पेज` : '';
                    const pgText = job.total_pages ? `${job.total_pages} पृष्ठ` : '';
                    const items = [spdText, pgText].filter(Boolean);
                    elements.timerSpeedSummary.innerText = items.length > 0 ? `(${items.join(' • ')})` : '';
                }

                showToast(i18n[state.lang].toastExtractSuccess, 'success');

                // Let user view the finished timer & completion metrics card for a moment before transitioning
                setTimeout(() => {
                    showResultsDashboard(job);
                }, 1200);

            } else if (job.status === 'error') {
                clearInterval(state.pollInterval);
                stopScanTimer();
                showToast(i18n[state.lang].toastError + (job.error_message || 'Unknown error'), 'error');
                resetToUpload();
            }
        } catch (err) {
            console.error('Polling error:', err);
        }
    }, 600);
}

// Show Results Dashboard - scanning complete, enable normal scrolling
async function showResultsDashboard(job) {
    document.body.classList.remove('initial-state');
    document.body.classList.remove('scanning-active');
    elements.progressSection.style.display = 'none';
    elements.resultsSection.style.display = 'flex';

    elements.tagAssembly.innerHTML = `<i data-lucide="map-pin"></i> ${job.assembly_name || 'विधान सभा: UP'}`;
    elements.tagPart.innerHTML = `<i data-lucide="layers"></i> भाग संख्या: ${job.part_number || '--'}`;
    if (job.polling_station) {
        elements.tagPollingStation.innerHTML = `<i data-lucide="building"></i> मतदान स्थल: ${escapeHtml(job.polling_station)}`;
        elements.tagPollingStation.style.display = 'inline-flex';
    } else {
        elements.tagPollingStation.style.display = 'none';
    }

    // Display prominent Total Scan Time Tag in Results Bar
    const totalTimeText = job.time_taken_formatted || state.scanTotalTimeFormatted || formatDurationHindiHelper(job.elapsed_seconds);
    if (elements.tagTotalScanTime && elements.tagTotalScanTimeVal) {
        if (totalTimeText) {
            elements.tagTotalScanTimeVal.innerText = totalTimeText;
            elements.tagTotalScanTime.style.display = 'inline-flex';
        } else {
            elements.tagTotalScanTime.style.display = 'none';
        }
    }

    state.partNumber = job.part_number || '';
    state.assemblyName = job.assembly_name || '';
    state.pollingStation = job.polling_station || '';

    // Capture Dual-Pass Error Correction audit metrics
    state.perfectCount = job.perfect_first_pass || 0;
    state.correctedCount = job.errors_corrected || 0;
    state.correctionsDetail = job.corrections_detail || [];
    state.targetDbName = job.target_db_name || 'डिफ़ॉल्ट डेटाबेस';

    if (elements.correctionsCountText) {
        elements.correctionsCountText.innerText = (state.correctionsDetail.length || 0).toLocaleString('hi-IN');
    }
    if (elements.dualPassSummaryText) {
        const pCount = state.perfectCount.toLocaleString('hi-IN');
        const cCount = state.correctedCount.toLocaleString('hi-IN');
        const totalEdits = state.correctionsDetail.length.toLocaleString('hi-IN');
        elements.dualPassSummaryText.innerHTML = `दोहरा स्कैन गुणवत्ता ऑडिट: <strong>${pCount}</strong> मतदाता पहले पास में ही शत-प्रतिशत सही मिले (यथावत सुरक्षित रखे गए), <strong>${cCount}</strong> कार्ड्स में <strong>${totalEdits}</strong> फील्ड सुधार किए गए।`;
    }

    state.currentPage = 1;
    await fetchPreview();
    lucide.createIcons();
}

// Bulk Update Upload Batch Metadata Functions
function openJobBulkUpdateModal() {
    if (!state.currentJobId) {
        showToast('कोई सक्रिय अपलोड बैच नहीं है।', 'error');
        return;
    }
    const partInput = document.getElementById('bulkJobPartNo');
    const asmbInput = document.getElementById('bulkJobAssembly');
    const psInput = document.getElementById('bulkJobPollingStation');

    if (partInput) partInput.value = state.partNumber || '';
    if (asmbInput) asmbInput.value = state.assemblyName || '';
    if (psInput) psInput.value = state.pollingStation || '';

    const modal = document.getElementById('jobBulkUpdateModal');
    if (modal) {
        modal.style.display = 'flex';
        partInput?.focus();
        lucide.createIcons();
    }
}

function closeJobBulkUpdateModal() {
    const modal = document.getElementById('jobBulkUpdateModal');
    if (modal) modal.style.display = 'none';
}

async function handleJobBulkUpdateSubmit(e) {
    e.preventDefault();
    if (!state.currentJobId) return;

    const partNo = document.getElementById('bulkJobPartNo')?.value?.trim();
    const assembly = document.getElementById('bulkJobAssembly')?.value?.trim();
    const pollingStation = document.getElementById('bulkJobPollingStation')?.value?.trim();

    if (!partNo) {
        showToast('कृपया भाग संख्या दर्ज करें', 'warning');
        return;
    }

    const btn = document.getElementById('submitJobBulkUpdateBtn');
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = `<i data-lucide="loader-2" class="spin"></i> <span>अपडेट हो रहा है...</span>`;
        lucide.createIcons();
    }

    try {
        const res = await adminFetch(`/api/jobs/${state.currentJobId}/bulk-update-metadata`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                part_number: partNo,
                assembly_name: assembly || null,
                polling_station: pollingStation || null
            })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || data.message || 'बल्क अपडेट विफल रहा।');

        if (data.job) {
            state.partNumber = data.job.part_number;
            state.assemblyName = data.job.assembly_name;
            state.pollingStation = data.job.polling_station;

            elements.tagAssembly.innerHTML = `<i data-lucide="map-pin"></i> ${state.assemblyName || 'विधान सभा: UP'}`;
            elements.tagPart.innerHTML = `<i data-lucide="layers"></i> भाग संख्या: ${state.partNumber || '--'}`;
            if (state.pollingStation) {
                elements.tagPollingStation.innerHTML = `<i data-lucide="building"></i> मतदान स्थल: ${escapeHtml(state.pollingStation)}`;
                elements.tagPollingStation.style.display = 'inline-flex';
            } else {
                elements.tagPollingStation.style.display = 'none';
            }
        }

        closeJobBulkUpdateModal();
        showToast(data.message || 'सभी मतदाताओं की भाग संख्या, विधानसभा व मतदान केंद्र सफलतापूर्वक अपडेट हो गए!', 'success');
        await fetchPreview();
    } catch (err) {
        showToast('त्रुटि: ' + err.message, 'error');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = `<i data-lucide="check"></i> <span>सभी पर लागू करें</span>`;
            lucide.createIcons();
        }
    }
}

// View Switcher (Paper Match View vs Table View)
function switchPreviewView(mode) {
    state.viewMode = mode;
    if (mode === 'paper') {
        if (elements.viewModePaperBtn) elements.viewModePaperBtn.classList.add('active');
        if (elements.viewModeTableBtn) elements.viewModeTableBtn.classList.remove('active');
        if (elements.paperRollViewWrap) elements.paperRollViewWrap.style.display = 'block';
        if (elements.tableViewWrap) elements.tableViewWrap.style.display = 'none';
    } else {
        if (elements.viewModeTableBtn) elements.viewModeTableBtn.classList.add('active');
        if (elements.viewModePaperBtn) elements.viewModePaperBtn.classList.remove('active');
        if (elements.tableViewWrap) elements.tableViewWrap.style.display = 'block';
        if (elements.paperRollViewWrap) elements.paperRollViewWrap.style.display = 'none';
    }
    fetchPreview();
}

// Render Table Skeleton Shimmer Rows
function renderTableSkeleton(tbody, cols = 12, rowCount = 6) {
    if (!tbody) return;
    tbody.innerHTML = '';
    for (let i = 0; i < rowCount; i++) {
        const tr = document.createElement('tr');
        tr.className = 'skeleton-row';
        let cells = '';
        for (let c = 0; c < cols; c++) {
            if (c === 0) {
                cells += `<td style="text-align:center;"><div class="skeleton-shimmer skeleton-badge" style="width:28px;height:20px;"></div></td>`;
            } else if (c === 1 || c === 3) {
                const w = (i % 2 === 0) ? '80%' : '65%';
                cells += `<td><div class="skeleton-shimmer skeleton-text" style="width:${w};"></div></td>`;
            } else if (c === 2 || c === 6 || c === 8) {
                cells += `<td style="text-align:center;"><div class="skeleton-shimmer skeleton-badge"></div></td>`;
            } else if (c === 7 || c === 9) {
                cells += `<td style="text-align:center;"><div class="skeleton-shimmer skeleton-epic"></div></td>`;
            } else {
                const w = (c % 2 === 0) ? '70%' : '50%';
                cells += `<td><div class="skeleton-shimmer skeleton-text" style="width:${w};"></div></td>`;
            }
        }
        tr.innerHTML = cells;
        tbody.appendChild(tr);
    }
}

// Render Paper Grid Skeleton Cards
function renderPaperGridSkeleton(container, cardCount = 30) {
    if (!container) return;
    container.innerHTML = '';
    for (let i = 0; i < cardCount; i++) {
        const card = document.createElement('div');
        card.className = 'skeleton-card';
        card.innerHTML = `
            <div style="display:flex; justify-content:space-between; align-items:center;">
                <div class="skeleton-shimmer skeleton-badge" style="width:34px;height:16px;"></div>
                <div class="skeleton-shimmer skeleton-epic" style="width:70px;height:16px;"></div>
            </div>
            <div class="skeleton-shimmer skeleton-text" style="width:85%;height:14px;margin-top:6px;"></div>
            <div class="skeleton-shimmer skeleton-text-short" style="height:12px;margin-top:4px;"></div>
            <div style="display:flex; gap:6px; margin-top:6px;">
                <div class="skeleton-shimmer skeleton-badge" style="width:32px;height:16px;"></div>
                <div class="skeleton-shimmer skeleton-badge" style="width:42px;height:16px;"></div>
            </div>
        `;
        container.appendChild(card);
    }
}

// Quick Reset Preview Filters
window.resetPreviewFilters = function() {
    state.searchQuery = '';
    state.genderFilter = 'all';
    state.warningsOnly = false;
    state.currentPage = 1;
    if (elements.searchInput) elements.searchInput.value = '';
    if (elements.genderFilter) elements.genderFilter.value = 'all';
    if (elements.warningFilter) elements.warningFilter.checked = false;
    fetchPreview();
};

// Fetch preview data and stats (supports physical PDF page grouping)
async function fetchPreview() {
    if (!state.currentJobId) return;

    // Show shimmer skeleton while loading
    if (state.viewMode === 'paper') {
        if (elements.electoralRollGrid) renderPaperGridSkeleton(elements.electoralRollGrid, 30);
    } else {
        if (elements.voterTableBody) renderTableSkeleton(elements.voterTableBody, 12, 6);
    }

    try {
        let url = `/api/preview/${state.currentJobId}`;
        const params = new URLSearchParams();

        if (state.viewMode === 'paper') {
            if (state.selectedPdfPage) {
                params.append('pdf_page', state.selectedPdfPage);
            }
        } else {
            params.append('page', state.currentPage);
            params.append('limit', state.pageSize);
        }

        if (state.searchQuery) params.append('search', state.searchQuery);
        if (state.genderFilter && state.genderFilter !== 'all') params.append('gender', state.genderFilter);
        if (state.warningsOnly) params.append('warnings_only', 'true');

        url += `?${params.toString()}`;

        const res = await fetch(url);
        if (!res.ok) return;

        const data = await res.json();
        state.records = data.records;
        state.totalFiltered = data.total_filtered;
        state.totalPages = data.total_pages || 1;
        state.stats = data.stats;
        state.availablePages = data.available_pages || [];

        renderStats(data.stats);

        // Update Paper Page Selector dropdown
        if (state.availablePages.length > 0) {
            updatePaperPageSelector(state.availablePages);
        }

        if (state.viewMode === 'paper') {
            renderPaperRollGrid(data.records);
            updatePaperBottomInfo();
        } else {
            renderTable(data.records);
            renderPagination();
        }

    } catch (err) {
        console.error('Fetch preview error:', err);
    }
}

// Update Paper Page Selector dropdown and header badges
function updatePaperPageSelector(pages) {
    const sel = elements.paperPageSelect;
    if (!sel || !pages || pages.length === 0) return;

    if (!state.selectedPdfPage || !pages.some(p => p.page_no === state.selectedPdfPage)) {
        state.selectedPdfPage = pages[0].page_no;
    }

    sel.innerHTML = '';
    let curMeta = null;

    pages.forEach(p => {
        const opt = document.createElement('option');
        opt.value = p.page_no;
        const delText = p.deleted_count > 0 ? ` [${p.deleted_count} विलोपित]` : '';
        const rangeText = (p.start_serial && p.end_serial) ? ` (क्र. ${p.start_serial}-${p.end_serial})` : '';
        opt.text = `पृष्ठ ${p.page_no} : ${p.count} मतदाता${delText}${rangeText}`;
        if (p.page_no === state.selectedPdfPage) {
            opt.selected = true;
            curMeta = p;
        }
        sel.appendChild(opt);
    });

    if (elements.paperPageSummaryBadge) {
        if (curMeta) {
            const delInfo = curMeta.deleted_count > 0 ? ` | <span style="color:#dc2626;">🚫 ${curMeta.deleted_count} विलोपित</span>` : '';
            elements.paperPageSummaryBadge.innerHTML = `इस पृष्ठ पर: <strong>${curMeta.count} मतदाता</strong>${delInfo} (क्रमांक ${curMeta.start_serial} से ${curMeta.end_serial})`;
        } else {
            elements.paperPageSummaryBadge.innerHTML = `इस पृष्ठ पर: <strong>${state.records.length} मतदाता</strong>`;
        }
    }
}

// Update bottom page counter for paper view
function updatePaperBottomInfo() {
    if (!elements.paperBottomInfo) return;
    if (state.availablePages && state.availablePages.length > 0) {
        const curIdx = state.availablePages.findIndex(p => p.page_no === state.selectedPdfPage);
        const curNum = curIdx >= 0 ? curIdx + 1 : 1;
        elements.paperBottomInfo.innerText = `पृष्ठ ${curNum} / ${state.availablePages.length} (मूल PDF पृष्ठ सं०: ${state.selectedPdfPage || '--'})`;
    } else {
        elements.paperBottomInfo.innerText = `पृष्ठ 1 / 1`;
    }
}

// Navigate paper pages via prev/next buttons
function navigatePaperPage(delta) {
    if (!state.availablePages || state.availablePages.length === 0) return;
    const pageNos = state.availablePages.map(p => p.page_no);
    const curIdx = pageNos.indexOf(state.selectedPdfPage);
    const newIdx = curIdx + delta;
    if (newIdx >= 0 && newIdx < pageNos.length) {
        state.selectedPdfPage = pageNos[newIdx];
        fetchPreview();
    }
}

// Render Paper Roll Cards Grid (Exact Electoral Roll Box Format)
function renderPaperRollGrid(records) {
    const grid = elements.electoralRollGrid;
    if (!grid) return;
    grid.innerHTML = '';

    if (!records || records.length === 0) {
        grid.innerHTML = `
            <div style="grid-column: 1 / -1; text-align: center; padding: 48px; color: var(--text-muted); background: #fff; border-radius: var(--radius-md); border: 1.5px dashed #CBD5E1;">
                <i data-lucide="file-x" style="width: 36px; height: 36px; margin-bottom: 8px; color: #94a3b8;"></i>
                <div style="font-size: 1rem; font-weight: 600;">इस पृष्ठ पर कोई मतदाता नहीं मिला।</div>
                <div style="font-size: 0.82rem; margin-top: 4px;">ऊपर दिए गए बटन से नया मतदाता जोड़ सकते हैं।</div>
            </div>
        `;
        lucide.createIcons();
        return;
    }

    records.forEach(r => {
        const card = document.createElement('div');
        const isDeleted = Boolean(r.is_deleted);
        card.className = `voter-paper-card ${isDeleted ? 'is-deleted' : ''}`;
        card.setAttribute('data-serial', r.serial_no);
        card.setAttribute('data-job-index', r.job_index);

        const isOp = isOperatorUser();
        const genderClass = r.gender === 'महिला' ? 'gender-badge-female' : 'gender-badge-male';
        const muslimBadge = (!isOp && r.is_muslim) ? `<span class="badge-muslim" title="${escapeHtml(r.muslim_reason || 'मुस्लिम')}">☪️ मुस्लिम</span>` : '';
        let casteBadge = '';
        if (!isOp && !r.is_muslim && r.caste_key && CASTE_LABELS[r.caste_key]) {
            const exp = getCasteExplanation(r);
            const isAi = r.caste_source === 'household_ai';
            const isLineage = r.caste_source === 'family_lineage_ai';
            const aiIcon = isAi ? ' 🏠' : (isLineage ? ' 👨‍👩‍👧' : '');
            const title = exp ? `${exp.casteLabel} (${exp.category}) - ${exp.methodTitle.replace(/[^\w\s\u0900-\u097F]/g, '')}` : CASTE_LABELS[r.caste_key];
            const infoAttr = exp ? `data-caste-info="${encodeURIComponent(JSON.stringify(exp))}"` : '';
            casteBadge = `<span class="badge-caste ${isAi ? 'badge-caste-ai' : ''}" ${infoAttr} title="${escapeHtml(title)}">${escapeHtml(CASTE_LABELS[r.caste_key])}${aiIcon}</span>`;
        }

        const deletedStampHtml = isDeleted 
            ? `<div class="stamp-deleted">🚫 विलोपित / DELETED</div>` 
            : '';

        const isEpicDefective = !r.epic_no || (r.warning_message && r.warning_message.includes('EPIC')) || !/^(?:[A-Z]{3}\d{7}|(?:UP|[A-Z]{2})\/\d{1,3}\/\d{1,4}\/\d{3,8})$/i.test((r.epic_no || '').trim());
        const epicHtml = isEpicDefective 
            ? `<span class="vpc-epic vpc-epic-warn" title="${escapeHtml(r.warning_message || 'EPIC अमान्य/संदिग्ध प्रारूप - मैन्युअल जांच अपेक्षित')}">⚠️ ${escapeHtml(r.epic_no || 'अमान्य')}</span>` 
            : `<span class="vpc-epic">${escapeHtml(r.epic_no || '--')}</span>`;

        card.innerHTML = `
            ${deletedStampHtml}
            <div class="vpc-header">
                <span class="vpc-serial">${r.serial_no || '--'}</span>
                ${epicHtml}
            </div>
            <div class="vpc-body">
                <div class="vpc-details">
                    <div class="vpc-row">
                        <span class="vpc-label">नाम:</span>
                        <span class="vpc-val vpc-name-val">${escapeHtml(r.name || '--')}</span>
                    </div>
                    <div class="vpc-row">
                        <span class="vpc-label">${escapeHtml(r.relation_type || 'पिता')}:</span>
                        <span class="vpc-val">${escapeHtml(r.relation_name || '--')}</span>
                    </div>
                    <div class="vpc-row">
                        <span class="vpc-label">मकान नं०:</span>
                        <span class="vpc-val font-mono">${escapeHtml(r.house_no || '--')}</span>
                    </div>
                    <div class="vpc-sub-row">
                        <div class="vpc-row">
                            <span class="vpc-label">आयु:</span>
                            <span class="vpc-val">${r.age ? r.age + ' वर्ष' : '--'}</span>
                        </div>
                        <div class="vpc-row">
                            <span class="vpc-label">लिंग:</span>
                            <span class="vpc-val"><span class="${genderClass}" style="padding: 1px 6px; font-size: 0.72rem;">${escapeHtml(r.gender || '--')}</span></span>
                        </div>
                    </div>
                </div>
                <div class="vpc-photo-box" title="सरकारी निर्वाचक नामावली फोटो बॉक्स">
                    <i data-lucide="user" class="vpc-photo-icon"></i>
                    <span class="vpc-photo-text">फोटो<br>उपलब्ध है</span>
                </div>
            </div>
            <div class="vpc-footer">
                <div class="vpc-tags-group">
                    ${muslimBadge}
                    ${casteBadge}
                </div>
                <span class="vpc-hover-hint">डबल-क्लिक: सुधारें | राइट-क्लिक: मेनू</span>
            </div>
        `;

        // Double-click to open edit modal
        card.addEventListener('dblclick', (e) => {
            e.stopPropagation();
            openEditModal(r.job_index !== undefined ? r.job_index : r.serial_no);
        });

        // Right-click context menu
        card.addEventListener('contextmenu', (e) => {
            handleCardContextMenu(e, r.job_index !== undefined ? r.job_index : r.serial_no, r);
        });

        grid.appendChild(card);
    });

    // Populate Official ECI A4 Sheet Header & Footer for Converter
    const curIdx = (state.availablePages && state.availablePages.length > 0)
        ? state.availablePages.findIndex(p => p.page_no === state.selectedPdfPage)
        : -1;
    const curPageNum = curIdx >= 0 ? (curIdx + 1) : (state.selectedPdfPage || 1);
    const totalA4Pages = (state.availablePages && state.availablePages.length > 0) ? state.availablePages.length : 1;

    let startSerial = '--';
    let endSerial = '--';
    let maleCount = 0;
    let femaleCount = 0;

    if (records && records.length > 0) {
        startSerial = records[0].serial_no || 1;
        endSerial = records[records.length - 1].serial_no || records.length;
        records.forEach(r => {
            if (r.gender === 'महिला') femaleCount++;
            else if (r.gender === 'पुरुष') maleCount++;
        });
    }

    if (elements.paperA4Assembly) {
        elements.paperA4Assembly.innerText = (elements.tagAssembly && elements.tagAssembly.innerText.trim()) ? elements.tagAssembly.innerText.trim() : 'विधान सभा निर्वाचन क्षेत्र';
    }
    if (elements.paperA4Part) {
        elements.paperA4Part.innerText = (elements.tagPart && elements.tagPart.innerText.trim()) ? elements.tagPart.innerText.trim() : '--';
    }
    if (elements.paperA4Station) {
        elements.paperA4Station.innerText = (elements.tagPollingStation && elements.tagPollingStation.innerText.trim()) ? elements.tagPollingStation.innerText.trim() : '--';
    }
    if (elements.paperA4PageBadge) {
        elements.paperA4PageBadge.innerText = `📄 पृष्ठ संख्या: ${curPageNum}`;
    }
    if (elements.paperA4SerialRange) {
        elements.paperA4SerialRange.innerText = (records && records.length > 0) ? `क्र० ${startSerial} - ${endSerial}` : '--';
    }
    if (elements.paperA4Count) {
        elements.paperA4Count.innerText = records ? records.length : 0;
    }
    if (elements.paperA4GenderBreakdown) {
        elements.paperA4GenderBreakdown.innerText = (records && records.length > 0) ? `(पुरुष: ${maleCount} | महिला: ${femaleCount})` : '';
    }
    if (elements.paperA4FooterPage) {
        elements.paperA4FooterPage.innerText = `पृष्ठ संख्या: ${curPageNum} / ${totalA4Pages}`;
    }

    lucide.createIcons();
}

// Right-Click Context Menu for Voter Cards
function handleCardContextMenu(e, jobIndex, record) {
    e.preventDefault();
    state.activeContextIndex = jobIndex;
    state.activeContextRecord = record;

    const menu = elements.voterContextMenu;
    if (!menu) return;

    if (elements.ctxToggleDeletedText) {
        elements.ctxToggleDeletedText.innerText = record.is_deleted ? '✓ सक्रिय चिह्नित करें (Mark Active)' : '🚫 विलोपित चिह्नित करें (Mark Deleted)';
    }

    menu.style.display = 'block';

    const menuWidth = 220;
    const menuHeight = 180;
    let posX = e.clientX;
    let posY = e.clientY;

    if (posX + menuWidth > window.innerWidth) posX = window.innerWidth - menuWidth - 10;
    if (posY + menuHeight > window.innerHeight) posY = window.innerHeight - menuHeight - 10;

    menu.style.left = `${posX}px`;
    menu.style.top = `${posY}px`;
    lucide.createIcons();
}

function hideCardContextMenu() {
    if (elements.voterContextMenu) {
        elements.voterContextMenu.style.display = 'none';
    }
}

// Toggle Deleted status of voter
async function handleToggleDeleted(jobIndex) {
    if (!state.currentJobId) return;
    try {
        const res = await fetch(`/api/toggle-deleted/${state.currentJobId}/${jobIndex}`, {
            method: 'POST'
        });
        const data = await res.json();
        if (res.ok) {
            showToast(data.message, 'info');
            await fetchPreview();
        } else {
            showToast(data.detail || 'त्रुटि हुई।', 'error');
        }
    } catch (err) {
        showToast('सर्वर से संपर्क करने में विफल।', 'error');
    }
}

// Delete scanned voter completely from list
async function handleDeleteScannedVoter(jobIndex, voterName) {
    if (!state.currentJobId) return;
    showDeleteConfirmModal({
        title: 'स्कैन सूची से मतदाता हटाएं',
        subtitle: `मतदाता: ${voterName || 'अनाम'}`,
        msg: `क्या आप वाकई मतदाता <strong>${escapeHtml(voterName || '')}</strong> को स्कैन सूची से हटाना चाहते हैं?`,
        warningText: 'यह रिकॉर्ड इस स्कैन सेशन और एक्सेल फ़ाइल से मिट जाएगा।',
        confirmBtnText: 'हाँ, हटाएं',
        onConfirm: async () => {
            try {
                const res = await fetch(`/api/delete-record/${state.currentJobId}/${jobIndex}`, {
                    method: 'DELETE'
                });
                const data = await res.json();
                if (res.ok) {
                    showToast(data.message || 'मतदाता हटा दिया गया।', 'success');
                    await fetchPreview();
                } else {
                    showToast(data.detail || 'हटाने में विफल।', 'error');
                }
            } catch (err) {
                showToast('सर्वर से संपर्क करने में विफल।', 'error');
            }
        }
    });
}

// Render Stats
function renderStats(stats) {
    if (!stats) return;
    elements.statTotalVoters.innerText = stats.total_voters || 0;
    elements.statMaleVoters.innerText = stats.total_males || 0;
    elements.statFemaleVoters.innerText = stats.total_females || 0;
    elements.statGenderRatio.innerText = stats.gender_ratio ? `${stats.gender_ratio}` : '0';
    elements.statAvgAge.innerText = stats.avg_age ? `${stats.avg_age} वर्ष` : '--';
    if (elements.statMuslimVoters) {
        elements.statMuslimVoters.innerText = stats.muslim_voters ? `${stats.muslim_voters} (${stats.muslim_percentage || 0}%)` : '0';
    }
    if (elements.statHinduVoters) {
        const hinduCount = stats.hindu_voters ?? stats.non_muslim_voters ?? 0;
        const hinduPct = stats.hindu_percentage ?? (stats.total_voters ? ((hinduCount / stats.total_voters) * 100).toFixed(1) : 0);
        elements.statHinduVoters.innerText = hinduCount ? `${hinduCount} (${hinduPct}%)` : '0';
    }
    if (elements.statCardDeleted && elements.statDeletedVoters) {
        if (stats.total_deleted > 0) {
            elements.statDeletedVoters.innerText = stats.total_deleted;
            elements.statCardDeleted.style.display = 'flex';
        } else {
            elements.statCardDeleted.style.display = 'none';
        }
    }
}

// Render Table Rows
function renderTable(records) {
    elements.voterTableBody.innerHTML = '';

    if (!records || records.length === 0) {
        const hasFilters = Boolean(state.searchQuery || (state.genderFilter && state.genderFilter !== 'all') || state.warningsOnly);
        elements.voterTableBody.innerHTML = `
            <tr>
                <td colspan="12">
                    <div class="empty-state-wrap">
                        <div class="empty-state-icon-box">
                            <i data-lucide="user-x"></i>
                        </div>
                        <h4 class="empty-state-title">कोई मतदाता रिकॉर्ड नहीं मिला</h4>
                        <p class="empty-state-desc">
                            ${hasFilters 
                                ? 'आपके द्वारा लगाए गए खोज या फ़िल्टर मापदंडों से कोई मतदाता नहीं मिला। कृपया अपने फ़िल्टर रीसेट करें।'
                                : 'इस पृष्ठ या फ़ाइल में कोई मतदाता रिकॉर्ड उपलब्ध नहीं है।'}
                        </p>
                        <div class="empty-state-actions">
                            ${hasFilters ? `
                                <button class="empty-state-btn-primary" onclick="resetPreviewFilters()">
                                    <i data-lucide="rotate-ccw"></i> फ़िल्टर रीसेट करें
                                </button>
                            ` : ''}
                        </div>
                    </div>
                </td>
            </tr>
        `;
        if (window.lucide) lucide.createIcons();
        return;
    }

    records.forEach((r, idx) => {
        const tr = document.createElement('tr');
        if (r.has_warning) tr.classList.add('row-warning');
        if (r.is_deleted) tr.classList.add('row-deleted');

        const genderClass = r.gender === 'महिला' ? 'gender-badge-female' : 'gender-badge-male';
        const statusHtml = r.is_deleted 
            ? `<span class="badge-deleted">[विलोपित]</span>` 
            : (r.has_warning 
                ? `<span class="status-badge-warn" title="${r.warning_message}"><i data-lucide="alert-triangle" style="width:14px;height:14px;"></i> जाँचें</span>`
                : `<span class="status-badge-ok"><i data-lucide="check" style="width:14px;height:14px;"></i> सही</span>`);

        const targetIdx = (r.job_index !== undefined) ? r.job_index : ((state.currentPage - 1) * state.pageSize + idx);
        const isOp = isOperatorUser();
        const muslimBadge = (!isOp && r.is_muslim) ? `<span class="badge-muslim" title="${escapeHtml(r.muslim_reason || 'मुस्लिम समुदाय')}">☪️ मुस्लिम</span>` : '';
        const nameClass = r.is_deleted ? 'name-deleted' : '';

        const isEpicDefective = !r.epic_no || (r.warning_message && r.warning_message.includes('EPIC')) || !/^(?:[A-Z]{3}\d{7}|(?:UP|[A-Z]{2})\/\d{1,3}\/\d{1,4}\/\d{3,8})$/i.test((r.epic_no || '').trim());
        const epicDisplay = r.epic_no
            ? (isEpicDefective
                ? `<span class="epic-badge epic-badge-warn" title="${escapeHtml(r.warning_message || 'EPIC अमान्य/संदिग्ध प्रारूप - मैन्युअल जांच अपेक्षित')}">⚠️ ${escapeHtml(r.epic_no)}</span>`
                : `<span class="epic-badge">${escapeHtml(r.epic_no)}</span>`)
            : `<span class="epic-badge epic-badge-warn" title="EPIC अनुपलब्ध / रिक्त">⚠️ रिक्त</span>`;

        tr.innerHTML = `
            <td><span class="serial-badge">${r.serial_no}</span></td>
            <td><strong class="${nameClass}">${escapeHtml(r.name)}</strong> ${muslimBadge}</td>
            <td><span class="tag" style="padding: 2px 8px; font-size: 0.75rem;">${escapeHtml(r.relation_type || 'पिता')}</span></td>
            <td>${escapeHtml(r.relation_name || '--')}</td>
            <td style="text-align: center;">${escapeHtml(r.house_no || '--')}</td>
            <td style="text-align: center;">${r.age || '--'}</td>
            <td style="text-align: center;"><span class="${genderClass}">${r.gender}</span></td>
            <td style="text-align: center;">${epicDisplay}</td>
            <td style="max-width: 220px; font-size: 0.8rem; line-height: 1.2;" title="${escapeHtml(r.polling_station || '')}">${escapeHtml(r.polling_station || '--')}</td>
            <td style="text-align: center;">${r.page_no}</td>
            <td>${statusHtml}</td>
            <td>
                <button class="edit-row-btn" onclick="openEditModal(${targetIdx})">
                    <i data-lucide="edit-2" style="width: 12px; height: 12px;"></i> सुधारें
                </button>
            </td>
        `;

        tr.addEventListener('contextmenu', (e) => {
            handleCardContextMenu(e, targetIdx, r);
        });

        elements.voterTableBody.appendChild(tr);
    });

    lucide.createIcons();
}

// Render Pagination Controls
function renderPagination() {
    const start = state.totalFiltered === 0 ? 0 : (state.currentPage - 1) * state.pageSize + 1;
    const end = Math.min(state.currentPage * state.pageSize, state.totalFiltered);

    const infoText = i18n[state.lang].showingInfo
        .replace('{start}', start)
        .replace('{end}', end)
        .replace('{total}', state.totalFiltered);

    elements.paginationInfo.innerText = infoText;

    const pageText = i18n[state.lang].pageInfo
        .replace('{current}', state.currentPage)
        .replace('{total}', Math.max(1, state.totalPages));

    elements.pageIndicator.innerText = pageText;

    elements.prevPageBtn.disabled = state.currentPage <= 1;
    elements.nextPageBtn.disabled = state.currentPage >= state.totalPages;
}

// Download Excel Handler
function handleDownloadExcel() {
    if (!state.currentJobId) return;
    window.location.href = `/api/download/${state.currentJobId}`;
}

// Reset UI
function resetToUpload() {
    stopScanTimer();
    if (state.pollInterval) clearInterval(state.pollInterval);
    state.currentJobId = null;
    state.records = [];
    state.availablePages = [];
    state.selectedPdfPage = null;
    state.searchQuery = '';
    state.genderFilter = 'all';
    state.warningsOnly = false;
    elements.fileInput.value = '';
    elements.searchInput.value = '';
    elements.genderFilter.value = 'all';
    elements.warningsOnlyCheck.checked = false;

    elements.progressSection.style.display = 'none';
    elements.resultsSection.style.display = 'none';
    elements.uploadSection.style.display = 'block';
    document.body.classList.add('initial-state');
    document.body.classList.add('scanning-active');
}

// Modal Edit & Add for PDF Scanned List
window.openEditModal = function(recordIndex) {
    let record = state.records.find(r => r.job_index === recordIndex);
    if (!record && state.records[recordIndex]) {
        record = state.records[recordIndex];
    }
    if (!record) return;

    document.getElementById('editRecordIndex').value = (record.job_index !== undefined) ? record.job_index : recordIndex;
    document.getElementById('editSerial').value = record.serial_no;
    document.getElementById('editEpic').value = record.epic_no || '';
    document.getElementById('editName').value = record.name || '';
    document.getElementById('editRelType').value = record.relation_type || 'पिता';
    document.getElementById('editRelName').value = record.relation_name || '';
    document.getElementById('editHouse').value = record.house_no || '';
    document.getElementById('editAge').value = record.age || '';
    document.getElementById('editGender').value = record.gender || 'पुरुष';

    const statusEl = document.getElementById('editStatus');
    const reasonEl = document.getElementById('editDeletedReason');
    const pageEl = document.getElementById('editPageNo');
    if (statusEl) statusEl.value = record.is_deleted ? 'deleted' : 'active';
    if (reasonEl) reasonEl.value = record.deleted_reason || '';
    if (pageEl) pageEl.value = record.page_no || state.selectedPdfPage || 1;

    const titleEl = document.getElementById('editModalTitle');
    if (titleEl) titleEl.textContent = 'मतदाता विवरण सुधारें (Edit Voter)';
    const btnTextEl = document.getElementById('editSaveBtnText');
    if (btnTextEl) btnTextEl.textContent = 'सुरक्षित करें';

    elements.editModal.style.display = 'flex';
    lucide.createIcons();
};

window.openAddPreviewVoterModal = function(suggestedSerial, suggestedPage) {
    if (!state.currentJobId) {
        showToast('कृपया पहले कोई PDF फ़ाइल प्रोसेस करें।', 'warning');
        return;
    }

    let nextSerial = 1;
    if (suggestedSerial) {
        nextSerial = suggestedSerial;
    } else if (state.records && state.records.length > 0) {
        const serials = state.records.map(r => parseInt(r.serial_no, 10)).filter(n => !isNaN(n));
        if (serials.length > 0) {
            nextSerial = Math.max(...serials) + 1;
        }
    }

    const targetPage = suggestedPage || state.selectedPdfPage || (state.records[0] ? state.records[0].page_no : 1);

    document.getElementById('editRecordIndex').value = '-1';
    document.getElementById('editSerial').value = nextSerial;
    document.getElementById('editEpic').value = '';
    document.getElementById('editName').value = '';
    document.getElementById('editRelType').value = 'पिता';
    document.getElementById('editRelName').value = '';
    document.getElementById('editHouse').value = '';
    document.getElementById('editAge').value = '';
    document.getElementById('editGender').value = 'पुरुष';

    const statusEl = document.getElementById('editStatus');
    const reasonEl = document.getElementById('editDeletedReason');
    const pageEl = document.getElementById('editPageNo');
    if (statusEl) statusEl.value = 'active';
    if (reasonEl) reasonEl.value = '';
    if (pageEl) pageEl.value = targetPage;

    const titleEl = document.getElementById('editModalTitle');
    if (titleEl) titleEl.textContent = '+ नया मतदाता जोड़ें (Add Voter to Scanned List)';
    const btnTextEl = document.getElementById('editSaveBtnText');
    if (btnTextEl) btnTextEl.textContent = '+ मतदाता जोड़ें (Add)';

    elements.editModal.style.display = 'flex';
    lucide.createIcons();
    document.getElementById('editName').focus();
};

function closeModal() {
    elements.editModal.style.display = 'none';
}

async function handleSaveRecord(e) {
    e.preventDefault();
    const recordIndex = parseInt(document.getElementById('editRecordIndex').value, 10);
    const isAdd = (recordIndex === -1);
    
    const isDeleted = document.getElementById('editStatus') ? (document.getElementById('editStatus').value === 'deleted') : false;
    const deletedReason = document.getElementById('editDeletedReason') ? document.getElementById('editDeletedReason').value.trim() : '';
    const pageNo = document.getElementById('editPageNo') ? (parseInt(document.getElementById('editPageNo').value, 10) || null) : null;

    const updatedData = {
        serial_no: parseInt(document.getElementById('editSerial').value, 10) || null,
        epic_no: document.getElementById('editEpic').value.trim().toUpperCase(),
        name: document.getElementById('editName').value.trim(),
        relation_type: document.getElementById('editRelType').value,
        relation_name: document.getElementById('editRelName').value.trim(),
        house_no: document.getElementById('editHouse').value.trim(),
        age: document.getElementById('editAge').value ? parseInt(document.getElementById('editAge').value, 10) : null,
        gender: document.getElementById('editGender').value,
        page_no: pageNo,
        is_deleted: isDeleted,
        deleted_reason: deletedReason
    };

    if (!updatedData.name) {
        showToast('कृपया मतदाता का नाम दर्ज करें।', 'warning');
        return;
    }

    try {
        let res;
        if (isAdd) {
            res = await fetch(`/api/add-record/${state.currentJobId}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(updatedData)
            });
        } else {
            res = await fetch(`/api/update-record/${state.currentJobId}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    record_index: recordIndex,
                    updated_data: updatedData
                })
            });
        }

        const resData = await res.json().catch(() => ({}));
        if (!res.ok) {
            throw new Error(resData.detail || (isAdd ? 'जोड़ने में त्रुटि हुई' : 'Save failed'));
        }

        closeModal();
        showToast(isAdd ? resData.message || 'नया मतदाता स्कैन सूची में सफलतापूर्वक जोड़ा गया।' : i18n[state.lang].toastSaveSuccess, 'success');
        
        if (isAdd && resData.target_page) {
            state.selectedPdfPage = resData.target_page;
        }

        await fetchPreview();

        // Animate pulse on target card
        const targetSerial = updatedData.serial_no;
        if (targetSerial) {
            setTimeout(() => {
                const card = document.querySelector(`.voter-paper-card[data-serial="${targetSerial}"]`);
                if (card) {
                    card.classList.add('card-pulse-new');
                    card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
                }
            }, 300);
        }

    } catch (err) {
        showToast(i18n[state.lang].toastError + err.message, 'error');
    }
}

// Language Toggle
function toggleLanguage() {
    state.lang = state.lang === 'hi' ? 'en' : 'hi';
    if (elements.currentLangLabel) {
        elements.currentLangLabel.innerText = i18n[state.lang].langToggle;
    }
    
    // Update static texts
    checkEngineHealth();
    renderPagination();
}

// Toast Notification
function showToast(message, type = 'info') {
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    
    const icon = type === 'success' ? 'check-circle' : (type === 'error' ? 'alert-circle' : 'info');
    toast.innerHTML = `<i data-lucide="${icon}"></i> <span>${escapeHtml(message)}</span>`;
    
    elements.toastContainer.appendChild(toast);
    lucide.createIcons();

    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateY(10px)';
        setTimeout(() => toast.remove(), 300);
    }, 3500);
}

// Helper: Escape HTML
function escapeHtml(str) {
    if (!str) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

// ==========================================================================
// Master Database & Tab Switching Methods
// ==========================================================================

function switchTab(tabName) {
    if (isOperatorUser() && (tabName === 'users' || tabName === 'audit' || tabName === 'pending-edits')) {
        tabName = 'dashboard';
    }
    // PER USER REQUIREMENT:
    // 'लेकिन इस user को कभी भी नगर पंचायत के गलीबार मकान के आधार पर मैच न दिखे , ये केवल harshsamrat सुपर एडमिन को ही दिखे'
    if (tabName === 'street-audit' && !isSuperAdminUser()) {
        tabName = 'dashboard';
    }

    // All tab buttons and views
    const tabBtns = ['tabDashboardBtn', 'tabConverterBtn', 'tabDatabaseBtn', 'tabUsersBtn', 'tabAuditBtn', 'tabPendingEditsBtn', 'tabStreetAuditBtn'];
    const viewIds = ['dashboardView', 'converterView', 'databaseView', 'usersView', 'auditView', 'pendingEditsView', 'streetAuditView'];
    const tabMap = { dashboard: 0, converter: 1, database: 2, users: 3, audit: 4, 'pending-edits': 5, 'street-audit': 6 };

    const idx = tabMap[tabName];
    if (idx === undefined) return;

    // Deactivate all tabs, hide all views
    tabBtns.forEach(id => document.getElementById(id)?.classList.remove('active'));
    viewIds.forEach(id => {
        const el = document.getElementById(id);
        if (el) el.style.display = 'none';
    });

    // Activate selected tab and show view
    document.getElementById(tabBtns[idx])?.classList.add('active');
    const activeView = document.getElementById(viewIds[idx]);
    if (activeView) {
        activeView.style.display = 'block';
        // Re-trigger animation
        activeView.style.animation = 'none';
        activeView.offsetHeight; // force reflow
        activeView.style.animation = 'fadeInUp 0.35s ease';
    }

    // Tab-specific scroll and data loading
    if (tabName === 'converter') {
        // Enforce zero-scroll if results are not currently visible
        if (elements.resultsSection?.style.display === 'none' || !elements.resultsSection?.style.display) {
            document.body.classList.add('initial-state');
            document.body.classList.add('scanning-active');
        } else {
            document.body.classList.remove('initial-state');
            document.body.classList.remove('scanning-active');
        }
    } else {
        document.body.classList.remove('initial-state');
        document.body.classList.remove('scanning-active');
    }

    if (tabName === 'database') {
        const isOp = isOperatorUser();
        const dbPriv = document.getElementById('dbPrivacyControlBtn');
        if (dbPriv) dbPriv.style.display = isOp ? 'none' : 'inline-flex';
        const dbRecomp = document.getElementById('dbRecomputeCastesBtn');
        if (dbRecomp) dbRecomp.style.display = isOp ? 'none' : 'inline-flex';
        fetchDbStats();
        if (!isOp) {
            loadCasteAnalytics();
        }
        fetchDbRecords();
        loadPartsForBulkUpdate(); // Ensures bulk update parts dropdown is populated
    } else if (tabName === 'users') {
        loadUsersList();
    } else if (tabName === 'dashboard') {
        loadDashboardData();
    } else if (tabName === 'audit') {
        loadAuditLog();
    } else if (tabName === 'pending-edits') {
        loadPendingEdits();
    } else if (tabName === 'street-audit') {
        initStreetAuditModule();
    }
    lucide.createIcons();
}

// Save active conversion job to master database
async function handleSaveToDatabase() {
    if (!state.currentJobId) {
        showToast('सेव करने के लिए कोई सक्रिय जॉब उपलब्ध नहीं है।', 'error');
        return;
    }

    try {
        elements.saveDbBtn.disabled = true;
        elements.saveDbBtn.innerHTML = `<i data-lucide="loader-2" class="spin"></i> <span>डेटाबेस में सुरक्षित हो रहा है...</span>`;
        lucide.createIcons();

        const res = await adminFetch(`/api/database/save-job/${state.currentJobId}`, {
            method: 'POST'
        });
        const data = await res.json();

        if (!res.ok) {
            throw new Error(data.detail || 'डेटाबेस में सेव करने में विफल।');
        }

        showToast(data.message || 'मतदाता डेटाबेस में सुरक्षित हो गया!', 'success');
        fetchDbStats();

    } catch (err) {
        showToast('त्रुटि: ' + err.message, 'error');
    } finally {
        elements.saveDbBtn.disabled = false;
        elements.saveDbBtn.innerHTML = `<i data-lucide="database"></i> <span>डेटाबेस में सुरक्षित करें</span>`;
        lucide.createIcons();
    }
}

// Fetch database metrics & stats
async function fetchDbStats() {
    try {
        const dbParam = dbState.selectedDbId ? `?db_id=${encodeURIComponent(dbState.selectedDbId)}` : '';
        const res = await fetch(`/api/database/stats${dbParam}`);
        if (!res.ok) return;
        const stats = await res.json();

        if (elements.navDbCountBadge) {
            elements.navDbCountBadge.innerText = stats.total_voters.toLocaleString('hi-IN');
        }
        if (elements.dbStatTotal) {
            elements.dbStatTotal.innerText = stats.total_voters.toLocaleString('hi-IN');
        }
        if (elements.dbStatMale) {
            elements.dbStatMale.innerText = stats.male_voters.toLocaleString('hi-IN');
        }
        if (elements.dbStatFemale) {
            elements.dbStatFemale.innerText = stats.female_voters.toLocaleString('hi-IN');
        }
        if (elements.dbStatParts) {
            elements.dbStatParts.innerText = stats.total_parts.toLocaleString('hi-IN');
        }
        if (elements.dbStatAssemblies) {
            elements.dbStatAssemblies.innerText = stats.total_assemblies.toLocaleString('hi-IN');
        }
        if (elements.dbStatHindu) {
            const hinduCount = stats.hindu_voters ?? stats.non_muslim_voters ?? 0;
            const hinduPct = stats.hindu_percentage ?? (stats.total_voters ? ((hinduCount / stats.total_voters) * 100).toFixed(1) : 0);
            elements.dbStatHindu.innerText = hinduCount ? `${hinduCount.toLocaleString('hi-IN')} (${hinduPct}%)` : '0';
        }
        if (elements.dbStatMuslim) {
            elements.dbStatMuslim.innerText = stats.muslim_voters ? `${stats.muslim_voters.toLocaleString('hi-IN')} (${stats.muslim_percentage}%)` : '0';
        }
        if (elements.dbStatDeleted) {
            elements.dbStatDeleted.innerText = (stats.deleted_voters || 0).toLocaleString('hi-IN');
        }
    } catch (err) {
        console.warn('Failed to fetch DB stats:', err);
    }
}

// Fetch searched database records
async function fetchDbRecords() {
    try {
        const params = new URLSearchParams();
        params.append('page', dbState.page);
        params.append('limit', dbState.pageSize);
        if (dbState.selectedDbId) params.append('db_id', dbState.selectedDbId);

        const q = elements.dbQueryInput.value.trim();
        if (q) params.append('q', q);

        const name = elements.dbNameInput.value.trim();
        if (name) params.append('name', name);

        const relName = elements.dbRelNameInput.value.trim();
        if (relName) params.append('relation_name', relName);

        const epic = elements.dbEpicInput.value.trim();
        if (epic) params.append('epic_no', epic);

        const part = elements.dbPartInput.value.trim();
        if (part) params.append('part_no', part);

        const gender = elements.dbGenderSelect.value;
        if (gender && gender !== 'all') params.append('gender', gender);

        const house = elements.dbHouseInput.value.trim();
        if (house) params.append('house_no', house);

        const minAge = elements.dbMinAgeInput.value.trim();
        if (minAge) params.append('min_age', minAge);

        const maxAge = elements.dbMaxAgeInput.value.trim();
        if (maxAge) params.append('max_age', maxAge);

        if (elements.dbMuslimSelect) {
            const mVal = elements.dbMuslimSelect.value;
            if (mVal && mVal !== 'all') params.append('muslim', mVal);
        }

        if (elements.dbCasteSelect) {
            const cVal = elements.dbCasteSelect.value;
            if (cVal && cVal !== 'all') params.append('caste_key', cVal);
        }

        if (elements.dbStatusSelect) {
            const sVal = elements.dbStatusSelect.value;
            if (sVal && sVal !== 'all') params.append('status', sVal);
        }

        elements.dbResultsSummaryText.innerText = 'खोज जारी है...';
        if (dbState.viewMode === 'paper') {
            if (elements.dbElectoralRollGrid) renderPaperGridSkeleton(elements.dbElectoralRollGrid, 30);
        } else {
            if (elements.dbVoterTableBody) renderTableSkeleton(elements.dbVoterTableBody, 15, 6);
        }

        const res = await fetch(`/api/database/search?${params.toString()}`);
        if (!res.ok) throw new Error('खोज विफल रही।');
        const data = await res.json();

        dbState.records = data.records || [];
        dbState.totalFiltered = data.total_filtered || 0;
        dbState.activeFiltered = data.active_filtered !== undefined ? data.active_filtered : (data.total_filtered || 0);
        dbState.deletedFiltered = data.deleted_filtered || 0;
        dbState.totalRecords = data.total_records || 0;
        dbState.totalPages = data.total_pages || 1;

        refreshActiveDbView();
        renderDbPagination();

    } catch (err) {
        showToast('डेटाबेस खोज त्रुटि: ' + err.message, 'error');
        elements.dbResultsSummaryText.innerText = 'खोज विफल।';
    }
}

// Switch Database View Mode (Table View vs Paper Match View)
function switchDbView(mode) {
    dbState.viewMode = mode;
    if (mode === 'paper') {
        if (elements.dbViewModePaperBtn) elements.dbViewModePaperBtn.classList.add('active');
        if (elements.dbViewModeTableBtn) elements.dbViewModeTableBtn.classList.remove('active');
        if (elements.dbPaperViewWrap) elements.dbPaperViewWrap.style.display = 'block';
        if (elements.dbTableViewWrap) elements.dbTableViewWrap.style.display = 'none';
        // In paper roll view, lock page size to exactly 30 voters per A4 sheet
        if (dbState.pageSize !== 30) {
            dbState.pageSize = 30;
            if (elements.dbPageSizeSelect) elements.dbPageSizeSelect.value = "30";
            dbState.page = 1;
            fetchDbRecords();
            return;
        }
        renderDbPaperGrid();
    } else {
        if (elements.dbViewModeTableBtn) elements.dbViewModeTableBtn.classList.add('active');
        if (elements.dbViewModePaperBtn) elements.dbViewModePaperBtn.classList.remove('active');
        if (elements.dbTableViewWrap) elements.dbTableViewWrap.style.display = 'block';
        if (elements.dbPaperViewWrap) elements.dbPaperViewWrap.style.display = 'none';
        renderDbTable();
    }
}

// Refresh whatever view mode is currently active
function refreshActiveDbView() {
    if (dbState.viewMode === 'paper') {
        renderDbPaperGrid();
    } else {
        renderDbTable();
    }
}

// Render database records in Electoral Roll Paper Grid (3-column cards)
function renderDbPaperGrid() {
    const grid = elements.dbElectoralRollGrid;
    if (!grid) return;
    grid.innerHTML = '';

    if (dbState.records.length === 0) {
        grid.innerHTML = `
            <div style="grid-column: 1 / -1;">
                <div class="empty-state-wrap">
                    <div class="empty-state-icon-box">
                        <i data-lucide="inbox"></i>
                    </div>
                    <h4 class="empty-state-title">डेटाबेस में कोई मतदाता रिकॉर्ड नहीं मिला</h4>
                    <p class="empty-state-desc">
                        कृपया अपने खोज शब्द बदलें या इस डेटाबेस में नई मतदाता सूची PDF अपलोड करें।
                    </p>
                    <div class="empty-state-actions">
                        <button class="empty-state-btn-primary" onclick="resetDbFilters()">
                            <i data-lucide="rotate-ccw"></i> खोज फ़िल्टर साफ़ करें
                        </button>
                        <button class="empty-state-btn-secondary" onclick="switchTab('converter')">
                            <i data-lucide="file-up"></i> PDF अपलोड करें
                        </button>
                    </div>
                </div>
            </div>
        `;
        if (elements.dbPaperA4Count) elements.dbPaperA4Count.innerText = '0';
        if (elements.dbPaperA4GenderBreakdown) elements.dbPaperA4GenderBreakdown.innerText = '';
        if (elements.dbPaperA4SerialRange) elements.dbPaperA4SerialRange.innerText = '--';
        if (elements.dbPaperPageSummaryBadge) elements.dbPaperPageSummaryBadge.innerText = 'इस A4 पृष्ठ पर: 0 मतदाता';
        if (elements.dbPaperBottomInfo) elements.dbPaperBottomInfo.innerText = 'A4 पृष्ठ 1 / 1';
        if (elements.dbPaperPrevBtn) elements.dbPaperPrevBtn.disabled = true;
        if (elements.dbPaperNextBtn) elements.dbPaperNextBtn.disabled = true;
        if (elements.dbPaperBottomPrevBtn) elements.dbPaperBottomPrevBtn.disabled = true;
        if (elements.dbPaperBottomNextBtn) elements.dbPaperBottomNextBtn.disabled = true;
        if (window.lucide) lucide.createIcons();
        elements.dbResultsSummaryText.innerText = 'कुल 0 मतदाता मिले';
        updateSelectionUI();
        return;
    }

    if (dbState.deletedFiltered > 0 && dbState.activeFiltered > 0) {
        elements.dbResultsSummaryText.innerText = `कुल ${dbState.activeFiltered.toLocaleString('hi-IN')} सक्रिय (+ ${dbState.deletedFiltered.toLocaleString('hi-IN')} विलोपित) मतदाता मिले (सक्रिय डेटाबेस: ${dbState.totalRecords.toLocaleString('hi-IN')})`;
    } else if (dbState.deletedFiltered > 0) {
        elements.dbResultsSummaryText.innerText = `कुल ${dbState.deletedFiltered.toLocaleString('hi-IN')} विलोपित मतदाता मिले (सक्रिय डेटाबेस: ${dbState.totalRecords.toLocaleString('hi-IN')})`;
    } else {
        elements.dbResultsSummaryText.innerText = `कुल ${dbState.totalFiltered.toLocaleString('hi-IN')} मतदाता मिले (सक्रिय डेटाबेस: ${dbState.totalRecords.toLocaleString('hi-IN')})`;
    }

    dbState.records.forEach(v => {
        const card = document.createElement('div');
        const isSelected = selectedVoterIds.has(v.id);
        const isDeleted = Boolean(v.is_deleted);

        card.className = `voter-paper-card ${isSelected ? 'row-selected' : ''} ${isDeleted ? 'is-deleted' : ''}`;
        card.setAttribute('data-id', v.id);

        const isOp = isOperatorUser();
        const genderClass = v.gender === 'महिला' ? 'gender-badge-female' : 'gender-badge-male';
        const muslimBadge = (!isOp && v.is_muslim) ? `<span class="badge-muslim" title="${escapeHtml(v.muslim_reason || 'मुस्लिम')}">☪️ मुस्लिम</span>` : '';
        
        let casteBadge = '';
        if (!isOp && !v.is_muslim && v.caste_key && v.caste_key !== 'muslim' && CASTE_LABELS[v.caste_key]) {
            const exp = getCasteExplanation(v);
            const cLabel = CASTE_LABELS[v.caste_key];
            const isAi = v.caste_source === 'household_ai';
            const isLineage = v.caste_source === 'family_lineage_ai';
            const aiIcon = isAi ? ' 🏠' : (isLineage ? ' 👨‍👩‍👧' : '');
            const title = exp ? `${exp.casteLabel} (${exp.category}) - ${exp.methodTitle.replace(/[^\w\s\u0900-\u097F]/g, '')}` : cLabel;
            const infoAttr = exp ? `data-caste-info="${encodeURIComponent(JSON.stringify(exp))}"` : '';
            casteBadge = `<span class="badge-caste ${isAi ? 'badge-caste-ai' : ''}" ${infoAttr} title="${escapeHtml(title)}">${escapeHtml(cLabel)}${aiIcon}</span>`;
        }

        const deletedStampHtml = isDeleted 
            ? `<div class="stamp-deleted">🚫 विलोपित / DELETED</div>` 
            : '';

        const pendingBadge = v.is_pending_approval
            ? `<span class="badge-pending-approval" title="यह बदलाव ऑपरेटर द्वारा प्रस्तावित है और एडमिन अनुमोदन के लिए लंबित है">⏳ अनुमोदन लंबित</span>`
            : '';

        const isEpicDefective = !v.epic_no || (v.warning_message && v.warning_message.includes('EPIC')) || !/^(?:[A-Z]{3}\d{7}|(?:UP|[A-Z]{2})\/\d{1,3}\/\d{1,4}\/\d{3,8})$/i.test((v.epic_no || '').trim());
        const epicHtml = isEpicDefective 
            ? `<span class="vpc-epic vpc-epic-warn font-mono" title="${escapeHtml(v.warning_message || 'EPIC अमान्य/संदिग्ध प्रारूप - मैन्युअल जांच अपेक्षित')}">⚠️ ${escapeHtml(v.epic_no || 'अमान्य')}</span>` 
            : `<span class="vpc-epic font-mono">${escapeHtml(v.epic_no || '--')}</span>`;

        card.innerHTML = `
            ${deletedStampHtml}
            <div class="vpc-header">
                <div class="vpc-card-select-wrap">
                    <input type="checkbox" class="db-row-checkbox" data-id="${v.id}" ${isSelected ? 'checked' : ''} title="मतदाता ID #${v.id} चुनें" onclick="event.stopPropagation();">
                    <span class="vpc-serial">${v.serial_no || '--'}</span>
                </div>
                ${epicHtml}
            </div>
            <div class="vpc-body">
                <div class="vpc-details">
                    <div class="vpc-row">
                        <span class="vpc-label">नाम:</span>
                        <span class="vpc-val vpc-name-val ${isDeleted ? 'name-deleted' : ''}">${escapeHtml(v.name || '--')}</span>
                    </div>
                    <div class="vpc-row">
                        <span class="vpc-label">${escapeHtml(v.relation_type || 'पिता')}:</span>
                        <span class="vpc-val">${escapeHtml(v.relation_name || '--')}</span>
                    </div>
                    <div class="vpc-row">
                        <span class="vpc-label">मकान नं०:</span>
                        <span class="vpc-val font-mono">${escapeHtml(v.house_no || '--')}</span>
                    </div>
                    <div class="vpc-sub-row">
                        <div class="vpc-row">
                            <span class="vpc-label">आयु:</span>
                            <span class="vpc-val">${v.age ? v.age + ' वर्ष' : '--'}</span>
                        </div>
                        <div class="vpc-row">
                            <span class="vpc-label">लिंग:</span>
                            <span class="vpc-val"><span class="${genderClass}" style="padding: 1px 6px; font-size: 0.72rem;">${escapeHtml(v.gender || '--')}</span></span>
                        </div>
                    </div>
                </div>
                <div class="vpc-photo-box" title="सरकारी निर्वाचक नामावली फोटो बॉक्स">
                    <i data-lucide="user" class="vpc-photo-icon"></i>
                    <span class="vpc-photo-text">फोटो<br>उपलब्ध है</span>
                </div>
            </div>
            <div class="vpc-footer" style="display: flex; align-items: center; justify-content: space-between; gap: 4px; padding-top: 4px; border-top: 1px dashed #CBD5E1; margin-top: 4px;">
                <div class="vpc-tags-group" style="display: flex; align-items: center; gap: 4px; flex-wrap: wrap;">
                    <span class="tag tag-sm" title="भाग संख्या" style="font-size: 0.7rem; padding: 1px 5px;">भाग ${escapeHtml(v.part_no || '--')}</span>
                    ${muslimBadge}
                    ${casteBadge}
                    ${pendingBadge}
                </div>
                <div class="vpc-db-actions" onclick="event.stopPropagation();">
                    <button type="button" title="🖨️ मतदाता सूचना पर्ची A4 प्रिंट" onclick="window.printSingleVoterSlip(${v.id})">
                        <i data-lucide="printer" style="width: 12px; height: 12px;"></i>
                    </button>
                    <button type="button" title="📱 पर्ची शेयर" onclick="window.showDbVoterSlip(${v.id})">
                        <i data-lucide="share-2" style="width: 12px; height: 12px;"></i>
                    </button>
                    <button type="button" title="विवरण सुधारें" onclick="window.openDbEditModal(${v.id})">
                        <i data-lucide="edit-3" style="width: 12px; height: 12px;"></i>
                    </button>
                    <button type="button" class="btn-card-del" title="रिकॉर्ड हटाएं" onclick="window.deleteDbVoter(${v.id})">
                        <i data-lucide="trash-2" style="width: 12px; height: 12px;"></i>
                    </button>
                </div>
            </div>
        `;

        // Double-click to open edit modal
        card.addEventListener('dblclick', (e) => {
            e.stopPropagation();
            if (typeof window.openDbEditModal === 'function') {
                window.openDbEditModal(v.id);
            }
        });

        // Click to toggle checkbox selection if clicked outside buttons/inputs
        card.addEventListener('click', (e) => {
            if (e.target.closest('button') || e.target.closest('input') || e.target.closest('.badge-caste')) return;
            const cb = card.querySelector('.db-row-checkbox');
            if (cb) {
                cb.checked = !cb.checked;
                cb.dispatchEvent(new Event('change'));
            }
        });

        grid.appendChild(card);
    });

    // Bind individual card checkbox changes
    grid.querySelectorAll('.db-row-checkbox').forEach(cb => {
        cb.addEventListener('change', (e) => {
            const id = parseInt(e.target.dataset.id);
            const card = e.target.closest('.voter-paper-card');
            if (e.target.checked) {
                selectedVoterIds.add(id);
                if (card) card.classList.add('row-selected');
            } else {
                selectedVoterIds.delete(id);
                if (card) card.classList.remove('row-selected');
            }
            updateSelectionUI();
        });
    });

    // Populate Official ECI A4 Sheet Header, Footer & Navigation for Database
    const startSerial = dbState.totalFiltered > 0 ? (dbState.page - 1) * 30 + 1 : 0;
    const endSerial = Math.min(dbState.page * 30, dbState.totalFiltered);

    let maleCount = 0;
    let femaleCount = 0;
    dbState.records.forEach(r => {
        if (r.gender === 'महिला') femaleCount++;
        else if (r.gender === 'पुरुष') maleCount++;
    });

    const firstRec = dbState.records[0] || {};
    const assemblyStr = firstRec.assembly_name || firstRec.assembly_no || (elements.bulkCurrentAssembly && elements.bulkCurrentAssembly.innerText !== '--' ? elements.bulkCurrentAssembly.innerText : 'विधान सभा निर्वाचन क्षेत्र');
    const partStr = firstRec.part_no ? `भाग ${firstRec.part_no}${firstRec.part_name ? ' (' + firstRec.part_name + ')' : ''}` : (elements.dbPartInput && elements.dbPartInput.value ? `भाग ${elements.dbPartInput.value}` : 'समस्त भाग');
    const stationStr = firstRec.polling_station || firstRec.section_name || '--';

    if (elements.dbPaperA4Assembly) elements.dbPaperA4Assembly.innerText = assemblyStr;
    if (elements.dbPaperA4Part) elements.dbPaperA4Part.innerText = partStr;
    if (elements.dbPaperA4Station) elements.dbPaperA4Station.innerText = stationStr;
    if (elements.dbPaperA4PageBadge) elements.dbPaperA4PageBadge.innerText = `📄 A4 पृष्ठ सं०: ${dbState.page}`;
    if (elements.dbPaperA4SerialRange) elements.dbPaperA4SerialRange.innerText = dbState.records.length > 0 ? `क्र० ${startSerial} - ${endSerial}` : '--';
    if (elements.dbPaperA4Count) elements.dbPaperA4Count.innerText = dbState.records.length;
    if (elements.dbPaperA4GenderBreakdown) elements.dbPaperA4GenderBreakdown.innerText = dbState.records.length > 0 ? `(पुरुष: ${maleCount} | महिला: ${femaleCount})` : '';
    if (elements.dbPaperA4FooterPage) elements.dbPaperA4FooterPage.innerText = `A4 पृष्ठ ${dbState.page} / ${dbState.totalPages}`;

    // Update Dropdown selector
    if (elements.dbPaperPageSelect) {
        elements.dbPaperPageSelect.innerHTML = '';
        const maxPageOptions = Math.min(dbState.totalPages, 500);
        for (let p = 1; p <= maxPageOptions; p++) {
            const opt = document.createElement('option');
            opt.value = p;
            const pStart = (p - 1) * 30 + 1;
            const pEnd = Math.min(p * 30, dbState.totalFiltered);
            opt.text = `A4 पृष्ठ ${p} (क्र० ${pStart} - ${pEnd})`;
            if (p === dbState.page) opt.selected = true;
            elements.dbPaperPageSelect.appendChild(opt);
        }
    }

    if (elements.dbPaperPageSummaryBadge) {
        elements.dbPaperPageSummaryBadge.innerHTML = `इस A4 पृष्ठ पर: <strong>${dbState.records.length} मतदाता</strong> (क्रमांक ${startSerial} से ${endSerial}) | कुल परिणाम: ${dbState.totalFiltered.toLocaleString('hi-IN')}`;
    }
    if (elements.dbPaperBottomInfo) {
        elements.dbPaperBottomInfo.innerText = `A4 पृष्ठ ${dbState.page} / ${dbState.totalPages} (दिखा रहे हैं ${startSerial} - ${endSerial})`;
    }
    if (elements.dbPaperPrevBtn) elements.dbPaperPrevBtn.disabled = dbState.page <= 1;
    if (elements.dbPaperNextBtn) elements.dbPaperNextBtn.disabled = dbState.page >= dbState.totalPages;
    if (elements.dbPaperBottomPrevBtn) elements.dbPaperBottomPrevBtn.disabled = dbState.page <= 1;
    if (elements.dbPaperBottomNextBtn) elements.dbPaperBottomNextBtn.disabled = dbState.page >= dbState.totalPages;

    if (window.lucide) lucide.createIcons();
    updateSelectionUI();
}

// Render database records in table
function renderDbTable() {
    const tbody = elements.dbVoterTableBody;
    tbody.innerHTML = '';

    if (dbState.records.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="15">
                    <div class="empty-state-wrap">
                        <div class="empty-state-icon-box">
                            <i data-lucide="inbox"></i>
                        </div>
                        <h4 class="empty-state-title">डेटाबेस में कोई मतदाता रिकॉर्ड नहीं मिला</h4>
                        <p class="empty-state-desc">
                            कृपया अपने खोज शब्द बदलें या इस डेटाबेस में नई मतदाता सूची PDF अपलोड करें।
                        </p>
                        <div class="empty-state-actions">
                            <button class="empty-state-btn-primary" onclick="resetDbFilters()">
                                <i data-lucide="rotate-ccw"></i> खोज फ़िल्टर साफ़ करें
                            </button>
                            <button class="empty-state-btn-secondary" onclick="switchTab('converter')">
                                <i data-lucide="file-up"></i> PDF अपलोड करें
                            </button>
                        </div>
                    </div>
                </td>
            </tr>
        `;
        if (window.lucide) lucide.createIcons();
        elements.dbResultsSummaryText.innerText = 'कुल 0 मतदाता मिले';
        updateSelectionUI();
        return;
    }

    if (dbState.deletedFiltered > 0 && dbState.activeFiltered > 0) {
        elements.dbResultsSummaryText.innerText = `कुल ${dbState.activeFiltered.toLocaleString('hi-IN')} सक्रिय (+ ${dbState.deletedFiltered.toLocaleString('hi-IN')} विलोपित) मतदाता मिले (सक्रिय डेटाबेस: ${dbState.totalRecords.toLocaleString('hi-IN')})`;
    } else if (dbState.deletedFiltered > 0) {
        elements.dbResultsSummaryText.innerText = `कुल ${dbState.deletedFiltered.toLocaleString('hi-IN')} विलोपित मतदाता मिले (सक्रिय डेटाबेस: ${dbState.totalRecords.toLocaleString('hi-IN')})`;
    } else {
        elements.dbResultsSummaryText.innerText = `कुल ${dbState.totalFiltered.toLocaleString('hi-IN')} मतदाता मिले (सक्रिय डेटाबेस: ${dbState.totalRecords.toLocaleString('hi-IN')})`;
    }

    dbState.records.forEach(v => {
        const tr = document.createElement('tr');
        const isSelected = selectedVoterIds.has(v.id);
        if (isSelected) {
            tr.classList.add('row-selected');
        }

        const isDeleted = Boolean(v.is_deleted);
        if (isDeleted) {
            tr.classList.add('row-deleted');
        }

        const isEpicDefective = !v.epic_no || (v.warning_message && v.warning_message.includes('EPIC')) || !/^(?:[A-Z]{3}\d{7}|(?:UP|[A-Z]{2})\/\d{1,3}\/\d{1,4}\/\d{3,8})$/i.test((v.epic_no || '').trim());
        const epicDisplay = v.epic_no 
            ? (isEpicDefective 
                ? `<span class="epic-badge epic-badge-warn font-mono" title="${escapeHtml(v.warning_message || 'EPIC प्रारूप अमान्य (कम/अधिक अंक) - मैन्युअल जांच अपेक्षित')}">⚠️ ${escapeHtml(v.epic_no)}</span>` 
                : `<span class="epic-badge font-mono">${escapeHtml(v.epic_no)}</span>`) 
            : '<span class="epic-badge epic-badge-warn font-mono" title="EPIC अनुपलब्ध / रिक्त">⚠️ रिक्त</span>';
        const isOp = isOperatorUser();
        const dateStr = v.created_at ? v.created_at.substring(0, 10) : '--';
        const muslimBadge = (!isOp && v.is_muslim) ? `<span class="badge-muslim" title="${escapeHtml(v.muslim_reason || 'मुस्लिम समुदाय')}">☪️ मुस्लिम</span>` : '';
        const deletedBadge = isDeleted ? `<span class="badge-deleted" title="मतदाता सूची से विलोपित / DELETED">[विलोपित / DELETED]</span>` : '';
        const pendingBadge = v.is_pending_approval ? `<span class="badge-pending-approval" title="यह बदलाव ऑपरेटर द्वारा प्रस्तावित है और एडमिन अनुमोदन के लिए लंबित है">⏳ अनुमोदन लंबित</span>` : '';
        const nameClass = isDeleted ? 'name-deleted' : '';

        let casteBadge = '';
        // Rule: When voter is Muslim or user is operator, do NOT display caste
        if (!isOp && !v.is_muslim && v.caste_key && v.caste_key !== 'muslim' && CASTE_LABELS[v.caste_key]) {
            const exp = getCasteExplanation(v);
            const cLabel = CASTE_LABELS[v.caste_key];
            const isAi = v.caste_source === 'household_ai';
            const isLineage = v.caste_source === 'family_lineage_ai';
            const aiIcon = isAi ? ' 🏠' : (isLineage ? ' 👨‍👩‍👧' : '');
            const title = exp ? `${exp.casteLabel} (${exp.category}) - ${exp.methodTitle.replace(/[^\w\s\u0900-\u097F]/g, '')}` : cLabel;
            const infoAttr = exp ? `data-caste-info="${encodeURIComponent(JSON.stringify(exp))}"` : '';
            casteBadge = `<span class="badge-caste ${isAi ? 'badge-caste-ai' : ''}" ${infoAttr} title="${escapeHtml(title)}">${escapeHtml(cLabel)}${aiIcon}</span>`;
        }

        tr.innerHTML = `
            <td style="text-align: center;">
                <input type="checkbox" class="db-row-checkbox" data-id="${v.id}" ${isSelected ? 'checked' : ''} title="मतदाता ID #${v.id} चुनें">
            </td>
            <td class="font-mono text-muted">${v.id}</td>
            <td class="text-muted font-mono">${v.serial_no || '--'}</td>
            <td><strong class="${nameClass}">${escapeHtml(v.name)}</strong> ${deletedBadge} ${pendingBadge} ${muslimBadge} ${casteBadge}</td>
            <td><span class="relation-badge">${escapeHtml(v.relation_type || 'पिता')}</span></td>
            <td>${escapeHtml(v.relation_name || '--')}</td>
            <td>${escapeHtml(v.house_no || '--')}</td>
            <td>${v.age ? v.age + ' वर्ष' : '--'}</td>
            <td><span class="gender-pill ${v.gender === 'महिला' ? 'gender-female' : 'gender-male'}">${escapeHtml(v.gender || 'पुरुष')}</span></td>
            <td>${epicDisplay}</td>
            <td><span class="tag tag-sm">${escapeHtml(v.part_no || '--')}</span></td>
            <td>${escapeHtml(v.assembly || '--')}</td>
            <td class="text-sm" title="${escapeHtml(v.polling_station || '')}">${escapeHtml((v.polling_station || '--').substring(0, 30))}</td>
            <td class="text-muted text-sm">${dateStr}</td>
            <td style="white-space: nowrap; text-align: center;">
                <button class="btn-slip-db" title="🖨️ मतदाता सूचना पर्ची A4 प्रिंट करें" onclick="window.printSingleVoterSlip(${v.id})" style="border-color: #bbf7d0; background: #f0fdf4; color: #166534;">
                    <i data-lucide="printer"></i>
                </button>
                <button class="btn-slip-db" title="📱 व्हाट्सएप पर्ची भेजें" onclick="window.showDbVoterSlip(${v.id})">
                    <i data-lucide="share-2"></i>
                </button>
                <button class="btn-slip-db" title="📋 पर्ची कॉपी करें" onclick="window.copyDbVoterSlip(${v.id})" style="border-color: #bfdbfe; background: #eff6ff; color: #2563eb;">
                    <i data-lucide="copy"></i>
                </button>
                <button class="btn-action-edit" title="मतदाता विवरण सुधारें (Edit Voter)" onclick="window.openDbEditModal(${v.id})">
                    <i data-lucide="edit-3"></i>
                </button>
                <button class="btn-delete-row" title="रिकॉर्ड हटाएं" onclick="window.deleteDbVoter(${v.id})">
                    <i data-lucide="trash-2"></i>
                </button>
            </td>
        `;
        tbody.appendChild(tr);
    });

    // Bind individual row checkbox changes
    tbody.querySelectorAll('.db-row-checkbox').forEach(cb => {
        cb.addEventListener('change', (e) => {
            const id = parseInt(e.target.dataset.id);
            const tr = e.target.closest('tr');
            if (e.target.checked) {
                selectedVoterIds.add(id);
                if (tr) tr.classList.add('row-selected');
            } else {
                selectedVoterIds.delete(id);
                if (tr) tr.classList.remove('row-selected');
            }
            updateSelectionUI();
        });
    });

    updateSelectionUI();
    lucide.createIcons();
}

// Update Batch Selection UI and Table Header Checkbox State
function updateSelectionUI() {
    const count = selectedVoterIds.size;
    if (elements.dbSelectedBadge) {
        elements.dbSelectedBadge.innerText = `${count} चयनित`;
    }

    if (elements.dbSelectionBar) {
        elements.dbSelectionBar.style.display = count > 0 ? 'inline-flex' : 'none';
    }

    if (elements.dbSelectAllThCheckbox) {
        if (dbState.records.length === 0) {
            elements.dbSelectAllThCheckbox.checked = false;
            elements.dbSelectAllThCheckbox.indeterminate = false;
        } else {
            const allOnPageSelected = dbState.records.every(v => selectedVoterIds.has(v.id));
            const someOnPageSelected = dbState.records.some(v => selectedVoterIds.has(v.id));

            if (allOnPageSelected) {
                elements.dbSelectAllThCheckbox.checked = true;
                elements.dbSelectAllThCheckbox.indeterminate = false;
            } else if (someOnPageSelected) {
                elements.dbSelectAllThCheckbox.checked = false;
                elements.dbSelectAllThCheckbox.indeterminate = true;
            } else {
                elements.dbSelectAllThCheckbox.checked = false;
                elements.dbSelectAllThCheckbox.indeterminate = false;
            }
        }
    }
}

// Handle Table Header Select All Checkbox Change
function handleSelectAllThCheckboxChange(e) {
    const isChecked = e.target.checked;
    handleSelectAllOnPage(isChecked);
}

// Select or Deselect all records on current page
function handleSelectAllOnPage(select) {
    if (dbState.records.length === 0) return;

    dbState.records.forEach(v => {
        if (select) {
            selectedVoterIds.add(v.id);
        } else {
            selectedVoterIds.delete(v.id);
        }
    });

    refreshActiveDbView();
    if (select) {
        showToast(`इस पेज के सभी ${dbState.records.length} मतदाता चुने गए।`, 'info');
    }
}

// Clear all selected records
function handleClearSelection() {
    selectedVoterIds.clear();
    refreshActiveDbView();
}

// Toggle Dropdown Menu
function toggleBulkMenu() {
    if (!elements.dbBulkMenu) return;
    const isVisible = elements.dbBulkMenu.style.display === 'block';
    elements.dbBulkMenu.style.display = isVisible ? 'none' : 'block';
}

function hideBulkMenu() {
    if (elements.dbBulkMenu) {
        elements.dbBulkMenu.style.display = 'none';
    }
}

// Show Delete Confirmation Modal
function showDeleteConfirmModal({ title, subtitle, msg, warningText, confirmBtnText = 'हाँ, हटाएं', onConfirm }) {
    if (!elements.deleteConfirmModal) return;

    elements.deleteModalTitle.innerText = title;
    elements.deleteModalSubtitle.innerText = subtitle || '';
    elements.deleteModalSubtitle.style.display = subtitle ? 'block' : 'none';
    elements.deleteModalMsg.innerHTML = msg;

    if (warningText) {
        elements.deleteWarningText.innerText = warningText;
        elements.deleteWarningBox.style.display = 'flex';
    } else {
        elements.deleteWarningBox.style.display = 'none';
    }

    elements.confirmDeleteBtnText.innerText = confirmBtnText;
    pendingDeleteAction = onConfirm;
    elements.deleteConfirmModal.style.display = 'flex';
    lucide.createIcons();
}

// Hide Delete Confirmation Modal
function hideDeleteConfirmModal() {
    if (elements.deleteConfirmModal) {
        elements.deleteConfirmModal.style.display = 'none';
    }
    pendingDeleteAction = null;
}

// Extract Active Search & Filter Parameters
function getActiveDbFilters() {
    const filters = {};
    const q = elements.dbQueryInput.value.trim();
    if (q) filters.q = q;
    const name = elements.dbNameInput.value.trim();
    if (name) filters.name = name;
    const relName = elements.dbRelNameInput.value.trim();
    if (relName) filters.relation_name = relName;
    const epic = elements.dbEpicInput.value.trim();
    if (epic) filters.epic_no = epic;
    const part = elements.dbPartInput.value.trim();
    if (part) filters.part_no = part;
    const gender = elements.dbGenderSelect.value;
    if (gender && gender !== 'all') filters.gender = gender;
    const house = elements.dbHouseInput.value.trim();
    if (house) filters.house_no = house;
    const minAge = elements.dbMinAgeInput.value.trim();
    if (minAge) filters.min_age = parseInt(minAge);
    const maxAge = elements.dbMaxAgeInput.value.trim();
    if (maxAge) filters.max_age = parseInt(maxAge);
    if (elements.dbMuslimSelect && elements.dbMuslimSelect.value !== 'all') {
        filters.muslim = elements.dbMuslimSelect.value;
    }
    return filters;
}

// Delete Selected Records (Batch Delete)
function handleDeleteSelected() {
    if (selectedVoterIds.size === 0) {
        showToast('कृपया पहले मतदाता चुनें।', 'warning');
        return;
    }

    const count = selectedVoterIds.size;
    showDeleteConfirmModal({
        title: 'चयनित मतदाता हटाने की पुष्टि',
        subtitle: `डेटाबेस से ${count} मतदाता हटाए जाने हैं`,
        msg: `क्या आप वाकई चयनित <strong>${count}</strong> मतदाताओं को डेटाबेस से हटाना चाहते हैं?`,
        warningText: 'सावधानी: चयनित रिकॉर्ड हमेशा के लिए मिट जाएंगे।',
        confirmBtnText: `हटाएं (${count})`,
        onConfirm: async () => {
            try {
                const res = await fetch('/api/database/delete-batch', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ ids: Array.from(selectedVoterIds) })
                });
                const data = await res.json();
                if (!res.ok) throw new Error(data.detail || 'हटाने में विफल');

                showToast(data.message || `${count} मतदाता हटा दिए गए।`, 'success');
                selectedVoterIds.clear();
                hideDeleteConfirmModal();
                fetchDbStats();
                fetchDbRecords();
            } catch (err) {
                showToast('त्रुटि: ' + err.message, 'error');
            }
        }
    });
}

// Delete All Voters Matching Current Filter
function handleDeleteByFilter() {
    if (dbState.totalFiltered === 0) {
        showToast('वर्तमान फ़िल्टर में कोई मतदाता नहीं मिला।', 'warning');
        return;
    }

    const filters = getActiveDbFilters();
    const hasAnyFilter = Object.keys(filters).length > 0;

    if (!hasAnyFilter) {
        showToast('कोई फ़िल्टर लागू नहीं है। पूरा डेटाबेस हटाने के लिए "पूरी डेटाबेस खाली करें" का प्रयोग करें।', 'warning');
        return;
    }

    const count = dbState.totalFiltered;
    showDeleteConfirmModal({
        title: 'फ़िल्टर किए गए मतदाता हटाने की पुष्टि',
        subtitle: `वर्तमान खोज फ़िल्टर के अनुसार कुल ${count} रिकॉर्ड पाए गए हैं`,
        msg: `क्या आप वर्तमान फ़िल्टर के सभी <strong>${count}</strong> मतदाताओं को डेटाबेस से स्थायी रूप से हटाना चाहते हैं?`,
        warningText: 'अत्यंत महत्वपूर्ण: वर्तमान फ़िल्टर के सभी रिकॉर्ड डिलीट हो जाएंगे। यह प्रक्रिया वापस नहीं ली जा सकती।',
        confirmBtnText: `फ़िल्टर के सभी (${count}) हटाएं`,
        onConfirm: async () => {
            try {
                const res = await fetch('/api/database/delete-by-filter', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(filters)
                });
                const data = await res.json();
                if (!res.ok) throw new Error(data.detail || 'हटाने में विफल');

                showToast(data.message || `${count} मतदाता हटा दिए गए।`, data.status === 'warning' ? 'warning' : 'success');
                selectedVoterIds.clear();
                hideDeleteConfirmModal();
                fetchDbStats();
                fetchDbRecords();
            } catch (err) {
                showToast('त्रुटि: ' + err.message, 'error');
            }
        }
    });
}

// Clear Entire Database
function handleClearAllDatabase() {
    if (dbState.totalRecords === 0) {
        showToast('डेटाबेस पहले से ही पूरी तरह खाली है।', 'info');
        return;
    }

    const count = dbState.totalRecords;
    showDeleteConfirmModal({
        title: 'पूरी डेटाबेस खाली करने की चेतावनी',
        subtitle: `डेटाबेस में कुल ${count} मतदाता उपलब्ध हैं`,
        msg: `क्या आप वाकई पूरा डेटाबेस खाली करना चाहते हैं? <strong>सभी ${count} मतदाताओं का विवरण हमेशा के लिए मिट जाएगा।</strong>`,
        warningText: 'खतरा: यह क्रिया पूर्ववत नहीं की जा सकती (Irreversible)।',
        confirmBtnText: 'हाँ, पूरा डेटाबेस खाली करें',
        onConfirm: async () => {
            try {
                const res = await fetch('/api/database/clear', {
                    method: 'DELETE'
                });
                const data = await res.json();
                if (!res.ok) throw new Error(data.detail || 'डेटाबेस खाली करने में विफल');

                showToast(data.message || 'पूरा डेटाबेस खाली कर दिया गया।', 'success');
                selectedVoterIds.clear();
                hideDeleteConfirmModal();
                fetchDbStats();
                fetchDbRecords();
            } catch (err) {
                showToast('त्रुटि: ' + err.message, 'error');
            }
        }
    });
}

// Render database pagination
function renderDbPagination() {
    const start = dbState.totalFiltered === 0 ? 0 : (dbState.page - 1) * dbState.pageSize + 1;
    const end = Math.min(dbState.page * dbState.pageSize, dbState.totalFiltered);

    elements.dbPaginationInfo.innerText = `दिखा रहे हैं ${start} - ${end} (कुल ${dbState.totalFiltered} मतदाता)`;
    elements.dbPageIndicator.innerText = `पेज ${dbState.page} / ${dbState.totalPages}`;

    elements.dbPrevPageBtn.disabled = dbState.page <= 1;
    elements.dbNextPageBtn.disabled = dbState.page >= dbState.totalPages;
}

// Reset database filters
function resetDbFilters() {
    elements.dbQueryInput.value = '';
    elements.dbNameInput.value = '';
    elements.dbRelNameInput.value = '';
    elements.dbEpicInput.value = '';
    elements.dbPartInput.value = '';
    elements.dbGenderSelect.value = 'all';
    elements.dbHouseInput.value = '';
    elements.dbMinAgeInput.value = '';
    elements.dbMaxAgeInput.value = '';
    if (elements.dbMuslimSelect) elements.dbMuslimSelect.value = 'all';
    if (elements.dbCasteSelect) elements.dbCasteSelect.value = 'all';
    if (elements.dbStatusSelect) elements.dbStatusSelect.value = 'all';
    clearDrilldownBannerOnly();
    dbState.page = 1;
    fetchDbRecords();
}
window.resetDbFilters = resetDbFilters;
window.switchTab = switchTab;

// Export database search to Excel
function handleDbExportExcel() {
    const params = new URLSearchParams();
    const q = elements.dbQueryInput.value.trim();
    if (q) params.append('q', q);
    const name = elements.dbNameInput.value.trim();
    if (name) params.append('name', name);
    const relName = elements.dbRelNameInput.value.trim();
    if (relName) params.append('relation_name', relName);
    const epic = elements.dbEpicInput.value.trim();
    if (epic) params.append('epic_no', epic);
    const part = elements.dbPartInput.value.trim();
    if (part) params.append('part_no', part);
    const gender = elements.dbGenderSelect.value;
    if (gender && gender !== 'all') params.append('gender', gender);
    const house = elements.dbHouseInput.value.trim();
    if (house) params.append('house_no', house);
    const minAge = elements.dbMinAgeInput.value.trim();
    if (minAge) params.append('min_age', minAge);
    const maxAge = elements.dbMaxAgeInput.value.trim();
    if (maxAge) params.append('max_age', maxAge);

    if (elements.dbMuslimSelect && elements.dbMuslimSelect.value !== 'all') {
        params.append('muslim', elements.dbMuslimSelect.value);
    }
    if (elements.dbCasteSelect && elements.dbCasteSelect.value !== 'all') {
        params.append('caste_key', elements.dbCasteSelect.value);
    }
    if (elements.dbStatusSelect && elements.dbStatusSelect.value !== 'all') {
        params.append('status', elements.dbStatusSelect.value);
    }

    window.location.href = `/api/database/export?${params.toString()}`;
    showToast('डेटाबेस खोज परिणाम Excel में डाउनलोड हो रहा है...', 'info');
}

// Delete single voter record
window.deleteDbVoter = function(voterId) {
    showDeleteConfirmModal({
        title: 'मतदाता रिकॉर्ड हटाएं',
        subtitle: `मतदाता ID: #${voterId}`,
        msg: `क्या आप वाकई इस मतदाता (ID #${voterId}) को डेटाबेस से हटाना चाहते हैं?`,
        warningText: 'यह रिकॉर्ड डेटाबेस से हटा दिया जाएगा।',
        confirmBtnText: 'हाँ, हटाएं',
        onConfirm: async () => {
            try {
                const res = await fetch(`/api/database/delete/${voterId}`, {
                    method: 'DELETE'
                });
                const data = await res.json();
                if (!res.ok) throw new Error(data.detail || 'हटाने में विफल');

                showToast(data.message || 'रिकॉर्ड हटा दिया गया।', 'success');
                selectedVoterIds.delete(voterId);
                hideDeleteConfirmModal();
                fetchDbStats();
                fetchDbRecords();
            } catch (err) {
                showToast('त्रुटि: ' + err.message, 'error');
            }
        }
    });
};

// =============================================================================
// DATABASE VOTER ADD & EDIT MODAL (ADMIN PANEL)
// =============================================================================

window.openDbAddModal = function() {
    const modal = document.getElementById('dbEditModal');
    if (!modal) return;

    // Reset form fields
    document.getElementById('dbEditId').value = '';
    document.getElementById('dbEditSerial').value = '';
    document.getElementById('dbEditEpic').value = '';

    // Auto-fill Part number from active DB filter if present
    const activePartFilter = elements.dbPartInput ? elements.dbPartInput.value.trim() : '';
    document.getElementById('dbEditPart').value = activePartFilter;

    document.getElementById('dbEditName').value = '';
    document.getElementById('dbEditRelType').value = 'पिता';
    document.getElementById('dbEditRelName').value = '';
    document.getElementById('dbEditHouse').value = '';
    document.getElementById('dbEditAge').value = '';
    document.getElementById('dbEditGender').value = 'पुरुष';

    const activeAssemblyFilter = elements.dbAssemblySelect ? elements.dbAssemblySelect.value.trim() : '';
    document.getElementById('dbEditAssembly').value = activeAssemblyFilter;
    document.getElementById('dbEditStation').value = '';

    const casteSelect = document.getElementById('dbEditCaste');
    const muslimSelect = document.getElementById('dbEditMuslim');
    if (isOperatorUser()) {
        if (casteSelect) casteSelect.closest('.form-group').style.display = 'none';
        if (muslimSelect) muslimSelect.closest('.form-group').style.display = 'none';
    } else {
        if (casteSelect) {
            casteSelect.closest('.form-group').style.display = '';
            casteSelect.value = '';
        }
        if (muslimSelect) {
            muslimSelect.closest('.form-group').style.display = '';
            muslimSelect.value = '0';
        }
    }

    const statusSelect = document.getElementById('dbEditStatus');
    if (statusSelect) statusSelect.value = 'active';

    const titleEl = document.getElementById('dbEditModalTitle');
    if (titleEl) titleEl.textContent = '+ नया मतदाता जोड़ें (Add New Voter to Database)';
    const iconEl = document.getElementById('dbEditModalIcon');
    if (iconEl) iconEl.setAttribute('data-lucide', 'user-plus');
    const btnTextEl = document.getElementById('dbEditSaveBtnText');
    if (btnTextEl) btnTextEl.textContent = '+ मतदाता जोड़ें (Add Voter)';

    modal.style.display = 'flex';
    lucide.createIcons();
    document.getElementById('dbEditName').focus();
};

window.openDbEditModal = async function(voterId) {
    const modal = document.getElementById('dbEditModal');
    if (!modal) return;

    try {
        const fetchFn = (window.VoterAuth && typeof VoterAuth.authFetch === 'function') ? VoterAuth.authFetch : fetch;
        const res = await fetchFn(`/api/database/voter/${voterId}`);
        if (!res.ok) {
            const errData = await res.json().catch(() => ({}));
            throw new Error(errData.detail || 'मतदाता विवरण लोड करने में त्रुटि हुई।');
        }
        const json = await res.json();
        const v = json.data;
        if (!v) throw new Error('मतदाता डेटा नहीं मिला।');

        // Populate fields
        document.getElementById('dbEditId').value = v.id;
        document.getElementById('dbEditSerial').value = v.serial_no || '';
        document.getElementById('dbEditEpic').value = v.epic_no || '';
        document.getElementById('dbEditPart').value = v.part_no || '';
        document.getElementById('dbEditName').value = v.name || '';
        document.getElementById('dbEditRelType').value = v.relation_type || 'पिता';
        document.getElementById('dbEditRelName').value = v.relation_name || '';
        document.getElementById('dbEditHouse').value = v.house_no || '';
        document.getElementById('dbEditAge').value = v.age || '';
        document.getElementById('dbEditGender').value = v.gender || 'पुरुष';
        document.getElementById('dbEditAssembly').value = v.assembly || '';
        document.getElementById('dbEditStation').value = v.polling_station || '';

        // Caste and Community
        const casteSelect = document.getElementById('dbEditCaste');
        const muslimSelect = document.getElementById('dbEditMuslim');
        if (isOperatorUser()) {
            if (casteSelect) casteSelect.closest('.form-group').style.display = 'none';
            if (muslimSelect) muslimSelect.closest('.form-group').style.display = 'none';
        } else {
            if (casteSelect) {
                casteSelect.closest('.form-group').style.display = '';
                casteSelect.value = v.caste_key || '';
            }
            if (muslimSelect) {
                muslimSelect.closest('.form-group').style.display = '';
                muslimSelect.value = v.is_muslim ? '1' : '0';
            }
        }

        const statusSelect = document.getElementById('dbEditStatus');
        if (statusSelect) {
            statusSelect.value = v.is_deleted ? 'deleted' : 'active';
        }

        const titleEl = document.getElementById('dbEditModalTitle');
        if (titleEl) titleEl.textContent = 'मतदाता विवरण सुधारें (Edit Voter in Database)';
        const iconEl = document.getElementById('dbEditModalIcon');
        if (iconEl) iconEl.setAttribute('data-lucide', 'user-cog');
        const btnTextEl = document.getElementById('dbEditSaveBtnText');
        if (btnTextEl) btnTextEl.textContent = 'सुरक्षित करें (Save Changes)';

        modal.style.display = 'flex';
        lucide.createIcons();
        document.getElementById('dbEditName').focus();
    } catch (err) {
        console.error('Failed to load voter for edit:', err);
        showToast('त्रुटि: ' + err.message, 'error');
    }
};

window.closeDbEditModal = function() {
    const modal = document.getElementById('dbEditModal');
    if (modal) modal.style.display = 'none';
};

async function handleSaveDbVoter(e) {
    if (e && e.preventDefault) e.preventDefault();
    const id = document.getElementById('dbEditId').value;
    const isAdd = !id;

    const name = document.getElementById('dbEditName').value.trim();
    const partNo = document.getElementById('dbEditPart').value.trim();
    if (!name) {
        showToast('कृपया मतदाता का नाम दर्ज करें।', 'warning');
        return;
    }
    if (!partNo) {
        showToast('कृपया भाग संख्या दर्ज करें।', 'warning');
        return;
    }

    const saveBtn = document.getElementById('saveDbEditBtn');
    const origHtml = saveBtn ? saveBtn.innerHTML : '';
    if (saveBtn) {
        saveBtn.disabled = true;
        saveBtn.innerHTML = '<i data-lucide="loader-2" class="spin"></i> <span>' + (isAdd ? 'जोड़ा जा रहा है...' : 'सुरक्षित हो रहा है...') + '</span>';
        lucide.createIcons();
    }

    try {
        const payload = {
            serial_no: document.getElementById('dbEditSerial').value ? parseInt(document.getElementById('dbEditSerial').value, 10) : null,
            epic_no: document.getElementById('dbEditEpic').value.trim().toUpperCase(),
            part_no: partNo,
            name: name,
            relation_type: document.getElementById('dbEditRelType').value,
            relation_name: document.getElementById('dbEditRelName').value.trim(),
            house_no: document.getElementById('dbEditHouse').value.trim(),
            age: document.getElementById('dbEditAge').value ? parseInt(document.getElementById('dbEditAge').value, 10) : null,
            gender: document.getElementById('dbEditGender').value,
            assembly: document.getElementById('dbEditAssembly').value.trim(),
            polling_station: document.getElementById('dbEditStation').value.trim(),
            caste_key: document.getElementById('dbEditCaste').value || null,
            is_muslim: parseInt(document.getElementById('dbEditMuslim').value, 10) || 0,
            is_deleted: document.getElementById('dbEditStatus').value === 'deleted' ? 1 : 0
        };

        const fetchFn = (window.VoterAuth && typeof VoterAuth.authFetch === 'function') ? VoterAuth.authFetch : fetch;
        const url = isAdd ? '/api/database/add' : `/api/database/update/${id}`;
        const method = isAdd ? 'POST' : 'PUT';

        const res = await fetchFn(url, {
            method: method,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        const data = await res.json();
        if (!res.ok || data.status !== 'success') {
            throw new Error(data.detail || data.message || (isAdd ? 'मतदाता जोड़ने में त्रुटि हुई।' : 'अद्यतन (Update) में त्रुटि हुई।'));
        }

        showToast(data.message || (isAdd ? 'नया मतदाता सफलतापूर्वक डेटाबेस में जोड़ा गया।' : 'मतदाता विवरण सफलतापूर्वक अद्यतन (Update) कर दिया गया।'), 'success');
        window.closeDbEditModal();
        await fetchDbRecords();
        fetchDbStats();
        loadCasteAnalytics();
    } catch (err) {
        console.error('Failed to save db voter:', err);
        showToast('त्रुटि: ' + err.message, 'error');
    } finally {
        if (saveBtn) {
            saveBtn.disabled = false;
            saveBtn.innerHTML = origHtml;
            lucide.createIcons();
        }
    }
}

// =============================================================================
// ONLINE SEARCH RESTRICTION & PRIVACY SETTINGS
// =============================================================================

let privacySettingsCache = null;

async function checkPrivacyStatus() {
    try {
        const res = await fetch('/api/admin/privacy-settings');
        if (!res.ok) return;
        const data = await res.json();
        privacySettingsCache = data;
        const navBadge = document.getElementById('navPrivacyBadge');
        if (navBadge) {
            if (data.settings && data.settings.enabled) {
                navBadge.style.display = 'inline-flex';
                navBadge.innerText = `${data.blocked_voters || 'सक्रिय'} ब्लॉक`;
            } else {
                navBadge.style.display = 'none';
            }
        }
    } catch (e) {
        console.warn('Privacy status fetch failed', e);
    }
}

async function openPrivacyModal() {
    if (isOperatorUser()) {
        showToast('पहुँच अस्वीकृत: ऑनलाइन सर्च गोपनीयता नियंत्रण केवल मुख्य एडमिन के लिए सुरक्षित है।', 'error');
        return;
    }
    const modal = document.getElementById('privacyModal');
    if (!modal) return;
    modal.style.display = 'flex';

    try {
        const fetchFn = typeof adminFetch === 'function' ? adminFetch : fetch;
        const res = await fetchFn('/api/admin/privacy-settings');
        if (!res.ok) throw new Error('गोपनीयता सेटिंग्स लोड नहीं हो सकीं।');
        const data = await res.json();
        privacySettingsCache = data;
        renderPrivacyModalContent(data);
    } catch (err) {
        showToast('त्रुटि: ' + err.message, 'error');
    }
}

function closePrivacyModal() {
    const modal = document.getElementById('privacyModal');
    if (modal) modal.style.display = 'none';
}

function renderPrivacyModalContent(data) {
    const s = data.settings || {};
    const enabledToggle = document.getElementById('privacyEnabledToggle');
    const blockMuslimCheck = document.getElementById('privacyBlockMuslimCheck');
    const customInput = document.getElementById('privacyCustomSurnames');
    const container = document.getElementById('privacyPresetsContainer');

    if (enabledToggle) enabledToggle.checked = Boolean(s.enabled);
    if (blockMuslimCheck) blockMuslimCheck.checked = Boolean(s.block_muslim);
    if (customInput) customInput.value = (s.custom_surnames || []).join(', ');

    const banner = document.getElementById('privacyTotalBanner');
    const statTotal = document.getElementById('privacyStatTotalVoters');
    const statBlocked = document.getElementById('privacyStatBlockedVoters');
    const statSearchable = document.getElementById('privacyStatSearchableVoters');
    const muslimBadge = document.getElementById('privacyMuslimCountBadge');

    if (banner) banner.innerText = (data.total_voters || 0).toLocaleString('hi-IN');
    if (statTotal) statTotal.innerText = (data.total_voters || 0).toLocaleString('hi-IN');
    if (statBlocked) statBlocked.innerText = (data.blocked_voters || 0).toLocaleString('hi-IN');
    if (statSearchable) statSearchable.innerText = (data.searchable_voters || 0).toLocaleString('hi-IN');
    if (muslimBadge) muslimBadge.innerText = `${data.muslim_count || 0} मतदाता`;

    if (container) {
        container.innerHTML = '';
        const blockedKeys = new Set(s.blocked_caste_keys || []);

        (data.presets || []).forEach(preset => {
            const isChecked = blockedKeys.has(preset.key);
            const card = document.createElement('label');
            card.className = `caste-preset-card ${isChecked ? 'is-active' : ''}`;
            card.innerHTML = `
                <input type="checkbox" value="${preset.key}" ${isChecked ? 'checked' : ''} class="caste-preset-chk">
                <div style="flex: 1;">
                    <span class="caste-preset-title">${preset.label}</span>
                    <span class="caste-preset-desc">${preset.description}</span>
                </div>
                <span class="caste-preset-badge">${preset.count || 0} मतदाता</span>
            `;

            card.querySelector('input').addEventListener('change', (e) => {
                if (e.target.checked) card.classList.add('is-active');
                else card.classList.remove('is-active');
                updatePrivacyLivePreview();
            });

            container.appendChild(card);
        });
    }

    lucide.createIcons();
    updatePrivacyLivePreview();
}

function updatePrivacyLivePreview() {
    if (!privacySettingsCache) return;
    const enabledToggle = document.getElementById('privacyEnabledToggle');
    const blockMuslimCheck = document.getElementById('privacyBlockMuslimCheck');
    const statBlocked = document.getElementById('privacyStatBlockedVoters');
    const statSearchable = document.getElementById('privacyStatSearchableVoters');

    const isEnabled = enabledToggle ? enabledToggle.checked : false;
    const isMuslimBlocked = blockMuslimCheck ? blockMuslimCheck.checked : false;

    const totalVoters = privacySettingsCache.total_voters || 0;

    if (!isEnabled) {
        if (statBlocked) statBlocked.innerText = '0';
        if (statSearchable) statSearchable.innerText = totalVoters.toLocaleString('hi-IN');
        return;
    }

    const chks = document.querySelectorAll('#privacyPresetsContainer .caste-preset-chk:checked');
    const checkedKeys = new Set(Array.from(chks).map(c => c.value));

    let blockedSum = 0;
    if (isMuslimBlocked) {
        blockedSum += (privacySettingsCache.muslim_count || 0);
    }
    (privacySettingsCache.presets || []).forEach(p => {
        if (checkedKeys.has(p.key)) {
            blockedSum += (p.count || 0);
        }
    });

    blockedSum = Math.min(totalVoters, blockedSum);
    const searchable = Math.max(0, totalVoters - blockedSum);

    if (statBlocked) statBlocked.innerText = blockedSum.toLocaleString('hi-IN');
    if (statSearchable) statSearchable.innerText = searchable.toLocaleString('hi-IN');
}

async function savePrivacySettings() {
    const saveBtn = document.getElementById('savePrivacySettingsBtn');
    if (saveBtn) {
        saveBtn.disabled = true;
        saveBtn.innerHTML = '<i data-lucide="loader-2" class="animate-spin" style="width:16px;height:16px;"></i> सुरक्षित हो रहा है...';
    }

    try {
        const enabledToggle = document.getElementById('privacyEnabledToggle');
        const blockMuslimCheck = document.getElementById('privacyBlockMuslimCheck');
        const customInput = document.getElementById('privacyCustomSurnames');

        const chks = document.querySelectorAll('#privacyPresetsContainer .caste-preset-chk:checked');
        const blocked_caste_keys = Array.from(chks).map(c => c.value);

        const rawCustom = customInput ? customInput.value : '';
        const custom_surnames = rawCustom.split(/[,;\s]+/).map(s => s.trim()).filter(Boolean);

        const payload = {
            enabled: enabledToggle ? enabledToggle.checked : false,
            block_muslim: blockMuslimCheck ? blockMuslimCheck.checked : false,
            blocked_caste_keys: blocked_caste_keys,
            custom_surnames: custom_surnames
        };

        const res = await fetch('/api/admin/privacy-settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'सेटिंग्स सुरक्षित नहीं हो सकीं');

        showToast(data.message || 'ऑनलाइन सर्च सेटिंग्स सुरक्षित हो गईं।', 'success');
        closePrivacyModal();
        await checkPrivacyStatus();
    } catch (err) {
        showToast('त्रुटि: ' + err.message, 'error');
    } finally {
        if (saveBtn) {
            saveBtn.disabled = false;
            saveBtn.innerHTML = '<i data-lucide="check" style="width:16px;height:16px;"></i> सेटिंग्स लागू व सुरक्षित करें';
            lucide.createIcons();
        }
    }
}

// ==========================================================================
// Interactive Caste & Community Analytics & Drill-Down
// ==========================================================================
let communityChartInstance = null;
let casteChartInstance = null;

async function loadCasteAnalytics() {
    try {
        const res = await fetch('/api/database/caste-analytics');
        if (!res.ok) return;
        const data = await res.json();
        dbState.analyticsData = data;

        if (elements.communityTotalBadge) {
            elements.communityTotalBadge.innerText = `${data.total_voters.toLocaleString('hi-IN')} कुल`;
        }

        if (elements.casteTotalBadge) {
            const totalClassified = (data.castes || []).reduce((acc, c) => c.key !== 'other' ? acc + c.count : acc, 0);
            const hh = data.household_ai_count || 0;
            elements.casteTotalBadge.innerText = `${totalClassified} चिह्नित (🏠 ${hh} मकान)`;
        }

        renderCommunityChart(data);
        renderCasteChart(data);
    } catch (err) {
        console.warn('Failed to load caste analytics:', err);
    }
}

async function handleRecomputeCastes() {
    showDeleteConfirmModal({
        title: 'जाति स्वतः पुनःनिर्धारण (Caste Re-classification)',
        subtitle: 'भाग संख्या, मकान नंबर व पारिवारिक सम्बन्ध आधारित विश्लेषण',
        msg: 'क्या आप सभी मतदाताओं की जाति को भाग संख्या, मकान नंबर एवं पारिवारिक सम्बन्धों के आधार पर पुनः निर्धारित करना चाहते हैं? यह प्रक्रिया परिवारों, उपनामों और मकानों का विश्लेषण कर सभी मतदाताओं की जाति अद्यतन करेगी।',
        confirmBtnText: 'हाँ, पुनः निर्धारण प्रारंभ करें',
        onConfirm: async () => {
            hideDeleteConfirmModal();
            await executeRecomputeCastes();
        }
    });
}

async function executeRecomputeCastes() {
    const btn = elements.dbRecomputeCastesBtn;
    const origHtml = btn ? btn.innerHTML : '';
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<i data-lucide="loader-2" class="spin"></i> <span>विश्लेषण जारी है...</span>';
        lucide.createIcons();
    }

    showToast('परिवारों व मकानों का विश्लेषण हो रहा है...', 'info');

    try {
        const fetchFn = (window.VoterAuth && typeof VoterAuth.authFetch === 'function') ? VoterAuth.authFetch : fetch;
        const res = await fetchFn('/api/database/recompute-castes', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });
        const data = await res.json();
        if (res.ok && data.status === 'success') {
            const totalClassified = data.data?.total_classified || 0;
            const hhCount = data.data?.source_breakdown?.household_ai || 0;
            const linCount = data.data?.source_breakdown?.family_lineage_ai || 0;
            showToast(`सफलतापूर्वक पूर्ण! कुल ${totalClassified} मतदाताओं की जाति निर्धारित (मकान आधार: ${hhCount}, परिवार आधार: ${linCount})`, 'success');
            await loadCasteAnalytics();
            await fetchDbRecords();
        } else {
            showToast(data.detail || data.message || 'जाति पुनःनिर्धारण में त्रुटि हुई।', 'error');
        }
    } catch (err) {
        console.error('Failed to recompute castes:', err);
        showToast('सर्वर से संपर्क करने में त्रुटि हुई।', 'error');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = origHtml;
            lucide.createIcons();
        }
    }
}

function renderCommunityChart(data) {
    if (!elements.communityChartCanvas) return;
    if (typeof Chart === 'undefined') {
        console.warn('Chart.js not loaded yet');
        return;
    }

    if (communityChartInstance) {
        communityChartInstance.destroy();
        communityChartInstance = null;
    }

    const labels = data.community.map(c => c.label);
    const counts = data.community.map(c => c.count);
    const colors = data.community.map(c => c.color);

    const ctx = elements.communityChartCanvas.getContext('2d');
    communityChartInstance = new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: labels,
            datasets: [{
                data: counts,
                backgroundColor: colors,
                borderColor: '#FFFFFF',
                borderWidth: 3,
                hoverOffset: 10,
                hoverBorderColor: '#FFFFFF'
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            cutout: '65%',
            plugins: {
                legend: {
                    display: false
                },
                tooltip: {
                    padding: 10,
                    callbacks: {
                        label: function(context) {
                            const item = data.community[context.dataIndex];
                            return ` ${item.label}: ${item.count} (${item.percentage}%) — क्लिक करके लिस्ट देखें`;
                        }
                    }
                }
            },
            onClick: (evt, activeElements) => {
                if (!activeElements || activeElements.length === 0) return;
                const idx = activeElements[0].index;
                const clickedItem = data.community[idx];
                applyCommunityDrilldown(clickedItem);
            }
        }
    });

    renderCommunityLegend(data.community);
}

function renderCommunityLegend(communities) {
    if (!elements.communityLegendList) return;
    elements.communityLegendList.innerHTML = communities.map(item => {
        const isActive = dbState.activeDrilldown?.type === 'community' && dbState.activeDrilldown?.key === item.key;
        return `
            <div class="legend-item-chip ${isActive ? 'active' : ''}" 
                 onclick="window.applyCommunityDrilldownByKey('${item.key}')" 
                 title="क्लिक करके ${item.label} मतदाता देखें">
                <span class="legend-color-dot" style="background-color: ${item.color};"></span>
                <span>${item.label}</span>
                <span class="legend-count">(${item.count.toLocaleString('hi-IN')})</span>
            </div>
        `;
    }).join('');
}

function renderCasteChart(data) {
    if (!elements.casteChartCanvas) return;
    if (typeof Chart === 'undefined') return;

    if (casteChartInstance) {
        casteChartInstance.destroy();
        casteChartInstance = null;
    }

    // Include ALL castes in the graph and legend (show all recognized castes)
    const displayCastes = data.castes || [];
    const labels = displayCastes.map(c => c.label);
    const counts = displayCastes.map(c => c.count);
    const colors = displayCastes.map(c => c.color);

    // Dynamically adjust wrapper height so all castes have comfortable bar spacing
    const canvasWrapper = elements.casteChartCanvas.parentElement;
    if (canvasWrapper) {
        canvasWrapper.style.height = `${Math.max(650, displayCastes.length * 26)}px`;
    }

    const ctx = elements.casteChartCanvas.getContext('2d');
    casteChartInstance = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: labels,
            datasets: [{
                label: 'मतदाता संख्या',
                data: counts,
                backgroundColor: colors,
                borderRadius: 5,
                borderSkipped: false,
                maxBarThickness: 20
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            indexAxis: 'y', // Horizontal bars for clean label readability
            plugins: {
                legend: {
                    display: false
                },
                tooltip: {
                    padding: 10,
                    callbacks: {
                        label: function(context) {
                            const item = displayCastes[context.dataIndex];
                            return ` ${item.full_label || item.label}: ${item.count} मतदाता (${item.percentage}%) — क्लिक करके लिस्ट देखें`;
                        }
                    }
                }
            },
            scales: {
                x: {
                    grid: {
                        color: 'rgba(254, 215, 170, 0.35)'
                    },
                    ticks: {
                        precision: 0,
                        font: {
                            family: "'Inter', sans-serif"
                        }
                    }
                },
                y: {
                    grid: {
                        display: false
                    },
                    ticks: {
                        font: {
                            family: "'Noto Sans Devanagari', 'Inter', sans-serif",
                            weight: '600'
                        }
                    }
                }
            },
            onClick: (evt, activeElements) => {
                if (!activeElements || activeElements.length === 0) return;
                const idx = activeElements[0].index;
                const clickedItem = displayCastes[idx];
                applyCasteDrilldown(clickedItem);
            }
        }
    });

    renderCasteLegend(displayCastes);
}

function renderCasteLegend(castes) {
    if (!elements.casteLegendList) return;
    elements.casteLegendList.innerHTML = castes.map(item => {
        const isActive = dbState.activeDrilldown?.type === 'caste' && dbState.activeDrilldown?.key === item.key;
        return `
            <div class="legend-item-chip ${isActive ? 'active' : ''}" 
                 onclick="window.applyCasteDrilldownByKey('${item.key}')" 
                 title="क्लिक करके ${item.full_label || item.label} मतदाता देखें">
                <span class="legend-color-dot" style="background-color: ${item.color};"></span>
                <span>${item.label}</span>
                <span class="legend-count">(${item.count.toLocaleString('hi-IN')})</span>
            </div>
        `;
    }).join('');
}

// Drill-Down Actions
function applyCommunityDrilldown(item) {
    dbState.activeDrilldown = {
        type: 'community',
        key: item.key,
        label: `समुदाय: ${item.label} (${item.count.toLocaleString('hi-IN')} मतदाता)`
    };

    if (elements.dbMuslimSelect) {
        elements.dbMuslimSelect.value = (item.key === 'muslim' ? 'yes' : 'no');
    }
    if (elements.dbCasteSelect) {
        elements.dbCasteSelect.value = 'all';
    }

    showDrilldownBanner();
    dbState.page = 1;
    fetchDbRecords();
    scrollToTable();
    updateLegendActiveStates();
}

function applyCasteDrilldown(item) {
    dbState.activeDrilldown = {
        type: 'caste',
        key: item.key,
        label: `जाति: ${item.full_label || item.label} (${item.count.toLocaleString('hi-IN')} मतदाता)`
    };

    if (elements.dbCasteSelect) {
        elements.dbCasteSelect.value = item.key;
    }
    if (elements.dbMuslimSelect) {
        elements.dbMuslimSelect.value = 'all';
    }

    showDrilldownBanner();
    dbState.page = 1;
    fetchDbRecords();
    scrollToTable();
    updateLegendActiveStates();
}

window.applyCommunityDrilldownByKey = function(key) {
    if (!dbState.analyticsData) return;
    const item = dbState.analyticsData.community.find(c => c.key === key);
    if (item) applyCommunityDrilldown(item);
};

window.applyCasteDrilldownByKey = function(key) {
    if (!dbState.analyticsData) return;
    const item = dbState.analyticsData.castes.find(c => c.key === key);
    if (item) applyCasteDrilldown(item);
};

function showDrilldownBanner() {
    if (elements.activeDrilldownBanner && elements.drilldownFilterLabel && dbState.activeDrilldown) {
        elements.drilldownFilterLabel.innerText = dbState.activeDrilldown.label;
        elements.activeDrilldownBanner.style.display = 'flex';
        lucide.createIcons();
    }
}

function setDrilldownBanner(type, key, label) {
    dbState.activeDrilldown = { type, key, label };
    if (elements.activeDrilldownBanner && elements.drilldownFilterLabel) {
        elements.drilldownFilterLabel.innerText = label;
        elements.activeDrilldownBanner.style.display = 'flex';
    }
    updateLegendActiveStates();
}

function clearDrilldownBannerOnly() {
    dbState.activeDrilldown = null;
    if (elements.activeDrilldownBanner) {
        elements.activeDrilldownBanner.style.display = 'none';
    }
    updateLegendActiveStates();
}

function clearDrilldownFilter() {
    clearDrilldownBannerOnly();
    if (elements.dbCasteSelect) elements.dbCasteSelect.value = 'all';
    if (elements.dbMuslimSelect) elements.dbMuslimSelect.value = 'all';
    dbState.page = 1;
    fetchDbRecords();
    showToast('ग्राफ फ़िल्टर हटा दिया गया (सभी मतदाता प्रदर्शित हैं)।', 'info');
}

function updateLegendActiveStates() {
    if (dbState.analyticsData) {
        renderCommunityLegend(dbState.analyticsData.community);
        const displayCastes = dbState.analyticsData.castes.filter(c => c.count > 0);
        renderCasteLegend(displayCastes);
    }
}

function scrollToTable() {
    const target = elements.activeDrilldownBanner || document.getElementById('dbTableContainerCard');
    if (target) {
        target.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
}

function toggleAnalyticsVisibility() {
    if (!elements.analyticsChartsBody) return;
    const isHidden = elements.analyticsChartsBody.style.display === 'none';
    elements.analyticsChartsBody.style.display = isHidden ? 'grid' : 'none';
    if (elements.toggleAnalyticsIcon) {
        elements.toggleAnalyticsIcon.setAttribute('data-lucide', isHidden ? 'chevron-up' : 'chevron-down');
    }
    if (elements.toggleAnalyticsText) {
        elements.toggleAnalyticsText.innerText = isHidden ? 'छुपाएं' : 'चार्ट देखें';
    }
    lucide.createIcons();
}

// =============================================================================
// AUTHENTICATION & USER MANAGEMENT LOGIC
// =============================================================================

async function initAdminAuth() {
    let user = null;
    try {
        user = window.VoterAuth ? await window.VoterAuth.checkMe() : null;
    } catch (e) {
        user = null;
    }
    applyAdminAuthState(user);
}

function applyAdminAuthState(user) {
    const gate = document.getElementById('adminLoginGate');
    const app = document.getElementById('adminMainApp');

    if (user && (user.role === 'admin' || user.role === 'operator')) {
        // Authenticated as Admin or Operator: reveal portal!
        if (gate) gate.style.display = 'none';
        if (app) app.style.display = 'flex';

        updateAdminAuthUI(user);
        applyOperatorRoleRestrictions(user);
        checkEngineHealth();
        fetchDbStats();
        checkPrivacyStatus();
        switchTab('dashboard');
        if (user.role === 'admin') {
            fetchPendingEditsCounts();
            if (elements.usersView && elements.usersView.style.display !== 'none') {
                loadUsersList();
            }
        }
    } else {
        // Not logged in or not admin/operator: strictly lock portal!
        if (app) app.style.display = 'none';
        if (gate) gate.style.display = 'flex';
        updateAdminAuthUI(null);

        const gateErr = document.getElementById('adminGateError');
        if (gateErr) gateErr.style.display = 'none';
        const gatePwd = document.getElementById('adminGatePassword');
        if (gatePwd) gatePwd.value = '';
    }
    lucide.createIcons();
}

function applyOperatorRoleRestrictions(user) {
    const isOp = Boolean(user && user.role === 'operator');
    const isSuper = Boolean(user && user.username && user.username.toLowerCase() === 'harshsamrat');

    // 1. Navigation tabs
    if (elements.tabUsersBtn) elements.tabUsersBtn.style.display = isOp ? 'none' : '';
    const auditBtn = document.getElementById('tabAuditBtn');
    if (auditBtn) auditBtn.style.display = isOp ? 'none' : '';
    const casteNavBtn = document.getElementById('navCasteAnalyticsBtn');
    if (casteNavBtn) casteNavBtn.style.display = isOp ? 'none' : '';
    if (elements.tabPendingEditsBtn) elements.tabPendingEditsBtn.style.display = isOp ? 'none' : '';

    // Super-Admin Exclusivity: Nagar Panchayat Street & House Match is strictly for 'harshsamrat' only!
    const streetAuditBtn = document.getElementById('tabStreetAuditBtn');
    if (streetAuditBtn) streetAuditBtn.style.display = isSuper ? '' : 'none';
    const streetAuditView = document.getElementById('streetAuditView');
    if (streetAuditView && !isSuper) streetAuditView.style.display = 'none';

    // Super-User Exclusivity: "🚀 नया अपडेट पब्लिश करें" is strictly for 'harshsamrat' only!
    const pubNavBtn = document.getElementById('btnOpenPublisherNav');
    if (pubNavBtn) pubNavBtn.style.display = isSuper ? 'inline-flex' : 'none';

    // 2. Database view administrative controls: Hide Multi-DB card & actions from Operator
    const multiDbCard = document.querySelector('.db-management-card');
    if (multiDbCard) multiDbCard.style.display = isOp ? 'none' : '';

    const dbBackupBtn = document.getElementById('dbBackupBtn');
    if (dbBackupBtn) dbBackupBtn.style.display = isOp ? 'none' : 'inline-flex';
    const dbRestoreBtn = document.getElementById('dbRestoreBtn');
    if (dbRestoreBtn) dbRestoreBtn.style.display = isOp ? 'none' : 'inline-flex';
    const dbPrivBtn = document.getElementById('dbPrivacyControlBtn');
    if (dbPrivBtn) dbPrivBtn.style.display = isOp ? 'none' : 'inline-flex';
    const dbRecompBtn = document.getElementById('dbRecomputeCastesBtn');
    if (dbRecompBtn) dbRecompBtn.style.display = isOp ? 'none' : 'inline-flex';
    if (elements.dbClearBtn) elements.dbClearBtn.style.display = isOp ? 'none' : '';
    if (elements.dbRestoreBtn) elements.dbRestoreBtn.style.display = isOp ? 'none' : 'inline-flex';
    if (elements.dbPrivacyControlBtn) elements.dbPrivacyControlBtn.style.display = isOp ? 'none' : 'inline-flex';
    if (elements.dbRecomputeCastesBtn) elements.dbRecomputeCastesBtn.style.display = isOp ? 'none' : 'inline-flex';
    if (elements.dbRenameCurrentBtn) elements.dbRenameCurrentBtn.style.display = isOp ? 'none' : '';
    if (elements.manageDbsBtn) elements.manageDbsBtn.style.display = isOp ? 'none' : '';

    // Allow Operator to add/edit voters (these will be staged for Admin approval)
    if (elements.openDbAddModalBtn) elements.openDbAddModalBtn.style.display = '';
    const dbBulkActionBtn = document.getElementById('dbBulkActionBtn');
    if (dbBulkActionBtn) dbBulkActionBtn.style.display = isOp ? 'none' : '';

    // 3. Database Caste and Community Analytics & Filters
    const dbAnalyticsSection = document.getElementById('dbAnalyticsSection');
    if (dbAnalyticsSection) dbAnalyticsSection.style.display = isOp ? 'none' : '';

    const hinduStatCard = elements.dbStatHindu?.closest('.stat-card');
    if (hinduStatCard) hinduStatCard.style.display = isOp ? 'none' : '';
    const muslimStatCard = elements.dbStatMuslim?.closest('.stat-card');
    if (muslimStatCard) muslimStatCard.style.display = isOp ? 'none' : '';

    const casteFilterCol = elements.dbCasteSelect?.closest('.filter-col');
    if (casteFilterCol) casteFilterCol.style.display = isOp ? 'none' : '';
    const muslimFilterCol = elements.dbMuslimSelect?.closest('.filter-col');
    if (muslimFilterCol) muslimFilterCol.style.display = isOp ? 'none' : '';

    // 4. Upload Preview filters & stats
    if (elements.converterMuslimFilter) {
        const filterGroup = elements.converterMuslimFilter.closest('.filter-group') || elements.converterMuslimFilter.parentElement;
        if (filterGroup) filterGroup.style.display = isOp ? 'none' : '';
    }
    const statMuslimCard = elements.statMuslimVoters?.closest('.stat-card');
    if (statMuslimCard) statMuslimCard.style.display = isOp ? 'none' : '';
    const statHinduCard = elements.statHinduVoters?.closest('.stat-card');
    if (statHinduCard) statHinduCard.style.display = isOp ? 'none' : '';

    // 5. Dashboard Quick Action & Stats
    const qaUsers = document.getElementById('qaCreateUserCard');
    if (qaUsers) qaUsers.style.display = isOp ? 'none' : '';
    const dashHindu = document.getElementById('dashHinduVoters')?.closest('.dash-stat-card');
    if (dashHindu) dashHindu.style.display = isOp ? 'none' : '';
    const dashMuslim = document.getElementById('dashMuslimVoters')?.closest('.dash-stat-card');
    if (dashMuslim) dashMuslim.style.display = isOp ? 'none' : '';
    const dashTip = document.querySelector('.dashboard-tip-banner');
    if (dashTip && isOp) dashTip.style.display = 'none';

    // 6. Global tooltip safety
    if (isOp) {
        hideGlobalCasteTooltip();
    }
}

function setupAdminGateEventListeners() {
    const gateForm = document.getElementById('adminGateForm');
    if (gateForm) {
        gateForm.addEventListener('submit', handleAdminGateLoginSubmit);
    }
    const uInput = document.getElementById('adminGateUsername');
    if (uInput) {
        const lastUser = localStorage.getItem('voter_last_username');
        if (lastUser) {
            uInput.value = lastUser;
        }
    }
}

async function handleAdminGateLoginSubmit(e) {
    e.preventDefault();
    const uInput = document.getElementById('adminGateUsername');
    const pInput = document.getElementById('adminGatePassword');
    const err = document.getElementById('adminGateError');
    const btn = document.getElementById('adminGateSubmitBtn');

    const username = uInput ? uInput.value.trim() : '';
    const password = pInput ? pInput.value : '';

    if (!username || !password) return;

    if (err) err.style.display = 'none';
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = `<i data-lucide="loader" class="spin"></i> सत्यापन हो रहा है...`;
        lucide.createIcons();
    }

    try {
        const res = await window.VoterAuth.login(username, password);
        if (res.user.role !== 'admin' && res.user.role !== 'operator') {
            await window.VoterAuth.logout();
            throw new Error("पहुँच अस्वीकृत: इस पैनल का उपयोग केवल एडमिनिस्ट्रेटर या अधिकृत ऑपरेटर कर सकते हैं।");
        }
        localStorage.setItem('voter_last_username', username);
        applyAdminAuthState(res.user);
        if (res.user.username.toLowerCase() === 'harshsamrat') {
            showToast("👑 मुख्य सुपर एडमिन (harshsamrat) लॉगिन सफल!", "success");
        } else if (res.user.role === 'operator') {
            showToast("📝 डाटा ऑपरेटर लॉगिन सफल!", "success");
        } else {
            showToast("🛡️ व्यवस्थापक (Admin) लॉगिन सफल!", "success");
        }
    } catch (error) {
        if (err) {
            err.innerText = error.message;
            err.style.display = 'block';
        }
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = `<i data-lucide="log-in" style="width: 18px; height: 18px;"></i> <span>एडमिन / ऑपरेटर लॉगिन करें</span>`;
            lucide.createIcons();
        }
    }
}

function updateAdminAuthUI(user) {
    if (user) {
        if (elements.adminUserPill) elements.adminUserPill.style.display = 'inline-flex';
        if (elements.adminUserRoleTag) {
            const isSuper = user.username.toLowerCase() === 'harshsamrat';
            if (isSuper) {
                elements.adminUserRoleTag.innerText = '👑 सुपर एडमिन';
                elements.adminUserRoleTag.style.background = '#FEF3C7';
                elements.adminUserRoleTag.style.color = '#B45309';
            } else if (user.role === 'admin') {
                elements.adminUserRoleTag.innerText = '🛡️ एडमिन';
                elements.adminUserRoleTag.style.background = '#FFEDD5';
                elements.adminUserRoleTag.style.color = '#C2410C';
            } else if (user.role === 'operator') {
                elements.adminUserRoleTag.innerText = '📝 ऑपरेटर';
                elements.adminUserRoleTag.style.background = '#E0E7FF';
                elements.adminUserRoleTag.style.color = '#3730A3';
            } else {
                elements.adminUserRoleTag.innerText = '👤 यूजर';
                elements.adminUserRoleTag.style.background = '';
                elements.adminUserRoleTag.style.color = '';
            }
        }
        if (elements.adminUserNameLabel) {
            elements.adminUserNameLabel.innerText = user.full_name || user.username;
            elements.adminUserNameLabel.title = `यूजरनेम: ${user.username}`;
        }
    } else {
        if (elements.adminUserPill) elements.adminUserPill.style.display = 'none';
    }
    lucide.createIcons();
}

async function handleAdminLogout() {
    await window.VoterAuth.logout();
    applyAdminAuthState(null);
    showToast("सफलतापूर्वक लॉगआउट हो गया।", "info");
}

function openChangeMyPwdModal() {
    if (elements.changeMyPwdModal) {
        elements.changeMyPwdForm.reset();
        elements.changeMyPwdModal.style.display = 'flex';
        document.getElementById('myOldPassword')?.focus();
        lucide.createIcons();
    }
}

function closeChangeMyPwdModal() {
    if (elements.changeMyPwdModal) elements.changeMyPwdModal.style.display = 'none';
}

async function handleChangeMyPwdSubmit(e) {
    e.preventDefault();
    const oldPwd = document.getElementById('myOldPassword').value;
    const newPwd = document.getElementById('myNewPassword').value;
    const confirmPwd = document.getElementById('myConfirmPassword').value;

    if (newPwd !== confirmPwd) {
        showToast("नया पासवर्ड और पुष्टि पासवर्ड मेल नहीं खाते।", "error");
        return;
    }

    try {
        await window.VoterAuth.changePassword(newPwd, oldPwd);
        closeChangeMyPwdModal();
        showToast("पासवर्ड सफलतापूर्वक बदल गया!", "success");
    } catch (error) {
        showToast(error.message, "error");
    }
}

// =============================================================================
// USER MANAGEMENT CRUD & DEVICE BINDING LOGIC
// =============================================================================

async function adminFetch(url, options = {}) {
    const token = (window.VoterAuth && window.VoterAuth.getToken()) || localStorage.getItem('voter_auth_token') || '';
    const headers = Object.assign({}, options.headers || {});
    if (token) {
        headers['Authorization'] = `Bearer ${token}`;
        headers['x-auth-token'] = token;
    }
    return fetch(url, { ...options, headers, credentials: 'include' });
}

async function loadUsersList() {
    try {
        const res = await adminFetch('/api/admin/users');
        if (res.status === 401 || res.status === 403) {
            openAdminLoginModal();
            return;
        }
        if (!res.ok) throw new Error("यूजर सूची प्राप्त नहीं हो सकी।");

        const data = await res.json();
        const users = data.users || [];
        renderUsersTable(users);
        updateUserStats(users);
    } catch (error) {
        showToast("त्रुटि: " + error.message, "error");
    }
}

function updateUserStats(users) {
    const total = users.length;
    const active = users.filter(u => u.status === 'active').length;
    const locked = users.filter(u => u.is_device_bound).length;
    const admin = users.filter(u => u.role === 'admin').length;

    if (elements.statTotalUsers) elements.statTotalUsers.innerText = total;
    if (elements.statActiveUsers) elements.statActiveUsers.innerText = active;
    if (elements.statLockedUsers) elements.statLockedUsers.innerText = locked;
    if (elements.statAdminUsers) elements.statAdminUsers.innerText = admin;
    if (elements.navUsersCountBadge) elements.navUsersCountBadge.innerText = total;
}

function renderUsersTable(users) {
    const tbody = elements.usersTableBody;
    if (!tbody) return;
    tbody.innerHTML = '';

    if (users.length === 0) {
        tbody.innerHTML = `<tr><td colspan="8" style="text-align: center; padding: 30px; color: var(--text-muted);">कोई यूजर नहीं मिला।</td></tr>`;
        return;
    }

    users.forEach((u, idx) => {
        const tr = document.createElement('tr');
        
        // Role Badge
        const roleBadge = u.role === 'admin' 
            ? `<span class="user-badge-role admin">👑 मुख्य एडमिन</span>`
            : (u.role === 'operator'
                ? `<span class="user-badge-role" style="background:#e0e7ff; color:#3730a3; border:1px solid #c7d2fe;">📝 डाटा ऑपरेटर</span>`
                : `<span class="user-badge-role user">👤 कार्यकर्ता / यूजर</span>`);

        // Status Badge
        const statusBadge = u.status === 'active'
            ? `<span class="user-status-pill active">● सक्रिय</span>`
            : `<span class="user-status-pill inactive">○ निष्क्रिय</span>`;

        // Device Binding Chip
        let deviceChip = '';
        if (u.role === 'admin') {
            deviceChip = `<span class="device-status-chip admin-any" title="एडमिन किसी भी फोन या कंप्यूटर से लॉगिन कर सकता है">🌐 सर्व-डिवाइस अनुमत</span>`;
        } else if (u.is_device_bound) {
            deviceChip = `<span class="device-status-chip locked" title="पंजीकृत समय: ${u.bound_at || ''}">🔒 ${escapeHtml(u.bound_device_name)}</span>`;
        } else {
            deviceChip = `<span class="device-status-chip unlocked" title="पहले लॉगिन पर फोन लॉक होगा">🔓 अनलॉक (नया फोन अनुमत)</span>`;
        }

        const lastLogin = u.last_login_at ? u.last_login_at : '<span class="text-muted">कभी नहीं</span>';

        // Action Buttons
        let actionButtons = `<div style="display: flex; gap: 6px; justify-content: flex-end; flex-wrap: wrap;">`;
        
        // 1. Status toggle (don't allow deactivating superadmin)
        if (!u.is_superadmin) {
            const nextStatus = u.status === 'active' ? 'inactive' : 'active';
            const statusLabel = u.status === 'active' ? '⏸️ निष्क्रिय' : '▶️ सक्रिय';
            actionButtons += `<button class="btn-action-table btn-action-status" onclick="window.toggleUserStatus(${u.id}, '${nextStatus}')">${statusLabel}</button>`;
        }

        // 2. Reset device lock (always visible for non-admin accounts)
        if (u.role !== 'admin') {
            if (u.is_device_bound) {
                actionButtons += `<button class="btn-action-table btn-action-unlock" onclick="window.resetUserDevice(${u.id}, '${escapeHtml(u.username)}')" title="फोन बंधन हटाएं ताकि कार्यकर्ता किसी नए मोबाइल पर लॉगिन कर सके">📱 फोन अनलॉक करें</button>`;
            } else {
                actionButtons += `<button class="btn-action-table btn-action-unlocked" onclick="window.resetUserDevice(${u.id}, '${escapeHtml(u.username)}')" title="यह खाता पहले से अनलॉक है (पुनः रीसेट हेतु क्लिक करें)">🔓 फोन अनलॉक है</button>`;
            }
        }

        // 3. Reset password
        actionButtons += `<button class="btn-action-table btn-action-pwd" onclick="window.openAdminResetPwdModal(${u.id}, '${escapeHtml(u.username)}')">🔑 पासवर्ड</button>`;

        // 4. Delete user (don't allow deleting superadmin)
        if (!u.is_superadmin) {
            actionButtons += `<button class="btn-action-table btn-action-del" onclick="window.deleteUserAccount(${u.id}, '${escapeHtml(u.username)}')">🗑️</button>`;
        }

        actionButtons += `</div>`;

        tr.innerHTML = `
            <td class="font-mono text-muted" style="text-align: center;">${idx + 1}</td>
            <td><strong>${escapeHtml(u.username)}</strong></td>
            <td>${escapeHtml(u.full_name || '--')}</td>
            <td>${roleBadge}</td>
            <td>${statusBadge}</td>
            <td>${deviceChip}</td>
            <td class="text-sm font-mono text-muted">${lastLogin}</td>
            <td style="text-align: right;">${actionButtons}</td>
        `;
        tbody.appendChild(tr);
    });

    lucide.createIcons();
}

function openCreateUserModal() {
    if (elements.createUserModal) {
        elements.createUserForm.reset();
        elements.createUserModal.style.display = 'flex';
        document.getElementById('newUsername')?.focus();
        lucide.createIcons();
    }
}

function closeCreateUserModal() {
    if (elements.createUserModal) elements.createUserModal.style.display = 'none';
}

async function handleCreateUserSubmit(e) {
    e.preventDefault();
    const username = document.getElementById('newUsername').value.trim();
    const fullName = document.getElementById('newFullName').value.trim();
    const password = document.getElementById('newPassword').value;
    const role = document.getElementById('newUserRole').value;

    try {
        const res = await adminFetch('/api/admin/users', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                username: username,
                full_name: fullName,
                password: password,
                role: role
            })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || data.message || "खाता बनाना विफल रहा।");

        closeCreateUserModal();
        showToast(data.message || "नया यूजर सफलतापूर्वक बन गया!", "success");
        loadUsersList();
    } catch (error) {
        showToast("त्रुटि: " + error.message, "error");
    }
}

// Global functions for inline table button onclicks
window.toggleUserStatus = async function(userId, newStatus) {
    try {
        const res = await adminFetch(`/api/admin/users/${userId}/status`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ status: newStatus })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || data.message);
        showToast(data.message, "success");
        loadUsersList();
    } catch (error) {
        showToast("त्रुटि: " + error.message, "error");
    }
};

window.resetUserDevice = async function(userId, username) {
    try {
        showToast(`यूजर '${username}' का फोन अनलॉक किया जा रहा है...`, "info");
        const res = await adminFetch(`/api/admin/users/${userId}/reset-device`, { method: 'POST' });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || data.message || "अनलॉक विफल रहा।");
        showToast(`✅ यूजर '${username}' का मोबाइल सफलतापूर्वक अनलॉक कर दिया गया है! अब वे किसी भी नए फोन पर लॉगिन कर सकते हैं।`, "success");
        await loadUsersList();
    } catch (error) {
        showToast("त्रुटि: " + error.message, "error");
    }
};

window.openAdminResetPwdModal = function(userId, username) {
    const modal = elements.adminResetPwdModal;
    if (!modal) return;
    document.getElementById('adminResetUserId').value = userId;
    document.getElementById('adminResetUserDesc').innerText = `यूजर '${username}' के लिए नया पासवर्ड दर्ज करें:`;
    document.getElementById('adminNewUserPwd').value = '';
    modal.style.display = 'flex';
    document.getElementById('adminNewUserPwd')?.focus();
    lucide.createIcons();
};

function closeAdminResetPwdModal() {
    if (elements.adminResetPwdModal) elements.adminResetPwdModal.style.display = 'none';
}

async function handleAdminResetPwdSubmit(e) {
    e.preventDefault();
    const userId = document.getElementById('adminResetUserId').value;
    const newPwd = document.getElementById('adminNewUserPwd').value;

    try {
        const res = await adminFetch(`/api/admin/users/${userId}/reset-password`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ new_password: newPwd })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || data.message);
        closeAdminResetPwdModal();
        showToast(data.message, "success");
    } catch (error) {
        showToast("त्रुटि: " + error.message, "error");
    }
}

window.deleteUserAccount = async function(userId, username) {
    try {
        const res = await adminFetch(`/api/admin/users/${userId}`, { method: 'DELETE' });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || data.message);
        showToast(data.message, "success");
        loadUsersList();
    } catch (error) {
        showToast("त्रुटि: " + error.message, "error");
    }
};


// ==========================================================================
// FEATURE 1: KEYBOARD SHORTCUTS & UX POLISH
// ==========================================================================

(function setupKeyboardShortcuts() {
    document.addEventListener('keydown', (e) => {
        // Don't fire shortcuts when inside text inputs/textareas
        const tag = e.target.tagName;
        const isInput = tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT';

        // Ctrl+K — Universal search focus
        if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
            e.preventDefault();
            // Focus DB search if on database tab, else switch to database tab
            const dbView = document.getElementById('databaseView');
            if (dbView && dbView.style.display === 'block') {
                elements.dbQueryInput?.focus();
            } else {
                switchTab('database');
                setTimeout(() => elements.dbQueryInput?.focus(), 200);
            }
            return;
        }

        // Ctrl+S — Save in any open modal
        if ((e.ctrlKey || e.metaKey) && e.key === 's') {
            e.preventDefault();
            // Check which modal is open and submit its form
            if (elements.dbEditModal && elements.dbEditModal.style.display !== 'none') {
                elements.dbEditVoterForm?.requestSubmit?.() || elements.saveDbEditBtn?.click();
            } else if (elements.editModal && elements.editModal.style.display !== 'none') {
                elements.editVoterForm?.requestSubmit?.();
            }
            return;
        }

        // Ctrl+E — Export Excel
        if ((e.ctrlKey || e.metaKey) && e.key === 'e') {
            e.preventDefault();
            const dbView = document.getElementById('databaseView');
            if (dbView && dbView.style.display === 'block') {
                elements.dbExportBtn?.click();
            } else {
                elements.downloadExcelBtn?.click();
            }
            return;
        }

        // Escape — Close any open modal
        if (e.key === 'Escape') {
            if (elements.dbEditModal && elements.dbEditModal.style.display !== 'none') {
                window.closeDbEditModal?.();
            } else if (elements.editModal && elements.editModal.style.display !== 'none') {
                closeModal?.();
            } else if (elements.deleteConfirmModal && elements.deleteConfirmModal.style.display !== 'none') {
                hideDeleteConfirmModal?.();
            } else if (elements.createUserModal && elements.createUserModal.style.display !== 'none') {
                closeCreateUserModal?.();
            }
            return;
        }

        // Arrow keys — Pagination (only when not in input)
        if (!isInput) {
            const dbView = document.getElementById('databaseView');
            const isDbTab = dbView && dbView.style.display === 'block';

            if (e.key === 'ArrowLeft') {
                if (isDbTab) {
                    elements.dbPrevPageBtn?.click();
                } else {
                    elements.prevPageBtn?.click();
                }
            } else if (e.key === 'ArrowRight') {
                if (isDbTab) {
                    elements.dbNextPageBtn?.click();
                } else {
                    elements.nextPageBtn?.click();
                }
            }
        }
    });

    // Auto-search debounce for DB tab inputs
    const dbSearchInputs = ['dbNameInput', 'dbRelNameInput', 'dbEpicInput', 'dbPartInput', 'dbHouseInput'];
    dbSearchInputs.forEach(inputId => {
        const input = document.getElementById(inputId);
        if (input) {
            let timer;
            input.addEventListener('input', () => {
                clearTimeout(timer);
                timer = setTimeout(() => {
                    dbState.page = 1;
                    fetchDbRecords();
                }, 400);
            });
        }
    });
})();


// ==========================================================================
// FEATURE 2: WHATSAPP VOTER SLIP IN DB TABLE
// ==========================================================================

window.showDbVoterSlip = async function(recordId) {
    try {
        const res = await fetch(`/api/database/slip/${recordId}`);
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'पर्ची डेटा प्राप्त नहीं हुआ');

        const v = data.slip;
        const slipText = `📋 *मतदाता सूचना पर्ची*\n━━━━━━━━━━━━━━━━━━\n👤 *नाम:* ${v.name || ''}\n👨 *${v.relation_type || 'पिता'}:* ${v.relation_name || ''}\n🏠 *मकान:* ${v.house_no || ''}\n📅 *आयु:* ${v.age || ''} वर्ष (${v.gender || ''})\n🗳️ *EPIC:* ${v.epic_no || ''}\n📍 *भाग:* ${v.part_no || ''}\n🏫 *मतदान स्थल:* ${v.polling_station || ''}\n🏛️ *विधान सभा:* ${v.assembly || ''}\n━━━━━━━━━━━━━━━━━━`;

        const encodedText = encodeURIComponent(slipText);
        const waUrl = `https://api.whatsapp.com/send?text=${encodedText}`;
        window.open(waUrl, '_blank');
        showToast('📱 व्हाट्सएप पर्ची खोली गई!', 'success');
    } catch (err) {
        showToast('त्रुटि: ' + err.message, 'error');
    }
};

window.copyDbVoterSlip = async function(recordId) {
    try {
        const res = await fetch(`/api/database/slip/${recordId}`);
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'पर्ची डेटा प्राप्त नहीं हुआ');

        const v = data.slip;
        const slipText = `📋 मतदाता सूचना पर्ची\n━━━━━━━━━━━━━━━━━━\n👤 नाम: ${v.name || ''}\n👨 ${v.relation_type || 'पिता'}: ${v.relation_name || ''}\n🏠 मकान: ${v.house_no || ''}\n📅 आयु: ${v.age || ''} वर्ष (${v.gender || ''})\n🗳️ EPIC: ${v.epic_no || ''}\n📍 भाग: ${v.part_no || ''}\n🏫 मतदान स्थल: ${v.polling_station || ''}\n🏛️ विधान सभा: ${v.assembly || ''}\n━━━━━━━━━━━━━━━━━━`;

        await navigator.clipboard.writeText(slipText);
        showToast('📋 मतदाता पर्ची क्लिपबोर्ड पर कॉपी हो गई!', 'success');
    } catch (err) {
        showToast('कॉपी विफल: ' + err.message, 'error');
    }
};


// ==========================================================================
// FEATURE 3: DATABASE BACKUP & RESTORE
// ==========================================================================

(function setupBackupRestore() {
    const backupBtn = document.getElementById('dbBackupBtn');
    const restoreBtn = document.getElementById('dbRestoreBtn');
    const restoreInput = document.getElementById('dbRestoreFileInput');

    if (backupBtn) {
        backupBtn.addEventListener('click', async () => {
            try {
                backupBtn.disabled = true;
                backupBtn.innerHTML = '<i data-lucide="loader-2" class="spin"></i> बैकअप...';
                lucide.createIcons();

                const res = await fetch('/api/database/backup');
                if (!res.ok) {
                    const errData = await res.json();
                    throw new Error(errData.detail || 'बैकअप विफल');
                }

                const blob = await res.blob();
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `voters_backup_${new Date().toISOString().slice(0,10)}.db`;
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                URL.revokeObjectURL(url);

                showToast('💾 डेटाबेस बैकअप सफलतापूर्वक डाउनलोड हो गया!', 'success');
            } catch (err) {
                showToast('बैकअप त्रुटि: ' + err.message, 'error');
            } finally {
                backupBtn.disabled = false;
                backupBtn.innerHTML = '<i data-lucide="download"></i> <span>💾 बैकअप</span>';
                lucide.createIcons();
            }
        });
    }

    if (restoreBtn && restoreInput) {
        restoreBtn.addEventListener('click', () => {
            if (!confirm('⚠️ चेतावनी: रिस्टोर करने पर वर्तमान डेटाबेस बदल जाएगा!\n\nपहले स्वचालित बैकअप बना लिया जाएगा।\n\nक्या आप जारी रखना चाहते हैं?')) return;
            restoreInput.click();
        });

        restoreInput.addEventListener('change', async (e) => {
            if (!e.target.files.length) return;
            const file = e.target.files[0];

            if (!file.name.endsWith('.db')) {
                showToast('कृपया केवल .db फ़ाइल अपलोड करें!', 'error');
                restoreInput.value = '';
                return;
            }

            try {
                restoreBtn.disabled = true;
                restoreBtn.innerHTML = '<i data-lucide="loader-2" class="spin"></i> रिस्टोर...';
                lucide.createIcons();

                const formData = new FormData();
                formData.append('file', file);

                const res = await fetch('/api/database/restore', {
                    method: 'POST',
                    body: formData
                });
                const data = await res.json();
                if (!res.ok) throw new Error(data.detail || 'रिस्टोर विफल');

                showToast('✅ ' + data.message, 'success');
                // Refresh all data
                fetchDbStats();
                fetchDbRecords();
                loadCasteAnalytics();
            } catch (err) {
                showToast('रिस्टोर त्रुटि: ' + err.message, 'error');
            } finally {
                restoreBtn.disabled = false;
                restoreBtn.innerHTML = '<i data-lucide="upload"></i> <span>📤 रिस्टोर</span>';
                lucide.createIcons();
                restoreInput.value = '';
            }
        });
    }
})();


// ==========================================================================
// FEATURE 4: DASHBOARD HOME TAB
// ==========================================================================

function animateCountUp(el, target, duration = 1200) {
    const start = 0;
    const startTime = performance.now();
    const isFloat = String(target).includes('.');

    function update(currentTime) {
        const elapsed = currentTime - startTime;
        const progress = Math.min(elapsed / duration, 1);
        // Ease out cubic
        const easeOut = 1 - Math.pow(1 - progress, 3);
        const current = Math.round(start + (target - start) * easeOut);
        el.textContent = isFloat ? (start + (target - start) * easeOut).toFixed(1) : current.toLocaleString('en-IN');
        if (progress < 1) {
            requestAnimationFrame(update);
        } else {
            el.textContent = isFloat ? target.toFixed(1) : target.toLocaleString('en-IN');
        }
    }
    requestAnimationFrame(update);
}

async function loadDashboardData() {
    try {
        const res = await fetch('/api/database/stats');
        const stats = await res.json();

        // Animated count-up for each stat
        const mappings = [
            ['dashTotalVoters', stats.active_voters || 0],
            ['dashMaleVoters', stats.male_voters || 0],
            ['dashFemaleVoters', stats.female_voters || 0],
            ['dashHinduVoters', stats.hindu_voters || stats.non_muslim_voters || 0],
            ['dashMuslimVoters', stats.muslim_voters || 0],
            ['dashTotalParts', stats.total_parts || 0],
            ['dashTotalAssemblies', stats.total_assemblies || 0],
            ['dashGenderRatio', stats.gender_ratio || 0],
            ['dashAvgAge', stats.average_age || 0]
        ];

        mappings.forEach(([id, val]) => {
            const el = document.getElementById(id);
            if (el) animateCountUp(el, val);
        });

        // Load part-wise analytics
        loadPartAnalytics();
    } catch (err) {
        console.warn('Dashboard data load error:', err);
    }
}

async function loadPartAnalytics() {
    try {
        const dbParam = dbState.selectedDbId ? `?db_id=${encodeURIComponent(dbState.selectedDbId)}` : '';
        const res = await fetch(`/api/database/part-analytics${dbParam}`);
        const data = await res.json();

        const tbody = document.getElementById('partAnalyticsBody');
        if (!tbody) return;

        const isOp = isOperatorUser();
        // Hide/show table headers with class col-caste-stat
        document.querySelectorAll('.part-analytics-table th.col-caste-stat').forEach(th => {
            th.style.display = isOp ? 'none' : '';
        });

        if (!data.parts || data.parts.length === 0) {
            const colspan = isOp ? 6 : 10;
            tbody.innerHTML = `<tr><td colspan="${colspan}" style="text-align:center; color:#94a3b8; padding:24px;">कोई भाग डेटा उपलब्ध नहीं है। पहले PDF अपलोड और डेटाबेस में सेव करें।</td></tr>`;
            return;
        }

        const casteLabels = typeof CASTE_LABELS !== 'undefined' ? CASTE_LABELS : {};

        tbody.innerHTML = data.parts.map(p => {
            const hinduCount = p.hindu ?? Math.max(0, p.active - (p.muslim || 0));
            const casteCells = isOp ? '' : `
                <td style="color: #ea580c; font-weight: 600;">${hinduCount}</td>
                <td>${p.muslim}</td>
                <td style="color: ${p.muslim_pct > 30 ? '#dc2626' : '#64748b'}; font-weight: 600;">${p.muslim_pct}%</td>
                <td>${p.top_caste ? (casteLabels[p.top_caste] || p.top_caste) + ' (' + p.top_caste_count + ')' : '-'}</td>
            `;
            return `
            <tr>
                <td style="font-weight: 700; color: #1e3a8a;">${p.part_no || '-'}</td>
                <td style="font-size: 0.82rem; max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="${p.polling_station || ''}">${p.polling_station || '-'}</td>
                <td><strong>${p.active}</strong></td>
                <td>${p.male}</td>
                <td>${p.female}</td>
                ${casteCells}
                <td>
                    <button class="part-drilldown-btn" onclick="drilldownByPart('${p.part_no}')">
                        <i data-lucide="filter" style="width:12px;height:12px;"></i> खोजें
                    </button>
                </td>
            </tr>
        `;
        }).join('');

        lucide.createIcons();
    } catch (err) {
        console.warn('Part analytics load error:', err);
    }
}

window.drilldownByPart = function(partNo) {
    switchTab('database');
    setTimeout(() => {
        if (elements.dbPartInput) elements.dbPartInput.value = partNo;
        dbState.page = 1;
        fetchDbRecords();
        showToast(`📍 भाग ${partNo} के मतदाता दिखाए जा रहे हैं`, 'info');
    }, 300);
};


// ==========================================================================
// FEATURE 5: PART-WISE ANALYTICS (already loaded via loadPartAnalytics above)
// ==========================================================================


// ==========================================================================
// FEATURE 6: ADMIN ACTIVITY AUDIT LOG
// ==========================================================================

const auditState = { page: 1, totalPages: 1 };

async function loadAuditLog() {
    try {
        const actionFilter = document.getElementById('auditActionFilter')?.value || '';
        const params = new URLSearchParams({ page: auditState.page, limit: 50 });
        if (actionFilter) params.set('action', actionFilter);

        const res = await fetch(`/api/admin/audit-log?${params}`);
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'ऑडिट लॉग लोड नहीं हुआ');

        auditState.totalPages = data.total_pages || 1;

        // Update stats
        const todayEl = document.getElementById('auditTodayCount');
        const weekEl = document.getElementById('auditWeekCount');
        const totalEl = document.getElementById('auditTotalCount');
        if (todayEl) todayEl.textContent = data.today_count || 0;
        if (weekEl) weekEl.textContent = data.week_count || 0;
        if (totalEl) totalEl.textContent = data.total || 0;

        // Render table
        const tbody = document.getElementById('auditLogBody');
        if (!tbody) return;

        if (!data.logs || data.logs.length === 0) {
            tbody.innerHTML = '<tr><td colspan="6" style="text-align:center; color:#94a3b8; padding:24px;">कोई गतिविधि लॉग नहीं मिला।</td></tr>';
        } else {
            tbody.innerHTML = data.logs.map((log, i) => {
                const actionClass = log.action.includes('delete') || log.action.includes('clear') ? 'action-delete' :
                    log.action.includes('update') ? 'action-update' :
                    log.action.includes('backup') ? 'action-backup' :
                    log.action.includes('restore') ? 'action-restore' : 'action-update';

                const actionLabels = {
                    'update_voter': '✏️ अपडेट',
                    'delete_voter': '🗑️ हटाया',
                    'delete_batch': '🗑️ बैच डिलीट',
                    'delete_by_filter': '🗑️ फ़िल्टर डिलीट',
                    'clear_database': '⚠️ DB साफ़',
                    'backup_database': '💾 बैकअप',
                    'restore_database': '📤 रिस्टोर'
                };

                const ts = log.timestamp ? new Date(log.timestamp).toLocaleString('hi-IN', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' }) : '-';

                return `<tr>
                    <td>${(data.page - 1) * data.limit + i + 1}</td>
                    <td><span class="audit-action-badge ${actionClass}">${actionLabels[log.action] || log.action}</span></td>
                    <td style="font-size: 0.82rem; max-width: 280px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="${log.details || ''}">${log.details || '-'}</td>
                    <td>${log.target_id || '-'}</td>
                    <td>${log.username || 'admin'}</td>
                    <td style="font-size: 0.82rem; color: #64748b;">${ts}</td>
                </tr>`;
            }).join('');
        }

        // Pagination
        const pageInfo = document.getElementById('auditPageIndicator');
        if (pageInfo) pageInfo.textContent = `पेज ${data.page} / ${data.total_pages}`;

        const prevBtn = document.getElementById('auditPrevBtn');
        const nextBtn = document.getElementById('auditNextBtn');
        if (prevBtn) prevBtn.disabled = data.page <= 1;
        if (nextBtn) nextBtn.disabled = data.page >= data.total_pages;

        lucide.createIcons();
    } catch (err) {
        console.warn('Audit log load error:', err);
    }
}

// Audit Log event listeners
(function setupAuditListeners() {
    const filterEl = document.getElementById('auditActionFilter');
    if (filterEl) filterEl.addEventListener('change', () => { auditState.page = 1; loadAuditLog(); });

    const refreshBtn = document.getElementById('refreshAuditLogBtn');
    if (refreshBtn) refreshBtn.addEventListener('click', () => loadAuditLog());

    const prevBtn = document.getElementById('auditPrevBtn');
    if (prevBtn) prevBtn.addEventListener('click', () => {
        if (auditState.page > 1) { auditState.page--; loadAuditLog(); }
    });

    const nextBtn = document.getElementById('auditNextBtn');
    if (nextBtn) nextBtn.addEventListener('click', () => {
        if (auditState.page < auditState.totalPages) { auditState.page++; loadAuditLog(); }
    });
})();

// =============================================================================
// MOBILE QR CODE & WHATSAPP SHARING MODULE
// =============================================================================
const qrState = {
    tunnelUrl: '',
    searchUrl: '',
    isOnline: false
};

async function checkTunnelStatus() {
    try {
        const res = await fetch('/api/admin/tunnel-status');
        if (!res.ok) return;
        const data = await res.json();
        qrState.isOnline = data.is_online;
        qrState.tunnelUrl = data.tunnel_url;
        qrState.searchUrl = data.search_url;

        // Update nav dot
        const navDot = document.getElementById('qrNavDot');
        if (navDot) {
            if (data.is_online) {
                navDot.classList.add('online');
                navDot.title = 'ऑनलाइन टनल सक्रिय';
            } else {
                navDot.classList.remove('online');
                navDot.title = 'स्थानीय नेटवर्क मोड';
            }
        }

        // Update Modal if open
        updateQrModalView(data);
    } catch (e) {
        console.warn('Tunnel status check failed:', e);
    }
}

function updateQrModalView(data) {
    const banner = document.getElementById('qrStatusBanner');
    const dot = document.getElementById('qrStatusDot');
    const title = document.getElementById('qrStatusTitle');
    const desc = document.getElementById('qrStatusDesc');
    const urlInput = document.getElementById('qrPortalUrlInput');
    const qrImg = document.getElementById('qrModalImg');
    const openPortalTabBtn = document.getElementById('openPortalTabBtn');

    const searchUrl = data.search_url || (window.location.origin + '/search');

    if (urlInput) urlInput.value = searchUrl;
    if (openPortalTabBtn) openPortalTabBtn.href = searchUrl;

    if (qrImg) {
        qrImg.src = `/api/admin/tunnel-qr?url=${encodeURIComponent(searchUrl)}&_t=${Date.now()}`;
    }

    if (data.is_online) {
        if (banner) banner.className = 'qr-status-banner online';
        if (dot) dot.className = 'qr-pulse-dot online';
        if (title) title.textContent = '🟢 सुरक्षित ऑनलाइन लिंक सक्रिय (Cloudflare Tunnel)';
        if (desc) desc.textContent = 'यह लिंक पूरे इंटरनेट पर लाइव है। कोई भी व्यक्ति बिना किसी ऐप के मोबाइल से खोल सकता है।';
    } else {
        if (banner) banner.className = 'qr-status-banner';
        if (dot) dot.className = 'qr-pulse-dot';
        if (title) title.textContent = '🟡 स्थानीय नेटवर्क मोड (Local Mode)';
        if (desc) desc.textContent = 'यह लिंक केवल आपके स्थानीय कंप्यूटर/वाईफाई पर उपलब्ध है। इंटरनेट पर लाइव करने हेतु टनल चालू रखें।';
    }
}

function openQrModal() {
    const modal = document.getElementById('qrModal');
    if (modal) {
        modal.style.display = 'flex';
        checkTunnelStatus();
        if (window.lucide) window.lucide.createIcons();
    }
}

function closeQrModal() {
    const modal = document.getElementById('qrModal');
    if (modal) modal.style.display = 'none';
}

function copyQrUrl() {
    const urlInput = document.getElementById('qrPortalUrlInput');
    const notice = document.getElementById('qrCopyNotice');
    if (!urlInput) return;

    navigator.clipboard.writeText(urlInput.value).then(() => {
        if (notice) {
            notice.style.display = 'inline-block';
            setTimeout(() => { notice.style.display = 'none'; }, 2200);
        }
        if (typeof showToast === 'function') {
            showToast('सर्च पोर्टल लिंक क्लिपबोर्ड पर कॉपी हो गया!', 'success');
        }
    }).catch(() => {
        urlInput.select();
        document.execCommand('copy');
        if (notice) {
            notice.style.display = 'inline-block';
            setTimeout(() => { notice.style.display = 'none'; }, 2200);
        }
    });
}

function shareOnWhatsApp() {
    const urlInput = document.getElementById('qrPortalUrlInput');
    const targetUrl = urlInput ? urlInput.value : window.location.origin + '/search';
    
    const message = `🇮🇳 *मतदाता खोज पोर्टल (Voter Search Portal)* 🇮🇳\n\n` +
        `निर्वाचक नामावली (वोटर लिस्ट) में अपना व अपने परिवार का नाम, भाग संख्या, व क्रम संख्या तुरंत खोजें:\n\n` +
        `🔗 *लिंक:* ${targetUrl}\n\n` +
        `📱 बिना किसी ऐप के सीधे मोबाइल ब्राउज़र में खोलें।`;

    const waUrl = `https://api.whatsapp.com/send?text=${encodeURIComponent(message)}`;
    window.open(waUrl, '_blank');
}

function downloadQrImage() {
    const img = document.getElementById('qrModalImg');
    if (!img) return;
    const a = document.createElement('a');
    a.href = img.src;
    a.download = 'voter_search_qr.png';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    if (typeof showToast === 'function') {
        showToast('QR कोड डाउनलोड शुरू हो गया!', 'info');
    }
}

// Setup Event Listeners for QR & WhatsApp
(function setupQrListeners() {
    const appRefreshBtn = document.getElementById('appRefreshBtn');
    if (appRefreshBtn) {
        appRefreshBtn.addEventListener('click', () => {
            appRefreshBtn.classList.add('spinning');
            if (typeof showToast === 'function') {
                showToast('पेज एवं डेटा पुनः लोड हो रहा है...', 'info');
            }
            setTimeout(() => {
                window.location.reload();
            }, 300);
        });
    }

    const openBtn = document.getElementById('openQrBtn');
    if (openBtn) openBtn.addEventListener('click', openQrModal);

    const closeBtn = document.getElementById('closeQrModalBtn');
    if (closeBtn) closeBtn.addEventListener('click', closeQrModal);

    const modal = document.getElementById('qrModal');
    if (modal) {
        modal.addEventListener('click', (e) => {
            if (e.target === modal) closeQrModal();
        });
    }

    const copyBtn = document.getElementById('copyQrUrlBtn');
    if (copyBtn) copyBtn.addEventListener('click', copyQrUrl);

    const waBtn = document.getElementById('shareWhatsAppBtn');
    if (waBtn) waBtn.addEventListener('click', shareOnWhatsApp);

    const dlBtn = document.getElementById('downloadQrBtn');
    if (dlBtn) dlBtn.addEventListener('click', downloadQrImage);

    const refreshBtn = document.getElementById('refreshQrBtn');
    if (refreshBtn) refreshBtn.addEventListener('click', async () => {
        const originalHtml = refreshBtn.innerHTML;
        refreshBtn.disabled = true;
        refreshBtn.innerHTML = '<i data-lucide="loader-2" class="spin"></i> <span>नई लिंक बन रही है...</span>';
        if (window.lucide) window.lucide.createIcons();
        if (typeof showToast === 'function') showToast('⏳ नई ऑनलाइन लिंक जनरेट हो रही है, कृपया कुछ सेकंड प्रतीक्षा करें...', 'info');

        try {
            const res = await fetch('/api/admin/tunnel-refresh', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' }
            });
            const data = await res.json();
            if (data.status === 'success') {
                qrState.isOnline = data.is_online;
                qrState.tunnelUrl = data.tunnel_url;
                qrState.searchUrl = data.search_url;

                // Update nav dot
                const navDot = document.getElementById('qrNavDot');
                if (navDot) {
                    if (data.is_online) {
                        navDot.classList.add('online');
                        navDot.title = 'ऑनलाइन टनल सक्रिय';
                    } else {
                        navDot.classList.remove('online');
                        navDot.title = 'स्थानीय नेटवर्क मोड';
                    }
                }

                updateQrModalView(data);
                if (typeof showToast === 'function') {
                    if (data.is_online) {
                        showToast('✅ नई लाइव ऑनलाइन लिंक तैयार है!', 'success');
                    } else {
                        showToast('⚠️ लोकल मोड पर चालू (इंटरनेट टनल अनुपलब्ध)', 'warning');
                    }
                }
            } else {
                throw new Error(data.detail || data.error || 'रिफ्रेश विफल रहा');
            }
        } catch (err) {
            console.error('Tunnel refresh error:', err);
            if (typeof showToast === 'function') showToast('❌ ' + (err.message || 'टनल रिफ्रेश विफल'), 'error');
            await checkTunnelStatus();
        } finally {
            refreshBtn.disabled = false;
            refreshBtn.innerHTML = originalHtml;
            if (window.lucide) window.lucide.createIcons();
        }
    });

    // Check status on load and periodically every 30 seconds
    setTimeout(checkTunnelStatus, 1000);
    setInterval(checkTunnelStatus, 30000);
})();

// ==========================================================================
// MULTI-DATABASE MANAGEMENT & BULK UPDATE & DUAL-PASS AUDIT
// ==========================================================================

async function loadDatabasesList() {
    try {
        if (!isOperatorUser()) {
            const res = await (typeof adminFetch === 'function' ? adminFetch : fetch)('/api/admin/databases');
            if (res.ok) {
                const data = await res.json();
                dbState.allDatabases = data.databases || [];

                // Determine active target and default search
                const activeTarget = dbState.allDatabases.find(d => d.is_active_target) || dbState.allDatabases[0];
                const defaultSearch = dbState.allDatabases.find(d => d.is_default_search) || dbState.allDatabases[0];

                // Update target DB badge in converter upload section
                if (elements.converterActiveDbName && activeTarget) {
                    elements.converterActiveDbName.innerText = `${activeTarget.name} (${activeTarget.file_name})`;
                }

                // Populate dropdown
                if (elements.currentDbSelect) {
                    const currentVal = dbState.selectedDbId || (activeTarget ? activeTarget.id : 'default');
                    elements.currentDbSelect.innerHTML = dbState.allDatabases.map(d => {
                        const badges = [];
                        if (d.is_active_target) badges.push('💾 सक्रिय सेविंग');
                        if (d.is_default_search) badges.push('⭐ डिफ़ॉल्ट सर्च');
                        const badgeStr = badges.length ? ` [${badges.join(', ')}]` : '';
                        return `<option value="${d.id}" ${d.id === currentVal ? 'selected' : ''}>${d.name} (${(d.voter_count || 0).toLocaleString('hi-IN')} मतदाता)${badgeStr}</option>`;
                    }).join('');

                    dbState.selectedDbId = elements.currentDbSelect.value;
                    updateDbBadgesForSelected();
                }
            }
        }

        // Always load parts for bulk update
        await loadPartsForBulkUpdate();

    } catch (err) {
        console.warn('Failed to load databases list:', err);
    }
}

function updateDbBadgesForSelected() {
    const selected = dbState.allDatabases.find(d => d.id === dbState.selectedDbId);
    if (!selected) return;

    if (elements.activeDbSaveBadge) {
        if (selected.is_active_target) {
            elements.activeDbSaveBadge.style.display = 'inline-block';
            elements.activeDbSaveBadge.innerText = '💾 सक्रिय सेविंग DB';
            elements.activeDbSaveBadge.style.background = '#dcfce7';
            elements.activeDbSaveBadge.style.color = '#166534';
        } else {
            elements.activeDbSaveBadge.style.display = 'none';
        }
    }

    if (elements.activeDbSearchBadge) {
        if (selected.is_default_search) {
            elements.activeDbSearchBadge.style.display = 'inline-block';
            elements.activeDbSearchBadge.innerText = '⭐ डिफ़ॉल्ट सर्च DB';
            elements.activeDbSearchBadge.style.background = '#e0f2fe';
            elements.activeDbSearchBadge.style.color = '#0369a1';
        } else {
            elements.activeDbSearchBadge.style.display = 'none';
        }
    }
}

async function loadPartsForBulkUpdate() {
    if (!elements.bulkCurrentPartSelect) return;
    try {
        const dbId = dbState.selectedDbId || 'default';
        const fetchFn = typeof adminFetch === 'function' ? adminFetch : fetch;
        const res = await fetchFn(`/api/database/part-analytics?db_id=${encodeURIComponent(dbId)}`);
        if (!res.ok) return;
        const data = await res.json();
        dbState.partsData = data.parts || [];

        if (dbState.partsData.length === 0) {
            elements.bulkCurrentPartSelect.innerHTML = '<option value="">-- कोई भाग उपलब्ध नहीं है --</option>';
            if (elements.bulkCurrentAssembly) elements.bulkCurrentAssembly.value = '';
            return;
        }

        elements.bulkCurrentPartSelect.innerHTML = '<option value="">-- भाग संख्या चुनें --</option>' +
            dbState.partsData.map(p => `<option value="${p.part_no}">भाग ${p.part_no} (${p.active} मतदाता${p.polling_station ? ' - ' + p.polling_station : ''})</option>`).join('');

        if (elements.bulkCurrentAssembly) elements.bulkCurrentAssembly.value = '';

    } catch (err) {
        console.warn('Failed to load parts for bulk update:', err);
    }
}

function handleBulkPartSelectChange(e) {
    const partNo = e.target.value;
    if (!partNo) {
        if (elements.bulkCurrentAssembly) elements.bulkCurrentAssembly.value = '';
        if (elements.bulkNewPartInput) elements.bulkNewPartInput.value = '';
        return;
    }

    // Set new part input initially to current part
    if (elements.bulkNewPartInput) elements.bulkNewPartInput.value = partNo;

    // Detect assembly from voters table
    fetch(`/api/database/search?part_no=${encodeURIComponent(partNo)}&limit=1&db_id=${encodeURIComponent(dbState.selectedDbId || 'default')}`)
        .then(r => r.json())
        .then(d => {
            if (d.records && d.records.length > 0) {
                const rec = d.records[0];
                if (elements.bulkCurrentAssembly) {
                    elements.bulkCurrentAssembly.value = rec.assembly_name || 'निर्धारित नहीं';
                }
                if (elements.bulkNewAssemblyInput && !elements.bulkNewAssemblyInput.value) {
                    elements.bulkNewAssemblyInput.value = rec.assembly_name || '';
                }
                if (elements.bulkNewPollingStationInput && rec.polling_station) {
                    elements.bulkNewPollingStationInput.value = rec.polling_station;
                }
            }
        })
        .catch(err => console.warn(err));
}

async function handleBulkUpdatePartSubmit(e) {
    e.preventDefault();
    const currentPart = elements.bulkCurrentPartSelect.value;
    let newAssembly = elements.bulkNewAssemblyInput.value.trim();
    let newPart = elements.bulkNewPartInput.value.trim();
    const newStation = elements.bulkNewPollingStationInput ? elements.bulkNewPollingStationInput.value.trim() : null;

    if (!currentPart) {
        showToast('कृपया वर्तमान भाग संख्या चुनें।', 'error');
        return;
    }

    const currentAssembly = (elements.bulkCurrentAssembly && elements.bulkCurrentAssembly.value !== 'निर्धारित नहीं')
        ? elements.bulkCurrentAssembly.value.trim() : '';

    if (!newAssembly && currentAssembly) {
        newAssembly = currentAssembly;
    }
    if (!newPart) {
        newPart = currentPart;
    }

    if (!newAssembly && !newPart && !newStation) {
        showToast('कृपया नई विधान सभा या नई भाग संख्या दर्ज करें।', 'error');
        return;
    }

    const submitBtn = document.getElementById('bulkUpdateSubmitBtn');
    if (submitBtn) {
        submitBtn.disabled = true;
        submitBtn.innerHTML = `<i data-lucide="loader-2" class="spin"></i> <span>अपडेट हो रहा है...</span>`;
        if (window.lucide) lucide.createIcons();
    }

    try {
        const payload = {
            current_part_no: currentPart,
            new_assembly: newAssembly || undefined,
            new_assembly_name: newAssembly || undefined,
            new_part_no: newPart || undefined,
            new_polling_station: newStation || null,
            db_id: dbState.selectedDbId || 'default'
        };

        const fetchFn = typeof adminFetch === 'function' ? adminFetch : fetch;
        const res = await fetchFn('/api/database/bulk-update-part', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        const data = await res.json();
        if (!res.ok) {
            throw new Error(data.detail || 'बल्क अपडेट में त्रुटि');
        }

        const count = data.updated_count !== undefined ? data.updated_count : (data.affected_voters || 0);
        showToast(data.message || `${count} मतदाताओं का विवरण सफलतापूर्वक अपडेट हुआ!`, 'success');

        // Reload DB stats, records, and parts
        await fetchDbStats();
        await fetchDbRecords();
        if (typeof loadPartAnalytics === 'function') await loadPartAnalytics();
        await loadPartsForBulkUpdate();

    } catch (err) {
        showToast('त्रुटि: ' + err.message, 'error');
    } finally {
        if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.innerHTML = `<i data-lucide="check-circle"></i> <span>एक क्लिक में बदलें</span>`;
            if (window.lucide) lucide.createIcons();
        }
    }
}

// Rescan Part in Database
async function handleRescanPartInDb() {
    const partNo = elements.bulkCurrentPartSelect ? elements.bulkCurrentPartSelect.value : null;
    if (!partNo) {
        showToast('कृपया पहले "वर्तमान भाग संख्या" चुनें जिस पर त्रुटि सुधार चलाना है।', 'warning');
        return;
    }

    if (elements.rescanPartBtn) {
        elements.rescanPartBtn.disabled = true;
        elements.rescanPartBtn.innerHTML = `<i data-lucide="loader-2" class="spin"></i> <span>जाँच जारी...</span>`;
        if (window.lucide) lucide.createIcons();
    }

    try {
        const fetchFn = typeof adminFetch === 'function' ? adminFetch : fetch;
        const res = await fetchFn('/api/database/rescan-correct-part', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                part_no: partNo,
                db_id: dbState.selectedDbId || 'default'
            })
        });

        const data = await res.json();
        if (!res.ok) {
            throw new Error(data.detail || 'त्रुटि सुधार में विफल');
        }

        showToast(data.message || `भाग ${partNo}: त्रुटि सुधार पूर्ण हुआ! (${data.errors_corrected || 0} सुधार)`, 'success');

        // Open corrections report modal
        state.perfectCount = data.perfect_first_pass || 0;
        state.correctedCount = data.errors_corrected || 0;
        state.correctionsDetail = data.corrections_detail || [];

        openCorrectionsModal();

        // Refresh DB
        await fetchDbStats();
        await fetchDbRecords();

    } catch (err) {
        showToast('त्रुटि: ' + err.message, 'error');
    } finally {
        if (elements.rescanPartBtn) {
            elements.rescanPartBtn.disabled = false;
            elements.rescanPartBtn.innerHTML = `<i data-lucide="sparkles"></i> <span>त्रुटि सुधार</span>`;
            if (window.lucide) lucide.createIcons();
        }
    }
}

// Retrigger Scan & Correct on Active Conversion Job
async function handleRetriggerScanCorrect() {
    if (!state.currentJobId) {
        showToast('कोई सक्रिय जॉब उपलब्ध नहीं है।', 'error');
        return;
    }

    if (elements.retriggerScanCorrectBtn) {
        elements.retriggerScanCorrectBtn.disabled = true;
        elements.retriggerScanCorrectBtn.innerHTML = `<i data-lucide="loader-2" class="spin"></i> <span>जाँच जारी...</span>`;
        lucide.createIcons();
    }

    try {
        const res = await fetch(`/api/jobs/${state.currentJobId}/rescan-correct`, {
            method: 'POST'
        });

        const data = await res.json();
        if (!res.ok) {
            throw new Error(data.detail || 'पुनः सुधार प्रक्रिया में त्रुटि');
        }

        showToast(data.message || 'दोहरा स्कैन व सुधार पूर्ण हुआ!', 'success');

        state.perfectCount = data.perfect_first_pass || 0;
        state.correctedCount = data.errors_corrected || 0;
        state.correctionsDetail = data.corrections_detail || [];

        if (elements.correctionsCountText) {
            elements.correctionsCountText.innerText = (state.correctionsDetail.length || 0).toLocaleString('hi-IN');
        }
        if (elements.dualPassSummaryText) {
            const pCount = state.perfectCount.toLocaleString('hi-IN');
            const cCount = state.correctedCount.toLocaleString('hi-IN');
            const totalEdits = state.correctionsDetail.length.toLocaleString('hi-IN');
            elements.dualPassSummaryText.innerHTML = `दोहरा स्कैन गुणवत्ता ऑडिट: <strong>${pCount}</strong> मतदाता पहले पास में ही शत-प्रतिशत सही मिले (यथावत सुरक्षित रखे गए), <strong>${cCount}</strong> कार्ड्स में <strong>${totalEdits}</strong> फील्ड सुधार किए गए।`;
        }

        // Refresh preview view
        await fetchPreview();

        // Show corrections modal
        openCorrectionsModal();

    } catch (err) {
        showToast('त्रुटि: ' + err.message, 'error');
    } finally {
        if (elements.retriggerScanCorrectBtn) {
            elements.retriggerScanCorrectBtn.disabled = false;
            elements.retriggerScanCorrectBtn.innerHTML = `<i data-lucide="refresh-cw"></i> <span>पुनः सुधार चलाएं</span>`;
            lucide.createIcons();
        }
    }
}

// Open / Close Corrections Report Modal
function openCorrectionsModal() {
    if (!elements.correctionsDetailModal) return;

    if (elements.auditModalPerfectCount) {
        elements.auditModalPerfectCount.innerText = (state.perfectCount || 0).toLocaleString('hi-IN');
    }
    if (elements.auditModalCorrectedCount) {
        elements.auditModalCorrectedCount.innerText = (state.correctedCount || 0).toLocaleString('hi-IN');
    }
    const details = state.correctionsDetail || [];
    if (elements.auditModalTotalEditsCount) {
        elements.auditModalTotalEditsCount.innerText = details.length.toLocaleString('hi-IN');
    }

    if (elements.correctionsTableBody) {
        if (details.length === 0) {
            elements.correctionsTableBody.innerHTML = `<tr><td colspan="6" style="text-align:center; padding:24px; color:#166534; font-weight:600;">
                <div style="font-size:1.1rem; margin-bottom:4px;">🎉 सभी कार्ड पहले पास में ही शत-प्रतिशत सही पाए गए!</div>
                <div style="font-size:0.82rem; color:#64748b; font-weight:normal;">सिस्टम ने प्रत्येक कार्ड की शुद्धता की पुष्टि की — डेटा पहले से ही सटीक होने के कारण किसी भी सुधार की आवश्यकता नहीं पड़ी।</div>
            </td></tr>`;
        } else {
            elements.correctionsTableBody.innerHTML = details.map((c, i) => `
                <tr>
                    <td>${i + 1}</td>
                    <td style="font-weight: 600; color: #1e293b;">${escapeHtml(c.voter_name || '-')}</td>
                    <td><span class="badge" style="background:#e0e7ff; color:#3730a3; font-size:0.75rem; padding:2px 8px; border-radius:4px; font-weight:600;">${escapeHtml(c.field || '-')}</span></td>
                    <td style="color: #dc2626; text-decoration: line-through; font-family: monospace; font-size:0.85rem;">${escapeHtml(c.old_val || '(रिक्त)')}</td>
                    <td style="color: #16a34a; font-weight: 700; font-family: monospace; font-size:0.85rem;">${escapeHtml(c.new_val || '-')}</td>
                    <td style="font-size: 0.82rem; color: #475569;">${escapeHtml(c.reason || 'सटीकता सुधार')}</td>
                </tr>
            `).join('');
        }
    }

    elements.correctionsDetailModal.style.display = 'flex';
    lucide.createIcons();
}

function closeCorrectionsModal() {
    if (elements.correctionsDetailModal) elements.correctionsDetailModal.style.display = 'none';
}

// Database Create Modal Handlers
function openCreateDbModal() {
    if (elements.createDbForm) elements.createDbForm.reset();
    if (elements.createDbModal) elements.createDbModal.style.display = 'flex';
    lucide.createIcons();
}
function closeCreateDbModal() {
    if (elements.createDbModal) elements.createDbModal.style.display = 'none';
}
async function handleCreateDbSubmit(e) {
    e.preventDefault();
    const id = document.getElementById('newDbId').value.trim();
    const name = document.getElementById('newDbName').value.trim();
    const desc = document.getElementById('newDbDesc').value.trim();
    const setActive = document.getElementById('newDbSetActiveCheck') ? document.getElementById('newDbSetActiveCheck').checked : true;

    try {
        const res = await fetch('/api/admin/databases/create', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                db_id: id,
                name: name,
                description: desc,
                set_active: setActive
            })
        });

        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'डेटाबेस बनाने में त्रुटि');

        showToast(data.message || 'नया डेटाबेस सफलतापूर्वक बनाया गया!', 'success');
        closeCreateDbModal();
        dbState.selectedDbId = id;
        await loadDatabasesList();
        await fetchDbStats();
        await fetchDbRecords();

    } catch (err) {
        showToast('त्रुटि: ' + err.message, 'error');
    }
}

// Database Rename Modal Handlers
function openRenameDbModal() {
    const currentDb = dbState.allDatabases.find(d => d.id === dbState.selectedDbId);
    if (!currentDb) return;

    if (elements.renameTargetDbId) elements.renameTargetDbId.value = currentDb.id;
    if (elements.renameDisplayDbId) elements.renameDisplayDbId.value = `${currentDb.name} (${currentDb.id})`;
    if (elements.renameDbNameInput) elements.renameDbNameInput.value = currentDb.name;
    if (elements.renameDbDescInput) elements.renameDbDescInput.value = currentDb.description || '';

    if (elements.renameDbModal) elements.renameDbModal.style.display = 'flex';
    lucide.createIcons();
}
function closeRenameDbModal() {
    if (elements.renameDbModal) elements.renameDbModal.style.display = 'none';
}
async function handleRenameDbSubmit(e) {
    e.preventDefault();
    const id = elements.renameTargetDbId.value;
    const name = elements.renameDbNameInput.value.trim();
    const desc = elements.renameDbDescInput.value.trim();

    try {
        const res = await fetch(`/api/admin/databases/${encodeURIComponent(id)}/rename`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: name, description: desc })
        });

        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'डेटाबेस का नाम बदलने में त्रुटि');

        showToast(data.message || 'डेटाबेस का नाम सफलतापूर्वक सुरक्षित किया गया!', 'success');
        closeRenameDbModal();
        await loadDatabasesList();

    } catch (err) {
        showToast('त्रुटि: ' + err.message, 'error');
    }
}

// Make Active Target (Data Saving DB)
async function handleMakeActiveSaveDb() {
    const id = dbState.selectedDbId;
    if (!id) return;
    try {
        const res = await fetch(`/api/admin/databases/${encodeURIComponent(id)}/set-active-target`, {
            method: 'POST'
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'सक्रिय डेटाबेस सेट करने में त्रुटि');

        showToast(data.message || 'सक्रिय सेविंग डेटाबेस सेट किया गया!', 'success');
        await loadDatabasesList();
    } catch (err) {
        showToast('त्रुटि: ' + err.message, 'error');
    }
}

// Make Default Search DB
async function handleMakeDefaultSearchDb() {
    const id = dbState.selectedDbId;
    if (!id) return;
    try {
        const res = await fetch(`/api/admin/databases/${encodeURIComponent(id)}/set-default-search`, {
            method: 'POST'
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'डिफ़ॉल्ट सर्च डेटाबेस सेट करने में त्रुटि');

        showToast(data.message || 'डिफ़ॉल्ट सर्च डेटाबेस सेट किया गया!', 'success');
        await loadDatabasesList();
    } catch (err) {
        showToast('त्रुटि: ' + err.message, 'error');
    }
}

// Manage All DBs Modal
function openManageDbsModal() {
    renderManageDbsTable();
    if (elements.manageDbsModal) elements.manageDbsModal.style.display = 'flex';
    lucide.createIcons();
}
function closeManageDbsModal() {
    if (elements.manageDbsModal) elements.manageDbsModal.style.display = 'none';
}

function renderManageDbsTable() {
    if (!elements.manageDbsTableBody) return;
    if (!dbState.allDatabases || dbState.allDatabases.length === 0) {
        elements.manageDbsTableBody.innerHTML = '<tr><td colspan="5" style="text-align:center; padding:16px;">कोई डेटाबेस नहीं मिला</td></tr>';
        return;
    }

    elements.manageDbsTableBody.innerHTML = dbState.allDatabases.map(d => {
        const isSelected = d.id === dbState.selectedDbId;
        const isDefault = d.id === 'default';

        const saveBadge = d.is_active_target
            ? '<span class="badge" style="background:#dcfce7; color:#166534; font-weight:600;">💾 सक्रिय (Active)</span>'
            : `<button class="btn-outline-sm" style="font-size:0.75rem; padding:3px 8px;" onclick="window.setTargetDb('${d.id}')">सक्रिय बनाएं</button>`;

        const searchBadge = d.is_default_search
            ? '<span class="badge" style="background:#e0f2fe; color:#0369a1; font-weight:600;">⭐ डिफ़ॉल्ट</span>'
            : `<button class="btn-outline-sm" style="font-size:0.75rem; padding:3px 8px;" onclick="window.setDefaultSearchDb('${d.id}')">डिफ़ॉल्ट बनाएं</button>`;

        const deleteBtn = (isDefault || d.is_active_target || d.is_default_search)
            ? `<button disabled class="btn-icon-subtle" title="सुरक्षित या सक्रिय DB हटाया नहीं जा सकता" style="opacity:0.4; cursor:not-allowed;"><i data-lucide="trash-2" style="width:14px;height:14px;"></i></button>`
            : `<button class="btn-icon-subtle" style="color:#dc2626;" title="हटाएं" onclick="window.deleteDatabase('${d.id}', '${escapeHtml(d.name)}')"><i data-lucide="trash-2" style="width:14px;height:14px;"></i></button>`;

        return `
            <tr style="${isSelected ? 'background: #f8fafc;' : ''}">
                <td>
                    <div style="font-weight: 700; color: #1e293b;">${escapeHtml(d.name)}</div>
                    <div style="font-size: 0.76rem; color: #64748b; font-family: monospace;">${escapeHtml(d.file_name)} (ID: ${escapeHtml(d.id)})</div>
                    ${d.description ? `<div style="font-size: 0.78rem; color: #475569; margin-top:2px;">${escapeHtml(d.description)}</div>` : ''}
                </td>
                <td style="font-weight: 600;">${(d.voter_count || 0).toLocaleString('hi-IN')}</td>
                <td>${saveBadge}</td>
                <td>${searchBadge}</td>
                <td>
                    <div style="display: flex; gap: 6px; align-items: center;">
                        <button class="btn-outline-sm" style="font-size:0.75rem; padding:4px 8px;" onclick="window.selectDbInApp('${d.id}')" title="इस डेटाबेस को चुनें">चुनें</button>
                        <button class="btn-icon-subtle" style="padding:4px;" onclick="window.openRenameModalForId('${d.id}')" title="नाम बदलें"><i data-lucide="edit-3" style="width:14px;height:14px;"></i></button>
                        ${deleteBtn}
                    </div>
                </td>
            </tr>
        `;
    }).join('');

    lucide.createIcons();
}

window.selectDbInApp = function(id) {
    dbState.selectedDbId = id;
    if (elements.currentDbSelect) elements.currentDbSelect.value = id;
    updateDbBadgesForSelected();
    closeManageDbsModal();
    dbState.page = 1;
    fetchDbStats();
    fetchDbRecords();
    loadPartAnalytics();
    loadPartsForBulkUpdate();
    showToast('डेटाबेस चुना गया!', 'info');
};

window.setTargetDb = async function(id) {
    try {
        const res = await fetch(`/api/admin/databases/${encodeURIComponent(id)}/set-active-target`, { method: 'POST' });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail);
        showToast(data.message || 'सक्रिय सेविंग डेटाबेस सेट हुआ!', 'success');
        await loadDatabasesList();
        renderManageDbsTable();
    } catch (err) {
        showToast('त्रुटि: ' + err.message, 'error');
    }
};

window.setDefaultSearchDb = async function(id) {
    try {
        const res = await fetch(`/api/admin/databases/${encodeURIComponent(id)}/set-default-search`, { method: 'POST' });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail);
        showToast(data.message || 'डिफ़ॉल्ट सर्च डेटाबेस सेट हुआ!', 'success');
        await loadDatabasesList();
        renderManageDbsTable();
    } catch (err) {
        showToast('त्रुटि: ' + err.message, 'error');
    }
};

window.openRenameModalForId = function(id) {
    closeManageDbsModal();
    dbState.selectedDbId = id;
    if (elements.currentDbSelect) elements.currentDbSelect.value = id;
    openRenameDbModal();
};

window.deleteDatabase = function(id, name) {
    showDeleteConfirmModal({
        title: 'डेटाबेस हटाएं (Delete Database)',
        subtitle: 'पुष्टि करें',
        msg: `क्या आप वाकई डेटाबेस <strong>"${name}"</strong> को हटाना चाहते हैं?`,
        warningText: 'चेतावनी: डेटाबेस फाइल स्थायी रूप से डिस्क से हटा दी जाएगी!',
        confirmBtnText: 'हाँ, स्थायी रूप से हटाएं',
        onConfirm: async () => {
            try {
                const res = await fetch(`/api/admin/databases/${encodeURIComponent(id)}`, { method: 'DELETE' });
                const data = await res.json();
                if (!res.ok) throw new Error(data.detail);
                showToast(data.message || 'डेटाबेस हटाया गया!', 'success');
                hideDeleteConfirmModal();
                if (dbState.selectedDbId === id) dbState.selectedDbId = 'default';
                await loadDatabasesList();
                renderManageDbsTable();
                await fetchDbStats();
                await fetchDbRecords();
            } catch (err) {
                showToast('त्रुटि: ' + err.message, 'error');
            }
        }
    });
};

// ==========================================================================
// FULLPAGE PAPER ROLL VIEW CONTROLLER & A4 FIT-SCREEN SCALING
// ==========================================================================

function setPaperSheetMode(canvasId, mode) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    const wrap = canvas.closest('.paper-roll-view-wrap');
    const isConverter = canvasId === 'paperA4Canvas';
    const fitBtn = isConverter ? elements.paperFitScreenBtn : elements.dbPaperFitScreenBtn;
    const actBtn = isConverter ? elements.paperActualSizeBtn : elements.dbPaperActualSizeBtn;

    if (mode === 'fit') {
        canvas.classList.add('fit-screen');
        if (wrap) wrap.classList.add('fit-screen-active');
        if (fitBtn) fitBtn.classList.add('active');
        if (actBtn) actBtn.classList.remove('active');
    } else {
        canvas.classList.remove('fit-screen');
        if (wrap) wrap.classList.remove('fit-screen-active');
        if (fitBtn) fitBtn.classList.remove('active');
        if (actBtn) actBtn.classList.add('active');
    }
}
window.setPaperSheetMode = setPaperSheetMode;

function togglePaperFullscreen(wrapId) {
    const wrap = document.getElementById(wrapId);
    if (!wrap) return;

    const isFull = wrap.classList.toggle('fullpage-view');
    document.body.classList.toggle('paper-fullscreen-active', isFull);

    const canvasId = (wrapId === 'paperRollViewWrap') ? 'paperA4Canvas' : 'dbPaperA4Canvas';
    if (isFull) {
        // Automatically default to "पूरे 30 एक स्क्रीन में (Fit to Screen)" on entering full page view
        setPaperSheetMode(canvasId, 'fit');
    }

    // Update toggle button text & icons
    const btn = (wrapId === 'paperRollViewWrap') 
        ? elements.paperFullscreenToggleBtn 
        : elements.dbPaperFullscreenToggleBtn;

    if (btn) {
        if (isFull) {
            btn.innerHTML = `<i data-lucide="minimize-2" style="width: 16px; height: 16px;"></i> <span>सामान्य दृश्य (ESC)</span>`;
            btn.classList.add('active');
        } else {
            btn.innerHTML = `<i data-lucide="maximize-2" style="width: 16px; height: 16px;"></i> <span>पूर्ण स्क्रीन (Full Page)</span>`;
            btn.classList.remove('active');
        }
    }
    if (window.lucide) lucide.createIcons();
}
window.togglePaperFullscreen = togglePaperFullscreen;

// Keyboard navigation listener for fullpage A4 view (ESC to exit, Arrow keys to flip pages)
document.addEventListener('keydown', (e) => {
    const fullWrap = document.querySelector('.paper-roll-view-wrap.fullpage-view');
    if (e.key === 'Escape') {
        if (fullWrap) {
            togglePaperFullscreen(fullWrap.id);
        }
    } else if (fullWrap) {
        // Keyboard arrow navigation between A4 sheets when in fullpage view
        if (e.key === 'ArrowLeft') {
            if (fullWrap.id === 'paperRollViewWrap') {
                navigatePaperPage(-1);
            } else if (fullWrap.id === 'dbPaperViewWrap') {
                if (dbState.page > 1) {
                    dbState.page--;
                    fetchDbRecords();
                }
            }
        } else if (e.key === 'ArrowRight') {
            if (fullWrap.id === 'paperRollViewWrap') {
                navigatePaperPage(1);
            } else if (fullWrap.id === 'dbPaperViewWrap') {
                if (dbState.page < dbState.totalPages) {
                    dbState.page++;
                    fetchDbRecords();
                }
            }
        }
    }
});

// ==========================================================================
// PENDING EDITS APPROVAL SYSTEM (ADMIN ONLY)
// ==========================================================================

async function fetchPendingEditsCounts() {
    try {
        if (isOperatorUser()) return;
        const fetchFn = typeof adminFetch === 'function' ? adminFetch : fetch;
        const res = await fetchFn('/api/admin/pending-edits/counts');
        if (!res.ok) return;
        const data = await res.json();

        const pendingCount = data.pending || 0;
        if (elements.navPendingEditsCountBadge) {
            if (pendingCount > 0) {
                elements.navPendingEditsCountBadge.style.display = 'inline-block';
                elements.navPendingEditsCountBadge.innerText = pendingCount;
            } else {
                elements.navPendingEditsCountBadge.style.display = 'none';
            }
        }
        if (elements.statPendingCount) elements.statPendingCount.innerText = pendingCount;
        if (elements.statApprovedCount) elements.statApprovedCount.innerText = data.approved || 0;
        if (elements.statRejectedCount) elements.statRejectedCount.innerText = data.rejected || 0;
    } catch (e) {
        console.warn('Failed to fetch pending edits counts:', e);
    }
}

async function loadPendingEdits() {
    if (isOperatorUser()) return;
    const status = elements.pendingEditsStatusFilter ? elements.pendingEditsStatusFilter.value : 'pending';
    const tbody = elements.pendingEditsTableBody;
    if (tbody) {
        tbody.innerHTML = `<tr><td colspan="9" style="text-align: center; padding: 30px; color: #64748b;"><i data-lucide="loader-2" class="spin"></i> बदलाव लोड हो रहे हैं...</td></tr>`;
        if (window.lucide) lucide.createIcons();
    }

    try {
        const fetchFn = typeof adminFetch === 'function' ? adminFetch : fetch;
        const res = await fetchFn(`/api/admin/pending-edits?status=${encodeURIComponent(status)}&limit=200`);
        if (!res.ok) throw new Error('बदलाव लोड करने में विफल');
        const data = await res.json();
        renderPendingEditsTable(data.records || []);
        fetchPendingEditsCounts();
    } catch (err) {
        if (tbody) {
            tbody.innerHTML = `<tr><td colspan="9" style="text-align: center; padding: 30px; color: #ef4444;">त्रुटि: ${escapeHtml(err.message)}</td></tr>`;
        }
    }
}

function renderPendingEditsTable(records) {
    const tbody = elements.pendingEditsTableBody;
    if (!tbody) return;
    tbody.innerHTML = '';

    if (!records || records.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="9" style="text-align: center; padding: 48px; color: #94a3b8;">
                    <i data-lucide="check-circle" style="width: 36px; height: 36px; display: block; margin: 0 auto 10px; color: #10b981;"></i>
                    कोई अनुमोदन हेतु लंबित बदलाव नहीं है।
                </td>
            </tr>
        `;
        if (window.lucide) lucide.createIcons();
        return;
    }

    const actionBadgeMap = {
        'update': '<span class="badge" style="background: #e0e7ff; color: #4338ca; font-weight: 700;">संशोधन (Edit)</span>',
        'add': '<span class="badge" style="background: #dcfce7; color: #15803d; font-weight: 700;">➕ नया मतदाता</span>',
        'delete': '<span class="badge" style="background: #fee2e2; color: #b91c1c; font-weight: 700;">🗑️ विलोपन (Delete)</span>',
        'bulk_update_part': '<span class="badge" style="background: #fef3c7; color: #b45309; font-weight: 700;">⚡ बल्क भाग अपडेट</span>'
    };

    records.forEach(r => {
        const tr = document.createElement('tr');
        const isPending = r.status === 'pending';
        const actionBadge = actionBadgeMap[r.action_type] || `<span class="badge">${r.action_type}</span>`;
        
        let statusBadge = '';
        if (r.status === 'pending') {
            statusBadge = '<span class="badge" style="background: #fef3c7; color: #92400e; font-weight: 700;">⏳ अनुमोदन लंबित</span>';
        } else if (r.status === 'approved') {
            statusBadge = '<span class="badge" style="background: #dcfce7; color: #166534; font-weight: 700;">✅ स्वीकृत (Approved)</span>';
        } else {
            statusBadge = '<span class="badge" style="background: #fee2e2; color: #991b1b; font-weight: 700;">❌ अस्वीकृत (Rejected)</span>';
        }

        // Format Diff
        let diffHtml = '';
        if (r.diff_summary && typeof r.diff_summary === 'object') {
            const keys = Object.keys(r.diff_summary);
            diffHtml = keys.map(k => {
                const d = r.diff_summary[k];
                return `<div style="margin-bottom: 4px; font-size: 0.8rem;">
                    <strong>${escapeHtml(k)}:</strong> 
                    <span class="diff-tag-old">${escapeHtml(String(d.old ?? ''))}</span> ➔ 
                    <span class="diff-tag-new">${escapeHtml(String(d.new ?? ''))}</span>
                </div>`;
            }).join('');
        } else if (r.action_type === 'add') {
            const p = r.proposed_data || {};
            diffHtml = `<div style="font-size: 0.8rem;"><strong>नया रिकॉर्ड:</strong> ${escapeHtml(p.name || '')} (${escapeHtml(p.relation_type || 'पिता')}: ${escapeHtml(p.relation_name || '')}), भाग: ${escapeHtml(p.part_no || '')}, EPIC: ${escapeHtml(p.epic_no || '')}</div>`;
        } else if (r.action_type === 'delete') {
            diffHtml = `<div style="font-size: 0.8rem; color: #b91c1c;">मतदाता रिकॉर्ड (ID #${r.target_id}) हटाने की सिफारिश की गई है।</div>`;
        }

        const dateStr = r.created_at ? r.created_at.substring(0, 16).replace('T', ' ') : '--';

        tr.innerHTML = `
            <td style="text-align: center;">
                <input type="checkbox" class="pending-edit-check" data-id="${r.id}" ${!isPending ? 'disabled' : ''}>
            </td>
            <td class="font-mono" style="font-size: 0.8rem; color: #64748b;">#${r.id}</td>
            <td style="font-size: 0.8rem; color: #475569;">${dateStr}</td>
            <td>
                <div style="font-weight: 600; color: #1e293b;">${escapeHtml(r.operator_name || r.operator_username)}</div>
                <div style="font-size: 0.75rem; color: #64748b;">@${escapeHtml(r.operator_username)}</div>
            </td>
            <td>${actionBadge}</td>
            <td>
                <div style="font-weight: 700; color: #0f172a;">${escapeHtml(r.voter_name || 'रिकॉर्ड #' + (r.target_id || '--'))}</div>
                <div style="font-size: 0.78rem; color: #64748b;">EPIC: <strong>${escapeHtml(r.epic_no || '--')}</strong> | भाग: <strong>${escapeHtml(r.part_no || '--')}</strong></div>
            </td>
            <td style="max-width: 280px; word-break: break-word;">${diffHtml || '--'}</td>
            <td>${statusBadge}</td>
            <td style="white-space: nowrap;">
                ${isPending ? `
                    <button type="button" class="btn-primary-sm" style="padding: 4px 10px; font-size: 0.78rem; background: #15803d; border: none; margin-right: 4px; cursor: pointer; color: white; border-radius: 4px;" onclick="handleApproveSingleEdit(${r.id})">
                        ✓ स्वीकृत
                    </button>
                    <button type="button" class="btn-outline-sm" style="padding: 4px 10px; font-size: 0.78rem; color: #b91c1c; border: 1px solid #fca5a5; background: #fff5f5; cursor: pointer; border-radius: 4px;" onclick="handleRejectSingleEdit(${r.id})">
                        ✕ अस्वीकृत
                    </button>
                ` : `<span style="font-size: 0.78rem; color: #94a3b8;">समीक्षित (${escapeHtml(r.reviewed_by || 'Admin')})</span>`}
            </td>
        `;
        tbody.appendChild(tr);
    });
    if (window.lucide) lucide.createIcons();
}

window.handleApproveSingleEdit = async function(editId) {
    try {
        const fetchFn = typeof adminFetch === 'function' ? adminFetch : fetch;
        const res = await fetchFn(`/api/admin/pending-edits/${editId}/approve`, { method: 'POST' });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'अनुमोदन विफल रहा');
        showToast(data.message || 'बदलाव स्वीकृत कर डेटाबेस में लागू कर दिया गया!', 'success');
        await loadPendingEdits();
        await fetchDbStats();
        await fetchDbRecords();
        await loadPartsForBulkUpdate();
    } catch (err) {
        showToast('त्रुटि: ' + err.message, 'error');
    }
};

window.handleRejectSingleEdit = async function(editId) {
    const reason = prompt('अस्वीकार करने का कारण (वैकल्पिक):');
    if (reason === null) return; // User cancelled prompt

    try {
        const fetchFn = typeof adminFetch === 'function' ? adminFetch : fetch;
        const res = await fetchFn(`/api/admin/pending-edits/${editId}/reject`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ review_notes: reason })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'अस्वीकरण विफल रहा');
        showToast(data.message || 'बदलाव अस्वीकृत कर दिया गया।', 'info');
        await loadPendingEdits();
    } catch (err) {
        showToast('त्रुटि: ' + err.message, 'error');
    }
};

async function handleBulkApprovePendingEdits() {
    const checked = Array.from(document.querySelectorAll('.pending-edit-check:checked')).map(c => parseInt(c.getAttribute('data-id')));
    if (checked.length === 0) {
        showToast('कृपया स्वीकृत करने के लिए कम से कम एक बदलाव चुनें।', 'warning');
        return;
    }

    if (!confirm(`क्या आप वाकई चयनित ${checked.length} बदलावों को स्वीकृत कर मास्टर डेटाबेस में लागू करना चाहते हैं?`)) {
        return;
    }

    try {
        const fetchFn = typeof adminFetch === 'function' ? adminFetch : fetch;
        const res = await fetchFn('/api/admin/pending-edits/bulk-approve', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ edit_ids: checked })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'बल्क अनुमोदन विफल रहा');
        showToast(data.message || `${checked.length} बदलाव सफलतापूर्वक स्वीकृत हुए!`, 'success');
        await loadPendingEdits();
        await fetchDbStats();
        await fetchDbRecords();
        await loadPartsForBulkUpdate();
    } catch (err) {
        showToast('त्रुटि: ' + err.message, 'error');
    }
}

async function handleBulkRejectPendingEdits() {
    const checked = Array.from(document.querySelectorAll('.pending-edit-check:checked')).map(c => parseInt(c.getAttribute('data-id')));
    if (checked.length === 0) {
        showToast('कृपया अस्वीकृत करने के लिए कम से कम एक बदलाव चुनें।', 'warning');
        return;
    }

    const reason = prompt(`चयनित ${checked.length} बदलावों को अस्वीकार करने का कारण (वैकल्पिक):`);
    if (reason === null) return;

    try {
        const fetchFn = typeof adminFetch === 'function' ? adminFetch : fetch;
        const res = await fetchFn('/api/admin/pending-edits/bulk-reject', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ edit_ids: checked, review_notes: reason })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'बल्क अस्वीकरण विफल रहा');
        showToast(data.message || `${checked.length} बदलाव अस्वीकृत कर दिए गए।`, 'info');
        await loadPendingEdits();
    } catch (err) {
        showToast('त्रुटि: ' + err.message, 'error');
    }
}


// ==========================================================================
// GEOGRAPHIC STREET SURVEY AUDIT MODULE (NPPropertyServey Integration)
// Rule: "सदस्य का नाम व संबंधी का नाम" से ध्वन्यात्मक मिलान से ही सम्पूर्ण
//       वोटर डेटाबेस मे करना है क्योंकि मकान नंबर एव वार्ड नंबर वोटर लिस्ट मे मैच नहीं हो पाएंगे।
// ==========================================================================

const streetAuditState = {
    initialized: false,
    zones: [],
    streetsByZone: {},
    wards: [],
    streets: [],
    allStreets: [],
    streetsByWard: {},
    currentAudit: null,
    filterMode: 'all', // 'all', 'unregistered', 'registered'
    searchQuery: '',
    isLoading: false
};

function initStreetAuditModule() {
    if (!isSuperAdminUser()) return;
    if (!streetAuditState.initialized) {
        setupStreetAuditEvents();
        streetAuditState.initialized = true;
    }
    loadSurveyStreets();
}

function setupStreetAuditEvents() {
    const zoneSelect = document.getElementById('surveyZoneSelect') || document.getElementById('surveyWardSelect');
    const streetSelect = document.getElementById('surveyStreetSelect');
    const loadBtn = document.getElementById('btnLoadStreetAudit');
    const exportBtn = document.getElementById('btnExportStreetAuditExcel');
    const retryBtn = document.getElementById('btnRetrySurveyConnect');
    const searchInput = document.getElementById('surveyHouseSearchInput');

    if (zoneSelect) {
        zoneSelect.addEventListener('change', () => {
            populateStreetDropdown(zoneSelect.value);
        });
    }

    if (loadBtn) {
        loadBtn.addEventListener('click', () => {
            loadStreetAuditData();
        });
    }

    if (exportBtn) {
        exportBtn.addEventListener('click', () => {
            downloadStreetAuditExcel();
        });
    }

    if (retryBtn) {
        retryBtn.addEventListener('click', () => {
            loadSurveyStreets();
        });
    }

    // Filter pills
    const pillAll = document.getElementById('filterAuditAllHouses');
    const pillUnreg = document.getElementById('filterAuditUnregisteredOnly');
    const pillF6_18 = document.getElementById('filterAuditForm6_18');
    const pillF6_17 = document.getElementById('filterAuditForm6_17');
    const pillReg = document.getElementById('filterAuditRegisteredOnly');

    const allPills = [pillAll, pillUnreg, pillF6_18, pillF6_17, pillReg];

    function setActivePill(pill, mode) {
        allPills.forEach(p => {
            if (p) {
                p.classList.remove('active');
                p.style.background = 'white';
                p.style.color = '#475569';
            }
        });
        if (pill) {
            pill.classList.add('active');
            pill.style.background = '#059669';
            pill.style.color = 'white';
        }
        streetAuditState.filterMode = mode;
        renderStreetAuditHouses();
    }

    if (pillAll) pillAll.addEventListener('click', () => setActivePill(pillAll, 'all'));
    if (pillUnreg) pillUnreg.addEventListener('click', () => setActivePill(pillUnreg, 'unregistered'));
    if (pillF6_18) pillF6_18.addEventListener('click', () => setActivePill(pillF6_18, 'form6_18'));
    if (pillF6_17) pillF6_17.addEventListener('click', () => setActivePill(pillF6_17, 'form6_17'));
    if (pillReg) pillReg.addEventListener('click', () => setActivePill(pillReg, 'registered'));

    if (searchInput) {
        searchInput.addEventListener('input', (e) => {
            streetAuditState.searchQuery = e.target.value.trim().toLowerCase();
            renderStreetAuditHouses();
        });
    }
}

async function loadSurveyStreets() {
    const offlineAlert = document.getElementById('surveyOfflineAlert');
    const statusBadge = document.getElementById('surveyApiStatusBadge');
    const zoneSelect = document.getElementById('surveyZoneSelect') || document.getElementById('surveyWardSelect');
    const streetSelect = document.getElementById('surveyStreetSelect');

    try {
        const fetchFn = typeof adminFetch === 'function' ? adminFetch : fetch;
        const res = await fetchFn('/api/survey-audit/streets');
        if (!res.ok) {
            const errData = await res.json().catch(() => ({}));
            throw new Error(errData.detail || 'सर्वे सर्वर से कनेक्ट नहीं हो सका।');
        }
        const data = await res.json();
        if (!data.success) {
            throw new Error(data.error || 'गलियों की सूची प्राप्त नहीं हो सकी।');
        }

        streetAuditState.zones = data.zones || [];
        streetAuditState.streetsByZone = data.streetsByZone || {};
        streetAuditState.wards = data.wards || [];
        streetAuditState.allStreets = data.allStreets || [];
        streetAuditState.streetsByWard = data.streetsByWard || {};
        streetAuditState.houseCountByZone = data.houseCountByZone || {};
        streetAuditState.houseCountByStreet = data.houseCountByStreet || {};
        streetAuditState.houseCountByWard = data.houseCountByWard || {};

        if (offlineAlert) offlineAlert.style.display = 'none';
        if (statusBadge) {
            const label = data.sourceLabel || (data.source === 'firebase_cloud' ? '🟢 लाइव क्लाउड सर्वे (Firestore)' : '🟢 NP सर्वे कनेक्टेड');
            statusBadge.innerHTML = `<span style="width: 8px; height: 8px; border-radius: 50%; background: #34D399; display: inline-block;"></span> ${label}`;
            statusBadge.style.background = 'rgba(255,255,255,0.22)';
        }

        // Populate Zones with house counts
        if (zoneSelect) {
            const zoneList = streetAuditState.zones.length > 0 ? streetAuditState.zones : streetAuditState.wards;
            const isZone = streetAuditState.zones.length > 0;
            let html = `<option value="">-- समस्त ${isZone ? 'ज़ोन' : 'वार्ड'} (${zoneList.length}) --</option>`;
            zoneList.forEach(z => {
                const cnt = (streetAuditState.houseCountByZone && streetAuditState.houseCountByZone[z]) || 
                            (streetAuditState.houseCountByWard && streetAuditState.houseCountByWard[z]);
                const countBadge = cnt ? ` (${cnt} मकान)` : '';
                html += `<option value="${z}">${isZone ? 'ज़ोन ' + z : z}${countBadge}</option>`;
            });
            zoneSelect.innerHTML = html;
        }

        populateStreetDropdown('');

    } catch (err) {
        console.warn('[StreetAudit] Streets load warning:', err.message);
        if (offlineAlert) {
            offlineAlert.style.display = 'block';
            const msgEl = document.getElementById('surveyOfflineMsg');
            if (msgEl) msgEl.innerText = err.message;
        }
        if (statusBadge) {
            statusBadge.innerHTML = '<span style="width: 8px; height: 8px; border-radius: 50%; background: #EF4444; display: inline-block;"></span> डिस्कनेक्टेड';
            statusBadge.style.background = 'rgba(239,68,68,0.25)';
        }
    }
}

function populateStreetDropdown(selectedZone) {
    const streetSelect = document.getElementById('surveyStreetSelect');
    if (!streetSelect) return;

    let list = [];
    if (selectedZone && streetAuditState.streetsByZone && streetAuditState.streetsByZone[selectedZone]) {
        list = streetAuditState.streetsByZone[selectedZone];
    } else if (selectedZone && streetAuditState.streetsByWard && streetAuditState.streetsByWard[selectedZone]) {
        list = streetAuditState.streetsByWard[selectedZone];
    } else {
        list = streetAuditState.allStreets || [];
    }

    const zoneLabel = selectedZone ? `ज़ोन ${selectedZone}` : 'शहर';
    let html = `<option value="ALL">-- 🌟 सम्पूर्ण ${zoneLabel} (समस्त गलियाँ - एक साथ मिलान) --</option>`;
    list.forEach(s => {
        const cnt = streetAuditState.houseCountByStreet && streetAuditState.houseCountByStreet[s];
        const countBadge = cnt ? ` (${cnt} मकान)` : '';
        html += `<option value="${s}">गली ${s}${countBadge}</option>`;
    });
    streetSelect.innerHTML = html;
}

async function loadStreetAuditData() {
    const streetSelect = document.getElementById('surveyStreetSelect');
    const zoneSelect = document.getElementById('surveyZoneSelect') || document.getElementById('surveyWardSelect');
    const minAgeSelect = document.getElementById('surveyMinAgeInput');
    const loadBtn = document.getElementById('btnLoadStreetAudit');

    const street = streetSelect ? streetSelect.value.trim() : '';
    const zone = (zoneSelect && zoneSelect.value) ? zoneSelect.value.trim() : '';
    const minAge = minAgeSelect ? parseInt(minAgeSelect.value) || 17 : 17;

    const isAllStreets = (!street || street === 'ALL');
    const targetScopeLabel = isAllStreets
        ? (zone ? `सम्पूर्ण ज़ोन ${zone} (समस्त गलियाँ)` : 'सम्पूर्ण सर्वेक्षण (समस्त ज़ोन व गलियाँ)')
        : `गली '${street}' ${zone ? '(ज़ोन ' + zone + ')' : ''}`;

    const originalBtnHtml = loadBtn ? loadBtn.innerHTML : '';
    if (loadBtn) {
        loadBtn.disabled = true;
        loadBtn.innerHTML = '<span class="loading-spinner-sm" style="display: inline-block; width: 16px; height: 16px; border: 2px solid white; border-top-color: transparent; border-radius: 50%; animation: spin 0.8s linear infinite;"></span> <span>सत्यापन जारी है...</span>';
    }

    const container = document.getElementById('streetAuditHousesContainer');
    if (container) {
        container.innerHTML = `
            <div style="text-align: center; padding: 60px 20px; color: #475569; background: white; border-radius: 12px;">
                <div class="loading-spinner" style="margin: 0 auto 16px auto; width: 44px; height: 44px; border: 4px solid #E2E8F0; border-top-color: #059669; border-radius: 50%; animation: spin 0.8s linear infinite;"></div>
                <h3 style="margin: 0 0 6px 0; font-size: 1.15rem; color: #1E293B;">${targetScopeLabel} के मकान एवं सदस्य लोड हो रहे हैं...</h3>
                <p style="margin: 0; font-size: 0.88rem; color: #64748B;">वोटर लिस्ट डेटाबेस के साथ सदस्य व संबंधी के नाम का ध्वन्यात्मक मिलान किया जा रहा है...</p>
            </div>
        `;
    }

    try {
        const fetchFn = typeof adminFetch === 'function' ? adminFetch : fetch;
        const params = new URLSearchParams({
            min_age: minAge
        });
        if (street && street !== 'ALL') params.append('street', street);
        if (zone && zone !== 'ALL') params.append('zone', zone);

        const res = await fetchFn(`/api/survey-audit/street-voters?${params.toString()}`);
        if (!res.ok) {
            const errData = await res.json().catch(() => ({}));
            throw new Error(errData.detail || 'ऑडिट डेटा प्राप्त करने में विफल।');
        }

        const data = await res.json();
        streetAuditState.currentAudit = data;

        // Update Stat Cards
        const statHouses = document.getElementById('auditStatHouses');
        const statEligible = document.getElementById('auditStatEligible');
        const statReg = document.getElementById('auditStatRegistered');
        const statForm6_18 = document.getElementById('auditStatForm6_18');
        const statForm6_17 = document.getElementById('auditStatForm6_17');

        if (statHouses) statHouses.innerText = (data.totalHouses || 0).toLocaleString();
        if (statEligible) statEligible.innerText = (data.totalEligibleMembers || 0).toLocaleString();
        if (statReg) statReg.innerText = (data.totalRegistered || 0).toLocaleString();
        if (statForm6_18) statForm6_18.innerText = (data.form6_18plusCount || 0).toLocaleString();
        if (statForm6_17) statForm6_17.innerText = (data.form6_17plusCount || 0).toLocaleString();

        const statsGrid = document.getElementById('streetAuditStatsGrid');
        const filterSec = document.getElementById('streetAuditFilterSection');
        if (statsGrid) statsGrid.style.display = 'grid';
        if (filterSec) filterSec.style.display = 'block';

        renderStreetAuditHouses();
        showToast(`सत्यापन पूर्ण (${targetScopeLabel}): कुल ${data.totalHouses} मकान, ${data.totalEligibleMembers} सदस्य विश्लेषित।`, 'success');

    } catch (err) {
        showToast('त्रुटि: ' + err.message, 'error');
        if (container) {
            container.innerHTML = `
                <div style="text-align: center; padding: 40px 20px; color: #DC2626; background: #FEF2F2; border-radius: 12px; border: 1px solid #FCA5A5;">
                    <div style="font-size: 32px; margin-bottom: 10px;">⚠️</div>
                    <h3 style="margin: 0 0 6px 0;">डेटा लोड करने में त्रुटि</h3>
                    <p style="margin: 0 0 16px 0; font-size: 0.9rem;">${err.message}</p>
                    <button class="btn-primary" onclick="loadStreetAuditData()" style="padding: 6px 16px;">पुनः प्रयास करें</button>
                </div>
            `;
        }
    } finally {
        if (loadBtn) {
            loadBtn.disabled = false;
            loadBtn.innerHTML = originalBtnHtml;
        }
    }
}

function renderStreetAuditHouses() {
    const container = document.getElementById('streetAuditHousesContainer');
    if (!container || !streetAuditState.currentAudit) return;

    const data = streetAuditState.currentAudit;
    const rawHouses = data.houses || [];

    // Filter houses
    const filter = streetAuditState.filterMode;
    const q = streetAuditState.searchQuery;

    let filtered = rawHouses.filter(h => {
        // Mode filter
        if (filter === 'unregistered' && !h.hasUnregistered) return false;
        if (filter === 'registered' && h.hasUnregistered) return false;
        if (filter === 'form6_18' && !(h.eligibleMembers || []).some(m => !m.isRegistered && (parseInt(m.age) || 0) >= 18)) return false;
        if (filter === 'form6_17' && !(h.eligibleMembers || []).some(m => !m.isRegistered && (parseInt(m.age) || 0) === 17)) return false;

        // Query filter
        if (q) {
            const hNo = (h.houseNumber || '').toLowerCase();
            const owner = (h.ownerName || '').toLowerCase();
            const propId = (h.propertyId || '').toLowerCase();
            const membersMatch = (h.eligibleMembers || []).some(m => 
                (m.name || '').toLowerCase().includes(q) || 
                (m.fatherHusbandName || '').toLowerCase().includes(q) ||
                (m.mobile || '').includes(q)
            );
            if (!hNo.includes(q) && !owner.includes(q) && !propId.includes(q) && !membersMatch) {
                return false;
            }
        }
        return true;
    });

    if (filtered.length === 0) {
        container.innerHTML = `
            <div style="text-align: center; padding: 50px 20px; color: #64748B; background: white; border-radius: 12px; border: 1px dashed #CBD5E1;">
                <div style="font-size: 32px; margin-bottom: 8px;">🔍</div>
                <h3 style="margin: 0 0 4px 0; color: #1E293B;">कोई मकान नहीं मिला</h3>
                <p style="margin: 0; font-size: 0.88rem;">दिए गए फ़िल्टर या खोज शब्दों के अनुसार कोई मकान उपलब्ध नहीं है।</p>
            </div>
        `;
        return;
    }

    let html = '<div style="display: flex; flex-direction: column; gap: 16px;">';

    filtered.forEach((h, hIdx) => {
        const hNo = h.houseNumber || 'अज्ञात';
        const owner = h.ownerName || h.headName || 'उपलब्ध नहीं';
        const propId = h.propertyId ? `[${h.propertyId}]` : '';
        const unregCount = h.houseUnregisteredCount || 0;
        const regCount = h.houseRegisteredCount || 0;
        const members = h.eligibleMembers || [];

        // Status badge for house
        let houseStatusBadge = '';
        if (unregCount === 0 && regCount > 0) {
            houseStatusBadge = `<span style="padding: 4px 10px; border-radius: 16px; font-size: 0.78rem; font-weight: 700; background: #DCFCE7; color: #15803D; border: 1px solid #BBF7D0;">✅ सभी ${regCount} सदस्य पंजीकृत</span>`;
        } else if (unregCount > 0) {
            houseStatusBadge = `<span style="padding: 4px 10px; border-radius: 16px; font-size: 0.78rem; font-weight: 700; background: #FEE2E2; color: #B91C1C; border: 1px solid #FECACA;">🔴 ${unregCount} सदस्य का वोट नहीं बना</span>`;
        } else {
            houseStatusBadge = `<span style="padding: 4px 10px; border-radius: 16px; font-size: 0.78rem; font-weight: 600; background: #F1F5F9; color: #64748B;">कोई पात्र सदस्य नहीं</span>`;
        }

        html += `
            <div class="street-house-card" style="background: white; border-radius: 12px; border: 1px solid ${unregCount > 0 ? '#FCA5A5' : '#E2E8F0'}; box-shadow: 0 2px 8px rgba(0,0,0,0.04); overflow: hidden;">
                <!-- House Header -->
                <div style="padding: 14px 20px; background: ${unregCount > 0 ? '#FFF5F5' : '#F8FAFC'}; border-bottom: 1px solid #E2E8F0; display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 10px;">
                    <div style="display: flex; align-items: center; gap: 12px;">
                        <span style="font-size: 1.1rem; font-weight: 750; color: #1E293B;">
                            🏠 मकान नं०: <span style="color: #059669;">${hNo}</span>
                        </span>
                        <span style="font-size: 0.85rem; color: #64748B; font-weight: 500;">
                            मुखिया/स्वामी: <strong style="color: #334155;">${owner}</strong> ${propId}
                        </span>
                    </div>
                    <div style="display: flex; align-items: center; gap: 8px;">
                        ${houseStatusBadge}
                        <button type="button" onclick="openHouseVotersModal(${hIdx})" style="background: ${members.some(mb => mb.isRegistered && mb.voterRecord) ? '#F0FDF4' : '#F8FAFC'}; border: 1px solid ${members.some(mb => mb.isRegistered && mb.voterRecord) ? '#86EFAC' : '#CBD5E1'}; color: ${members.some(mb => mb.isRegistered && mb.voterRecord) ? '#166534' : '#475569'}; padding: 4px 10px; border-radius: 6px; font-size: 0.8rem; font-weight: 700; cursor: pointer; display: inline-flex; align-items: center; gap: 5px; box-shadow: 0 1px 2px rgba(0, 0, 0, 0.05);" title="वोटर लिस्ट के इस मकान में किस-किस के वोट हैं देखें व सदस्य मैप करें">
                            <span>🏠 मकान के सभी वोट देखें</span>
                        </button>
                        <button type="button" onclick="printHouseVotersDirect(${hIdx})" style="background: white; border: 1px solid #CBD5E1; color: #1E293B; padding: 4px 9px; border-radius: 6px; font-size: 0.8rem; font-weight: 600; cursor: pointer; display: inline-flex; align-items: center; gap: 4px;" title="इस मकान की पारिवारिक मतदाता पर्ची A4 प्रिंट करें">
                            <span>🖨️ पर्ची प्रिंट</span>
                        </button>
                        <span style="font-size: 0.82rem; color: #64748B; background: white; padding: 3px 8px; border-radius: 6px; border: 1px solid #CBD5E1;">
                            कुल सदस्य (17+): <strong>${members.length}</strong>
                        </span>
                    </div>
                </div>

                <!-- Members Table -->
                <div style="overflow-x: auto;">
                    <table style="width: 100%; border-collapse: collapse; text-align: left; font-size: 0.88rem;">
                        <thead>
                            <tr style="background: #F8FAFC; border-bottom: 2px solid #E2E8F0; color: #475569; font-weight: 600; font-size: 0.82rem;">
                                <th style="padding: 10px 16px; width: 40px; text-align: center;">क्र०</th>
                                <th style="padding: 10px 16px;">सदस्य का नाम</th>
                                <th style="padding: 10px 16px;">संबंधी (पिता/पति) का नाम</th>
                                <th style="padding: 10px 12px; width: 80px; text-align: center;">आयु / लिंग</th>
                                <th style="padding: 10px 12px; width: 100px;">सम्बन्ध</th>
                                <th style="padding: 10px 12px; width: 110px;">मोबाइल</th>
                                <th style="padding: 10px 16px; min-width: 220px;">मतदाता स्थिति (लाइव वोटर लिस्ट)</th>
                                <th style="padding: 10px 16px; min-width: 180px;">आवश्यक कार्यवाही</th>
                            </tr>
                        </thead>
                        <tbody>
        `;

        if (members.length === 0) {
            html += `
                <tr>
                    <td colspan="8" style="padding: 18px; text-align: center; color: #94A3B8;">
                        इस मकान में 17+ आयु का कोई सदस्य दर्ज नहीं है।
                    </td>
                </tr>
            `;
        } else {
            const hasAnyRegInHouse = members.some(mb => mb.isRegistered && mb.voterRecord);

            members.forEach((m, mIdx) => {
                const isReg = m.isRegistered;
                const vRec = m.voterRecord;
                const mAge = parseInt(m.age) || 0;

                let statusBadgeHtml = '';
                let actionHtml = '';

                if (isReg && vRec) {
                    const isManual = vRec.is_manual_mapped || (m.matchDescription && m.matchDescription.includes("मैन्युअल"));
                    statusBadgeHtml = `
                        <div style="display: flex; flex-direction: column; gap: 2px;">
                            <span style="display: inline-flex; align-items: center; gap: 4px; padding: 2px 8px; border-radius: 12px; font-size: 0.78rem; font-weight: 700; background: ${isManual ? '#EFF6FF' : '#DCFCE7'}; color: ${isManual ? '#1D4ED8' : '#15803D'}; width: fit-content; border: 1px solid ${isManual ? '#BFDBFE' : '#BBF7D0'};">
                                <span style="width: 6px; height: 6px; border-radius: 50%; background: ${isManual ? '#2563EB' : '#16A34A'};"></span>
                                ${isManual ? 'वोट बना हुआ है (मैप किया गया)' : 'वोट बना हुआ है'}
                            </span>
                            <div style="font-size: 0.78rem; color: #475569; margin-top: 2px;">
                                <strong>EPIC:</strong> ${vRec.epic_no} &bull; <strong>भाग:</strong> ${vRec.part_no} &bull; <strong>क्रम:</strong> #${vRec.serial_no}
                            </div>
                            <div style="font-size: 0.74rem; color: #64748B;">
                                वोटर लिस्ट में दर्ज: '${vRec.voter_name}' ${vRec.relation_type || 'पिता'} '${vRec.voter_relative}'
                            </div>
                        </div>
                    `;
                    actionHtml = `
                        <button type="button" class="btn-verified-voter" onclick="openHouseVotersModal(${hIdx}, ${mIdx})" style="background: #ECFDF5; color: #047857; border: 1px solid #A7F3D0; padding: 6px 12px; border-radius: 8px; font-size: 0.8rem; font-weight: 700; cursor: pointer; display: inline-flex; align-items: center; gap: 6px; box-shadow: 0 1px 3px rgba(4,120,87,0.08); transition: all 0.2s;" onmouseover="this.style.background='#D1FAE5'; this.style.borderColor='#059669';" onmouseout="this.style.background='#ECFDF5'; this.style.borderColor='#A7F3D0';" title="क्लिक करें: वोटर लिस्ट के इस मकान में किस-किस के वोट हैं देखें व सदस्य मैप करें">
                            <span>✓</span> <span>सत्यापित मतदाता</span>
                            <span style="font-size: 0.74rem; background: #059669; color: white; padding: 2px 7px; border-radius: 10px; font-weight: 600;">मकान वोट देखें 🔍</span>
                        </button>
                    `;
                } else {
                    const is18Plus = mAge >= 18;
                    const badgeBg = is18Plus ? '#FEE2E2' : '#FEF3C7';
                    const badgeColor = is18Plus ? '#991B1B' : '#92400E';
                    const dotColor = is18Plus ? '#DC2626' : '#D97706';
                    const badgeText = is18Plus ? 'वोट नहीं बना (18+)' : 'वोट नहीं बना (17+ अग्रिम)';

                    statusBadgeHtml = `
                        <div style="display: flex; flex-direction: column; gap: 2px;">
                            <span style="display: inline-flex; align-items: center; gap: 4px; padding: 2px 8px; border-radius: 12px; font-size: 0.78rem; font-weight: 700; background: ${badgeBg}; color: ${badgeColor}; width: fit-content;">
                                <span style="width: 6px; height: 6px; border-radius: 50%; background: ${dotColor};"></span>
                                ${badgeText}
                            </span>
                            <div style="font-size: 0.74rem; color: #64748B; margin-top: 2px;">
                                सम्पूर्ण वोटर डेटाबेस में नाम व सम्बन्धी का रिकॉर्ड अनुपलब्ध
                            </div>
                        </div>
                    `;

                    actionHtml = `
                        <div style="display: flex; flex-direction: column; gap: 4px;">
                            <span style="font-size: 0.82rem; font-weight: 700; color: ${is18Plus ? '#DC2626' : '#D97706'};">
                                ${is18Plus ? '📝 फॉर्म 6 भरें (नया मतदाता)' : '⏳ अग्रिम फॉर्म 6 (17+ युवा)'}
                            </span>
                            <button type="button" onclick="openHouseVotersModal(${hIdx}, ${mIdx})" style="background: white; border: 1px dashed #059669; color: #059669; padding: 3px 8px; border-radius: 6px; font-size: 0.76rem; font-weight: 600; cursor: pointer; display: inline-flex; align-items: center; gap: 4px; width: fit-content;" title="यदि इनका वोट पहले से बना है तो वोटर लिस्ट के इस मकान के वोटरों से लिंक करें">
                                <span>🔗 सदस्य मैपिंग करें</span>
                            </button>
                        </div>
                    `;
                }

                const rowBg = isReg ? 'transparent' : (mAge >= 18 ? 'rgba(254, 242, 242, 0.45)' : 'rgba(254, 243, 199, 0.25)');

                html += `
                    <tr style="border-bottom: 1px solid #F1F5F9; background: ${rowBg};">
                        <td style="padding: 10px 16px; text-align: center; color: #94A3B8; font-weight: 600;">${mIdx + 1}</td>
                        <td style="padding: 10px 16px; font-weight: 650; color: #1E293B;">
                            ${m.name || ''}
                        </td>
                        <td style="padding: 10px 16px; color: #334155;">
                            ${m.fatherHusbandName || ''}
                        </td>
                        <td style="padding: 10px 12px; text-align: center;">
                            <span style="font-weight: 600; color: #1E293B;">${m.age || '-'}</span> 
                            <span style="font-size: 0.78rem; color: #64748B;">(${m.gender || '-'})</span>
                        </td>
                        <td style="padding: 10px 12px; color: #475569;">
                            ${m.relationship || '-'}
                        </td>
                        <td style="padding: 10px 12px; font-family: monospace; font-size: 0.82rem; color: #475569;">
                            ${m.mobile || '-'}
                        </td>
                        <td style="padding: 10px 16px;">
                            ${statusBadgeHtml}
                        </td>
                        <td style="padding: 10px 16px;">
                            ${actionHtml}
                        </td>
                    </tr>
                `;
            });
        }

        html += `
                        </tbody>
                    </table>
                </div>
            </div>
        `;
    });

    html += '</div>';
    container.innerHTML = html;
}

async function downloadStreetAuditExcel() {
    if (!streetAuditState.currentAudit) {
        showToast('कृपया पहले किसी गली का डेटा लोड करें।', 'warning');
        return;
    }

    const audit = streetAuditState.currentAudit;
    const street = audit.street;
    const zone = audit.zone || (document.getElementById('surveyZoneSelect')?.value || '');
    const ward = audit.ward || '';
    const minAgeSelect = document.getElementById('surveyMinAgeInput');
    const minAge = minAgeSelect ? parseInt(minAgeSelect.value) || 17 : 17;

    const exportBtn = document.getElementById('btnExportStreetAuditExcel');
    const origHtml = exportBtn ? exportBtn.innerHTML : '';
    if (exportBtn) {
        exportBtn.disabled = true;
        exportBtn.innerHTML = '<span>एक्सेल तैयार हो रहा है...</span>';
    }

    try {
        const fetchFn = typeof adminFetch === 'function' ? adminFetch : fetch;
        const res = await fetchFn('/api/survey-audit/export-excel', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                street: street,
                zone: zone,
                ward: ward,
                min_age: minAge,
                filter_mode: streetAuditState.filterMode || 'all'
            })
        });

        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || 'एक्सेल फ़ाइल डाउनलोड में विफल।');
        }

        const blob = await res.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        const zoneSuffix = zone ? `_ज़ोन_${zone}` : '';
        const streetPart = (street && street !== 'ALL') ? `गली_${street}` : 'सम्पूर्ण_सर्वे';
        a.download = `डोर_टू_डोर_वोटर_सत्यापन_${streetPart}${zoneSuffix}.xlsx`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        window.URL.revokeObjectURL(url);
        showToast('एक्सेल रिपोर्ट सफलतापूर्वक डाउनलोड हो गई!', 'success');
    } catch (err) {
        showToast('त्रुटि: ' + err.message, 'error');
    } finally {
        if (exportBtn) {
            exportBtn.disabled = false;
            exportBtn.innerHTML = origHtml;
        }
    }
}

// ==============================================================================
// HOUSE VOTERS & NPPROPERTY SURVEY MEMBER MAPPING MODAL
// Rule: "गली के सत्यापित मतदाता पर क्लिक करने से वोटर लिस्ट के उक्त मकान मे किस
//        किस के वोट हैं उन्हे दिखा सके । और उक्त लिस्ट से NPPropertServey के
//        किसी परिवार के सदस्य को मैप करने की भी सुविधा हो"
// ==============================================================================

const houseVotersState = {
    currentHouse: null,
    currentMember: null,
    anchorVRec: null,
    houseVoters: [],
    allHouseVoters: [],
    familyMembers: [],
    familyMappings: {},
    activeTab: 'voters',
    distinctParts: []
};

async function loadDistinctParts() {
    if (houseVotersState.distinctParts && houseVotersState.distinctParts.length > 0) {
        return houseVotersState.distinctParts;
    }
    try {
        const fetchFn = typeof adminFetch === 'function' ? adminFetch : fetch;
        const res = await fetchFn('/api/voters/distinct-parts');
        if (res.ok) {
            const data = await res.json();
            houseVotersState.distinctParts = data.parts || [];
            return houseVotersState.distinctParts;
        }
    } catch (e) {
        console.warn('[StreetAudit] Failed to load distinct parts:', e);
    }
    return [];
}

function switchHvmTab(tabName) {
    houseVotersState.activeTab = tabName;
    const tabVotersBtn = document.getElementById('hvmTabVotersBtn');
    const tabMembersBtn = document.getElementById('hvmTabMembersBtn');
    const tabGlobalBtn = document.getElementById('hvmTabGlobalSearchBtn');
    const votersContent = document.getElementById('hvmVotersTabContent');
    const membersContent = document.getElementById('hvmMembersTabContent');
    const globalContent = document.getElementById('hvmGlobalTabContent');

    const tabs = [
        { name: 'voters', btn: tabVotersBtn, content: votersContent },
        { name: 'members', btn: tabMembersBtn, content: membersContent },
        { name: 'global', btn: tabGlobalBtn, content: globalContent }
    ];

    tabs.forEach(t => {
        if (!t.btn || !t.content) return;
        if (t.name === tabName) {
            t.btn.style.borderBottomColor = '#059669';
            t.btn.style.color = '#059669';
            t.btn.style.fontWeight = '700';
            t.content.style.display = 'block';
        } else {
            t.btn.style.borderBottomColor = 'transparent';
            t.btn.style.color = '#64748B';
            t.btn.style.fontWeight = '600';
            t.content.style.display = 'none';
        }
    });

    if (tabName === 'global') {
        const input = document.getElementById('hvmGlobalSearchInput');
        if (input && !input.value.trim() && houseVotersState.currentMember) {
            input.value = houseVotersState.currentMember.name || '';
            executeHvmGlobalSearch();
        }
    }
}

async function loadHouseVotersManual() {
    const partSelect = document.getElementById('hvmPartNoSelect');
    const partNo = partSelect ? partSelect.value : 'ALL';
    const houseNo = (document.getElementById('hvmHouseNoInput')?.value || '').trim();
    if (!houseNo) {
        showToast('कृपया मकान संख्या दर्ज करें।', 'warning');
        return;
    }
    await fetchAndDisplayHouseVoters(partNo, houseNo);
}

async function fetchAndDisplayHouseVoters(partNo, houseNo) {
    const pSelect = document.getElementById('hvmPartNoSelect');
    const hInput = document.getElementById('hvmHouseNoInput');
    const voterDetails = document.getElementById('hvmVoterListDetails');
    const noticeText = document.getElementById('hvmVotersNoticeText');
    const tbodyVoters = document.getElementById('hvmVotersTableBody');

    const cleanPart = (!partNo || partNo === 'ALL' || partNo === 'null' || partNo === 'undefined') ? 'ALL' : String(partNo).trim();
    const cleanHouse = String(houseNo || '').trim();

    if (hInput) hInput.value = cleanHouse;

    const displayPartText = cleanPart === 'ALL' ? 'समस्त भाग' : `भाग नं० ${cleanPart}`;
    if (voterDetails) voterDetails.innerHTML = `🗳️ ${displayPartText} &bull; मकान: <strong>${cleanHouse}</strong>`;

    if (tbodyVoters) {
        tbodyVoters.innerHTML = `
            <tr>
                <td colspan="7" style="padding: 30px; text-align: center; color: #64748B;">
                    <div class="loading-spinner-sm" style="display: inline-block; width: 20px; height: 20px; border: 2px solid #CBD5E1; border-top-color: #059669; border-radius: 50%; animation: spin 0.8s linear infinite;"></div>
                    <span style="margin-left: 8px;">${displayPartText}, मकान नं० ${cleanHouse} के मतदाता लोड हो रहे हैं...</span>
                </td>
            </tr>
        `;
    }

    try {
        const fetchFn = typeof adminFetch === 'function' ? adminFetch : fetch;
        const queryParams = new URLSearchParams({ house_no: cleanHouse });
        if (cleanPart !== 'ALL') {
            queryParams.append('part_no', cleanPart);
        }

        const res = await fetchFn(`/api/voters/by-house?${queryParams.toString()}`);
        if (!res.ok) throw new Error('मकान के वोटर लोड नहीं हो सके।');
        const data = await res.json();
        const voters = data.voters || [];
        houseVotersState.houseVoters = voters;
        houseVotersState.allHouseVoters = voters;

        // Populate parts dropdown with distinct parts + counts
        const allParts = await loadDistinctParts();
        if (pSelect) {
            let partOptions = `<option value="ALL">-- समस्त भाग (${allParts.length}) --</option>`;
            allParts.forEach(p => {
                const isSelected = (cleanPart === String(p.part_no));
                partOptions += `<option value="${p.part_no}" ${isSelected ? 'selected' : ''}>भाग ${p.part_no} (${p.voter_count} मतदाता)</option>`;
            });
            pSelect.innerHTML = partOptions;
            pSelect.value = cleanPart;
        }

        // Show parts found info notice
        const partsFound = data.parts_found || [];
        if (noticeText) {
            if (cleanPart === 'ALL') {
                if (partsFound.length > 1) {
                    const partsDesc = partsFound.map(pf => `भाग ${pf.part_no} (${pf.count})`).join(', ');
                    noticeText.innerHTML = `मकान संख्या <strong>${cleanHouse}</strong> में कुल <strong>${voters.length}</strong> मतदाता मिले (${partsDesc}):`;
                } else if (partsFound.length === 1) {
                    noticeText.innerHTML = `मकान संख्या <strong>${cleanHouse}</strong> के भाग <strong>${partsFound[0].part_no}</strong> में <strong>${voters.length}</strong> मतदाता:`;
                } else {
                    noticeText.innerHTML = `मकान संख्या <strong>${cleanHouse}</strong> में वोटर लिस्ट का कोई मतदाता नहीं मिला।`;
                }
            } else {
                noticeText.innerHTML = `भाग <strong>${cleanPart}</strong>, मकान संख्या <strong>${cleanHouse}</strong> के मतदाता (${voters.length}):`;
            }
        }

        // Fetch family mappings if familyId exists
        const house = houseVotersState.currentHouse;
        if (house && house.familyId) {
            const mapRes = await fetchFn(`/api/survey-audit/house-mappings?family_id=${encodeURIComponent(house.familyId)}`);
            if (mapRes.ok) {
                const mapData = await mapRes.json();
                const mDict = {};
                (mapData.mappings || []).forEach(mp => {
                    mDict[mp.member_id] = mp;
                });
                houseVotersState.familyMappings = mDict;
            }
        }

        renderHvmVoters();
        renderHvmMembers();

    } catch (err) {
        if (tbodyVoters) {
            tbodyVoters.innerHTML = `
                <tr>
                    <td colspan="7" style="padding: 24px; text-align: center; color: #DC2626;">
                        त्रुटि: ${err.message}
                    </td>
                </tr>
            `;
        }
    }
}

async function openHouseVotersModal(hIdx, mIdx) {
    if (!isSuperAdminUser()) {
        showToast('पहुँच अस्वीकृत: यह सुविधा केवल सुपर एडमिन (harshsamrat) हेतु आरक्षित है।', 'warning');
        return;
    }
    if (!streetAuditState.currentAudit || !streetAuditState.currentAudit.houses) {
        showToast('ऑडिट डेटा लोड नहीं है।', 'warning');
        return;
    }

    const house = streetAuditState.currentAudit.houses[hIdx];
    if (!house) return;

    houseVotersState.currentHouse = house;
    houseVotersState.familyMembers = house.eligibleMembers || [];

    let anchorVRec = null;
    let targetMember = null;
    if (mIdx !== undefined && house.eligibleMembers && house.eligibleMembers[mIdx]) {
        targetMember = house.eligibleMembers[mIdx];
        anchorVRec = targetMember.voterRecord || null;
    } else {
        const regMember = (house.eligibleMembers || []).find(m => m.isRegistered && m.voterRecord);
        if (regMember) {
            anchorVRec = regMember.voterRecord;
            targetMember = regMember;
        }
    }
    houseVotersState.currentMember = targetMember;
    houseVotersState.anchorVRec = anchorVRec;

    const modal = document.getElementById('houseVotersModal');
    if (!modal) return;

    const titleElem = document.getElementById('hvmHouseTitle');
    const subElem = document.getElementById('hvmHouseSubtitle');
    const surveyDetails = document.getElementById('hvmSurveyHouseDetails');

    if (titleElem) titleElem.innerText = `मकान नं० ${house.houseNumber || 'अज्ञात'} — मतदाता सत्यापन एवं सदस्य मैपिंग`;
    if (subElem) subElem.innerText = `स्वामी: ${house.ownerName || 'उपलब्ध नहीं'} • गली: ${streetAuditState.currentAudit.street || ''} (ज़ोन: ${streetAuditState.currentAudit.zone || '-'})`;
    if (surveyDetails) surveyDetails.innerHTML = `🏠 मकान: <strong>${house.houseNumber || '-'}</strong> | स्वामी: <strong>${house.ownerName || '-'}</strong> ${house.propertyId ? `[${house.propertyId}]` : ''}`;

    // Populate Global Tab Target Member Selector & Part Selectors
    await populateHvmGlobalControls(targetMember);

    modal.style.display = 'flex';

    if (targetMember && !targetMember.isRegistered) {
        switchHvmTab('members');
    } else {
        switchHvmTab('voters');
    }

    // Auto-detect part number or search across ALL parts
    const partNo = (anchorVRec && anchorVRec.part_no) ? anchorVRec.part_no : 'ALL';
    const houseNo = (anchorVRec && anchorVRec.voter_house) ? anchorVRec.voter_house : (house.houseNumber || '1');

    await fetchAndDisplayHouseVoters(partNo, houseNo);
}

async function populateHvmGlobalControls(targetMember) {
    const memberSelect = document.getElementById('hvmGlobalTargetMemberSelect');
    const partSelect = document.getElementById('hvmGlobalPartSelect');
    const searchInput = document.getElementById('hvmGlobalSearchInput');

    if (memberSelect) {
        const members = houseVotersState.familyMembers || [];
        let html = '';
        members.forEach((m, idx) => {
            const isReg = m.isRegistered;
            const isSelected = targetMember ? (m.memberId === targetMember.memberId) : (!isReg && idx === 0);
            const badge = isReg ? ' [✓ पंजीकृत]' : ' [🔴 अपंजीकृत]';
            html += `<option value="${m.memberId || idx}" ${isSelected ? 'selected' : ''}>${m.name} (${m.fatherHusbandName || '-'}, ${m.age} वर्ष)${badge}</option>`;
        });
        memberSelect.innerHTML = html;
    }

    const allParts = await loadDistinctParts();
    if (partSelect) {
        let partOpts = '<option value="">-- समस्त भाग (All Parts) --</option>';
        allParts.forEach(p => {
            partOpts += `<option value="${p.part_no}">भाग ${p.part_no} (${p.voter_count} मतदाता)</option>`;
        });
        partSelect.innerHTML = partOpts;
    }

    if (searchInput && targetMember) {
        searchInput.value = targetMember.name || '';
    }
}

function filterHvmVoters() {
    const q = (document.getElementById('hvmVoterSearchInput')?.value || '').trim().toLowerCase();
    if (!q) {
        houseVotersState.houseVoters = [...houseVotersState.allHouseVoters];
    } else {
        houseVotersState.houseVoters = houseVotersState.allHouseVoters.filter(v =>
            (v.name || '').toLowerCase().includes(q) ||
            (v.relation_name || '').toLowerCase().includes(q) ||
            (v.epic_no || '').toLowerCase().includes(q) ||
            String(v.serial_no || '').includes(q)
        );
    }
    renderHvmVoters();
}

function renderHvmVoters() {
    const tbody = document.getElementById('hvmVotersTableBody');
    const badge = document.getElementById('hvmVoterCountBadge');
    const voters = houseVotersState.houseVoters || [];

    if (badge) badge.innerText = voters.length;
    if (!tbody) return;

    if (voters.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="7" style="padding: 26px; text-align: center; color: #94A3B8;">
                    इस मकान संख्या में कोई मतदाता दर्ज नहीं मिला। आप 'समस्त भाग' या अन्य भाग चुनकर पुनः खोज सकते हैं।
                </td>
            </tr>
        `;
        return;
    }

    let html = '';
    voters.forEach((v) => {
        const isMapped = v.is_mapped || Object.values(houseVotersState.familyMappings).some(m => m.voter_id === v.id);
        const mappedInfo = Object.values(houseVotersState.familyMappings).find(m => m.voter_id === v.id) || (v.mapped_member_name ? { member_name: v.mapped_member_name } : null);

        let actionCol = '';
        if (isMapped) {
            actionCol = `
                <span style="font-size: 0.76rem; font-weight: 700; color: #15803D; background: #DCFCE7; padding: 2px 8px; border-radius: 10px; display: inline-block;">
                    ✓ मैप: ${mappedInfo?.member_name || 'सदस्य'}
                </span>
            `;
        } else {
            actionCol = `
                <button type="button" onclick="selectVoterForMapping(${v.id})" style="background: white; border: 1px solid #CBD5E1; color: #059669; padding: 3px 8px; border-radius: 6px; font-size: 0.76rem; font-weight: 600; cursor: pointer;" title="इस मतदाता को NPPropertyServey सदस्य से मैप करें">
                    🔗 सदस्य से मैप करें
                </button>
            `;
        }

        html += `
            <tr style="border-bottom: 1px solid #F1F5F9; ${isMapped ? 'background: rgba(220, 252, 231, 0.25);' : ''}">
                <td style="padding: 8px 12px; text-align: center; color: #64748B; font-weight: 700;">${v.serial_no || '-'}</td>
                <td style="padding: 8px 10px; text-align: center; font-weight: 600; color: #475569; font-size: 0.8rem;">भाग ${v.part_no || '-'}</td>
                <td style="padding: 8px 12px; font-weight: 650; color: #1E293B;">
                    ${v.name || ''}
                </td>
                <td style="padding: 8px 12px; color: #334155;">
                    <span style="font-size: 0.74rem; color: #64748B;">(${v.relation_type || 'पिता'})</span> ${v.relation_name || ''}
                </td>
                <td style="padding: 8px 8px; text-align: center; color: #475569;">
                    ${v.age || '-'} <span style="font-size: 0.74rem; color: #64748B;">(${v.gender || '-'})</span>
                </td>
                <td style="padding: 8px 12px; font-family: monospace; font-size: 0.8rem; color: #1E293B; font-weight: 600;">
                    ${v.epic_no || 'उपलब्ध नहीं'}
                </td>
                <td style="padding: 8px 12px; text-align: center;">
                    ${actionCol}
                </td>
            </tr>
        `;
    });

    tbody.innerHTML = html;
}

function selectVoterForMapping(voterId) {
    switchHvmTab('members');
    const selects = document.querySelectorAll('.hvm-voter-select');
    selects.forEach(s => {
        if (!s.disabled) s.value = voterId;
    });
    showToast('मतदाता चयनित। अब संबंधित परिवार सदस्य के सामने "मैप करें" बटन दबाएं।', 'info');
}

function renderHvmMembers() {
    const tbody = document.getElementById('hvmMembersTableBody');
    const badge = document.getElementById('hvmMemberCountBadge');
    const members = houseVotersState.familyMembers || [];
    const voters = houseVotersState.allHouseVoters || [];

    if (badge) badge.innerText = members.length;
    if (!tbody) return;

    if (members.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="7" style="padding: 24px; text-align: center; color: #94A3B8;">
                    इस मकान में कोई 17+ सदस्य दर्ज नहीं है।
                </td>
            </tr>
        `;
        return;
    }

    let voterOptionsHtml = '<option value="">-- इस मकान की वोटर लिस्ट से चुनें --</option>';
    voters.forEach(v => {
        voterOptionsHtml += `<option value="${v.id}">भाग ${v.part_no} | क्र० #${v.serial_no}: ${v.name} (${v.relation_name}) - EPIC: ${v.epic_no || 'N/A'}</option>`;
    });

    let html = '';
    members.forEach((m, idx) => {
        const mId = m.memberId || '';
        const savedMapping = houseVotersState.familyMappings[mId];
        const isMapped = Boolean(savedMapping || (m.isRegistered && m.voterRecord));
        const vInfo = savedMapping ? {
            voter_name: savedMapping.voter_name,
            epic_no: savedMapping.epic_no,
            part_no: savedMapping.part_no,
            serial_no: savedMapping.serial_no
        } : (m.voterRecord || null);

        let mappingCol = '';
        let actionCol = '';

        if (savedMapping) {
            mappingCol = `
                <div style="font-size: 0.8rem; color: #15803D; font-weight: 600;">
                    ✓ मैप: <strong>${vInfo.voter_name}</strong> (EPIC: ${vInfo.epic_no}) &bull; भाग ${vInfo.part_no}, क्र० #${vInfo.serial_no}
                </div>
            `;
            actionCol = `
                <button type="button" onclick="deleteMemberVoterMapping('${houseVotersState.currentHouse.familyId}', '${mId}', '${m.name}')" style="background: #FEE2E2; color: #DC2626; border: 1px solid #FCA5A5; padding: 4px 10px; border-radius: 6px; font-size: 0.75rem; font-weight: 600; cursor: pointer;">
                    अनलिंक ✕
                </button>
            `;
        } else if (m.isRegistered && vInfo) {
            mappingCol = `
                <div style="font-size: 0.8rem; color: #047857; font-weight: 600;">
                    ✓ स्वतः सत्यापित: <strong>${vInfo.voter_name}</strong> (EPIC: ${vInfo.epic_no}) &bull; भाग ${vInfo.part_no}, क्र० #${vInfo.serial_no}
                </div>
            `;
            actionCol = `
                <span style="font-size: 0.75rem; color: #059669; font-weight: 600; background: #DCFCE7; padding: 3px 8px; border-radius: 6px; display: inline-block;">सत्यापित ✓</span>
            `;
        } else {
            mappingCol = `
                <div style="display: flex; gap: 6px; align-items: center;">
                    <select id="hvmSelect_${idx}" class="hvm-voter-select" style="flex: 1; min-width: 140px; padding: 5px 8px; border-radius: 6px; border: 1px solid #CBD5E1; font-size: 0.78rem;">
                        ${voterOptionsHtml}
                    </select>
                    <button type="button" onclick="handleMapSubmit(${idx}, '${mId}', '${m.name}')" style="background: #059669; color: white; border: none; padding: 5px 10px; border-radius: 6px; font-size: 0.78rem; font-weight: 600; cursor: pointer; white-space: nowrap;">
                        मैप करें ✓
                    </button>
                </div>
            `;
            actionCol = `
                <button type="button" onclick="openGlobalSearchForMember(${idx})" style="background: #EFF6FF; color: #1D4ED8; border: 1px solid #BFDBFE; padding: 4px 8px; border-radius: 6px; font-size: 0.75rem; font-weight: 600; cursor: pointer; white-space: nowrap; display: inline-flex; align-items: center; gap: 4px;" title="सम्पूर्ण वोटर डेटाबेस में खोजें">
                    🌐 खोजें व लिंक
                </button>
            `;
        }

        html += `
            <tr style="border-bottom: 1px solid #F1F5F9; ${isMapped ? 'background: rgba(220, 252, 231, 0.15);' : ''}">
                <td style="padding: 8px 12px; text-align: center; color: #64748B;">${idx + 1}</td>
                <td style="padding: 8px 12px; font-weight: 650; color: #1E293B;">
                    ${m.name || ''}
                    ${m.isOwner ? '<span style="font-size: 0.68rem; background: #E0E7FF; color: #4338CA; padding: 1px 4px; border-radius: 4px; margin-left: 4px;">स्वामी</span>' : ''}
                </td>
                <td style="padding: 8px 12px; color: #334155;">
                    ${m.fatherHusbandName || '-'}
                </td>
                <td style="padding: 8px 8px; text-align: center; color: #475569;">
                    ${m.age || '-'} <span style="font-size: 0.74rem; color: #64748B;">(${m.gender || '-'})</span>
                </td>
                <td style="padding: 8px 10px; color: #64748B;">
                    ${m.relationship || '-'}
                </td>
                <td style="padding: 8px 12px;">
                    ${mappingCol}
                </td>
                <td style="padding: 8px 12px; text-align: center;">
                    ${actionCol}
                </td>
            </tr>
        `;
    });

    tbody.innerHTML = html;
}

function openGlobalSearchForMember(memberIdx) {
    const members = houseVotersState.familyMembers || [];
    const member = members[memberIdx];
    if (!member) return;

    houseVotersState.currentMember = member;
    switchHvmTab('global');

    const memberSelect = document.getElementById('hvmGlobalTargetMemberSelect');
    if (memberSelect) {
        memberSelect.value = member.memberId || memberIdx;
    }

    const searchInput = document.getElementById('hvmGlobalSearchInput');
    if (searchInput) {
        searchInput.value = member.name || '';
    }

    executeHvmGlobalSearch();
}

async function executeHvmGlobalSearch() {
    const qInput = document.getElementById('hvmGlobalSearchInput');
    const pSelect = document.getElementById('hvmGlobalPartSelect');
    const tbody = document.getElementById('hvmGlobalResultsTableBody');
    const btn = document.getElementById('btnExecHvmGlobalSearch');

    const q = (qInput?.value || '').trim();
    if (!q) {
        showToast('कृपया खोज हेतु नाम, संबंधी या EPIC दर्ज करें।', 'warning');
        return;
    }

    const partNo = pSelect?.value || '';

    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<span class="loading-spinner-sm" style="display: inline-block; width: 14px; height: 14px; border: 2px solid white; border-top-color: transparent; border-radius: 50%; animation: spin 0.8s linear infinite;"></span> <span>खोज रहे हैं...</span>';
    }

    if (tbody) {
        tbody.innerHTML = `
            <tr>
                <td colspan="9" style="padding: 30px; text-align: center; color: #64748B;">
                    <div class="loading-spinner-sm" style="display: inline-block; width: 22px; height: 22px; border: 2px solid #CBD5E1; border-top-color: #2563EB; border-radius: 50%; animation: spin 0.8s linear infinite;"></div>
                    <span style="margin-left: 8px;">'${q}' से सम्पूर्ण डेटाबेस में खोज की जा रही है...</span>
                </td>
            </tr>
        `;
    }

    try {
        const fetchFn = typeof adminFetch === 'function' ? adminFetch : fetch;
        const params = new URLSearchParams({ q: q, limit: '40' });
        if (partNo) params.append('part_no', partNo);

        const res = await fetchFn(`/api/survey-audit/search-voter-candidate?${params.toString()}`);
        if (!res.ok) throw new Error('मतदाता खोज विफल।');
        const data = await res.json();
        const voters = data.voters || [];

        if (voters.length === 0) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="9" style="padding: 30px; text-align: center; color: #94A3B8;">
                        '${q}' के लिए कोई मतदाता नहीं मिला। कृपया वर्तनी बदलकर या EPIC द्वारा खोजें।
                    </td>
                </tr>
            `;
            return;
        }

        let html = '';
        voters.forEach((v, vIdx) => {
            const escapedName = escapeHtml(v.name || '').replace(/'/g, "\\'");
            const escapedEpic = escapeHtml(v.epic_no || '').replace(/'/g, "\\'");
            html += `
                <tr style="border-bottom: 1px solid #F1F5F9;">
                    <td style="padding: 8px 10px; text-align: center; color: #64748B;">${vIdx + 1}</td>
                    <td style="padding: 8px 10px; text-align: center; font-weight: 600; color: #475569;">भाग ${v.part_no || '-'}</td>
                    <td style="padding: 8px 10px; text-align: center; font-weight: 700; color: #1E293B;">#${v.serial_no || '-'}</td>
                    <td style="padding: 8px 12px; font-weight: 650; color: #1E293B;">${escapeHtml(v.name || '')}</td>
                    <td style="padding: 8px 12px; color: #334155;">${escapeHtml(v.relation_name || '-')}</td>
                    <td style="padding: 8px 8px; text-align: center; color: #475569;">${v.age || '-'} (${escapeHtml(v.gender || '-')})</td>
                    <td style="padding: 8px 10px; text-align: center; font-weight: 600;">${escapeHtml(v.house_no || '-')}</td>
                    <td style="padding: 8px 12px; font-family: monospace; font-size: 0.8rem; font-weight: 600; color: #1E293B;">${escapeHtml(v.epic_no || 'N/A')}</td>
                    <td style="padding: 8px 12px; text-align: center;">
                        <button type="button" onclick="quickMapCandidateToMember(${v.id}, '${escapedName}', '${escapedEpic}', '${v.part_no}', '${v.serial_no}')" style="background: #2563EB; color: white; border: none; padding: 4px 10px; border-radius: 6px; font-size: 0.78rem; font-weight: 600; cursor: pointer; display: inline-flex; align-items: center; gap: 4px; white-space: nowrap;">
                            🔗 1-क्लिक लिंक करें
                        </button>
                    </td>
                </tr>
            `;
        });

        tbody.innerHTML = html;

    } catch (err) {
        if (tbody) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="9" style="padding: 24px; text-align: center; color: #DC2626;">
                        त्रुटि: ${err.message}
                    </td>
                </tr>
            `;
        }
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = '<i data-lucide="search" style="width: 16px; height: 16px;"></i> <span>खोजें</span>';
            if (window.lucide) {
                try { lucide.createIcons({ root: btn }); } catch (e) {}
            }
        }
    }
}

async function quickMapCandidateToMember(voterId, voterName, epicNo, partNo, serialNo) {
    const memberSelect = document.getElementById('hvmGlobalTargetMemberSelect');
    const memberIdVal = memberSelect?.value;
    if (!memberIdVal) {
        showToast('कृपया ऊपर परिवार का सदस्य चुनें जिसे लिंक करना है।', 'warning');
        return;
    }

    const house = houseVotersState.currentHouse;
    if (!house || !house.familyId) {
        showToast('परिवार पहचान अनुपलब्ध है।', 'error');
        return;
    }

    const members = houseVotersState.familyMembers || [];
    const targetMember = members.find(m => (m.memberId === memberIdVal || String(members.indexOf(m)) === String(memberIdVal)));
    const memberId = targetMember ? targetMember.memberId : memberIdVal;
    const memberName = targetMember ? targetMember.name : '';

    const fetchFn = typeof adminFetch === 'function' ? adminFetch : fetch;
    try {
        const res = await fetchFn('/api/survey-audit/map-member-voter', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                family_id: house.familyId,
                member_id: memberId,
                voter_id: voterId,
                survey_id: house.id || '',
                member_name: memberName
            })
        });

        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'लिंक सेव नहीं हो सका।');

        showToast(`सदस्य '${memberName}' को मतदाता '${voterName}' (भाग ${partNo}, क्रम #${serialNo}) से सफलतापूर्वक लिंक कर दिया गया!`, 'success');

        if (targetMember) {
            targetMember.isRegistered = true;
            targetMember.voterRecord = {
                id: voterId,
                epic_no: epicNo,
                part_no: partNo,
                serial_no: serialNo,
                voter_name: voterName,
                is_manual_mapped: true
            };
            targetMember.matchDescription = 'ग्लोबल खोज व 1-क्लिक लिंकिंग द्वारा सत्यापित';
        }

        const unreg = house.eligibleMembers.filter(m => !m.isRegistered).length;
        const reg = house.eligibleMembers.filter(m => m.isRegistered).length;
        house.houseRegisteredCount = reg;
        house.houseUnregisteredCount = unreg;
        house.hasUnregistered = unreg > 0;

        renderStreetAuditHouses();
        openHouseVotersModal(streetAuditState.currentAudit.houses.indexOf(house));
        switchHvmTab('members');

    } catch (err) {
        showToast('त्रुटि: ' + err.message, 'error');
    }
}

async function handleMapSubmit(memberIdx, memberId, memberName) {
    const select = document.getElementById(`hvmSelect_${memberIdx}`);
    if (!select || !select.value) {
        showToast('कृपया पहले वोटर लिस्ट से संबंधित मतदाता का चयन करें।', 'warning');
        return;
    }

    const voterId = parseInt(select.value, 10);
    const house = houseVotersState.currentHouse;
    if (!house || !house.familyId) {
        showToast('परिवार पहचान अनुपलब्ध है।', 'error');
        return;
    }

    const fetchFn = typeof adminFetch === 'function' ? adminFetch : fetch;
    try {
        const res = await fetchFn('/api/survey-audit/map-member-voter', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                family_id: house.familyId,
                member_id: memberId,
                voter_id: voterId,
                survey_id: house.id || '',
                member_name: memberName
            })
        });

        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'मैपिंग सेव नहीं हो सकी।');

        showToast(data.message || 'मैपिंग सफलतापूर्वक सुरक्षित हुई!', 'success');

        // Update local audit state
        const targetMember = house.eligibleMembers.find(m => m.memberId === memberId);
        if (targetMember) {
            targetMember.isRegistered = true;
            targetMember.voterRecord = {
                id: data.data.voter_id,
                epic_no: data.data.epic_no,
                part_no: data.data.part_no,
                serial_no: data.data.serial_no,
                voter_name: data.data.voter_name,
                is_manual_mapped: true
            };
            targetMember.matchDescription = 'मैन्युअल मैपिंग द्वारा सत्यापित (Manual Verified Mapping)';
        }

        // Recompute house counts
        const unreg = house.eligibleMembers.filter(m => !m.isRegistered).length;
        const reg = house.eligibleMembers.filter(m => m.isRegistered).length;
        house.houseRegisteredCount = reg;
        house.houseUnregisteredCount = unreg;
        house.hasUnregistered = unreg > 0;

        // Refresh modals and street audit table
        renderStreetAuditHouses();
        openHouseVotersModal(streetAuditState.currentAudit.houses.indexOf(house));

    } catch (err) {
        showToast('त्रुटि: ' + err.message, 'error');
    }
}

async function deleteMemberVoterMapping(familyId, memberId, memberName) {
    if (!confirm(`क्या आप सदस्य '${memberName}' की वोटर मैपिंग हटाना (Unlink) चाहते हैं?`)) {
        return;
    }

    const fetchFn = typeof adminFetch === 'function' ? adminFetch : fetch;
    try {
        const res = await fetchFn(`/api/survey-audit/map-member-voter?family_id=${encodeURIComponent(familyId)}&member_id=${encodeURIComponent(memberId)}`, {
            method: 'DELETE'
        });

        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'मैपिंग हटाने में त्रुटि।');

        showToast('मैपिंग सफलतापूर्वक हटा दी गई।', 'info');

        const house = houseVotersState.currentHouse;
        if (house) {
            const targetMember = (house.eligibleMembers || []).find(m => m.memberId === memberId);
            if (targetMember) {
                targetMember.isRegistered = false;
                targetMember.voterRecord = null;
                targetMember.matchDescription = '';
            }
            const unreg = house.eligibleMembers.filter(m => !m.isRegistered).length;
            const reg = house.eligibleMembers.filter(m => m.isRegistered).length;
            house.houseRegisteredCount = reg;
            house.houseUnregisteredCount = unreg;
            house.hasUnregistered = unreg > 0;

            renderStreetAuditHouses();
            openHouseVotersModal(streetAuditState.currentAudit.houses.indexOf(house));
        }

    } catch (err) {
        showToast('त्रुटि: ' + err.message, 'error');
    }
}

function closeHouseVotersModal() {
    const modal = document.getElementById('houseVotersModal');
    if (modal) modal.style.display = 'none';
}
// Attach modal close event listeners
document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('closeHouseVotersModalBtn')?.addEventListener('click', closeHouseVotersModal);
    document.getElementById('closeHvmBottomBtn')?.addEventListener('click', closeHouseVotersModal);
    document.getElementById('houseVotersModal')?.addEventListener('click', (e) => {
        if (e.target.id === 'houseVotersModal') closeHouseVotersModal();
    });
});

// ==========================================================================
// PROFESSIONAL A4 PRINT SYSTEM IMPLEMENTATION
// High-fidelity Electoral Roll & Field Survey Print Engines
// ==========================================================================

function executePrint(htmlContent, documentTitle = 'निर्वाचक नामावली रिपोर्ट') {
    const printSection = document.getElementById('printSection');
    if (!printSection) {
        console.error('Print container #printSection not found');
        return;
    }
    printSection.innerHTML = htmlContent;
    const oldTitle = document.title;
    document.title = documentTitle;
    
    // Refresh any lucide icons rendered in print section
    if (window.lucide) {
        try { lucide.createIcons({ root: printSection }); } catch (e) {}
    }

    setTimeout(() => {
        window.print();
        setTimeout(() => {
            document.title = oldTitle;
        }, 1200);
    }, 200);
}

function renderAndExecuteHousePrint(house, voters, members, partNo) {
    const audit = streetAuditState.currentAudit || {};
    const street = audit.street || 'गली';
    const zone = audit.zone || '-';
    const dateStr = new Date().toLocaleDateString('hi-IN', { day: '2-digit', month: '2-digit', year: 'numeric' });
    const timeStr = new Date().toLocaleTimeString('hi-IN', { hour: '2-digit', minute: '2-digit' });

    let voterRowsHtml = '';
    if (!voters || voters.length === 0) {
        voterRowsHtml = `<tr><td colspan="8" style="text-align: center; color: #64748b; padding: 12px;">वोटर लिस्ट के इस भाग एवं मकान में कोई मतदाता दर्ज नहीं मिला।</td></tr>`;
    } else {
        voters.forEach((v, idx) => {
            const epic = escapeHtml(v.epic_no || '--');
            const relType = escapeHtml(v.relation_type || 'पिता');
            const relName = escapeHtml(v.relation_name || '--');
            const caste = (!isOperatorUser() && !v.is_muslim && v.caste_key && CASTE_LABELS[v.caste_key]) ? CASTE_LABELS[v.caste_key] : (v.is_muslim ? 'मुस्लिम' : '--');
            voterRowsHtml += `
                <tr>
                    <td style="text-align: center; font-weight: 700;">${idx + 1}</td>
                    <td style="font-weight: 700;">${escapeHtml(v.name || '')}</td>
                    <td>${relName} <small style="color: #64748b;">(${relType})</small></td>
                    <td style="text-align: center;">${v.age || '--'} / ${escapeHtml(v.gender || '--')}</td>
                    <td style="font-family: monospace; font-weight: 700;">${epic}</td>
                    <td style="text-align: center;">${escapeHtml(v.part_no || partNo || '--')}</td>
                    <td style="text-align: center; font-weight: 700;">${v.serial_no || '--'}</td>
                    <td style="font-size: 7.5pt;">${escapeHtml(caste)}</td>
                </tr>
            `;
        });
    }

    let memberRowsHtml = '';
    if (!members || members.length === 0) {
        memberRowsHtml = `<tr><td colspan="8" style="text-align: center; color: #64748b; padding: 12px;">सर्वे ऐप (NPPropertyServey) में कोई परिवार सदस्य दर्ज नहीं मिला।</td></tr>`;
    } else {
        members.forEach((m, idx) => {
            const isReg = Boolean(m.isRegistered);
            const statusHtml = isReg
                ? `<span class="print-badge print-badge-success">✅ पंजीकृत मतदाता</span>`
                : (m.age >= 18 
                    ? `<span class="print-badge print-badge-danger">❌ गैर-पंजीकृत (फॉर्म 6 अपेक्षित)</span>`
                    : `<span class="print-badge print-badge-warning">⏳ 17+ अग्रिम पंजीकरण</span>`);
            const epicMapped = isReg && m.voterRecord ? escapeHtml(m.voterRecord.epic_no || '--') : '--';
            memberRowsHtml += `
                <tr>
                    <td style="text-align: center; font-weight: 700;">${idx + 1}</td>
                    <td style="font-weight: 700;">${escapeHtml(m.name || '')}</td>
                    <td>${escapeHtml(m.relationName || '--')}</td>
                    <td style="text-align: center;">${m.age || '--'} / ${escapeHtml(m.gender || '--')}</td>
                    <td>${escapeHtml(m.relation || '--')}</td>
                    <td style="font-family: monospace;">${escapeHtml(m.mobile || '--')}</td>
                    <td>${statusHtml}</td>
                    <td style="font-family: monospace; font-weight: 700;">${epicMapped}</td>
                </tr>
            `;
        });
    }

    const html = `
        <div class="print-document">
            <div class="print-tiranga-stripe">
                <div class="print-stripe-saffron"></div>
                <div class="print-stripe-white"></div>
                <div class="print-stripe-green"></div>
            </div>

            <div class="print-header">
                <div class="print-header-top">
                    <div class="print-emblem-badge">
                        <span>🏛️ भारत निर्वाचन आयोग / राज्य निर्वाचन आयोग</span>
                    </div>
                    <div class="print-doc-meta">
                        <div><strong>प्रारूप:</strong> पारिवारिक मतदाता रजिस्टर</div>
                        <div><strong>दिनांक:</strong> ${dateStr} ${timeStr}</div>
                    </div>
                </div>
                <div class="print-title-main">पारिवारिक मतदाता पर्ची एवं डोर-टू-डोर सत्यापन रजिस्टर</div>
                <div class="print-subtitle">मकान संख्या: ${escapeHtml(house.houseNumber || '-')} | गली: ${escapeHtml(street)} | ज़ोन/वार्ड: ${escapeHtml(zone)}</div>
            </div>

            <div class="print-meta-grid">
                <div class="print-meta-item">
                    <span class="print-meta-label">मकान संख्या (House No)</span>
                    <span class="print-meta-value">🏠 ${escapeHtml(house.houseNumber || '-')}</span>
                </div>
                <div class="print-meta-item">
                    <span class="print-meta-label">परिवार मुखिया / स्वामी</span>
                    <span class="print-meta-value">👤 ${escapeHtml(house.ownerName || '-')}</span>
                </div>
                <div class="print-meta-item">
                    <span class="print-meta-label">सम्पत्ति संख्या / कोड</span>
                    <span class="print-meta-value">🏷️ ${escapeHtml(house.propertyId || '-')}</span>
                </div>
                <div class="print-meta-item">
                    <span class="print-meta-label">मतदान केंद्र भाग संख्या</span>
                    <span class="print-meta-value">📍 भाग संख्या ${escapeHtml(partNo || '-')}</span>
                </div>
                <div class="print-meta-item">
                    <span class="print-meta-label">वोटर लिस्ट में दर्ज मतदाता</span>
                    <span class="print-meta-value" style="color: #059669;">🗳️ ${voters.length} कुल मतदाता</span>
                </div>
                <div class="print-meta-item">
                    <span class="print-meta-label">सर्वे में दर्ज सदस्य (17+)</span>
                    <span class="print-meta-value">👨‍👩‍👧‍👦 ${members.length} कुल सदस्य</span>
                </div>
            </div>

            <div class="print-section-title">
                <span>1. वोटर लिस्ट में पंजीकृत पारिवारिक मतदाता (Registered Electoral Roll Voters)</span>
                <span style="font-size: 8pt; font-weight: normal;">कुल: ${voters.length} मतदाता</span>
            </div>
            <table class="print-table">
                <thead>
                    <tr>
                        <th style="width: 35px; text-align: center;">क्र०</th>
                        <th>मतदाता का नाम</th>
                        <th>संबंधी (पिता/पति) का नाम</th>
                        <th style="width: 75px; text-align: center;">आयु/लिंग</th>
                        <th style="width: 110px;">पहचान पत्र (EPIC)</th>
                        <th style="width: 55px; text-align: center;">भाग सं०</th>
                        <th style="width: 55px; text-align: center;">क्रम सं०</th>
                        <th style="width: 80px;">जाति/टिप्पणी</th>
                    </tr>
                </thead>
                <tbody>
                    ${voterRowsHtml}
                </tbody>
            </table>

            <div class="print-section-title" style="margin-top: 14px;">
                <span>2. NPPropertyServey परिवार सदस्य एवं मतदाता पंजीकरण स्थिति (Household Survey Verification)</span>
                <span style="font-size: 8pt; font-weight: normal;">कुल सदस्य: ${members.length}</span>
            </div>
            <table class="print-table">
                <thead>
                    <tr>
                        <th style="width: 35px; text-align: center;">क्र०</th>
                        <th>सदस्य का नाम</th>
                        <th>संबंधी (पिता/पति) का नाम</th>
                        <th style="width: 75px; text-align: center;">आयु/लिंग</th>
                        <th style="width: 70px;">रिश्ता</th>
                        <th style="width: 85px;">मोबाइल</th>
                        <th>मतदाता पंजीकरण स्थिति</th>
                        <th style="width: 100px;">मैप्ड EPIC</th>
                    </tr>
                </thead>
                <tbody>
                    ${memberRowsHtml}
                </tbody>
            </table>

            <div class="print-footer-signatures">
                <div class="print-signature-box">
                    <div class="print-signature-line"></div>
                    <div class="print-signature-label">हस्ताक्षर परिवार मुखिया / प्रतिनिधि</div>
                    <div class="print-signature-sub">नाम: ${escapeHtml(house.ownerName || '')}</div>
                </div>
                <div class="print-signature-box">
                    <div class="print-signature-line"></div>
                    <div class="print-signature-label">हस्ताक्षर बीएलओ (BLO)</div>
                    <div class="print-signature-sub">बूथ लेवल अधिकारी, भाग सं० ${escapeHtml(partNo || '')}</div>
                </div>
                <div class="print-signature-box">
                    <div class="print-signature-line"></div>
                    <div class="print-signature-label">हस्ताक्षर सर्वेक्षक / सुपरवाइजर</div>
                    <div class="print-signature-sub">डोर-टू-डोर सत्यापन टीम</div>
                </div>
            </div>
        </div>
    `;

    executePrint(html, `परिवार_रजिस्टर_मकान_${house.houseNumber || '0'}`);
}

function printHouseVotersReport() {
    if (!houseVotersState.currentHouse) {
        showToast('कोई मकान चयनित नहीं है।', 'warning');
        return;
    }
    const house = houseVotersState.currentHouse;
    const voters = houseVotersState.allHouseVoters || houseVotersState.houseVoters || [];
    const members = houseVotersState.familyMembers || house.eligibleMembers || [];
    const partSelectVal = document.getElementById('hvmPartNoSelect')?.value;
    const partNo = (voters[0] && voters[0].part_no) || 
                   (houseVotersState.anchorVRec && houseVotersState.anchorVRec.part_no) || 
                   (partSelectVal && partSelectVal !== 'ALL' ? partSelectVal : '') || 
                   'समस्त';
    
    renderAndExecuteHousePrint(house, voters, members, partNo);
}

async function printHouseVotersDirect(hIdx) {
    if (!streetAuditState.currentAudit || !streetAuditState.currentAudit.houses) {
        showToast('ऑडिट डेटा लोड नहीं है।', 'warning');
        return;
    }
    const house = streetAuditState.currentAudit.houses[hIdx];
    if (!house) return;

    const regMember = (house.eligibleMembers || []).find(m => m.isRegistered && m.voterRecord);
    const partNo = regMember ? regMember.voterRecord.part_no : '';
    const houseNo = regMember ? (regMember.voterRecord.voter_house || house.houseNumber) : (house.houseNumber || '1');

    showToast(`मकान नं० ${house.houseNumber || ''} का प्रिंट तैयार हो रहा है...`, 'info');
    try {
        const queryParams = new URLSearchParams({ house_no: houseNo });
        if (partNo) queryParams.append('part_no', partNo);
        const res = await fetch(`/api/voters/by-house?${queryParams.toString()}`);
        const data = await res.json();
        const voters = data.voters || [];
        const finalPart = partNo || (voters[0] && voters[0].part_no) || 'समस्त';
        renderAndExecuteHousePrint(house, voters, house.eligibleMembers || [], finalPart);
    } catch (e) {
        showToast('प्रिंट डेटा लोड करने में त्रुटि: ' + e.message, 'error');
    }
}

function printStreetAuditRegister() {
    if (!streetAuditState.currentAudit || !streetAuditState.currentAudit.houses) {
        showToast('कृपया पहले किसी गली का ऑडिट लोड करें।', 'warning');
        return;
    }
    const audit = streetAuditState.currentAudit;
    const houses = audit.houses || [];
    const stats = audit.stats || {};
    const dateStr = new Date().toLocaleDateString('hi-IN', { day: '2-digit', month: '2-digit', year: 'numeric' });

    let rowsHtml = '';
    houses.forEach((h, idx) => {
        const members = h.eligibleMembers || [];
        const regMembers = members.filter(m => m.isRegistered);
        const unreg18 = members.filter(m => !m.isRegistered && m.age >= 18);
        const unreg17 = members.filter(m => !m.isRegistered && m.age < 18);

        const regNames = regMembers.map(m => {
            const epic = m.voterRecord?.epic_no ? ` (${m.voterRecord.epic_no})` : '';
            return `${escapeHtml(m.name)}${epic}`;
        }).join(', ') || '<span style="color: #94a3b8;">--</span>';

        const unreg18Names = unreg18.map(m => `${escapeHtml(m.name)} (${m.age} वर्ष)`).join(', ') || '<span style="color: #94a3b8;">शून्य</span>';
        const unreg17Names = unreg17.map(m => `${escapeHtml(m.name)} (${m.age} वर्ष)`).join(', ') || '<span style="color: #94a3b8;">शून्य</span>';

        let statusBadge = '';
        if (members.length > 0 && regMembers.length === members.length) {
            statusBadge = '<span class="print-badge print-badge-success">पूर्ण पंजीकृत</span>';
        } else if (unreg18.length > 0) {
            statusBadge = `<span class="print-badge print-badge-danger">${unreg18.length} फॉर्म 6 शेष</span>`;
        } else {
            statusBadge = '<span class="print-badge print-badge-warning">अग्रिम शेष</span>';
        }

        rowsHtml += `
            <tr>
                <td style="text-align: center; font-weight: 700;">${idx + 1}</td>
                <td style="font-weight: 700; text-align: center;">${escapeHtml(h.houseNumber || '-')}</td>
                <td><strong>${escapeHtml(h.ownerName || '-')}</strong> <small style="color: #64748b;">${escapeHtml(h.propertyId || '')}</small></td>
                <td style="text-align: center; font-weight: 700;">${members.length}</td>
                <td style="font-size: 7.5pt;">${regNames}</td>
                <td style="font-size: 7.5pt; color: #991b1b;">${unreg18Names}</td>
                <td style="font-size: 7.5pt; color: #854d0e;">${unreg17Names}</td>
                <td style="text-align: center;">${statusBadge}</td>
            </tr>
        `;
    });

    const html = `
        <div class="print-document">
            <div class="print-tiranga-stripe">
                <div class="print-stripe-saffron"></div>
                <div class="print-stripe-white"></div>
                <div class="print-stripe-green"></div>
            </div>

            <div class="print-header">
                <div class="print-header-top">
                    <div class="print-emblem-badge">
                        <span>🏛️ स्थानीय निकाय व विधान सभा निर्वाचन | डोर-टू-डोर सत्यापन</span>
                    </div>
                    <div class="print-doc-meta">
                        <div><strong>प्रारूप:</strong> सम्पूर्ण गली सर्वे व मतदाता रजिस्टर</div>
                        <div><strong>प्रिंट दिनांक:</strong> ${dateStr}</div>
                    </div>
                <div class="print-title-main">सम्पूर्ण गली सर्वे व निर्वाचक नामावली रजिस्टर</div>
                <div class="print-subtitle">गली / क्षेत्र: ${escapeHtml((audit.street && audit.street !== 'ALL') ? audit.street : 'समस्त गलियाँ')} | ज़ोन/वार्ड: ${escapeHtml(audit.zone || 'समस्त')} | न्यूनतम आयु सीमा: ${audit.minAge || 17}+ वर्ष</div>
            </div>

            <div class="print-meta-grid" style="grid-template-columns: repeat(6, 1fr);">
                <div class="print-meta-item">
                    <span class="print-meta-label">कुल सर्वे मकान</span>
                    <span class="print-meta-value">${stats.totalHouses || houses.length}</span>
                </div>
                <div class="print-meta-item">
                    <span class="print-meta-label">कुल पात्र सदस्य (17+)</span>
                    <span class="print-meta-value">${stats.totalEligibleMembers || 0}</span>
                </div>
                <div class="print-meta-item">
                    <span class="print-meta-label">पंजीकृत मतदाता</span>
                    <span class="print-meta-value" style="color: #166534;">${stats.totalRegistered || 0}</span>
                </div>
                <div class="print-meta-item">
                    <span class="print-meta-label">फॉर्म 6 पात्र (18+)</span>
                    <span class="print-meta-value" style="color: #991b1b;">${stats.unregistered18Plus || 0}</span>
                </div>
                <div class="print-meta-item">
                    <span class="print-meta-label">अग्रिम पात्र (17+)</span>
                    <span class="print-meta-value" style="color: #854d0e;">${stats.unregistered17Plus || 0}</span>
                </div>
                <div class="print-meta-item">
                    <span class="print-meta-label">कवरेज प्रतिशत</span>
                    <span class="print-meta-value">${stats.totalEligibleMembers ? Math.round((stats.totalRegistered / stats.totalEligibleMembers) * 100) : 0}%</span>
                </div>
            </div>

            <table class="print-table">
                <thead>
                    <tr>
                        <th style="width: 35px; text-align: center;">क्र०</th>
                        <th style="width: 60px; text-align: center;">मकान नं०</th>
                        <th style="width: 140px;">मुखिया / स्वामी का नाम</th>
                        <th style="width: 45px; text-align: center;">कुल (17+)</th>
                        <th>पंजीकृत मतदाता (नाम व EPIC)</th>
                        <th>फॉर्म 6 शेष पात्र (18+ वर्ष)</th>
                        <th>अग्रिम पात्र (17+ वर्ष)</th>
                        <th style="width: 80px; text-align: center;">स्थिति</th>
                    </tr>
                </thead>
                <tbody>
                    ${rowsHtml}
                </tbody>
            </table>

            <div class="print-footer-signatures">
                <div class="print-signature-box">
                    <div class="print-signature-line"></div>
                    <div class="print-signature-label">हस्ताक्षर बीएलओ (BLO)</div>
                    <div class="print-signature-sub">नाम व फोन नं०: ____________________</div>
                </div>
                <div class="print-signature-box">
                    <div class="print-signature-line"></div>
                    <div class="print-signature-label">हस्ताक्षर फील्ड सुपरवाइजर</div>
                    <div class="print-signature-sub">सत्यापन अधिकारी</div>
                </div>
                <div class="print-signature-box">
                    <div class="print-signature-line"></div>
                    <div class="print-signature-label">सहायक निर्वाचक रजिस्ट्रीकरण अधिकारी (AERO)</div>
                    <div class="print-signature-sub">तहसील / नगर पालिका परिषद</div>
                </div>
            </div>
        </div>
    `;

    executePrint(html, `गली_रजिस्टर_${audit.street || 'सर्वे'}`);
}

function printForm6ActionList() {
    if (!streetAuditState.currentAudit || !streetAuditState.currentAudit.houses) {
        showToast('कृपया पहले किसी गली का ऑडिट लोड करें।', 'warning');
        return;
    }
    const audit = streetAuditState.currentAudit;
    const houses = audit.houses || [];
    const dateStr = new Date().toLocaleDateString('hi-IN', { day: '2-digit', month: '2-digit', year: 'numeric' });

    const unregList = [];
    houses.forEach(h => {
        (h.eligibleMembers || []).forEach(m => {
            if (!m.isRegistered) {
                unregList.push({
                    houseNo: h.houseNumber || '-',
                    ownerName: h.ownerName || '-',
                    member: m
                });
            }
        });
    });

    if (unregList.length === 0) {
        showToast('बधाई! इस गली में कोई गैर-पंजीकृत सदस्य नहीं है। सभी सदस्य वोटर लिस्ट में पंजीकृत हैं।', 'info');
        return;
    }

    let rowsHtml = '';
    unregList.forEach((item, idx) => {
        const m = item.member;
        const isAdult = m.age >= 18;
        const categoryBadge = isAdult 
            ? '<span class="print-badge print-badge-danger">18+ पूर्ण पात्र</span>'
            : '<span class="print-badge print-badge-warning">17+ अग्रिम</span>';

        rowsHtml += `
            <tr>
                <td style="text-align: center; font-weight: 700;">${idx + 1}</td>
                <td style="text-align: center; font-weight: 700;">${escapeHtml(item.houseNo)}</td>
                <td><strong>${escapeHtml(m.name || '')}</strong></td>
                <td>${escapeHtml(m.relationName || '--')}</td>
                <td style="text-align: center;">${m.age || '--'} / ${escapeHtml(m.gender || '--')}</td>
                <td style="font-family: monospace;">${escapeHtml(m.mobile || '--')}</td>
                <td>${escapeHtml(item.ownerName)}</td>
                <td style="text-align: center;">${categoryBadge}</td>
                <td style="width: 90px; text-align: center; font-size: 7.5pt;">[ &nbsp; ] दिया<br>[ &nbsp; ] ऑनलाइन</td>
                <td style="width: 100px;">&nbsp;</td>
                <td style="width: 90px;">&nbsp;</td>
            </tr>
        `;
    });

    const html = `
        <div class="print-document">
            <div class="print-tiranga-stripe">
                <div class="print-stripe-saffron"></div>
                <div class="print-stripe-white"></div>
                <div class="print-stripe-green"></div>
            </div>

            <div class="print-header">
                <div class="print-header-top">
                    <div class="print-emblem-badge">
                        <span>📋 भारत निर्वाचन आयोग — मतदाता सूची विशेष पुनरीक्षण कार्यक्रम</span>
                    </div>
                    <div class="print-doc-meta">
                        <div><strong>प्रारूप:</strong> फॉर्म 6 डोर-टू-डोर फील्ड कार्य सूची</div>
                        <div><strong>प्रिंट दिनांक:</strong> ${dateStr}</div>
                    </div>
                </div>
                <div class="print-title-main">फॉर्म 6 (नया मतदाता पंजीकरण) डोर-टू-डोर बीएलओ फील्ड एक्शन सूची</div>
                <div class="print-subtitle">गली: ${escapeHtml(audit.street || '')} | ज़ोन/वार्ड: ${escapeHtml(audit.zone || '-')} | समस्त गैर-पंजीकृत (17+ व 18+) नागरिकों की सूची</div>
            </div>

            <div class="print-meta-grid" style="grid-template-columns: repeat(4, 1fr);">
                <div class="print-meta-item">
                    <span class="print-meta-label">गली का नाम</span>
                    <span class="print-meta-value">📍 ${escapeHtml(audit.street || '')}</span>
                </div>
                <div class="print-meta-item">
                    <span class="print-meta-label">कुल चिन्हित नए मतदाता</span>
                    <span class="print-meta-value" style="color: #991b1b;">📝 ${unregList.length} व्यक्ति</span>
                </div>
                <div class="print-meta-item">
                    <span class="print-meta-label">18+ पूर्ण पात्र / 17+ अग्रिम</span>
                    <span class="print-meta-value">18+: ${unregList.filter(u => u.member.age >= 18).length} | 17+: ${unregList.filter(u => u.member.age < 18).length}</span>
                </div>
                <div class="print-meta-item">
                    <span class="print-meta-label">बीएलओ का नाम व मोबाइल</span>
                    <span class="print-meta-value">____________________</span>
                </div>
            </div>

            <table class="print-table">
                <thead>
                    <tr>
                        <th style="width: 30px; text-align: center;">क्र०</th>
                        <th style="width: 50px; text-align: center;">मकान सं०</th>
                        <th>आवेदक / सदस्य का नाम</th>
                        <th>संबंधी (पिता/पति) का नाम</th>
                        <th style="width: 65px; text-align: center;">आयु/लिंग</th>
                        <th style="width: 80px;">मोबाइल</th>
                        <th>परिवार मुखिया</th>
                        <th style="width: 75px; text-align: center;">श्रेणी</th>
                        <th style="width: 80px; text-align: center;">फॉर्म 6 स्थिति</th>
                        <th style="width: 95px; text-align: center;">आवेदक के हस्ताक्षर</th>
                        <th style="width: 85px; text-align: center;">बीएलओ टीप</th>
                    </tr>
                </thead>
                <tbody>
                    ${rowsHtml}
                </tbody>
            </table>

            <div style="font-size: 7.5pt; color: #475569; border: 1px solid #cbd5e1; background: #f8fafc; padding: 6px 10px; border-radius: 4px; margin-top: 8px;">
                <strong>बीएलओ हेतु फील्ड दिशा-निर्देश:</strong> 
                (1) 18 वर्ष पूर्ण कर चुके सभी पात्र नागरिकों से फॉर्म 6 प्राप्त करें अथवा Voter Helpline App / voters.eci.gov.in से ऑनलाइन आवेदन दर्ज करवाएं। 
                (2) 17 वर्ष आयु के युवाओं का अग्रिम पंजीकरण फॉर्म 6 में भरवाएं। 
                (3) आवेदक का हस्ताक्षर लेकर बीएलओ अपनी टीप व पावती संख्या अवश्य अंकित करें।
            </div>

            <div class="print-footer-signatures">
                <div class="print-signature-box">
                    <div class="print-signature-line"></div>
                    <div class="print-signature-label">हस्ताक्षर बीएलओ (BLO)</div>
                    <div class="print-signature-sub">सत्यापनकर्ता बूथ लेवल अधिकारी</div>
                </div>
                <div class="print-signature-box">
                    <div class="print-signature-line"></div>
                    <div class="print-signature-label">हस्ताक्षर फील्ड सुपरवाइजर</div>
                    <div class="print-signature-sub">जांच अधिकारी</div>
                </div>
                <div class="print-signature-box">
                    <div class="print-signature-line"></div>
                    <div class="print-signature-label">सहायक निर्वाचक रजिस्ट्रीकरण अधिकारी (AERO)</div>
                    <div class="print-signature-sub">कार्यालय मुहर एवं हस्ताक्षर</div>
                </div>
            </div>
        </div>
    `;

    executePrint(html, `फॉर्म6_फील्ड_सूची_${audit.street || 'गली'}`);
}


// ==========================================================================
// MASTER DATABASE PRACTICAL PRINT SYSTEM (Electoral Roll, Agent Table & Slips)
// ==========================================================================

const dbPrintState = {
    selectedFormat: 'grid', // 'grid' | 'table' | 'slips'
    selectedScope: 'current', // 'current' | 'all' | 'range'
};

function openDbPrintModal() {
    const modal = document.getElementById('dbPrintOptionsModal');
    if (!modal) return;

    // Populate current counts
    const curCount = (dbState.records || []).length;
    const totalCount = dbState.totalFiltered || curCount;
    const totalPages = dbState.totalPages || 1;

    const curCountSpan = document.getElementById('printCurrentPageCount');
    const totalCountSpan = document.getElementById('printTotalFilterCount');
    const pageFromInput = document.getElementById('printPageFrom');
    const pageToInput = document.getElementById('printPageTo');

    if (curCountSpan) curCountSpan.innerText = curCount;
    if (totalCountSpan) totalCountSpan.innerText = totalCount.toLocaleString('hi-IN');
    if (pageFromInput) {
        pageFromInput.value = "1";
        pageFromInput.max = totalPages;
    }
    if (pageToInput) {
        pageToInput.value = String(Math.min(totalPages, 5));
        pageToInput.max = totalPages;
    }

    // Default to 'grid' if in paper view, or keep current
    if (dbState.viewMode === 'paper') {
        selectDbPrintFormat('grid');
    }

    updateDbPrintEstimates();
    modal.style.display = 'flex';
    if (window.lucide) lucide.createIcons();
}

function closeDbPrintModal() {
    const modal = document.getElementById('dbPrintOptionsModal');
    if (modal) modal.style.display = 'none';
}

function selectDbPrintFormat(formatKey) {
    dbPrintState.selectedFormat = formatKey;

    ['grid', 'table', 'slips'].forEach(key => {
        const card = document.getElementById(`pfc${key.charAt(0).toUpperCase() + key.slice(1)}`);
        const radio = document.getElementById(key === 'grid' ? 'formatEciGrid' : (key === 'table' ? 'formatAgentTable' : 'formatSlips'));
        if (card) {
            if (key === formatKey) {
                card.classList.add('active');
            } else {
                card.classList.remove('active');
            }
        }
        if (radio) {
            radio.checked = (key === formatKey);
        }
    });

    updateDbPrintEstimates();
}

function updateDbPrintEstimates() {
    const scopeRadio = document.querySelector('input[name="dbPrintScope"]:checked');
    const scope = scopeRadio ? scopeRadio.value : 'current';
    dbPrintState.selectedScope = scope;

    const rangeInputs = document.getElementById('printRangeInputs');
    if (rangeInputs) {
        rangeInputs.style.display = (scope === 'range') ? 'inline-flex' : 'none';
    }

    const curCount = (dbState.records || []).length;
    const totalCount = dbState.totalFiltered || curCount;
    const pageSize = dbState.pageSize || 30;

    let voterCount = curCount;
    if (scope === 'all') {
        voterCount = totalCount;
    } else if (scope === 'range') {
        const from = parseInt(document.getElementById('printPageFrom')?.value || '1', 10);
        const to = parseInt(document.getElementById('printPageTo')?.value || '1', 10);
        const numPages = Math.max(1, to - from + 1);
        voterCount = Math.min(totalCount, numPages * pageSize);
    }

    const format = dbPrintState.selectedFormat;
    let itemsPerPage = 30;
    let formatLabel = 'ECI 30-कार्ड ग्रिड';
    if (format === 'table') {
        itemsPerPage = 40;
        formatLabel = 'सघन एजेंट रजिस्टर';
    } else if (format === 'slips') {
        itemsPerPage = 8;
        formatLabel = 'मतदाता पर्ची शीट';
    }

    const estPages = Math.max(1, Math.ceil(voterCount / itemsPerPage));

    const estVoterElem = document.getElementById('estVoterCount');
    const estFormatElem = document.getElementById('estFormatName');
    const estPageElem = document.getElementById('estPageCount');

    if (estVoterElem) estVoterElem.innerText = voterCount.toLocaleString('hi-IN');
    if (estFormatElem) estFormatElem.innerText = `${formatLabel} (${itemsPerPage}/पेज)`;
    if (estPageElem) estPageElem.innerText = `${estPages} A4 पृष्ठ`;
}

async function executeDbPrintWithOptions() {
    const btn = document.getElementById('btnStartDbPrint');
    if (btn) btn.disabled = true;

    closeDbPrintModal();
    showToast('A4 प्रिंट डेटा तैयार किया जा रहा है...', 'info');

    try {
        const format = dbPrintState.selectedFormat;
        const scope = dbPrintState.selectedScope;
        const showCaste = document.getElementById('printOptShowCaste')?.checked ?? true;
        const excludeDeleted = document.getElementById('printOptExcludeDeleted')?.checked ?? true;

        let records = [];

        if (scope === 'current') {
            records = [...(dbState.records || [])];
        } else {
            // Need to fetch from server
            const params = new URLSearchParams();
            if (dbState.selectedDbId) params.append('db_id', dbState.selectedDbId);

            const q = elements.dbQueryInput?.value.trim();
            if (q) params.append('q', q);
            const name = elements.dbNameInput?.value.trim();
            if (name) params.append('name', name);
            const relName = elements.dbRelNameInput?.value.trim();
            if (relName) params.append('relation_name', relName);
            const epic = elements.dbEpicInput?.value.trim();
            if (epic) params.append('epic_no', epic);
            const part = elements.dbPartInput?.value.trim();
            if (part) params.append('part_no', part);
            const gender = elements.dbGenderSelect?.value;
            if (gender && gender !== 'all') params.append('gender', gender);
            const house = elements.dbHouseInput?.value.trim();
            if (house) params.append('house_no', house);
            const minAge = elements.dbMinAgeInput?.value.trim();
            if (minAge) params.append('min_age', minAge);
            const maxAge = elements.dbMaxAgeInput?.value.trim();
            if (maxAge) params.append('max_age', maxAge);

            if (elements.dbMuslimSelect && elements.dbMuslimSelect.value !== 'all') {
                params.append('muslim', elements.dbMuslimSelect.value);
            }
            if (elements.dbCasteSelect && elements.dbCasteSelect.value !== 'all') {
                params.append('caste_key', elements.dbCasteSelect.value);
            }
            if (elements.dbStatusSelect && elements.dbStatusSelect.value !== 'all') {
                params.append('status', elements.dbStatusSelect.value);
            }

            if (scope === 'all') {
                params.set('page', '1');
                params.set('limit', '5000');
                const res = await fetch(`/api/database/search?${params.toString()}`);
                if (!res.ok) throw new Error('सर्वर से मतदाता डेटा प्राप्त नहीं हुआ');
                const data = await res.json();
                records = data.records || [];
            } else if (scope === 'range') {
                const from = parseInt(document.getElementById('printPageFrom')?.value || '1', 10);
                const to = parseInt(document.getElementById('printPageTo')?.value || '1', 10);
                const pSize = dbState.pageSize || 30;
                
                params.set('page', '1');
                params.set('limit', String(Math.min(5000, to * pSize)));
                const res = await fetch(`/api/database/search?${params.toString()}`);
                if (!res.ok) throw new Error('सर्वर से मतदाता डेटा प्राप्त नहीं हुआ');
                const data = await res.json();
                const allFetched = data.records || [];
                const startIdx = (from - 1) * pSize;
                const endIdx = to * pSize;
                records = allFetched.slice(startIdx, endIdx);
            }
        }

        if (excludeDeleted) {
            records = records.filter(r => !r.is_deleted);
        }

        if (records.length === 0) {
            showToast('प्रिंट करने हेतु कोई मतदाता रिकॉर्ड नहीं मिला।', 'warning');
            return;
        }

        // Meta info for headers
        const firstRec = records[0] || {};
        const meta = {
            assembly: firstRec.assembly_name || firstRec.assembly_no || (elements.bulkCurrentAssembly?.innerText !== '--' ? elements.bulkCurrentAssembly.innerText : 'विधान सभा निर्वाचन क्षेत्र'),
            part: firstRec.part_no ? `भाग सं० ${firstRec.part_no}${firstRec.part_name ? ' (' + firstRec.part_name + ')' : ''}` : (elements.dbPartInput?.value ? `भाग सं० ${elements.dbPartInput.value}` : 'समस्त भाग'),
            partNo: firstRec.part_no || elements.dbPartInput?.value || '--',
            pollingStation: firstRec.polling_station || firstRec.section_name || '--',
            totalRecords: records.length,
            dateStr: new Date().toLocaleDateString('hi-IN', { day: '2-digit', month: '2-digit', year: 'numeric' })
        };

        let html = '';
        let docTitle = `मतदाता_सूची_${meta.partNo || 'डेटाबेस'}`;

        if (format === 'grid') {
            html = renderEciPaperGridPrint(records, meta, showCaste);
            docTitle = `ECI_वोटर_लिस्ट_भाग_${meta.partNo || '0'}`;
        } else if (format === 'table') {
            html = renderCompactAgentTablePrint(records, meta, showCaste);
            docTitle = `एजेंट_रजिस्टर_भाग_${meta.partNo || '0'}`;
        } else if (format === 'slips') {
            html = renderVoterSlipsSheetPrint(records, meta);
            docTitle = `मतदाता_पर्चियां_भाग_${meta.partNo || '0'}`;
        }

        executePrint(html, docTitle);

    } catch (err) {
        showToast('प्रिंट त्रुटि: ' + err.message, 'error');
    } finally {
        if (btn) btn.disabled = false;
    }
}

// --------------------------------------------------------------------------
// FORMAT 1: ECI 30-CARD ELECTORAL ROLL (3x10 cards per A4 page)
// --------------------------------------------------------------------------
function renderEciPaperGridPrint(records, meta, showCaste) {
    const CARDS_PER_PAGE = 30;
    const totalPages = Math.ceil(records.length / CARDS_PER_PAGE);
    let fullHtml = '<div class="print-document" style="padding: 0; background: #fff;">';

    for (let pageIdx = 0; pageIdx < totalPages; pageIdx++) {
        const pageRecords = records.slice(pageIdx * CARDS_PER_PAGE, (pageIdx + 1) * CARDS_PER_PAGE);
        let cardsHtml = '';

        pageRecords.forEach(v => {
            const serialNo = v.serial_no != null ? v.serial_no : '--';
            const epic = escapeHtml(v.epic_no || '--');
            const name = escapeHtml(v.name || '--');
            const relType = escapeHtml(v.relation_type || 'पिता');
            const relName = escapeHtml(v.relation_name || '--');
            const houseNo = escapeHtml(v.house_no || '--');
            const age = v.age ? `${v.age} वर्ष` : '--';
            const gender = escapeHtml(v.gender || '--');
            const isDel = Boolean(v.is_deleted);

            let casteBadge = '';
            if (showCaste && !isOperatorUser()) {
                if (v.is_muslim) {
                    casteBadge = '<span style="color: #047857; font-weight: 700; font-size: 6.5pt;">[मुस्लिम]</span>';
                } else if (v.caste_key && CASTE_LABELS[v.caste_key]) {
                    casteBadge = `<span style="color: #7c2d12; font-weight: 700; font-size: 6.5pt;">[${CASTE_LABELS[v.caste_key]}]</span>`;
                }
            }

            cardsHtml += `
                <div class="print-eci-card ${isDel ? 'deleted' : ''}">
                    <div class="print-card-header">
                        <span class="print-card-serial">${serialNo}</span>
                        <span class="print-card-epic">${epic}</span>
                    </div>
                    <div class="print-card-body">
                        <div class="print-card-info">
                            <div class="print-card-name">${name} ${casteBadge}</div>
                            <div class="print-card-row">
                                <span class="print-card-lbl">${relType}:</span>
                                <span>${relName}</span>
                            </div>
                            <div class="print-card-row">
                                <span class="print-card-lbl">मकान नं०:</span>
                                <span style="font-weight: 700;">${houseNo}</span>
                            </div>
                            <div class="print-card-row">
                                <span class="print-card-lbl">आयु / लिंग:</span>
                                <span>${age} / ${gender}</span>
                            </div>
                        </div>
                        <div class="print-card-photo-box">
                            फोटो<br>उपलब्ध<br>है
                        </div>
                    </div>
                </div>
            `;
        });

        // Fill remaining empty slots up to 30 if needed
        if (pageRecords.length < CARDS_PER_PAGE) {
            for (let emptyIdx = pageRecords.length; emptyIdx < CARDS_PER_PAGE; emptyIdx++) {
                cardsHtml += `
                    <div class="print-eci-card" style="border-style: dashed; opacity: 0.25; background: #fafafa;">
                        <div class="print-card-header" style="background: transparent; border: none;">
                            <span class="print-card-serial">--</span>
                        </div>
                        <div style="height: 60px; display: flex; align-items: center; justify-content: center; color: #94a3b8; font-size: 7pt;">
                            [रिक्त स्थान]
                        </div>
                    </div>
                `;
            }
        }

        fullHtml += `
            <div class="print-eci-page">
                <div class="print-page-header">
                    <div class="print-page-header-title">
                        विधान सभा निर्वाचन क्षेत्र: <strong>${escapeHtml(meta.assembly)}</strong> | <strong>${escapeHtml(meta.part)}</strong>
                    </div>
                    <div class="print-page-header-meta">
                        पृष्ठ ${pageIdx + 1} / ${totalPages} | कुल मतदाता: ${meta.totalRecords}
                    </div>
                </div>

                <div class="print-eci-grid-30">
                    ${cardsHtml}
                </div>

                <div class="print-page-footer">
                    <div>मतदान स्थल: <strong>${escapeHtml(meta.pollingStation)}</strong></div>
                    <div>प्रिंट दिनांक: ${meta.dateStr} | भारत निर्वाचन आयोग प्रारूप</div>
                </div>
            </div>
        `;
    }

    fullHtml += '</div>';
    return fullHtml;
}

// --------------------------------------------------------------------------
// FORMAT 2: COMPACT POLLING AGENT REGISTER (35-40 voters per A4 page)
// --------------------------------------------------------------------------
function renderCompactAgentTablePrint(records, meta, showCaste) {
    const ROWS_PER_PAGE = 38;
    const totalPages = Math.ceil(records.length / ROWS_PER_PAGE);
    let fullHtml = '<div class="print-document" style="padding: 0; background: #fff;">';

    for (let pageIdx = 0; pageIdx < totalPages; pageIdx++) {
        const pageRecords = records.slice(pageIdx * ROWS_PER_PAGE, (pageIdx + 1) * ROWS_PER_PAGE);
        let rowsHtml = '';

        pageRecords.forEach((v, idx) => {
            const overallIdx = (pageIdx * ROWS_PER_PAGE) + idx + 1;
            const serialNo = v.serial_no != null ? v.serial_no : '--';
            const epic = escapeHtml(v.epic_no || '--');
            const name = escapeHtml(v.name || '--');
            const relType = escapeHtml(v.relation_type || 'पिता');
            const relName = escapeHtml(v.relation_name || '--');
            const houseNo = escapeHtml(v.house_no || '--');
            const age = v.age || '--';
            const gender = escapeHtml(v.gender || '--');
            const isDel = Boolean(v.is_deleted);

            let casteStr = '';
            if (showCaste && !isOperatorUser()) {
                if (v.is_muslim) {
                    casteStr = 'मुस्लिम';
                } else if (v.caste_key && CASTE_LABELS[v.caste_key]) {
                    casteStr = CASTE_LABELS[v.caste_key];
                }
            }

            rowsHtml += `
                <tr style="${isDel ? 'text-decoration: line-through; background: #fee2e2;' : ''}">
                    <td style="text-align: center; font-weight: 700;">${overallIdx}</td>
                    <td class="col-tick">
                        <div class="print-tick-box"></div>
                    </td>
                    <td class="col-serial">${serialNo}</td>
                    <td class="col-house">${houseNo}</td>
                    <td class="col-name">${name}</td>
                    <td class="col-rel">${relName} <small style="color: #64748b;">(${relType})</small></td>
                    <td class="col-age-gen">${age} / ${gender}</td>
                    <td class="col-epic">${epic}</td>
                    ${showCaste && !isOperatorUser() ? `<td style="font-size: 7pt; text-align: center;">${casteStr}</td>` : ''}
                    <td class="col-sign"></td>
                </tr>
            `;
        });

        fullHtml += `
            <div class="print-agent-page">
                <div class="print-agent-header">
                    <div style="font-weight: 800; font-size: 11pt; color: #0f172a;">
                        मतदान अभिकर्ता रजिस्टर (POLLING AGENT BOOTH REGISTER)
                    </div>
                    <div class="print-agent-meta">
                        <div><strong>वि०स०:</strong> ${escapeHtml(meta.assembly)}</div>
                        <div><strong>${escapeHtml(meta.part)}</strong></div>
                        <div><strong>मतदान केंद्र:</strong> ${escapeHtml(meta.pollingStation)}</div>
                        <div><strong>पृष्ठ:</strong> ${pageIdx + 1} / ${totalPages}</div>
                    </div>
                </div>

                <table class="print-agent-table">
                    <thead>
                        <tr>
                            <th style="width: 25px; text-align: center;">क्र०</th>
                            <th style="width: 22px; text-align: center;">मत</th>
                            <th style="width: 42px; text-align: center;">सूची क्र०</th>
                            <th style="width: 45px; text-align: center;">मकान नं०</th>
                            <th style="width: 140px;">मतदाता का नाम</th>
                            <th style="width: 130px;">संबंधी का नाम</th>
                            <th style="width: 55px; text-align: center;">आयु/लिंग</th>
                            <th style="width: 85px; text-align: center;">पहचान पत्र (EPIC)</th>
                            ${showCaste && !isOperatorUser() ? '<th style="width: 50px; text-align: center;">जाति</th>' : ''}
                            <th style="width: 70px; text-align: center;">हस्ताक्षर / रिमार्क</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${rowsHtml}
                    </tbody>
                </table>

                <div style="display: flex; justify-content: space-between; font-size: 6.5pt; color: #64748b; margin-top: 4px; padding-top: 2px; border-top: 1px dashed #cbd5e1;">
                    <div>कुल मतदाता: ${meta.totalRecords} | प्रिंट दिनांक: ${meta.dateStr}</div>
                    <div>एजेंट हस्ताक्षर: ____________________ | दल/उम्मीदवार: ____________________</div>
                </div>
            </div>
        `;
    }

    fullHtml += '</div>';
    return fullHtml;
}

// --------------------------------------------------------------------------
// FORMAT 3: VOTER DISTRIBUTION SLIP SHEET (8 slips per A4 page, 2 cols x 4 rows)
// --------------------------------------------------------------------------
function renderVoterSlipsSheetPrint(records, meta) {
    const SLIPS_PER_PAGE = 8;
    const totalPages = Math.ceil(records.length / SLIPS_PER_PAGE);
    let fullHtml = '<div class="print-document" style="padding: 0; background: #fff;">';

    for (let pageIdx = 0; pageIdx < totalPages; pageIdx++) {
        const pageRecords = records.slice(pageIdx * SLIPS_PER_PAGE, (pageIdx + 1) * SLIPS_PER_PAGE);
        let slipsHtml = '';

        pageRecords.forEach(v => {
            const serialNo = v.serial_no != null ? v.serial_no : '--';
            const epic = escapeHtml(v.epic_no || '--');
            const name = escapeHtml(v.name || '--');
            const relType = escapeHtml(v.relation_type || 'पिता');
            const relName = escapeHtml(v.relation_name || '--');
            const houseNo = escapeHtml(v.house_no || '--');
            const age = v.age ? `${v.age} वर्ष` : '--';
            const gender = escapeHtml(v.gender || '--');
            const partNo = escapeHtml(v.part_no || meta.partNo || '--');
            const partName = escapeHtml(v.part_name || '');
            const pollingStation = escapeHtml(v.polling_station || meta.pollingStation || '--');

            slipsHtml += `
                <div class="print-slip-cut-item">
                    <div class="print-slip-cut-header">
                        <span>भारत निर्वाचन आयोग | ELECTION COMMISSION OF INDIA</span>
                        <span>भाग: ${partNo}</span>
                    </div>
                    <div class="print-slip-cut-title">मतदाता सूचना पर्ची (VOTER INFORMATION SLIP)</div>

                    <div class="print-slip-cut-content">
                        <div class="print-slip-cut-left">
                            <div class="slip-row">
                                <span class="slip-lbl">नाम:</span>
                                <span class="slip-val name">${name}</span>
                            </div>
                            <div class="slip-row">
                                <span class="slip-lbl">${relType}:</span>
                                <span class="slip-val">${relName}</span>
                            </div>
                            <div class="slip-row">
                                <span class="slip-lbl">मकान नं०:</span>
                                <span class="slip-val" style="font-weight: 700;">${houseNo}</span>
                                <span class="slip-lbl" style="margin-left: 8px;">आयु/लिंग:</span>
                                <span class="slip-val">${age} / ${gender}</span>
                            </div>
                            <div class="slip-row" style="margin-top: 2px;">
                                <span class="slip-lbl">EPIC NO:</span>
                                <span class="slip-val" style="font-family: monospace; font-weight: 850; color: #1e3a8a;">${epic}</span>
                            </div>
                        </div>

                        <div class="print-slip-cut-right">
                            <div class="print-slip-serial-badge">
                                <div class="badge-lbl">मतदाता सूची क्रमांक</div>
                                <div class="badge-num">${serialNo}</div>
                            </div>
                            <div class="slip-booth-info">
                                <div><strong>मतदान केंद्र:</strong> ${pollingStation}</div>
                                <div style="margin-top: 2px; color: #166534; font-weight: 600;">समय: 07:00 AM - 06:00 PM</div>
                            </div>
                        </div>
                    </div>

                    <div class="print-slip-cut-footer">
                        <span>नोट: पहचान हेतु मूल EPIC या मान्य फोटो पहचान पत्र साथ लाएं।</span>
                        <span>✂ कैंची से काटें</span>
                    </div>
                </div>
            `;
        });

        // Fill remaining empty slots up to 8 if needed
        if (pageRecords.length < 8) {
            for (let emptyIdx = pageRecords.length; emptyIdx < 8; emptyIdx++) {
                slipsHtml += `
                    <div class="print-slip-cut-item" style="border: 1px dashed #e2e8f0; background: #fafafa; opacity: 0.3;">
                        <div style="height: 100%; display: flex; align-items: center; justify-content: center; color: #94a3b8; font-size: 7pt;">
                            [रिक्त पर्ची]
                        </div>
                    </div>
                `;
            }
        }

        fullHtml += `
            <div class="print-slips-page">
                <div class="print-slips-grid-8">
                    ${slipsHtml}
                </div>
                <div style="text-align: center; font-size: 6pt; color: #64748b; margin-top: 3px;">
                    A4 शीट ${pageIdx + 1} / ${totalPages} | कैंची से काटकर मतदाताओं को बांटे | कम्प्यूटर जनरेटेड
                </div>
            </div>
        `;
    }

    fullHtml += '</div>';
    return fullHtml;
}

function printFilteredVoterList() {
    openDbPrintModal();
}

window.printSingleVoterSlip = async function(recordId) {
    try {
        let v = (dbState.records || []).find(r => r.id === recordId);
        if (!v) {
            const res = await fetch(`/api/database/slip/${recordId}`);
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || 'पर्ची डेटा प्राप्त नहीं हुआ');
            v = data.slip;
        }

        if (!v) {
            showToast('मतदाता विवरण नहीं मिला।', 'warning');
            return;
        }

        const dateStr = new Date().toLocaleDateString('hi-IN', { day: '2-digit', month: '2-digit', year: 'numeric' });
        const epic = escapeHtml(v.epic_no || '--');
        const serialNo = v.serial_no || '--';
        const partNo = escapeHtml(v.part_no || '--');
        const partName = escapeHtml(v.part_name || '');
        const relType = escapeHtml(v.relation_type || 'पिता');
        const relName = escapeHtml(v.relation_name || '--');
        const assembly = escapeHtml(v.assembly || v.assembly_name || 'विधान सभा निर्वाचन क्षेत्र');
        const pollingStation = escapeHtml(v.polling_station || '--');

        const html = `
            <div class="print-document" style="padding: 20px 0;">
                <div class="print-slip-box">
                    <div class="print-tiranga-stripe">
                        <div class="print-stripe-saffron"></div>
                        <div class="print-stripe-white"></div>
                        <div class="print-stripe-green"></div>
                    </div>

                    <div style="text-align: center; border-bottom: 2px solid #0f172a; padding-bottom: 8px; margin-bottom: 12px;">
                        <div style="font-size: 9pt; font-weight: 800; color: #1e3a8a; letter-spacing: 0.5px;">भारत निर्वाचन आयोग / ELECTION COMMISSION OF INDIA</div>
                        <div style="font-size: 14pt; font-weight: 850; color: #0f172a; margin: 3px 0;">डिजिटल मतदाता सूचना पर्ची (VOTER INFORMATION SLIP)</div>
                        <div style="font-size: 9pt; color: #475569; font-weight: 600;">विधान सभा निर्वाचन क्षेत्र: <strong>${assembly}</strong></div>
                    </div>

                    <div style="display: grid; grid-template-columns: 1.2fr 1fr; gap: 14px; margin-bottom: 12px; font-size: 8.5pt;">
                        <div style="background: #f8fafc; border: 1px solid #cbd5e1; border-radius: 6px; padding: 10px 12px; display: flex; flex-direction: column; gap: 6px;">
                            <div><span style="color: #64748b; font-size: 7.5pt; text-transform: uppercase;">मतदाता का नाम:</span><br><strong style="font-size: 11pt; color: #0f172a;">${escapeHtml(v.name || '')}</strong></div>
                            <div><span style="color: #64748b; font-size: 7.5pt; text-transform: uppercase;">${relType} का नाम:</span><br><strong style="font-size: 9.5pt; color: #1e293b;">${relName}</strong></div>
                            <div style="display: flex; gap: 16px;">
                                <div><span style="color: #64748b; font-size: 7.5pt; text-transform: uppercase;">मकान संख्या:</span><br><strong style="font-size: 9.5pt; color: #0f172a;">${escapeHtml(v.house_no || '--')}</strong></div>
                                <div><span style="color: #64748b; font-size: 7.5pt; text-transform: uppercase;">आयु / लिंग:</span><br><strong style="font-size: 9.5pt; color: #0f172a;">${v.age ? v.age + ' वर्ष' : '--'} / ${escapeHtml(v.gender || '--')}</strong></div>
                            </div>
                            <div style="margin-top: 4px; padding-top: 6px; border-top: 1px dashed #cbd5e1;">
                                <span style="color: #64748b; font-size: 7.5pt; text-transform: uppercase;">पहचान पत्र क्रमांक (EPIC NO):</span><br>
                                <span style="font-size: 13pt; font-weight: 850; font-family: monospace; color: #1e3a8a; letter-spacing: 1px;">${epic}</span>
                            </div>
                        </div>

                        <div style="background: #f8fafc; border: 1px solid #cbd5e1; border-radius: 6px; padding: 10px 12px; display: flex; flex-direction: column; gap: 6px;">
                            <div style="background: #f0fdf4; border: 1.5px solid #86efac; border-radius: 6px; padding: 6px 10px; text-align: center;">
                                <span style="color: #166534; font-size: 7.5pt; font-weight: 700; text-transform: uppercase;">मतदाता सूची क्रमांक (SERIAL NO)</span><br>
                                <span style="font-size: 16pt; font-weight: 900; color: #15803d; font-family: monospace;">${serialNo}</span>
                            </div>
                            <div>
                                <span style="color: #64748b; font-size: 7.5pt; text-transform: uppercase;">भाग संख्या व नाम:</span><br>
                                <strong style="font-size: 9pt; color: #0f172a;">भाग सं० ${partNo} ${partName ? `- ${partName}` : ''}</strong>
                            </div>
                            <div>
                                <span style="color: #64748b; font-size: 7.5pt; text-transform: uppercase;">मतदान स्थल (POLLING STATION):</span><br>
                                <strong style="font-size: 8.5pt; color: #1e293b;">${pollingStation}</strong>
                            </div>
                            <div>
                                <span style="color: #64748b; font-size: 7.5pt; text-transform: uppercase;">मतदान का समय:</span><br>
                                <span style="font-size: 8pt; color: #334155; font-weight: 600;">प्रातः 07:00 बजे से सायं 06:00 बजे तक</span>
                            </div>
                        </div>
                    </div>

                    <div style="background: #fefce8; border: 1px solid #fef08a; border-radius: 4px; padding: 6px 10px; font-size: 7pt; color: #713f12; line-height: 1.4; margin-bottom: 12px;">
                        <strong>महत्वपूर्ण निर्देश:</strong> (1) यह पर्ची केवल मतदाता की सुविधा व मतदान केंद्र की जानकारी हेतु है। यह पहचान का प्रमाण नहीं है। (2) मतदान केंद्र पर अपना मूल मतदाता फोटो पहचान पत्र (EPIC) या चुनाव आयोग द्वारा मान्य 12 वैकल्पिक फोटो पहचान पत्रों में से कोई एक अवश्य साथ लाएं।
                    </div>

                    <div style="display: flex; justify-content: space-between; align-items: flex-end; padding-top: 8px; border-top: 1px dashed #64748b; font-size: 7.5pt;">
                        <div>
                            <div><strong>प्रिंट दिनांक:</strong> ${dateStr}</div>
                            <div style="color: #64748b;">कम्प्यूटर जनरेटेड अधिकृत मतदाता पर्ची</div>
                        </div>
                        <div style="text-align: center;">
                            <div style="height: 22px; width: 120px; border-bottom: 1px solid #0f172a; margin-bottom: 2px;"></div>
                            <div style="font-weight: 700; color: #0f172a;">हस्ताक्षर बीएलओ / प्राधिकृत</div>
                        </div>
                    </div>
                </div>
            </div>
        `;

        executePrint(html, `मतदाता_पर्ची_${v.name || 'voter'}`);
    } catch (err) {
        showToast('पर्ची प्रिंट त्रुटि: ' + err.message, 'error');
    }
};

window.executePrint = executePrint;
window.printHouseVotersReport = printHouseVotersReport;
window.printHouseVotersDirect = printHouseVotersDirect;
window.renderAndExecuteHousePrint = renderAndExecuteHousePrint;
window.printStreetAuditRegister = printStreetAuditRegister;
window.printForm6ActionList = printForm6ActionList;
window.printFilteredVoterList = printFilteredVoterList;

window.switchHvmTab = switchHvmTab;
window.loadHouseVotersManual = loadHouseVotersManual;
window.openHouseVotersModal = openHouseVotersModal;
window.closeHouseVotersModal = closeHouseVotersModal;
window.filterHvmVoters = filterHvmVoters;
window.selectVoterForMapping = selectVoterForMapping;
window.handleMapSubmit = handleMapSubmit;
window.deleteMemberVoterMapping = deleteMemberVoterMapping;
window.openGlobalSearchForMember = openGlobalSearchForMember;
window.executeHvmGlobalSearch = executeHvmGlobalSearch;
window.quickMapCandidateToMember = quickMapCandidateToMember;
window.loadSurveyStreets = loadSurveyStreets;
window.loadStreetAuditData = loadStreetAuditData;

window.openDbPrintModal = openDbPrintModal;
window.closeDbPrintModal = closeDbPrintModal;
window.selectDbPrintFormat = selectDbPrintFormat;
window.updateDbPrintEstimates = updateDbPrintEstimates;
window.executeDbPrintWithOptions = executeDbPrintWithOptions;
window.renderEciPaperGridPrint = renderEciPaperGridPrint;
window.renderCompactAgentTablePrint = renderCompactAgentTablePrint;
window.renderVoterSlipsSheetPrint = renderVoterSlipsSheetPrint;

// ==========================================================================
// OTA AUTO-UPDATER & HOT-PATCH CLIENT ENGINE
// ==========================================================================

const updateState = {
    isChecking: false,
    updateAvailable: false,
    info: null,
    isUpdating: false,
    hasNotifiedUser: false
};

async function checkAppUpdates(userInitiated = false) {
    if (updateState.isChecking || updateState.isUpdating) return;
    updateState.isChecking = true;

    try {
        const res = await (typeof adminFetch === 'function' ? adminFetch : fetch)('/api/system/check-update');
        if (!res.ok) {
            throw new Error(`HTTP ${res.status}`);
        }
        const data = await res.json();
        updateState.info = data;

        const updateBtn = document.getElementById('btnUpdateNav');
        const updateText = document.getElementById('updateNavText');

        if (data.update_available) {
            updateState.updateAvailable = true;
            if (updateBtn) {
                updateBtn.style.display = 'inline-flex';
                if (updateText) {
                    updateText.innerText = `अपडेट v${data.latest_version}`;
                }
            }

            // If user clicked or if not notified yet in this session
            if (userInitiated) {
                openUpdateModal();
            } else if (!updateState.hasNotifiedUser) {
                updateState.hasNotifiedUser = true;
                if (typeof showToast === 'function') {
                    showToast(`⚡ नया अपडेट v${data.latest_version} उपलब्ध है! नेवबार में बटन दबाकर अपडेट करें।`, 'info');
                }
            }
        } else {
            updateState.updateAvailable = false;
            if (updateBtn && !userInitiated) {
                updateBtn.style.display = 'none';
            }
            if (userInitiated && typeof showToast === 'function') {
                showToast(`✅ आपका सॉफ्टवेयर नवीनतम संस्करण (v${data.current_version}) पर है।`, 'success');
            }
        }
    } catch (err) {
        console.warn('[Auto-Updater] Check update notice:', err);
        if (userInitiated && typeof showToast === 'function') {
            showToast('अपडेट जांचने में असमर्थ (इंटरनेट अनुपलब्ध हो सकता है)', 'warning');
        }
    } finally {
        updateState.isChecking = false;
    }
}

function openUpdateModal() {
    const modal = document.getElementById('softwareUpdateModal');
    if (!modal) return;

    const data = updateState.info || {};
    const curBadge = document.getElementById('currentVersionBadge');
    const newBadge = document.getElementById('latestVersionBadge');
    const typeTag = document.getElementById('updateTypeTag');
    const sizeBadge = document.getElementById('updateSizeBadge');
    const changelogList = document.getElementById('updateChangelogList');
    const subtitle = document.getElementById('updateModalSubtitle');

    if (curBadge) curBadge.innerText = `v${data.current_version || '1.0.1'}`;
    if (newBadge) newBadge.innerText = `v${data.latest_version || '1.0.2'}`;
    if (subtitle && data.title) subtitle.innerText = data.title;

    if (typeTag) {
        if (data.update_type === 'full') {
            typeTag.innerText = '📦 पूर्ण इंस्टालर (Full Setup)';
            typeTag.style.background = '#FEF3C7';
            typeTag.style.color = '#B45309';
        } else {
            typeTag.innerText = '⚡ हॉट-पैच (Code Patch)';
            typeTag.style.background = '#DBEAFE';
            typeTag.style.color = '#1D4ED8';
        }
    }

    if (sizeBadge) {
        const sizeMb = data.update_type === 'full' ? (data.full_installer_size_mb || 285) : (data.patch_size_mb || 2.5);
        sizeBadge.innerText = `अनुमानित साइज़: ~${sizeMb} MB`;
    }

    if (changelogList) {
        changelogList.innerHTML = '';
        const items = Array.isArray(data.changelog) && data.changelog.length > 0
            ? data.changelog
            : ['सुरक्षा संवर्द्धन एवं प्रदर्शन में सुधार', 'आधिकारिक मतदाता सूची अद्यतन'];
        items.forEach(item => {
            const li = document.createElement('li');
            li.innerText = item;
            changelogList.appendChild(li);
        });
    }

    // Reset progress UI
    const progressSec = document.getElementById('updateProgressSection');
    if (progressSec) progressSec.style.display = 'none';

    const execBtn = document.getElementById('btnExecuteUpdate');
    if (execBtn) {
        execBtn.disabled = false;
        const execText = document.getElementById('btnExecuteUpdateText');
        if (execText) execText.innerText = '⚡ अभी अपडेट करें (Install Update)';
    }

    modal.style.display = 'flex';
    if (window.lucide) window.lucide.createIcons();
}

function closeUpdateModal() {
    if (updateState.isUpdating) {
        if (typeof showToast === 'function') {
            showToast('⚠️ अपडेट प्रक्रिया प्रगति पर है, कृपया प्रतीक्षा करें...', 'warning');
        }
        return;
    }
    const modal = document.getElementById('softwareUpdateModal');
    if (modal) modal.style.display = 'none';
}

async function executeAppUpdate() {
    if (updateState.isUpdating) return;
    const data = updateState.info;
    if (!data || !data.update_available) {
        if (typeof showToast === 'function') showToast('कोई वैध अपडेट उपलब्ध नहीं है।', 'warning');
        return;
    }

    const downloadUrl = data.patch_download_url || (data.patch && data.patch.url);
    const expectedSha = data.patch_sha256 || (data.patch && data.patch.sha256);
    const targetVersion = data.latest_version;

    if (!downloadUrl) {
        if (typeof showToast === 'function') showToast('पैच डाउनलोड URL उपलब्ध नहीं है।', 'error');
        return;
    }

    updateState.isUpdating = true;

    const execBtn = document.getElementById('btnExecuteUpdate');
    const dismissBtn = document.getElementById('btnDismissUpdate');
    const progressSec = document.getElementById('updateProgressSection');
    const progressBar = document.getElementById('updateProgressBar');
    const progressTitle = document.getElementById('updateProgressTitle');
    const progressPercent = document.getElementById('updateProgressPercent');
    const progressSubtext = document.getElementById('updateProgressSubtext');

    if (execBtn) execBtn.disabled = true;
    if (dismissBtn) dismissBtn.disabled = true;
    if (progressSec) progressSec.style.display = 'block';

    if (progressBar) progressBar.style.width = '20%';
    if (progressPercent) progressPercent.innerText = '20%';
    if (progressTitle) progressTitle.innerText = 'पैच डाउनलोड हो रहा है...';
    if (progressSubtext) progressSubtext.innerText = 'गिटहब रिलीज़ से सुरक्षित फ़ाइल डाउनलोड की जा रही है...';

    try {
        // Step progress animation
        const timer1 = setTimeout(() => {
            if (progressBar) progressBar.style.width = '55%';
            if (progressPercent) progressPercent.innerText = '55%';
            if (progressTitle) progressTitle.innerText = 'SHA-256 हैश व डेटा शील्ड सत्यापन...';
            if (progressSubtext) progressSubtext.innerText = 'मतदाता डेटाबेस (DB) एवं लाइसेंस को सुरक्षित रखा जा रहा है...';
        }, 1200);

        const res = await (typeof adminFetch === 'function' ? adminFetch : fetch)('/api/system/apply-update', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                download_url: downloadUrl,
                expected_sha256: expectedSha,
                target_version: targetVersion
            })
        });

        clearTimeout(timer1);

        const resData = await res.json();
        if (!res.ok || resData.status !== 'success') {
            throw new Error(resData.detail || resData.message || 'अपडेट लागू करने में विफलता');
        }

        if (progressBar) progressBar.style.width = '100%';
        if (progressPercent) progressPercent.innerText = '100%';
        if (progressTitle) progressTitle.innerText = '✅ अपडेट सफलतापूर्वक पूर्ण हुआ!';
        if (progressSubtext) progressSubtext.innerText = 'सॉफ्टवेयर अब स्वतः रिफ्रेश हो रहा है...';

        if (typeof showToast === 'function') {
            showToast(`🎉 सॉफ्टवेयर सफलतापूर्वक v${targetVersion} पर अपडेट हो गया!`, 'success');
        }

        // Auto reload page after 2 seconds
        setTimeout(() => {
            window.location.reload();
        }, 2000);

    } catch (err) {
        console.error('[Auto-Updater] Error applying update:', err);
        updateState.isUpdating = false;
        if (execBtn) execBtn.disabled = false;
        if (dismissBtn) dismissBtn.disabled = false;
        if (progressSec) progressSec.style.display = 'none';

        if (typeof showToast === 'function') {
            showToast(`❌ अपडेट त्रुटि: ${err.message || 'अज्ञात समस्या'}`, 'error');
        }
        alert(`अपडेट लागू नहीं हो सका:\n${err.message || err}\n\nआपका पिछला डेटा और कोड पूरी तरह सुरक्षित है।`);
    }
}

// Wire up Auto-Updater Listeners and Periodic Polling
(function initAutoUpdater() {
    const updateNavBtn = document.getElementById('btnUpdateNav');
    if (updateNavBtn) {
        updateNavBtn.addEventListener('click', openUpdateModal);
    }

    const modal = document.getElementById('softwareUpdateModal');
    if (modal) {
        modal.addEventListener('click', (e) => {
            if (e.target === modal) closeUpdateModal();
        });
    }

    // Wire up Super-Admin Git Publisher
    const pubBtn = document.getElementById('btnOpenPublisherNav');
    if (pubBtn) {
        pubBtn.addEventListener('click', openGitPublishModal);
    }

    const execPublishBtn = document.getElementById('btnExecutePublish');
    if (execPublishBtn) {
        execPublishBtn.onclick = function(e) {
            if (e) e.preventDefault();
            executeOtaPublish();
        };
    }

    const pubModal = document.getElementById('gitPublishModal');
    if (pubModal) {
        pubModal.addEventListener('click', (e) => {
            if (e.target === pubModal) closeGitPublishModal();
        });
    }

    // Check 3.5s after load, then every 5 minutes
    setTimeout(() => checkAppUpdates(false), 3500);
    setInterval(() => checkAppUpdates(false), 300000);

    // Check superadmin publisher visibility
    setTimeout(checkSuperAdminPublisherVisibility, 800);
    setInterval(checkSuperAdminPublisherVisibility, 8000);
})();

// ==========================================================================
// SUPER-ADMIN ONE-CLICK GIT & OTA RELEASE PUBLISHER (HARSHSAMRAT ONLY)
// ==========================================================================

let gitPublishState = {
    isPublishing: false,
    config: null
};

function checkSuperAdminPublisherVisibility() {
    const pubBtn = document.getElementById('btnOpenPublisherNav');
    if (!pubBtn) return;
    if (typeof isSuperAdminUser === 'function' && isSuperAdminUser()) {
        pubBtn.style.display = 'inline-flex';
    } else {
        pubBtn.style.display = 'none';
    }
}

async function openGitPublishModal() {
    if (!isSuperAdminUser()) {
        if (typeof showToast === 'function') {
            showToast('पहुँच अस्वीकृत: "🚀 नया अपडेट पब्लिश करें" सुविधा केवल मुख्य सुपर एडमिन (harshsamrat) हेतु आरक्षित है।', 'error');
        }
        return;
    }
    const modal = document.getElementById('gitPublishModal');
    if (!modal) return;

    try {
        const res = await (typeof adminFetch === 'function' ? adminFetch : fetch)('/api/admin/git-publish-config');
        if (res.ok) {
            const data = await res.json();
            gitPublishState.config = data;

            const curLbl = document.getElementById('pubCurVersionLabel');
            const patchTxt = document.getElementById('pubNextPatchText');
            const minorTxt = document.getElementById('pubNextMinorText');
            const tokenBadge = document.getElementById('pubSavedTokenBadge');
            const tokenInput = document.getElementById('pubGitTokenInput');

            if (curLbl) curLbl.innerText = `v${data.current_version}`;
            if (patchTxt) patchTxt.innerText = `v${data.next_patch} (बगफिक्स/स्पीड सुधार)`;
            if (minorTxt) minorTxt.innerText = `v${data.next_minor} (नई सुविधाएं)`;

            if (tokenBadge && tokenInput) {
                if (data.has_saved_token) {
                    tokenBadge.style.display = 'inline-block';
                    tokenBadge.innerText = `🔒 सुरक्षित सेव्ड (${data.token_preview})`;
                    tokenInput.placeholder = 'सेव्ड टोकन मौजूद है (बदलने के लिए नया टोकन दर्ज करें)';
                } else {
                    tokenBadge.style.display = 'none';
                    tokenInput.placeholder = 'ghp_... (GitHub Personal Access Token)';
                }
            }
        }
    } catch (e) {
        console.warn('Could not load git config:', e);
    }

    // Reset UI
    const progressSec = document.getElementById('pubProgressSection');
    if (progressSec) progressSec.style.display = 'none';
    const execBtn = document.getElementById('btnExecutePublish');
    if (execBtn) {
        execBtn.disabled = false;
        const txt = document.getElementById('btnExecutePublishText');
        if (txt) txt.innerText = '🚀 एक क्लिक में पैकेज व GitHub पर पब्लिश करें';
    }

    modal.style.display = 'flex';
    if (window.lucide) window.lucide.createIcons();
}

function closeGitPublishModal() {
    if (gitPublishState.isPublishing) {
        if (typeof showToast === 'function') {
            showToast('⚠️ पब्लिश प्रक्रिया चालू है, कृपया समाप्त होने तक प्रतीक्षा करें...', 'warning');
        }
        return;
    }
    const modal = document.getElementById('gitPublishModal');
    if (modal) modal.style.display = 'none';
}

function updatePublishVersionPreview() {
    const radios = document.getElementsByName('pubBumpType');
    let sel = 'patch';
    for (const r of radios) {
        if (r.checked) sel = r.value;
    }
    const custInput = document.getElementById('pubCustomVersionInput');
    if (custInput) {
        custInput.disabled = (sel !== 'custom');
        if (sel === 'custom') custInput.focus();
    }
}

async function executeOtaPublish() {
    if (gitPublishState.isPublishing) return;
    if (!isSuperAdminUser()) {
        alert('पहुँच अस्वीकृत: "🚀 नया अपडेट पब्लिश करें" केवल मुख्य सुपर एडमिन (harshsamrat) ही उपयोग कर सकते हैं।');
        return;
    }

    const radios = document.getElementsByName('pubBumpType');
    let bumpType = 'patch';
    for (const r of radios) {
        if (r.checked) bumpType = r.value;
    }

    const custInput = document.getElementById('pubCustomVersionInput');
    const customVer = (custInput && !custInput.disabled) ? custInput.value.trim() : null;

    const notesInput = document.getElementById('pubReleaseNotes');
    const notes = notesInput ? notesInput.value.trim() : '';

    const clText = document.getElementById('pubChangelogText');
    const changelogLines = clText ? clText.value.split('\n').map(l => l.trim()).filter(l => l.length > 0) : [];

    const tokenInput = document.getElementById('pubGitTokenInput');
    const token = tokenInput ? tokenInput.value.trim() : '';

    const remCheck = document.getElementById('pubRememberTokenCheck');
    const remember = remCheck ? remCheck.checked : false;

    // Validate GitHub Token before execution
    const hasStoredToken = Boolean(gitPublishState.config?.has_saved_token);
    if (!token && !hasStoredToken) {
        if (typeof showToast === 'function') {
            showToast('⚠️ कृपया GitHub Personal Access Token (PAT) दर्ज करें।', 'warning');
        }
        alert('⚠️ GitHub Personal Access Token (PAT) आवश्यक है।\n\nकृपया "4. GitHub Personal Access Token" वाले बॉक्स में अपना GitHub टोकन (ghp_...) दर्ज करें।\nयदि टोकन नहीं है, तो नीचे दिए गए "टोकन बनाएं ↗" लिंक पर क्लिक करके नया टोकन प्राप्त करें (scope: repo)।');
        if (tokenInput) {
            tokenInput.focus();
            tokenInput.style.borderColor = '#EF4444';
        }
        return;
    }

    // Target version preview
    const targetVerPreview = (bumpType === 'custom' && customVer) 
        ? customVer 
        : (bumpType === 'minor' ? (gitPublishState.config?.next_minor || '1.1.0') : (gitPublishState.config?.next_patch || '1.0.3'));

    gitPublishState.isPublishing = true;

    const execBtn = document.getElementById('btnExecutePublish');
    const dismissBtn = document.getElementById('btnDismissPublish');
    const progressSec = document.getElementById('pubProgressSection');
    const progressBar = document.getElementById('pubProgressBar');
    const progressTitle = document.getElementById('pubProgressTitle');
    const progressPercent = document.getElementById('pubProgressPercent');
    const consoleLogs = document.getElementById('pubConsoleLogs');

    if (execBtn) execBtn.disabled = true;
    if (dismissBtn) dismissBtn.disabled = true;
    if (progressSec) progressSec.style.display = 'block';

    if (progressBar) progressBar.style.width = '25%';
    if (progressPercent) progressPercent.innerText = '25%';
    if (progressTitle) progressTitle.innerText = 'कोड फाइल्स स्कैन व 2.4 MB पैच जिप तैयार हो रहा है...';
    if (consoleLogs) {
        consoleLogs.innerHTML = `<div>[${new Date().toLocaleTimeString()}] 🚀 पब्लिश प्रक्रिया प्रारंभ हुई...</div>`;
    }

    const timer1 = setTimeout(() => {
        if (progressBar) progressBar.style.width = '60%';
        if (progressPercent) progressPercent.innerText = '60%';
        if (progressTitle) progressTitle.innerText = 'SHA-256 हैश, version.json व Git कमिट तैयार...';
        if (consoleLogs) {
            consoleLogs.innerHTML += `<div>[${new Date().toLocaleTimeString()}] 🔐 क्रिप्टोग्राफिक SHA-256 सत्यापन पूर्ण</div>`;
            consoleLogs.innerHTML += `<div>[${new Date().toLocaleTimeString()}] 📝 version.json v${targetVerPreview} तैयार</div>`;
        }
    }, 1200);

    const timer2 = setTimeout(() => {
        if (progressBar) progressBar.style.width = '85%';
        if (progressPercent) progressPercent.innerText = '85%';
        if (progressTitle) progressTitle.innerText = 'GitHub पर कोड व टैग पुश हो रहा है...';
        if (consoleLogs) {
            consoleLogs.innerHTML += `<div>[${new Date().toLocaleTimeString()}] 🏷️ Git टैग v${targetVerPreview} बनाया गया</div>`;
            consoleLogs.innerHTML += `<div>[${new Date().toLocaleTimeString()}] 🌐 harshsamrat-lgtm/voter_list पर पुश चालू...</div>`;
        }
    }, 3000);

    try {
        const res = await (typeof adminFetch === 'function' ? adminFetch : fetch)('/api/admin/publish-ota-release', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                bump_type: bumpType,
                custom_version: customVer,
                notes: notes,
                changelog: changelogLines,
                github_pat_token: token || null,
                remember_token: remember
            })
        });

        clearTimeout(timer1);
        clearTimeout(timer2);

        const data = await res.json();
        if (!res.ok || data.status !== 'success') {
            throw new Error(data.detail || data.message || 'पब्लिश विफल रहा');
        }

        if (progressBar) progressBar.style.width = '100%';
        if (progressPercent) progressPercent.innerText = '100%';
        if (progressTitle) progressTitle.innerText = '✅ GitHub पर सफलतापूर्वक पब्लिश हुआ!';

        if (consoleLogs && Array.isArray(data.logs)) {
            data.logs.forEach(l => {
                consoleLogs.innerHTML += `<div style="color: #38BDF8;">[${new Date().toLocaleTimeString()}] ${l}</div>`;
            });
            consoleLogs.scrollTop = consoleLogs.scrollHeight;
        }

        if (typeof showToast === 'function') {
            showToast(`🎉 संस्करण v${data.version} सफलतापूर्वक GitHub पर पब्लिश हो गया!`, 'success');
        }

        alert(`🎉 सफलता!\n\nसॉफ्टवेयर संस्करण v${data.version} सफलतापूर्वक Git पर पब्लिश हो गया है।\n\n• रिपॉजिटरी: https://github.com/harshsamrat-lgtm/voter_list\n• पैच साइज़: ${data.patch_size_mb} MB\n• SHA-256: ${data.sha256.substring(0, 16)}...\n\nअन्य सभी कंप्यूटरों पर अब यह अपडेट अपने आप उपलब्ध हो जाएगा।`);

        setTimeout(() => {
            window.location.reload();
        }, 1500);

    } catch (err) {
        console.error('Publish error:', err);
        clearTimeout(timer1);
        clearTimeout(timer2);
        gitPublishState.isPublishing = false;

        if (execBtn) execBtn.disabled = false;
        if (dismissBtn) dismissBtn.disabled = false;
        if (progressTitle) progressTitle.innerText = '❌ त्रुटि उत्पन्न हुई';
        if (consoleLogs) {
            consoleLogs.innerHTML += `<div style="color: #F87171;">[त्रुटि] ${err.message}</div>`;
        }

        if (typeof showToast === 'function') {
            showToast(`पब्लिश त्रुटि: ${err.message}`, 'error');
        }
        alert(`पब्लिश करने में समस्या आई:\n${err.message}\n\nकृपया सुनिश्चित करें कि GitHub Personal Access Token (PAT) वैध है और उसमें 'repo' स्कोप सक्रिय है।`);
    }
}

window.checkAppUpdates = checkAppUpdates;
window.openUpdateModal = openUpdateModal;
window.closeUpdateModal = closeUpdateModal;
window.executeAppUpdate = executeAppUpdate;

window.openGitPublishModal = openGitPublishModal;
window.closeGitPublishModal = closeGitPublishModal;
window.updatePublishVersionPreview = updatePublishVersionPreview;
window.executeOtaPublish = executeOtaPublish;
window.checkSuperAdminPublisherVisibility = checkSuperAdminPublisherVisibility;


