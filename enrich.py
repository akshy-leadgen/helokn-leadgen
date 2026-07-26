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
# positions 6-8, behind an early-stop at position 5. v7: added faculty/
# teacher-directory paths (schools rarely use "/team" but very commonly
# use "/faculty" or "/staff-directory") right after /staff, for the same
# reason — and general hit-rate: these paths help non-school leads too
# wherever an org calls its people page something other than "/team".
CONTACT_PATHS = ["/contact", "/about", "/team", "/leadership", "/staff",
                 "/faculty", "/faculty-directory", "/teachers", "/our-staff",
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
    # school-admin titles (org-level fallback contact when no individual
    # teacher is found — the actual purchase decision for a K-12 tool
    # often sits with these roles even though teachers are the end users)
    "principal", "assistant principal", "vice principal",
    "curriculum director", "instructional coach",
    "director of curriculum and instruction", "edtech coordinator",
    "director of technology", "superintendent",
]
TITLE_RE = re.compile("|".join(re.escape(t) for t in PRIORITY_TITLES), re.I)

# Individual-educator titles — deliberately SEPARATE from PRIORITY_TITLES
# above (org-level gatekeepers). Used only by enrich_teacher_contacts()
# for the "find teacher emails" (plural, front-line staff) feature.
# Grade/subject vocabulary is explicit (not "any word + teacher") so the
# extracted title doesn't swallow filler words like "Our"/"And" from the
# surrounding sentence — a bare "Teacher" with no prefix still matches
# fine since both prefix groups are optional.
_GRADE_WORDS = (r"pre-?k|kindergarten|\d{1,2}(?:st|nd|rd|th)?\s*grade|"
               r"elementary|middle school|high school|primary|secondary")
_SUBJECT_WORDS = (r"math(?:ematics)?|science|english|language arts|ela|"
                  r"history|social studies|\bart\b|music|"
                  r"physical education|\bpe\b|special education|\bsped\b|"
                  r"\besl\b|\bell\b|reading|spanish|french|technology|"
                  r"computer science|health")
TEACHER_TITLE_RE = re.compile(
    rf"\b(?:(?:{_GRADE_WORDS})\s+)?(?:(?:{_SUBJECT_WORDS})\s+)?"
    rf"(?:classroom\s+|lead\s+)?teachers?\b|"
    r"\binstructors?\b|\beducators?\b|\bteaching assistants?\b", re.I)

NAME_RE = re.compile(r"\b([A-Z][a-z]+(?: [A-Z]\.)? [A-Z][a-z]+(?:-[A-Z][a-z]+)?)\b")
# Overlapping variant (lookahead): in text like "WebDesign Studio Dana
# Whitfield, Executive Director", non-overlapping matching consumes
# "Studio Dana" and the real "Dana Whitfield" never becomes a candidate.
NAME_RE_OVERLAP = re.compile(
    r"(?=\b([A-Z][a-z]+(?: [A-Z]\.)? [A-Z][a-z]+(?:-[A-Z][a-z]+)?)\b)")

# "Last, First" format — staff directories are very commonly sorted
# alphabetically by last name and rendered exactly this way ("Mora,
# Sara"), confirmed from real production evidence where a school's
# staff directory clearly listed teachers this way and NONE were ever
# found, because NAME_RE/NAME_RE_OVERLAP only ever matched the "First
# Last" (space-separated) shape. This was very likely the single
# biggest reason teacher-email yield was near zero, more than any
# blocklist gap.
#
# The negative lookahead (?!\s[A-Z]) matters: without it, the extremely
# common "Name, Job Title" convention ("Marcus Bell, Math Teacher")
# is structurally identical to "Last, First" and gets misread as a
# person named "Math Bell" — found via a real regression during testing.
# Requiring the "first name" part NOT be followed by another capitalized
# word rules out "JobTitle ContinuesHere" while still matching a real
# "Mora, Sara." (followed by a period, not another capitalized word).
_LASTFIRST_RE = re.compile(r"\b([A-Z][a-z]+(?:-[A-Z][a-z]+)?), ([A-Z][a-z]+)\b(?!\s[A-Z])")


def _gather_name_candidates(window: str) -> list[tuple[str, int, int]]:
    """Every plausible person-name span in window, from BOTH supported
    formats, normalized to a single (display_name, start, end) shape so
    callers don't need to know which pattern matched. Display name is
    always 'First Last' order, even when the source text was 'Last,
    First' — reordered here, once, rather than making every caller
    handle both orderings."""
    out = [(c.group(1), c.start(1), c.end(1)) for c in NAME_RE_OVERLAP.finditer(window)]
    for m in _LASTFIRST_RE.finditer(window):
        last, first = m.group(1), m.group(2)
        out.append((f"{first} {last}", m.start(), m.end()))
    return out


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
    "founder", "head", "lead", "supervisor", "board", "superintendent",
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
    # school-context nav/section words (same failure pattern as
    # "Highland Avenue" etc., now on school sites: "Elementary School",
    # "Faculty Directory", "Classroom News" read as if two-word names)
    "elementary", "middle", "high", "school", "schools", "classroom",
    "classrooms", "faculty", "homeroom", "district", "campus",
    "students", "student", "parents", "parent", "grades", "grade",
    "kindergarten", "preschool", "academics", "curriculum",
    # v8: two real production fake names — "Achievement Report" and
    # "It Fast" — both from school-district site nav/document links near
    # a "Superintendent" title mention. "bell" deliberately NOT here —
    # it collided with the common surname "Bell" ("Marcus Bell") and
    # cost a real teacher lead; "schedule" alone still catches "Bell
    # Schedule" style nav phrases without the collision.
    "achievement", "report", "reports", "fast", "facts", "results",
    "summary", "overview", "directory", "listing", "download", "upload",
    "calendar", "menu", "lunch", "transportation", "athletics",
    "activities", "announcement", "announcements", "handbook",
    "enrollment", "boundaries", "information", "welcome", "click",
    "here", "learn", "more", "visit", "explore", "portal", "login",
    "schedule", "form", "forms", "application", "applications",
    "first", "last", "name", "location", "position", "alphabetically",
    # v9: more real production fake names — "Fraud Magazine" (a
    # publication name, not a person), "Medication Therapy" (a
    # substring of the org's OWN name, "...Medication Therapy
    # Management"), "Email Us" (a call-to-action link), "Recertification
    # My" (nav-text fragment, likely "My Account"), "Early Childhood"
    # (a program-name fragment) and "Find It" (a "Find It Fast" site-
    # search widget — the exact same widget behind the earlier "It
    # Fast" false positive, seen again on a different district's site,
    # confirming it's boilerplate shared across many school-district
    # website templates, not a one-off).
    "magazine", "journal", "bulletin", "newsletter", "update", "updates",
    "therapy", "medication", "management", "email", "us", "my",
    "account", "signin", "subscribe", "recertification", "find", "it",
    "early", "childhood",
    # v10: third round of real production fake names.
    # "Esq Suchocki" — a legal title suffix ("Esq.") following a comma
    # after a real surname is structurally identical to "Last, First"
    # (both are "Capitalized, Capitalized"), so it gets misread as if
    # "Esq" were a first name. Blocking common professional-title
    # abbreviations closes this whole class rather than one instance;
    # jr/sr/ii/iii/iv/md/jd/phd/cpa are the same failure shape.
    "esq", "jr", "sr", "ii", "iii", "iv", "md", "jd", "phd", "cpa",
    # "Review Course", "By Mail", "Content Assistance", "Criminal Law",
    # "Physician Lookup", "Los Angeles", "Nadeau Consultants",
    # "Emergency Medical" — a course/product name, a payment-method
    # instruction, a support link, a bar-association practice-area
    # section, a directory tool, a city name, a firm-descriptor word,
    # and EMS terminology, respectively. None are people.
    "review", "course", "courses", "by", "mail", "content", "assistance",
    "criminal", "physician", "lookup", "los", "angeles", "consultant",
    "consultants", "emergency", "medical",
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


def _find_all_named_contacts(text: str, emails: list[str],
                             title_re: re.Pattern) -> list[tuple[str, str, str]]:
    """Every (name, smart-cased title, matched email or '') found in text
    for the given title regex — one entry per title occurrence, in
    document order, not stopping at the first. Shared building block for
    both enrich_contact's single-best-contact search and
    enrich_teacher_contacts' collect-everything search."""
    results = []
    for m in title_re.finditer(text):
        window = text[max(0, m.start() - 120): m.end() + 120]
        offset = max(0, m.start() - 120)
        title_lo, title_hi = m.start() - offset, m.end() - offset
        # Candidates from BOTH "First Last" and "Last, First" formats,
        # minus anything overlapping the title span itself ("Certification
        # Program" is two capitalized words too).
        candidates_nm = [c for c in _gather_name_candidates(window)
                         if (c[2] <= title_lo or c[1] >= title_hi)
                         and _looks_like_person_name(c[0])]
        if not candidates_nm:
            continue
        candidates_nm.sort(key=lambda x: min(abs(x[1] - title_hi),
                                             abs(title_lo - x[2])))
        # Prefer the candidate whose email exists on this page — strongest
        # possible person<->email linkage; fall back to nearest name.
        nm, email = candidates_nm[0], ""
        for c in candidates_nm:
            e = next((e for e in emails if _email_matches_name(e, c[0])), "")
            if e:
                nm, email = c, e
                break
        results.append((nm[0], smart_title(m.group(0)), email))
    return results


def _find_bare_name_email_pairs(text: str, emails: list[str]) -> list[tuple[str, str]]:
    """Catches the common case a title-based search misses entirely: a
    small org's informal 'Our Team' page listing just 'Jane Smith —
    jsmith@org.com' with no job title at all. Only called on pages
    already in CONTACT_PATHS (i.e. already trusted to be about people,
    not random content), and requires an individually-shaped email
    (classify_email == 'named') within a TIGHT window of the name — a
    smaller window than the title-search uses, since there's no title
    keyword here to anchor the proximity check, so caution matters more.
    Returns (name, email) pairs, empty title (honestly, since none was
    found — see enrich_contact's email_confidence choice for this path).
    """
    results = []
    for e in emails:
        if classify_email(e) != "named":
            continue
        idx = text.lower().find(e.lower())
        if idx == -1:
            continue
        window = text[max(0, idx - 40): idx + len(e) + 40]
        offset = max(0, idx - 40)
        e_lo, e_hi = idx - offset, idx - offset + len(e)
        candidates_nm = [c for c in _gather_name_candidates(window)
                         if (c[2] <= e_lo or c[1] >= e_hi)
                         and _looks_like_person_name(c[0])]
        if not candidates_nm:
            continue
        candidates_nm.sort(key=lambda x: min(abs(x[1] - e_hi), abs(e_lo - x[2])))
        nm = candidates_nm[0]
        if _email_matches_name(e, nm[0]):
            results.append((nm[0], e))
    return results


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
            for name, title, email in _find_all_named_contacts(text, emails, TITLE_RE):
                named_hit = (name, title, email, url)
                if email:
                    break

        # 1.5) no title match on this page at all? Common on small orgs'
        # informal "Our Team" pages that just list "Jane Smith —
        # jsmith@org.com" with no job title. Title-based search above
        # finds nothing in that case; without this, the email is simply
        # discarded — a real, avoidable contributor to low hit rate.
        # Confidence capped at medium (not high) since there's no title
        # confirming this person is actually in a relevant role.
        if not (named_hit and named_hit[2]):
            for name, email in _find_bare_name_email_pairs(text, emails):
                named_hit = (name, "", email, url)
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
            # high_verified_named requires BOTH a matching email format AND
            # a confirmed buyer title — a title-less match (the bare-name-
            # email fallback above) can be just as sure the ADDRESS is
            # correct, but has no confirmation this person is in a
            # relevant role at all, so it's honestly capped at medium.
            confidence = ("high_verified_named"
                         if title and _email_matches_name(email, name)
                         else "medium_likely_named")
            base.update(contact_email=email, contact_status="Named Buyer Email",
                        email_confidence=confidence)
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


def enrich_teacher_contacts(domain: str, company_name: str,
                            max_contacts: int = 5) -> tuple[list[dict], int]:
    """Find UP TO max_contacts individual teacher/educator contacts for a
    school. Unlike enrich_contact (one best org-level gatekeeper), 'find
    teacher emails' means plural front-line staff, not one administrator
    — so this collects as many as it can, deduped, instead of stopping
    at the first.

    Conservative by design: a match is only kept if it has an ACTUAL
    matched email next to it. TEACHER_TITLE_RE is deliberately loose
    (a bare "Teacher" can match ordinary prose, e.g. "every teacher at
    our school..."), so requiring a real matched email is what keeps a
    stray marketing-copy mention from becoming a fabricated contact row
    — the same lesson as the v6 fake-name audit, applied here up front
    rather than after the fact.

    Returns (contacts, pages_tried) — contacts is a list of dicts each
    shaped like enrich_contact's return value, one per teacher found.
    """
    li_url, gg_url = search_urls(company_name)
    found: list[dict] = []
    seen_keys: set[tuple[str, str]] = set()
    pages_tried = 0

    for path in CONTACT_PATHS:
        if len(found) >= max_contacts:
            break
        if len(found) >= max(1, config.MAX_CONTACT_PAGES // 3) and pages_tried >= 4:
            # Modest early-stop once we already have a reasonable haul and
            # have tried several pages — avoids burning the full page
            # budget on a school that's clearly already yielded results,
            # leaving more budget for schools that haven't yielded any yet.
            break
        url = urljoin(f"https://{domain}", path)
        html = fetch_page(url, timeout=config.CONTACT_HTTP_TIMEOUT)
        if not html:
            continue
        pages_tried += 1
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(" ", strip=True)
        emails = extract_emails(html, soup, domain)

        for name, title, email in _find_all_named_contacts(text, emails, TEACHER_TITLE_RE):
            if not email:
                continue    # see docstring: no email, no row — too noisy otherwise
            key = (name.lower(), email.lower())
            if key in seen_keys:
                continue
            seen_keys.add(key)
            found.append({
                "contact_name": name,
                "contact_title": title,
                "contact_email": email,
                "email_confidence": "high_verified_named"
                    if _email_matches_name(email, name) else "medium_likely_named",
                "contact_status": "Named Buyer Email",
                "contact_source_url": url,
                "linkedin_search_url": li_url,
                "google_search_url": gg_url,
            })
            if len(found) >= max_contacts:
                break

    return found, pages_tried