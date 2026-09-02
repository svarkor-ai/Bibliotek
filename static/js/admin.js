/* Admin dashboard controller (MC 743.7).
 *
 * Replaces the inline <script> block in templates/admin.html. The page
 * itself only keeps two small helpers (toggleMenu, a guard that redirects
 * non-admins); everything else — stats, users, HCF, logout — lives here.
 *
 * CSRF: mutating calls (PUT/DELETE on /api/admin/users) send the page's
 * <meta name="csrf-token"> value in the X-CSRF-Token header, which
 * src.csrf.verify_csrf double-submits against the "csrf" cookie.
 */

(function () {
  "use strict";

  function csrfToken() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute("content") || "" : (window.CSRF_TOKEN || "");
  }

  async function adminApi(path, options) {
    var opts = options || {};
    var headers = opts.headers || {};
    if (opts.method === "PUT" || opts.method === "DELETE") {
      headers["X-CSRF-Token"] = csrfToken();
    }
    var resp = await fetch(path, {
      method: opts.method || "GET",
      headers: headers,
      body: opts.body,
      credentials: "same-origin",
    });
    var data = null;
    try {
      data = await resp.json();
    } catch (err) {
      data = null;
    }
    if (!resp.ok) {
      var msg = (data && (data.detail || data.error)) || resp.statusText;
      throw new Error(msg);
    }
    return data;
  }

  function escapeHtml(str) {
    if (!str) return "";
    var div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
  }

  function setStat(id, value) {
    var el = document.getElementById(id);
    if (el) el.textContent = value;
  }

  async function loadStats() {
    try {
      var books = await fetch("/api/books?limit=1", { credentials: "same-origin" }).then(function (r) { return r.json(); });
      setStat("statBooks", books.total || 0);
    } catch (err) {
      setStat("statBooks", "—");
    }
    try {
      var users = await adminApi("/api/users");
      setStat("statUsers", users.length);
    } catch (err) {
      setStat("statUsers", "—");
    }
    try {
      var loans = await fetch("/api/loans/active", { credentials: "same-origin" }).then(function (r) { return r.json(); });
      setStat("statActiveLoans", Array.isArray(loans) ? loans.length : 0);
    } catch (err) {
      setStat("statActiveLoans", "—");
    }
    try {
      var overdue = await fetch("/api/loans/overdue", { credentials: "same-origin" }).then(function (r) { return r.json(); });
      setStat("statOverdue", Array.isArray(overdue) ? overdue.length : 0);
    } catch (err) {
      setStat("statOverdue", "—");
    }
  }

  async function loadUsers() {
    var tbody = document.getElementById("userTableBody");
    var loading = document.getElementById("userLoading");
    var empty = document.getElementById("userEmpty");
    if (!tbody) return;

    loading.style.display = "block";
    tbody.innerHTML = "";
    empty.style.display = "none";

    try {
      var users = await adminApi("/api/users");
      loading.style.display = "none";
      if (!users.length) {
        empty.style.display = "block";
        return;
      }
      users.forEach(function (u) {
        var tr = document.createElement("tr");
        tr.dataset.userId = u.id;
        tr.innerHTML =
          "<td>" + escapeHtml(u.username) + "</td>" +
          "<td>" + escapeHtml(u.email || "—") + "</td>" +
          '<td><span class="badge badge-available">' + escapeHtml(u.role || "user") + "</span></td>" +
          "<td>" + (u.created_at ? new Date(u.created_at).toLocaleDateString() : "—") + "</td>" +
          "<td>" +
          '<button class="act-toggle" data-act="toggle" data-userid="' + u.id + '" title="Aktivera/deaktivera">' + (u.active ? "✅" : "❌") + "</button> " +
          '<button class="act-delete" data-act="delete" data-userid="' + u.id + '" title="Ta bort">🗑</button>' +
          "</td>";
        tbody.appendChild(tr);
      });
    } catch (err) {
      /* /api/users failed — degrade to the known session user */
      loading.style.display = "none";
      var tr = document.createElement("tr");
      tr.innerHTML =
        "<td>" + escapeHtml((APP && APP.user && APP.user.username) || "") + "</td>" +
        "<td>" + escapeHtml((APP && APP.user && APP.user.email) || "—") + "</td>" +
        '<td><span class="badge badge-available">' + escapeHtml((APP && APP.user && APP.user.role) || "") + "</span></td>" +
        "<td>—</td><td></td>";
      tbody.appendChild(tr);
    }
  }

  async function toggleUser(id, btn) {
    try {
      var u = await adminApi("/api/users/" + id);
      var next = !u.active;
      await adminApi("/api/users/" + id, { method: "PUT", body: JSON.stringify({ active: next }) });
      btn.textContent = next ? "✅" : "❌";
      if (window.toast) window.toast("Användaren är " + (next ? "aktiverad" : "deaktiverad"), "success");
    } catch (err) {
      if (window.toast) window.toast("Kunde inte ändra användare: " + err.message, "error");
    }
  }

  async function deleteUser(id, btn) {
    if (!confirm("Ta bort användaren? Detta kan inte ångras.")) return;
    try {
      await adminApi("/api/users/" + id, { method: "DELETE" });
      var row = btn.closest("tr");
      if (row) row.remove();
      if (window.toast) window.toast("Användaren togs bort", "success");
    } catch (err) {
      if (window.toast) window.toast("Kunde inte ta bort användaren: " + err.message, "error");
    }
  }

  function bindUserActions() {
    var tbody = document.getElementById("userTableBody");
    if (!tbody || tbody.dataset.bound) return;
    tbody.dataset.bound = "1";
    tbody.addEventListener("click", function (ev) {
      var btn = ev.target.closest("button[data-act]");
      if (!btn) return;
      var id = btn.dataset.userid;
      if (btn.dataset.act === "toggle") toggleUser(id, btn);
      else if (btn.dataset.act === "delete") deleteUser(id, btn);
    });
  }

  function logout() {
    clearAuth();
    window.location.href = "/";
  }

  /* ---- init ---- */
  loadStats();
  loadUsers();
  bindUserActions();

  /* Expose for the small inline page scripts (admin.html keeps toggleMenu
   * and the admin guard inline so they run before this file). */
  window.AdminDashboard = {
    logout: logout,
    loadUsers: loadUsers,
    loadStats: loadStats,
    csrfToken: csrfToken,
  };
})();
