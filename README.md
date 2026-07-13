# Helokn Lead Generation Pipeline (MVP)

Finds organizations that could plausibly **buy Helokn** (AI evaluation /
assessment platform): certification bodies, credentialing orgs, CE providers,
clinical/legal education, compliance training orgs, workforce development,
corporate L&D — and exports them as clean, sales-ready CSVs.

## Pipeline

```
ICP query bank (queries.py — 82 queries: 45 production + 37 top-up)
  -> Gather        Serper search, disk-cached, hard budget cap
  -> Junk filter   free string rules: blogs, articles, vendors, job
                   boards, directories, weak "mentions compliance" out
  -> Dedupe        one candidate per domain (before spending anything)
  -> Validate org  free homepage fetch: real org name, alive domain,
                   vendor/consultancy detection      <- BEFORE contacts
  -> Enrich        free contact-page scrape, one best contact,
                   honest status + confidence labels
  -> Score & tier  ICP 25 + trigger 25 + evidence 20 + validity 15 +
                   contact 15; tier gates, full audit trail
  -> Export        output/helokn_leads.csv + output/rejected_candidates.csv
```

## v2 changes (post-audit, July 2026)

A production run of v1 returned 70 qualified leads with no duplicates and
no missing fields — but two classes of problem kept it under the 100-lead
target and skewed quality in a few places. Both are fixed without
loosening any filter:

**1. Discovery gap, not a filter problem.** ICP mix was 63% Continuing
Education Provider + Certification Body, while Healthcare Training
Organization, Safety Training Organization, Structured Onboarding, and
Education Organization (Assessments) had **zero** leads — because the
v1 query bank barely searched for them. Fix: `queries.py` grew from 42
to 82 queries, with the production set raised from 30 to 45 and every
ICP category now getting dedicated first-person-language queries. Also
raised `MAX_RESULTS_PER_QUERY` from 10 to 15 — Serper bills per request
regardless of result count, so this is a free volume increase.

**2. Five specific quality bugs**, all found by auditing the v1 output
row by row:
  - Some names resolved to literal domain/URL strings ("www.aabb.org",
    "NJ.gov") when a site's own `<title>`/og:site_name happened to BE
    its domain. Fixed: any resolved name that looks like a bare domain
    is now discarded, falling through to an `<h1>`-based fallback before
    the domain-stem fallback.
  - Page-chrome words ("CBDCE **Website**", "NASW **Home**") leaked into
    otherwise-good names. Fixed: expanded the drop/strip list.
  - A SaaS vendor (Centranum-style "Key Features / competency management
    software / skills validation checklists" copy) passed the v1 vendor
    check. Fixed: `VENDOR_PAGE_RE` now catches feature-list and
    platform/SaaS language, not just "request a demo" phrasing.
  - Bar associations were consistently reclassified as generic "Legal
    Education / CLE Provider" because that category's keyword overlap
    incidentally beat the query's own intended hint. Fixed:
    `classify_icp` now gives the query's deliberate `icp_hint` a
    tie-breaking edge; four previously-unreachable ICP categories (Bar
    Association, Healthcare Training Organization, Education
    Organization (Assessments), Structured Onboarding) were also added
    to the keyword map — they existed as valid categories but had no
    path to ever being selected.
  - A `.gov` result surfaced as a lead with the company name "NJ.gov" —
    a huge multi-department state portal isn't one coherent
    organization. Fixed: `.gov` domains are now only accepted when a
    specific name is confirmed via `og:site_name` or schema.org markup
    (not a title/h1/domain guess), which is what a real board/agency
    subsite provides and a generic portal doesn't.

  Also: evidence scoring was demoting legitimate program pages (e.g. a
  candidate handbook titled "X: Certification Exam Guide") purely for
  containing a colon — that check is now reserved for company-name
  validation and no longer used to judge evidence strength. Contact
  scraping now checks more page paths and matches "Name, Title" order
  in addition to "Title — Name", to raise the named-contact yield.

All four fixes above are covered by dedicated regression tests in
`test_offline.py` (`python test_offline.py` — 0 network, 0 credits).

## Setup

```bash
cd helokn-leadgen
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # then paste your real key into .env
```

## Run

```bash
python run.py           # TEST mode (default, safe): 1 query, 1 lead, ~1 credit
python run.py --full    # PRODUCTION mode (explicit): 45-65 credits
```

Optional knobs:

```bash
python run.py --full --max-queries 65 --max-results 15 --max-contacts 1 --target 100
python test_offline.py  # offline end-to-end check, 0 network, 0 credits
```

Production is impossible to trigger by accident: without `--full` the run is
always capped at 1 query and 1 processed lead.

## Outputs

* `output/helokn_leads.csv` — qualified leads, best first. Every row has a
  real company name (resolved from the org's homepage, never a page title),
  domain, website URL, evidence URL, audit trail explaining the score, and
  LinkedIn + Google manual-research links.
* `output/rejected_candidates.csv` — every discarded result with
  `rejection_reason` + explanation, so "why only N leads?" is always answerable.

## v4 changes (final MVP polish, July 2026)

The v3 run hit the target (106 leads, ~15 min runtime, resilient export).
Four leaks found in its output, all fixed with regression tests:

1. **Content-site domains** — degreeforum.net (a forum) and
   project-management-podcast.com (a podcast) qualified as leads because
   junk rules only checked URL paths, not the domain itself. New rule:
   forum/podcast/blog/news/wiki/jobs in the domain name = the org IS a
   content site → rejected. (Token-bounded so newschool.edu is safe.)
2. **Named-contact yield collapsed to 0** — the v3 speed early-stop fired
   at attempt 5 while team/leadership pages sat at positions 6-8. Paths
   reordered (people pages first) and early-stop now only triggers on a
   role email in hand, never a merely-generic one.
3. **Name tails** — "...Charlotte Homepage" leaked ("Homepage" wasn't in
   the tail regex); fixed with care: "Home"/"Site" tails are only
   stripped when the remainder is a single acronym-like token, so real
   names like "Sunrise Funeral Home" are never mangled. Lowercase
   acronym names (mwcc) are now uppercased (MWCC).
4. **New blocklist entries** — RedVector (Vector Solutions brand) and
   WIN Learning as vendors; Chief Talent Officer / CLO Media / Talent
   Management magazines as publishers.

A note on runtime: production now finishes in minutes, not hours. That is
the thread pool overlapping network waits — per-lead analysis is
unchanged (same validation, scraping, and scoring; see the rejects CSV
for the full audit trail of everything it screened out).

## v5 changes (email quality, July 2026)

The v4 run's audit showed 111 leads but effectively zero usable named
emails: the single "Named Buyer Email" was corrupted
(`%E2%80%A8amoore@...` — URL-encoded junk glued on by regex-over-HTML),
`no-reply@` and `webmaster@` inboxes surfaced as contacts, and several
emails belonged to the WRONG organization entirely (a web designer's
credit link, an embedded platform vendor). Fixes, all regression-tested:

1. **Proper extraction layer** — emails now come primarily from
   `mailto:` links (URL-decoded, `?subject=` stripped), then from page
   text/HTML, then from de-obfuscated `name [at] domain [dot] org`
   patterns. Every candidate passes strict validation before use.
2. **Same-domain rule** — a contact email must belong to the org's own
   root domain (subdomains OK). Cross-domain addresses on org pages are
   nearly always designer credits or embedded vendors.
3. **Dead inboxes excluded** — no-reply/webmaster/postmaster/etc. never
   appear in the CSV at all; mail to them goes nowhere.
4. **Wider buyer-title matching** — coordinator/specialist variants and
   Executive Director (at small certification boards, the ED *is* the
   buyer), with fixed casing ("Director of Certification", not "Of").
5. **Overlapping name matching** — adjacent capitalized words (link
   text run together) could previously eat a real name ("WebDesign
   Studio Dana Whitfield" matched as "Studio Dana"); candidates are now
   found with lookahead and the one whose email actually matches wins.

## Serper credit economics

* 1 production run = at most `--max-queries` fresh calls (default 65,
  against an 82-query bank: 45 production + 37 top-up). Contacts are
  scraped from company websites — **0 Serper credits**.
* `--max-results` (default 15) controls results per query — Serper bills
  per request, not per result, so this is a free way to widen the raw
  candidate pool without spending more credits.
* Every Serper response is cached in `cache/serper/` forever; repeating a
  query (or re-running after a crash) costs 0. The summary prints credits
  spent vs credits saved.
* Domains are deduped before validation/enrichment, and validation happens
  before enrichment, so junk never consumes any effort.

## Extending

* Add/reorder searches in `queries.py` (first 30 = production set; the rest
  is the top-up bank used only when under target and within budget).
* Add newly discovered vendors to `VENDOR_DOMAINS` in `junkfilter.py` —
  every entry saves credits and reviewer time.