# ANTIGRAVITY — FULL JARVIS BUILD PLAN
## EVO MK2: Complete Autonomous Agent Across All Domains

**Status:** READY TO EXECUTE
**Estimated Build Time:** 21 days
**Excluded:** Smart home hardware control (lights, locks, thermostats, appliances)
**Included:** Everything else — money, communication, scheduling, knowledge, research, security, health, proactive intelligence

---

## CORE PRINCIPLES

1. **Nothing happens without user consent.** First-time actions require explicit approval. Repeat actions auto-approve only after 3 successful precedents.
2. **Every action is logged.** Complete audit trail of everything EVO does — what, when, why, outcome.
3. **EVO evaluates its own actions before executing.** A moral/reasoning layer decides: is this action likely to help or harm?
4. **User can revoke anything at any time.** Kill switch stops all autonomous activity instantly.
5. **Gradual trust building.** Starts with read-only. Earns write/send/execute permissions through demonstrated competence.
6. **EVO thinks even when you don't ask it to.** Proactive intelligence — anticipates needs, connects dots, speaks first when it matters.

---

## PHASE 1: FOUNDATION — Trust, Security, Moral Engine, Event Bus

### Days 1-3: Build the trust layer that everything else depends on

#### 1.1 Encrypted Credential Vault
**File:** `mk2/vault.py` (new)

```python
class CredentialVault:
 """Encrypted storage for all user credentials."""

 def store(self, service: str, data: dict) -> dict:
 """Store credentials for a service (upwork, gmail, stripe, fiverr, etc.)
 data = {username, password, api_key, session_cookies, last_used, status}"""

 def get(self, service: str) -> dict | None:
 """Retrieve credentials. Returns None if not stored."""

 def has(self, service: str) -> bool:
 """Check if credentials exist for a service."""

 def list_services(self) -> list[str]:
 """List all services with stored credentials."""

 def remove(self, service: str) -> bool:
 """Remove credentials for a service."""

 def update_session(self, service: str, cookies: dict) -> None:
 """Update session cookies after login."""

 def _encrypt(self, data: str) -> bytes:
 """Encrypt using Fernet (symmetric key from user master password)."""

 def _decrypt(self, encrypted: bytes) -> str:
 """Decrypt using Fernet."""
```

**Design:**
- Encryption key derived from user's master password via PBKDF2 (100,000 rounds)
- Vault file stored locally at `data/credential_vault.enc`
- Never logged, never transmitted, never shown in plaintext
- Each service entry: `{service, username, password, api_key, session_cookies, last_used, status}`

#### 1.2 Consent Manager
**File:** `mk2/consent.py` (new)

```python
class ConsentManager:
 """Manages user consent levels and action approvals."""

 CONSENT_LEVELS = ["none", "read", "assist", "execute", "full"]
 ACTIONS_BY_LEVEL = {
 "none": [],
 "read": ["web_search", "screen_read", "weather", "calendar_read", "inbox_read"],
 "assist": ["fs_write", "docs_create", "research", "translate", "screenshot", "timer_set", "todo_add"],
 "execute": ["browser_navigate", "browser_screenshot", "browser_click", "browser_type",
 "mail_draft", "mail_send", "shell_run", "autonomy_execute"],
 "full": ["everything"],
 }

 def request_consent(self, level: str, reason: str) -> dict:
 """Request user to upgrade consent level."""

 def has_consent(self, action: str) -> bool:
 """Check if current consent level allows this action."""

 def get_level(self) -> str:
 """Get current consent level."""

 def record_success(self, action_type: str) -> None:
 """Record successful execution. After 3 successes, auto-approves."""

 def record_failure(self, action_type: str) -> None:
 """Record failed execution. Resets success streak to 0."""

 def is_trusted(self, action_type: str) -> bool:
 """Check if action type has 3+ consecutive successes."""

 def trust_score(self) -> float:
 """Calculate overall trust score 0.0-1.0."""
```

**Design:**
- Starts at `assist` level (read + create, no send/execute)
- Each new action type requires one-time user approval
- After 3 successful precedents of the same action type, auto-approves
- Any failure resets the streak to 0
- User can downgrade at any time
- All consent changes logged to audit trail

#### 1.3 Moral Reasoning Engine
**File:** `mk2/ethics.py` (new)

```python
class MoralEngine:
 """Evaluates actions before execution for potential harm."""

 def evaluate(self, action: dict, context: dict) -> MoralVerdict:
 """
 Evaluate: is this action likely to help or harm?

 Returns MoralVerdict:
 - safe: execute normally
 - caution: queue for user approval with reasoning
 - block: refuse with explanation
 """

 def evaluate_email(self, email: dict) -> MoralVerdict:
 """Is this email professional, personalized, and appropriate?"""

 def evaluate_browser_action(self, action: dict) -> MoralVerdict:
 """Is this browser action safe? Could it get accounts banned?"""

 def evaluate_financial(self, action: dict) -> MoralVerdict:
 """Is this financial action in the user's best interest?"""

 def evaluate_communication(self, action: dict) -> MoralVerdict:
 """Is this communication appropriate, timely, and well-timed?"""

 def evaluate_privacy(self, action: dict) -> MoralVerdict:
 """Does this action respect privacy and data protection?"""
```

**Design:**
- Uses LLM reasoning (not hardcoded rules) to evaluate each action
- Evaluates BEFORE execution, not after
- Three verdicts: `safe`, `caution`, `block`
- Considers: reputation risk, legal risk, financial risk, relationship risk, spam risk, timing risk
- For money-making: evaluates legitimacy, sustainability, alignment with user values
- For communication: evaluates tone, timing, appropriateness, recipient relationship

**Example evaluations:**
```
Action: Send cold email to 50 companies offering web design
Verdict: caution
Reasoning: "Cold outreach is legitimate but 50/day may trigger spam filters
 and damage domain reputation. Recommend: 5-10 personalized emails/day."
Risks: [spam_filter, domain_reputation, low_response_rate]

Action: Submit Upwork proposal for $500 Python automation gig
Verdict: safe
Reasoning: "Gig matches user's Python skill profile. Rate is within market range.
 Proposal will be personalized based on gig description."
Risks: []

Action: Create fake reviews for product
Verdict: block
Reasoning: "Fake reviews violate platform terms and risk account termination.
 This would harm long-term revenue and reputation."
```

#### 1.4 Audit Logger
**File:** `mk2/audit.py` (new, extends existing)

```python
class AuditLogger:
 """Complete audit trail of every autonomous action."""

 def log_action(self, action: dict, verdict: MoralVerdict, outcome: dict) -> None:
 """Log: timestamp, action, moral evaluation, execution result, user feedback."""

 def get_history(self, action_type: str = "", limit: int = 50) -> list[dict]:
 """Retrieve action history for learning."""

 def get_feedback(self, action_id: str) -> dict | None:
 """Get user feedback on a past action."""

 def export_report(self, start: float, end: float) -> str:
 """Generate human-readable report of all actions in time range."""
```

#### 1.5 Unified Event Bus
**File:** `mk2/bus.py` (extend existing)

```python
"""All systems communicate through the unified event bus.

Topics:
- autonomy.action — autonomous action started/completed
- autonomy.approval — approval needed from user
- autonomy.alert — something needs user attention
- comms.email_received — new email arrived
- comms.email_sent — email was sent
- comms.message_received — SMS/Telegram message
- calendar.event_soon — calendar event approaching
- calendar.event_changed — calendar event modified
- knowledge.new_finding — research agent found something relevant
- security.alert — security threat detected
- wellness.alert — health/wellness alert
- proactive.briefing — morning/evening briefing ready
- proactive.suggestion — EVO has a suggestion
- money.opportunity — money-making opportunity found
- money.revenue — revenue received
- system.status — system health updates
"""
```

---

## PHASE 2: BROWSER AUTONOMY — Persistent Sessions + Form Automation

### Days 4-5: Build the browser agent

#### 2.1 Persistent Browser Agent
**File:** `mk2/browser_agent.py` (new)

```python
class BrowserAgent:
 """Persistent browser with per-platform profiles and login memory."""

 def __init__(self, profile_dir: Path):
 self.profile_dir = profile_dir
 self.browser = None
 self.context = None
 self.sessions = {} # service -> {logged_in, cookies, last_used}

 def start(self) -> None:
 """Launch browser with persistent context."""

 def stop(self) -> None:
 """Close browser and save session state."""

 def login(self, service: str) -> MoralVerdict:
 """Log into a service using stored credentials."""

 def navigate(self, service: str, path: str) -> MoralVerdict:
 """Navigate to a page on a logged-in service."""

 def execute_sequence(self, service: str, actions: list[dict]) -> MoralVerdict:
 """Execute a sequence of browser actions (navigate, click, type, wait, scroll)."""

 def screenshot(self, service: str = "") -> str:
 """Take a screenshot for verification."""

 def _do_action(self, page, action: dict) -> dict:
 """Execute a single browser action."""
```

**Key features:**
- Chromium persistent context in `data/browser_profile/`
- Login sessions survive reboots
- Per-service sessions (Upwork logged in, Gmail logged in, etc.)
- Screenshot verification after each action
- Automatic retry on failure (max 3 attempts)

#### 2.2 Platform Browser Config
**File:** `mk2/browser_selectors.py` (new)

```python
PLATFORM_CONFIG = {
 "upwork": {
 "login_url": "...",
 "selectors": {username, password, submit, job_tile, proposal_button, ...},
 "rate_limits": {proposals_per_day: 5, min_interval_seconds: 3600},
 "rules": {no_spam: True, personalize_every: True, max_bid: 500},
 },
 "fiverr": { ... },
 "gmail": { ... },
 "linkedin": { ... },
}
```

---

## PHASE 3: COMMUNICATION INTELLIGENCE — Email, SMS, Telegram, Calls

### Days 6-8: Full communication management

#### 3.1 Email Agent
**File:** `mk2/email_agent.py` (new)

```python
class EmailAgent:
 """Full email intelligence — read, understand, draft, send, track."""

 def connect(self, service: str = "gmail") -> MoralVerdict:
 """Connect via IMAP/SMTP using stored credentials."""

 def read_inbox(self, limit: int = 20, filter: str = "all") -> list[dict]:
 """Read inbox. Filter: all, opportunities, important, spam."""

 def read_sent(self, limit: int = 20) -> list[dict]:
 """Read sent folder to track outreach."""

 def read_thread(self, thread_id: str) -> list[dict]:
 """Read full conversation thread."""

 def draft_email(self, to: str, subject: str, context: str, tone: str = "professional") -> MoralVerdict:
 """Draft email. Does NOT send. Returns for review."""

 def send(self, draft: dict) -> MoralVerdict:
 """Send email. Requires approval on first contact."""

 def track_replies(self) -> list[dict]:
 """Check for replies to sent emails."""

 def detect_opportunities(self) -> list[dict]:
 """Scan inbox for money-making opportunities (gig offers, client inquiries, etc.)."""

 def auto_reply_suggestion(self, email: dict) -> MoralVerdict:
 """Suggest a reply to an email. Does NOT send."""
```

#### 3.2 SMS/Telegram Agent
**File:** `mk2/comms_agent.py` (new)

```python
class CommsAgent:
 """Unified communication across all channels."""

 def send_sms(self, to: str, message: str) -> MoralVerdict:
 """Send SMS via Twilio or similar."""

 def send_telegram(self, to: str, message: str) -> MoralVerdict:
 """Send Telegram message."""

 def read_telegram(self, limit: int = 20) -> list[dict]:
 """Read recent Telegram messages."""

 def prioritize_messages(self, messages: list[dict]) -> list[dict]:
 """Rank messages by urgency and importance."""

 def draft_reply(self, message: dict, context: str) -> MoralVerdict:
 """Draft a reply. Does NOT send."""
```

#### 3.3 Proactive Communication Manager
**File:** `mk2/comms_intelligence.py` (new)

```python
class CommsIntelligence:
 """Intelligent communication management — proactive, not reactive."""

 def should_reply_now(self, email: dict) -> MoralVerdict:
 """Should EVO reply to this email right now, or wait?"""

 def suggest_follow_up(self, thread: list[dict]) -> MoralVerdict:
 """Suggest a follow-up for a thread that went cold."""

 def detect_urgent(self, message: dict) -> MoralVerdict:
 """Is this message urgent enough to interrupt the user?"""

 def auto_triage(self) -> list[dict]:
 """Sort all unread messages: respond now, respond later, ignore."""
```

---

## PHASE 4: PLATFORM EXECUTION — Money-Making Across All Channels

### Days 9-13: Build platform-specific execution agents

#### 4.1 Upwork Agent
**File:** `mk2/platforms/upwork.py` (new)

```python
class UpworkAgent:
 """Autonomous Upwork — search, evaluate, propose, message, deliver."""

 def search_gigs(self, query: str, budget_range: tuple = (0, 1000)) -> MoralVerdict:
 """Search for matching gigs."""

 def evaluate_gig(self, gig: dict) -> MoralVerdict:
 """LLM-based scoring: skill fit, competition, scam risk, ROI."""

 def submit_proposal(self, gig: dict, cover_note: str, rate: float) -> MoralVerdict:
 """Submit proposal. Rate-limited. Requires approval for new clients."""

 def check_messages(self) -> list[dict]:
 """Check for client replies."""

 def submit_deliverable(self, contract_id: str, files: list[str]) -> MoralVerdict:
 """Submit work for a contract."""
```

#### 4.2 Fiverr Agent
**File:** `mk2/platforms/fiverr.py` (new)

```python
class FiverrAgent:
 """Autonomous Fiverr — gig creation, order management, delivery."""

 def create_gig(self, title: str, description: str, price: float) -> MoralVerdict:
 """Create a gig on Fiverr."""

 def check_orders(self) -> list[dict]:
 """Check for new orders."""

 def deliver_order(self, order_id: str, files: list[str]) -> MoralVerdict:
 """Deliver work for an order."""
```

#### 4.3 Gumroad Agent
**File:** `mk2/platforms/gumroad.py` (new)

```python
class GumroadAgent:
 """Autonomous Gumroad — product creation, publishing, sales tracking."""

 def create_product(self, name: str, price: float, file_path: str) -> MoralVerdict:
 """Create and publish a digital product."""

 def check_sales(self) -> list[dict]:
 """Check recent sales."""

 def update_product(self, product_id: str, updates: dict) -> MoralVerdict:
 """Update product details."""
```

#### 4.4 Stripe/Payment Agent
**File:** `mk2/platforms/stripe.py` (new)

```python
class StripeAgent:
 """Payment tracking and invoicing."""

 def track_payments(self) -> list[dict]:
 """Get recent payments."""

 def create_invoice(self, client: str, amount: float, description: str) -> MoralVerdict:
 """Create and send invoice."""

 def get_revenue_stats(self, days: int = 7) -> dict:
 """Revenue statistics."""
```

#### 4.5 Generic Web Agent
**File:** `mk2/platforms/web_agent.py` (new)

```python
class WebAgent:
 """Can interact with ANY website — not just known platforms."""

 def interact(self, url: str, task: str) -> MoralVerdict:
 """Navigate to any URL and complete a task using LLM-guided browser control."""
 # LLM analyzes the page, decides what to click/fill/type
 # Executes actions with moral checks at each step
 # Verifies outcome via screenshot + text extraction
```

---

## PHASE 5: SCHEDULING + CALENDAR INTELLIGENCE

### Days 14-15: Intelligent time management

#### 5.1 Calendar Agent
**File:** `mk2/schedule_agent.py` (new)

```python
class ScheduleAgent:
 """Intelligent scheduling — not just reading, but managing."""

 def analyze_calendar(self, days: int = 7) -> dict:
 """Analyze calendar patterns. Find gaps, overloads, inefficiencies."""

 def suggest_optimizations(self) -> list[dict]:
 """Suggest calendar improvements: block focus time, move meetings, etc."""

 def schedule_focus_block(self, duration_minutes: int, preferred_time: str = "") -> MoralVerdict:
 """Automatically block focus time on calendar."""

 def pre_meeting_prep(self, event_id: str) -> MoralVerdict:
 """Before a meeting: research attendees, pull relevant files, create agenda."""

 def post_meeting_followup(self, event_id: str) -> MoralVerdict:
 """After a meeting: draft follow-up email, create action items, schedule next steps."""

 def resolve_conflict(self, new_event: dict) -> MoralVerdict:
 """When double-booked: suggest alternatives, decline lower-priority meeting."""

 def travel_reminder(self, event_id: str) -> MoralVerdict:
 """Calculate travel time and set departure reminder."""
```

---

## PHASE 6: KNOWLEDGE + FILE INTELLIGENCE

### Days 16-17: Your external brain

#### 6.1 Knowledge Agent
**File:** `mk2/knowledge_agent.py` (new)

```python
class KnowledgeAgent:
 """Semantic knowledge management — find, connect, summarize."""

 def search(self, query: str, scope: str = "all") -> list[dict]:
 """Semantic search across files, notes, emails, conversations."""

 def auto_tag(self, file_path: str) -> list[str]:
 """Auto-tag a file based on content."""

 def find_related(self, item: str) -> list[dict]:
 """Find items related to this one (documents, meetings, people)."""

 def summarize(self, item: str, max_words: int = 200) -> str:
 """Summarize a document, email thread, or conversation."""

 def knowledge_graph(self, topic: str) -> dict:
 """Map connections: people, projects, documents, events related to a topic."""

 def proactive_surface(self, context: str) -> list[dict]:
 """Surface relevant information based on current context.
 Example: User has meeting with Acme Corp → surface last email thread, project brief, related files."""
```

#### 6.2 File Intelligence
**File:** `mk2/file_agent.py` (new)

```python
class FileAgent:
 """Intelligent file management."""

 def organize(self, directory: str) -> MoralVerdict:
 """Auto-organize files by type, project, date."""

 def find(self, query: str) -> list[dict]:
 """Find files by semantic search, not just filename."""

 def summarize_batch(self, file_paths: list[str]) -> str:
 """Summarize multiple files into one briefing."""
```

---

## PHASE 7: RESEARCH + INFORMATION INTELLIGENCE

### Days 18-19: Continuous research and synthesis

#### 7.1 Research Agent
**File:** `mk2/research_agent.py` (new)

```python
class ResearchAgent:
 """Continuous research intelligence — monitors, tracks, synthesizes."""

 def monitor_topic(self, topic: str, frequency: str = "daily") -> str:
 """Set up continuous monitoring of a topic."""

 def daily_briefing(self) -> str:
 """Generate daily research briefing based on monitored topics."""

 def deep_dive(self, topic: str, depth: str = "standard") -> str:
 """Deep research on a topic with multi-source synthesis."""

 def track_competitor(self, competitor: str) -> dict:
 """Monitor a competitor's activity."""

 def trend_report(self, industry: str) -> str:
 """Generate trend report for an industry."""

 def synthesize(self, sources: list[str], question: str) -> str:
 """Synthesize multiple sources into a coherent answer."""
```

#### 7.2 Information Synthesis Engine
**File:** `mk2/synthesis.py` (new)

```python
class SynthesisEngine:
 """Combine information from multiple sources into actionable intelligence."""

 def synthesize_briefing(self, sources: list[dict]) -> str:
 """Combine: calendar events + email threads + research findings + news
 into a single, coherent briefing."""

 def connect_dots(self, items: list[dict]) -> list[dict]:
 """Find unexpected connections between seemingly unrelated items.
 Example: Calendar shows meeting with Acme Corp + email thread mentions budget constraints
 → EVO proactively notes: 'Acme meeting — they mentioned budget constraints last week.
 I've prepared 3 cost-saving options.'"""
```

---

## PHASE 8: SECURITY + PRIVACY MONITORING

### Days 20-21: Digital security intelligence

#### 8.1 Security Agent
**File:** `mk2/security_agent.py` (new)

```python
class SecurityAgent:
 """Digital security monitoring and protection."""

 def check_logins(self) -> list[dict]:
 """Check for new/unfamiliar device logins across accounts."""

 def check_passwords(self) -> list[dict]:
 """Flag weak, reused, or old passwords."""

 def check_phishing(self, email: dict) -> MoralVerdict:
 """Detect phishing attempts in emails."""

 def check_breaches(self) -> list[dict]:
 """Check if any credentials have been exposed in data breaches."""

 def check_backups(self) -> dict:
 """Verify that important files are backed up."""

 def security_report(self) -> str:
 """Generate security status report."""
```

---

## PHASE 9: HEALTH + WELLNESS

### Days 22-23: Health intelligence

#### 9.1 Wellness Agent
**File:** `mk2/wellness_agent.py` (new)

```python
class WellnessAgent:
 """Health and wellness intelligence."""

 def track_screen_time(self) -> dict:
 """Track daily screen time and patterns."""

 def suggest_break(self) -> MoralVerdict:
 """Suggest a break based on usage patterns."""

 def sleep_analysis(self) -> dict:
 """Analyze sleep patterns from activity data."""

 def stress_detection(self) -> MoralVerdict:
 """Detect stress from typing patterns, calendar density, late-night activity."""

 def daily_wellness_report(self) -> str:
 """Daily wellness summary and recommendations."""
```

---

## PHASE 10: THE JARVIS BRAIN — Proactive Intelligence + Money Engine

### Days 24-26: The core intelligence that ties everything together

#### 10.1 Proactive Intelligence Engine
**File:** `mk2/jarvis_agent.py` (new)

```python
class JarvisAgent:
 """The JARVIS brain — proactive, contextual, anticipatory."""

 def __init__(self):
 self.vault = CredentialVault()
 self.consent = ConsentManager()
 self.ethics = MoralEngine()
 self.audit = AuditLogger()
 self.calendar = ScheduleAgent()
 self.email = EmailAgent()
 self.knowledge = KnowledgeAgent()
 self.research = ResearchAgent()
 self.security = SecurityAgent()
 self.wellness = WellnessAgent()
 self.comms = CommsAgent()
 self.money = MoneyEngine()

 def start(self) -> None:
 """Start the proactive intelligence loop."""
 while self.running:
 self._tick()
 time.sleep(60) # Check every minute

 def _tick(self) -> None:
 """One tick of the JARVIS brain."""
 # 1. Check user activity
 if self._user_is_active():
 return # Don't interrupt

 # 2. Check calendar (upcoming events needing prep)
 self._check_calendar_prep()

 # 3. Check email (urgent, opportunities, replies needed)
 self._check_email_priorities()

 # 4. Check security (anomalies, breaches)
 self._check_security()

 # 5. Check wellness (breaks, stress, sleep)
 self._check_wellness()

 # 6. Check money opportunities (if consented)
 if self.consent.has_consent("autonomy_execute"):
 self.money.scan_and_execute()

 # 7. Check research monitoring (new developments in tracked topics)
 self._check_research_topics()

 # 8. Proactive suggestions
 self._generate_suggestions()

 def _check_calendar_prep(self) -> None:
 """Proactive meeting preparation.
 If meeting in 30 min → pull relevant files, draft agenda, prepare talking points.
 If meeting request without prep time → suggest declining or rescheduling."""

 def _check_email_priorities(self) -> None:
 """Proactive email management.
 If urgent email from important contact → surface immediately.
 If email needs reply and user hasn't responded in 24h → suggest follow-up.
 If cold outreach opportunity → queue for review."""

 def _check_security(self) -> None:
 """Proactive security.
 If new login detected → alert user.
 If password breach → alert and suggest change.
 If suspicious email → flag and suggest ignoring."""

 def _check_wellness(self) -> None:
 """Proactive wellness.
 If screen time > 4h → suggest break.
 If coding at 11pm → suggest saving state and resting.
 If back-to-back meetings tomorrow → suggest blocking focus time."""

 def _check_research_topics(self) -> None:
 """Proactive research.
 If tracked topic has new development → surface in briefing.
 If competitor made news → alert.
 If industry trend shifts → update strategy."""

 def _generate_suggestions(self) -> None:
 """Generate proactive suggestions based on context.
 Connect dots across: calendar + email + knowledge + money + wellness.
 Example: Meeting tomorrow with Acme Corp + last email mentioned budget + 
 knowledge base has past proposal → suggest preparing cost-saving option."""
```

#### 10.2 Money Decision Engine
**File:** `mk2/money_engine.py` (new)

```python
class MoneyEngine:
 """Autonomous money-making — scan, evaluate, execute, learn."""

 def scan_and_execute(self) -> None:
 """One tick of the money engine."""

 def _scan_opportunities(self) -> list[dict]:
 """Scan all platforms and channels for money opportunities."""

 def _evaluate_opportunity(self, opp: dict) -> MoralVerdict:
 """Evaluate opportunity morally and strategically."""

 def _pick_best(self, opportunities: list[dict]) -> dict:
 """LLM-based selection of highest-value opportunity."""

 def _execute(self, opportunity: dict) -> dict:
 """Execute the chosen opportunity."""

 def _learn(self, opportunity: dict, result: dict) -> None:
 """Record outcome, update strategy, adjust trust scores."""

 def _request_approval(self, action: dict) -> None:
 """Queue action for user approval via Approval Queue."""

 def weekly_report(self) -> str:
 """Weekly revenue and activity report."""
```

---

## PHASE 11: APPROVAL QUEUE + DASHBOARD

### Days 27-28: Human-in-the-loop interface

#### 11.1 Approval Queue
**File:** `mk2/approval_queue.py` (new)

```python
class ApprovalQueue:
 """Queue of actions pending user approval."""

 def enqueue(self, action: dict, verdict: MoralVerdict) -> str:
 """Add action to approval queue. Returns approval ID."""

 def get_pending(self) -> list[dict]:
 """Get all pending approvals."""

 def approve(self, approval_id: str) -> dict:
 """User approves. Execute it."""

 def reject(self, approval_id: str, reason: str = "") -> None:
 """User rejects. Log for learning."""

 def auto_approve(self, action_type: str) -> None:
 """Set action type to auto-approve going forward."""
```

#### 11.2 JARVIS Dashboard
**File:** `mk2/ui/autonomy.html` (new)

Sections:
- **Status:** What EVO is doing right now, what's queued, what's pending approval
- **Opportunities:** Money opportunities found, scored, queued
- **Communications:** Emails sent, replies pending, follow-ups due
- **Calendar:** Upcoming events, prep status, conflicts
- **Security:** Alerts, warnings, recommendations
- **Revenue:** Stats, trends, weekly report
- **Approval Queue:** Pending actions with approve/reject buttons
- **Audit Log:** Complete history of all autonomous actions

---

## PHASE 12: KILL SWITCH + EMERGENCY CONTROLS

### Day 29: Safety infrastructure

#### 12.1 Emergency Stop
```python
class KillSwitch:
 """Instant emergency stop for all autonomous activity."""

 def stop_all(self) -> None:
 """<10ms: halt money engine, close browser, downgrade consent, log event."""

 def stop_money(self) -> None:
 """Stop only money-making activity."""

 def stop_communications(self) -> None:
 """Stop only communication activity."""

 def downgrade_to(self, level: str) -> None:
 """Downgrade consent to specified level."""
```

**Triggers:**
- Voice: "EVO, stop everything" → <10ms response
- Text: "Emergency stop" → instant
- Dashboard: Red [STOP EVERYTHING] button → instant
- Keyboard shortcut: Ctrl+Shift+X → instant

---

## PHASE 13: LEARNING + ADAPTATION

### Days 30-31: EVO gets smarter over time

#### 13.1 Strategy Learner
**File:** `mk2/strategy_learner.py` (new)

```python
class StrategyLearner:
 """Learn from outcomes and adapt strategy."""

 def record_outcome(self, action: dict, result: dict) -> None:
 """Record what happened."""

 def get_best_strategy(self, goal: str) -> dict:
 """Get the best known strategy for a goal based on past success."""

 def adapt_rate(self, action_type: str, success_rate: float) -> None:
 """Adjust rate limits based on success/failure patterns."""

 def learn_timing(self, action_type: str, best_hours: list[int]) -> None:
 """Learn the best times to take certain actions."""
```

#### 13.2 User Preference Learner
**File:** `mk2/preference_learner.py` (new)

```python
class PreferenceLearner:
 """Learn user preferences from feedback and behavior."""

 def record_feedback(self, action: dict, feedback: str) -> None:
 """Record user's approval/rejection/edit of an action."""

 def get_preference(self, category: str) -> dict:
 """Get learned preferences for a category (email style, meeting preferences, etc.)."""

 def adapt_style(self, category: str) -> str:
 """Adapt communication/style based on learned preferences."""
```

---

## EXECUTION ORDER FOR ANTIGRAVITY

| Days | Files to Create | Files to Modify | Dependencies |
|------|----------------|-----------------|--------------|
| 1-3 | `mk2/vault.py`, `mk2/consent.py`, `mk2/ethics.py`, `mk2/audit.py` | `mk2/autonomy.py` (replace tier system), `mk2/bus.py` (extend) | cryptography |
| 4-5 | `mk2/browser_agent.py`, `mk2/browser_selectors.py` | `mk2/autonomy.py` (use BrowserAgent) | playwright |
| 6-8 | `mk2/email_agent.py`, `mk2/comms_agent.py`, `mk2/comms_intelligence.py` | | imaplib, smtplib, twilio |
| 9-13 | `mk2/platforms/upwork.py`, `fiverr.py`, `gumroad.py`, `stripe.py`, `web_agent.py` | | browser_agent, email_agent, ethics |
| 14-15 | `mk2/schedule_agent.py` | `mk2/calendar_tools.py` (extend) | google-api-python-client |
| 16-17 | `mk2/knowledge_agent.py`, `mk2/file_agent.py` | `mk2/deep_memory.py` (replace with semantic search) | chromadb or sentence-transformers |
| 18-19 | `mk2/research_agent.py`, `mk2/synthesis.py` | `mk2/deep_research` (extend) | |
| 20-21 | `mk2/security_agent.py` | | |
| 22-23 | `mk2/wellness_agent.py` | | |
| 24-26 | `mk2/jarvis_agent.py`, `mk2/money_engine.py` | `mk2/kernel.py` (integrate all agents) | All above |
| 27-28 | `mk2/approval_queue.py`, `mk2/ui/autonomy.html` | `mk2/ui/app.js`, `mk2/server.py` (add endpoints) | |
| 29 | `mk2/kill_switch.py` | `mk2/kernel.py` (integrate kill switch) | |
| 30-31 | `mk2/strategy_learner.py`, `mk2/preference_learner.py` | All agents (integrate learning) | |

---

## WHAT EVO CAN DO AFTER THIS PLAN

### Money
- Scans Upwork, Fiverr, Gumroad for opportunities
- Evaluates each opportunity morally and strategically
- Submits personalized proposals (with your approval on first contact)
- Sends cold outreach emails (personalized, rate-limited, spam-safe)
- Tracks revenue, invoices, conversion rates
- Learns what works and adapts strategy

### Communication
- Reads all email, SMS, Telegram
- Prioritizes messages by urgency
- Drafts replies (you approve before sending)
- Follows up on cold threads
- Sends outreach with personalization
- Never spams, never sends at bad times

### Scheduling
- Manages your calendar
- Blocks focus time automatically
- Prepares for meetings (research, agenda, files)
- Follows up after meetings (notes, action items, next steps)
- Resolves conflicts intelligently

### Knowledge
- Searches everything you've ever done (files, emails, conversations)
- Finds connections between seemingly unrelated things
- Surfaces relevant information before you ask
- Auto-organizes and tags files
- Summarizes long documents

### Research
- Monitors topics you care about
- Delivers daily briefings with relevant news
- Deep-dives on demand
- Tracks competitors and industry trends
- Synthesizes multi-source reports

### Security
- Alerts on new logins, weak passwords, breaches
- Detects phishing in emails
- Verifies backups
- Security status reports

### Wellness
- Tracks screen time and focus patterns
- Suggests breaks
- Detects stress
- Sleep analysis
- Daily wellness recommendations

### Proactive Intelligence (THE JARVIS PART)
- Anticipates needs before you ask
- Prepares for meetings before they happen
- Alerts on urgent things while you're busy
- Connects dots across all domains
- Speaks first when it matters, stays silent when it doesn't
- Learns your preferences and adapts

### Control
- You approve everything the first time
- After 3 successes, it auto-approves
- You can revoke anything instantly
- "EVO, stop everything" halts everything in <10ms
- Complete audit trail of every action
- Weekly reports on all activity

---

## SUCCESS CRITERIA

This plan is complete when:

1. EVO logs into Upwork with stored credentials and navigates autonomously
2. EVO searches gigs, evaluates them, and submits personalized proposals
3. EVO reads your email, drafts replies, and sends them (with consent)
4. EVO manages your calendar — prep, follow-up, conflict resolution
5. EVO finds any file or piece of information instantly via semantic search
6. EVO monitors topics you care about and delivers daily briefings
7. EVO alerts you to security threats before you notice them
8. EVO suggests breaks and manages your wellness
9. EVO proactively prepares for meetings, follows up on threads, connects dots
10. EVO makes money autonomously across multiple platforms
11. Every action is evaluated for moral safety before execution
12. Every action is logged to a complete audit trail
13. User has full control — kill switch, approval queue, consent levels
14. EVO learns from outcomes and improves over time
15. EVO earns real money without user intervention (after trust is earned)

---

## THE FINAL PRODUCT

A system that:
- **Thinks** — continuously monitors everything, connects dots, anticipates needs
- **Decides** — evaluates opportunities morally and strategically
- **Acts** — executes on your behalf across money, communication, scheduling, research
- **Learns** — improves from outcomes, adapts to your preferences
- **Protects** — watches your security, your wellness, your reputation
- **Reports** — transparently on all activity, weekly summaries, audit trail
- **Respects** — you're always in control, always informed, always able to stop it

**This is JARVIS.** Not a research assistant. Not a money bot. An agent that thinks, acts, learns, and protects — across every domain of your digital life.
