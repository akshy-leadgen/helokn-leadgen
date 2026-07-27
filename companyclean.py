"""
companyclean.py — Stage 3: validate the ORGANIZATION before spending
anything on contacts. Costs 0 Serper credits: one plain HTTP fetch of
the candidate's homepage.

What the homepage proves that a search snippet cannot:
  * the domain is alive and belongs to a real organization
  * the organization's REAL name (og:site_name / schema.org / <title>)
    — never the search-result page title
  * whether the org is a vendor (sells LMS/compliance software) or a
    publisher/consultancy rather than a plausible Helokn buyer
  * additional trigger evidence from the org's own words

Fetched homepages are cached to cache/pages/ so re-runs are free and fast.
"""

import hashlib
import json
import re

import requests
from bs4 import BeautifulSoup

import config
from junkfilter import extract_triggers, looks_like_article_title

PAGE_CACHE = config.CACHE_DIR / "pages"

VENDOR_PAGE_RE = re.compile(
    r"\b(request a demo|book a demo|start (your )?free trial|see it in action|"
    r"our (platform|software|lms)|pricing|per (user|seat|learner)|"
    r"all-in-one (training|compliance|learning) (platform|solution|software)|"
    r"training solutions? for|compliance solutions? for|course library|"
    r"content library|white-?label|key features|"
    r"(competency|talent|workforce|learning) management (software|platform|system)|"
    r"integrates? with|book a (call|meeting)|trusted by \d|customers? love|"
    r"g2 (leader|rating)|capterra|software advice)\b", re.I)

CONSULTING_RE = re.compile(r"\b(consulting|consultants?|advisory|advisors)\b", re.I)

BUYER_PAGE_RE = re.compile(
    r"\b(our (certification|credential|members|courses|programs|academy|students|learners)|"
    r"become (a member|certified)|earn your|apply now|enroll|register for|"
    r"accredited provider|approved provider|candidate handbook|"
    r"recertification|contact hours|ce credits|cme|cle)\b", re.I)


def _cache_path(url: str):
    return PAGE_CACHE / (hashlib.sha256(url.encode()).hexdigest()[:24] + ".json")


def fetch_page(url: str, timeout: int | None = None) -> str | None:
    """Fetch a page with disk cache. Returns HTML text or None.

    Tries https first; if the URL is a bare-https homepage that fails,
    falls back to http:// once — a meaningful number of smaller org
    sites (the exact long tail this pipeline targets) still serve only
    http or have broken TLS, and each one recovered here is a lead that
    was previously lost to 'homepage_unreachable'.
    """
    PAGE_CACHE.mkdir(parents=True, exist_ok=True)
    cp = _cache_path(url)
    if cp.exists():
        return json.loads(cp.read_text()).get("html")
    timeout = timeout or config.HTTP_TIMEOUT

    attempts = [url]
    if url.startswith("https://"):
        attempts.append("http://" + url[len("https://"):])

    html = None
    for attempt_url in attempts:
        for _ in range(1 + config.HTTP_RETRIES):
            try:
                r = requests.get(attempt_url, timeout=timeout,
                                 headers={"User-Agent": config.USER_AGENT},
                                 allow_redirects=True)
                if r.ok and "text/html" in r.headers.get("content-type", "text/html"):
                    html = r.text[:500_000]
                    break
            except requests.RequestException:
                continue
        if html is not None:
            break
    cp.write_text(json.dumps({"html": html}))
    return html


def _name_from_domain(domain: str) -> str:
    stem = domain.rsplit(".", 1)[0].replace("-", " ").replace("_", " ")
    words = stem.split()
    if len(words) == 1 and len(words[0]) <= 6:
        return words[0].upper()          # acronym-ish: "mwcc" -> "MWCC"
    return " ".join(w.upper() if len(w) <= 3 else w.capitalize() for w in words)


_DOMAINLIKE_NAME_RE = re.compile(
    r"^(www\.)?[a-z0-9-]+\.(com|org|net|gov|edu|us|io|co)$", re.I)

# Page-chrome words that are never an org name on their own. These leaked
# into the CSV as full company names ("Search" from bcen.org, "Home" from
# lincolntech.edu) because og:site_name/schema values previously bypassed
# the cleaning that <title> got.
_CHROME_WORDS_RE = re.compile(
    r"^(home|homepage|welcome|website|site|portal|search|menu|index|"
    r"main page|official (web)?site|untitled)$", re.I)
# "Welcome to PRA" (real production example): a leading "Welcome to"/
# "Welcome to the" survives as company_name when the whole phrase has
# no separator (no "|"/"-"/":") for the segment-splitter in
# _clean_title_name to isolate "Welcome" as its own droppable segment —
# the combined phrase just reads as one ordinary-looking name. Stripped
# as its own step so the org name underneath ("PRA") still gets used.
_WELCOME_PREFIX_RE = re.compile(r"^welcome to (the )?", re.I)
# Unconditional chrome tails — no real org name ends in these:
_CHROME_TAIL_RE = re.compile(r"\s+(website|homepage|portal|web ?page)$", re.I)
# Conditional chrome tails — "Home"/"Site" CAN end a real name ("Sunrise
# Funeral Home"), so these are only stripped when what remains is a single
# acronym-like token ("NASW Home" -> "NASW", but "Sunrise Funeral Home"
# stays intact).
_CHROME_TAIL_RISKY_RE = re.compile(r"\s+(home|site)$", re.I)

_CORP_SUFFIX_RE = re.compile(r",\s*(inc|llc|ltd|l\.l\.c|p\.?c|p\.?a|co|corp)\b\.?", re.I)


def _looks_domainlike(name: str) -> bool:
    """Catch the 'www.aabb.org' / 'nj.gov' failure: the site's own <title>
    or og:site_name was literally a bare URL/domain string, not an org
    name. A raw name resolution can't be trusted if it IS the domain."""
    return bool(_DOMAINLIKE_NAME_RE.match((name or "").strip()))


def _sanitize_name(name: str | None) -> str | None:
    """Normalize a candidate org name from ANY source and return None if
    it isn't usable. Every name source (og:site_name, schema.org, <title>,
    <h1>) must pass through here — the v3 audit found 'CBDCE Website',
    'Search', 'Home', 'MONTANA', 'EduClasses | Online Courseware' and a
    ™-tagline all leaking specifically through og/schema, which skipped
    the title-only cleaning."""
    if not name:
        return None
    # collapse whitespace (fixes 'Educationfor theNext' style mangling)
    name = re.sub(r"\s+", " ", name).strip(" -|·•\u00a0")
    # "We Are the Voice ofAmerica's Banks" (real production example): the
    # source HTML had zero whitespace between "of" and "America's" at
    # all, so the collapse above has nothing to collapse — this is a
    # missing space, not excess whitespace. Narrow on purpose: only a
    # short list of common function words, so a legitimate camelCase
    # brand name doesn't get incorrectly split.
    name = re.sub(r"\b(of|the|and|in|on|for|to)([A-Z][a-z])", r"\1 \2", name)
    # trademark/registered marks are tagline indicators; strip the symbol
    # but remember we saw one — a ™'d string is a slogan, not an org name
    had_tm = "™" in name or "®" in name
    name = name.replace("™", "").replace("®", "").strip()
    if not name:
        return None
    # og:site_name sometimes contains full title-style separators
    # ("EduClasses | Online Courseware") — take the brand segment
    if re.search(r"\s[|–—•·]\s| - ", name):
        name = _clean_title_name(name) or ""
        if not name:
            return None
    name = _WELCOME_PREFIX_RE.sub("", name).strip()      # "Welcome to PRA" -> "PRA"
    name = _CHROME_TAIL_RE.sub("", name).strip()          # "... Homepage" -> "..."
    risky = _CHROME_TAIL_RISKY_RE.search(name)
    if risky:
        remainder = name[:risky.start()].strip()
        # strip "Home"/"Site" only when the rest is one acronym-ish token
        if remainder and " " not in remainder and (
                remainder.isupper() or len(remainder) <= 8):
            name = remainder
    if _CHROME_WORDS_RE.match(name):                       # "Search", "Home"
        return None
    # A bare domain string is a resolution failure ("www.aabb.org") —
    # UNLESS it carried a ™/®, which marks it as deliberate branding
    # (e.g. "RN.org®" really is that org's brand name).
    if _looks_domainlike(name) and not had_tm:
        return None
    # Single all-caps token: fine for acronyms (NBCC, ISACA, PTCB) but a
    # long one is usually a shouted word from a banner, not an acronym
    # ("MONTANA" from montanabar.org).
    if " " not in name and name.isupper() and len(name) > 6:
        return None
    # Tagline heuristics: a comma that isn't a corporate suffix, or a ™'d
    # multi-word phrase, is a slogan ("Daily Medical News, Free CME and
    # Clinical Guidance") — reject and let the next source try.
    if "," in name and not _CORP_SUFFIX_RE.search(name):
        return None
    if had_tm and len(name.split()) > 3:
        return None
    if looks_like_article_title(name):
        return None
    # A short single lowercase token is almost always an acronym scraped
    # in lowercase ("mwcc" -> "MWCC", "ahvap" -> "AHVAP")
    if " " not in name and name.islower() and len(name) <= 6:
        name = name.upper()
    return name


def _clean_title_name(title: str) -> str | None:
    """'Acme Certification Board | Home' -> 'Acme Certification Board'."""
    if not title:
        return None
    parts = re.split(r"\s*[|–—•·:]\s*| - ", title)
    parts = [p.strip() for p in parts if p.strip()]
    # Segments that describe the PAGE, not the organization — these were
    # leaking through as e.g. "CBDCE Website" or "NASW Home".
    drop = re.compile(r"^(home|homepage|welcome|website|site|portal|"
                      r"official (web)?site|main page)$", re.I)
    parts = [p for p in parts if not drop.match(p)]
    # Trailing/leading "Website"/"Home"/"Portal" tacked onto a real name
    # ("CBDCE Website" -> "CBDCE") also needs stripping within a segment.
    tail = re.compile(r"\s+(website|home|portal|site)$", re.I)
    parts = [tail.sub("", p).strip() for p in parts]
    parts = [p for p in parts if p and not _looks_domainlike(p)]
    if not parts:
        return None
    # The brand is almost always the FIRST segment ("Acme Board | Programs").
    # Only fall back to later segments if the first is article-like, and
    # skip generic descriptors that describe pages rather than orgs.
    generic = re.compile(r"^(training|programs?|courses?|education|services|"
                         r"solutions|resources|certification)s?"
                         r"( (programs?|courses?|center|hub))?$", re.I)
    for p in parts:
        if not looks_like_article_title(p) and not generic.match(p):
            return p
    return None


def _name_from_h1(soup) -> str | None:
    """Fallback: a page <h1> is often the org/brand name on simpler sites
    that skip og:site_name and schema.org markup entirely."""
    h1 = soup.find("h1")
    if not h1:
        return None
    text = re.sub(r"\s+", " ", h1.get_text(strip=True))
    if not text or looks_like_article_title(text) or _looks_domainlike(text):
        return None
    if len(text) > 60:
        return None
    return text


def resolve_company(candidate) -> dict:
    """
    Validate one candidate's organization.
    Returns dict:
      valid: bool
      company_name, website_url, homepage_triggers, buyer_signals: on success
      rejection_reason, explanation: on failure
    """
    website_url = f"https://{candidate.domain}"
    html = fetch_page(website_url)
    if html is None:
        return {"valid": False, "rejection_reason": "homepage_unreachable",
                "explanation": f"Could not fetch https://{candidate.domain} "
                               f"(dead domain, blocked, or timeout)."}

    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)[:20_000]

    # ---- vendor / consultancy classification on the org's OWN words ----
    vendor_hits = len(VENDOR_PAGE_RE.findall(text))
    buyer_hits = len(BUYER_PAGE_RE.findall(text))
    if vendor_hits >= 2 and vendor_hits > buyer_hits:
        return {"valid": False, "rejection_reason": "vendor_homepage",
                "explanation": "Homepage sells training/compliance software or "
                               f"content ({vendor_hits} vendor signals vs {buyer_hits} buyer signals)."}
    if CONSULTING_RE.search(text[:3000]) and buyer_hits == 0:
        return {"valid": False, "rejection_reason": "consulting_firm",
                "explanation": "Consulting/advisory firm with no evidence of "
                               "running large-scale training or certification programs."}

    # ---- real organization name (never the search-result title) ----
    # Priority: og:site_name -> schema.org Organization -> cleaned <title>
    # -> <h1> -> domain-derived. EVERY source passes through
    # _sanitize_name(): the v3 audit showed og/schema values leaking raw
    # ("Search", "Home", "MONTANA", "CBDCE Website", "EduClasses |
    # Online Courseware", ™-taglines) because they skipped the cleaning
    # that titles got. A source whose value fails sanitization simply
    # yields to the next source.
    name_from_markup = False  # True only for og:site_name / schema.org
    name = None
    og = soup.find("meta", property="og:site_name")
    if og and og.get("content"):
        name = _sanitize_name(og["content"])
        name_from_markup = name is not None
    if not name:
        for s in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(s.string or "")
            except (json.JSONDecodeError, TypeError):
                continue
            items = data if isinstance(data, list) else [data]
            for it in items:
                if isinstance(it, dict) and it.get("@type") in (
                        "Organization", "EducationalOrganization", "Corporation",
                        "NGO", "GovernmentOrganization", "LocalBusiness", "College",
                        "University") and it.get("name"):
                    name = _sanitize_name(str(it["name"]))
                    if name:
                        name_from_markup = True
                        break
            if name:
                break
    if not name and soup.title:
        # get_text(" ") not get_text(): child tags inside <title> were
        # concatenating without spaces ("Educationfor theNext Generation")
        name = _sanitize_name(_clean_title_name(
            soup.title.get_text(" ", strip=True)))
    if not name:
        name = _sanitize_name(_name_from_h1(soup))

    name_is_domain_guess = False
    if not name:
        # Last resort: derive from the domain stem ("iowabar.org" ->
        # "Iowabar"). Honest but ugly — flag it so the CSV notes column
        # tells sales to verify the name before outreach.
        name = _sanitize_name(_name_from_domain(candidate.domain))
        name_is_domain_guess = name is not None
        if not name:
            return {"valid": False, "rejection_reason": "no_real_company_name",
                    "explanation": "Could not resolve a real organization name "
                                   "from the homepage."}

    # A bare .gov TLD is often a huge multi-department state/federal portal,
    # not one coherent organization — the search hit may be one page buried
    # inside it (e.g. nj.gov). Only accept .gov when we resolved a real,
    # specific name from structured markup (not a title/h1/domain guess),
    # which is what a specific board/agency subsite provides.
    if candidate.domain.endswith(".gov") and not name_from_markup:
        return {"valid": False, "rejection_reason": "gov_portal_unresolved",
                "explanation": f"{candidate.domain} is a .gov domain and no "
                               "specific agency/board name could be confirmed "
                               "from structured page data — likely a broad "
                               "government portal, not one organization."}

    return {
        "valid": True,
        "company_name": name,
        "website_url": website_url,
        "homepage_triggers": extract_triggers(text),
        "buyer_signals": buyer_hits,
        "name_is_domain_guess": name_is_domain_guess,
    }