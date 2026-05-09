/**
 * RL dashboard — auto-train status, poll while server training runs, circuit reset.
 */
(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", function () {
    var trainingNotice = document.getElementById("rlTrainingNotice");
    var trainWaitActive = false;

    function fetchRlStatus() {
      return fetch("/api/rl/status", {
        headers: { Accept: "application/json" },
        cache: "no-store",
        credentials: "same-origin",
      }).then(function (r) {
        if (!r.ok) {
          return { _status_error: true, httpStatus: r.status };
        }
        return r.json();
      });
    }

    function reloadFresh() {
      try {
        var u = new URL(window.location.href);
        u.searchParams.set("_mmrl", String(Date.now()));
        window.location.replace(u.toString());
      } catch (_e) {
        window.location.reload();
      }
    }

    function setTrainingNotice(text, isOk) {
      if (!trainingNotice) return;
      trainingNotice.style.display = "block";
      trainingNotice.textContent = text;
      trainingNotice.classList.remove("ok", "rl-msg--error");
      if (isOk) trainingNotice.classList.add("ok");
      else if (text) trainingNotice.classList.add("rl-msg--error");
    }

    function hideTrainingNotice() {
      if (!trainingNotice) return;
      trainingNotice.style.display = "none";
      trainingNotice.textContent = "";
      trainingNotice.classList.remove("ok", "rl-msg--error");
    }

    function waitForTrainingThenReload() {
      if (trainWaitActive) return;
      trainWaitActive = true;
      var deadline = Date.now() + 45 * 60 * 1000;
      var intervalMs = 1500;
      var statusErrors = 0;
      setTrainingNotice(
        "Training in progress… This tab will reload when the run finishes.",
        true
      );

      function pollOnce() {
        return fetchRlStatus().then(function (d) {
          if (d && d._status_error) {
            statusErrors += 1;
            if (statusErrors >= 6) {
              setTrainingNotice(
                "Could not read training status (HTTP " +
                  d.httpStatus +
                  "). Refresh the page — if you are logged out, sign in again.",
                false
              );
              trainWaitActive = false;
              return true;
            }
            return false;
          }
          statusErrors = 0;
          if (d && !d.training_active) {
            if (d.last_run && d.last_run.ok === false && d.last_run.error) {
              setTrainingNotice(
                "Run finished with an error (see banner after reload). Loading…",
                false
              );
            } else {
              setTrainingNotice("Finished. Loading latest…", true);
            }
            setTimeout(reloadFresh, 1600);
            return true;
          }
          return false;
        });
      }

      function loop() {
        if (Date.now() >= deadline) {
          setTrainingNotice(
            "Still running — leave this tab open or open RL again later.",
            false
          );
          trainWaitActive = false;
          return;
        }
        pollOnce().then(function (done) {
          if (done) return;
          setTimeout(loop, intervalMs);
        });
      }

      setTimeout(function () {
        pollOnce().then(function (done) {
          if (!done) loop();
        });
      }, 400);
    }

    function applyAutoTrainStatus(d) {
      if (!d || !d.auto_train) return;
      var a = d.auto_train;
      var badge = document.getElementById("rlAutoBadge");
      var intervalLine = document.getElementById("rlAutoIntervalLine");
      var lastEl = document.getElementById("rlAutoLastRun");
      var nextEl = document.getElementById("rlAutoNextRun");
      if (badge) {
        badge.className = "rl-auto-badge " + (a.enabled ? "on" : "off");
        badge.innerHTML = a.enabled
          ? '<i class="fas fa-circle" style="font-size:.45rem;"></i> On'
          : "Off";
      }
      if (intervalLine) {
        if (a.enabled) {
          var label =
            a.interval_label ||
            (a.interval_minutes ? a.interval_minutes + " minutes" : "24 hours");
          intervalLine.innerHTML = "Every <span>" + label + "</span>";
        } else {
          intervalLine.innerHTML = "<span>Auto-training disabled</span>";
        }
      }
      if (lastEl) lastEl.textContent = a.enabled ? a.last_run_display || "—" : "—";
      if (nextEl) nextEl.textContent = a.enabled ? a.next_due_display || "—" : "—";
    }

    fetchRlStatus().then(function (d) {
      if (!d || d._status_error) return;
      applyAutoTrainStatus(d);
      if (d.training_active) {
        waitForTrainingThenReload();
      } else {
        hideTrainingNotice();
      }
    });

    var circuitBtn = document.getElementById("rlCircuitReset");
    var circuitMsg = document.getElementById("rlCircuitMsg");
    if (circuitBtn && circuitMsg) {
      circuitBtn.addEventListener("click", function () {
        circuitBtn.disabled = true;
        circuitMsg.textContent = "";
        fetch("/api/rl/circuit/reset", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: "{}",
          cache: "no-store",
          credentials: "same-origin",
        })
          .then(function (res) {
            return res.json().then(function (data) {
              return { res: res, data: data };
            });
          })
          .then(function (_ref) {
            var res = _ref.res;
            var data = _ref.data;
            if (res.ok) {
              circuitMsg.textContent = "Pause cleared. Updating…";
              circuitMsg.classList.add("ok");
              setTimeout(reloadFresh, 600);
            } else {
              circuitMsg.textContent = (data && data.error) || "Something went wrong.";
            }
          })
          .catch(function () {
            circuitMsg.textContent = "Could not reach the server.";
          })
          .finally(function () {
            circuitBtn.disabled = false;
          });
      });
    }

    window.addEventListener("pageshow", function (ev) {
      if (ev.persisted) {
        try {
          var u = new URL(window.location.href);
          u.searchParams.set("_mmrlbf", String(Date.now()));
          window.location.replace(u.toString());
        } catch (_e2) {
          window.location.reload();
        }
      }
    });

    function escHtml(str) {
      return String(str || "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
    }

    var grid = document.getElementById("rlSignalsGrid");
    var loadingEl = document.getElementById("rlSignalsLoading");
    var errEl = document.getElementById("rlSignalsError");
    var emptyEl = document.getElementById("rlSignalsEmpty");
    if (grid && loadingEl) {
      fetch("/api/rl/dashboard_signals", {
        credentials: "same-origin",
        cache: "no-store",
        headers: { Accept: "application/json" },
      })
        .then(function (r) {
          return r.json().then(function (j) {
            return { ok: r.ok, status: r.status, body: j };
          });
        })
        .then(function (pack) {
          loadingEl.style.display = "none";
          var j = pack.body || {};
          if (!pack.ok || !j.success) {
            if (errEl) {
              errEl.style.display = "block";
              errEl.textContent =
                (j && j.error) ||
                "Could not load RL signals (HTTP " + pack.status + ").";
            }
            return;
          }
          var sigs = j.signals || [];
          if (!sigs.length) {
            if (emptyEl) emptyEl.style.display = "block";
            return;
          }
          grid.style.display = "grid";
          grid.innerHTML = sigs
            .map(function (s) {
              var act = String(s.action || "HOLD");
              var isBuy = act === "BUY";
              var isHold = act === "HOLD";
              var sigCls = isBuy
                ? "buy-color"
                : act === "SELL"
                  ? "sell-color"
                  : "neutral-color";
              var cardNeutral = isHold ? " neutral" : "";
              var conf = Math.round(Number(s.confidence) || 55);
              var fillCls = isBuy
                ? "positive"
                : act === "SELL"
                  ? "negative"
                  : "neutral";
              var price =
                s.price != null && s.price !== ""
                  ? String(s.price)
                  : "—";
              var sym = escHtml(s.symbol || "—");
              var nm = escHtml(s.name || s.symbol || "—");
              var cat = escHtml(s.category || "");
              var actEsc = escHtml(act);
              var priceEsc = escHtml(price);
              return (
                '<div class="signal-card stocks' +
                cardNeutral +
                '">' +
                '<div class="card-header">' +
                '<span class="signal-type ' +
                sigCls +
                '">' +
                actEsc +
                "</span>" +
                '<span class="asset-category">' +
                cat +
                "</span>" +
                "</div>" +
                '<div class="asset-info"><h3>' +
                sym +
                "</h3><p>" +
                nm +
                "</p></div>" +
                '<div class="price-score" style="grid-template-columns: 1fr 1fr;">' +
                '<div class="metric"><span class="value">' +
                priceEsc +
                '</span><span class="label">Price</span></div>' +
                '<div class="metric"><span class="value neutral-color">' +
                conf +
                '%</span><span class="label">Confidence</span></div>' +
                "</div>" +
                '<div class="confidence-meter-small">' +
                '<div class="confidence-fill ' +
                fillCls +
                '" style="width:' +
                conf +
                '%;"></div>' +
                '<span class="confidence-value">' +
                conf +
                "%</span></div></div>"
              );
            })
            .join("");
        })
        .catch(function () {
          loadingEl.style.display = "none";
          if (errEl) {
            errEl.style.display = "block";
            errEl.textContent = "Network error loading RL signals.";
          }
        });
    }
  });
})();
