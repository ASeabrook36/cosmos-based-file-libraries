# ruff: noqa: E501
"""Sample Markdown, HTML and plain-text documents. Every subject and fact here is invented."""

from .render import Doc

MD = [
    Doc(
        "01-deploy-runbook-ferrywatch",
        "Runbook: Deploying Ferrywatch, the Ferry Timetable Service",
        "Kofi Adjei",
        "2025-11-03",
        [
            (
                "Pre-flight",
                "Check that the `timetable-db` replica lag is under 5 seconds. Freeze deploys during the Friday 16:00 to 18:00 commuter window. Announce in the channel `#ferry-ops` before starting.",
            ),
            (
                "Rollout",
                "Run `make release TAG=v3.8.1`. The canary receives 5% of traffic for 12 minutes. Abort if the 99th percentile latency exceeds 450 ms or the error rate exceeds 0.8%. To roll back, run `make rollback` which restores the previous tag in about 90 seconds.",
            ),
        ],
        "What latency or error rate threshold aborts a Ferrywatch canary?",
        "p99 latency over 450 ms or error rate over 0.8%.",
    ),
    Doc(
        "02-mushroom-foraging-field-notes",
        "Field Notes: Autumn Foraging in Beech Woodland",
        "Saskia van der Meer",
        "2024-10-06",
        [
            (
                "Safe finds",
                "Hen-of-the-woods (Grifola frondosa) grows at the base of oaks and has overlapping greyish fronds. Chicken-of-the-woods is bright orange-yellow and grows on trunks; cook thoroughly.",
            ),
            (
                "Dangerous lookalikes",
                "The deathcap (Amanita phalloides) has a white volva at its base, a skirt on the stem and white spores. Never pick a mushroom without digging out the base to look. If in doubt, throw it out.",
            ),
            (
                "Tally",
                "On 6 October the party collected 3.2 kg of hen-of-the-woods, 1.1 kg of chicken-of-the-woods and 14 penny buns.",
            ),
        ],
        "What features distinguish the deathcap in these notes?",
        "A white volva at the base, a skirt on the stem and white spores.",
    ),
    Doc(
        "03-adr-sqlite-for-museum-kiosk",
        "ADR 012: Use SQLite for the Museum Kiosk Data Store",
        "Helen Brightwell",
        "2025-02-18",
        [
            (
                "Context",
                "The 14 touch-screen kiosks at the Harbour Museum run offline for up to 9 hours when the building network drops. They need to look up around 6,000 exhibit records.",
            ),
            (
                "Decision",
                "Use SQLite with the FTS5 extension, shipped as a 38 MB file updated nightly. We rejected a local PostgreSQL install because of the maintenance burden on devices with 2 GB of RAM.",
            ),
            (
                "Consequences",
                "Search latency under 20 ms. Content editors must publish through the nightly build rather than seeing changes live.",
            ),
        ],
        "Why did the kiosk project reject a local PostgreSQL install?",
        "Because of the maintenance burden on devices with 2 GB of RAM.",
    ),
    Doc(
        "04-sailing-knots-reference",
        "Sailing Knots Quick Reference",
        "Captain Roderick Ames",
        "2023-05-14",
        [
            (
                "Bowline",
                "Forms a fixed loop that does not slip. The rabbit comes out of the hole, goes around the tree and back down the hole. Use for sheets and for securing a rescue line.",
            ),
            (
                "Clove hitch and cleat hitch",
                "A clove hitch is quick but can work loose under changing loads; add two half hitches. A cleat hitch finishes with a locking turn so it can be released under load.",
            ),
        ],
        "Which knot is used for sheets and rescue lines in the reference?",
        "The bowline.",
    ),
    Doc(
        "05-espresso-dialling-guide",
        "Dialling In Espresso: A Practical Guide",
        "Marco Benedetti",
        "2026-01-19",
        [
            (
                "The recipe",
                "Start with an 18 g dose, aim for 36 g out in 27 to 30 seconds at 93 degrees C. This is a 1:2 ratio.",
            ),
            (
                "Troubleshooting",
                "Sour and thin: grind finer or lengthen the shot. Bitter and harsh: grind coarser or shorten. Channeling shows as spurts on the bottomless portafilter; improve distribution with a needle tool.",
            ),
        ],
        "What dose, yield and time are the starting espresso recipe?",
        "18 g in, 36 g out, in 27 to 30 seconds.",
    ),
    Doc(
        "06-postmortem-printer-outage",
        "Postmortem: Label Printer Outage, 11 February 2026",
        "Dariusz Wolny",
        "2026-02-16",
        [
            (
                "Timeline",
                "09:02 a firmware auto-update rolled out to all 31 label printers. 09:10 printers began rebooting in a loop. 09:48 on-call identified the update as the cause. 11:30 firmware pinned to 4.2.7 and printers restored.",
            ),
            (
                "Root cause",
                "The updater trusted a manifest whose checksum field was blank for the new build. Nothing validated the field was present.",
            ),
            (
                "Actions",
                "Require checksums in the manifest. Stage updates to two printers first. Estimated cost: 2 hours 28 minutes of lost label printing across the dispatch hall.",
            ),
        ],
        "How long was the printing outage and what firmware version was pinned?",
        "2 hours 28 minutes; firmware 4.2.7.",
    ),
    Doc(
        "07-hiking-itinerary-val-serena",
        "Itinerary: Five-Day Walk Through the Val Serena Villages",
        "Giulia Ferrante",
        "2025-04-22",
        [
            (
                "Day by day",
                "Day 1: Brenta to Corvo, 14 km, 620 m ascent. Day 2: Corvo to Pietra Bianca via the Passo del Lupo at 2,140 m, 17 km. Day 3: rest and cooking class in Pietra Bianca. Days 4 and 5: ridge walk to the Rifugio Aurora and descent to Serena Bassa.",
            ),
            (
                "Kit",
                "Carry a waterproof layer, 2 litres of water and a printed map: there is no signal on the pass. The refuge charges EUR 58 for half-board.",
            ),
        ],
        "How high is the Passo del Lupo and what does half-board at the refuge cost?",
        "2,140 m; EUR 58.",
    ),
    Doc(
        "08-api-style-guide-excerpt",
        "Style Guide: Naming and Pagination for the Tideline API",
        "Ayesha Rahman",
        "2025-08-27",
        [
            (
                "Naming",
                "Use plural nouns for collections (`/boats`, `/harbours`), snake_case for fields and ISO 8601 for timestamps. Never expose database ids in URLs; use opaque ids prefixed with a type, for example `hbr_4F2k9`.",
            ),
            (
                "Pagination",
                "Use cursor pagination with `limit` (default 50, maximum 200) and an opaque `cursor`. Responses include `next_cursor`, which is null on the last page.",
            ),
        ],
        "What are the default and maximum pagination limits in the Tideline API style guide?",
        "Default 50, maximum 200.",
    ),
    Doc(
        "09-seed-saving-handbook",
        "Seed Saving for Beginners",
        "Rowan Ellery",
        "2024-08-03",
        [
            (
                "Easy crops",
                "Beans, peas, lettuce and tomatoes are self-pollinating and rarely cross. Leave a pod to dry fully on the plant until it rattles.",
            ),
            (
                "Tomatoes",
                "Scoop out the seeds with the gel, ferment in a jar of water for three days at room temperature, rinse and dry on a plate, not paper. Store in labelled envelopes in a cool dark place; they last 5 years.",
            ),
        ],
        "How long should tomato seeds ferment and how long do they last in storage?",
        "Three days of fermenting; about 5 years in storage.",
    ),
    Doc(
        "10-board-game-rules-salt-road",
        "Rules: Salt Road (2 to 4 Players)",
        "Imogen Castellan",
        "2023-11-30",
        [
            (
                "Goal",
                "Be the first to deliver five barrels of salt to the coast. Each turn, move a caravan up to 4 spaces, then buy or sell one goods card.",
            ),
            (
                "Hazards",
                "Landing on a bandit space costs you one barrel unless you pay 3 coins. A rainstorm card halves all movement for one round. Ties are broken by the most coins.",
            ),
        ],
        "How many barrels of salt must a player deliver to win Salt Road?",
        "Five barrels.",
    ),
    Doc(
        "11-data-retention-schedule-charities",
        "Data Retention Schedule for Small Charities",
        "Prudence Whitlock",
        "2025-03-12",
        [
            (
                "Schedule",
                "Financial records: 6 years from the end of the financial year. Gift Aid declarations: 6 years after the last donation claimed. Trustee minutes: permanently. Volunteer records: 2 years after leaving. Unsuccessful job applications: 6 months.",
            ),
            (
                "Disposal",
                "Shred paper records with a cross-cut shredder. Erase digital files from backups at the next scheduled cycle, within 90 days.",
            ),
        ],
        "How long should unsuccessful job applications be kept?",
        "6 months.",
    ),
    Doc(
        "12-meeting-notes-allotment-society-agm",
        "Meeting Notes: Allotment Society AGM, 9 May 2026",
        "Joan Pettifer",
        "2026-05-10",
        [
            (
                "Decisions",
                "Plot rents rise from GBP 28 to GBP 31 a year from 1 October. The society will buy a second wheelbarrow for GBP 85. Bee-keeping on site was approved by 22 votes to 4.",
            ),
            (
                "Actions",
                "Dennis to repair the north gate by 30 June. Joan to circulate the bee-keeping rules. Next meeting 12 September.",
            ),
        ],
        "How was the vote on bee-keeping at the AGM?",
        "Approved 22 votes to 4.",
    ),
]

HTML = [
    Doc(
        "01-town-museum-opening-hours",
        "Wrenfield Town Museum: Visiting Information",
        "Wrenfield Museum Trust",
        "2025-03-01",
        [
            (
                "Opening hours",
                "Open Tuesday to Saturday, 10 am to 4:30 pm; Sundays 12 noon to 4 pm. Closed on Mondays and from 24 December to 2 January.",
            ),
            (
                "Admission",
                "Adults GBP 6.50, concessions GBP 4.00, under-16s free. A family ticket for two adults and up to three children is GBP 15. The museum cafe serves a famous lemon drizzle cake baked to the recipe of founder Hilda Pargeter.",
            ),
        ],
        "How much is a family ticket at Wrenfield Town Museum?",
        "GBP 15.",
    ),
    Doc(
        "02-recipe-lamb-tagine",
        "Slow-Cooked Lamb Tagine with Apricots",
        "Layla Haddad",
        "2024-11-21",
        [
            (
                "Ingredients",
                "1.2 kg lamb shoulder, 2 onions, 3 cloves garlic, 1 tsp each of ground ginger, cumin and cinnamon, a pinch of saffron, 150 g dried apricots and 50 g toasted almonds.",
            ),
            (
                "Method",
                "Brown the lamb, soften the onions with the spices, add 500 ml water and cook covered at 160 degrees C for 2 hours 30 minutes. Add the apricots for the final 30 minutes. Serve with couscous and a squeeze of lemon.",
            ),
        ],
        "How long does the lamb tagine cook and when are the apricots added?",
        "2 hours 30 minutes; apricots go in for the final 30 minutes.",
    ),
    Doc(
        "03-faq-bicycle-repair-cafe",
        "Frequently Asked Questions: The Riverside Bicycle Repair Cafe",
        "Hettie Joubert",
        "2025-07-15",
        [
            (
                "What do I need to bring?",
                "Bring your bike and any parts you have bought. Volunteer mechanics will show you how to fix it yourself; we do not do the repair for you.",
            ),
            (
                "Cost",
                "Free, with a suggested donation of GBP 3. Inner tubes cost GBP 4 and brake cables GBP 2.50. We meet on the second Saturday of every month in the old boathouse.",
            ),
        ],
        "On which day does the Riverside Bicycle Repair Cafe meet?",
        "The second Saturday of every month.",
    ),
    Doc(
        "04-press-release-comet-discovery",
        "Press Release: Hollin Fell Observatory Detects Comet C/2026 H3",
        "Hollin Fell Observatory",
        "2026-06-02",
        [
            (
                "Discovery",
                "Amateur astronomer Dr. Siobhan Garrity found the comet on 18 April 2026 in images from the observatory's 0.4 m telescope. It was then at magnitude 17.8, in the constellation Lynx.",
            ),
            (
                "Orbit",
                "Preliminary orbit calculations give a perihelion distance of 1.3 astronomical units on 4 December 2026. The comet may reach naked-eye brightness of magnitude 5 in late November.",
            ),
        ],
        "When did the observatory discover Comet C/2026 H3 and who found it?",
        "On 18 April 2026, by Dr. Siobhan Garrity.",
    ),
    Doc(
        "05-job-posting-lighthouse-guide",
        "Job Posting: Visitor Guide, Skerryvore Island",
        "Skerryvore Heritage Trust",
        "2025-02-25",
        [
            (
                "The role",
                "Lead tours of the island lighthouse from April to September. Full-time, 37.5 hours, GBP 24,300 per year plus accommodation in the old keeper's cottage.",
            ),
            (
                "You will need",
                "A clear speaking voice, a head for heights (the tower has 147 steps), a boat-handling certificate or willingness to obtain one, and a love of seabirds. Applications close 14 March.",
            ),
        ],
        "How many steps does the Skerryvore tower have, and what is the closing date?",
        "147 steps; applications close 14 March.",
    ),
    Doc(
        "06-privacy-notice-public-library",
        "Privacy Notice: Calderbank Public Library",
        "Calderbank Library Service",
        "2024-06-17",
        [
            (
                "What we hold",
                "Your name, address, borrower number and loan history. We delete your loan history 30 days after an item is returned unless you opt in to reading history.",
            ),
            (
                "Your rights",
                "You may ask for a copy of your data, to correct it, or to have it erased. Write to the Library Data Officer. We answer within one month.",
            ),
        ],
        "How long after return does the library delete a borrower's loan history?",
        "30 days.",
    ),
    Doc(
        "07-tide-table-portmere",
        "Tide Table: Portmere Harbour, July 2026",
        "Portmere Harbour Board",
        "2026-06-25",
        [
            (
                "Notes",
                "Times are in BST. Heights are above chart datum. Harbour entrance dries at low water on spring tides; vessels drawing more than 1.2 m should enter within two hours of high water.",
            ),
            (
                "Selected tides",
                "Highest tide of the month, 6.8 m, on 24 July.",
                [
                    ("Date", "High water", "Height (m)"),
                    ("1 July", "05:14", "5.9"),
                    ("10 July", "11:52", "6.4"),
                    ("24 July", "11:08", "6.8"),
                ],
            ),
        ],
        "What is the highest tide at Portmere in July 2026 and on which date?",
        "6.8 m on 24 July.",
    ),
    Doc(
        "08-newsletter-village-hall",
        "Aldringham Village Hall Newsletter: Autumn",
        "Bridget Lowther",
        "2025-09-12",
        [
            (
                "Events",
                "Harvest supper on 4 October at 7 pm, tickets GBP 9. The over-60s lunch club moves to Thursdays. Pilates continues on Wednesdays at 9:30.",
            ),
            (
                "Hall news",
                "The new roof has been fitted at a cost of GBP 31,400, funded by the lottery and 112 donors. The 1962 stage curtains have been restored by the Women's Institute.",
            ),
        ],
        "How much did Aldringham village hall's new roof cost?",
        "GBP 31,400.",
    ),
    Doc(
        "09-glossary-wine-tasting",
        "A Short Glossary of Wine Tasting Terms",
        "Sommelier Association of Northumbria",
        "2023-10-03",
        [
            (
                "Terms",
                "Tannin: the drying, grippy sensation from grape skins and oak. Acidity: makes the mouth water, felt at the sides of the tongue. Terroir: the combined effect of soil, climate and site. Finish: how long flavour lingers after swallowing; ten seconds is long.",
            ),
            (
                "Serving",
                "Serve whites at 8 to 10 degrees C, light reds at 14 degrees C and full reds at 16 to 18 degrees C.",
            ),
        ],
        "At what temperature should full red wines be served?",
        "16 to 18 degrees C.",
    ),
    Doc(
        "10-terms-of-hire-narrowboats",
        "Terms of Hire: Narrowboat Holidays at Lockwood Basin",
        "Lockwood Basin Boats",
        "2024-03-29",
        [
            (
                "Hire period",
                "Weekly hire starts Saturday at 2 pm and ends the following Saturday at 9 am. Damage deposit GBP 250, refunded within 7 days. The helmsman must be over 21.",
            ),
            (
                "Rules",
                "Maximum speed 4 mph, 2 mph near moored boats. Moor only at designated places. Pets are allowed for a fee of GBP 30 per week.",
            ),
        ],
        "What is the damage deposit and the maximum canal speed in the hire terms?",
        "GBP 250 deposit and 4 mph (2 mph near moored boats).",
    ),
    Doc(
        "11-help-centre-password-reset",
        "Help Centre: Resetting Your Loyalty Card Password",
        "Customer Care, Harvest Basket Co-op",
        "2025-12-01",
        [
            (
                "Steps",
                "Choose Forgot password on the login page, enter the email on your account and open the link within 20 minutes. Passwords need 12 characters including a number. Your old password cannot be reused.",
            ),
            (
                "Still stuck?",
                "Call 0300 555 0142 between 9 am and 5 pm on weekdays. We will ask for your card number and postcode.",
            ),
        ],
        "How long is the password reset link valid?",
        "20 minutes.",
    ),
    Doc(
        "12-event-page-moth-night",
        "Event: National Moth Night at Elmstead Common",
        "Essex Wildlife Collective",
        "2026-07-08",
        [
            (
                "About",
                "Join local naturalists for an evening of moth trapping on 25 September from 8 pm to midnight. Last year's event recorded 61 species, including the Merveille du Jour and a rare Clifden Nonpareil.",
            ),
            (
                "Practical",
                "Free, no booking. Bring a torch with a red filter and warm clothes. Children over 8 welcome with an adult.",
            ),
        ],
        "How many species were recorded at last year's Moth Night?",
        "61 species.",
    ),
]

TXT = [
    Doc(
        "01-interview-notes-clockmaker",
        "Interview Notes: A Retired Clockmaker",
        "Oral History Project, Calder Valley",
        "2024-01-23",
        [
            (
                "Early life",
                "Walter Hesketh began as an apprentice in 1957 at the age of fifteen, earning 12 shillings a week. His master, Mr. Cobb, insisted that every apprentice make their own screwdriver before touching a clock.",
            ),
            (
                "The turret clock",
                "In 1971 Walter restored the turret clock of Todmorden Mill, which had stopped in 1944. The pendulum weighs 41 pounds and he found a wasps' nest in the escape wheel housing.",
            ),
        ],
        "What did Mr. Cobb require apprentices to make before touching a clock?",
        "Their own screwdriver.",
    ),
    Doc(
        "02-shift-handover-night-bakery",
        "Shift Handover Notes: Night Bakery, 6 March 2026",
        "Pedro Aguilar",
        "2026-03-06",
        [
            (
                "Production",
                "Overnight output: 220 white tins, 140 seeded batards, 60 spelt loaves, 18 trays of cinnamon buns. The spelt batch was 8 minutes under proof; check the crumb.",
            ),
            (
                "Equipment",
                "Oven 2 is running 12 degrees C cold; engineer booked for Monday 8 am. The dough divider needs oiling. Flour delivery of 25 sacks arrives at 5:30.",
            ),
        ],
        "How many degrees cold is Oven 2 running in the handover notes?",
        "12 degrees C cold.",
    ),
    Doc(
        "03-poetry-anthology-introduction",
        "Introduction to 'Salt and Slate': An Anthology of Hebridean Verse",
        "Moira MacAskill",
        "2023-06-12",
        [
            (
                "Preface",
                "These sixty-one poems were collected between 1998 and 2022 from the islands of Barra, Eriskay and South Uist. About a third are translated from Gaelic by the poets themselves.",
            ),
            (
                "Themes",
                "The sea, absence, peat smoke, the sound of the ferry horn. The oldest piece, a milking song, was recorded from Peigi Nic Neill, who was ninety-four.",
            ),
        ],
        "How many poems does the anthology contain?",
        "Sixty-one.",
    ),
    Doc(
        "04-minutes-parish-council-bench",
        "Minutes: Thornley Parish Council, Item 7 (The Memorial Bench)",
        "Clerk to Thornley Parish Council",
        "2025-05-21",
        [
            (
                "Item 7",
                "Councillor Fenwick proposed installing a memorial bench for the late Mr. Arthur Dunmore on the green. The bench is recycled plastic and costs GBP 640. A plaque is GBP 85 extra.",
            ),
            (
                "Resolution",
                "Approved 6 votes to 1 (Cllr Platt dissenting on cost). Location to be agreed with the Dunmore family and installed before 1 September.",
            ),
        ],
        "How much does the memorial bench cost, and who dissented?",
        "GBP 640 (plaque GBP 85 extra); Councillor Platt dissented.",
    ),
    Doc(
        "05-call-transcript-broadband-fault",
        "Call Transcript: Broadband Fault Report",
        "Contact Centre Quality Team",
        "2025-10-14",
        [
            (
                "Transcript",
                "Agent: Thank you for calling Fennick Broadband, how can I help? Caller: My internet drops every evening about eight o'clock. Agent: Let me check your line. I can see noise on the line between 7:50 and 9:30 pm. Caller: It started when the neighbour bought a new heater. Agent: That's a common cause of interference. I'll book an engineer for Thursday between 8 am and 1 pm.",
            ),
            (
                "Outcome",
                "Engineer visit booked. Reference FB-88241. Customer offered GBP 5 credit for the days affected.",
            ),
        ],
        "What time does the caller's internet drop and what do they think caused it?",
        "About eight o'clock in the evening; they suspect the neighbour's new heater.",
    ),
    Doc(
        "06-whale-watching-sightings-log",
        "Sightings Log: Whale Watching Trips, August 2025",
        "Captain Irene Solberg",
        "2025-09-02",
        [
            (
                "Log",
                "3 August: minke whale, 2 animals, 09:40. 9 August: humpback whale named Fjord, breaching 11 times. 17 August: pod of 12 orca near the headland. 24 August: nothing but dolphins. 30 August: fin whale, 20 m long, 14:15.",
            ),
            (
                "Totals",
                "Eight trips, six with sightings. Total passengers 312. The orca pod was the first seen in the bay since 2021.",
            ),
        ],
        "How many times did the humpback Fjord breach on 9 August?",
        "Eleven times.",
    ),
    Doc(
        "07-ski-resort-conditions-report",
        "Conditions Report: Les Hautes Sources, 14 February 2026",
        "Resort Snow Team",
        "2026-02-14",
        [
            (
                "Snow",
                "Base depth 168 cm at the summit, 94 cm at mid-station, 31 cm at the village. 12 cm of new snow overnight. Avalanche risk level 3 above 2,200 m.",
            ),
            (
                "Lifts",
                "Open: 17 of 23. Closed for wind: the Aiguille chair and the top T-bar. Last descent of the Piste Noire Etoile at 4:15 pm.",
            ),
        ],
        "What is the base depth at the summit, and how many lifts are open?",
        "168 cm at the summit; 17 of 23 lifts open.",
    ),
    Doc(
        "08-readme-vinyl-collection",
        "README: Cataloguing a Vinyl Record Collection",
        "Dev Malhotra",
        "2024-12-02",
        [
            (
                "Overview",
                "A plain-text catalogue of 412 records, sorted by catalogue number. Each record has artist, title, year, label and condition grade (M, NM, VG+, VG, G).",
            ),
            (
                "Conventions",
                "Original pressings are marked with an asterisk. The rarest item is a 1968 mono pressing of 'Hollow Hours' by the Cormorants, graded NM and valued around GBP 900.",
            ),
        ],
        "What is the rarest record in the catalogue and how is it graded?",
        "A 1968 mono pressing of 'Hollow Hours' by the Cormorants, graded NM.",
    ),
    Doc(
        "09-letter-from-the-front-1917",
        "Transcribed Letter, 3 June 1917",
        "Archive Volunteer, Local History Society",
        "2023-04-19",
        [
            (
                "Letter",
                "Dear Mother, I am well and in good heart though the mud is worse than I can tell. We have been moved back four miles to rest and the band played last night. Tell Father the pigeon loft survived. I am sending a pressed cornflower from the field behind the barn. Your son, Tom.",
            ),
            (
                "Archive note",
                "Private Thomas Aldous, 7th Battalion. Killed in action 31 July 1917. The cornflower remains in the original envelope.",
            ),
        ],
        "What did Tom send in the envelope with his 1917 letter?",
        "A pressed cornflower from the field behind the barn.",
    ),
    Doc(
        "10-lab-notebook-yeast-growth",
        "Lab Notebook Entry: Yeast Growth at Three Temperatures",
        "Chidi Okonkwo",
        "2025-03-27",
        [
            (
                "Setup",
                "Saccharomyces cerevisiae in YPD broth, initial OD600 0.05, shaken at 200 rpm. Temperatures: 25, 30 and 37 degrees C.",
            ),
            (
                "Results",
                "Doubling times: 25 degrees C 2.1 hours, 30 degrees C 1.5 hours, 37 degrees C 2.6 hours. Optimum growth at 30 degrees C as expected.",
            ),
        ],
        "What was the yeast doubling time at 30 degrees C?",
        "1.5 hours.",
    ),
    Doc(
        "11-ship-captains-memoir-extract",
        "Memoir Extract: The Roaring Forties Run",
        "Captain Hamish Lennox (ed. Fiona Lennox)",
        "2022-11-08",
        [
            (
                "Extract",
                "We ran the easting down for thirty-eight days with the wool clipper Marwood Bay carrying 3,200 bales. On the twenty-second day a rogue wave stove in the starboard hatch and we lost the cook's galley and a goat named Duchess.",
            ),
            (
                "Afterword",
                "The Marwood Bay made Sydney in 83 days from London, which the family claims was a record for that season.",
            ),
        ],
        "How many days did the Marwood Bay take from London to Sydney?",
        "83 days.",
    ),
    Doc(
        "12-support-faq-3d-printer",
        "Plain-Text FAQ: Tarn T200 3D Printer",
        "Tarn Support",
        "2025-11-19",
        [
            (
                "First layer won't stick",
                "Level the bed with a sheet of paper at all four corners. Clean the bed with isopropyl alcohol. Raise the bed temperature to 60 degrees C for PLA.",
            ),
            (
                "Stringing",
                "Reduce nozzle temperature by 5 degrees C and enable retraction at 5 mm at 40 mm/s. Dry the filament for 4 hours at 45 degrees C.",
            ),
        ],
        "What retraction settings does the FAQ recommend to stop stringing?",
        "5 mm at 40 mm/s.",
    ),
]
