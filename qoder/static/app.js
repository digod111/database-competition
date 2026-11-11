(function () {
  "use strict";

  function qs(selector, root) {
    return (root || document).querySelector(selector);
  }

  function qsa(selector, root) {
    return Array.from((root || document).querySelectorAll(selector));
  }

  function wireFinishButtons() {
    qsa("#finish-btn").forEach(function (button) {
      if (button.dataset.wired === "1") return;
      button.dataset.wired = "1";
      button.addEventListener("click", function () {
        var root = qs(".practice-root");
        var sessionId = (root && root.dataset.session) || button.dataset.session;
        if (!sessionId) return;
        if (!window.confirm("结束后会生成本轮总结，并把薄弱点、会话摘要和未完成交接写入 PowerContext。确定结束吗？")) return;
        button.disabled = true;
        var original = button.textContent;
        button.textContent = "正在写入记忆…";
        fetch("/api/practice/" + sessionId + "/finish", { method: "POST" })
          .then(function (response) { return response.json().then(function (data) { return { ok: response.ok, data: data }; }); })
          .then(function (result) {
            if (!result.ok) throw new Error(result.data.error || "结束失败");
            window.location.href = "/practice/" + sessionId + "/summary";
          })
          .catch(function (error) {
            button.disabled = false;
            button.textContent = original;
            window.alert(error.message);
          });
      });
    });
  }

  function wirePractice() {
    var root = qs(".practice-root");
    wireFinishButtons();
    if (!root) return;

    var sessionId = root.dataset.session;
    var questionId = root.dataset.question;
    var qtype = root.dataset.type;
    var selectedIndex = null;
    var rating = null;
    var startedAt = Date.now();
    var pending = false;
    var submitButton = qs("#submit-answer");
    var hint = qs(".answer-actions .hint");

    qsa(".option-btn", root).forEach(function (button) {
      button.addEventListener("click", function () {
        if (button.disabled) return;
        selectedIndex = Number(button.dataset.index);
        qsa(".option-btn", root).forEach(function (item) { item.classList.remove("selected"); });
        button.classList.add("selected");
        if (submitButton) submitButton.disabled = false;
      });
    });

    qsa("#short-ratings .btn", root).forEach(function (button) {
      button.addEventListener("click", function () {
        if (pending) return;
        rating = button.dataset.rating;
        submitAnswer();
      });
    });

    function showError(message) {
      if (hint) {
        hint.textContent = message;
        hint.style.color = "#b3473f";
      } else {
        window.alert(message);
      }
    }

    function submitAnswer() {
      if (pending) return;
      if (qtype === "mcq" && selectedIndex === null) {
        showError("先选择一个选项。");
        return;
      }
      if (qtype !== "mcq" && !rating) return;
      pending = true;
      if (submitButton) {
        submitButton.disabled = true;
        submitButton.textContent = "判分中…";
      }
      var payload = {
        question_id: questionId,
        selected: qtype === "mcq" ? selectedIndex : null,
        rating: qtype === "mcq" ? null : rating,
        elapsed_ms: Date.now() - startedAt
      };
      fetch("/api/practice/" + sessionId + "/answer", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      })
        .then(function (response) {
          return response.json().then(function (data) { return { ok: response.ok, data: data }; });
        })
        .then(function (result) {
          if (!result.ok) throw new Error(result.data.error || "提交失败");
          renderFeedback(result.data);
        })
        .catch(function (error) {
          pending = false;
          if (submitButton) {
            submitButton.disabled = false;
            submitButton.textContent = "提交答案";
          }
          showError(error.message);
        });
    }

    function renderFeedback(data) {
      var panel = qs("#feedback-panel");
      var verdict = qs("#feedback-verdict");
      var text = qs("#feedback-text");
      var evidenceList = qs("#feedback-evidence");
      var meta = qs("#feedback-meta");
      if (!panel || !verdict || !text || !evidenceList || !meta) return;

      qsa(".option-btn", root).forEach(function (button) {
        button.disabled = true;
        button.classList.remove("selected");
        var index = Number(button.dataset.index);
        if (qtype === "mcq" && data.question && index === Number(data.question.answer)) {
          button.classList.add("correct");
        }
        if (qtype === "mcq" && index === selectedIndex && !data.correct) {
          button.classList.add("wrong");
        }
      });

      verdict.className = "feedback-verdict " + (data.correct ? "ok" : "bad");
      verdict.textContent = data.correct ? "答对了 · " + data.quality_label : "答错了 · " + data.quality_label;
      text.textContent = data.explanation.text;

      evidenceList.innerHTML = "";
      (data.explanation.evidence || []).forEach(function (item) {
        var article = document.createElement("article");
        article.className = "evidence-item";
        var top = document.createElement("div");
        top.className = "evidence-top";
        var title = document.createElement("strong");
        title.textContent = item.title;
        var score = document.createElement("span");
        score.className = "score";
        score.textContent = "相关度 " + Number(item.score).toFixed(3);
        top.appendChild(title);
        top.appendChild(score);
        article.appendChild(top);
        var metaLine = document.createElement("small");
        metaLine.textContent = item.course + " · " + item.chapter + " · " + item.source_path;
        article.appendChild(metaLine);
        var snippet = document.createElement("p");
        snippet.innerHTML = item.snippet_html;
        article.appendChild(snippet);
        evidenceList.appendChild(article);
      });

      var mastery = Math.round((data.state.mastery || 0) * 100);
      meta.innerHTML = "";
      var pieces = [
        "解析风格：" + data.explanation.style_label,
        "下一次：" + data.due_human,
        "掌握度：" + mastery + "%",
        "seekdb 证据：" + data.evidence_query_id
      ];
      pieces.forEach(function (piece) {
        var span = document.createElement("span");
        span.textContent = piece;
        meta.appendChild(span);
      });

      var actions = qs(".answer-actions", root);
      if (actions) actions.classList.add("hidden");
      panel.classList.remove("hidden");
      var nextLink = qs("#next-link", panel);
      if (nextLink) nextLink.href = "/practice/" + sessionId;
      panel.scrollIntoView({ behavior: "smooth", block: "start" });
    }

    if (submitButton) {
      submitButton.addEventListener("click", submitAnswer);
    }

    document.addEventListener("keydown", function (event) {
      if (pending) return;
      if (event.key >= "1" && event.key <= "4" && qtype === "mcq") {
        var button = qsa(".option-btn", root)[Number(event.key) - 1];
        if (button && !button.disabled) button.click();
      }
      if (event.key === "Enter" && qtype === "mcq" && selectedIndex !== null) {
        event.preventDefault();
        submitAnswer();
      }
    });

    wireFinishButtons();
  }

  function wirePreferences() {
    var form = qs("#preference-form");
    if (!form) return;
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      var status = qs("#pref-status");
      var data = new FormData(form);
      var payload = {
        daily_goal: Number(data.get("daily_goal")),
        explanation_style: data.get("explanation_style")
      };
      if (status) status.textContent = "保存中…";
      fetch("/api/preferences", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      })
        .then(function (response) { return response.json().then(function (body) { return { ok: response.ok, body: body }; }); })
        .then(function (result) {
          if (!result.ok) throw new Error(result.body.error || "保存失败");
          if (status) status.textContent = "已写入 PowerContext，重启后仍然生效。";
          setTimeout(function () { window.location.reload(); }, 700);
        })
        .catch(function (error) {
          if (status) status.textContent = error.message;
        });
    });
  }

  function wireFeedback() {
    var form = qs("#feedback-form");
    if (!form) return;
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      var status = qs("#feedback-status");
      var data = new FormData(form);
      var payload = {
        rating: Number(data.get("rating")),
        comment: String(data.get("comment") || "")
      };
      if (status) status.textContent = "提交中…";
      fetch("/api/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      })
        .then(function (response) { return response.json().then(function (body) { return { ok: response.ok, body: body }; }); })
        .then(function (result) {
          if (!result.ok) throw new Error(result.body.error || "提交失败");
          if (status) status.textContent = "已记录，感谢反馈。";
          form.reset();
        })
        .catch(function (error) {
          if (status) status.textContent = error.message;
        });
    });
  }

  function wireReset() {
    var button = qs("#reset-memory");
    if (!button) return;
    button.addEventListener("click", function () {
      if (!window.confirm("将清空当前用户在 seekdb 中的答题记录、会话和统计，并切换到新的 PowerContext Scope。旧记忆不会被召回。确定继续吗？")) return;
      button.disabled = true;
      button.textContent = "正在清空…";
      fetch("/api/memory/reset", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirm: true })
      })
        .then(function (response) { return response.json().then(function (body) { return { ok: response.ok, body: body }; }); })
        .then(function (result) {
          if (!result.ok) throw new Error(result.body.error || "清空失败");
          window.location.href = "/welcome";
        })
        .catch(function (error) {
          button.disabled = false;
          button.textContent = "清空并重新开始";
          window.alert(error.message);
        });
    });
  }

  function wireExamples() {
    qsa(".example-query").forEach(function (button) {
      button.addEventListener("click", function () {
        var input = qs('input[name="q"]');
        var form = input && input.closest("form");
        if (!input || !form) return;
        input.value = button.dataset.query || button.textContent.trim();
        form.submit();
      });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    wirePractice();
    wirePreferences();
    wireFeedback();
    wireReset();
    wireExamples();
  });
})();
