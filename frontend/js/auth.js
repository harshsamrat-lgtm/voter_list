/**
 * Shared Authentication and Device Identification Client for UP Voter Seva.
 * Handles:
 * - Persistent Device ID & User-Friendly Device Name (e.g. 'Samsung Galaxy - Chrome')
 * - Token & Session Management (localStorage + cookie)
 * - Login, Logout, Self-Service Password Change
 * - Authenticated fetch wrapper (authFetch)
 */

const VoterAuth = (function() {
    const TOKEN_KEY = 'voter_auth_token';
    const USER_KEY = 'voter_auth_user';
    const DEVICE_ID_KEY = 'voter_device_id';

    // 1. Hardware & Browser Canvas Fingerprint Generator
    function getDeviceFingerprint() {
        try {
            const components = [];
            // Screen & Display
            components.push((screen.width || 0) + "x" + (screen.height || 0) + "x" + (screen.colorDepth || 24));
            components.push(window.devicePixelRatio || 1);
            // Platform & Hardware
            components.push(navigator.platform || "");
            components.push(navigator.hardwareConcurrency || 2);
            components.push(navigator.maxTouchPoints || 0);
            // Timezone & Language
            components.push(Intl.DateTimeFormat().resolvedOptions().timeZone || "");
            components.push(navigator.language || "");
            // Canvas subtle rendering fingerprint
            const canvas = document.createElement("canvas");
            canvas.width = 200;
            canvas.height = 40;
            const ctx = canvas.getContext("2d");
            if (ctx) {
                ctx.textBaseline = "top";
                ctx.font = "14px 'Arial', sans-serif";
                ctx.fillStyle = "#FF9933";
                ctx.fillRect(100, 1, 60, 20);
                ctx.fillStyle = "#138808";
                ctx.fillText("VoterSeva2026", 2, 12);
                ctx.fillStyle = "rgba(0, 0, 128, 0.7)";
                ctx.fillText("VoterSeva2026", 4, 14);
                components.push(canvas.toDataURL().slice(-40));
            }
            // FNV-1a 32-bit hash
            const str = components.join("###");
            let hash = 2166136261;
            for (let i = 0; i < str.length; i++) {
                hash ^= str.charCodeAt(i);
                hash += (hash << 1) + (hash << 4) + (hash << 7) + (hash << 8) + (hash << 24);
            }
            return "fp_" + (hash >>> 0).toString(16);
        } catch (e) {
            return "fp_std";
        }
    }

    // Cookie helper for device ID persistence
    function getCookie(name) {
        const match = document.cookie.match(new RegExp('(^|;\\s*)(' + name + ')=([^;]*)'));
        return match ? decodeURIComponent(match[3]) : null;
    }

    function setCookie(name, val, days = 730) {
        try {
            document.cookie = `${name}=${encodeURIComponent(val)}; path=/; max-age=${days * 86400}; SameSite=Lax`;
        } catch (e) {}
    }

    // 2. Multi-Storage Persistent Device ID Generator / Retrieval
    function getDeviceId() {
        const fp = getDeviceFingerprint();
        // Check localStorage, sessionStorage, and cookie
        let devId = localStorage.getItem(DEVICE_ID_KEY) || 
                    sessionStorage.getItem(DEVICE_ID_KEY) || 
                    getCookie(DEVICE_ID_KEY);

        if (!devId) {
            // Generate a persistent device UUID anchored to the hardware fingerprint
            const randomPart = ([1e7]+-1e3+-4e3+-8e3+-1e11).replace(/[018]/g, c =>
                (c ^ crypto.getRandomValues(new Uint8Array(1))[0] & 15 >> c / 4).toString(16)
            );
            devId = 'dev_' + fp + '_' + randomPart;
        }

        // Always heal and re-sync across all storage layers
        try { localStorage.setItem(DEVICE_ID_KEY, devId); } catch (e) {}
        try { sessionStorage.setItem(DEVICE_ID_KEY, devId); } catch (e) {}
        setCookie(DEVICE_ID_KEY, devId, 730);

        return devId;
    }

    // 2. User-Friendly Device & Browser Name Detector
    function getDeviceName() {
        const ua = navigator.userAgent;
        let os = "Unknown Device";
        let browser = "Browser";

        // Detect OS / Mobile Device
        if (/Android/i.test(ua)) {
            const match = ua.match(/Android\s([0-9\.]+);\s*([^;]+)\sBuild/i) || ua.match(/Android\s([0-9\.]+)/i);
            const model = match && match[2] ? match[2].trim() : "Android Mobile";
            os = model;
        } else if (/iPhone/i.test(ua)) {
            os = "Apple iPhone";
        } else if (/iPad/i.test(ua)) {
            os = "Apple iPad";
        } else if (/Windows/i.test(ua)) {
            os = "Windows PC";
        } else if (/Macintosh/i.test(ua)) {
            os = "Apple Mac";
        } else if (/Linux/i.test(ua)) {
            os = "Linux";
        }

        // Detect Browser
        if (/Edg/i.test(ua)) {
            browser = "Edge";
        } else if (/Chrome/i.test(ua) && !/Edg/i.test(ua)) {
            browser = "Chrome";
        } else if (/Safari/i.test(ua) && !/Chrome/i.test(ua)) {
            browser = "Safari";
        } else if (/Firefox/i.test(ua)) {
            browser = "Firefox";
        }

        return `${os} (${browser})`;
    }

    // 3. Token & User Storage
    function getToken() {
        return localStorage.getItem(TOKEN_KEY) || "";
    }

    function getUser() {
        try {
            const raw = localStorage.getItem(USER_KEY);
            return raw ? JSON.parse(raw) : null;
        } catch (e) {
            return null;
        }
    }

    function setSession(token, user) {
        localStorage.setItem(TOKEN_KEY, token);
        localStorage.setItem(USER_KEY, JSON.stringify(user));
        // Also set cookie so standard requests or downloads have credentials
        document.cookie = `voter_auth_token=${token}; path=/; max-age=2592000; SameSite=Lax`;
    }

    function clearSession() {
        localStorage.removeItem(TOKEN_KEY);
        localStorage.removeItem(USER_KEY);
        document.cookie = `voter_auth_token=; path=/; max-age=0; SameSite=Lax`;
    }

    // 4. Authenticated Fetch wrapper
    async function authFetch(url, options = {}) {
        const token = getToken();
        const headers = Object.assign({}, options.headers || {});
        if (token) {
            headers['Authorization'] = `Bearer ${token}`;
            headers['x-auth-token'] = token;
        }
        return fetch(url, { ...options, headers });
    }

    // 5. Login API
    async function login(username, password) {
        const payload = {
            username: username.trim(),
            password: password,
            device_id: getDeviceId(),
            device_name: getDeviceName(),
            device_fp: getDeviceFingerprint()
        };

        const res = await fetch('/api/auth/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        const data = await res.json();
        if (!res.ok) {
            throw new Error(data.detail || data.message || "लॉगिन विफल रहा।");
        }

        setSession(data.token, data.user);
        return data;
    }

    // 6. Logout API
    async function logout() {
        try {
            await authFetch('/api/auth/logout', { method: 'POST' });
        } catch (e) {
            console.warn("Logout request failed:", e);
        } finally {
            clearSession();
        }
    }

    // 7. Verify session with server
    async function checkMe() {
        const token = getToken();
        if (!token) {
            clearSession();
            return null;
        }
        try {
            const res = await authFetch('/api/auth/me');
            if (res.ok) {
                const data = await res.json();
                if (data.authenticated && data.user) {
                    setSession(token, data.user);
                    return data.user;
                }
            }
        } catch (e) {
            console.warn("Check auth error:", e);
        }
        clearSession();
        return null;
    }

    // 8. Change Password API
    async function changePassword(newPassword, oldPassword = null) {
        const payload = {
            new_password: newPassword,
            old_password: oldPassword
        };
        const res = await authFetch('/api/auth/change-password', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (!res.ok) {
            throw new Error(data.detail || data.message || "पासवर्ड बदलना विफल रहा।");
        }
        return data;
    }

    return {
        getDeviceId,
        getDeviceFingerprint,
        getDeviceName,
        getToken,
        getUser,
        setSession,
        clearSession,
        authFetch,
        login,
        logout,
        checkMe,
        changePassword
    };
})();

// Attach to window for global accessibility
window.VoterAuth = VoterAuth;
