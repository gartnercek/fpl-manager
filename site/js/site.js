(function () {
  var cfg = window.FPL_SITE || {};

  function $(sel, root) {
    return (root || document).querySelector(sel);
  }

  function extractTeamId(raw) {
    var s = String(raw || "").trim();
    var m = s.match(/entry\/(\d+)/i);
    if (m) return m[1];
    if (/^\d{1,10}$/.test(s)) return s;
    return "";
  }

  var support = $("#support-email");
  if (support && cfg.contactEmail) {
    support.href = "mailto:" + cfg.contactEmail;
    support.textContent = cfg.contactEmail;
    var line = $("#support-line");
    if (line) line.hidden = false;
  }

  var idInput = $("#team-id");
  if (idInput) {
    idInput.addEventListener("blur", function () {
      var id = extractTeamId(idInput.value);
      if (id) idInput.value = id;
    });
    idInput.addEventListener("paste", function () {
      setTimeout(function () {
        var id = extractTeamId(idInput.value);
        if (id) idInput.value = id;
      }, 0);
    });
  }

  var seasonAmt = $("#price-season");
  var monthAmt = $("#price-month");
  if (seasonAmt) {
    seasonAmt.innerHTML = "€" + cfg.season + " <span>/ " + (cfg.seasonName || "season") + "</span>";
  }
  if (monthAmt) {
    monthAmt.innerHTML = "€" + cfg.monthly + " <span>/ month</span>";
  }

  function setPlan(plan) {
    if (plan !== "monthly") plan = "season";
    var cards = document.querySelectorAll(".price-card[data-plan]");
    for (var i = 0; i < cards.length; i++) {
      var on = cards[i].getAttribute("data-plan") === plan;
      cards[i].classList.toggle("preferred", on);
      cards[i].setAttribute("aria-pressed", on ? "true" : "false");
    }
    var hidden = $("#plan-value");
    if (hidden) hidden.value = plan;
    var cta = $("#price-cta");
    if (cta) cta.setAttribute("href", "subscribe.html?plan=" + plan);
  }

  var planCards = document.querySelectorAll(".price-card[data-plan]");
  for (var c = 0; c < planCards.length; c++) {
    planCards[c].addEventListener("click", function () {
      setPlan(this.getAttribute("data-plan"));
    });
  }
  var qPlan = new URLSearchParams(location.search).get("plan");
  if (qPlan === "monthly" || qPlan === "season") setPlan(qPlan);

  var manage = $("#manage-billing");
  if (manage) {
    var sid = new URLSearchParams(location.search).get("session_id");
    if (sid && sid.indexOf("cs_") === 0) {
      manage.hidden = false;
      manage.addEventListener("click", function (ev) {
        ev.preventDefault();
        manage.setAttribute("aria-busy", "true");
        fetch("/api/portal", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ session_id: sid }),
        })
          .then(function (r) {
            return r.json();
          })
          .then(function (data) {
            if (data && data.url) {
              location.href = data.url;
              return;
            }
            manage.removeAttribute("aria-busy");
            alert((data && data.error) || "Could not open billing.");
          })
          .catch(function () {
            manage.removeAttribute("aria-busy");
            alert("Could not open billing.");
          });
      });
    }
  }

  var form = $("#subscribe-form");
  if (!form) return;

  var status = $("#form-status");
  var submitBtn = form.querySelector('button[type="submit"]');
  var checkoutPath = cfg.checkoutPath || "/api/checkout";
  var betaPath = cfg.betaPath || "/api/beta";
  var paymentsOpen = false;

  if (new URLSearchParams(location.search).get("cancelled") === "1" && status) {
    status.className = "alert show";
    status.textContent = "Checkout closed without a payment. Nothing was charged.";
  }

  fetch(betaPath, { method: "GET" })
    .then(function (r) {
      return r.ok ? r.json() : null;
    })
    .then(function (info) {
      if (info && info.payments_open) {
        paymentsOpen = true;
        if (submitBtn) submitBtn.textContent = "Continue to payment";
      } else if (submitBtn) {
        submitBtn.textContent = "Join the free beta";
      }
    })
    .catch(function () {});

  function paymentUrl(plan, email, teamId) {
    var base = plan === "monthly" ? cfg.paymentLinkMonthly : cfg.paymentLinkSeason;
    if (!base) return "";
    try {
      var url = new URL(base);
      url.searchParams.set("prefilled_email", email);
      url.searchParams.set("client_reference_id", teamId);
      return url.toString();
    } catch (err) {
      return "";
    }
  }

  function fail(msg) {
    status.className = "alert show";
    status.textContent = msg;
    if (submitBtn) submitBtn.disabled = false;
  }

  function showBetaOk(updated) {
    status.className = "alert show ok";
    status.textContent = updated
      ? "Updated. Same email, new team ID. The next deadline letter uses that squad."
      : "You’re on the free beta. No card was taken. The weekly letter uses this email and team ID. We will write before paid plans open.";
    if (submitBtn) {
      submitBtn.disabled = false;
      submitBtn.textContent = "Joined";
    }
  }

  form.addEventListener("submit", function (ev) {
    var id = extractTeamId(idInput && idInput.value);
    var email = ($("#email") && $("#email").value) || "";
    var consent = $("#consent");
    var unofficial = $("#unofficial");
    var hp = $("#company");
    var errs = [];
    if (!id) errs.push("Need a numeric FPL team ID (or paste the entry URL).");
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) errs.push("Need a working email.");
    if (consent && !consent.checked) errs.push("Tick the privacy box.");
    if (unofficial && !unofficial.checked) errs.push("Confirm you know this is unofficial advice.");
    if (errs.length) {
      ev.preventDefault();
      fail(errs.join(" "));
      return;
    }
    if (idInput) idInput.value = id;

    var plan = ($("#plan-value") && $("#plan-value").value) || "season";
    var name = ($("#name") && $("#name").value) || "";

    ev.preventDefault();
    if (submitBtn) submitBtn.disabled = true;
    status.className = "alert";
    status.textContent = "";

    var payload = {
      email: email,
      team_id: id,
      plan: plan,
      name: name,
      consent: true,
    };
    if (hp && hp.value) payload._gotcha = hp.value;

    if (!paymentsOpen) {
      fetch(betaPath, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(payload),
      })
        .then(function (r) {
          var ct = r.headers.get("content-type") || "";
          if (ct.indexOf("json") === -1) {
            return { ok: false, status: r.status, data: {} };
          }
          return r.json().then(function (data) {
            return { ok: r.ok, status: r.status, data: data };
          });
        })
        .then(function (res) {
          if (res.ok && res.data && res.data.ok) {
            showBetaOk(res.data.updated);
            return;
          }
          fail((res.data && res.data.error) || "Could not join the beta. Try again in a moment.");
        })
        .catch(function () {
          fail("Could not reach the beta list. Try again in a moment.");
        });
      return;
    }

    fetch(checkoutPath, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
    })
      .then(function (r) {
        var ct = r.headers.get("content-type") || "";
        if (ct.indexOf("json") === -1) {
          return { ok: false, status: r.status, data: {} };
        }
        return r.json().then(function (data) {
          return { ok: r.ok, status: r.status, data: data };
        });
      })
      .then(function (res) {
        if (res.data && res.data.url) {
          location.href = res.data.url;
          return;
        }
        var link = paymentUrl(plan, email, id);
        if (link) {
          location.href = link;
          return;
        }
        fail(
          (res.data && res.data.error) ||
            "Checkout is not live yet. Join the free beta, or set Stripe when payments open."
        );
      })
      .catch(function () {
        fail("Could not reach checkout. Try again in a moment.");
      });
  });
})();
