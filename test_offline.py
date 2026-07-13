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

    for l in leads:
        print(f"  lead: {l['tier']} {l['score']:>3} {l['icp_category']:<38} "
              f"{l['company_name']} [{l['contact_status']}]")
    print("\nALL ASSERTIONS PASSED")


if __name__ == "__main__":
    main()