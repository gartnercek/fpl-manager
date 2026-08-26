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
  var seasonRadio = $("#plan-season-label");
  var monthRadio = $("#plan-month-label");
  if (seasonRadio) {
    seasonRadio.textContent =
      "Season " + (cfg.seasonName || "") + " — €" + cfg.season;
  }
  if (monthRadio) {
    monthRadio.textContent = "Monthly — €" + cfg.monthly;
  }

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

  if (new URLSearchParams(location.search).get("cancelled") === "1" && status) {
    status.className = "alert show";
    status.textContent = "Checkout closed without a payment. Nothing was charged.";
  }

  fetch(checkoutPath, { method: "GET" })
    .then(function (r) {
      return r.ok ? r.json() : null;
    })
    .then(function (info) {
      if (info && info.configured && submitBtn) {
        submitBtn.textContent = "Continue to payment";
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

  function mailtoFallback(email, teamId, plan, name) {
    if (!cfg.contactEmail) return "";
    var body =
      "Name: " +
      name +
      "\nEmail: " +
      email +
      "\nFPL team ID: " +
      teamId +
      "\nPlan: " +
      plan +
      "\n";
    return (
      "mailto:" +
      cfg.contactEmail +
      "?subject=" +
      encodeURIComponent("FPL Manager subscribe " + teamId) +
      "&body=" +
      encodeURIComponent(body)
    );
  }

  function fail(msg) {
    status.className = "alert show";
    status.textContent = msg;
    if (submitBtn) submitBtn.disabled = false;
  }

  form.addEventListener("submit", function (ev) {
    var id = extractTeamId(idInput && idInput.value);
    var email = ($("#email") && $("#email").value) || "";
    var consent = $("#consent");
    var unofficial = $("#unofficial");
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

    var plan = ($("input[name='plan']:checked") || {}).value || "season";
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
        if (cfg.formspreeId) {
          form.setAttribute("action", "https://formspree.io/f/" + cfg.formspreeId);
          form.setAttribute("method", "POST");
          form.submit();
          return;
        }
        var mail = mailtoFallback(email, id, plan, name);
        if (mail) {
          status.className = "alert show ok";
          status.innerHTML =
            "Card checkout is not live on this host. Send the details instead: <a href=\"" +
            mail +
            "\">open an email</a>.";
          window.location.href = mail;
          return;
        }
        fail(
          (res.data && res.data.error) ||
            "Checkout is not live yet. Set Stripe on Cloudflare Pages, or a Payment Link in js/config.js."
        );
      })
      .catch(function () {
        var link = paymentUrl(plan, email, id);
        if (link) {
          location.href = link;
          return;
        }
        var mail = mailtoFallback(email, id, plan, name);
        if (mail) {
          window.location.href = mail;
          return;
        }
        fail("Could not reach checkout. Try again in a moment.");
      });
  });
})();
