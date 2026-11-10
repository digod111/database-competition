(function () {
  "use strict";

  const $ = (selector, root) => (root || document).querySelector(selector);
  const $$ = (selector, root) => Array.from((root || document).querySelectorAll(selector));

  function setupNav() {
    const toggle = $("#nav-toggle");
    const nav = $("#site-nav");
    if (!toggle || !nav) return;
    toggle.addEventListener("click", () => {
      const open = nav.classList.toggle("open");
      toggle.setAttribute("aria-expanded", String(open));
      toggle.setAttribute("aria-label", open ? "关闭导航" : "打开导航");
    });
    $$("a", nav).forEach((link) => {
      link.addEventListener("click", () => {
        nav.classList.remove("open");
        toggle.setAttribute("aria-expanded", "false");
      });
    });
  }

  function setupScrollSpy() {
    const sections = $$("main section[id]");
    const links = $$(".nav-link");
    if (!sections.length || !links.length) return;
    const byId = new Map(links.map((link) => [link.getAttribute("href").slice(1), link]));
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) return;
          links.forEach((link) => link.classList.remove("is-active"));
          const active = byId.get(entry.target.id);
          if (active) active.classList.add("is-active");
        });
      },
      { rootMargin: "-35% 0px -55% 0px", threshold: 0.01 }
    );
    sections.forEach((section) => observer.observe(section));
  }

  function setupReveal() {
    const items = $$(".reveal");
    if (!items.length) return;
    if (!("IntersectionObserver" in window) || window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      items.forEach((item) => item.classList.add("is-visible"));
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting || entry.boundingClientRect.top < 0) {
            entry.target.classList.add("is-visible");
            observer.unobserve(entry.target);
          }
        });
      },
      { rootMargin: "0px 0px -8% 0px", threshold: 0.08 }
    );
    items.forEach((item) => observer.observe(item));
  }

  function setupBackTop() {
    const button = $("#back-top");
    if (!button) return;
    const update = () => {
      button.classList.toggle("visible", window.scrollY > 620);
    };
    window.addEventListener("scroll", update, { passive: true });
    update();
    button.addEventListener("click", () => window.scrollTo({ top: 0, behavior: "smooth" }));
  }

  function setupCopy() {
    $$(".copy-btn").forEach((button) => {
      button.addEventListener("click", async () => {
        const target = $(button.dataset.copy);
        if (!target) return;
        const text = target.textContent || "";
        try {
          await navigator.clipboard.writeText(text);
          flashCopied(button, "已复制");
        } catch (error) {
          const area = document.createElement("textarea");
          area.value = text;
          area.style.position = "fixed";
          area.style.opacity = "0";
          document.body.appendChild(area);
          area.select();
          try {
            document.execCommand("copy");
            flashCopied(button, "已复制");
          } catch (fallbackError) {
            flashCopied(button, "复制失败");
          }
          area.remove();
        }
      });
    });
  }

  function flashCopied(button, text) {
    const original = button.textContent;
    button.textContent = text;
    button.disabled = true;
    window.setTimeout(() => {
      button.textContent = original;
      button.disabled = false;
    }, 1500);
  }

  let lastFocus = null;

  function setupLightbox() {
    const cards = $$("[data-lightbox]");
    const dialog = $("#lightbox");
    const image = $("#lightbox-image");
    const caption = $("#lightbox-caption");
    const close = $("#lightbox-close");
    if (!cards.length || !dialog || !image || !caption || !close) return;

    const open = (card) => {
      lastFocus = card;
      image.src = card.dataset.full || $("img", card).src;
      image.alt = $("img", card).alt || "";
      caption.textContent = card.dataset.caption || "";
      dialog.hidden = false;
      document.body.classList.add("no-scroll");
      close.focus();
    };
    const closeDialog = () => {
      dialog.hidden = true;
      document.body.classList.remove("no-scroll");
      image.src = "";
      if (lastFocus) lastFocus.focus();
    };

    cards.forEach((card) => {
      card.addEventListener("click", () => open(card));
      card.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          open(card);
        }
      });
    });
    close.addEventListener("click", closeDialog);
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) closeDialog();
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !dialog.hidden) closeDialog();
    });
  }

  function formatDate(value) {
    if (!value) return "";
    return String(value).replace("T", " ").slice(0, 16);
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function renderUsage() {
    const root = $("#usage-root");
    if (!root) return;
    const usage = window.WENGU_USAGE;
    if (!usage || !usage.has_real_data) {
      const empty = el("div", "usage-empty");
      empty.appendChild(el("span", "tag tag-amber", "真实数据"));
      empty.appendChild(el("h3", "", "真实使用数据收集中"));
      empty.appendChild(
        el(
          "p",
          "",
          "当前数据库只有 demo- / pytest- 验证账号。邀请同学使用后，运行 python tools/export_usage.py 并重新发布，这里会自动显示真实人数、次数、正确率与反馈。"
        )
      );
      if (usage && usage.exported_at) {
        empty.appendChild(el("p", "usage-source", "数据导出时间：" + formatDate(usage.exported_at) + " · 真实用户 0 人"));
      }
      root.replaceChildren(empty);
      return;
    }

    const real = usage.real_usage || {};
    const wrapper = document.createElement("div");
    const grid = el("div", "usage-grid");
    const stats = [
      [usage.real_users || 0, "真实学习者"],
      [real.sessions || 0, "复习会话"],
      [real.attempts || 0, "累计答题"],
      [Math.round((real.accuracy || 0) * 100) + "%", "整体正确率"],
    ];
    stats.forEach(([value, label]) => {
      const card = el("div", "usage-stat");
      card.appendChild(el("strong", "", String(value)));
      card.appendChild(el("span", "", label));
      grid.appendChild(card);
    });
    wrapper.appendChild(grid);

    if (Array.isArray(real.recent_days) && real.recent_days.length) {
      const panel = el("div", "usage-panel");
      panel.appendChild(el("h3", "", "近 7 天答题"));
      const chart = el("div", "usage-chart");
      const maxAttempts = Math.max(1, ...real.recent_days.map((day) => day.attempts || 0));
      real.recent_days.forEach((day) => {
        const bar = el("div", "usage-bar");
        const track = el("div", "usage-bar-track");
        const fill = el("div", "usage-bar-fill");
        fill.style.height = Math.max(2, Math.round(((day.attempts || 0) / maxAttempts) * 78)) + "px";
        track.appendChild(fill);
        bar.appendChild(track);
        bar.appendChild(el("strong", "", String(day.attempts || 0)));
        bar.appendChild(el("small", "", (day.day || "").slice(5)));
        chart.appendChild(bar);
      });
      panel.appendChild(chart);
      if (Array.isArray(usage.usage_events) && usage.usage_events.length) {
        const events = el("div", "usage-events");
        usage.usage_events.forEach((event) => {
          events.appendChild(el("span", "", `${event.event} · ${event.count}`));
        });
        panel.appendChild(events);
      }
      wrapper.appendChild(panel);
    }

    if (Array.isArray(usage.feedback) && usage.feedback.length) {
      const panel = el("div", "usage-panel");
      panel.appendChild(el("h3", "", "用户反馈（匿名）"));
      const list = el("ul", "usage-feedback");
      usage.feedback.forEach((item, index) => {
        const prefix = `同学 #${index + 1} · ${item.rating}/5 · `;
        list.appendChild(el("li", "", prefix + (item.comment || "（没有填写文字）")));
      });
      panel.appendChild(list);
      wrapper.appendChild(panel);
    }

    wrapper.appendChild(
      el(
        "p",
        "usage-source",
        "数据来源：seekdb attempts / sessions / usage_events / feedback；导出时间 " +
          formatDate(usage.exported_at) +
          "；已排除 demo- / pytest- 验证账号。"
      )
    );
    root.replaceChildren(wrapper);
  }

  function renderVerification() {
    const verification = window.WENGU_VERIFICATION;
    if (!verification) return;
    const setText = (selector, text) => {
      const node = $(selector);
      if (node) node.textContent = text;
    };
    if (verification.checks_total) {
      setText("#metric-checks", `${verification.checks_passed}/${verification.checks_total}`);
      setText("#proof-checks", `${verification.checks_passed}/${verification.checks_total} 通过`);
    }
    if (verification.session1_bplus && verification.session2_bplus) {
      setText("#metric-bplus", `${verification.session1_bplus} → ${verification.session2_bplus}`);
      setText("#proof-bplus1", `${verification.session1_bplus} 道`);
      setText("#proof-bplus2", `${verification.session2_bplus} 道`);
    }
    const strip = $("#verification-strip");
    if (strip) {
      const values = $$("strong", strip);
      if (values[0] && verification.attempts) values[0].textContent = String(verification.attempts);
      if (values[1] && verification.sessions) values[1].textContent = String(verification.sessions);
      if (values[2] && verification.accuracy) values[2].textContent = Math.round(verification.accuracy * 100) + "%";
      if (values[3] && verification.checks_total) {
        values[3].textContent = `${verification.checks_passed}/${verification.checks_total}`;
      }
    }
  }

  function setupRepoLink() {
    const link = $("[data-repo-link]");
    if (!link) return;
    const repo = window.WENGU_REPO_URL;
    if (repo) {
      link.href = repo;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = "查看代码仓库";
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    setupNav();
    setupScrollSpy();
    setupReveal();
    setupBackTop();
    setupCopy();
    setupLightbox();
    renderUsage();
    renderVerification();
    setupRepoLink();
  });
})();
