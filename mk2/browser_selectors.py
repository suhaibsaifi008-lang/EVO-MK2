"""Platform Browser Selectors and Rules for EVO MK2 (JARVIS Phase 2).

Defines DOM selectors, login workflows, and anti-abuse rate limits for autonomous platforms.
"""
from __future__ import annotations

from typing import Any

PLATFORM_CONFIG: dict[str, dict[str, Any]] = {
    "upwork": {
        "login_url": "https://www.upwork.com/ab/account-security/login",
        "selectors": {
            "username": "input#login_username, input[name='login[username]']",
            "password": "input#login_password, input[name='login[password]']",
            "submit": "button#login_control_continue, button[type='submit']",
            "search_input": "input[data-test='search-input'], input[placeholder*='Search']",
            "job_tile": "[data-test='job-tile-list'] .job-tile, section.up-card-section",
            "job_title": "[data-test='job-title-link'], h3.job-tile-title",
            "job_description": "[data-test='job-description-text'], .up-line-clamp-2",
            "job_budget": "[data-test='is-fixed-price'], [data-test='budget']",
            "proposal_button": "button[data-test='submit-proposal-button'], a[href*='apply']",
            "cover_note": "textarea[data-test='cover-note'], textarea#cover-letter",
            "proposal_rate": "input[data-test='proposal-rate'], input#step-rate",
            "submit_proposal": "button[data-test='submit-proposal-btn'], button:has-text('Submit Proposal')",
        },
        "rate_limits": {
            "proposals_per_day": 5,
            "min_interval_seconds": 3600,
            "max_bid_amount": 500.0,
        },
        "rules": {
            "no_spam": True,
            "personalize_every": True,
            "require_approval_first_contact": True,
        },
    },
    "fiverr": {
        "login_url": "https://www.fiverr.com/login",
        "selectors": {
            "username": "input#login, input[name='user[login]']",
            "password": "input#password, input[name='user[password]']",
            "submit": "button[type='submit']",
            "buyer_request_row": "tr.request-row, div.buyer-request-card",
            "send_offer_btn": "button.btn-send-offer, button:has-text('Send Offer')",
            "offer_desc": "textarea.offer-description",
            "offer_price": "input.offer-price",
            "submit_offer": "button.btn-submit-offer",
        },
        "rate_limits": {
            "offers_per_day": 10,
            "min_interval_seconds": 1800,
        },
        "rules": {
            "personalize_every": True,
        },
    },
    "gumroad": {
        "login_url": "https://gumroad.com/login",
        "selectors": {
            "username": "input#email",
            "password": "input#password",
            "submit": "button[type='submit']",
            "new_product_btn": "a[href*='/products/new'], button:has-text('New product')",
            "product_name": "input#product_name",
            "product_price": "input#product_price",
            "publish_btn": "button:has-text('Publish and continue'), button.publish-btn",
        },
        "rate_limits": {
            "creations_per_day": 3,
        },
    },
    "gmail": {
        "login_url": "https://accounts.google.com/signin",
        "selectors": {
            "username": "input[type='email']",
            "password": "input[type='password']",
            "submit": "button#identifierNext, button#passwordNext, button[type='submit']",
            "compose_btn": "div[role='button']:has-text('Compose'), div.T-I-KE",
            "to_field": "input[aria-label*='To'], input[peoplekit-id]",
            "subject_field": "input[name='subjectbox']",
            "body_field": "div[aria-label*='Message Body'], div[role='textbox']",
            "send_btn": "div[role='button']:has-text('Send'), div.aoO",
        },
        "rate_limits": {
            "emails_per_day": 15,
            "min_interval_seconds": 900,
        },
        "rules": {
            "no_spam": True,
            "personalized_only": True,
        },
    },
}


def get_platform_config(service: str) -> dict[str, Any] | None:
    return PLATFORM_CONFIG.get(service.strip().lower())
