"""
enrich.py — Stage 4: contacts, only for VALIDATED companies.

Zero Serper credits: we scrape the org's own /contact, /about, /team,
/leadership pages (cached). Manual-research URLs (LinkedIn + Google)
are ALWAYS generated, so a lead with no scraped contact is still
actionable for sales.

Honesty rules (the old pipeline lied here):
  * info@ is never "Verified Email" — it is "Generic Email Only".
  * A named person only counts if their title matches a buyer role.
  * If we can't find anything, we say "Manual Research Needed" and
    hand sales the search links.

contact_status  : Named Buyer Email | Named Buyer - No Email |
                  Role-Based Email | Generic Email Only |
                  Manual Research Needed | No Contact Found
email_confidence: high_verified_named | medium_likely_named |
                  medium_role_based | low_generic | none
"""

import re
from urllib.parse import quote_plus, urljoin, unquote

from bs4 import BeautifulSoup

import config
from companyclean import fetch_page
from junkfilter import root_domain

# Order matters: the speed cap (config.MAX_CONTACT_PAGES) and early-stop
# only ever see the FIRST few paths, so pages where named people appear
# (team/leadership/staff) must come before secondary contact pages. The
# v4 audit showed named-contact yield collapse to zero when these sat at
# positions 6-8, behind an early-stop at position 5.
CONTACT_PATHS = ["/contact", "/about", "/team", "/leadership", "/staff",
                 "/our-team", "/about-us", "/contact-us",
                 "/staff-directory", "/directory", "/board-and-staff",
                 "/who-we-are", "/about/leadership", "/about/staff",
                 "/about/team", "/meet-the-team", "/administration"]

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
# Strict shape for a FINAL accepted email (fullmatch, ASCII only) — this is
# what stops artifacts like '%E2%80%A8amoore@...' (URL-encoded U+2028 glued
# on by regex-over-raw-HTML) from ever reaching the CSV.
STRICT_EMAIL_RE = re.compile(
    r"[a-z0-9][a-z0-9._+\-']*@[a-z0-9][a-z0-9.\-]*\.[a-z]{2,}", re.I)
# "name [at] domain [dot] org" style obfuscation
DEOBF_RE = [
    (re.compile(r"\s*[\[\(\{]\s*at\s*[\]\)\}]\s*", re.I), "@"),
    (re.compile(r"\s*[\[\(\{]\s*dot\s*[\]\)\}]\s*", re.I), "."),
]

GENERIC_LOCALPARTS = {
    "info", "contact", "contactus", "hello", "admin", "office", "support",
    "help", "mail", "email", "enquiries", "inquiries", "general", "sales",
    "marketing", "media", "press", "comms", "communications", "feedback",
    "team", "credit", "consulting", "service", "services", "reception",
}

# Dead inboxes: worse than generic — mail to these bounces or goes nowhere.
# 'no-reply@atcb.org' and 'webmaster@fbpe.org' were surfaced as contacts in
# the v5 audit; they must never appear in the CSV at all.
EXCLUDED_LOCALPARTS = {
    "noreply", "no-reply", "donotreply", "do-not-reply", "webmaster",
    "postmaster", "mailer-daemon", "abuse", "privacy", "unsubscribe",
    "bounce", "notifications", "alert", "alerts", "newsletter",
}

ROLE_LOCALPARTS = {
    "training", "education", "learning", "certification", "credentialing",
    "compliance", "exams", "exam", "ce", "cme", "cle", "academy",
    "professionaldevelopment", "workforce", "hr", "membership", "programs",
    "certifications", "credentials", "recertification", "testing",
    "accreditation", "examinations",
    # widened for the certification/licensing-body-heavy ICP mix: these are
    # exactly the inboxes small boards/associations actually publish for
    # exam/license administration, found missing during the v6 audit.
    "registrar", "licensing", "renewal", "renewals", "records",
    "registration", "credentialingdept", "certboard", "examdept",
}

# Buyer titles, highest priority first (used for named-contact matching).
# v5 additions: coordinator/specialist variants and — importantly —
# "executive director": at small certification boards and associations
# (a large share of qualified leads) the ED is exactly the buyer sales
# needs. It sits last so any more specific education/certification title
# on the same page wins first.
PRIORITY_TITLES = [
    "chief learning officer",
    "director of learning and development", "vp of training",
    "vice president of training", "director of education",
    "director of clinical education", "certification program manager",
    "assessment director", "compliance training manager",
    "continuing education manager", "credentialing manager",
    "workforce development director", "training program manager",
    "professional education manager", "talent development lead",
    "director of training", "director of certification",
    "director of credentialing", "director of continuing education",
    "director of professional development", "cle director",
    "certification director", "education director",
    "certification coordinator", "credentialing coordinator",
    "education coordinator", "continuing education coordinator",
    "credentialing specialist", "certification specialist",
    "certification manager", "education manager", "training manager",
    "program director", "executive director",
]
TITLE_RE = re.compile("|".join(re.escape(t) for t in PRIORITY_TITLES), re.I)
NAME_RE = re.compile(r"\b([A-Z][a-z]+(?: [A-Z]\.)? [A-Z][a-z]+(?:-[A-Z][a-z]+)?)\b")
# Overlapping variant (lookahead): in text like "WebDesign Studio Dana
# Whitfield, Executive Director", non-overlapping matching consumes
# "Studio Dana" and the real "Dana Whitfield" never becomes a candidate.
NAME_RE_OVERLAP = re.compile(
    r"(?=\b([A-Z][a-z]+(?: [A-Z]\.)? [A-Z][a-z]+(?:-[A-Z][a-z]+)?)\b)")

_LOWER_TITLE_WORDS = {"of", "and", "for", "the", "in"}

# Words that disqualify a NAME_RE candidate from being a real person's name.
# Found via audit: "Director Ron", "Highland Avenue", "Member Benefits",
# "Certification Resources", "Oregon Community", "Community Center", and
# "Consumer Voice" all passed the old regex-only check because ANY two
# consecutive Title-Case words within 120 chars of a title match qualified
# — nav links, address fragments, and section headers all match that shape.
#
# Two categories, both real failure modes seen in production:
#  1. Title/role words — a SECOND, unrelated mention of "Director" etc.
#     elsewhere on the page (not the one TITLE_RE just matched) can itself
#     get captured as if it were a first name ("Director Ron").
#  2. Institutional/navigational/address nouns — "Benefits", "Resources",
#     "Community", "Avenue" etc. are page-chrome words, never surnames.
_NAME_DISQUALIFYING_WORDS = {
    # role/title words (catches a stray second title mention)
    "director", "manager", "coordinator", "specialist", "administrator",
    "president", "chief", "officer", "executive", "chair", "chairman",
    "chairwoman", "vice", "senior", "associate", "assistant", "deputy",
    "founder", "head", "lead", "supervisor", "board",
    # institutional / navigational / address nouns
    "avenue", "street", "road", "boulevard", "drive", "suite", "floor",
    "building", "room", "community", "center", "centre", "resources",
    "resource", "benefits", "benefit", "services", "service", "program",
    "programs", "department", "committee", "office", "bureau", "division",
    "section", "team", "group", "network", "alliance", "coalition",
    "council", "institute", "foundation", "society", "association",
    "union", "voice", "media", "news", "press", "policy", "advocacy",
    "membership", "registration", "certification", "accreditation",
    "compliance", "training", "education", "learning", "development",
    "support", "help", "contact", "about", "home", "main", "general",
    "national", "state", "regional", "local", "chapter", "member",
    "members", "consumer", "consumers",
}


def _looks_like_person_name(candidate: str) -> bool:
    words = candidate.lower().split()
    return not any(w in _NAME_DISQUALIFYING_WORDS for w in words)


def smart_title(text: str) -> str:
    """'director of certification' -> 'Director of Certification'
    (str.title() produced 'Director Of Certification' in the CSV)."""
    words = text.split()
    out = []
    for i, w in enumerate(words):
        lw = w.lower()
        out.append(lw if i > 0 and lw in _LOWER_TITLE_WORDS else lw.capitalize())
    return " ".join(out)


def _normalize_email(raw: str, org_domain: str) -> str | None:
    """Decode, sanitize, and validate one raw email candidate. Returns a
    clean lowercase address or None. Enforces:
      * URL-decoding (mailto hrefs and encoded artifacts like %E2%80%A8)
      * strict ASCII email shape via fullmatch after trimming junk
      * excluded dead inboxes (no-reply@, webmaster@, ...) dropped
      * SAME-DOMAIN rule: the address must belong to the org's own root
        domain. Cross-domain addresses on org pages are almost always a
        web designer's credit link or an embedded platform vendor
        ('hello@rfuenzalida.com', 'support@costech.com' in the v5 audit)
        — surfacing them as the org's contact is worse than nothing.
    """
    if not raw:
        return None
    e = unquote(raw).strip()
    e = e.split("?")[0]                      # mailto:x@y.org?subject=...
    # trim any leading/trailing junk that isn't part of an address, then
    # require the WHOLE remainder to be a well-formed address
    m = STRICT_EMAIL_RE.search(e)
    if not m:
        return None
    e = m.group(0).lower()
    if not STRICT_EMAIL_RE.fullmatch(e):
        return None
    local, _, edomain = e.partition("@")
    key = local.replace(".", "").replace("-", "").replace("_", "")
    if key in EXCLUDED_LOCALPARTS or local in EXCLUDED_LOCALPARTS:
        return None
    if e.endswith((".png", ".jpg", ".gif", ".svg", ".webp")):
        return None
    if edomain in ("example.com", "example.org", "example.net") \
            or "sentry" in edomain or "wixpress" in edomain:
        return None
    if root_domain("https://" + edomain) != org_domain:
        return None
    return e


def extract_emails(html: str, soup, org_domain: str) -> list[str]:
    """All valid same-domain emails on a page, best-source-first:
    mailto: hrefs (most reliable, where most real staff emails live),
    then regex over visible text and raw HTML, then de-obfuscated
    'name [at] domain [dot] org' patterns."""
    found: list[str] = []

    def add(raw):
        e = _normalize_email(raw, org_domain)
        if e and e not in found:
            found.append(e)

    for a in soup.select('a[href^="mailto:" i]'):
        add(a.get("href", "")[7:])
    text = soup.get_text(" ", strip=True)
    for raw in EMAIL_RE.findall(text):
        add(raw)
    for raw in EMAIL_RE.findall(html):
        add(raw)
    deobf = text
    for rx, rep in DEOBF_RE:
        deobf = rx.sub(rep, deobf)
    if deobf != text:
        for raw in EMAIL_RE.findall(deobf):
            add(raw)
    return found[:25]


def classify_email(email: str) -> str:
    local = email.split("@")[0].lower().replace(".", "").replace("-", "")
    if local in {g.replace("-", "") for g in GENERIC_LOCALPARTS}:
        return "generic"
    if local in ROLE_LOCALPARTS:
        return "role"
    return "named"          # looks like a person (jsmith, jane.doe, ...)


def _email_matches_name(email: str, name: str) -> bool:
    local = email.split("@")[0].lower()
    parts = [p.lower() for p in re.split(r"[ .]", name) if len(p) > 1]
    if not parts:
        return False
    first, last = parts[0], parts[-1]
    forms = {f"{first}.{last}", f"{first}{last}", f"{first[0]}{last}",
             f"{first}.{last[0]}", f"{first}_{last}", first, last}
    return local in forms or last in local


def search_urls(company_name: str):
    li = ("https://www.linkedin.com/search/results/people/?keywords="
          + quote_plus(f'{company_name} "learning and development" OR '
                       f'"training" OR "certification" OR "education" director OR manager'))
    gg = ("https://www.google.com/search?q="
          + quote_plus(f'"{company_name}" ("director of learning" OR "director of education" '
                       f'OR "training manager" OR "certification manager") email'))
    return li, gg


def enrich_contact(domain: str, company_name: str, max_contacts: int = 1) -> dict:
    """Best-effort single best contact for a validated company."""
    li_url, gg_url = search_urls(company_name)
    base = {"contact_name": "", "contact_title": "", "contact_email": "",
            "contact_source_url": "", "linkedin_search_url": li_url,
            "google_search_url": gg_url}

    named_hit = None      # (name, title, email or "", source_url)
    role_email = None     # (email, source_url)
    generic_email = None  # (email, source_url)
    pages_tried = 0
    attempts = 0

    for path in CONTACT_PATHS:
        if named_hit and named_hit[2]:
            break                                # best possible: stop early
        # Runtime control: each miss on a slow/unresponsive site costs a
        # full timeout (x retries, x https->http fallback). 17 paths at
        # 10s each was the dominant cost of a 2-hour production run.
        # Cap attempts and use the shorter contact timeout. Early-stop
        # only when a ROLE email is already in hand — a merely-generic
        # email must not cut short the search for named people (that
        # mistake zeroed out named-contact yield in the v4 run).
        attempts += 1
        if attempts > config.MAX_CONTACT_PAGES:
            break
        if attempts > config.MAX_CONTACT_PAGES // 2 and role_email:
            break
        url = urljoin(f"https://{domain}", path)
        html = fetch_page(url, timeout=config.CONTACT_HTTP_TIMEOUT)
        if not html:
            continue
        pages_tried += 1
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(" ", strip=True)
        emails = extract_emails(html, soup, domain)

        # 1) named person with a buyer title near their name. Staff
        # directories list people both ways ("Jane Smith, Training
        # Manager" and "Training Manager: Jane Smith") so search a
        # window on both sides of the title match, not just after it.
        if not named_hit:
            for m in TITLE_RE.finditer(text):
                window = text[max(0, m.start() - 120): m.end() + 120]
                offset = max(0, m.start() - 120)
                title_lo, title_hi = m.start() - offset, m.end() - offset
                # Overlapping candidates (see NAME_RE_OVERLAP note), minus
                # anything overlapping the title span itself ("Certification
                # Program" is two capitalized words too).
                candidates_nm = [c for c in NAME_RE_OVERLAP.finditer(window)
                                 if (c.end(1) <= title_lo or c.start(1) >= title_hi)
                                 and _looks_like_person_name(c.group(1))]
                if not candidates_nm:
                    continue
                candidates_nm.sort(key=lambda x: min(abs(x.start(1) - title_hi),
                                                     abs(title_lo - x.end(1))))
                # Prefer the candidate whose email exists on this page —
                # strongest possible person<->email linkage; fall back to
                # the nearest name to the title.
                nm, email = candidates_nm[0], ""
                for c in candidates_nm:
                    e = next((e for e in emails
                              if _email_matches_name(e, c.group(1))), "")
                    if e:
                        nm, email = c, e
                        break
                name, title = nm.group(1), smart_title(m.group(0))
                named_hit = (name, title, email, url)
                if email:
                    break

        # 2) role / generic inboxes as fallback
        for e in emails:
            kind = classify_email(e)
            if kind == "role" and not role_email:
                role_email = (e, url)
            elif kind == "generic" and not generic_email:
                generic_email = (e, url)
            elif kind == "named" and named_hit and not named_hit[2] \
                    and _email_matches_name(e, named_hit[0]):
                named_hit = (named_hit[0], named_hit[1], e, url)

    # ---- honest labeling ----
    if named_hit:
        name, title, email, src = named_hit
        base.update(contact_name=name, contact_title=title,
                    contact_source_url=src)
        if email:
            base.update(contact_email=email,
                        contact_status="Named Buyer Email",
                        email_confidence="high_verified_named"
                        if _email_matches_name(email, name)
                        else "medium_likely_named")
            return base
        if role_email:
            base.update(contact_email=role_email[0],
                        contact_status="Named Buyer - No Email",
                        email_confidence="medium_role_based",
                        notes_hint=f"Role inbox {role_email[0]} available as fallback.")
            return base
        base.update(contact_status="Named Buyer - No Email",
                    email_confidence="none")
        return base

    if role_email:
        base.update(contact_email=role_email[0], contact_source_url=role_email[1],
                    contact_status="Role-Based Email",
                    email_confidence="medium_role_based")
        return base

    if generic_email:
        base.update(contact_email=generic_email[0],
                    contact_source_url=generic_email[1],
                    contact_status="Generic Email Only",
                    email_confidence="low_generic")
        return base

    base.update(contact_status="Manual Research Needed" if pages_tried
                else "No Contact Found",
                email_confidence="none")
    return base