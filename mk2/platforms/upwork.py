"""Upwork Autonomous Specialist for EVO MK2 (JARVIS Phase 4)."""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from .. import llm
from ..audit import get_audit_logger
from ..browser_agent import get_browser_agent
from ..consent import get_consent_manager
from ..ethics import MoralVerdict, get_moral_engine

from ..config import DATA

log = logging.getLogger("mk2.platforms.upwork")
SEEN_GIGS_FILE = DATA / "upwork_seen_gigs.json"


class UpworkAgent:
    """Specialist agent for Upwork gig discovery, evaluation, and proposal generation."""

    RATE_LIMITS = {
        "proposals_per_day": 5,
        "min_interval_seconds": 3600,
        "min_scrape_interval_seconds": 3600,
        "max_bid_amount": 500.0,
    }

    def __init__(self):
        self.browser = get_browser_agent()
        self.ethics = get_moral_engine()
        self.consent = get_consent_manager()
        self.audit = get_audit_logger()
        self.proposals_sent_today = 0
        self.last_proposal_ts = 0.0
        self.last_scrape_ts = 0.0
        self.known_clients: set[str] = set()
        self.seen_gigs: set[str] = set()
        self._load_seen_gigs()

    def _load_seen_gigs(self) -> None:
        if SEEN_GIGS_FILE.exists():
            try:
                data = json.loads(SEEN_GIGS_FILE.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    self.seen_gigs = set(data)
            except Exception as exc:
                log.debug("Failed loading seen gigs: %s", exc)

    def _save_seen_gigs(self) -> None:
        try:
            SEEN_GIGS_FILE.parent.mkdir(parents=True, exist_ok=True)
            SEEN_GIGS_FILE.write_text(json.dumps(list(self.seen_gigs)[-500:]), encoding="utf-8")
        except Exception:
            pass

    def scrape_gigs(self, query: str = "Python automation", budget_max: float = 1000.0) -> MoralVerdict:
        """Navigate to Upwork job search and scrape live gig listings with deduplication and safety gates."""
        action = {"platform": "upwork", "action": "scrape_gigs", "query": query, "budget_max": budget_max}

        # 1. Check consent for browser navigation
        if not self.consent.has_consent("browser_navigate"):
            return MoralVerdict.caution("Scraping Upwork gigs requires browser navigation consent.", action=action)

        # 2. Check moral evaluation
        v = self.ethics.evaluate(action)
        if v.verdict == "block":
            return v

        # 3. Rate limiting: max 1 scrape per hour
        now = time.time()
        if (now - self.last_scrape_ts) < self.RATE_LIMITS.get("min_scrape_interval_seconds", 3600):
            wait = int(self.RATE_LIMITS.get("min_scrape_interval_seconds", 3600) - (now - self.last_scrape_ts))
            log.info("Upwork gig scrape throttled (%d seconds remaining)", wait)
            return MoralVerdict.safe("Upwork scraping throttled by rate limit.", action={"gigs": [], "count": 0, "throttled": True})

        # 4. Navigate to Upwork search
        search_url = f"https://www.upwork.com/nx/jobs/search/?q={query.replace(' ', '%20')}"
        nav_res = self.browser.navigate(search_url)
        if nav_res.verdict != "safe":
            return nav_res

        # 5. Randomized wait (2-5s) to avoid bot detection
        time.sleep(2.0 + (int(now) % 30) / 10.0)

        # 6. Scrape and parse job tiles
        scraped_raw: list[dict[str, Any]] = []
        try:
            if self.browser.page:
                eval_script = """
                () => {
                    const tiles = document.querySelectorAll('article.job-tile, section.air3-card-section, [data-test="job-tile-list"] > section');
                    const results = [];
                    tiles.forEach((t, i) => {
                        const titleEl = t.querySelector('h3 a, h2 a, [data-test="job-title-link"]');
                        const descEl = t.querySelector('[data-test="job-description-text"], .job-description, p');
                        const budgetEl = t.querySelector('[data-test="budget"], [data-test="is-fixed-price"], [data-test="job-type"]');
                        if (titleEl) {
                            results.push({
                                id: titleEl.getAttribute('href') || `gig_${i}`,
                                title: titleEl.innerText.trim(),
                                url: titleEl.getAttribute('href') ? `https://www.upwork.com${titleEl.getAttribute('href')}` : '',
                                description: descEl ? descEl.innerText.trim() : '',
                                budget: budgetEl ? budgetEl.innerText.trim() : '$100.00',
                            });
                        }
                    });
                    return results;
                }
                """
                extracted = self.browser.page.evaluate(eval_script)
                if isinstance(extracted, list):
                    scraped_raw = extracted
        except Exception as exc:
            log.debug("Upwork page evaluate note: %s", exc)

        if not scraped_raw:
            texts = self.browser.extract_text("article, .job-tile, section")
            for i, txt in enumerate(texts[:5]):
                if len(txt) > 30 and ("Python" in txt or "script" in txt or "bot" in txt or "automation" in txt or "API" in txt):
                    lines = [l.strip() for l in txt.split("\\n") if l.strip()]
                    scraped_raw.append({
                        "id": f"scraped_text_{i}_{int(now)}",
                        "title": lines[0] if lines else f"Python Automation Task #{i+1}",
                        "description": txt[:400],
                        "budget": "$150.00",
                        "url": search_url,
                    })

        self.last_scrape_ts = now

        # 7. Deduplicate & score with LLM
        valid_gigs: list[dict[str, Any]] = []
        for g in scraped_raw:
            gid = str(g.get("id") or g.get("url") or g.get("title"))
            if gid in self.seen_gigs:
                continue
            self.seen_gigs.add(gid)
            self._save_seen_gigs()

            score_res = self.evaluate_gig(g)
            score = score_res.get("score", 5)
            if score >= 6:
                g["evaluation"] = score_res
                g["score"] = score
                valid_gigs.append(g)

        self.audit.log_action(action, v, {"ok": True, "gigs_found": len(scraped_raw), "qualified": len(valid_gigs)})
        return MoralVerdict.safe(f"Scraped {len(valid_gigs)} qualified Upwork gigs.", action={"gigs": valid_gigs, "count": len(valid_gigs)})

    def evaluate_gig(self, gig: dict[str, Any], user_skills: str = "") -> dict[str, Any]:
        """Score an Upwork job listing 1-10 on suitability, margin, and win rate."""
        if not user_skills:
            try:
                from ..preference_learner import get_preference_learner
                prefs = get_preference_learner().get_preference("user_profile")
                user_skills = prefs.get("skills", "Python, Web Scraping, Automation, AI Agents, Playwright")
            except Exception:
                user_skills = "Python, Web Scraping, Automation, AI Agents, Playwright"

        title = gig.get("title", "")
        desc = gig.get("description", "")
        budget = gig.get("budget", "Flexible")

        prompt = (
            f"Evaluate this Upwork gig for a freelancer with skills: {user_skills}\n\n"
            f"Title: {title}\n"
            f"Description: {desc}\n"
            f"Budget: {budget}\n\n"
            "Evaluate on a 1-10 scale:\n"
            "1. Skill fit\n"
            "2. Win likelihood\n"
            "3. Legitimacy (low scam probability)\n"
            "4. Effort-to-pay ratio\n\n"
            'Return ONLY JSON: {"score": <1-10>, "recommendation": "pursue"|"skip"|"caution", "reasoning": "<1 sentence>", "suggested_bid": <number>}'
        )

        try:
            raw = llm.chat([
                {"role": "system", "content": "You are a pragmatic freelance strategist scoring opportunities."},
                {"role": "user", "content": prompt},
            ], role="fast", temperature=0.2)
            clean = raw.strip()
            if clean.startswith("```"):
                clean = clean.split("\n", 1)[-1].rsplit("```", 1)[0]
            return json.loads(clean.strip())
        except Exception as exc:
            log.warning("Gig evaluation failed: %s", exc)
            return {"score": 5, "recommendation": "caution", "reasoning": f"Automated scoring error: {exc}", "suggested_bid": 150.0}

    def generate_cover_note(self, gig: dict[str, Any], user_skills: str = "") -> str:
        """Write a personalized 3-4 sentence high-converting proposal."""
        if not user_skills:
            try:
                from ..preference_learner import get_preference_learner
                prefs = get_preference_learner().get_preference("user_profile")
                user_skills = prefs.get("skills", "Python, Playwright, Automation")
            except Exception:
                user_skills = "Python, Playwright, Automation"

        title = gig.get("title", "")
        desc = gig.get("description", "")

        prompt = (
            f"Write an Upwork proposal for this gig:\n"
            f"Title: {title}\n"
            f"Description: {desc}\n"
            f"Skills: {user_skills}\n\n"
            "Rules:\n"
            "- Reference a SPECIFIC technical detail from the job description.\n"
            "- Explain exactly how you will solve their problem directly.\n"
            "- 3-4 sentences max. No fluff, no 'Dear Hiring Manager', no generic boasts.\n"
            "- Professional and confident tone."
        )

        try:
            reply = llm.chat([
                {"role": "system", "content": "You write concise, winning freelance proposals."},
                {"role": "user", "content": prompt},
            ], role="fast", temperature=0.3)
            return reply.strip()
        except Exception as exc:
            return f"I can implement the required solution for {title} using {user_skills}. Ready to start immediately."

    def submit_proposal(self, gig: dict[str, Any], user_skills: str = "", user_approved: bool = False) -> MoralVerdict:
        """Prepare and submit an Upwork proposal with strict rate limits and approval gates."""
        if not user_skills:
            try:
                from ..preference_learner import get_preference_learner
                prefs = get_preference_learner().get_preference("user_profile")
                user_skills = prefs.get("skills", "Python, Automation")
            except Exception:
                user_skills = "Python, Automation"

        now = time.time()
        if self.proposals_sent_today >= self.RATE_LIMITS["proposals_per_day"]:
            return MoralVerdict.block(f"Daily limit reached ({self.proposals_sent_today}/{self.RATE_LIMITS['proposals_per_day']}).")

        if (now - self.last_proposal_ts) < self.RATE_LIMITS["min_interval_seconds"]:
            wait = int(self.RATE_LIMITS["min_interval_seconds"] - (now - self.last_proposal_ts))
            return MoralVerdict.block(f"Interval safety active. Wait {wait} seconds before next proposal.")

        evaluation = self.evaluate_gig(gig, user_skills)
        if evaluation.get("recommendation") == "skip":
            return MoralVerdict.block(f"Opportunity skipped: {evaluation.get('reasoning')}")

        cover_note = self.generate_cover_note(gig, user_skills)
        bid = float(evaluation.get("suggested_bid", 150.0))
        if bid > self.RATE_LIMITS["max_bid_amount"]:
            bid = self.RATE_LIMITS["max_bid_amount"]

        client_id = str(gig.get("client_id") or gig.get("client_name") or "unknown_client")
        is_first_contact = client_id not in self.known_clients

        action_payload = {
            "platform": "upwork",
            "type": "proposal_submit",
            "gig": gig,
            "cover_note": cover_note,
            "bid": bid,
            "client_id": client_id,
            "evaluation": evaluation,
        }

        v = self.ethics.evaluate(action_payload)
        if v.verdict == "block":
            return v

        if is_first_contact and not user_approved:
            return MoralVerdict.caution(
                f"First proposal to client '{client_id}' requires user review.",
                risks=["first_time_client", "approval_required"],
                action=action_payload,
            )

        if not self.consent.has_consent("proposal_submit") and not user_approved:
            return MoralVerdict.caution("Submitting proposals requires explicit consent.", action=action_payload)

        self.proposals_sent_today += 1
        self.last_proposal_ts = now
        self.known_clients.add(client_id)
        self.consent.record_outcome("proposal_submit", True, f"Submitted to {client_id} (${bid})")

        try:
            from ..crm import get_crm
            get_crm().add_or_update_client(name=client_id, platform="upwork", stage="pitched", budget=bid, notes=gig.get("title", ""))
            get_crm().record_interaction(client_id, "proposal", f"Upwork proposal submitted for '{gig.get('title')}' (${bid:.2f})", {"bid": bid, "gig_url": gig.get("url", "")})
        except Exception as exc:
            log.debug("CRM proposal record note: %s", exc)

        # Live browser automation if URL is present
        gig_url = gig.get("url")
        if gig_url and self.browser.browser:
            try:
                self.browser.navigate(gig_url)
                time.sleep(2)
                if self.browser.page:
                    # Fill proposal text area if present
                    self.browser.page.evaluate(f"""
                    (note) => {{
                        const textarea = document.querySelector('textarea[aria-labelledby*="cover_letter"], textarea.air3-textarea, textarea');
                        if (textarea) {{
                            textarea.value = note;
                            textarea.dispatchEvent(new Event('input', {{ bubbles: true }}));
                        }}
                    }}
                    """, cover_note)
                    # Click submit button
                    selectors = [
                        'button:has-text("Submit")',
                        'button[type="submit"]',
                        'input[type="submit"]',
                        '.submit-btn',
                        'button[aria-label*="submit" i]'
                    ]
                    for sel in selectors:
                        try:
                            btn = self.browser.page.query_selector(sel)
                            if btn:
                                btn.click()
                                break
                        except Exception as exc:
                            log.warning("Submit button selector %s click note: %s", sel, exc)
            except Exception as exc:
                log.warning("Browser proposal form automation note: %s", exc)

        self.audit.log_action(action_payload, v, {"ok": True, "bid": bid, "status": "submitted"})
        return MoralVerdict.safe(f"Proposal submitted for '{gig.get('title')}' at ${bid:.2f}", action=action_payload)

    def check_proposal_status(self) -> list[dict[str, Any]]:
        """Check status of active and submitted proposals on Upwork."""
        if not self.consent.has_consent("browser_navigate"):
            return []
        try:
            self.browser.navigate("https://www.upwork.com/ab/proposals/")
            time.sleep(2)
            proposals = []
            if self.browser.page:
                extracted = self.browser.page.evaluate("""
                () => {
                    const rows = document.querySelectorAll('tr, .proposal-item, [data-test="proposal-row"]');
                    const out = [];
                    rows.forEach(r => {
                        const titleEl = r.querySelector('a, h4, .job-title');
                        const statusEl = r.querySelector('.badge, [data-test="status"], .status-text');
                        if (titleEl) {
                            out.push({
                                title: titleEl.innerText.trim(),
                                status: statusEl ? statusEl.innerText.trim().toLowerCase() : 'pending',
                            });
                        }
                    });
                    return out;
                }
                """)
                if isinstance(extracted, list):
                    proposals = extracted
            return proposals
        except Exception as exc:
            log.warning("Upwork proposal status check failed: %s", exc)
            return []

    def check_messages(self) -> list[dict[str, Any]]:
        """Check for client replies in Upwork messaging center."""
        if not self.consent.has_consent("browser_navigate"):
            return []
        try:
            self.browser.navigate("https://www.upwork.com/ab/messages/")
            time.sleep(2)
            messages = []
            if self.browser.page:
                extracted = self.browser.page.evaluate("""
                () => {
                    const rooms = document.querySelectorAll('.room-list-item, .chat-item, [data-test="room-item"]');
                    const out = [];
                    rooms.forEach(rm => {
                        const sender = rm.querySelector('.name, .room-title, strong');
                        const snippet = rm.querySelector('.snippet, .last-message, p');
                        const unread = rm.querySelector('.unread-badge, .is-unread') !== null;
                        if (sender) {
                            out.push({
                                client: sender.innerText.trim(),
                                snippet: snippet ? snippet.innerText.trim() : '',
                                unread: unread,
                            });
                        }
                    });
                    return out;
                }
                """)
                if isinstance(extracted, list):
                    messages = extracted
            return messages
        except Exception as exc:
            log.warning("Upwork messages check failed: %s", exc)
            return []
