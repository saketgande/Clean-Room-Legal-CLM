// =============================================================
// AEGIS · runtime config
//
// Loaded BEFORE api.js so window.__AEGIS_CONFIG.apiBase is set
// before the API client reads it. In production this file is
// overridden (or replaced at deploy time) to point at the same
// origin behind the reverse proxy, e.g. apiBase: "/api/v1".
// =============================================================
window.__AEGIS_CONFIG = window.__AEGIS_CONFIG || { apiBase: "http://localhost:8000/api/v1" };
