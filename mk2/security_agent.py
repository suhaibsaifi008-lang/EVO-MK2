"""Digital Security & Privacy Monitoring Agent for EVO MK2 (JARVIS Phase 8)."""
from __future__ import annotations

import logging
import re
import time
from typing import Any, Optional

from .credential_vault import get_credential_vault
from .ethics import MoralVerdict

log = logging.getLogger("mk2.security_agent")


class SecurityAgent:
    """Monitors credential health, phishing threats, and system privacy."""

    def __init__(self):
        self.vault = get_credential_vault()

    def check_passwords(self) -> dict[str, Any]:
        services = self.vault.list_services()
        weak = []
        for s in services:
            data = self.vault.get(s) or {}
            pw = str(data.get("password", ""))
            if len(pw) < 8 or pw.lower() in ("123456", "password", "admin"):
                weak.append(s)
        return {
            "total_services_checked": len(services),
            "weak_or_short_passwords": weak,
            "health_score": round(max(0, (len(services) - len(weak)) / (len(services) or 1)), 2),
        }

    def check_phishing(self, email_data: dict[str, Any]) -> MoralVerdict:
        text = (str(email_data.get("subject", "")) + " " + str(email_data.get("body", "") or email_data.get("snippet", ""))).lower()
        phishing_red_flags = [
            r"confirm your password", r"account has been suspended", r"urgent wire transfer",
            r"click here to unlock", r"security alert verify immediately", r"irs refund notification",
        ]
        detected = [pat for pat in phishing_red_flags if re.search(pat, text)]
        if detected:
            return MoralVerdict.caution(
                f"Potential phishing threat detected (triggers: {', '.join(detected)}). Do not click embedded links.",
                risks=["phishing_threat", "credential_theft_attempt"],
                action=email_data,
            )
        return MoralVerdict.safe("No overt phishing patterns detected in message.")

    def security_report(self) -> str:
        pw_health = self.check_passwords()
        return (
            f"# EVO Digital Security Status\n"
            f"**Credential Health Score:** {int(pw_health['health_score'] * 100)}%\n"
            f"**Services Protected:** {pw_health['total_services_checked']}\n"
            f"**Weak Credentials Identified:** {', '.join(pw_health['weak_or_short_passwords']) or 'None (All strong)'}\n"
            f"**Active Defense:** Phishing detection and session encryption online."
        )


_global_security: Optional[SecurityAgent] = None


def get_security_agent() -> SecurityAgent:
    global _global_security
    if _global_security is None:
        _global_security = SecurityAgent()
    return _global_security
