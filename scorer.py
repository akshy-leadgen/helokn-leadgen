"""
scorer.py — Stage 5: score honestly.

Rubric (max 100):
  ICP fit              0–25   how well the org matches a Helokn ICP category
  Trigger strength     0–25   strong triggers, corroborated on the org's own site
  Evidence quality     0–20   trigger page is a real program page on the org's domain
  Company validity     0–15   homepage reachable, real name resolved, buyer signals
  Contact usefulness   0–15   named buyer email > named > role > generic > none

Tiers:
  A: score >= 80 AND real validated buyer org AND a strong trigger
  B: 65–79 with decent evidence
  C: 50–64 (needs review)
  below 50 -> rejected ("failed_scoring")

What can NOT happen anymore:
  * generic email pushing a lead into A (worth max 3 of 100 points)
  * a keyword mention alone reaching 50 (weak-only rows never got here)
  * an article title as company (name is resolved from the homepage)
"""

from junkfilter import root_domain

ICP_KEYWORDS = {
    "Certification Body": ["certification exam", "exams / knowledge checks", "accredited provider"],
    "Credentialing Organization": ["credentialing program"],
    "Continuing Education Provider": ["continuing education credits", "accredited provider"],
    "Healthcare Training Organization": ["clinical education", "compliance training with assessments",
                                         "continuing education credits"],
    "Clinical Education Organization": ["clinical education", "competency assessment"],
    "Nursing Education Organization": ["clinical education", "continuing education credits"],
    "Legal Education / CLE Provider": ["legal education / CLE"],
    "Bar Association": ["legal education / CLE", "continuing education credits"],
    "Compliance Training Organization": ["compliance training with assessments"],
    "Workforce Development Organization": ["workforce development"],
    "Corporate L&D Team": ["training academy", "learning and development program", "employee onboarding"],
    "Professional Association (Certification)": ["certification exam", "continuing education credits"],
    "Safety Training Organization": ["staff training program", "training academy"],
    "Education Organization (Assessments)": ["assessment rubric", "exams / knowledge checks",
                                              "skills validation", "competency assessment"],
    "Structured Onboarding / Training Program": ["employee onboarding", "staff training program"],
}

CONTACT_POINTS = {
    "Named Buyer Email": 15,
    "Named Buyer - No Email": 10,
    "Role-Based Email": 8,
    "Generic Email Only": 3,
    "Manual Research Needed": 0,
    "No Contact Found": 0,
}


def classify_icp(candidate, company_info) -> str:
    """Pick the ICP category best supported by observed triggers.

    The query bank was built so each query's icp_hint already reflects a
    deliberate targeting decision (e.g. a bar-association-specific query
    hints "Bar Association"). Incidental keyword overlap with a broader
    category (almost every bar association page also matches "Legal
    Education / CLE Provider" on the "legal education / CLE" trigger)
    should not silently override that unless the alternative category is
    CLEARLY better supported — otherwise every bar association gets
    mislabeled as a generic CLE provider, which is what happened before
    this fix. The hint gets a +1 head start; another category must beat
    that outright to win.
    """
    triggers = set(candidate.triggers) | set(company_info.get("homepage_triggers", []))
    hint_n = len(triggers & set(ICP_KEYWORDS.get(candidate.icp_hint, [])))
    best, best_n = candidate.icp_hint, hint_n + 1
    for cat, keys in ICP_KEYWORDS.items():
        if cat == candidate.icp_hint:
            continue
        n = len(triggers & set(keys))
        if n > best_n:
            best, best_n = cat, n
    return best


def score_lead(candidate, company_info, contact) -> dict:
    triggers_snippet = set(candidate.triggers)
    triggers_home = set(company_info.get("homepage_triggers", []))
    all_triggers = triggers_snippet | triggers_home
    corroborated = triggers_snippet & triggers_home
    buyer_signals = company_info.get("buyer_signals", 0)

    # --- ICP fit (0-25): trigger-backed category match ---
    icp = classify_icp(candidate, company_info)
    icp_keys = set(ICP_KEYWORDS.get(icp, []))
    icp_pts = min(25, 10 + 5 * len(all_triggers & icp_keys) + min(5, buyer_signals))

    # --- Trigger strength (0-25) ---
    trig_pts = min(25, 8 * len(corroborated) + 4 * len(all_triggers - corroborated))

    # --- Evidence quality (0-20) ---
    evidence_on_own_domain = root_domain(candidate.raw_url) == candidate.domain
    ev_pts = 0
    if evidence_on_own_domain:
        ev_pts += 8
    if not candidate.evidence_weak:
        ev_pts += 7          # program page, not a blog/job/article path
    if corroborated:
        ev_pts += 5          # homepage independently confirms the trigger
    ev_pts = min(20, ev_pts)

    # --- Company validity (0-15) ---
    val_pts = 8              # homepage reachable + real name (validation gate)
    if buyer_signals >= 2:
        val_pts += 4
    if triggers_home:
        val_pts += 3
    val_pts = min(15, val_pts)

    # --- Contact usefulness (0-15) ---
    con_pts = CONTACT_POINTS.get(contact["contact_status"], 0)

    score = icp_pts + trig_pts + ev_pts + val_pts + con_pts

    # --- Tier gates (not just thresholds) ---
    strong = bool(corroborated) or (len(all_triggers) >= 2 and not candidate.evidence_weak)
    if score >= 80 and strong:
        tier = "A"
    elif score >= 65:
        tier = "B"
    elif score >= 50:
        tier = "C"
    else:
        tier = "Rejected"

    audit = (
        f"ICP fit {icp_pts}/25 ({icp}); "
        f"triggers {trig_pts}/25 ({', '.join(sorted(all_triggers)) or 'none'}"
        f"{'; corroborated on homepage' if corroborated else ''}); "
        f"evidence {ev_pts}/20 "
        f"({'own-domain' if evidence_on_own_domain else 'off-domain'} page, "
        f"{'weak/blog path' if candidate.evidence_weak else 'program page'}); "
        f"validity {val_pts}/15 ({buyer_signals} buyer signals on homepage); "
        f"contact {con_pts}/15 ({contact['contact_status']})"
    )

    return {"score": score, "tier": tier, "icp_category": icp,
            "audit_trail": audit,
            "trigger_type": ", ".join(sorted(all_triggers)[:3]),
            "strong": strong}