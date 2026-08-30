# ANTIGRAVITY — FULL AUTONOMY IMPLEMENTATION PROMPT

## YOUR MISSION

You are building the final 25% of EVO MK2 that makes it fully autonomous. The core architecture is already built and working (75% complete). Your job is to eliminate every stub, every hardcoded fallback, every fake integration, and replace it with real implementations that actually work.

**DO NOT change anything in the existing 75%.** The vault, consent, ethics, audit, browser agent, email agent, Upwork agent, web agent, money engine, kill switch, approval queue, JARVIS brain, schedule agent, security agent, wellness agent, strategy learner, preference learner, revenue tracker, file agent, comms agent, and dashboard are all real and working. Do not touch them.

**ONLY fix and build the following 10 items:**

---

## ITEM 1: Remove Hardcoded Money Engine Fallback

**File:** `mk2/money_engine.py`
**Lines:** 138-147

**DELETE these lines:**
```python
# In absence of direct browser scrape, add verified mock/seed leads if empty
if not results:
 results.append({
 "platform": "upwork",
 "type": "proposal_submit",
 "title": "Python Automation Pipeline for Data Aggregation",
 "description": "Need an autonomous script using Playwright and pandas to scrape and structure market records.",
 "budget": "$350",
 "client_id": "EnterpriseDataCorp",
 })
```

**Replace with:** Simply return empty results. If no real opportunities exist, the money engine should return `{"ok": True, "opportunities_found": 0}` and wait for the next scan cycle. It should NEVER invent fake opportunities.

---

## ITEM 2: Make User Skills Configurable

**File:** `mk2/platforms/upwork.py`
**Line:** 36

**CURRENT (hardcoded):**
```python
def evaluate_gig(self, gig: dict[str, Any], user_skills: str = "Python, Web Scraping, Automation, AI Agents, Playwright") -> dict[str, Any]:
```

**REPLACE WITH:** Read user skills from the preference learner:
```python
def evaluate_gig(self, gig: dict[str, Any], user_skills: str = "") -> dict[str, Any]:
 if not user_skills:
 try:
 from .preference_learner import get_preference_learner
 prefs = get_preference_learner().get_preference("user_profile")
 user_skills = prefs.get("skills", "Python, Web Scraping, Automation, AI Agents, Playwright")
 except Exception:
 user_skills = "Python, Web Scraping, Automation, AI Agents, Playwright"
```

Do the same for `generate_cover_note` and `submit_proposal` in the same file.

Also create a `user_profile` preference category in `mk2/preference_learner.py` defaults:
```python
"user_profile": {
 "skills": "Python, Web Scraping, Automation, AI Agents, Playwright",
 "title": "Freelance Developer",
 "hourly_rate": 75,
 "bio": "I build autonomous systems and automation tools."
}
```

---

## ITEM 3: Fix Machine Fingerprint Fallback in Vault

**File:** `mk2/credential_vault.py`
**Lines:** 48-55

**CURRENT:**
```python
def _derive_key_material(self) -> str:
 key_source = (
 self._master_key
 or os.environ.get("EVO_MASTER_KEY")
 or f"{platform.node()}:{platform.machine()}:{os.environ.get('USERNAME', 'evo')}"
 )
 return key_source
```

**REPLACE WITH:** Require explicit key. NO machine fingerprint fallback:
```python
def _derive_key_material(self) -> str:
 key_source = (
 self._master_key
 or os.environ.get("EVO_MASTER_KEY")
 )
 if not key_source:
 raise ValueError(
 "No master key provided. Set EVO_MASTER_KEY environment variable "
 "or pass master_key to CredentialVault(). "
 "Machine fingerprint fallback is disabled for security."
 )
 return key_source
```

This means the vault will NOT decrypt without the master key. The user must set `EVO_MASTER_KEY` in their `.env` file. This is a security requirement, not a bug.

---

## ITEM 4: Wire Real Google Calendar API into Schedule Agent

**File:** `mk2/schedule_agent.py`

**CURRENT:** Uses local SQLite table `autonomous_calendar`. No real calendar sync.

**ADD:** Google Calendar integration behind the credential vault:

1. In `schedule_agent.py`, add a `connect_google_calendar()` method:
```python
def connect_google_calendar(self) -> MoralVerdict:
 """Connect to Google Calendar using OAuth2 credentials from vault."""
 creds_data = self.vault.get("google_calendar")
 if not creds_data:
 return MoralVerdict.block(
 "No Google Calendar credentials stored. "
 "Add credentials via vault.store('google_calendar', {client_id, client_secret, refresh_token})."
 )
 # Use google-api-python-client with stored credentials
 # Return MoralVerdict.safe("Connected to Google Calendar")
```

2. Add real sync methods:
```python
def sync_google_calendar(self) -> MoralVerdict:
 """Pull events from Google Calendar into local cache."""

def push_to_google_calendar(self, event: dict) -> MoralVerdict:
 """Push a local event to Google Calendar."""
```

3. In `jarvis_agent.py` tick, call `schedule.sync_google_calendar()` at the start of each tick so the local cache stays fresh.

**Dependencies to add to requirements:** `google-api-python-client`, `google-auth-httplib2`, `google-auth-oauthlib`

**If Google Calendar credentials are not in the vault, the schedule agent should fall back to local SQLite but clearly log that real calendar sync is not available.**

---

## ITEM 5: Build Real Fiverr Agent

**File:** `mk2/platforms/fiverr.py` — REPLACE ENTIRE FILE

**Current:** Stub with one hardcoded method.

**Build a real Fiverr agent:**
```python
class FiverrAgent:
 """Autonomous Fiverr — gig creation, buyer request responses, order management."""

 RATE_LIMITS = {
 "buyer_requests_per_day": 10,
 "min_interval_seconds": 1800,
 "max_offer_amount": 500.0,
 }

 def __init__(self, browser: BrowserAgent, ethics: MoralEngine, consent: ConsentManager):
 self.browser = browser
 self.ethics = ethics
 self.consent = consent
 self.offers_sent_today = 0
 self.last_offer_ts = 0.0

 def search_buyer_requests(self, category: str = "programming") -> MoralVerdict:
 """Search Fiverr buyer requests matching user skills."""

 def evaluate_request(self, request: dict) -> MoralVerdict:
 """LLM-based scoring of a buyer request."""

 def submit_offer(self, request: dict, offer_note: str, price: float, delivery_days: int) -> MoralVerdict:
 """Submit an offer on a buyer request. Rate-limited."""

 def check_orders(self) -> list[dict]:
 """Check for new orders."""

 def deliver_order(self, order_id: str, files: list[str], message: str) -> MoralVerdict:
 """Deliver work for an order."""
```

**Implementation details:**
- Use the persistent browser agent (already built) to navigate to `fiverr.com/requests`
- Scrape buyer request listings from the DOM
- Use LLM to evaluate each request (skill fit, budget, competition)
- Generate personalized offer descriptions
- Rate limit: max 10 offers/day, minimum 30 min between offers
- First-time client requires approval (use ApprovalQueue)
- Track offers sent in `known_clients` set

---

## ITEM 6: Build Real Gumroad Agent

**File:** `mk2/platforms/gumroad.py` — REPLACE ENTIRE FILE

**Current:** Stub with one hardcoded method.

**Build a real Gumroad agent:**
```python
class GumroadAgent:
 """Autonomous Gumroad — product creation, publishing, sales tracking."""

 def __init__(self, browser: BrowserAgent, ethics: MoralEngine, consent: ConsentManager):
 self.browser = browser
 self.ethics = ethics
 self.consent = consent

 def create_product(self, name: str, price: float, file_path: str, description: str) -> MoralVerdict:
 """Create and publish a digital product on Gumroad."""

 def update_product(self, product_id: str, updates: dict) -> MoralVerdict:
 """Update product details (price, description, etc.)."""

 def check_sales(self) -> list[dict]:
 """Check recent sales from Gumroad API."""

 def generate_product_idea(self, user_skills: str, market_trends: str) -> dict:
 """Use LLM to suggest a digital product based on user skills and market trends."""
```

**Implementation details:**
- Use the persistent browser agent to navigate to Gumroad dashboard
- OR use Gumroad's API if user provides an API token in the vault
- For product creation: navigate to the publish page, fill form (name, price, description, file upload), submit
- For sales: scrape the dashboard or call the API
- Product ideas: LLM generates a product concept (e.g., "Python automation scripts pack", "AI prompt templates") based on user skills
- First product creation requires approval
- All products must be evaluated by moral engine (no copyrighted content, no misleading descriptions)

---

## ITEM 7: Build Real Stripe Agent

**File:** `mk2/platforms/stripe.py` — REPLACE ENTIRE FILE

**Current:** Stub with one hardcoded method.

**Build a real Stripe agent:**
```python
class StripeAgent:
 """Payment tracking and invoicing via Stripe."""

 def __init__(self, ethics: MoralEngine, consent: ConsentManager):
 self.ethics = ethics
 self.consent = consent
 self.api_key = None

 def connect(self) -> MoralVerdict:
 """Connect to Stripe using API key from vault."""

 def create_invoice(self, client_email: str, amount: float, description: str, due_days: int = 7) -> MoralVerdict:
 """Create and send an invoice via Stripe."""

 def get_balance(self) -> dict:
 """Get current Stripe balance."""

 def get_payments(self, limit: int = 20) -> list[dict]:
 """Get recent payment history."""

 def create_payment_link(self, product_name: str, price: float) -> MoralVerdict:
 """Create a Stripe payment link for a product."""
```

**Implementation details:**
- User stores Stripe API key in vault (`vault.store("stripe", {api_key: "sk_..."})`)
- Use `stripe` Python library (add to requirements)
- All financial actions require moral evaluation + user approval on first use
- Track all invoices and payments in the revenue tracker (already built)
- Never create charges without explicit user approval

**Dependencies to add to requirements:** `stripe`

---

## ITEM 8: Upgrade Knowledge Agent to Semantic Search

**File:** `mk2/knowledge_agent.py`

**CURRENT:** Keyword search on vault files and SQLite facts table.

**UPGRADE TO:** Semantic search using embeddings.

**Implementation:**
1. Add a local embedding model. Use `sentence-transformers` with `all-MiniLM-L6-v2` (small, fast, runs locally):
```python
from sentence_transformers import SentenceTransformer

class KnowledgeAgent:
 def __init__(self):
 self.model = SentenceTransformer("all-MiniLM-L6-v2")
 self.vault_dir = VAULT_DIR
 self._index_documents()
```

2. Index all vault documents, notes, email summaries, and conversation turns into a local vector store:
```python
def _index_documents(self):
 """Index all documents for semantic search."""
 # Index vault markdown files
 # Index SQLite facts
 # Index email summaries
 # Store embeddings in memory (or use chromadb for persistence)
```

3. Replace keyword search with semantic similarity:
```python
def search(self, query: str, limit: int = 5) -> list[dict]:
 """Semantic search across all indexed documents."""
 query_embedding = self.model.encode(query)
 # Compare against indexed document embeddings
 # Return top-k most similar results with similarity scores
```

4. Add relationship mapping:
```python
def find_related(self, item_id: str) -> list[dict]:
 """Find documents related to a given item based on semantic similarity."""

def knowledge_graph(self, topic: str) -> dict:
 """Map connections: people, projects, documents, events related to a topic."""
```

**Dependencies to add to requirements:** `sentence-transformers`, `torch` (or `onnxruntime` for lighter install)

**Fallback:** If `sentence-transformers` is not installed, fall back to keyword search (current behavior). Log a warning.

---

## ITEM 9: Upgrade Research Agent to Real Monitoring

**File:** `mk2/research_agent.py`

**CURRENT:** Stores topics to JSON, calls LLM for briefings. No real monitoring.

**UPGRADE TO:** Actual web monitoring using the existing `deep_research` tool.

**Implementation:**
1. The `monitor_topic()` method already stores topics. Keep that.
2. The `daily_briefing()` method already calls LLM. Upgrade it to actually pull fresh data:
```python
def daily_briefing(self) -> str:
 """Generate daily briefing with REAL fresh data."""
 topics = self.list_monitored_topics()
 fresh_data = []

 for topic in topics:
 # Use the existing deep_research tool to get current info
 from .research_tools import deep_research
 result = deep_research(topic)
 fresh_data.append({"topic": topic, "latest": result})

 # Synthesize into briefing using LLM
 prompt = f"Generate a daily intelligence briefing from this fresh data:\n{json.dumps(fresh_data)}"
 return llm.chat([...])
```

3. Add trend detection:
```python
def trend_report(self, industry: str) -> str:
 """Generate trend report by researching current developments."""
```

4. Add competitor tracking that actually does research:
```python
def track_competitor(self, competitor: str) -> dict:
 """Research competitor using deep_research, return structured intel."""
 from .research_tools import deep_research
 result = deep_research(f"{competitor} latest news product launches 2025 2026")
 return {"competitor": competitor, "intelligence": result}
```

5. Wire the research agent into the JARVIS brain tick. In `jarvis_agent.py`, add:
```python
def _check_research_topics(self) -> None:
 """Proactive research monitoring."""
 briefing = self.research.daily_briefing()
 if briefing and len(briefing) > 50:
 self.proactive_alerts.append(f"Research briefing ready: {len(self.monitored_topics)} topics monitored")
```

---

## ITEM 10: Build Real Synthesis Engine

**File:** `mk2/synthesis.py`

**CURRENT:** One method `connect_dots()` that calls LLM with a prompt.

**REPLACE WITH:** A real cross-domain synthesis engine:

```python
class SynthesisEngine:
 """Combines data from multiple EVO subsystems into unified intelligence."""

 def connect_dots(self, items: list[dict]) -> list[str]:
 """Find non-obvious connections between calendar, email, knowledge, money data."""
 # Gather context from all subsystems
 calendar = self.schedule.get_upcoming_events(48)
 inbox = self.email.read_inbox(limit=10, filter="all")
 knowledge = self.knowledge.proactive_surface(" ".join(e.get("title", "") for e in calendar))

 prompt = (
 "You are JARVIS connecting dots across calendar, email, knowledge, and money data.\n\n"
 f"Upcoming calendar events: {json.dumps(calendar[:3])}\n"
 f"Recent emails: {json.dumps(inbox[:5])}\n"
 f"Relevant knowledge: {json.dumps(knowledge[:3])}\n\n"
 "Identify 1-3 non-obvious, actionable connections. "
 "Example: 'Meeting tomorrow with Acme Corp + last email mentioned budget constraints "
 "+ knowledge base has past proposal → suggest preparing cost-saving option.'\n\n"
 "Return ONLY a bulleted list of insights."
 )
 # ... LLM call

 def generate_briefing(self) -> str:
 """Generate a unified morning briefing from ALL subsystems:
 - Calendar events for today
 - Urgent emails
 - Money opportunities found
 - Security alerts
 - Wellness status
 - Research updates
 """
 calendar = self.schedule.get_upcoming_events(24)
 emails = self.email.read_inbox(limit=5, filter="important")
 money = self.money.scan_opportunities()
 security = self.security.security_report()
 wellness = self.wellness.track_screen_time()
 research = self.research.daily_briefing()

 prompt = f"""Generate a concise morning briefing for the user:

TODAY'S CALENDAR:
{json.dumps(calendar, indent=2)}

URGENT EMAILS:
{json.dumps(emails, indent=2)}

MONEY OPPORTUNITIES:
{json.dumps(money[:3], indent=2)}

SECURITY STATUS:
{security}

WELLNESS:
{json.dumps(wellness, indent=2)}

RESEARCH BRIEFING:
{research}

Format as a clean, scannable briefing with sections:
1. Today at a Glance (calendar + urgent items)
2. Money (opportunities, pending proposals)
3. Alerts (security, wellness)
4. Research (key findings)
5. Recommended Actions (3-5 specific things to do today)
"""
 return llm.chat([...])

 def proactive_suggestion(self, context: str) -> str:
 """Generate a proactive suggestion based on current context."""
 # Gather all relevant data
 # Use LLM to generate a single, high-value suggestion
 pass
```

**Wire the synthesis engine into the JARVIS brain tick.** Each tick should call `synthesis.generate_briefing()` and surface the result as a proactive alert.

---

## ADDITIONAL FIXES

### Fix A: Remove Dead Duplicate autonomy.html

**File:** `mk2/ui/autonomy.html`

**DELETE this file.** It is a byte-for-byte duplicate of `money_dashboard.html` and is never served by any route. The server serves `money_dashboard.html` at `/autonomy`. Having two identical files is confusing and wastes space.

### Fix B: Add LLM Rate Limiting

**Files:** All files that call `llm.chat()` in loops (money_engine.py, jarvis_agent.py, research_agent.py, web_agent.py)

**ADD a simple rate limiter:**
```python
import time

class LLMRateLimiter:
 """Prevent excessive LLM calls in autonomous loops."""
 def __init__(self, max_calls_per_minute: int = 20):
 self.max_calls = max_calls_per_minute
 self.calls: list[float] = []

 def allow(self) -> bool:
 now = time.time()
 self.calls = [t for t in self.calls if now - t < 60]
 if len(self.calls) >= self.max_calls:
 return False
 self.calls.append(now)
 return True
```

Use it in every agent that makes LLM calls in a loop. If rate limit is hit, skip the tick and wait for the next one.

### Fix C: Add User Profile Configuration

**Create:** `mk2/user_profile.py` (new file)

```python
class UserProfile:
 """Central user profile — skills, preferences, goals."""

 DEFAULTS = {
 "skills": "Python, Web Scraping, Automation, AI Agents, Playwright",
 "title": "Freelance Developer",
 "hourly_rate": 75,
 "bio": "I build autonomous systems and automation tools.",
 "timezone": "Asia/Kolkata",
 "work_hours_start": 9,
 "work_hours_end": 23,
 "max_work_hours_per_day": 8,
 "income_goal_monthly": 5000,
 }

 def get(self, key: str, default=None):
 """Get a profile field."""

 def set(self, key: str, value):
 """Set a profile field."""

 def get_skills(self) -> str:
 """Get comma-separated skills string."""

 def get_context_for_llm(self) -> str:
 """Get a context string for LLM prompts."""
```

Store profile data in SQLite (`user_profile` table) with fallback to defaults. All agents should read from UserProfile instead of hardcoded values.

---

## WHAT "FULLY AUTONOMOUS" MEANS AFTER YOUR CHANGES

After you implement all 10 items above, EVO MK2 will:

1. **Never invent fake data** — the money engine returns empty when there are no real opportunities
2. **Use real user data** — skills, profile, preferences come from user configuration, not hardcoded strings
3. **Secure by default** — vault requires explicit master key, no machine fingerprint fallback
4. **Real calendar sync** — Google Calendar integration with bidirectional sync
5. **Real Fiverr integration** — search buyer requests, evaluate, send offers, track orders
6. **Real Gumroad integration** — create products, publish, track sales
7. **Real Stripe integration** — create invoices, track payments, get balance
8. **Semantic knowledge search** — find related documents, emails, conversations by meaning, not just keywords
9. **Real research monitoring** — daily briefings with fresh web data, competitor tracking with actual research
10. **Real cross-domain synthesis** — connects calendar + email + money + security + wellness into actionable briefings
11. **LLM rate limiting** — prevents runaway API costs in autonomous loops
12. **Clean codebase** — no dead duplicates, no stubs, no hardcoded fallbacks

---

## EXECUTION ORDER

| Priority | Item | Files to Change |
|----------|------|-----------------|
| P0 | ITEM 1: Remove fake money fallback | `mk2/money_engine.py` (delete lines 138-147) |
| P0 | ITEM 3: Fix vault security | `mk2/credential_vault.py` (lines 48-55) |
| P1 | ITEM 2: Make skills configurable | `mk2/platforms/upwork.py`, `mk2/preference_learner.py` |
| P1 | ITEM 5: Real Fiverr agent | `mk2/platforms/fiverr.py` (replace) |
| P1 | ITEM 6: Real Gumroad agent | `mk2/platforms/gumroad.py` (replace) |
| P1 | ITEM 7: Real Stripe agent | `mk2/platforms/stripe.py` (replace) |
| P1 | ITEM 8: Semantic knowledge | `mk2/knowledge_agent.py` (upgrade) |
| P2 | ITEM 4: Google Calendar | `mk2/schedule_agent.py` (add sync) |
| P2 | ITEM 9: Real research | `mk2/research_agent.py` (upgrade) |
| P2 | ITEM 10: Real synthesis | `mk2/synthesis.py` (replace) |
| P3 | Fix A: Remove dead file | delete `mk2/ui/autonomy.html` |
| P3 | Fix B: Rate limiting | Add to all LLM-calling agents |
| P3 | Fix C: User profile | Create `mk2/user_profile.py` |

---

## IMPORTANT RULES

1. **Do NOT modify any existing working code** unless it's specifically listed above as needing a change.
2. **Do NOT add new dependencies without adding them to `requirements.txt`** and documenting why each is needed.
3. **Every new method must have error handling** — wrap external API calls in try/except, log failures, return MoralVerdict.caution on error.
4. **Every new method that calls an external API must check consent first** using `self.consent.has_consent(action_type)`.
5. **Every new method that modifies external state must evaluate morally first** using `self.ethics.evaluate(action)`.
6. **Every new method that executes autonomously must log to audit** using `self.audit.log_action(action, verdict, outcome)`.
7. **First-time actions must go through the ApprovalQueue** — never auto-execute a new action type without user approval.
8. **All LLM calls must use the `role="fast"` parameter** for speed in autonomous loops, unless the task requires deep reasoning (then use `role="primary"`).
9. **All new code must follow the existing patterns** — singleton accessors (`get_*_agent()`), `_global_*` pattern, logging with `log = logging.getLogger("mk2.*")`.
10. **Run the test suite after every change** to ensure nothing breaks: `python -m pytest tests/test_jarvis_autonomy.py tests/test_jarvis_all_phases.py -v`

---

## WHAT SUCCESS LOOKS LIKE

When you're done:
- `python -m pytest tests/test_jarvis_autonomy.py tests/test_jarvis_all_phases.py -v` passes (13/13 minimum, more if you add tests)
- No file in `mk2/platforms/` returns hardcoded data
- No file in `mk2/` has a hardcoded fallback that returns fake results
- The money engine returns empty results when no real opportunities exist
- The vault requires an explicit master key
- All user-configurable values read from user profile or preference learner
- The calendar syncs with Google Calendar when credentials are provided
- Knowledge search returns semantically relevant results, not just keyword matches
- The research agent pulls fresh data, not just LLM hallucinations
- The synthesis engine connects real data from multiple subsystems

---

## START HERE

```bash
cd C:\Users\MOHD SUHAIB\Downloads\EVO-MK2
git checkout -b jarvis-full-autonomy
```

Work through the items in priority order (P0 first, then P1, P2, P3). After each item:
1. Implement the change
2. Run the tests
3. Commit with message: `feat(jarvis): <item description>`

When all items are done, run the full test suite one final time and report the results.
