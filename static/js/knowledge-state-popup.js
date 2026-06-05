(function () {
  "use strict";

  var ANALYZE_ENDPOINT = "/api/tracking/knowledge-state/analyze";
  var MODAL_ID = "knowledge-state-modal";
  var STYLE_ID = "knowledge-state-modal-style";

  var STATE_LABELS = {
    true_mastery: "True Mastery",
    illusion_of_competence: "Illusion of Competence",
    lucky_underconfident_correct: "Lucky / Underconfident Correct",
    conscious_weakness: "Conscious Weakness",
    stubborn_error: "Stubborn Error",
    exam_transfer_failure: "Exam Transfer Failure",
    "True Mastery": "True Mastery",
    "Illusion of Competence": "Illusion of Competence",
    "Lucky / Underconfident Correct": "Lucky / Underconfident Correct",
    "Conscious Weakness": "Conscious Weakness",
    "Stubborn Error": "Stubborn Error",
    "Exam Transfer Failure": "Exam Transfer Failure"
  };

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function asArray(value) {
    if (Array.isArray(value)) {
      return value;
    }
    if (value == null || value === "") {
      return [];
    }
    return [value];
  }

  function stateLabel(value) {
    if (value && typeof value === "object") {
      return stateLabel(value.label || value.name || value.state || value.code || "");
    }
    return STATE_LABELS[value] || value || "Not assessed";
  }

  function confidenceLabel(value) {
    if (value == null || value === "") {
      return "Confidence unavailable";
    }
    if (typeof value === "number") {
      return Math.round(value * 100) + "% confidence";
    }
    return String(value);
  }

  function ensureStyles() {
    if (document.getElementById(STYLE_ID)) {
      return;
    }

    var style = document.createElement("style");
    style.id = STYLE_ID;
    style.textContent = [
      "#" + MODAL_ID + " { position: fixed; inset: 0; z-index: 10000; display: flex; align-items: center; justify-content: center; padding: 20px; background: rgba(15, 23, 42, 0.52); box-sizing: border-box; }",
      "#" + MODAL_ID + " * { box-sizing: border-box; }",
      "#" + MODAL_ID + " .ks-dialog { width: min(920px, 100%); max-height: min(86vh, 820px); overflow: auto; background: #fff; color: #172033; border-radius: 8px; box-shadow: 0 24px 80px rgba(15, 23, 42, 0.28); }",
      "#" + MODAL_ID + " .ks-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; padding: 22px 24px 14px; border-bottom: 1px solid #e5e7eb; }",
      "#" + MODAL_ID + " .ks-title { margin: 0; font-size: 22px; line-height: 1.25; font-weight: 700; }",
      "#" + MODAL_ID + " .ks-close { flex: 0 0 auto; width: 36px; height: 36px; border: 1px solid #d1d5db; border-radius: 8px; background: #fff; color: #111827; font-size: 24px; line-height: 30px; cursor: pointer; }",
      "#" + MODAL_ID + " .ks-body { padding: 18px 24px 24px; }",
      "#" + MODAL_ID + " .ks-summary { margin: 0 0 16px; color: #374151; line-height: 1.55; }",
      "#" + MODAL_ID + " .ks-notes { margin: 0 0 18px; padding: 12px 14px; border: 1px solid #fde68a; border-radius: 8px; background: #fffbeb; color: #78350f; line-height: 1.45; }",
      "#" + MODAL_ID + " .ks-notes ul, #" + MODAL_ID + " .ks-evidence { margin: 8px 0 0; padding-left: 20px; }",
      "#" + MODAL_ID + " .ks-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 14px; }",
      "#" + MODAL_ID + " .ks-card { min-width: 0; border: 1px solid #dfe3ea; border-radius: 8px; padding: 16px; background: #f9fafb; }",
      "#" + MODAL_ID + " .ks-point { margin: 0 0 12px; font-size: 17px; line-height: 1.3; font-weight: 700; }",
      "#" + MODAL_ID + " .ks-states { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 12px; }",
      "#" + MODAL_ID + " .ks-label { display: block; margin-bottom: 4px; color: #6b7280; font-size: 12px; font-weight: 700; text-transform: uppercase; }",
      "#" + MODAL_ID + " .ks-value { overflow-wrap: anywhere; color: #111827; font-weight: 600; }",
      "#" + MODAL_ID + " .ks-confidence { margin: 0 0 12px; color: #1f2937; font-weight: 700; }",
      "#" + MODAL_ID + " .ks-insight { margin: 0 0 12px; color: #374151; line-height: 1.45; }",
      "#" + MODAL_ID + " .ks-evidence { color: #374151; line-height: 1.45; }",
      "#" + MODAL_ID + " .ks-next { width: 100%; margin-top: 14px; border: 0; border-radius: 8px; padding: 10px 12px; background: #2563eb; color: #fff; font-weight: 700; cursor: pointer; }",
      "@media (max-width: 640px) { #" + MODAL_ID + " { padding: 10px; align-items: stretch; } #" + MODAL_ID + " .ks-dialog { max-height: 100%; } #" + MODAL_ID + " .ks-header, #" + MODAL_ID + " .ks-body { padding-left: 16px; padding-right: 16px; } #" + MODAL_ID + " .ks-states { grid-template-columns: 1fr; } }"
    ].join("\n");
    document.head.appendChild(style);
  }

  function removeExistingModal() {
    var existing = document.getElementById(MODAL_ID);
    if (existing) {
      existing.parentNode.removeChild(existing);
    }
  }

  function renderNotes(notes) {
    var items = asArray(notes);
    if (!items.length) {
      return "";
    }

    if (items.length === 1) {
      return '<div class="ks-notes">' + escapeHtml(items[0]) + "</div>";
    }

    return '<div class="ks-notes"><strong>Reliability notes</strong><ul>' +
      items.map(function (item) {
        return "<li>" + escapeHtml(item) + "</li>";
      }).join("") +
      "</ul></div>";
  }

  function renderEvidence(evidence) {
    var items = asArray(evidence);
    if (!items.length) {
      return '<ul class="ks-evidence"><li>No evidence summary available.</li></ul>';
    }
    return '<ul class="ks-evidence">' +
      items.map(function (item) {
        return "<li>" + escapeHtml(item) + "</li>";
      }).join("") +
      "</ul>";
  }

  function renderCard(card, index) {
    var nextAction = card.next_action || {};
    var nextActionLabel = typeof nextAction === "string" ? nextAction : (nextAction.label || "继续复盘");
    var nextActionType = typeof nextAction === "object" ? nextAction.type || "" : "";
    return [
      '<article class="ks-card">',
      '<h3 class="ks-point">' + escapeHtml(card.knowledge_point || ("Knowledge point " + (index + 1))) + "</h3>",
      '<div class="ks-states">',
      '<div><span class="ks-label">Previous</span><div class="ks-value">' + escapeHtml(stateLabel(card.previous_state || card.previous_state_label)) + "</div></div>",
      '<div><span class="ks-label">Current</span><div class="ks-value">' + escapeHtml(stateLabel(card.current_state || card.current_state_label)) + "</div></div>",
      "</div>",
      '<p class="ks-confidence">' + escapeHtml(confidenceLabel(card.state_confidence)) + "</p>",
      '<p class="ks-insight">' + escapeHtml(card.interesting_insight || "No additional insight available.") + "</p>",
      '<span class="ks-label">Evidence</span>',
      renderEvidence(card.evidence_summary),
      '<button type="button" class="ks-next" data-knowledge-state-next="' + index + '" data-action="' + escapeHtml(nextActionType) + '">' + escapeHtml(nextActionLabel) + "</button>",
      "</article>"
    ].join("");
  }

  function renderKnowledgeStatePopup(result, options) {
    var data = result || {};
    var settings = options || {};
    var cards = asArray(data.cards || data.knowledge_points);

    ensureStyles();
    removeExistingModal();

    var modal = document.createElement("div");
    modal.id = MODAL_ID;
    modal.setAttribute("role", "dialog");
    modal.setAttribute("aria-modal", "true");
    modal.setAttribute("aria-labelledby", "knowledge-state-modal-title");

    modal.innerHTML = [
      '<section class="ks-dialog">',
      '<header class="ks-header">',
      '<h2 id="knowledge-state-modal-title" class="ks-title">' + escapeHtml(data.popup_title || "Knowledge state update") + "</h2>",
      '<button type="button" class="ks-close" aria-label="Close knowledge state update">&times;</button>',
      "</header>",
      '<div class="ks-body">',
      '<p class="ks-summary">' + escapeHtml(data.overall_summary || "Your latest practice session has been analyzed.") + "</p>",
      renderNotes(data.low_reliability_notes),
      '<div class="ks-cards">',
      cards.length ? cards.map(renderCard).join("") : '<article class="ks-card">No knowledge-state cards are available yet.</article>',
      "</div>",
      "</div>",
      "</section>"
    ].join("");

    function closeModal() {
      if (modal.parentNode) {
        modal.parentNode.removeChild(modal);
      }
    }

    modal.addEventListener("click", function (event) {
      if (event.target === modal || event.target.className === "ks-close") {
        closeModal();
      }

      if (event.target && event.target.hasAttribute("data-knowledge-state-next")) {
        if (typeof settings.onNextAction === "function") {
          var cardIndex = Number(event.target.getAttribute("data-knowledge-state-next"));
          settings.onNextAction(cards[cardIndex], cardIndex);
        }
      }
    });

    document.body.appendChild(modal);
    return modal;
  }

  function showKnowledgeStateAfterPractice(sessionId, options) {
    var settings = options || {};
    return fetch(ANALYZE_ENDPOINT, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "Accept": "application/json"
      },
      body: JSON.stringify({
        session_id: sessionId,
        scope_key: settings.scope_key || null,
        trigger: settings.trigger || "practice_completed"
      })
    }).then(function (response) {
      if (!response.ok) {
        throw new Error("Knowledge-state analysis failed with status " + response.status);
      }
      return response.json();
    }).then(function (data) {
      return renderKnowledgeStatePopup(data, settings);
    });
  }

  window.renderKnowledgeStatePopup = renderKnowledgeStatePopup;
  window.showKnowledgeStateAfterPractice = showKnowledgeStateAfterPractice;
}());
