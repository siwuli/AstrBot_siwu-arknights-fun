/* 兔兔方舟 · 管理后台前端（AstrBot Plugin Page） */
(function () {
  "use strict";

  var bridge = window.AstrBotPluginPage;
  if (!bridge) {
    document.body.innerHTML = "<p style='padding:24px'>桥接不可用：请在 AstrBot 内置 WebUI 的插件页面中打开。</p>";
    return;
  }

  function unwrap(r) {
    if (r && (r.ok === false || r.status === "error")) {
      throw new Error((r && r.message) || "请求失败");
    }
    if (r && typeof r === "object" && "data" in r) return r.data;
    return r;
  }

  function apiGet(endpoint, params) {
    return bridge.apiGet(endpoint, params).then(unwrap);
  }
  function apiPost(endpoint, body) {
    return bridge.apiPost(endpoint, body || {}).then(unwrap);
  }

  function $(sel) { return document.querySelector(sel); }

  var titles = { overview: "概览", users: "用户管理", data: "数据维护", pools: "卡池" };
  var toastTimer = null;
  function toast(msg, isErr) {
    var t = $("#toast");
    t.textContent = msg;
    t.className = "toast" + (isErr ? " error" : "");
    t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { t.hidden = true; }, 3000);
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function statCard(label, value, icon) {
    return "<div class='stat-card'><div class='stat-icon'>" + icon + "</div>" +
      "<div><div class='stat-value'>" + value + "</div><div class='stat-label'>" + label + "</div></div></div>";
  }

  function renderSummary(s) {
    $("#statGrid").innerHTML =
      statCard("注册用户", s.users, "👥") +
      statCard("累计抽数", s.total_pulls, "🎰") +
      statCard("发放凭证", s.total_coupon, "🎟️") +
      statCard("持有合成玉", s.total_jade, "💎");
  }

  function renderDataStatus(d) {
    var rows = [
      ["gamedata excel", d.excel_ready ? "✅ 已就绪（" + d.excel_files + " 个文件）" : "⏳ 未就绪（后台下载中/内置兜底）"],
      ["上次更新", d.last_update || "未知"],
      ["干员数据", d.operators + " 名"],
      ["卡池", d.pools + " 个"],
      ["语音台词", d.voices + " 条"],
    ];
    $("#dataStatus").innerHTML = rows.map(function (r) {
      return "<div class='kv'><span class='kv-label'>" + escapeHtml(r[0]) + "</span><span class='kv-value'>" + escapeHtml(r[1]) + "</span></div>";
    }).join("");
  }

  function adjustUser(uid) {
    $("#adjustUid").textContent = uid;
    $("#adjustCoupon").value = "0";
    $("#adjustJade").value = "0";
    $("#adjustModal").hidden = false;
    $("#adjustConfirm").onclick = function () {
      var coupon = parseInt($("#adjustCoupon").value, 10) || 0;
      var jade = parseInt($("#adjustJade").value, 10) || 0;
      $("#adjustModal").hidden = true;
      apiPost("page/users/adjust", { uid: uid, coupon_delta: coupon, jade_delta: jade })
        .then(function () { toast("已更新 " + uid); return loadAll(); })
        .catch(function (e) { toast(e.message, true); });
    };
    $("#adjustCancel").onclick = function () { $("#adjustModal").hidden = true; };
  }

  function renderUsers(users) {
    var body = $("#usersBody");
    if (!users.length) {
      body.innerHTML = "<tr><td colspan='9' class='empty'>暂无用户</td></tr>";
      return;
    }
    body.innerHTML = users.map(function (u) {
      return "<tr>" +
        "<td class='mono'>" + escapeHtml(u.uid) + "</td>" +
        "<td>" + u.coupon + "</td>" +
        "<td>" + u.jade + "</td>" +
        "<td>" + u.total + "</td>" +
        "<td>" + u.break_even + "</td>" +
        "<td>" + u.box + "</td>" +
        "<td>" + u.sign_days + "</td>" +
        "<td>" + u.guess_score + "</td>" +
        "<td class='actions'>" +
        "<button class='btn mini' data-act='adjust' data-uid='" + escapeHtml(u.uid) + "'>调整</button>" +
        "<button class='btn mini' data-act='pity' data-uid='" + escapeHtml(u.uid) + "'>重置保底</button>" +
        "<button class='btn mini danger' data-act='del' data-uid='" + escapeHtml(u.uid) + "'>删除</button>" +
        "</td></tr>";
    }).join("");
    body.querySelectorAll("button[data-act]").forEach(function (btn) {
      var uid = btn.dataset.uid;
      var act = btn.dataset.act;
      btn.addEventListener("click", function () {
        if (act === "adjust") { adjustUser(uid); return; }
        if (act === "pity") {
          if (!confirm("确认重置用户 " + uid + " 的保底水位？")) return;
          apiPost("page/users/reset-pity", { uid: uid })
            .then(function () { toast("已重置 " + uid); return loadAll(); })
            .catch(function (e) { toast(e.message, true); });
          return;
        }
        if (act === "del") {
          if (!confirm("确认删除用户 " + uid + "？该操作不可恢复。")) return;
          apiPost("page/users/delete", { uid: uid })
            .then(function () { toast("已删除 " + uid); return loadAll(); })
            .catch(function (e) { toast(e.message, true); });
        }
      });
    });
  }

  function renderPools(pools) {
    var body = $("#poolsBody");
    if (!pools.length) {
      body.innerHTML = "<tr><td colspan='5' class='empty'>暂无卡池数据（数据下载中）</td></tr>";
      return;
    }
    body.innerHTML = pools.map(function (p, i) {
      return "<tr><td>" + (i + 1) + "</td><td>" + escapeHtml(p.name) + "</td>" +
        "<td class='mono'>" + escapeHtml(p.id) + "</td>" +
        "<td>" + escapeHtml(p.pickup_6 || "—") + "</td>" +
        "<td>" + escapeHtml(p.pickup_5 || "—") + "</td></tr>";
    }).join("");
  }

  function triggerUpdate() {
    var btn1 = $("#updateDataBtn"), btn2 = $("#updateDataBtn2");
    var r1 = $("#updateResult"), r2 = $("#updateResult2");
    [btn1, btn2].forEach(function (b) { if (b) { b.disabled = true; b.textContent = "更新中…"; } });
    apiPost("page/tools/update", {})
      .then(function (d) {
        if (r1) { r1.textContent = d.message || "完成"; }
        if (r2) { r2.textContent = d.message || "完成"; }
        toast(d.message || "更新完成");
        return loadAll();
      })
      .catch(function (e) {
        toast(e.message, true);
        if (r1) { r1.textContent = e.message; }
        if (r2) { r2.textContent = e.message; }
      })
      .finally(function () {
        [btn1, btn2].forEach(function (b) { if (b) { b.disabled = false; b.textContent = "⚡ 立即更新数据"; } });
      });
  }

  function switchTab(tab) {
    document.querySelectorAll(".nav-item").forEach(function (n) {
      n.classList.toggle("active", n.dataset.tab === tab);
    });
    document.querySelectorAll(".tab-page").forEach(function (p) { p.hidden = true; });
    var page = $("#tab-" + tab);
    if (page) page.hidden = false;
    $("#pageTitle").textContent = titles[tab] || tab;
  }

  function loadAll() {
    return Promise.all([
      apiGet("page/bootstrap"),
      apiGet("page/users"),
      apiGet("page/pools"),
    ]).then(function (res) {
      renderSummary(res[0].summary);
      renderDataStatus(res[0].data);
      renderUsers(res[1]);
      renderPools(res[2]);
    }).catch(function (e) { toast(e.message, true); });
  }

  document.querySelectorAll(".nav-item").forEach(function (n) {
    n.addEventListener("click", function () { switchTab(n.dataset.tab); });
  });
  $("#refreshBtn").addEventListener("click", loadAll);
  $("#updateDataBtn").addEventListener("click", triggerUpdate);
  $("#updateDataBtn2").addEventListener("click", triggerUpdate);
  $("#adjustModal").addEventListener("click", function (e) {
    if (e.target === $("#adjustModal")) $("#adjustModal").hidden = true;
  });

  switchTab("overview");
  loadAll();
})();
