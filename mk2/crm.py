"""Client Relationship Management (CRM) for EVO MK2.

Tracks client lifecycle, interaction timelines, and dynamic lead scoring.
Stages: lead -> pitched -> in_discussion -> contract_sent -> active -> completed -> churned.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from .config import DATA

log = logging.getLogger("mk2.crm")

CRM_DIR = DATA / "crm"
CRM_DIR.mkdir(parents=True, exist_ok=True)
CLIENTS_FILE = CRM_DIR / "clients.json"
INTERACTIONS_FILE = CRM_DIR / "interactions.json"

STAGES = ("lead", "pitched", "in_discussion", "contract_sent", "active", "completed", "churned")


@dataclass
class Interaction:
    id: str
    client_id: str
    type: str  # email, proposal, message, meeting, payment, note
    summary: str
    timestamp: float = field(default_factory=time.time)
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def kind(self) -> str:
        return self.type


@dataclass
class Client:
    id: str
    name: str
    email: str = ""
    platform: str = "general"  # upwork, fiverr, direct, stripe, etc.
    stage: str = "lead"
    lead_score: float = 50.0  # 0 to 100
    total_revenue: float = 0.0
    budget: float = 0.0
    tags: list[str] = field(default_factory=list)
    notes: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


class CRM:
    """Thread-safe persistent CRM engine."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._clients: dict[str, Client] = {}
        self._interactions: list[Interaction] = []
        self._load()

    def _load(self) -> None:
        with self._lock:
            if CLIENTS_FILE.exists():
                try:
                    data = json.loads(CLIENTS_FILE.read_text(encoding="utf-8"))
                    self._clients = {k: Client(**v) for k, v in data.items()}
                except Exception as exc:
                    log.warning("Failed to load clients.json: %s", exc)

            if INTERACTIONS_FILE.exists():
                try:
                    data = json.loads(INTERACTIONS_FILE.read_text(encoding="utf-8"))
                    self._interactions = [Interaction(**i) for i in data]
                except Exception as exc:
                    log.warning("Failed to load interactions.json: %s", exc)

    def _save(self) -> None:
        try:
            CLIENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
            raw_clients = {k: asdict(v) for k, v in self._clients.items()}
            CLIENTS_FILE.write_text(json.dumps(raw_clients, indent=2), encoding="utf-8")
            raw_ints = [asdict(i) for i in self._interactions]
            INTERACTIONS_FILE.write_text(json.dumps(raw_ints, indent=2), encoding="utf-8")
        except Exception as exc:
            log.warning("Failed to save CRM files: %s", exc)

    def calculate_lead_score(self, client: Client) -> float:
        """Score lead from 0 to 100 based on budget, stage progression, and history."""
        score = 40.0
        # Budget factor (max +25)
        if client.budget > 5000:
            score += 25
        elif client.budget > 1000:
            score += 15
        elif client.budget > 200:
            score += 8

        # Stage progression (max +30)
        stage_weights = {
            "lead": 0,
            "pitched": 10,
            "in_discussion": 25,
            "contract_sent": 35,
            "active": 45,
            "completed": 40,
            "churned": -20,
        }
        score += stage_weights.get(client.stage, 0)

        # Revenue bonus
        if client.total_revenue > 0:
            score += min(20, client.total_revenue / 100.0)

        return min(100.0, max(0.0, score))

    def add_or_update_client(
        self,
        name: str,
        email: str = "",
        platform: str = "general",
        stage: str = "lead",
        budget: float = 0.0,
        notes: str = "",
        tags: list[str] | None = None,
    ) -> Client:
        with self._lock:
            # Match by email or normalized name
            clean_id = re.sub(r"[^a-z0-9_]", "_", (email or name).lower().strip())
            client = self._clients.get(clean_id)
            now = time.time()
            if client:
                if email:
                    client.email = email
                if platform:
                    client.platform = platform
                if stage in STAGES:
                    client.stage = stage
                if budget > 0:
                    client.budget = budget
                if notes:
                    client.notes = f"{client.notes}\n{notes}".strip()
                if tags:
                    client.tags = list(set(client.tags + tags))
                client.updated_at = now
            else:
                client = Client(
                    id=clean_id,
                    name=name.strip(),
                    email=email.strip(),
                    platform=platform.strip(),
                    stage=stage if stage in STAGES else "lead",
                    budget=budget,
                    notes=notes.strip(),
                    tags=tags or [],
                    created_at=now,
                    updated_at=now,
                )
            client.lead_score = self.calculate_lead_score(client)
            self._clients[clean_id] = client
            self._save()
            return client

    def record_interaction(
        self,
        client_id_or_name: str,
        interaction_type: str,
        summary: str,
        meta: dict | None = None,
    ) -> Interaction:
        with self._lock:
            clean_id = re.sub(r"[^a-z0-9_]", "_", client_id_or_name.lower().strip())
            if clean_id not in self._clients:
                self._clients[clean_id] = Client(id=clean_id, name=client_id_or_name)

            client = self._clients[clean_id]
            int_id = f"int_{int(time.time()*1000)}"
            interaction = Interaction(
                id=int_id,
                client_id=clean_id,
                type=interaction_type,
                summary=summary,
                timestamp=time.time(),
                meta=meta or {},
            )
            self._interactions.append(interaction)
            client.updated_at = time.time()
            client.lead_score = self.calculate_lead_score(client)
            self._save()
            return interaction

    def list_interactions(self, client_name: Optional[str] = None) -> list[Interaction]:
        with self._lock:
            if not client_name:
                return list(self._interactions)
            clean_id = re.sub(r"[^a-z0-9_]", "_", client_name.lower().strip())
            return [i for i in self._interactions if i.client_id == clean_id]

    def record_payment(self, client_id_or_name: str, amount: float, source: str = "general") -> None:
        with self._lock:
            clean_id = re.sub(r"[^a-z0-9_]", "_", client_id_or_name.lower().strip())
            if clean_id not in self._clients:
                self._clients[clean_id] = Client(id=clean_id, name=client_id_or_name, total_revenue=amount, stage="active")
            else:
                self._clients[clean_id].total_revenue += amount
                self._clients[clean_id].stage = "active"
                self._clients[clean_id].updated_at = time.time()
            self._clients[clean_id].lead_score = self.calculate_lead_score(self._clients[clean_id])
            self._save()
        self.record_interaction(client_id_or_name, "payment", f"Received payment of ${amount:.2f} via {source}", {"amount": amount, "source": source})

    def get_client(self, client_id_or_name: str) -> Optional[Client]:
        with self._lock:
            clean_id = re.sub(r"[^a-z0-9_]", "_", client_id_or_name.lower().strip())
            return self._clients.get(clean_id)

    def list_clients(self, stage: Optional[str] = None, platform: Optional[str] = None) -> list[Client]:
        with self._lock:
            out = list(self._clients.values())
            if stage:
                out = [c for c in out if c.stage == stage]
            if platform:
                out = [c for c in out if c.platform == platform]
            return sorted(out, key=lambda c: c.lead_score, reverse=True)

    def get_pipeline_summary(self) -> dict[str, Any]:
        with self._lock:
            stages_count = {s: 0 for s in STAGES}
            pipeline_value = 0.0
            total_earned = 0.0
            for c in self._clients.values():
                stages_count[c.stage] = stages_count.get(c.stage, 0) + 1
                if c.stage != "churned":
                    pipeline_value += c.budget
                total_earned += c.total_revenue

            return {
                "total_clients": len(self._clients),
                "stages": stages_count,
                "pipeline_value": round(pipeline_value, 2),
                "total_revenue": round(total_earned, 2),
                "hot_leads": [asdict(c) for c in sorted(self._clients.values(), key=lambda x: x.lead_score, reverse=True)[:5]],
            }


_global_crm: Optional[CRM] = None


def get_crm() -> CRM:
    global _global_crm
    if _global_crm is None:
        _global_crm = CRM()
    return _global_crm
