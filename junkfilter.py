"""
junkfilter.py — Stage 2: throw away junk BEFORE spending any effort.

Everything here is zero-cost (string rules only, no network). The goal
is to reject the failure modes of the old pipeline explicitly, and to
log a rejection_reason for every discard so the run is auditable.

Order of checks per raw search result:
  1. domain blocklist        (social media, job boards, publishers, ...)
  2. known-vendor blocklist  (LMS / compliance-software sellers)
  3. junk URL path           (blog/news/jobs/tag/category/...)
  4. article-style title     (How to..., Top 10..., Guide..., ...)
  5. trigger extraction      (strong vs weak signals)
  6. weak-only rejection     ("mentions compliance" is not a lead)

A result may carry a junk *path* but a strong trigger on a plausible
org domain (e.g. acmecertboard.org/blog/exam-updates). We keep those as
candidates with evidence_weak=True — the organization gets validated
against its homepage in the next stage, never against the article.
"""

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# 1. Domains that can never be a lead (aggregators, media, social, jobs)
# ---------------------------------------------------------------------------
BLOCKED_DOMAINS = {
    # social / video / community
    "linkedin.com", "facebook.com", "twitter.com", "x.com", "youtube.com",
    "instagram.com", "reddit.com", "quora.com", "medium.com", "tiktok.com",
    "pinterest.com", "slideshare.net", "substack.com",
    # job boards
    "indeed.com", "glassdoor.com", "ziprecruiter.com", "monster.com",
    "simplyhired.com", "careerbuilder.com", "lever.co", "greenhouse.io",
    "workday.com", "myworkdayjobs.com", "jobapscloud.com", "builtin.com",
    "dice.com", "idealist.org", "higheredjobs.com",
    # wikis / reference / academia aggregators
    "wikipedia.org", "wikihow.com", "investopedia.com", "britannica.com",
    "sciencedirect.com", "springer.com", "onlinelibrary.wiley.com",
    "researchgate.net", "jstor.org", "nih.gov", "ncbi.nlm.nih.gov",
    "arxiv.org", "ssrn.com", "academia.edu", "scholar.google.com",
    "pubmed.gov", "doaj.org",
    # media / publishers
    "forbes.com", "hbr.org", "nytimes.com", "wsj.com", "reuters.com",
    "bloomberg.com", "businessinsider.com", "inc.com", "entrepreneur.com",
    "fastcompany.com", "shrm.org", "hrdive.com", "edsurge.com",
    "insidehighered.com", "chronicle.com", "prnewswire.com",
    "businesswire.com", "globenewswire.com", "yahoo.com", "msn.com",
    "cuinsight.com", "ltcnews.com", "beckershospitalreview.com",
    "fiercehealthcare.com", "healthcaredive.com", "law.com", "abajournal.com",
    "healio.com", "medscape.com", "medpagetoday.com",
    "chieftalentofficer.co", "clomedia.com", "talentmgt.com",
    # directories / review sites / marketplaces
    "yelp.com", "g2.com", "capterra.com", "getapp.com", "softwareadvice.com",
    "trustpilot.com", "clutch.co", "crunchbase.com", "zoominfo.com",
    "bloomberglaw.com", "yellowpages.com", "bbb.org", "mapquest.com",
    "manta.com", "dnb.com", "chamberofcommerce.com", "thumbtack.com",
    "coursera.org", "udemy.com", "edx.org", "classcentral.com",
    "seamless.ai", "apollo.io", "rocketreach.co", "signalhire.com",
    # misc utility
    "archive.org", "scribd.com", "issuu.com", "docslib.org",
    "studocu.com", "coursehero.com", "chegg.com",
}

# ---------------------------------------------------------------------------
# 2. Known vendors: sell LMS / compliance / training SOFTWARE or content
#    libraries to the same buyers Helokn wants. They are competitors or
#    adjacent tools, never prospects. (Extend freely — every name added
#    here saves credits and reviewer time.)
# ---------------------------------------------------------------------------
VENDOR_DOMAINS = {
    "skillsoft.com", "cornerstoneondemand.com", "docebo.com", "litmos.com",
    "absorblms.com", "talentlms.com", "learnupon.com", "360learning.com",
    "ispringsolutions.com", "articulate.com", "adobe.com", "sap.com",
    "workramp.com", "lessonly.com", "seismic.com", "mindtickle.com",
    "navexglobal.com", "navex.com", "traliant.com", "kantola.com",
    "everfi.com", "emtrain.com", "ethena.co", "hsi.com", "vectorsolutions.com",
    "complianceonline.com", "lorman.com", "blr.com", "jjkeller.com",
    "skillcast.com", "knowbe4.com", "proofpoint.com", "infosecinstitute.com",
    "relias.com", "healthstream.com", "medbridge.com", "symplr.com",
    "cebroker.com", "propelus.com", "evercheck.com",
    "examsoft.com", "proctoru.com", "meazurelearning.com", "prometric.com",
    "pearsonvue.com", "psiexams.com", "kryteriononline.com", "questionmark.com",
    "surpass.com", "risr.global", "certiverse.com", "caveon.com",
    "accredible.com", "credly.com", "badgr.com", "sertifier.com",
    "thoughtindustries.com", "skilljar.com", "northpass.com", "intellum.com",
    "amlwatcher.com", "complyadvantage.com", "trinet.com", "adp.com",
    "paychex.com", "bamboohr.com", "gusto.com", "zenefits.com",
    "xtramile.no", "allgravy.com", "docupace.com", "cliniciannexus.com",
    "centranum.com", "redvector.com", "winlearning.com",
}

# Homepage/vendor language checked later in companyclean.py too, but the
# snippet-level version catches obvious cases for free:
VENDOR_SNIPPET_RE = re.compile(
    r"\b(request a demo|book a demo|free trial|our platform|our software|"
    r"lms software|training software|compliance software|elearning platform|"
    r"pricing plans|per user per month|all-in-one platform)\b",
    re.I,
)

# ---------------------------------------------------------------------------
# 3b. Content-site DOMAIN names. The path rules below catch content pages on
#     an org's site, but if the domain itself is named for content — a forum,
#     a podcast, a blog network — the whole ORGANIZATION is a content site,
#     not a buyer ("degreeforum.net", "project-management-podcast.com" both
#     leaked into leads in the v4 audit). 'forum'/'podcast' are safe as
#     substrings; 'blog'/'news'/'wiki'/'jobs' only as hyphen/underscore-
#     bounded tokens so real names like newschool.edu don't false-positive.
# ---------------------------------------------------------------------------
_CONTENT_DOMAIN_SUBSTRINGS = ("forum", "podcast")
_CONTENT_DOMAIN_TOKEN_RE = re.compile(
    r"(^|[-_])(blog|news|wiki|jobs|magazine|journal)s?([-_]|$)", re.I)


def is_content_site_domain(domain: str) -> bool:
    stem = domain.rsplit(".", 1)[0].lower()
    if any(s in stem for s in _CONTENT_DOMAIN_SUBSTRINGS):
        return True
    return bool(_CONTENT_DOMAIN_TOKEN_RE.search(stem))


# ---------------------------------------------------------------------------
# 4. URL paths that indicate content, not an organization page
# ---------------------------------------------------------------------------
JUNK_PATH_RE = re.compile(
    r"/(blog|news|article|articles|post|posts|press|press-release|media|"
    r"resources/blog|insights|stories|tag|tags|category|categories|archive|"
    r"archives|glossary|wiki|whitepaper|white-papers|ebook|webinar|podcast|"
    r"event|events|job|jobs|career|careers|vacancy|vacancies|openings|"
    r"newsletter|faq)s?(/|$|\?)",
    re.I,
)

# ---------------------------------------------------------------------------
# 4. Article-style titles (the old pipeline's "company names")
# ---------------------------------------------------------------------------
ARTICLE_TITLE_RES = [
    re.compile(r"^\s*(how|why|what|when|where|which|who|should|can|do|does|is|are)\b", re.I),
    re.compile(r"^\s*(top|best|essential|ultimate|complete|comprehensive|key|free)\b", re.I),
    re.compile(r"^\s*\d+\s+\w+", re.I),                       # "8 Things to Know..."
    re.compile(r"\b(guide|checklist|best practices?|tips|tricks|strategies|"
               r"trends|q&a|vs\.?|versus|explained|examples?|template|"
               r"archives?)\b", re.I),
    re.compile(r"\.{3}|…"),                                    # truncated titles
    re.compile(r"\bjob\b|\bhiring\b|\bsalary\b|\(\d+\s*hour\)", re.I),
    re.compile(r":\s"),                                        # "Title: Subtitle" pattern
    re.compile(r"\?"),                                         # questions are articles
]

def looks_like_article_title(title: str) -> bool:
    """Strict check, used for COMPANY NAME validation only."""
    t = (title or "").strip()
    if not t:
        return True
    if len(t.split()) > 8:          # real org names are short
        return True
    return any(rx.search(t) for rx in ARTICLE_TITLE_RES)


# Subset used to judge EVIDENCE strength (not names). Deliberately excludes
# the bare colon (":\s") and bare question-mark rules: official program
# pages routinely use "Candidate Handbook: Certification Exam Guide" or
# "Do I Need to Recertify?" phrasing and that is NOT a sign of a blog
# listicle the way "8 Things to Know: A Guide" is. Using the strict
# version here was demoting legitimate program pages (e.g. a candidate
# handbook) to weak evidence purely for punctuation.
_LISTICLE_EVIDENCE_RES = [ARTICLE_TITLE_RES[i] for i in (0, 1, 2, 3, 4, 5)]

def is_listicle_style(title: str) -> bool:
    """Looser check, used for EVIDENCE STRENGTH only."""
    t = (title or "").strip()
    if not t:
        return True
    if len(t.split()) > 12:
        return True
    return any(rx.search(t) for rx in _LISTICLE_EVIDENCE_RES)

# ---------------------------------------------------------------------------
# 5. Trigger extraction — strong buyer signals vs weak mentions
# ---------------------------------------------------------------------------
STRONG_TRIGGERS = {
    "certification exam": r"certification exam|certified exam|exam content outline|candidate handbook|proctored exam",
    "credentialing program": r"credentialing program|credential holders?|recertification",
    "continuing education credits": r"continuing education credits?|\bce credits?\b|contact hours|cme credits?|cle credits?|pdus?\b",
    "compliance training with assessments": r"compliance training (program|and assessment)|annual compliance training",
    "skills validation": r"skills? validation|skills? assessment",
    "competency assessment": r"competenc(y|ies) (assessment|evaluation|validation)",
    "clinical education": r"clinical education|nurse residency|clinical competenc",
    "legal education / CLE": r"\bcle\b|continuing legal education|mcle\b",
    "staff training program": r"staff training program|employee training program",
    "training academy": r"training academy|corporate university|training center|training institute",
    "employee onboarding": r"onboarding program|new hire training|first 90 days",
    "workforce development": r"workforce development|apprenticeship program|workforce training",
    "professional education": r"professional education|professional development program",
    "learning and development program": r"learning (and|&) development|l&d program",
    "assessment rubric": r"assessment rubric|scoring rubric",
    "exams / knowledge checks": r"knowledge checks?|final exam|practice exam|exam prep",
    "accredited provider": r"accredited provider|approved provider|accreditation statement",
    # K-12 / teacher discovery triggers (Helokn's "AI Assistant Teacher"
    # positioning). Kept as distinct trigger names from the adjacent
    # "skills validation"/"competency assessment" triggers above even
    # though conceptually related, since scoring keys off the trigger
    # name string and this ICP needs its own signal.
    "classroom teacher": r"classroom teachers?|our teachers|meet our teachers|faculty directory|staff directory",
    "teacher professional development": r"teacher professional development|pd for teachers|professional development (day|credits) for (teachers|staff|faculty)",
    "differentiated instruction": r"differentiated instruction|individualized learning plan|student-centered learning",
    "lesson planning": r"lesson plans?|lesson planning|curriculum support|instructional coach",
    "student assessment tools": r"student assessment|formative assessment|grading tools?|edtech (tool|platform) for teachers",
}
STRONG_TRIGGER_RES = {k: re.compile(v, re.I) for k, v in STRONG_TRIGGERS.items()}

WEAK_ONLY_RE = re.compile(r"\bcompliance|training|regulation|learning\b", re.I)


def extract_triggers(text: str) -> list[str]:
    """Return the list of strong trigger names found in text."""
    return [name for name, rx in STRONG_TRIGGER_RES.items() if rx.search(text or "")]


# ---------------------------------------------------------------------------
# Candidate model + the filter itself
# ---------------------------------------------------------------------------
@dataclass
class Candidate:
    raw_title: str
    raw_url: str
    domain: str
    snippet: str
    source_query: str
    icp_hint: str
    triggers: list = field(default_factory=list)
    evidence_weak: bool = False   # trigger found on a blog/junk path


def root_domain(url: str) -> str:
    """'https://www.sub.acme.org/x' -> 'acme.org' (naive but adequate)."""
    host = (urlparse(url).netloc or "").lower()
    host = host.removeprefix("www.")  # NOT lstrip: lstrip("www.") eats leading w/. chars
    parts = host.split(".")
    # keep 3 labels for common two-part TLDs, else 2
    two_part = {"co.uk", "org.uk", "ac.uk", "com.au", "org.au", "co.nz"}
    if len(parts) >= 3 and ".".join(parts[-2:]) in two_part:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def filter_result(item: dict, source_query: str, icp_hint: str):
    """
    Classify one Serper organic result.
    Returns (Candidate | None, rejection: dict | None).
    """
    title = item.get("title", "") or ""
    url = item.get("link", "") or ""
    snippet = item.get("snippet", "") or ""
    domain = root_domain(url)

    def reject(reason, explanation):
        return None, {
            "raw_title": title, "raw_url": url, "domain": domain,
            "source_query": source_query, "rejection_reason": reason,
            "explanation": explanation,
        }

    if not domain or "." not in domain:
        return reject("invalid_domain", "Result URL has no usable domain.")

    if domain in BLOCKED_DOMAINS:
        return reject("blocked_domain",
                      f"{domain} is a social/media/jobs/directory site, not a buyer org.")

    if domain in VENDOR_DOMAINS:
        return reject("known_vendor",
                      f"{domain} sells training/compliance/exam software — competitor-adjacent, not a prospect.")

    if is_content_site_domain(domain):
        return reject("content_site_domain",
                      f"{domain} is named for content (forum/podcast/blog/news) — "
                      f"the organization itself is a content site, not a buyer.")

    text = f"{title} {snippet}"
    if VENDOR_SNIPPET_RE.search(text):
        return reject("vendor_language",
                      "Snippet uses vendor sales language (demo/pricing/platform).")

    junk_path = bool(JUNK_PATH_RE.search(urlparse(url).path or ""))
    triggers = extract_triggers(text)

    if not triggers:
        if junk_path:
            return reject("junk_content_page",
                          "Blog/news/job/directory page with no strong buyer trigger.")
        if WEAK_ONLY_RE.search(text):
            return reject("weak_signal_only",
                          "Only weak keyword mentions (e.g. 'compliance'); no strong trigger.")
        return reject("no_trigger",
                      "No buyer trigger found in title or snippet.")

    # Strong trigger present. Article-style title is fine — we will NEVER
    # use the page title as the company name — but it demotes evidence.
    evidence_weak = junk_path or is_listicle_style(title)

    return Candidate(
        raw_title=title, raw_url=url, domain=domain, snippet=snippet,
        source_query=source_query, icp_hint=icp_hint,
        triggers=triggers, evidence_weak=evidence_weak,
    ), None