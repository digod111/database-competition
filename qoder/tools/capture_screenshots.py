from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8000"
ROOT = Path(__file__).resolve().parent.parent
SHOTS = ROOT / "docs" / "screenshots"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

ANSWER_INDEX = {
    "Q-DS-001": 1,
    "Q-DS-002": 2,
    "Q-DS-003": 2,
    "Q-DS-004": 2,
    "Q-DS-005": 1,
    "Q-DS-006": 1,
    "Q-DS-007": 1,
    "Q-DS-008": 1,
    "Q-OS-001": 2,
    "Q-OS-002": 2,
    "Q-OS-003": 1,
    "Q-OS-004": 1,
    "Q-OS-005": 2,
    "Q-OS-006": 2,
    "Q-OS-007": 1,
    "Q-OS-008": 1,
    "Q-OS-009": 2,
    "Q-OS-010": 2,
    "Q-NET-001": 1,
    "Q-NET-002": 1,
    "Q-NET-003": 1,
    "Q-NET-004": 1,
    "Q-NET-005": 1,
    "Q-NET-006": 2,
    "Q-NET-007": 2,
    "Q-NET-008": 1,
    "Q-NET-009": 1,
}


def main() -> None:
    SHOTS.mkdir(parents=True, exist_ok=True)
    summary = json.loads((ROOT / "docs" / "runs" / "demo_summary.json").read_text(encoding="utf-8"))
    demo_user = summary["demo_user"]
    session2 = summary["session2"]["session_id"]
    paths = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True, args=["--hide-scrollbars"])
        context = browser.new_context(viewport={"width": 1440, "height": 1080}, locale="zh-CN")
        page = context.new_page()

        def go(url: str) -> None:
            page.goto(url, wait_until="networkidle")
            page.add_style_tag(
                content=".site-header{position:static !important;} .plan-panel{position:static !important;}"
            )

        def shot(name: str, full_page: bool = False, wait: int = 900) -> Path:
            page.wait_for_timeout(wait)
            if full_page:
                page.evaluate("window.scrollTo(0, 0)")
            path = SHOTS / name
            page.screenshot(path=str(path), full_page=full_page)
            paths.append(path)
            print(f"saved {path}", flush=True)
            return path

        go(f"{BASE}/welcome")
        shot("01_welcome.png")

        go(f"{BASE}/switch/{demo_user}")
        go(f"{BASE}/library?q=B%2B%E6%A0%91%20%E6%8F%92%E5%85%A5%20%E5%88%86%E8%A3%82&course=%E6%95%B0%E6%8D%AE%E7%BB%93%E6%9E%84")
        shot("03_library_search.png", full_page=True)

        go(f"{BASE}/")
        shot("02_dashboard.png")
        shot("02b_dashboard_full.png", full_page=True)

        continue_link = page.locator("a", has_text="继续答题")
        if continue_link.count():
            go(BASE + continue_link.first.get_attribute("href"))
        else:
            go(f"{BASE}/practice/start?course=%E6%95%B0%E6%8D%AE%E7%BB%93%E6%9E%84")
        question_id = page.get_attribute(".practice-root", "data-question")
        print("practice question:", question_id, flush=True)
        shot("04_practice_question.png", full_page=True)

        index = ANSWER_INDEX.get(question_id, 1)
        page.locator(".option-btn").nth(index).click()
        page.locator("#submit-answer").click()
        page.wait_for_selector("#feedback-panel:not(.hidden)", timeout=15000)
        page.locator("#feedback-panel").screenshot(path=str(SHOTS / "05a_feedback_panel.png"))
        paths.append(SHOTS / "05a_feedback_panel.png")
        print(f"saved {SHOTS / '05a_feedback_panel.png'}", flush=True)
        shot("05_practice_feedback.png", full_page=True)

        go(f"{BASE}/practice/{session2}/summary")
        shot("06_session_summary.png", full_page=True)

        go(f"{BASE}/memory")
        shot("07_coach_memory.png", full_page=True)

        go(f"{BASE}/evidence")
        shot("08_technical_evidence.png", full_page=True)

        go(f"{BASE}/stats")
        shot("09_usage_stats.png", full_page=True)

        context.close()
        browser.close()

    print(json.dumps({"screenshots": [str(path) for path in paths]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
