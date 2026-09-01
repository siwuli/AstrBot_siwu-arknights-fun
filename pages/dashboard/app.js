/* 兔兔方舟 · 管理后台前端（AstrBot Plugin Page） */
(function () {
  "use strict";

  // 桥接 SDK 由 AstrBot 注入（页面 head 已显式引用，或兜底追加到 body 末尾）。
  // 统一等待其就绪后再初始化，避免同步检查时 SDK 尚未来得及加载。
  function start() {
    var bridge = window.AstrBotPluginPage;
    if (!bridge) {
      return false;
    }

  function unwrap(r) {
    // 注意：WebUI 父页面（PluginPagePage.vue）已把 axios 响应解一层，
    // 传给 iframe 的 r 就是插件 handler 返回 json_response 中的 data 字段。
    // 因此这里直接返回 r，不再二次解包，否则 bootstrap 的 summary 会丢失。
    if (r && (r.ok === false || r.status === "error")) {
      throw new Error((r && r.message) || "请求失败");
    }
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
      body.innerHTML = "<tr><td colspan='7' class='empty'>暂无卡池数据（数据下载中）</td></tr>";
      return;
    }
    body.innerHTML = pools.map(function (p, i) {
      var c = p.counts || {};
      var ov = p.override || {};
      var up6 = ov.pickup_6 || p.pickup_6 || "—";
      var up5 = ov.pickup_5 || p.pickup_5 || "—";
      return "<tr><td>" + (i + 1) + "</td>" +
        "<td>" + escapeHtml(p.name) + "</td>" +
        "<td>6★" + (c["6"] || 0) + "・5★" + (c["5"] || 0) + "・4★" + (c["4"] || 0) + "・3★" + (c["3"] || 0) + "</td>" +
        "<td>" + escapeHtml(up6) + "</td>" +
        "<td>" + escapeHtml(up5) + "</td>" +
        "<td><button class='btn mini' data-manage='" + escapeHtml(p.id) + "'>管理</button></td></tr>";
    }).join("");
    body.querySelectorAll("button[data-manage]").forEach(function (btn) {
      btn.addEventListener("click", function () { openPoolManager(btn.dataset.manage); });
    });
  }

  function openPoolManager(poolId) {
    apiGet("page/pools/detail", { pool_id: poolId })
      .then(function (d) {
        $("#poolModalTitle").textContent = "卡池管理： " + d.name;
        $("#poolPickup6").value = (d.override && d.override.pickup_6) || d.pickup_6 || "";
        $("#poolPickup5").value = (d.override && d.override.pickup_5) || d.pickup_5 || "";
        var labels = { "6": "6★", "5": "5★", "4": "4★", "3": "3★" };
        var html = "";
        ["6", "5", "4", "3"].forEach(function (r) {
          var list = d.by_rarity[r] || [];
          html += "<details class='pool-list'><summary>" + labels[r] + "（" + list.length + " 名）</summary>" +
            "<div class='ops-tags'>" + list.map(escapeHtml).map(function (n) { return "<span class='op-tag'>" + n + "</span>"; }).join("") + "</div></details>";
        });
        $("#poolLists").innerHTML = html;
        $("#poolModal").hidden = false;
      })
      .catch(function (e) { toast(e.message, true); });
    $("#poolSave").onclick = function () {
      var up6 = $("#poolPickup6").value.trim();
      var up5 = $("#poolPickup5").value.trim();
      $("#poolModal").hidden = true;
      apiPost("page/pools/override", { pool_id: poolId, pickup_6: up6, pickup_5: up5 })
        .then(function () { toast("UP 已保存"); return loadAll(); })
        .catch(function (e) { toast(e.message, true); });
    };
    $("#poolCancel").onclick = function () { $("#poolModal").hidden = true; };
    $("#poolModal").onclick = function (e) {
      if (e.target === $("#poolModal")) $("#poolModal").hidden = true;
    };
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
    return true;
  }

  function waitBridge(tries) {
    if (start()) return;
    if (tries <= 0) {
      document.body.innerHTML = "<p style='padding:24px'>桥接不可用：请在 AstrBot 内置 WebUI 的插件页面中打开。</p>";
      return;
    }
    setTimeout(function () { waitBridge(tries - 1); }, 200);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { waitBridge(20); });
  } else {
    waitBridge(20);
  }
})();