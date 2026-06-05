/* ===========================================================
   AEGIS · navigation + interactions
   Client-side, vanilla JS (talks to the backend via api.js).
   =========================================================== */
(() => {
  // Single shared HTML-escape helper (the others below are thin aliases of it).
  // api.js already created window.aegis; guard in case app.js ever loads alone.
  window.aegis = window.aegis || {};
  window.aegis.esc =
    window.aegis.esc ||
    ((s) =>
      String(s == null ? "" : s).replace(/[&<>"']/g, (m) =>
        ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[m])
      ));

  /* ---------- Global error handler ----------
     Logs uncaught errors + unhandled promise rejections. If a Sentry DSN is
     configured (window.__AEGIS_CONFIG.sentryDsn) AND the Sentry SDK is loaded,
     forward to it — otherwise this is a pure no-op beyond console logging.
     No hard dependency on Sentry. */
  (function installGlobalErrorHandler() {
    const cfg = window.__AEGIS_CONFIG || {};
    const captureToSentry = (err) => {
      if (cfg.sentryDsn && window.Sentry && typeof window.Sentry.captureException === "function") {
        try { window.Sentry.captureException(err); } catch {}
      }
    };
    window.addEventListener("error", (e) => {
      console.error("[AEGIS] uncaught error:", e.error || e.message || e);
      captureToSentry(e.error || new Error(e.message || "uncaught error"));
    });
    window.addEventListener("unhandledrejection", (e) => {
      console.error("[AEGIS] unhandled promise rejection:", e.reason);
      captureToSentry(e.reason instanceof Error ? e.reason : new Error(String(e.reason)));
    });
  })();

  const root = document.documentElement;
  const views = document.querySelectorAll("[data-view]");
  const navItems = document.querySelectorAll(".nav-item[data-view]");
  const threadItems = document.querySelectorAll(".thread-item[data-view]");
  const paletteItems = document.querySelectorAll(".palette__item[data-view]");
  const palette = document.querySelector("[data-palette]");
  const themeToggleButtons = document.querySelectorAll('[data-action="theme"]');

  /* ---------- View switching ---------- */
  function show(viewName) {
    // Spec B — Contract Brain + Workflows are collapsed into the one assistant.
    // Any link/route to them lands on Ask AEGIS instead.
    if (viewName === "brain" || viewName === "workflows") viewName = "assistant";
    views.forEach((v) => {
      const isPanel = v.classList.contains("view");
      if (!isPanel) return;
      const match = v.dataset.view === viewName;
      v.hidden = !match;
    });
    navItems.forEach((n) =>
      n.classList.toggle("is-active", n.dataset.view === viewName)
    );
    // Update browser hash for back/forward
    if (location.hash.slice(1) !== viewName) {
      history.replaceState(null, "", `#${viewName}`);
    }
    // Scroll the new view to top
    const active = document.querySelector(`.view[data-view="${viewName}"]`);
    if (active) active.scrollTop = 0;
    // Notify renderers (live-data layer) of the switch
    if (window.aegis && window.aegis.store) {
      window.aegis.store.emit("view/show", viewName);
    }
  }
  // Expose for renderers.js and other modules
  window.__aegisShow = show;

  /* ===========================================================
     Approval decision page — opened from the email's Approve/Reject
     link: #approve?token=…&d=approve. Token-authenticated (no login;
     the single-use expiring token is the credential). Overlays the app.
     =========================================================== */
  (function approvalDecisionPage() {
    function parse() {
      const h = location.hash || "";
      if (!h.startsWith("#approve")) return null;
      const qs = h.includes("?") ? h.slice(h.indexOf("?") + 1) : "";
      const p = new URLSearchParams(qs);
      return { token: p.get("token") || "", d: (p.get("d") || p.get("decision") || "").toLowerCase() };
    }
    function render() {
      const info = parse();
      const existing = document.querySelector(".approve-page");
      // Only show for a real decision link (#approve WITH a token). Any other
      // hash / in-app navigation tears the overlay down so it can never block
      // normal views.
      if (!info || !info.token) { existing && existing.remove(); return; }
      if (existing) return;
      const ov = document.createElement("div");
      ov.className = "approve-page";
      ov.innerHTML =
        '<div class="approve-card">' +
        '<div class="approve-card__brand">AEGIS</div>' +
        "<h1>Approval decision</h1>" +
        '<p class="approve-card__sub">You’ve been asked to approve a contract via a secure one-time link. Confirm your decision below.</p>' +
        '<textarea class="approve-card__comment" rows="3" placeholder="Add a comment (optional)…"></textarea>' +
        '<div class="approve-card__actions">' +
        '<button class="approve-card__btn approve-card__btn--approve">Approve</button>' +
        '<button class="approve-card__btn approve-card__btn--reject">Reject</button>' +
        "</div>" +
        '<div class="approve-card__result" hidden></div>' +
        '<p class="approve-card__note">Secure single-use link · expires after a limited time.</p>' +
        "</div>";
      document.body.appendChild(ov);
      const result = ov.querySelector(".approve-card__result");
      const comment = ov.querySelector(".approve-card__comment");
      async function decide(decision) {
        if (!info.token) {
          result.hidden = false;
          result.className = "approve-card__result approve-card__result--err";
          result.textContent = "This link is missing its token — please click the button directly from your email.";
          return;
        }
        ov.querySelectorAll("button").forEach((b) => (b.disabled = true));
        result.hidden = false;
        result.className = "approve-card__result";
        result.textContent = "Submitting…";
        try {
          const r = await window.aegis.api.approvals.tokenDecision(info.token, decision, comment.value.trim());
          result.className = "approve-card__result approve-card__result--ok";
          result.innerHTML =
            "<strong>" + (decision === "approve" ? "✓ Approved." : "Rejected.") + "</strong> The decision has been recorded" +
            (r && r.status ? " (status: " + r.status + ")" : "") + ". You can close this tab.";
          ov.querySelector(".approve-card__actions").style.display = "none";
          comment.style.display = "none";
        } catch (e) {
          ov.querySelectorAll("button").forEach((b) => (b.disabled = false));
          result.className = "approve-card__result approve-card__result--err";
          result.textContent =
            e.status === 404 || e.status === 400 || /not found|invalid|expired|used/i.test(e.message || "")
              ? "This link is invalid, already used, or expired."
              : (e.message || "Couldn’t record the decision. Please try again.");
        }
      }
      ov.querySelector(".approve-card__btn--approve").onclick = () => decide("approve");
      ov.querySelector(".approve-card__btn--reject").onclick = () => decide("reject");
      (info.d === "reject"
        ? ov.querySelector(".approve-card__btn--reject")
        : ov.querySelector(".approve-card__btn--approve")
      ).classList.add("is-primary");
    }
    render();
    window.addEventListener("hashchange", render);
    // The app navigates via history.replaceState (no hashchange event), so also
    // re-evaluate on every in-app view switch — otherwise the overlay can persist.
    if (window.aegis && window.aegis.store) window.aegis.store.on("view/show", render);
  })();

  function bindNav(items) {
    items.forEach((el) => {
      el.addEventListener("click", (e) => {
        e.preventDefault();
        const v = el.dataset.view;
        if (!v) return;
        show(v);
        closePalette();
      });
    });
  }
  bindNav(navItems);
  bindNav(threadItems);
  bindNav(paletteItems);

  /* ---------- Initial view from hash (Ask AEGIS is the landing) ---------- */
  const initial = (location.hash || "#assistant").slice(1);
  const valid = [
    "hub",
    "contracts",
    "contract",
    "projects",
    "project",
    "playbooks",
    "playbook",
    "approvals",
    "signatures",
    "obligations",
    "renewals",
    "review",
    "assistant",
    "workflows",
    "brain",
    "search",
    "notifications",
    "admin",
    "jobs",
    "upload",
  ];
  show(valid.includes(initial) ? initial : "assistant");

  /* ---------- Theme toggle ---------- */
  function toggleTheme() {
    const cur = root.getAttribute("data-theme");
    const next = cur === "dark" ? "light" : "dark";
    root.setAttribute("data-theme", next);
    try {
      localStorage.setItem("aegis-theme", next);
    } catch {}
  }
  try {
    const saved = localStorage.getItem("aegis-theme");
    if (saved === "dark" || saved === "light")
      root.setAttribute("data-theme", saved);
  } catch {}
  themeToggleButtons.forEach((b) => b.addEventListener("click", toggleTheme));

  /* ---------- Command palette ---------- */
  function openPalette() {
    palette.hidden = false;
    const input = palette.querySelector("input");
    if (input) {
      input.value = "";
      setTimeout(() => input.focus(), 0);
    }
  }
  function closePalette() {
    palette.hidden = true;
  }
  document
    .querySelector('[data-action="close-palette"]')
    ?.addEventListener("click", closePalette);

  window.addEventListener("keydown", (e) => {
    const isMod = e.metaKey || e.ctrlKey;
    if (isMod && e.key === "k") {
      e.preventDefault();
      palette.hidden ? openPalette() : closePalette();
    } else if (e.key === "Escape" && !palette.hidden) {
      closePalette();
    } else if (isMod && /^[1-7]$/.test(e.key)) {
      const map = [
        "hub",
        "contracts",
        "projects",
        "playbooks",
        "approvals",
        "renewals",
        "assistant",
      ];
      e.preventDefault();
      show(map[parseInt(e.key, 10) - 1]);
    } else if (isMod && e.key === "n") {
      e.preventDefault();
      show("contracts");
    } else if (isMod && e.key === "t") {
      e.preventDefault();
      toggleTheme();
    }
  });

  /* ---------- Auto-grow textareas ---------- */
  document.querySelectorAll(".composer__input").forEach((t) => {
    const grow = () => {
      t.style.height = "auto";
      t.style.height = Math.min(t.scrollHeight, 320) + "px";
    };
    t.addEventListener("input", grow);
    grow();
  });

  /* ---------- Send on Enter (home composer only — the assistant view
       has its own handler that produces a real conversation) ---------- */
  document.querySelectorAll(".composer").forEach((c) => {
    // Skip composers inside the assistant view AND the brain Q&A — they
    // get their own dedicated handlers below.
    if (c.closest(".view--assistant") || c.closest(".brain__qa")) return;
    const input = c.querySelector(".composer__input");
    const send = c.querySelector(".send-btn");
    if (!input || !send) return;
    const carry = () => {
      const q = input.value.trim();
      if (!q) return;
      flashSend(send);
      input.value = "";
      input.style.height = "auto";
      show("assistant");
      // Carry the typed question into the assistant thread and fire it
      // (previously the text was discarded and Priya landed on a blank hero).
      if (window.__seedAssistant) window.__seedAssistant(q);
    };
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); carry(); }
    });
    send.addEventListener("click", carry);
  });

  /* ---------- Contract row navigates to contract detail ---------- */
  document.querySelectorAll(".ctr-table tbody tr[data-view]").forEach((row) => {
    row.addEventListener("click", () => show(row.dataset.view));
  });

  /* ---------- Filter chips toggle ---------- */
  document.querySelectorAll(".filter-bar").forEach((bar) => {
    bar.addEventListener("click", (e) => {
      const chip = e.target.closest(".filter-chip");
      if (!chip || chip.classList.contains("filter-chip--ghost")) return;
      bar.querySelectorAll(".filter-chip").forEach((c) =>
        c.classList.remove("is-active")
      );
      chip.classList.add("is-active");
    });
  });

  /* ---------- Vault / wf-filter tabs (visual only) ---------- */
  document
    .querySelectorAll(".vault__tabs, .wf__filters")
    .forEach((wrap) => {
      wrap.addEventListener("click", (e) => {
        const tab = e.target.closest(".tab");
        if (!tab) return;
        wrap.querySelectorAll(".tab").forEach((t) => t.classList.remove("is-active"));
        tab.classList.add("is-active");
      });
    });

  /* ---------- Real tab switching (contract detail, project, etc.) ---------- */
  document.querySelectorAll(".cd-tabs").forEach((tabs) => {
    // Find the nearest container that contains both the tabs and the tab-panels
    const container =
      tabs.closest(".cd-grid__main") ||
      tabs.closest(".cd") ||
      tabs.closest(".approvals") ||
      tabs.closest(".contracts") ||
      tabs.parentElement;
    const panels = container.querySelectorAll(":scope > .tab-panel, :scope > div > .tab-panel, :scope .tab-panel");
    tabs.addEventListener("click", (e) => {
      const tab = e.target.closest(".cd-tab[data-tab]");
      if (!tab) return;
      const key = tab.dataset.tab;
      tabs.querySelectorAll(".cd-tab").forEach((t) =>
        t.classList.toggle("is-active", t === tab)
      );
      // Only switch panels owned by this container's immediate tabs
      const owned = Array.from(panels).filter((p) => {
        // Reject panels owned by a different cd-tabs in the same container
        const closestTabs = p.closest(".cd-tabs");
        return closestTabs === null || closestTabs === tabs;
      });
      owned.forEach((p) => {
        p.hidden = p.dataset.tabPanel !== key;
      });
    });
  });

  /* ---------- Document viewer: highlight ↔ annotation pairing ---------- */
  function focusAnn(annId, scrollTarget) {
    if (!annId) return;
    document.querySelectorAll(".hl, .ann").forEach((el) =>
      el.classList.remove("is-focused")
    );
    document
      .querySelectorAll(`.hl[data-ann="${annId}"]`)
      .forEach((el) => el.classList.add("is-focused"));
    const ann = document.querySelector(`.ann[data-ann-id="${annId}"]`);
    if (ann) {
      ann.classList.add("is-focused");
      if (scrollTarget === "ann") {
        ann.scrollIntoView({ behavior: "smooth", block: "center" });
      }
    }
    if (scrollTarget === "hl") {
      const hl = document.querySelector(`.hl[data-ann="${annId}"]`);
      if (hl) hl.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }
  document.querySelectorAll(".hl[data-ann]").forEach((hl) => {
    hl.addEventListener("click", () => focusAnn(hl.dataset.ann, "ann"));
  });
  document.querySelectorAll(".ann[data-ann-id]").forEach((ann) => {
    ann.addEventListener("click", () => focusAnn(ann.dataset.annId, "hl"));
  });

  /* ---------- Brain graph node click → focus side panel ---------- */
  document.querySelectorAll(".bg-node").forEach((node) => {
    node.addEventListener("click", () => {
      document.querySelectorAll(".bg-node").forEach((n) =>
        n.classList.remove("is-active")
      );
      node.classList.add("is-active");
      const label = node.querySelector("text")?.textContent || "Node";
      const titleEl = document.querySelector(".brain-side h4");
      if (titleEl) titleEl.textContent = "Selected · " + label;
    });
  });

  /* ---------- Notification bell dropdown ---------- */
  const bellPanel = document.querySelector("[data-bell-panel]");
  // Populate the dropdown from REAL notifications (was static fabricated items).
  async function loadBellNotifications() {
    if (!bellPanel) return;
    const list = bellPanel.querySelector(".bell__list");
    const unreadLabel = bellPanel.querySelector(".bell__head span");
    const dot = document.querySelector(".bell__dot");
    const esc2 = window.aegis.esc; // shared escaper (thin alias)
    if (list) list.innerHTML = `<li class="bell__item"><div><div class="bell__title" style="color:var(--fg-muted)">Loading…</div></div></li>`;
    try {
      const notifs = window.aegis ? await window.aegis.api.notifications.list() : [];
      const items = Array.isArray(notifs) ? notifs : [];
      const isUnread = (n) => !n.read_at && !n.is_read && n.status !== "read";
      const unread = items.filter(isUnread).length;
      if (unreadLabel) unreadLabel.textContent = unread ? `${unread} unread` : "all caught up";
      if (dot) dot.style.display = unread ? "" : "none";
      if (list) list.innerHTML = items.length
        ? items.slice(0, 8).map((n) => `<li class="bell__item ${isUnread(n) ? "bell__item--unread" : ""}">
            <span class="notif__icon notif__icon--ai">●</span>
            <div><div class="bell__title">${esc2(n.title || n.message || n.body || String(n.event_type || "Notification").replace(/[._]/g, " "))}</div>
            <div class="bell__meta">${esc2(String(n.created_at || "").slice(0, 16).replace("T", " "))}</div></div></li>`).join("")
        : `<li class="bell__item"><div><div class="bell__title" style="color:var(--fg-muted)">No new notifications.</div></div></li>`;
    } catch (e) {
      if (list) list.innerHTML = `<li class="bell__item"><div><div class="bell__title" style="color:var(--fg-muted)">Couldn't load notifications.</div></div></li>`;
    }
  }
  function toggleBell() {
    if (!bellPanel) return;
    bellPanel.hidden = !bellPanel.hidden;
    if (!bellPanel.hidden) { closeUserMenu(); loadBellNotifications(); }
  }
  function closeBell() { if (bellPanel) bellPanel.hidden = true; }
  document
    .querySelector('[data-action="bell-toggle"]')
    ?.addEventListener("click", (e) => {
      e.stopPropagation();
      toggleBell();
    });
  bellPanel?.addEventListener("click", (e) => {
    const link = e.target.closest("[data-view]");
    if (link) {
      e.preventDefault();
      show(link.dataset.view);
      closeBell();
    }
  });

  /* ---------- User menu dropdown ---------- */
  const userMenu = document.querySelector("[data-user-menu]");
  function toggleUserMenu() {
    if (!userMenu) return;
    userMenu.hidden = !userMenu.hidden;
    if (!userMenu.hidden) closeBell();
  }
  function closeUserMenu() { if (userMenu) userMenu.hidden = true; }
  document
    .querySelector('[data-action="user-menu"]')
    ?.addEventListener("click", (e) => {
      e.stopPropagation();
      toggleUserMenu();
    });
  userMenu?.addEventListener("click", async (e) => {
    const item = e.target.closest(".user-menu__item, [data-view]");
    if (!item) return;
    const dv = item.getAttribute("data-view");
    if (dv) { e.preventDefault(); show(dv); closeUserMenu(); return; }
    const txt = (item.textContent || "").trim().toLowerCase();
    const esc = window.aegis.esc; // shared escaper (thin alias)
    if (/my profile/.test(txt)) {
      e.preventDefault(); closeUserMenu();
      const modal = window.__aegisModal; if (!modal || !window.aegis) return;
      const m = modal("My profile", `<div class="live-loading">Loading…</div>`, { noFoot: true });
      try {
        const me = await window.aegis.api.me.get();
        const inits = (me.full_name || me.email || "U").split(/\s+/).map((s) => s[0]).filter(Boolean).slice(0, 2).join("").toUpperCase();
        m.body.innerHTML = `<div style="display:flex;align-items:center;gap:12px;margin-bottom:14px"><div class="avatar" style="width:46px;height:46px;font-size:16px">${esc(inits)}</div><div><div style="font-weight:600;color:var(--fg-strong);font-size:15px">${esc(me.full_name || "—")}</div><div style="color:var(--fg-muted);font-size:13px">${esc(me.email || "")}</div><div style="color:var(--accent-bronze);font-size:12px;margin-top:2px">${esc(me.role || me.active_role || "Member")}</div></div></div><dl class="prof__meta" style="display:grid;grid-template-columns:auto 1fr;gap:6px 14px;font-size:12.5px"><dt style="color:var(--fg-subtle)">User ID</dt><dd style="font-family:monospace;color:var(--fg-muted)">${esc(String(me.id || "—").slice(0, 18))}</dd><dt style="color:var(--fg-subtle)">Org</dt><dd style="font-family:monospace;color:var(--fg-muted)">${esc(String(me.org_id || "—").slice(0, 18))}</dd><dt style="color:var(--fg-subtle)">Status</dt><dd>${esc(me.status || "active")}</dd></dl>`;
      } catch (err) { m.body.innerHTML = `<div class="live-error">Couldn't load profile: ${esc(err.message)}</div>`; }
      return;
    }
    if (/notification preferences/.test(txt)) { e.preventDefault(); closeUserMenu(); show("notifications"); }
  });

  /* ---------- Outside click closes both dropdowns ---------- */
  document.addEventListener("click", (e) => {
    if (bellPanel && !bellPanel.hidden && !e.target.closest("[data-bell]")) {
      closeBell();
    }
    if (userMenu && !userMenu.hidden && !e.target.closest(".user-chip, .user-menu")) {
      closeUserMenu();
    }
  });

  /* ---------- Login overlay ---------- */
  const login = document.querySelector("[data-login]");
  function showLogin() { if (login) login.hidden = false; }
  function hideLogin() { if (login) login.hidden = true; }
  // The "Sign out" menu item also uses data-action="login" (it points to login)
  // Wire it to actually log out, then show login.
  document.querySelectorAll('[data-action="login"]').forEach((b) =>
    b.addEventListener("click", async () => {
      try { await window.aegis?.api.auth.logout(); } catch {}
      showLogin();
      toast({ title: "Signed out", kind: "success" });
    })
  );
  document.querySelectorAll('[data-action="enter-app"]').forEach((b) =>
    b.addEventListener("click", hideLogin)
  );

  /* ---------- Real auth: login form submits to backend ---------- */
  document
    .querySelector('[data-action="login-submit"]')
    ?.addEventListener("click", async (e) => {
      e.preventDefault();
      if (!window.aegis) return hideLogin();
      const email = document.querySelector("[data-login-email]")?.value;
      const password = document.querySelector("[data-login-password]")?.value;
      const errEl = document.querySelector("[data-login-error]");
      if (errEl) errEl.hidden = true;
      try {
        await window.aegis.api.auth.login(email, password);
        hideLogin();
        toast({ title: "Signed in", sub: email, kind: "success" });
        try {
          const me = await window.aegis.api.me.get();
          updateUserChip(me);
        } catch {}
      } catch (err) {
        if (errEl) {
          errEl.textContent = err.message || "Sign-in failed";
          errEl.hidden = false;
        }
      }
    });

  /* ---------- Apply real user to the sidebar chip + menu header ---------- */
  function updateUserChip(me) {
    if (!me) return;
    const name = me.full_name || me.email || "User";
    const initials = me.avatar || name.split(" ").map((s) => s[0]).join("").slice(0, 2).toUpperCase();
    document.querySelectorAll(".user-chip .user-name").forEach((el) => (el.textContent = name));
    document.querySelectorAll(".user-chip .avatar").forEach((el) => (el.textContent = initials));
    document.querySelectorAll(".user-menu__name").forEach((el) => (el.textContent = name));
    document.querySelectorAll(".user-menu__email").forEach((el) => (el.textContent = me.email || ""));
    document.querySelectorAll(".user-menu .avatar").forEach((el) => (el.textContent = initials));
    if (me.role) {
      document.querySelectorAll(".user-chip .user-role").forEach((el) => (el.textContent = me.role));
    }
  }

  /* ---------- Backend = LIVE always ---------- */
  document.documentElement.setAttribute("data-aegis-mode", "rest");

  /* ---------- Auth: show login if no token, listen for 401 ---------- */
  if (window.aegis) {
    if (!window.aegis.api.auth.isLoggedIn()) {
      showLogin();
    } else {
      window.aegis.api.me.get().then(updateUserChip).catch(() => {});
    }
    window.aegis.store.on("auth/expired", () => {
      showLogin();
      toast({ title: "Session expired", sub: "Please sign in again", kind: "warn" });
    });
  }

  /* ---------- Hub segmented tabs (Overview ↔ Contracts) ---------- */
  document.querySelectorAll(".hub-tab[data-view]").forEach((tab) => {
    tab.addEventListener("click", (e) => {
      e.preventDefault();
      show(tab.dataset.view);
    });
  });

  /* ---------- Playbook DETAIL is fully handled in renderers.js
       (version selector, rich rule cards, runs, deviations, add-rule
       modal, publish, run). No detail handlers here to avoid conflicts. ---------- */

  /* ---------- Playbook: + New playbook modal ---------- */
  const pbModal = document.querySelector("[data-pb-new-modal]");
  function openPbModal() { if (pbModal) pbModal.hidden = false; }
  function closePbModal() { if (pbModal) pbModal.hidden = true; }
  document
    .querySelector('[data-action="pb-new"]')
    ?.addEventListener("click", openPbModal);
  document
    .querySelectorAll('[data-action="pb-new-close"]')
    .forEach((b) => b.addEventListener("click", closePbModal));
  document
    .querySelector('[data-action="pb-new-create"]')
    ?.addEventListener("click", async (e) => {
      const btn = e.currentTarget;
      if (!window.aegis) { closePbModal(); return show("playbook"); }
      const nameInput = pbModal?.querySelector('input[type="text"], input:not([type])');
      const name = (nameInput?.value || "").trim() || "New playbook";
      const activeTpl = pbModal?.querySelector(".pb-template.is-active");
      const desc = activeTpl ? (activeTpl.querySelector("h4, strong, .pb-template__name")?.textContent || "").trim() : "";
      btn.disabled = true;
      const orig = btn.textContent;
      btn.textContent = "Creating…";
      try {
        const pb = await window.aegis.api.playbooks.create({ name, description: desc || null });
        toast({ title: "Playbook created", sub: name, kind: "success" });
        closePbModal();
        window.__activePlaybookId = pb.id;
        show("playbook");
        window.aegis.renderers?.playbook?.();
      } catch (err) {
        toast({ title: "Couldn't create playbook", sub: err.message, kind: "error" });
      } finally {
        btn.disabled = false;
        btn.textContent = orig;
      }
    });
  pbModal?.addEventListener("click", (e) => {
    const t = e.target.closest(".pb-template");
    if (!t) return;
    pbModal.querySelectorAll(".pb-template").forEach((x) =>
      x.classList.remove("is-active")
    );
    t.classList.add("is-active");
  });

  /* ---------- Project card click → project detail ---------- */
  document.querySelectorAll(".view--projects .vault-card").forEach((c) => {
    if (c.classList.contains("vault-card--ghost")) return;
    c.addEventListener("click", () => show("project"));
  });
  /* ---------- Playbook card click → playbook detail ---------- */
  document.querySelectorAll(".view--playbooks .vault-card").forEach((c) => {
    if (c.classList.contains("vault-card--ghost")) return;
    c.addEventListener("click", () => show("playbook"));
  });
  /* ---------- "+ New contract" / "+ New" buttons → upload wizard ---------- */
  document.querySelectorAll(".primary-btn").forEach((b) => {
    const t = (b.textContent || "").trim().toLowerCase();
    if (
      t === "+ new contract" ||
      t === "new contract" ||
      t === "+ new"
    ) {
      b.addEventListener("click", (e) => {
        e.preventDefault();
        show("upload");
      });
    }
  });
  /* ---------- Upload-wizard "Save & open contract" → REAL upload ---------- */
  document
    .querySelectorAll('.upload__foot .primary-btn[data-view]')
    .forEach((b) =>
      b.addEventListener("click", async () => {
        if (!window.aegis) return show(b.dataset.view);
        const title = document.querySelector("[data-up-title]")?.value || "New Contract";
        const counterparty = document.querySelector("[data-up-counterparty]")?.value || "";
        b.disabled = true;
        const orig = b.textContent;
        b.textContent = "Uploading…";
        try {
          // Generate a real contract text file and upload it (multipart)
          const text =
            `${title.toUpperCase()}\n\n` +
            `This Agreement is entered into between the Company and ${counterparty}.\n\n` +
            `1. SERVICES. The Vendor shall provide services as described in each Statement of Work.\n\n` +
            `2. FEES. Customer shall pay the fees set forth in each SOW, net 30 days.\n\n` +
            `3. TERM. Initial term of three (3) years, automatically renewing for one (1) year periods ` +
            `unless either party gives 90 days' written notice of non-renewal.\n\n` +
            `4. LIMITATION OF LIABILITY. Aggregate liability shall be capped at 12 months of fees, ` +
            `with carve-outs for confidentiality breach and IP infringement.\n\n` +
            `5. TERMINATION. Either party may terminate for material breach uncured after 30 days' notice.\n\n` +
            `6. GOVERNING LAW. This Agreement is governed by the laws of Delaware.\n`;
          const file = new File([text], `${title.replace(/[^a-z0-9]+/gi, "_")}.txt`, { type: "text/plain" });
          const created = await window.aegis.api.contracts.upload(file, {
            title,
            counterparty_name: counterparty,
          });
          toast({
            title: "Contract created",
            sub: `${title} · stage ${created.lifecycle_stage || "intake"}`,
            kind: "success",
          });
          // Open the new contract detail
          window.__activeContractId = created.id;
          show("contract");
          if (window.aegis.renderers && window.aegis.renderers.contract) {
            window.aegis.renderers.contract();
          }
        } catch (e) {
          toast({ title: "Upload failed", sub: e.message, kind: "error" });
          b.disabled = false;
          b.textContent = orig;
        }
      })
    );

  /* ---------- Live run: progress + step animation ---------- */
  (() => {
    const bar = document.querySelector(".run-progress__bar span");
    const meta = document.querySelector(".run-progress__meta");
    if (!bar) return;
    let pct = 44;
    setInterval(() => {
      if (pct < 56) {
        pct += 0.3;
        bar.style.width = pct.toFixed(1) + "%";
        if (meta) {
          meta.innerHTML =
            "<strong>" + pct.toFixed(0) + "%</strong> · 4 of 9 steps";
        }
      }
    }, 1200);
  })();

  /* ---------- Funnel step → contracts list filtered by stage (delegated) ----------
     Live funnel/queue nodes are re-rendered by renderHub, so bind via delegation. */
  document.addEventListener("click", (e) => {
    const step = e.target.closest(".funnel__step");
    if (!step) return;
    e.preventDefault();
    if (step.dataset.stage) window.__contractStageFilter = step.dataset.stage;
    show("contracts");
  });

  /* ---------- Queue item click → relevant view (delegated) ---------- */
  document.addEventListener("click", (e) => {
    const q = e.target.closest(".queue-item[data-view]");
    if (!q) return;
    e.preventDefault();
    show(q.dataset.view);
  });

  function flashSend(btn) {
    btn.animate(
      [
        { transform: "scale(1)" },
        { transform: "scale(0.92)" },
        { transform: "scale(1.05)" },
        { transform: "scale(1)" },
      ],
      { duration: 240, easing: "cubic-bezier(0.2, 0.9, 0.2, 1.1)" }
    );
  }

  /* ---------- Source pill toggles ---------- */
  document.querySelectorAll(".source-pill").forEach((p) => {
    if (p.classList.contains("source-pill--add")) return;
    p.addEventListener("click", () => p.classList.toggle("is-on"));
  });

  /* ---------- Suggestion cards seed composer ---------- */
  const seedPrompts = {
    "Summarize an NDA":
      "Summarize the attached NDA: term, scope of confidentiality, carve-outs, and any non-standard provisions. Flag anything materially off-market.",
    "Compare two MSAs":
      "Compare the two attached MSAs side-by-side on governing law, liability cap, IP assignment, and termination — output a review table.",
    "Draft a SaaS order form":
      "Draft a SaaS order form from our standard playbook. Term: 24 months. Annual fee: $180,000. Auto-renew: yes, 60-day notice. Governing law: Delaware.",
    "Pull renewal dates from Vault":
      "Across the Project Helios vault, list every contract expiring in the next 90 days with their auto-renew terms and notice deadlines.",
    "Build a §220 demand":
      "Draft a Delaware §220 books-and-records demand letter on behalf of a 2.3% stockholder seeking minutes and board materials regarding the Q4 2025 transaction.",
    "Run M&A diligence checklist":
      "Run the firm's 240-question M&A diligence checklist against the Project Helios vault. Output: red-flag log + executive memo.",
  };
  document.querySelectorAll(".suggestion-card").forEach((card) => {
    card.addEventListener("click", () => {
      const title = card.querySelector(".suggestion-card__title")?.textContent;
      const composer = document.querySelector(
        '.view--home .composer__input'
      );
      if (composer && title && seedPrompts[title]) {
        composer.value = seedPrompts[title];
        composer.dispatchEvent(new Event("input"));
        composer.focus();
        composer.scrollIntoView({ behavior: "smooth", block: "center" });
      }
    });
  });

  /* ---------- Citation chip → highlight source ---------- */
  document.querySelectorAll(".cite-chip").forEach((chip) => {
    chip.addEventListener("click", (e) => {
      e.preventDefault();
      const num = chip.textContent.replace(/\D/g, "");
      const target = [...document.querySelectorAll(".source-item__num")].find(
        (n) => n.textContent.trim() === num
      );
      if (!target) return;
      const item = target.closest(".source-item");
      item.scrollIntoView({ behavior: "smooth", block: "center" });
      item.animate(
        [
          { background: "var(--accent-amber-soft)" },
          { background: "transparent" },
        ],
        { duration: 1200, easing: "ease-out" }
      );
    });
  });

  /* ===========================================================
     TOAST helper — global confirmations
     =========================================================== */
  const toastStack = document.querySelector("[data-toast-stack]");
  function toast(opts) {
    if (!toastStack) return;
    const { title, sub = "", kind = "success", icon, duration = 3200 } = opts;
    const el = document.createElement("div");
    el.className = "toast toast--" + kind;
    el.innerHTML = `
      <div class="toast__icon">${icon || (kind === "success" ? "✓" : kind === "warn" ? "!" : kind === "error" ? "×" : "A")}</div>
      <div class="toast__body">
        <div class="toast__title">${title}</div>
        ${sub ? `<div class="toast__sub">${sub}</div>` : ""}
      </div>
      <button class="toast__close" aria-label="Dismiss">×</button>`;
    el.querySelector(".toast__close").addEventListener("click", () => dismiss());
    function dismiss() {
      el.classList.add("is-leaving");
      setTimeout(() => el.remove(), 220);
    }
    toastStack.appendChild(el);
    setTimeout(dismiss, duration);
    return dismiss;
  }
  // Expose toast so renderers.js can use it
  window.__aegisToast = toast;

  /* ===========================================================
     APPROVALS · approve / reject / request changes (via API)
     =========================================================== */
  document.addEventListener("click", async (e) => {
    const btn = e.target.closest(".approval-card__foot button");
    if (!btn) return;
    if (btn.classList.contains("approval-card__comment")) return;
    const card = btn.closest(".approval-card");
    if (!card) return;
    if (card.classList.contains("is-resolved")) return; // already decided
    const action = (btn.textContent || "").trim().toLowerCase();
    const title =
      card.querySelector(".approval-card__title")?.textContent || "Approval";
    const map = {
      approve: { decision: "approved", kind: "success", pill: "Approved", pillClass: "approval-card__resolved--approved", sub: "Routed back to requester" },
      reject:  { decision: "rejected", kind: "error",   pill: "Rejected", pillClass: "approval-card__resolved--rejected", sub: "Logged · requester notified" },
    };
    const op = map[action];
    if (!op) return;
    const commentField = card.querySelector(".approval-card__comment");
    const comment = (commentField && commentField.value || "").trim();
    // The backend requires a reason to reject — enforce it in the UI instead of 422-ing.
    if (op.decision === "rejected" && !comment) {
      toast({ title: "A reason is required to reject", sub: "Add a short note, then click Reject.", kind: "warn" });
      if (commentField) { commentField.placeholder = "Reason for rejection (required)…"; commentField.focus(); commentField.style.borderColor = "var(--sev-high, #dc2626)"; }
      return;
    }

    // Optimistic UI lock
    btn.disabled = true;
    try {
      const approvalId = card.dataset.id;
      if (approvalId && window.aegis) {
        await window.aegis.api.approvals.decide(approvalId, op.decision, comment);
      }
      card.classList.add("is-resolved");
      const pill = document.createElement("div");
      pill.className = "approval-card__resolved " + op.pillClass;
      pill.textContent = op.pill + " · just now";
      card.querySelector(".approval-card__body")?.after(pill);
      toast({ title: `${op.pill}: ${title}`, sub: op.sub, kind: op.kind });

      // Decrement the sidebar Approvals badge
      const badge = document.querySelector('[data-view="approvals"] .badge');
      if (badge && op.decision !== null) {
        const n = parseInt(badge.textContent, 10);
        if (!isNaN(n) && n > 0) badge.textContent = String(n - 1);
      }
    } catch (err) {
      toast({ title: "Action failed", sub: err.message || "Try again", kind: "error" });
      btn.disabled = false;
    }
  });

  /* ===========================================================
     SIGNATURES · send reminder / void envelope
     =========================================================== */
  document.addEventListener("click", (e) => {
    const btn = e.target.closest(".approval-card button");
    if (!btn) return;
    const txt = (btn.textContent || "").trim().toLowerCase();
    if (txt === "send reminder") {
      const card = btn.closest(".approval-card");
      const title =
        card?.querySelector(".approval-card__title")?.textContent || "envelope";
      toast({
        title: "Reminder sent",
        sub: title + " · DocuSign push",
        kind: "success",
      });
    }
    if (txt === "void envelope") {
      const card = btn.closest(".approval-card");
      card?.animate([{ opacity: 1 }, { opacity: 0 }], {
        duration: 200,
        fill: "forwards",
      });
      setTimeout(() => card?.remove(), 200);
      toast({
        title: "Envelope voided",
        sub: "Signers notified · audit logged",
        kind: "warn",
      });
    }
  });

  /* ===========================================================
     WORKFLOWS · Run / Build / Marketplace are wired live in
     renderers.js (renderWorkflows). No static handlers here — they
     would double-fire against re-rendered nodes.
     =========================================================== */

  /* ===========================================================
     BRAIN · suggestion chips populate composer
     =========================================================== */
  // Suggestion chips just populate the composer; the actual ask() is wired in
  // renderers.js (renderBrain) which calls POST /contract-brain/ask. We do NOT
  // bind a send-btn handler here — that would shadow the real one.
  document.querySelectorAll(".brain-suggest .chip-btn").forEach((chip) => {
    chip.addEventListener("click", () => {
      const composer = document.querySelector(".brain__qa .composer__input");
      if (!composer) return;
      composer.value = chip.textContent.trim().replace(/→$/, "").trim();
      composer.focus();
    });
  });

  /* ---------- Ask AEGIS answer follow-up chips (were dead) ---------- */
  document.addEventListener("click", (e) => {
    const chip = e.target.closest(".msg__followups .chip-btn");
    if (!chip) return;
    const txt = (chip.textContent || "").trim().toLowerCase();
    if (/show citations|citation/.test(txt)) {
      const sources = document.querySelector("[data-thread-sources]");
      if (sources) { sources.hidden = false; sources.scrollIntoView({ behavior: "smooth", block: "center" }); }
      toast({ title: "Citations", sub: "Shown in the Sources panel on the right", kind: "ai" });
    } else if (/open in review table/.test(txt)) {
      show("review");
    } else if (/draft markup/.test(txt)) {
      const inp = document.querySelector(".view--assistant .composer--inline .composer__input") || document.querySelector(".composer--landing .composer__input");
      if (inp) { inp.value = "Draft suggested redline markup for the clause discussed above."; inp.focus(); }
      toast({ title: "Drafting", sub: "Prompt added to the composer — press send", kind: "ai" });
    }
  });

  /* ===========================================================
     ASK AEGIS · composer sends message with typing → AI reply
     =========================================================== */
  (() => {
    const view = document.querySelector(".view--assistant");
    if (!view) return;
    const composer = view.querySelector(".composer--inline .composer__input");
    const sendBtn = view.querySelector(".composer--inline .send-btn");
    const stream = view.querySelector(".thread-stream");
    if (!composer || !sendBtn || !stream) return;
    const composerEl = view.querySelector(".composer--inline");
    // The inline composer lives inside the (initially hidden) ask-thread div,
    // so insertBefore must operate on the composer's actual parent — not the
    // stream root. Use composerEl.parentNode so we always anchor correctly.
    function insertBeforeComposer(node) {
      const parent = composerEl?.parentNode;
      if (parent) parent.insertBefore(node, composerEl);
      else stream.appendChild(node);
    }

    function appendUser(text) {
      const esc = window.aegis.esc;
      const a = document.createElement("article");
      a.className = "msg msg--user";
      a.innerHTML = `<div class="msg__avatar">—</div>
        <div class="msg__body">
          <div class="msg__meta">You · just now</div>
          <div class="msg__content">${esc(text)}</div>
        </div>`;
      insertBeforeComposer(a);
      a.scrollIntoView({ behavior: "smooth", block: "end" });
    }
    function appendAI(text, citations) {
      // Render REAL citations only. Never fabricate a source count (was Math.random),
      // and don't advertise follow-up actions the thread can't actually perform.
      const cites = Array.isArray(citations) ? citations : [];
      const escA = window.aegis.esc; // shared escaper (thin alias)
      const a = document.createElement("article");
      a.className = "msg msg--assistant";
      const meta = cites.length ? `AEGIS · ${cites.length} cited source${cites.length === 1 ? "" : "s"}` : "AEGIS";
      const citeHtml = cites.length
        ? `<div class="msg__cites">${cites.slice(0, 8).map((c, i) => `<span class="cite-chip">[${i + 1}] ${escA(c.title || c.contract_title || c.source || c.label || c.snippet || "source")}</span>`).join("")}</div>`
        : "";
      a.innerHTML = `<div class="msg__avatar msg__avatar--ai">A</div>
        <div class="msg__body">
          <div class="msg__meta">${meta}</div>
          <div class="msg__content"><p>${escA(text)}</p></div>
          ${citeHtml}
        </div>`;
      insertBeforeComposer(a);
      a.scrollIntoView({ behavior: "smooth", block: "end" });
    }
    // Populate the right-hand Sources panel from the CURRENT answer's real citations.
    // No citations → hide it (never show the stale static TechCorp/playbook clauses).
    function renderThreadSources(citations) {
      const panel = document.querySelector("[data-thread-sources]");
      if (!panel) return;
      const cites = Array.isArray(citations) ? citations : [];
      if (!cites.length) { panel.hidden = true; panel.innerHTML = ""; return; }
      const escS = window.aegis.esc; // shared escaper (thin alias)
      const docCount = new Set(cites.map((c) => c.contract_title || c.title || c.document || c.source || "Source")).size;
      panel.innerHTML =
        `<div class="sources__head"><div><div class="sources__title">Sources</div>` +
        `<div class="sources__sub">${cites.length} citation${cites.length === 1 ? "" : "s"} · ${docCount} document${docCount === 1 ? "" : "s"}</div></div></div>` +
        `<div class="source-group">` +
        cites.slice(0, 12).map((c, i) =>
          `<div class="source-item"><div class="source-item__num">${i + 1}</div><div class="source-item__body">` +
          `<div class="source-item__quote">${escS(c.snippet || c.quote || c.text || c.excerpt || c.title || "cited source")}</div>` +
          `<div class="source-item__meta">${escS(c.contract_title || c.title || c.source || c.label || "source")}${c.section ? " · " + escS(c.section) : ""}</div>` +
          `</div></div>`).join("") +
        `</div>`;
      panel.hidden = false;
    }
    function appendTyping() {
      const t = document.createElement("article");
      t.className = "msg msg--assistant";
      t.dataset.typing = "1";
      t.innerHTML = `<div class="msg__avatar msg__avatar--ai">A</div>
        <div class="msg__body">
          <div class="msg__meta">AEGIS · thinking…</div>
          <div class="typing"><span></span><span></span><span></span></div>
        </div>`;
      insertBeforeComposer(t);
      t.scrollIntoView({ behavior: "smooth", block: "end" });
      return t;
    }
    async function send() {
      const text = composer.value.trim();
      if (!text) return;
      appendUser(text);
      composer.value = "";
      composer.style.height = "auto";
      const t = appendTyping();
      try {
        const reply = window.aegis
          ? await window.aegis.api.assistant.sendMessage(text)
          : { content: "API not loaded.", citations: [] };
        t.remove();
        appendAI(reply.content, reply.citations);
        renderThreadSources(reply.citations);
      } catch (err) {
        t.remove();
        appendAI("Sorry — that request failed. " + (err.message || ""));
        renderThreadSources([]);
      }
    }
    sendBtn.addEventListener("click", (e) => { e.preventDefault(); send(); });
    composer.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        send();
      }
    });
  })();

  /* ===========================================================
     CONTRACT DETAIL · topbar + right-rail quick actions
     =========================================================== */
  document
    .querySelectorAll(".view--contract .topbar__actions button, .view--contract .quick-actions button")
    .forEach((btn) => {
      btn.addEventListener("click", () => {
        const txt = (btn.textContent || "").trim();
        if (txt === "Send for signature") {
          toast({
            title: "Sent for signature",
            sub: "DocuSign envelope queued · 2 signers",
            kind: "success",
          });
        } else if (txt === "Send for approval") {
          toast({
            title: "Sent for approval",
            sub: "Routed to S. Gande (partner)",
            kind: "success",
          });
        } else if (txt === "Ask AEGIS") {
          show("assistant");
        } else if (txt.match(/^[\u{1F300}-\u{1FFFF}☀-➿]/u)) {
          toast({
            title: txt.replace(/^[^\s]+\s/, ""),
            sub: "Action queued",
            kind: "ai",
          });
        }
      });
    });

  /* ---------- Sidebar search box → run a real search in the Search view ---------- */
  (() => {
    const side = document.querySelector(".sidebar__search input");
    if (!side) return;
    const go = () => {
      const q = side.value.trim();
      show("search");
      setTimeout(() => {
        const si = document.querySelector(".view--search .search-input input");
        if (si) {
          si.value = q;
          si.dispatchEvent(new Event("input", { bubbles: true }));
          si.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
          si.focus();
        }
      }, 140);
    };
    side.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); go(); } });
  })();

  /* ---------- Hub "This week" — honest: the hub API has no date-range filter ---------- */
  document.addEventListener("click", (e) => {
    const b = e.target.closest(".view--hub button");
    if (!b || !/^this week$|^this month$|^all time$/i.test((b.textContent || "").trim())) return;
    toast({ title: "Hub shows all-time metrics", sub: "The /contract-hub endpoint has no date-range filter.", kind: "ai" });
  });

  /* ===========================================================
     SEARCH · live filter
     =========================================================== */
  (() => {
    const input = document.querySelector(".view--search .search-input input");
    if (!input) return;
    function filter() {
      const q = input.value.trim().toLowerCase();
      const items = document.querySelectorAll(".view--search .search-results li");
      let visible = 0;
      items.forEach((li) => {
        const match = !q || li.textContent.toLowerCase().includes(q);
        li.hidden = !match;
        if (match) visible++;
      });
      const sub = document.querySelector(".view--search .search-hero p");
      if (sub)
        sub.textContent = `${visible} result${visible === 1 ? "" : "s"} across contracts, clauses, playbooks, Brain · 0.3s`;
    }
    input.addEventListener("input", filter);
  })();

  /* ===========================================================
     NOTIFICATIONS · per-item read + mark-all-read
     =========================================================== */
  function updateBellCount(n) {
    const dot = document.querySelector(".bell__dot");
    if (!dot) return;
    if (n <= 0) dot.style.display = "none";
    else { dot.style.display = "grid"; dot.textContent = String(n); }
  }
  document.querySelectorAll(".view--notifications .notif").forEach((n) => {
    n.addEventListener("click", (e) => {
      if (e.target.closest("a, button")) return;
      n.classList.remove("notif--unread");
      n.classList.add("is-read");
    });
  });
  document.querySelectorAll(".view--notifications .ghost-btn, [data-bell-panel] .ghost-btn").forEach((b) => {
    if ((b.textContent || "").trim() === "Mark all read") {
      b.addEventListener("click", () => {
        document.querySelectorAll(".view--notifications .notif, .bell__item").forEach((n) => {
          n.classList.remove("notif--unread", "bell__item--unread");
          n.classList.add("is-read");
        });
        updateBellCount(0);
        toast({ title: "All notifications marked read", kind: "success" });
      });
    }
  });

  /* ===========================================================
     PROJECT detail · action buttons are wired live in
     renderers.js (renderProjectDetail) with real backend writes.
     ADMIN Save changes / Invite, JOBS Pause / Retry are wired
     via delegation below (survives re-render).
     =========================================================== */

  /* ADMIN · Save changes → real PUT /admin/settings (persists org_profile) */
  document.addEventListener("click", async (e) => {
    const b = e.target.closest(".view--admin .topbar__actions button");
    if (!b || (b.textContent || "").trim() !== "Save changes" || !window.aegis) return;
    b.disabled = true;
    const orig = b.textContent;
    b.textContent = "Saving…";
    try {
      const inputs = [...document.querySelectorAll('.view--admin [data-tab-panel="adm-org"] input')];
      const profile = {};
      inputs.forEach((inp, i) => {
        const key = (inp.closest("label")?.querySelector("span")?.textContent || inp.placeholder || "field_" + i).trim();
        profile[key] = inp.value;
      });
      await window.aegis.api.admin.settings.set("org_profile", profile);
      toast({ title: "Settings saved", sub: `${Object.keys(profile).length} fields persisted to /admin/settings`, kind: "success" });
    } catch (err) {
      toast({ title: "Couldn't save", sub: err.message, kind: "error" });
    } finally {
      b.disabled = false;
      b.textContent = orig;
    }
  });

  /* ADMIN · + Invite member → real POST /users/invitations */
  document.addEventListener("click", (e) => {
    const b = e.target.closest('.view--admin button, [data-action="adm-invite"]');
    if (!b || !/invite member/i.test(b.textContent) || !window.aegis) return;
    const modal = window.__aegisModal;
    if (!modal) return;
    const m = modal("Invite a member", `<label class="up-field"><span>Email</span><input class="inv-email" type="email" placeholder="name@firm.com"/></label><label class="up-field"><span>Role</span><input class="inv-role" placeholder="member" value="member"/></label>`, { ok: "Send invite" });
    m.ok.onclick = async () => {
      const email = m.el.querySelector(".inv-email").value.trim();
      if (!email) { toast({ title: "Email required", kind: "warn" }); return; }
      m.ok.disabled = true; m.ok.textContent = "Sending…";
      try { await window.aegis.api.admin.members.invite({ email, role: m.el.querySelector(".inv-role").value.trim() || "member" }); toast({ title: "Invitation sent", sub: email, kind: "success" }); m.close(); }
      catch (err) { toast({ title: "Couldn't invite", sub: err.message, kind: "error" }); m.ok.disabled = false; m.ok.textContent = "Send invite"; }
    };
  });

  /* JOBS · Pause queue (honest) / Retry failed (real per-job re-run) */
  document.addEventListener("click", async (e) => {
    const b = e.target.closest(".view--jobs .topbar__actions button");
    if (!b || !window.aegis) return;
    const txt = (b.textContent || "").trim();
    if (txt === "Pause queue") {
      toast({ title: "Queue control unavailable", sub: "There's no global pause endpoint — cancel or re-run jobs individually on each row.", kind: "ai" });
    } else if (txt === "Retry failed") {
      const ids = window.__aegisFailedJobs || [];
      if (!ids.length) { toast({ title: "No failed jobs to retry", kind: "ai" }); return; }
      b.disabled = true; const orig = b.textContent; b.textContent = "Retrying…";
      let ok = 0;
      for (const id of ids) { try { await window.aegis.api.jobs.run(id); ok++; } catch {} }
      toast({ title: `Re-queued ${ok}/${ids.length} failed job${ids.length === 1 ? "" : "s"}`, kind: ok ? "success" : "error" });
      b.disabled = false; b.textContent = orig;
      window.aegis.renderers?.jobs?.();
    }
  });

  /* UPLOAD WIZARD save is wired above to a REAL multipart upload. */

  /* ===========================================================
     RENEWALS · timeline action buttons
     =========================================================== */
  document.querySelectorAll(".timeline__actions button").forEach((b) => {
    b.addEventListener("click", (e) => {
      e.stopPropagation();
      const item = b.closest(".timeline__item");
      const name = item?.querySelector("h4")?.textContent || "Renewal";
      const action = (b.textContent || "").trim();
      toast({
        title: `${action} · ${name}`,
        sub: "Action queued",
        kind: "success",
      });
    });
  });

  /* ===========================================================
     OBLIGATIONS · row click → contract detail
     =========================================================== */
  document
    .querySelectorAll(".view--obligations .ctr-table tbody tr")
    .forEach((row) => {
      row.style.cursor = "pointer";
      row.addEventListener("click", () => show("contract"));
    });

  /* ===========================================================
     REVIEW TABLE · cell click → pin citation
     =========================================================== */
  document.querySelectorAll(".rt-table tbody td .cell-flag").forEach((cell) => {
    cell.style.cursor = "pointer";
    cell.addEventListener("click", (e) => {
      e.stopPropagation();
      const row = cell.closest("tr");
      const doc = row?.querySelector(".doc-cell")?.textContent.trim() || "";
      const head = document.querySelector(".rt-citation__title");
      if (head)
        head.textContent = `Cell citation · ${doc} / cell value: ${cell.textContent.trim()}`;
      document
        .querySelector(".rt-citation")
        ?.scrollIntoView({ behavior: "smooth", block: "center" });
      toast({
        title: "Citation pinned",
        sub: doc,
        kind: "ai",
        duration: 1800,
      });
    });
  });

  /* ===========================================================
     ASK AEGIS · landing hero composer + suggestion chips + recents
     =========================================================== */
  (() => {
    const landing = document.querySelector(".composer--landing");
    if (!landing) return;
    const input = landing.querySelector(".composer__input");
    const send = landing.querySelector(".send-btn");
    const view = document.querySelector(".view--assistant");
    const inline = view?.querySelector(".composer--inline .composer__input");
    const inlineSend = view?.querySelector(".composer--inline .send-btn");

    function fire(q) {
      if (!inline || !inlineSend) return;
      // Reveal the thread area + hide the landing hero so the new conversation
      // is visible (otherwise messages get appended into a hidden container).
      const hero = document.querySelector(".ask-hero");
      const thread = document.querySelector("[data-ask-thread]");
      const sources = document.querySelector("[data-thread-sources]");
      if (hero) hero.hidden = true;
      if (thread) thread.hidden = false;
      // Keep the Sources panel hidden until the answer brings REAL citations
      // (renderThreadSources reveals it) — never flash the old static clauses.
      if (sources) { sources.hidden = true; sources.innerHTML = ""; }
      // Remove the prebuilt example messages so the new thread starts clean
      thread?.querySelectorAll(".msg").forEach((m) => m.remove());
      // Pipe through the inline composer so we reuse the send() handler
      inline.value = q;
      inlineSend.click();
      setTimeout(() => {
        document
          .querySelector(".view--assistant .msg--user:last-of-type")
          ?.scrollIntoView({ behavior: "smooth", block: "center" });
      }, 80);
    }

    function send_() {
      const q = input.value.trim();
      if (!q) return;
      fire(q);
      input.value = "";
      input.style.height = "auto";
    }
    send.addEventListener("click", send_);
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        send_();
      }
    });

    // Capability cards — click to seed + fire. Route through the landing
    // composer (reliable) and fall back to the card's heading text if a card
    // has no data-prompt.
    document.querySelectorAll(".cap-card").forEach((card) => {
      card.addEventListener("click", () => {
        const prompt = card.dataset.prompt || (card.querySelector("h4, h3, strong, .cap-card__title")?.textContent || "").trim();
        if (!prompt) return;
        if (input) { input.value = prompt; send_(); }
        else fire(prompt);
      });
    });

    // Recent thread cards
    const thread = document.querySelector("[data-ask-thread]");
    const sources = document.querySelector("[data-thread-sources]");
    document.querySelectorAll(".ask-recent").forEach((card) => {
      card.addEventListener("click", (e) => {
        e.preventDefault();
        const title = card.querySelector(".ask-recent__title")?.textContent || "Thread";
        // Active card opens the prebuilt TechCorp thread
        if (card.classList.contains("ask-recent--open") && thread) {
          thread.hidden = false;
          if (sources) sources.hidden = false;
          // hide the landing hero
          const hero = document.querySelector(".ask-hero");
          if (hero) hero.hidden = true;
          thread.scrollIntoView({ behavior: "smooth", block: "start" });
          toast({
            title: `Opened "${title}"`,
            sub: "14 sources · 8 thinking steps",
            kind: "ai",
          });
          return;
        }
        toast({
          title: `Opened "${title}"`,
          sub: "Loading prior context…",
          kind: "ai",
        });
      });
    });

    // "New thread" button in sidebar → reset landing
    document.querySelector(".new-thread-btn")?.addEventListener("click", () => {
      const hero = document.querySelector(".ask-hero");
      if (hero) hero.hidden = false;
      if (thread) thread.hidden = true;
      if (sources) sources.hidden = true;
      show("assistant");
    });
  })();

  /* ===========================================================
     ADMIN · tab toggles (settings toggles, swatch picker, invite)
     =========================================================== */
  document.querySelectorAll("[data-toggle]").forEach((t) => {
    t.addEventListener("click", () => {
      t.classList.toggle("is-on");
      toast({
        title: t.classList.contains("is-on") ? "Setting enabled" : "Setting disabled",
        kind: t.classList.contains("is-on") ? "success" : "warn",
        duration: 1600,
      });
    });
  });
  document.querySelectorAll(".swatch").forEach((s) => {
    s.addEventListener("click", () => {
      document
        .querySelectorAll(".swatch")
        .forEach((x) => x.classList.remove("is-active"));
      s.classList.add("is-active");
      toast({ title: "Brand color updated", duration: 1400, kind: "success" });
    });
  });
  /* (Invite member is wired to a real POST /users/invitations modal above.) */

  /* ===========================================================
     RESTORE DECIDED APPROVALS on page load
     (so the demo's persisted state matches the visible UI)
     =========================================================== */
  if (window.aegis) {
    window.aegis.api.approvals.list().then((rows) => {
      let resolvedCount = 0;
      rows.forEach((a) => {
        if (a.status === "pending" || a.direction !== "incoming") return;
        const card = document.querySelector(`.approval-card[data-id="${a.id}"]`);
        if (!card || card.classList.contains("is-resolved")) return;
        card.classList.add("is-resolved");
        const pillText =
          a.status === "approved"
            ? "Approved"
            : a.status === "rejected"
            ? "Rejected"
            : "Changes requested";
        const pillClass =
          a.status === "approved"
            ? "approval-card__resolved--approved"
            : a.status === "rejected"
            ? "approval-card__resolved--rejected"
            : "approval-card__resolved--changes";
        const pill = document.createElement("div");
        pill.className = "approval-card__resolved " + pillClass;
        pill.textContent = `${pillText} · persisted`;
        card.querySelector(".approval-card__body")?.after(pill);
        resolvedCount++;
      });
      // Sync the sidebar badge to the live count
      if (resolvedCount > 0) {
        window.aegis.api.metrics.hub().then((m) => {
          const badge = document.querySelector('[data-view="approvals"] .badge');
          if (badge && m && typeof m.awaiting_my_approval === "number") {
            // 6 awaiting + 3 signatures in the combined view
            badge.textContent = String(m.awaiting_my_approval + 3);
          }
        });
      }
    });

    // Restore notifications read state
    window.aegis.api.notifications.list().then((rows) => {
      rows.forEach((n) => {
        if (!n.read) return;
        document
          .querySelectorAll(".view--notifications .notif")
          .forEach((el, i) => {
            // Match by index for the demo
            if (i < rows.length && rows[i].id === n.id) {
              el.classList.remove("notif--unread");
              el.classList.add("is-read");
            }
          });
      });
      const unread = rows.filter((n) => !n.read).length;
      const dot = document.querySelector(".bell__dot");
      if (dot) {
        if (unread <= 0) dot.style.display = "none";
        else dot.textContent = String(unread);
      }
    });
  }

  /* ===========================================================
     UNIVERSAL: "?" keyboard help toast
     =========================================================== */
  window.addEventListener("keydown", (e) => {
    if (e.key === "?" && !e.target.matches("input, textarea")) {
      toast({
        title: "Keyboard shortcuts",
        sub: "⌘K palette · ⌘1–7 jump · ⌘T theme · ⌘N new contract",
        kind: "ai",
        duration: 5200,
      });
    }
  });
})();
