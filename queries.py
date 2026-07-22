"""
queries.py — the query bank.

v2 (post-audit expansion): the first version reached only 70 qualified
leads per run and the ICP mix was badly skewed — Continuing Education
Provider and Certification Body alone were 63% of output, while
Healthcare Training Organization, Safety Training Organization,
Structured Onboarding, and Education Organization (Assessments) had
ZERO leads. That's a discovery gap (too few queries aimed at those
categories), not a filter problem — v1's filters were correctly honest,
they just didn't have enough good candidates to work with in those
verticals. This version adds targeted queries for every underserved
category instead of loosening any rejection rule.

Design principle unchanged: search for phrases that only appear on a
BUYER ORGANIZATION'S OWN pages, not in articles about the topic.

  BAD  query: "compliance training best practices"
       -> returns blogs, vendors, listicles

  GOOD query: "our certification program" association exam
       -> "our certification program" is first-person org language;
          articles say "a certification program", orgs say "our".

Each entry: (query, icp_category_hint). The hint seeds classification;
scorer.classify_icp only overrides it when triggers clearly support a
different category (see scorer.py for the tie-breaking rule).

PRODUCTION_QUERY_COUNT queries form the standard production run; the
remainder is the top-up bank used automatically when a run comes in
under target and Serper budget allows.
"""

QUERY_BANK = [
    # ================= Certification bodies / credentialing =================
    ('"our certification program" exam eligibility apply', "Certification Body"),
    ('"certification exam" "candidate handbook" apply', "Certification Body"),
    ('"recertification requirements" "continuing education" credential holders', "Credentialing Organization"),
    ('"credentialing program" "competency assessment" apply', "Credentialing Organization"),
    ('"become certified" "exam content outline"', "Certification Body"),
    ('"digital badge" "earn your certification" professional', "Certification Body"),
    ('"proctored exam" "certification" "candidate" schedule', "Certification Body"),
    ('"certification board" "recertification" "continuing education units"', "Credentialing Organization"),
    ('"our credential" "renewal requirements" professionals apply', "Credentialing Organization"),

    # ================= Professional associations =================
    ('professional association "our members" "certification program" exam', "Professional Association (Certification)"),
    ('association "certificate program" "continuing education credits" members', "Professional Association (Certification)"),
    ('society "board certification" "eligibility requirements"', "Professional Association (Certification)"),
    ('"national association" "our certification" "exam" members apply', "Professional Association (Certification)"),
    ('institute "professional designation" "continuing education" "our members"', "Professional Association (Certification)"),
    ('guild OR federation "certification program" "our members" exam', "Professional Association (Certification)"),

    # ================= Continuing education providers =================
    ('"approved provider" "continuing education credits" courses', "Continuing Education Provider"),
    ('"CE credits" "accredited provider" "our courses"', "Continuing Education Provider"),
    ('"continuing education requirements" "our programs" licensees', "Continuing Education Provider"),
    ('"license renewal" "continuing education" "our courses" board approved', "Continuing Education Provider"),
    ('"real estate" "continuing education" "license renewal" "our courses" school', "Continuing Education Provider"),
    ('"insurance" "continuing education" "license" "approved courses" provider', "Continuing Education Provider"),
    ('"PDU" OR "professional development units" "our courses" certification renewal', "Continuing Education Provider"),

    # ================= Healthcare / clinical / nursing education =================
    ('"clinical education" department "competency assessment" hospital', "Clinical Education Organization"),
    ('"nurse residency program" "our program" apply hospital', "Nursing Education Organization"),
    ('"nursing continuing education" "contact hours" accredited provider', "Nursing Education Organization"),
    ('"CME" "accredited provider" "our activities" physicians', "Healthcare Training Organization"),
    ('health system "clinical competency" "skills validation" education', "Clinical Education Organization"),
    ('hospital "new employee orientation" OR "new hire training" clinical staff', "Healthcare Training Organization"),
    ('"annual competency" "skills fair" OR "skills day" hospital staff', "Healthcare Training Organization"),
    ('"clinical skills lab" "competency validation" nursing education hospital', "Clinical Education Organization"),
    ('health system "staff education department" "our programs" training', "Healthcare Training Organization"),
    ('"allied health" "certification" "competency" "exam" organization', "Credentialing Organization"),
    ('"paramedic certification" OR "EMS certification" "our program" exam', "Certification Body"),
    ('"pharmacy technician" OR "dental assistant" "certification exam" "our program"', "Certification Body"),
    ('"long term care" OR "skilled nursing facility" "staff training program" annual', "Healthcare Training Organization"),
    ('"infection control training" "our staff" hospital annual competency', "Healthcare Training Organization"),

    # ================= Legal education / CLE / bar associations =================
    ('"CLE credits" "our programs" attorneys accredited', "Legal Education / CLE Provider"),
    ('bar association "CLE requirements" members courses', "Bar Association"),
    ('"mandatory continuing legal education" provider "our courses"', "Legal Education / CLE Provider"),
    ('"state bar" "continuing legal education" "our members" requirements', "Bar Association"),
    ('"paralegal certification" "our program" exam apply', "Certification Body"),

    # ================= Compliance training organizations (buyer side) =================
    ('"compliance training program" "our employees" "annual training" company', "Compliance Training Organization"),
    ('"corporate compliance" "training and education" program hospital', "Compliance Training Organization"),
    ('"annual compliance training" "all employees" "our organization" complete', "Compliance Training Organization"),
    ('financial institution "compliance training" "our staff" annual required', "Compliance Training Organization"),

    # ================= K-12 / teacher discovery =================
    # Added for Helokn's "AI Assistant Teacher" positioning. Placed here
    # (inside the guaranteed primary set, not appended to the tail) on
    # purpose — a top-up-only query only runs if an earlier shortfall
    # triggers deep top-up, which under default budget settings would
    # rarely or never reach queries this far down the bank. Teacher
    # discovery is a first-class feature, not a fallback.
    #
    # These are deliberately aimed at pages that PUBLISH a staff/faculty
    # directory (where individual teacher emails actually live), not just
    # any page that mentions teachers — "our teachers", "faculty
    # directory", and "staff directory" are the phrases that distinguish
    # a real directory page from an article or news mention about schools
    # in general.
    ('"meet our teachers" elementary OR middle OR high school', "K-12 Education (Teachers)"),
    ('"faculty directory" teachers school "email"', "K-12 Education (Teachers)"),
    ('"staff directory" "grade" teacher school contact', "K-12 Education (Teachers)"),
    ('"our teachers" school "classroom" contact', "K-12 Education (Teachers)"),
    ('"our faculty" "elementary school" OR "middle school" OR "high school" directory', "K-12 Education (Teachers)"),
    ('school district "staff directory" "teacher" contact email', "K-12 Education (Teachers)"),
    ('"private school" OR "charter school" "our faculty" teachers directory', "K-12 Education (Teachers)"),
    ('"teacher professional development" district "our teachers"', "K-12 Education (Teachers)"),
    ('"instructional coach" OR "curriculum director" school "our team"', "K-12 Education (Teachers)"),
    ('"AI in the classroom" OR "edtech" teacher "our teachers" school', "K-12 Education (Teachers)"),
    ('"differentiated instruction" "our teachers" classroom school', "K-12 Education (Teachers)"),
    ('"lesson plans" teacher "our staff" school contact', "K-12 Education (Teachers)"),
    ('elementary school "our staff" "grade teacher" contact', "K-12 Education (Teachers)"),
    ('middle school OR high school "department" "our teachers" faculty', "K-12 Education (Teachers)"),

    # ================= Workforce development =================
    ('"workforce development" board "training programs" "our services"', "Workforce Development Organization"),
    ('"apprenticeship program" "skills assessment" apply sponsor', "Workforce Development Organization"),
    ('"workforce training" center "industry credentials" enroll', "Workforce Development Organization"),
    ('"workforce board" "our programs" "industry certification" job seekers', "Workforce Development Organization"),
    ('"skills training center" "our programs" enroll credential', "Workforce Development Organization"),

    # ================= Corporate L&D / onboarding / training academies =================
    ('"training academy" "our academy" employees careers company', "Corporate L&D Team"),
    ('"corporate university" "leadership development" "our programs"', "Corporate L&D Team"),
    ('"employee onboarding program" "first 90 days" careers "our"', "Structured Onboarding / Training Program"),
    ('"new hire training program" "our onboarding" company careers', "Structured Onboarding / Training Program"),
    ('"structured onboarding" "our new hires" program company', "Structured Onboarding / Training Program"),
    ('company "learning and development team" "our programs" employees', "Corporate L&D Team"),
    ('"internal certification program" "our employees" company skills', "Corporate L&D Team"),
    ('"employee training program" "our company" required annual modules', "Corporate L&D Team"),
    ('retailer OR manufacturer "new employee orientation program" "our stores" OR "our plants"', "Structured Onboarding / Training Program"),

    # ================= Safety training organizations =================
    ('"safety training" council "our courses" certification OSHA', "Safety Training Organization"),
    ('"safety certification" "training center" enroll courses', "Safety Training Organization"),
    ('"OSHA authorized" "training provider" "our courses" certification', "Safety Training Organization"),
    ('"safety council" "our members" "training programs" certification', "Safety Training Organization"),
    ('construction OR manufacturing "safety training program" "our workers" certification', "Safety Training Organization"),
    ('"forklift certification" OR "confined space training" "our program" enroll', "Safety Training Organization"),

    # ================= Education organizations with assessments =================
    ('"teacher certification" "praxis" OR "licensure exam" preparation program state', "Education Organization (Assessments)"),
    ('"competency-based assessment" "our students" program credential', "Education Organization (Assessments)"),
    ('school OR district "skills assessment" "our students" credentialing program', "Education Organization (Assessments)"),
    ('"knowledge check" OR "final exam" "our course" certificate of completion', "Education Organization (Assessments)"),
    ('"assessment center" "our candidates" scoring credential', "Education Organization (Assessments)"),

    # ================= Additional certification / credentialing verticals =================
    ('"financial services" institute "designation" "continuing education" candidates', "Certification Body"),
    ('"HR certification" institute exam "recertification credits"', "Certification Body"),
    ('"project management" certification "PDU" OR "contact hours" provider', "Continuing Education Provider"),
    ('"IT certification" "exam voucher" "our certification" candidates', "Certification Body"),
    ('"cybersecurity certification" "our exam" "candidate" credential', "Certification Body"),
    ('"aviation" OR "aircraft maintenance" "certification program" "our candidates" exam', "Certification Body"),
    ('"veterinary" OR "dental hygiene" "continuing education" "our courses" credits', "Continuing Education Provider"),
    ('"physical therapy" OR "occupational therapy" "continuing education" "our courses" credits', "Continuing Education Provider"),
    ('"mental health counselor" OR "social worker" "continuing education" "our courses" credits', "Continuing Education Provider"),
    ('"childcare" OR "early childhood educator" "certification" "our program" credential', "Certification Body"),
    ('"real estate appraiser" OR "home inspector" "certification exam" "our program"', "Certification Body"),
    ('"insurance adjuster" OR "claims adjuster" "certification" "our program" exam', "Certification Body"),

    # ================= v3 top-up: untapped verticals =================
    # Added after the v3 audit run reached 91: these institutional
    # training verticals were absent from the bank entirely.
    ('"fire academy" "our recruits" OR "our cadets" training certification', "Structured Onboarding / Training Program"),
    ('"police academy" OR "law enforcement training" "our recruits" certification', "Structured Onboarding / Training Program"),
    ('"corrections officer" OR "911 dispatcher" "training academy" "our program" certification', "Structured Onboarding / Training Program"),
    ('"banking" institute "our courses" "certification" professional development', "Certification Body"),
    ('"credit union" "training" "our employees" "certification" program', "Corporate L&D Team"),
    ('"insurance institute" "designation" "our courses" continuing education', "Certification Body"),
    ('"utility" OR "lineworker" "apprenticeship" "our program" certification training', "Workforce Development Organization"),
    ('"welding" OR "HVAC" OR "electrician" "certification" "our program" exam enroll', "Certification Body"),
    ('"commercial driver" OR "CDL" "training school" "our program" certification', "Safety Training Organization"),
    ('"flight school" OR "aviation academy" "our students" "checkride" OR "certification"', "Education Organization (Assessments)"),
    ('"franchise" "training program" "our franchisees" certification required', "Structured Onboarding / Training Program"),
    ('"food safety" OR "food handler" "certification" "our courses" exam', "Safety Training Organization"),
    ('"court reporter" OR "notary" "certification exam" "our program" apply', "Certification Body"),
    ('"interpreter" OR "translator" "certification" "our exam" credential apply', "Certification Body"),
]

PRODUCTION_QUERY_COUNT = 59  # first N entries form the standard production run
                             # (45 -> 59: extended to guarantee the 14 K-12/
                             # teacher discovery queries always run, not just
                             # when a shortfall happens to trigger deep top-up)