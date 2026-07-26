"""
test_offline.py — offline end-to-end verification (no network, no credits).

Monkeypatches SerperClient.search, the query bank, and page fetching with
fixtures that reproduce known failure modes (v1 old-CSV problems + v2
audit findings), then runs the real pipeline code and asserts the new
behavior. Run: python test_offline.py

Each fixture query maps 1:1 to one Serper "organic" result so a
candidate's icp_hint is deterministic — this matters for testing ICP
classification specifically (see the Bar Association case below).
"""

import sys
import serper_client
import companyclean
import enrich

# ---------------------------------------------------------------------------
# Fixture queries -> one organic result each. (query_text, icp_hint, item)
# ---------------------------------------------------------------------------
FIXTURES = [
    # -- junk that MUST be rejected (mirrors the old CSV's failure modes) --
    ("q_junk_blog", "Compliance Training Organization", {
        "title": "Compliance Training for Employees: 8 Best Practices",
        "link": "https://www.xtramile.no/en/blog/compliance-training",
        "snippet": "Compliance training program tips and best practices for HR."}),
    ("q_junk_listicle", "Compliance Training Organization", {
        "title": "Top 3 Requirements of AML Compliance For Credit Unions",
        "link": "https://amlwatcher.com/blog/aml-compliance",
        "snippet": "Learn about AML compliance. Request a demo of our platform."}),
    ("q_junk_job", "Clinical Education Organization", {
        "title": "Clinical Education Specialist (Psychiatric) (40 Hour)",
        "link": "https://jobapscloud.com/jobs/clinical-education-specialist",
        "snippet": "Job opening for a clinical education specialist."}),
    ("q_junk_media", "Compliance Training Organization", {
        "title": "Healthcare Compliance Training in 2025: New Standards",
        "link": "https://www.beckershospitalreview.com/compliance-2025",
        "snippet": "News: hospitals face new compliance training standards."}),
    ("q_junk_weak", "Compliance Training Organization", {
        "title": "Acme Widgets Inc",
        "link": "https://acmewidgets.com/about",
        "snippet": "We follow all compliance regulations in manufacturing."}),

    # -- good candidates --
    ("q_good_certbody", "Certification Body", {
        "title": "NCCB Certification Exam | Candidate Handbook",
        "link": "https://certboard-example.org/certification/exam",
        "snippet": "Our certification exam and candidate handbook. Recertification "
                   "requires continuing education credits."}),
    ("q_good_ceprovider", "Continuing Education Provider", {
        "title": "How to Earn Your Nursing Contact Hours in 2026",
        "link": "https://nursece-example.com/blog/contact-hours-2026",
        "snippet": "As an accredited provider, we offer nursing continuing "
                   "education credits and contact hours."}),
    ("q_good_workforce", "Workforce Development Organization", {
        "title": "Workforce Training Programs",
        "link": "https://workforce-example.org/programs",
        "snippet": "Our workforce development board offers apprenticeship "
                   "programs with skills assessment and industry credentials."}),
    ("q_vendor_lms", "Compliance Training Organization", {
        "title": "TrainCo LMS",
        "link": "https://trainco-example.com/solutions",
        "snippet": "Compliance training program management for enterprises."}),
    ("q_dup_certbody", "Certification Body", {
        "title": "NBCC Recertification",
        "link": "https://certboard-example.org/recertification",
        "snippet": "Credentialing program recertification requirements."}),

    # -- v2 audit-fix cases --
    ("q_domainlike_title", "Credentialing Organization", {
        # v2 bug: title/og literally the domain string ("www.aabb.org")
        "title": "www.credboard-lincoln.org",
        "link": "https://credboard-lincoln.org/certificate",
        "snippet": "Our certification program offers a certificate program with "
                   "continuing education credits for credential holders."}),
    ("q_bar_association", "Bar Association", {
        # v2 bug: was reclassified as generic Legal Education / CLE Provider
        "title": "State Bar of Lincoln — CLE Requirements",
        "link": "https://lincolnstatebar.org/cle",
        "snippet": "Our members must complete mandatory continuing legal "
                   "education. State bar CLE requirements apply to all "
                   "licensed attorneys."}),
    ("q_saas_vendor", "Corporate L&D Team", {
        # v2 bug: feature-list SaaS copy (Centranum-style) wasn't caught
        "title": "CompetencyTrack Software Inc",
        "link": "https://competencytrack-demo.com/product",
        "snippet": "Key Features: competency management software, skills "
                   "validation checklists, workforce management platform "
                   "reporting for compliance training programs."}),
    ("q_gov_resolvable", "Certification Body", {
        # .gov WITH a resolvable specific org name -> should be kept
        "title": "State Radiography Board",
        "link": "https://radboard-lincoln.gov/certification",
        "snippet": "Our certification exam and candidate handbook for "
                   "radiologic technologists. Continuing education "
                   "credits required for renewal."}),
    ("q_gov_portal", "Clinical Education Organization", {
        # v2 bug: broad .gov portal ("nj.gov") leaked through as a lead
        "title": "Clinical Education Guidance",
        "link": "https://stateportal-lincoln.gov/health/ct-guidance",
        "snippet": "Clinical education in computed tomography requires "
                   "competency assessment per state guidance."}),

    ("q_name_trap", "Certification Body", {
        # v6 bug: nav/address text near a title match was extracted as a
        # fake person's name ("Highland Avenue", "Certification Resources")
        "title": "Board Certification Program",
        "link": "https://trapboard-lincoln.org/certification",
        "snippet": "Our certification exam and candidate handbook. "
                   "Recertification requires continuing education credits."}),

    ("q_teachers_found", "K-12 Education (Teachers)", {
        # v7: new feature — school with multiple findable teacher emails
        "title": "Meet Our Teachers | Lincoln Elementary School",
        "link": "https://lincolnelementary-lincoln.org/staff",
        "snippet": "Meet our teachers at Lincoln Elementary. Our staff "
                   "directory includes contact information for every "
                   "classroom teacher."}),

    ("q_teachers_fallback", "K-12 Education (Teachers)", {
        # v7: school with NO individual teacher emails published — must
        # fall back to a standard org-level contact (principal), not
        # produce zero output for the school
        "title": "Our Faculty | Lincoln Middle School",
        "link": "https://lincolnmiddle-lincoln.org/faculty",
        "snippet": "Our faculty at Lincoln Middle School are dedicated "
                   "educators focused on differentiated instruction and "
                   "student assessment."}),

    ("q_bare_name_email", "Certification Body", {
        # v7 hit-rate fix: a small org's informal "Our Team" page lists
        # just "Name — email", no job title at all. Previously discarded
        # entirely (title-based search finds nothing -> falls through to
        # role/generic/nothing). Must now be picked up as a genuine,
        # honestly-labeled named contact.
        "title": "Small Org Certification Program",
        "link": "https://smallorg-lincoln.org/certification",
        "snippet": "Our certification exam and candidate handbook. "
                   "Continuing education credits required for renewal."}),
]

TEST_QUERY_BANK = [(q, hint) for q, hint, _ in FIXTURES]
RESULTS_BY_QUERY = {q: {"organic": [item]} for q, _, item in FIXTURES}

# ---------------------------------------------------------------------------
# Fixture homepages / contact pages
# ---------------------------------------------------------------------------
PAGES = {
    "https://certboard-example.org": """
        <html><head><title>National Board for Certified Counselors - Example |
        Home</title><meta property="og:site_name" content="National Counselor Certification Board">
        </head><body>Our certification program serves 40,000 credential holders.
        Apply now for the certification exam. Recertification requires 60
        continuing education credits. Download the candidate handbook.</body></html>""",
    "https://certboard-example.org/contact": """
        <html><body>Contact us: info@certboard-example.org.
        Maria Sanchez, Certification Program Manager — msanchez@certboard-example.org.
        </body></html>""",
    "https://nursece-example.com": """
        <html><head><title>NurseCE Institute: Accredited Nursing CE Provider</title></head>
        <body>We are an accredited provider of nursing continuing education.
        Earn your contact hours. Enroll in our courses today. Our learners
        love us.</body></html>""",
    "https://nursece-example.com/contact": """
        <html><body>Questions? education@nursece-example.com or
        info@nursece-example.com</body></html>""",
    "https://workforce-example.org": """
        <html><head><title>Metro Workforce Board | Training Programs</title></head>
        <body>Our programs include apprenticeship programs with skills
        assessment. Enroll now. Register for workforce training. Our members
        earn industry credentials.</body></html>""",
    "https://workforce-example.org/contact": """
        <html><body>General inquiries: info@workforce-example.org</body></html>""",
    "https://trainco-example.com": """
        <html><head><title>TrainCo — The All-in-One Compliance Training Platform</title></head>
        <body>Request a demo. Pricing: per user per month. Our platform powers
        compliance training for 500 enterprises. Start your free trial.</body></html>""",
    "https://acmewidgets.com": "<html><title>Acme Widgets</title><body>We make widgets.</body></html>",

    # title is literally the domain (v2 bug); h1 must supply the real name
    "https://credboard-lincoln.org": """
        <html><head><title>www.credboard-lincoln.org</title></head>
        <body><h1>Credentialing Board of Lincoln State</h1>
        Our certification program and certificate program offer continuing
        education credits. Credential holders must complete recertification.
        </body></html>""",
    "https://credboard-lincoln.org/contact": """
        <html><body>
        <p>General: <a href="mailto:no-reply@credboard-lincoln.org">no-reply</a></p>
        <p>Site by <a href="mailto:hello@webdesignstudio.com">WebDesign Studio</a></p>
        <p>Dana Whitfield, Executive Director —
        <a href="mailto:DWhitfield@credboard-lincoln.org?subject=Hello">email Dana</a></p>
        </body></html>""",

    # real bar association homepage confirms the trigger
    "https://lincolnstatebar.org": """
        <html><head><title>State Bar of Lincoln</title>
        <meta property="og:site_name" content="State Bar of Lincoln"></head>
        <body>Our members complete mandatory continuing legal education.
        State bar CLE requirements. Enroll in our courses.</body></html>""",
    "https://lincolnstatebar.org/contact": """
        <html><body>Contact: info@lincolnstatebar.org</body></html>""",

    # vendor homepage with feature-list SaaS copy (v2 bug: was missed)
    "https://competencytrack-demo.com": """
        <html><head><title>CompetencyTrack — Competency Management Software</title></head>
        <body>Key Features: competency management software helps you manage
        workforce management platform learning. Request a demo today.
        Pricing starts at $10 per user per month.</body></html>""",

    # .gov WITH a resolvable specific org name via schema.org -> allowed
    "https://radboard-lincoln.gov": """
        <html><head><title>State Board of Radiologic Technology</title>
        <script type="application/ld+json">
        {"@type": "GovernmentOrganization", "name": "State Board of Radiologic Technology"}
        </script></head>
        <body>Our certification exam and candidate handbook for radiologic
        technologists. Continuing education credits required for renewal.
        Apply now.</body></html>""",
    "https://radboard-lincoln.gov/contact": """
        <html><body>Contact: licensing@radboard-lincoln.gov</body></html>""",

    # .gov portal with NO og:site_name / schema -> must be rejected
    "https://stateportal-lincoln.gov": """
        <html><head><title>State of Lincoln Government Portal</title></head>
        <body>Clinical education in computed tomography requires competency
        assessment per state guidance. Find services from every state
        department here.</body></html>""",

    # v6 bug: nav/address text near a title match, no real person present
    "https://trapboard-lincoln.org": """
        <html><head><title>Board of Trap Certification</title>
        <meta property="og:site_name" content="Board of Trap Certification"></head>
        <body>Our certification program offers a certification exam with
        continuing education credits. Apply now.</body></html>""",
    "https://trapboard-lincoln.org/contact": """
        <html><body>Certification Resources: contact our Executive Director
        for questions. Highland Avenue office.
        <a href="mailto:general@trapboard-lincoln.org">general@trapboard-lincoln.org</a>
        </body></html>""",

    # v7: school WITH multiple findable teacher emails
    "https://lincolnelementary-lincoln.org": """
        <html><head><title>Lincoln Elementary School</title>
        <meta property="og:site_name" content="Lincoln Elementary School"></head>
        <body>Meet our teachers. Our classroom teachers focus on
        differentiated instruction and lesson plans for every student.
        </body></html>""",
    "https://lincolnelementary-lincoln.org/staff": """
        <html><body>
        <p>Sarah Chen, 3rd Grade Teacher —
        <a href="mailto:schen@lincolnelementary-lincoln.org">email</a></p>
        <p>Marcus Bell, Math Teacher —
        <a href="mailto:mbell@lincolnelementary-lincoln.org">email</a></p>
        <p>Every teacher at our school is dedicated. (no email here, prose only)</p>
        <p>Principal Diane Ford —
        <a href="mailto:dford@lincolnelementary-lincoln.org">email</a></p>
        </body></html>""",
    # a second page listing Sarah Chen again — must NOT produce a duplicate row
    "https://lincolnelementary-lincoln.org/faculty": """
        <html><body>
        <p>Sarah Chen, 3rd Grade Teacher —
        <a href="mailto:schen@lincolnelementary-lincoln.org">email</a></p>
        </body></html>""",

    # v7: school with NO individual teacher emails — must fall back to
    # the standard org-level contact (Principal) instead of zero output
    "https://lincolnmiddle-lincoln.org": """
        <html><head><title>Lincoln Middle School</title>
        <meta property="og:site_name" content="Lincoln Middle School"></head>
        <body>Our faculty are dedicated educators focused on
        differentiated instruction and student assessment.</body></html>""",
    "https://lincolnmiddle-lincoln.org/faculty": """
        <html><body>Our faculty are wonderful educators and teachers who
        care deeply about every student. (no names, no emails on this page)
        </body></html>""",
    "https://lincolnmiddle-lincoln.org/contact": """
        <html><body>Principal Robert Kim —
        <a href="mailto:rkim@lincolnmiddle-lincoln.org">email</a></body></html>""",

    # v7 hit-rate fix: name + email, deliberately NO job title anywhere
    "https://smallorg-lincoln.org": """
        <html><head><title>Small Org Certification Board</title>
        <meta property="og:site_name" content="Small Org Certification Board"></head>
        <body>Our certification exam and candidate handbook. Continuing
        education credits required for renewal.</body></html>""",
    "https://smallorg-lincoln.org/contact": """
        <html><body>Our Team: Priya Nair —
        <a href="mailto:pnair@smallorg-lincoln.org">pnair@smallorg-lincoln.org</a>.
        Reach out anytime with questions about certification!</body></html>""",
}


def fake_search(self, query):
    self.fresh_calls += 1
    return RESULTS_BY_QUERY.get(query, {"organic": []})


def fake_fetch(url, timeout=None):
    return PAGES.get(url.rstrip("/"))


def main():
    serper_client.SerperClient.search = fake_search
    companyclean.fetch_page = fake_fetch
    enrich.fetch_page = fake_fetch

    sys.argv = ["run.py", "--full", "--max-queries", str(len(TEST_QUERY_BANK)),
               "--target", "6"]
    import run
    run.QUERY_BANK = TEST_QUERY_BANK        # inject deterministic fixtures
    run.main()

    # ---- assertions on the produced CSVs ----
    import csv
    import config
    leads = list(csv.DictReader(open(config.OUTPUT_DIR / "helokn_leads.csv")))
    rejects = list(csv.DictReader(open(config.OUTPUT_DIR / "rejected_candidates.csv")))

    names = {l["company_name"] for l in leads}
    domains = {l["domain"] for l in leads}
    print("\nASSERTIONS")

    assert "certboard-example.org" in domains, "good cert body missing"
    assert "nursece-example.com" in domains, "good CE provider missing"
    assert "workforce-example.org" in domains, "good workforce board missing"
    print("  [ok] all 3 v1 real buyer orgs qualified")

    assert not any("How to" in n or "Top 3" in n or ":" in n for n in names), \
        f"article title leaked into company_name: {names}"
    assert "National Counselor Certification Board" in names, names
    print(f"  [ok] real org names resolved from homepages: {sorted(names)}")

    assert "trainco-example.com" not in domains, "vendor leaked through"
    assert "xtramile.no" not in domains and "amlwatcher.com" not in domains
    assert "jobapscloud.com" not in domains, "job board leaked"
    print("  [ok] vendors, blogs, job boards, media all rejected")

    rej_reasons = {r["domain"]: r["rejection_reason"] for r in rejects}
    assert rej_reasons.get("trainco-example.com") == "vendor_homepage"
    assert rej_reasons.get("acmewidgets.com") in ("weak_signal_only", "failed_scoring", "no_trigger")
    print(f"  [ok] rejection audit trail present ({len(rejects)} rows)")

    by_dom = {l["domain"]: l for l in leads}
    cb = by_dom["certboard-example.org"]
    assert cb["contact_status"] == "Named Buyer Email", cb["contact_status"]
    assert cb["email_confidence"] == "high_verified_named"
    assert cb["contact_name"] == "Maria Sanchez"
    print("  [ok] named buyer contact found & labeled high_verified_named")

    nce = by_dom["nursece-example.com"]
    assert nce["contact_status"] == "Role-Based Email", nce["contact_status"]
    assert nce["contact_email"] == "education@nursece-example.com"
    wf = by_dom["workforce-example.org"]
    assert wf["contact_status"] == "Generic Email Only"
    assert wf["email_confidence"] == "low_generic"
    print("  [ok] role-based preferred over generic; generic labeled honestly")

    assert all(l["linkedin_search_url"] and l["google_search_url"] for l in leads)
    assert all(l["domain"] and l["website_url"] and l["evidence_url"] for l in leads)
    assert all(l["audit_trail"] for l in leads)
    print("  [ok] manual-research URLs, domains, evidence, audit trail on every row")

    # ---- v2 audit-fix regression checks ----
    cred = by_dom.get("credboard-lincoln.org")
    assert cred is not None, "h1-fallback org missing"
    assert cred["company_name"] == "Credentialing Board of Lincoln State", cred["company_name"]
    print(f"  [ok] domain-string title fixed via h1 fallback: {cred['company_name']!r}")

    bar = by_dom.get("lincolnstatebar.org")
    assert bar is not None, "bar association missing"
    assert bar["icp_category"] == "Bar Association", \
        f"bar association misclassified as {bar['icp_category']}"
    print("  [ok] Bar Association no longer overridden by generic CLE overlap")

    assert "competencytrack-demo.com" not in by_dom, \
        "feature-copy SaaS vendor leaked through"
    assert rej_reasons.get("competencytrack-demo.com") == "vendor_homepage"
    print("  [ok] feature-list/SaaS vendor copy now caught (Centranum-style leak fixed)")

    rad = by_dom.get("radboard-lincoln.gov")
    assert rad is not None, ".gov org with resolvable schema name was wrongly rejected"
    assert rad["company_name"] == "State Board of Radiologic Technology"
    assert "stateportal-lincoln.gov" not in by_dom, \
        "unresolvable .gov portal leaked through (nj.gov-style bug)"
    assert rej_reasons.get("stateportal-lincoln.gov") == "gov_portal_unresolved"
    print("  [ok] .gov handled correctly: resolvable board kept, broad portal rejected")

    # ---- v3 audit-fix regression checks: name sanitizer ----
    from companyclean import _sanitize_name
    v3_bad = ["Search", "Home", "MONTANA", "www.aabb.org",
              "Daily Medical News, Free CME and Clinical Guidance",
              "Continuing Legal Educationfor theNext Generation™"]
    assert all(_sanitize_name(n) is None for n in v3_bad), \
        "a v3 bad name passed the sanitizer"
    assert _sanitize_name("CBDCE Website") == "CBDCE"
    assert _sanitize_name("EduClasses | Online Courseware") == "EduClasses"
    v3_good = ["NBCC", "ISACA", "PTCB", "GIAC Certifications",
               "Art Therapy Credentials Board, Inc.", "The Florida Bar",
               "RN.org®"]
    assert all(_sanitize_name(n) is not None for n in v3_good), \
        "a legit name was wrongly rejected by the sanitizer"
    print("  [ok] v3 name sanitizer: chrome words, taglines, ALL-CAPS banners, "
          "domain strings rejected; acronyms & real names kept")

    # ---- v4 audit-fix regression checks ----
    from junkfilter import is_content_site_domain, filter_result
    assert is_content_site_domain("degreeforum.net")
    assert is_content_site_domain("project-management-podcast.com")
    assert is_content_site_domain("training-blog.com")
    assert not is_content_site_domain("newschool.edu")          # 'news' substring trap
    assert not is_content_site_domain("certboard-example.org")
    c, rej = filter_result(
        {"title": "PDU Podcast", "link": "https://project-management-podcast.com/pdu",
         "snippet": "Earn all 60 PMI PDUs, continuing education credits."},
        "q", "Continuing Education Provider")
    assert c is None and rej["rejection_reason"] == "content_site_domain"
    print("  [ok] v4: forum/podcast/blog DOMAINS rejected as content sites "
          "(degreeforum.net / pm-podcast leak fixed)")

    assert _sanitize_name(
        "The University of North Carolina at Charlotte Homepage") == \
        "The University of North Carolina at Charlotte"
    assert _sanitize_name("NASW Home") == "NASW"
    assert _sanitize_name("Sunrise Funeral Home") == "Sunrise Funeral Home"
    assert _sanitize_name("mwcc") == "MWCC"
    print("  [ok] v4: 'Homepage' tail stripped, 'NASW Home' -> 'NASW', real "
          "'... Funeral Home' names protected, lowercase acronyms uppercased")

    from junkfilter import VENDOR_DOMAINS
    assert "redvector.com" in VENDOR_DOMAINS and "winlearning.com" in VENDOR_DOMAINS
    from junkfilter import BLOCKED_DOMAINS
    assert "chieftalentofficer.co" in BLOCKED_DOMAINS
    print("  [ok] v4: RedVector & WIN Learning vendors + L&D-magazine domains blocked")

    # ---- v5 audit-fix regression checks: email quality ----
    from enrich import _normalize_email, smart_title
    assert _normalize_email("%E2%80%A8amoore@flcertificationboard.org",
                            "flcertificationboard.org") == \
        "amoore@flcertificationboard.org"
    assert _normalize_email("no-reply@atcb.org", "atcb.org") is None
    assert _normalize_email("webmaster@fbpe.org", "fbpe.org") is None
    assert _normalize_email("hello@rfuenzalida.com", "warmhumancoldworld.org") is None
    assert smart_title("director of certification") == "Director of Certification"
    print("  [ok] v5: encoded-junk emails cleaned, dead inboxes excluded, "
          "cross-domain emails rejected, titles cased correctly")

    cred2 = by_dom["credboard-lincoln.org"]
    assert cred2["contact_status"] == "Named Buyer Email", cred2["contact_status"]
    assert cred2["contact_name"] == "Dana Whitfield"
    assert cred2["contact_title"] == "Executive Director"
    assert cred2["contact_email"] == "dwhitfield@credboard-lincoln.org"
    assert cred2["email_confidence"] == "high_verified_named"
    print("  [ok] v5: mailto-only email extracted; Executive Director matched "
          "as buyer title; no-reply & designer-credit emails ignored")

    # ---- v6 audit-fix regression check: fake-name extraction ----
    from enrich import _looks_like_person_name
    v6_bad_names = ["Director Ron", "Highland Avenue", "Member Benefits",
                    "Certification Resources", "Oregon Community",
                    "Community Center", "Consumer Voice"]
    assert all(not _looks_like_person_name(n) for n in v6_bad_names), \
        "a real production fake-name failure is no longer blocked"
    v6_real_names = ["Dana Whitfield", "Maria Sanchez", "John Mudd", "Cherise Lee"]
    assert all(_looks_like_person_name(n) for n in v6_real_names), \
        "a real name was wrongly blocked"

    trap = by_dom.get("trapboard-lincoln.org")
    assert trap is not None, "trap-fixture org missing"
    assert trap["contact_name"] == "", \
        f"fake name leaked through end-to-end: {trap['contact_name']!r}"
    assert trap["contact_status"] == "Generic Email Only", trap["contact_status"]
    assert trap["contact_email"] == "general@trapboard-lincoln.org"
    print("  [ok] v6: 'Highland Avenue' / 'Certification Resources' style "
          "fake names rejected end-to-end; falls through to honest "
          "Generic Email Only instead of a fabricated named contact")

    # ---- v7 feature checks: K-12 / teacher email discovery ----
    elem_rows = [l for l in leads if l["domain"] == "lincolnelementary-lincoln.org"]
    assert len(elem_rows) == 2, \
        f"expected 2 teacher rows (Sarah Chen, Marcus Bell), got {len(elem_rows)}: " \
        f"{[r['contact_name'] for r in elem_rows]}"
    names = {r["contact_name"] for r in elem_rows}
    assert names == {"Sarah Chen", "Marcus Bell"}, \
        f"wrong teachers extracted: {names} (Principal Diane Ford must be " \
        f"excluded — not a teacher title; no duplicate Sarah Chen from the " \
        f"second page that repeats her)"
    for r in elem_rows:
        assert r["contact_status"] == "Named Buyer Email"
        assert r["icp_category"] == "K-12 Education (Teachers)"
        assert r["company_name"] == "Lincoln Elementary School"
        assert r["contact_email"].endswith("@lincolnelementary-lincoln.org")
    titles = {r["contact_name"]: r["contact_title"] for r in elem_rows}
    assert titles["Sarah Chen"] == "3Rd Grade Teacher" or titles["Sarah Chen"] == "3rd Grade Teacher", \
        titles["Sarah Chen"]  # smart_title capitalizes each word; "3rd" has no letters to re-case oddly
    assert titles["Marcus Bell"] == "Math Teacher", titles["Marcus Bell"]
    print(f"  [ok] v7: multi-teacher extraction — 2 distinct teachers found, "
          f"Principal excluded, cross-page duplicate correctly deduped, "
          f"titles: {titles}")

    mid_rows = [l for l in leads if l["domain"] == "lincolnmiddle-lincoln.org"]
    assert len(mid_rows) == 1, \
        f"school with no teacher emails should fall back to exactly 1 " \
        f"org-level contact row, got {len(mid_rows)}"
    assert mid_rows[0]["contact_name"] == "Robert Kim", mid_rows[0]["contact_name"]
    assert mid_rows[0]["contact_title"] == "Principal", mid_rows[0]["contact_title"]
    assert mid_rows[0]["icp_category"] == "K-12 Education (Teachers)"
    print(f"  [ok] v7: school with zero teacher emails correctly falls back "
          f"to org-level contact ({mid_rows[0]['contact_name']}, "
          f"{mid_rows[0]['contact_title']}) instead of producing nothing")

    small = by_dom.get("smallorg-lincoln.org")
    assert small is not None, "bare-name-email org missing"
    assert small["contact_name"] == "Priya Nair", small["contact_name"]
    assert small["contact_email"] == "pnair@smallorg-lincoln.org"
    assert small["contact_title"] == "", \
        f"title should be honestly empty (none was found), got {small['contact_title']!r}"
    assert small["contact_status"] == "Named Buyer Email", small["contact_status"]
    assert small["email_confidence"] == "medium_likely_named", \
        "no title confirmation -> capped at medium, not high"
    print("  [ok] v7 hit-rate fix: name+email with NO job title nearby is "
          "now captured (previously discarded entirely), honestly labeled "
          "with an empty title and capped confidence")

    # ---- v8 audit-fix regression checks: real production CSV findings ----
    from enrich import _gather_name_candidates, _looks_like_person_name

    # "Last, First" staff-directory format — the likely dominant reason
    # teacher-email yield was near zero: real evidence had "Mora, Sara"
    # and the old space-only regex could never see it at all.
    real_text = ("Staff Directory. By PositionAlphabetically. Administration. "
                "DB. Buffano ... 331-228-53503rd Grade Teacher- In-Classroom "
                "TBESM. Mora, Sara. 331-228-541")
    names_found = {c[0] for c in _gather_name_candidates(real_text)}
    assert "Sara Mora" in names_found, \
        f"Last,First format not detected in real evidence text: {names_found}"

    assert not _looks_like_person_name("Achievement Report")
    assert not _looks_like_person_name("It Fast")
    print("  [ok] v8: 'Last, First' staff-directory format now detected "
          "('Mora, Sara' -> 'Sara Mora'); 'Achievement Report'/'It Fast' "
          "fake names (from real production CSV) now blocked")

    # "Name, Job Title" is structurally identical to "Last, First" and
    # must NOT be misread as a person named e.g. "Math Bell" out of
    # "Marcus Bell, Math Teacher" — a real regression caught in testing.
    jobtitle_text = "Marcus Bell, Math Teacher — contact us"
    bogus = {c[0] for c in _gather_name_candidates(jobtitle_text)}
    assert "Math Bell" not in bogus, f"'Name, JobTitle' misread as 'Last, First': {bogus}"
    assert "Marcus Bell" in bogus, f"real space-form name should still be found: {bogus}"
    print("  [ok] v8: 'Name, Job Title' convention correctly NOT misread "
          "as a 'Last, First' person name")

    assert _looks_like_person_name("Marcus Bell"), \
        "a common surname was wrongly blocked by the nav-word blocklist"
    print("  [ok] v8: common surname 'Bell' no longer collides with the "
          "'bell schedule' nav-word blocklist entry")

    # ---- v9 audit-fix regression checks: second round of production findings ----
    # Real fake names from the SECOND production CSV audit — a publication
    # name, a substring of the org's own name, a call-to-action link, a
    # nav-text fragment, a program-name fragment, and the same "Find It
    # Fast" site-search widget recurring on a different district's site.
    v9_bad_names = ["Fraud Magazine", "Medication Therapy", "Email Us",
                    "Recertification My", "Early Childhood", "Find It"]
    assert all(not _looks_like_person_name(n) for n in v9_bad_names), \
        f"a real v9 production fake name is no longer blocked"
    # Must not have broken anything real in the process
    v9_real_names = ["Sarah Chen", "Marcus Bell", "Sara Mora", "Priya Nair"]
    assert all(_looks_like_person_name(n) for n in v9_real_names), \
        "v9 blocklist expansion wrongly blocked a real name"
    print("  [ok] v9: 'Fraud Magazine' / 'Medication Therapy' / 'Email Us' / "
          "'Recertification My' / 'Early Childhood' / 'Find It' (real "
          "production fake names) now blocked; no real names affected")

    from companyclean import _sanitize_name
    assert _sanitize_name("Welcome to PRA") == "PRA", \
        f"'Welcome to X' company-name prefix not stripped: {_sanitize_name('Welcome to PRA')!r}"
    assert _sanitize_name("Welcome to the National Registry") == "National Registry"
    print("  [ok] v9: 'Welcome to X' company-name prefix now stripped "
          "('Welcome to PRA' -> 'PRA')")

    # ---- v10 audit-fix regression checks: third round of production findings ----
    v10_bad_names = ["Esq Suchocki", "Review Course", "By Mail",
                     "Content Assistance", "Criminal Law", "Physician Lookup",
                     "Los Angeles", "Nadeau Consultants", "Emergency Medical"]
    assert all(not _looks_like_person_name(n) for n in v10_bad_names), \
        "a real v10 production fake name is no longer blocked"
    v10_real_names = ["Ron Hanchar", "Meryl Dorf", "Janae Langreck",
                      "Amanda Oliveira", "Roxy Aldred", "Pam Johnson-Carlson"]
    assert all(_looks_like_person_name(n) for n in v10_real_names), \
        "v10 blocklist expansion wrongly blocked a real name"
    print("  [ok] v10: 'Esq Suchocki' (legal-title-suffix misread as a "
          "first name), 'Review Course', 'By Mail', 'Content Assistance', "
          "'Criminal Law', 'Physician Lookup', 'Los Angeles', 'Nadeau "
          "Consultants', 'Emergency Medical' (real production fake names) "
          "now blocked; real names unaffected")

    # Query-bank interleaving: real production incident — K-12 queries sat
    # at positions 45-58 of 59 and a run went from 8 K-12 leads to 0 with
    # no code change, purely because gather()'s deadline (or an unlucky
    # slow stretch) never reached that far into the list. Every category
    # must now appear early enough to survive a run that only gets partway
    # through the primary set.
    from queries import QUERY_BANK as _QB
    _cats_in_first_20 = {h for _, h in _QB[:20]}
    _all_cats = {h for _, h in _QB}
    assert len(_all_cats) >= 15, f"expected at least 15 ICP categories, got {len(_all_cats)}"
    assert _cats_in_first_20 == _all_cats, \
        f"not every category appears in the first 20 queries — a partial " \
        f"run could still zero out a category by position: missing " \
        f"{_all_cats - _cats_in_first_20}"
    print(f"  [ok] v10: query bank interleaved — all {len(_all_cats)} ICP "
          f"categories appear within the first 20 of {len(_QB)} queries, "
          f"so a run cut short at ANY point still samples every category")

    for l in leads:
        print(f"  lead: {l['tier']} {l['score']:>3} {l['icp_category']:<38} "
              f"{l['company_name']} [{l['contact_status']}]")
    print("\nALL ASSERTIONS PASSED")


if __name__ == "__main__":
    main()