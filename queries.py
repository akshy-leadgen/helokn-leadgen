"""
queries.py — the query bank.

v12 (rebalanced toward reachable buyers): Corporate L&D Team queries
raised from 6 -> 20 and Structured Onboarding from 8 -> 11, after
production output showed the query bank was skewed 3.5x toward large
national Certification Bodies (21 queries) versus Corporate L&D (6).
That imbalance directly explained why output leaned so heavily toward
big, bureaucratic certification bodies over mid-size companies with a
faster, single-decision-maker buying motion — the SAME evidence-based
scoring and validation applies to every lead either way; this changes
WHAT gets searched for, not how strictly it's judged once found. New
queries target active-hiring and growth-stage signals ("we are
hiring", "scaling our team", "fast-growing company") specifically
because a company visibly building out its People/L&D function is a
much stronger reachability signal than a static "about us" page.

v10 (interleaved for robustness): the query list is round-robin
INTERLEAVED across all ICP categories, rather than grouped into
contiguous per-category blocks. This closes a real gap found in
production: K-12 Education (Teachers) queries sat at positions 45-58
of a 59-query primary set. If gather()'s enforced deadline (added to
fix an indefinite-hang bug) ever trips before reaching position 45 —
a slow Serper day, some retries — EVERY K-12 query gets skipped
entirely, not degraded: a run went from 8 K-12 leads to 0 with no
code change at all, purely from where that category happened to sit
in the list. Interleaving means any prefix of the query list (however
far a run gets before stopping, for any reason) samples from every
category early, instead of some categories being all-or-nothing based
on list position.

Design principle unchanged from earlier versions: search for phrases
that only appear on a BUYER ORGANIZATION'S OWN pages, not in articles
about the topic.

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
under target and Serper budget allows. Because the whole bank is
interleaved, both the primary set AND the top-up tail draw from every
category fairly, rather than the tail being dominated by whichever
categories happened to be defined last.
"""

QUERY_BANK = [
    ('"our certification program" exam eligibility apply', 'Certification Body'),
    ('"recertification requirements" "continuing education" credential holders', 'Credentialing Organization'),
    ('professional association "our members" "certification program" exam', 'Professional Association (Certification)'),
    ('"approved provider" "continuing education credits" courses', 'Continuing Education Provider'),
    ('"clinical education" department "competency assessment" hospital', 'Clinical Education Organization'),
    ('"nurse residency program" "our program" apply hospital', 'Nursing Education Organization'),
    ('"CME" "accredited provider" "our activities" physicians', 'Healthcare Training Organization'),
    ('"CLE credits" "our programs" attorneys accredited', 'Legal Education / CLE Provider'),
    ('bar association "CLE requirements" members courses', 'Bar Association'),
    ('"compliance training program" "our employees" "annual training" company', 'Compliance Training Organization'),
    ('"meet our teachers" elementary OR middle OR high school', 'K-12 Education (Teachers)'),
    ('"workforce development" board "training programs" "our services"', 'Workforce Development Organization'),
    ('"training academy" "our academy" employees careers company', 'Corporate L&D Team'),
    ('"employee onboarding program" "first 90 days" careers "our"', 'Structured Onboarding / Training Program'),
    ('"safety training" council "our courses" certification OSHA', 'Safety Training Organization'),
    ('"teacher certification" "praxis" OR "licensure exam" preparation program state', 'Education Organization (Assessments)'),
    ('"certification exam" "candidate handbook" apply', 'Certification Body'),
    ('"credentialing program" "competency assessment" apply', 'Credentialing Organization'),
    ('association "certificate program" "continuing education credits" members', 'Professional Association (Certification)'),
    ('"CE credits" "accredited provider" "our courses"', 'Continuing Education Provider'),
    ('health system "clinical competency" "skills validation" education', 'Clinical Education Organization'),
    ('"nursing continuing education" "contact hours" accredited provider', 'Nursing Education Organization'),
    ('hospital "new employee orientation" OR "new hire training" clinical staff', 'Healthcare Training Organization'),
    ('"mandatory continuing legal education" provider "our courses"', 'Legal Education / CLE Provider'),
    ('"state bar" "continuing legal education" "our members" requirements', 'Bar Association'),
    ('"corporate compliance" "training and education" program hospital', 'Compliance Training Organization'),
    ('"faculty directory" teachers school "email"', 'K-12 Education (Teachers)'),
    ('"apprenticeship program" "skills assessment" apply sponsor', 'Workforce Development Organization'),
    ('"corporate university" "leadership development" "our programs"', 'Corporate L&D Team'),
    ('"new hire training program" "our onboarding" company careers', 'Structured Onboarding / Training Program'),
    ('"safety certification" "training center" enroll courses', 'Safety Training Organization'),
    ('"competency-based assessment" "our students" program credential', 'Education Organization (Assessments)'),
    ('"become certified" "exam content outline"', 'Certification Body'),
    ('"certification board" "recertification" "continuing education units"', 'Credentialing Organization'),
    ('society "board certification" "eligibility requirements"', 'Professional Association (Certification)'),
    ('"continuing education requirements" "our programs" licensees', 'Continuing Education Provider'),
    ('"clinical skills lab" "competency validation" nursing education hospital', 'Clinical Education Organization'),
    ('"annual competency" "skills fair" OR "skills day" hospital staff', 'Healthcare Training Organization'),
    ('"annual compliance training" "all employees" "our organization" complete', 'Compliance Training Organization'),
    ('"staff directory" "grade" teacher school contact', 'K-12 Education (Teachers)'),
    ('"workforce training" center "industry credentials" enroll', 'Workforce Development Organization'),
    ('company "learning and development team" "our programs" employees', 'Corporate L&D Team'),
    ('"structured onboarding" "our new hires" program company', 'Structured Onboarding / Training Program'),
    ('"OSHA authorized" "training provider" "our courses" certification', 'Safety Training Organization'),
    ('school OR district "skills assessment" "our students" credentialing program', 'Education Organization (Assessments)'),
    ('"digital badge" "earn your certification" professional', 'Certification Body'),
    ('"our credential" "renewal requirements" professionals apply', 'Credentialing Organization'),
    ('"national association" "our certification" "exam" members apply', 'Professional Association (Certification)'),
    ('"license renewal" "continuing education" "our courses" board approved', 'Continuing Education Provider'),
    ('health system "staff education department" "our programs" training', 'Healthcare Training Organization'),
    ('financial institution "compliance training" "our staff" annual required', 'Compliance Training Organization'),
    ('"our teachers" school "classroom" contact', 'K-12 Education (Teachers)'),
    ('"workforce board" "our programs" "industry certification" job seekers', 'Workforce Development Organization'),
    ('"internal certification program" "our employees" company skills', 'Corporate L&D Team'),
    ('retailer OR manufacturer "new employee orientation program" "our stores" OR "our plants"', 'Structured Onboarding / Training Program'),
    ('"safety council" "our members" "training programs" certification', 'Safety Training Organization'),
    ('"knowledge check" OR "final exam" "our course" certificate of completion', 'Education Organization (Assessments)'),
    ('"proctored exam" "certification" "candidate" schedule', 'Certification Body'),
    ('"allied health" "certification" "competency" "exam" organization', 'Credentialing Organization'),
    ('institute "professional designation" "continuing education" "our members"', 'Professional Association (Certification)'),
    ('"real estate" "continuing education" "license renewal" "our courses" school', 'Continuing Education Provider'),
    ('"long term care" OR "skilled nursing facility" "staff training program" annual', 'Healthcare Training Organization'),
    ('"our faculty" "elementary school" OR "middle school" OR "high school" directory', 'K-12 Education (Teachers)'),
    ('"skills training center" "our programs" enroll credential', 'Workforce Development Organization'),
    ('"employee training program" "our company" required annual modules', 'Corporate L&D Team'),
    ('"fire academy" "our recruits" OR "our cadets" training certification', 'Structured Onboarding / Training Program'),
    ('construction OR manufacturing "safety training program" "our workers" certification', 'Safety Training Organization'),
    ('"assessment center" "our candidates" scoring credential', 'Education Organization (Assessments)'),
    ('"paramedic certification" OR "EMS certification" "our program" exam', 'Certification Body'),
    ('guild OR federation "certification program" "our members" exam', 'Professional Association (Certification)'),
    ('"insurance" "continuing education" "license" "approved courses" provider', 'Continuing Education Provider'),
    ('"infection control training" "our staff" hospital annual competency', 'Healthcare Training Organization'),
    ('school district "staff directory" "teacher" contact email', 'K-12 Education (Teachers)'),
    ('"utility" OR "lineworker" "apprenticeship" "our program" certification training', 'Workforce Development Organization'),
    ('"credit union" "training" "our employees" "certification" program', 'Corporate L&D Team'),
    ('"police academy" OR "law enforcement training" "our recruits" certification', 'Structured Onboarding / Training Program'),
    ('"forklift certification" OR "confined space training" "our program" enroll', 'Safety Training Organization'),
    ('"flight school" OR "aviation academy" "our students" "checkride" OR "certification"', 'Education Organization (Assessments)'),
    ('"pharmacy technician" OR "dental assistant" "certification exam" "our program"', 'Certification Body'),
    ('"PDU" OR "professional development units" "our courses" certification renewal', 'Continuing Education Provider'),
    ('"private school" OR "charter school" "our faculty" teachers directory', 'K-12 Education (Teachers)'),
    ('"our L&D team" OR "learning and development team" company careers', 'Corporate L&D Team'),
    ('"corrections officer" OR "911 dispatcher" "training academy" "our program" certification', 'Structured Onboarding / Training Program'),
    ('"commercial driver" OR "CDL" "training school" "our program" certification', 'Safety Training Organization'),
    ('"paralegal certification" "our program" exam apply', 'Certification Body'),
    ('"project management" certification "PDU" OR "contact hours" provider', 'Continuing Education Provider'),
    ('"teacher professional development" district "our teachers"', 'K-12 Education (Teachers)'),
    ('"director of learning and development" "our team" company hiring', 'Corporate L&D Team'),
    ('"franchise" "training program" "our franchisees" certification required', 'Structured Onboarding / Training Program'),
    ('"food safety" OR "food handler" "certification" "our courses" exam', 'Safety Training Organization'),
    ('"financial services" institute "designation" "continuing education" candidates', 'Certification Body'),
    ('"veterinary" OR "dental hygiene" "continuing education" "our courses" credits', 'Continuing Education Provider'),
    ('"instructional coach" OR "curriculum director" school "our team"', 'K-12 Education (Teachers)'),
    ('"learning experience" "our employees" company culture careers', 'Corporate L&D Team'),
    ('"employee onboarding experience" "our company" new hires', 'Structured Onboarding / Training Program'),
    ('"HR certification" institute exam "recertification credits"', 'Certification Body'),
    ('"physical therapy" OR "occupational therapy" "continuing education" "our courses" credits', 'Continuing Education Provider'),
    ('"AI in the classroom" OR "edtech" teacher "our teachers" school', 'K-12 Education (Teachers)'),
    ('"we are hiring" "learning and development manager" company', 'Corporate L&D Team'),
    ('"first day" "our new hires" company onboarding program', 'Structured Onboarding / Training Program'),
    ('"IT certification" "exam voucher" "our certification" candidates', 'Certification Body'),
    ('"mental health counselor" OR "social worker" "continuing education" "our courses" credits', 'Continuing Education Provider'),
    ('"differentiated instruction" "our teachers" classroom school', 'K-12 Education (Teachers)'),
    ('"employee development program" "our company" growing team', 'Corporate L&D Team'),
    ('"onboarding buddy" OR "onboarding checklist" "our new hires" company', 'Structured Onboarding / Training Program'),
    ('"cybersecurity certification" "our exam" "candidate" credential', 'Certification Body'),
    ('"lesson plans" teacher "our staff" school contact', 'K-12 Education (Teachers)'),
    ('"internal training team" "our employees" company culture', 'Corporate L&D Team'),
    ('"aviation" OR "aircraft maintenance" "certification program" "our candidates" exam', 'Certification Body'),
    ('elementary school "our staff" "grade teacher" contact', 'K-12 Education (Teachers)'),
    ('"people development" "our team" company culture careers', 'Corporate L&D Team'),
    ('"childcare" OR "early childhood educator" "certification" "our program" credential', 'Certification Body'),
    ('middle school OR high school "department" "our teachers" faculty', 'K-12 Education (Teachers)'),
    ('"talent development" "our organization" employees program', 'Corporate L&D Team'),
    ('"real estate appraiser" OR "home inspector" "certification exam" "our program"', 'Certification Body'),
    ('"corporate training program" "our employees" company culture', 'Corporate L&D Team'),
    ('"insurance adjuster" OR "claims adjuster" "certification" "our program" exam', 'Certification Body'),
    ('"learning culture" "our company" employees development', 'Corporate L&D Team'),
    ('"banking" institute "our courses" "certification" professional development', 'Certification Body'),
    ('"skills development program" company "our employees" careers', 'Corporate L&D Team'),
    ('"insurance institute" "designation" "our courses" continuing education', 'Certification Body'),
    ('startup OR "fast-growing company" "learning and development" hiring', 'Corporate L&D Team'),
    ('"welding" OR "HVAC" OR "electrician" "certification" "our program" exam enroll', 'Certification Body'),
    ('"scaling our team" "learning and development" OR "people development"', 'Corporate L&D Team'),
    ('"court reporter" OR "notary" "certification exam" "our program" apply', 'Certification Body'),
    ('"head of people" OR "VP of people" "learning and development" company', 'Corporate L&D Team'),
    ('"interpreter" OR "translator" "certification" "our exam" credential apply', 'Certification Body'),
]

PRODUCTION_QUERY_COUNT = 92  # first N entries form the standard production run
                             # (raised 80 -> 92 to keep pace with the bank
                             # growing 110 -> 127 queries, preserving the
                             # same ~73% primary-vs-topup ratio as before)