# ruff: noqa: E501
"""Sample PDFs and Word documents. Every subject and fact here is invented."""

from .render import Doc

PDF = [
    Doc(
        "01-wexcombe-lighthouse-keepers-log",
        "Wexcombe Point Lighthouse: Keeper's Log, Winter 1911",
        "Edith Marlowe",
        "2023-11-04",
        [
            (
                "The storm of 14 January",
                "On the night of 14 January 1911 the lamp at Wexcombe Point burned for nineteen hours without a break after the clockwork rotation seized. Head keeper Alasdair Quill turned the lens by hand in shifts of forty minutes with his two assistants, using a brass crank that had last been used during the 1887 refit.\n\nThe relief boat could not land for eleven days. Rations were reduced to one tin of pilchards between three men per day, supplemented by gulls' eggs that Quill described in the log as 'a penance'.",
            ),
            (
                "Equipment notes",
                "The paraffin vapour burner consumed 2.4 litres per hour. The fog bell was struck every ninety seconds. The barometer reading at its lowest was 951 millibars at 3:10 in the morning.",
            ),
        ],
        "How long did the Wexcombe Point lamp burn without a break on the night of the storm, and why was the lens turned by hand?",
        "Nineteen hours; the clockwork rotation seized, so the keepers turned the lens by hand in forty-minute shifts.",
    ),
    Doc(
        "02-sourdough-hydration-study",
        "Hydration and Crumb Structure in Rye-Wheat Sourdough",
        "Dr. Ingrid Solheim",
        "2024-05-19",
        [
            (
                "Method",
                "Forty loaves were baked at hydration levels from 68% to 84% in 2% steps, five loaves per level, using a starter maintained at 22 degrees C and fed at a 1:5:5 ratio. All loaves used a 30% dark rye, 70% bread wheat flour blend and a 14 hour bulk ferment.",
            ),
            (
                "Results",
                "Open crumb with large irregular holes first appeared at 78% hydration. Above 82% the loaves spread sideways in the oven and lost 11% of their height. The best-scoring loaf by the panel of nine tasters was baked at 76% hydration, with a mean score of 8.4 out of 10.",
                [
                    ("Hydration", "Mean height (cm)", "Panel score"),
                    ("70%", "9.8", "6.9"),
                    ("76%", "9.1", "8.4"),
                    ("82%", "7.9", "7.2"),
                ],
            ),
        ],
        "Which hydration level produced the best-scoring sourdough loaf and what was its panel score?",
        "76% hydration, with a mean panel score of 8.4 out of 10.",
    ),
    Doc(
        "03-tenancy-agreement-14-orchard-row",
        "Assured Shorthold Tenancy Agreement: 14 Orchard Row",
        "Halloran & Pike Solicitors",
        "2025-02-03",
        [
            (
                "Term and rent",
                "The landlord, Mrs. Beatrix Oyelaran, lets 14 Orchard Row to the tenant, Mr. Callum Reeve, for a fixed term of 12 months starting 1 March 2025. Rent is GBP 1,185 per calendar month, payable in advance on the first day of the month by standing order.",
            ),
            (
                "Deposit and obligations",
                "A deposit of GBP 1,365 is protected in a government-approved scheme within 30 days. The tenant must keep the garden lawn cut and may not keep any pet larger than a cat without written consent. The tenant may not hold a drum kit on the premises.",
            ),
            (
                "Ending the tenancy",
                "Either party must give two months' written notice after the first six months. Break clause: the landlord may end the tenancy early only if she or her immediate family intend to live in the property.",
            ),
        ],
        "What is the monthly rent and the deposit under the Orchard Row tenancy?",
        "Rent is GBP 1,185 per month and the deposit is GBP 1,365.",
    ),
    Doc(
        "04-hypertension-clinic-guideline",
        "Community Clinic Guideline: Initial Management of Stage 1 Hypertension",
        "Dr. Rashid Karimi",
        "2025-08-11",
        [
            (
                "Diagnosis",
                "Clinic readings of 140/90 mmHg or higher should be confirmed with 24-hour ambulatory monitoring where available. A daytime average of 135/85 mmHg or higher confirms the diagnosis. Take two readings at least one minute apart and record the lower.",
            ),
            (
                "First-line advice",
                "For adults under 60 with no diabetes, offer lifestyle advice for three months before medication unless the 10-year cardiovascular risk exceeds 10%. Advise a dietary sodium target of under 2.3 grams per day and 150 minutes of moderate activity weekly.",
            ),
            (
                "Review",
                "Review at four weeks if starting treatment, then every six months once stable. Home monitoring: seven days, two readings morning and evening, discarding day one.",
            ),
        ],
        "What daytime ambulatory average confirms hypertension in this guideline, and what sodium target is advised?",
        "135/85 mmHg or higher; under 2.3 grams of sodium per day.",
    ),
    Doc(
        "05-orbital-mechanics-lecture-notes",
        "Lecture 6: Hohmann Transfers and the Cost of Changing Orbit",
        "Prof. Anneliese Brandt",
        "2024-10-28",
        [
            (
                "The two-burn transfer",
                "A Hohmann transfer moves a spacecraft between two coplanar circular orbits using an elliptical orbit tangent to both. The first burn raises the apoapsis; the second circularises at the target radius. For Earth orbit at 6,678 km to geostationary radius of 42,164 km the total delta-v is about 3.9 km/s.",
            ),
            (
                "Worked example",
                "Using the vis-viva equation, a satellite at 400 km altitude needs 2.43 km/s for the first burn and 1.47 km/s for the second. The transfer takes 5 hours 16 minutes, half the period of the transfer ellipse.",
            ),
            (
                "Limits",
                "Bi-elliptic transfers become cheaper than Hohmann only when the ratio of final to initial radius exceeds 11.94, though they take far longer.",
            ),
        ],
        "At what radius ratio do bi-elliptic transfers beat Hohmann transfers?",
        "When the ratio of final to initial radius exceeds 11.94.",
    ),
    Doc(
        "06-bridge-inspection-report-tarn-viaduct",
        "Inspection Report: Tarn Viaduct, Span 7",
        "Marguerite Dufresne, C.Eng.",
        "2026-03-17",
        [
            (
                "Summary",
                "Span 7 of the Tarn Viaduct was inspected on 9 and 10 March 2026. Condition grade: 3 (fair), unchanged since 2022. Immediate closure is not required.",
            ),
            (
                "Findings",
                "Three transverse cracks up to 0.4 mm wide were recorded on the soffit of the deck, all within the tolerance of 0.5 mm. Expansion joint E7 shows 18 mm of lateral creep. Corrosion section loss of 6% was measured on bearing plate B2, against a repair threshold of 10%.",
            ),
            (
                "Recommendations",
                "Re-inspect in 12 months. Repaint bearing plates B1 to B4 by September 2026. Install two crack gauges on the soffit.",
            ),
        ],
        "What is the corrosion section loss on bearing plate B2 and what is the repair threshold?",
        "6% section loss against a 10% repair threshold.",
    ),
    Doc(
        "07-olive-grove-lease-letter",
        "Lease Offer: Terraced Olive Grove, Contrada Fossano",
        "Studio Legale Brancato",
        "2024-07-09",
        [
            (
                "Property",
                "The offer concerns 2.8 hectares of terraced olive grove with 410 mature trees of the Ogliarola variety, a stone press house and a cistern of 30 cubic metres, in Contrada Fossano near Monopoli.",
            ),
            (
                "Terms",
                "Lease term nine years, renewable once. Annual rent EUR 7,400, adjusted yearly by 75% of the ISTAT consumer index. The lessee must prune the trees every second winter and may not graft new varieties without consent. The lessor keeps the right to harvest the three oldest trees beside the cistern for personal use.",
            ),
        ],
        "Which trees does the lessor keep the right to harvest, and what is the annual rent?",
        "The three oldest trees beside the cistern; EUR 7,400 per year.",
    ),
    Doc(
        "08-medieval-wool-trade-essay",
        "The Wool Staple at Calais: A Reassessment",
        "Dr. Thomas Haverford",
        "2023-03-22",
        [
            (
                "Argument",
                "Between 1363 and 1558 the Calais Staple controlled English wool exports to the continent. Historians have long attributed its decline to the growth of the domestic cloth industry. This essay argues that the loss of the Burgundian market after 1477 mattered more than the domestic shift.",
            ),
            (
                "Evidence",
                "Customs accounts show exports of 8,400 sacks in 1475 falling to 3,100 sacks by 1490, a drop of 63%. Cloth exports over the same period rose by only 22%, too little to explain the collapse.",
            ),
            (
                "Conclusion",
                "Political disruption of continental demand, rather than competition at home, explains the Staple's loss of trade in the late fifteenth century.",
            ),
        ],
        "According to the essay, by what percentage did wool exports fall between 1475 and 1490?",
        "By 63%, from 8,400 sacks to 3,100 sacks.",
    ),
    Doc(
        "09-bird-survey-saltmarsh-2025",
        "Winter Wader Survey, Harrow Saltmarsh 2025",
        "Naomi Tetteh",
        "2025-03-05",
        [
            (
                "Counts",
                "Six counts were made between December 2024 and February 2025 at high tide. The peak count was 2,312 birds on 19 January, dominated by dunlin (1,420) and knot (610). Curlew numbers peaked at 88.",
            ),
            (
                "Notable records",
                "A single spotted redshank was seen on 2 February, the first at the site since 2019. Disturbance by dog walkers caused flocks to flush on four of the six visits. The recommendation is a seasonal dog exclusion zone on the north embankment from 1 November to 28 February.",
            ),
        ],
        "What was the peak count and how many dunlin were in it?",
        "2,312 birds on 19 January, including 1,420 dunlin.",
    ),
    Doc(
        "10-chess-endgame-primer",
        "Rook Endgames: The Lucena and Philidor Positions",
        "Viktor Szabo",
        "2022-12-14",
        [
            (
                "Lucena",
                "The Lucena position arises when the stronger side has a king on the eighth rank in front of a pawn on the seventh, with the defending king cut off by at least one file. The winning method is known as building a bridge: the rook moves to the fourth rank and then shelters the king from checks.",
            ),
            (
                "Philidor",
                "The Philidor defence holds the draw by keeping the defending rook on the sixth rank until the pawn advances to that rank, then switching to checks from behind. Keep the rook on the third rank (counting from the defender's side) to prevent the king from advancing.",
            ),
            (
                "Practice",
                "Practise both positions until you can play them in under two minutes from memory. Szabo recommends 20 repetitions a week.",
            ),
        ],
        "What technique wins the Lucena position?",
        "Building a bridge: the rook goes to the fourth rank and shelters the king from checks.",
    ),
    Doc(
        "11-solar-installation-quote",
        "Quotation: 6.4 kW Rooftop Solar and 10 kWh Battery",
        "Brightfield Energy Ltd",
        "2026-04-02",
        [
            (
                "System",
                "16 panels of 400 W each, one hybrid inverter and a 10 kWh lithium iron phosphate battery. Estimated annual generation is 5,900 kWh for a south-facing roof at 35 degrees pitch.",
            ),
            (
                "Price and payback",
                "Total price GBP 11,860 including VAT at 0%. Estimated annual saving GBP 1,020, giving a payback of 11.6 years. The inverter carries a 12-year warranty; panels carry a 25-year performance warranty at 87.4% of rated output.",
            ),
            (
                "Installation",
                "Scaffolding is needed on the north gable only. Installation takes two days. Quote valid for 45 days.",
            ),
        ],
        "What is the estimated payback period of the solar installation quote?",
        "11.6 years.",
    ),
    Doc(
        "12-glacier-mass-balance-brief",
        "Field Brief: Mass Balance on the Kjerringvatn Glacier, 2025 Season",
        "Dr. Pernille Aas",
        "2025-10-21",
        [
            (
                "Measurements",
                "Stakes were drilled at nine sites between 1,120 and 1,610 metres. The glacier-wide net balance for the 2025 hydrological year was minus 1.37 metres water equivalent, the fifth most negative in the 38-year record.",
            ),
            (
                "Context",
                "Summer ablation reached 4.9 metres water equivalent at the lowest stake. Winter accumulation was 3.6 metres at the highest. The equilibrium line altitude rose to 1,490 metres, about 120 metres above the long-term mean.",
            ),
        ],
        "What was the glacier-wide net balance for 2025?",
        "Minus 1.37 metres water equivalent.",
    ),
]

DOCX = [
    Doc(
        "01-allotment-seasonal-planting-guide",
        "Allotment Planting Guide for Cool Temperate Climates",
        "Bernadette Quigley",
        "2024-02-12",
        [
            (
                "Early spring",
                "Sow broad beans under cloche from late February. Plant first early potatoes (variety Swift) on 20 March, 30 cm apart in rows 60 cm apart, and earth up when shoots reach 20 cm.",
            ),
            (
                "Summer succession",
                "Sow carrots every three weeks between April and July to avoid a glut. Beetroot sown after 15 June rarely bolts. Net brassicas against cabbage white from the day of planting.",
            ),
            (
                "Winter cover",
                "Sow green manure of field beans in October. Mulch the rhubarb crowns with 10 cm of well-rotted manure after the first frost.",
            ),
        ],
        "When should first early potatoes (Swift) be planted and how far apart?",
        "On 20 March, 30 cm apart in rows 60 cm apart.",
    ),
    Doc(
        "02-employee-grievance-procedure",
        "Grievance Procedure for Staff of Marlbury Parish Council",
        "Gwen Prothero, Clerk",
        "2023-09-01",
        [
            (
                "Stage one",
                "An employee should raise a grievance in writing with the Clerk within 10 working days of the event. The Clerk will hold a meeting within 5 working days and respond in writing within 3 working days after that.",
            ),
            (
                "Stage two",
                "If unresolved, the employee may appeal in writing to the Staffing Committee within 7 working days. The committee will hear the appeal within 14 days. Its decision is final. An employee may be accompanied by a colleague or a trade union representative.",
            ),
        ],
        "How many working days does an employee have to raise a grievance in writing?",
        "10 working days of the event.",
    ),
    Doc(
        "03-kombucha-brewing-procedure",
        "Small-Batch Kombucha: Brewing and Safety Procedure",
        "Otieno Mwangi",
        "2025-06-30",
        [
            (
                "First fermentation",
                "Brew 2 litres of sweet tea using 140 g of sugar and 6 g of tea. Cool to under 30 degrees C before adding the SCOBY and 200 ml of mature kombucha. Cover with cloth and ferment at 24 degrees C for 7 to 10 days.",
            ),
            (
                "Safety",
                "The pH must be below 4.2 by day four. If it is not, discard the batch. Mould appears as dry, fuzzy and coloured; it is not the same as the brown yeast strands that hang from the SCOBY.",
            ),
        ],
        "By what day and below what pH must kombucha have fallen, or be discarded?",
        "Below pH 4.2 by day four.",
    ),
    Doc(
        "04-court-small-claims-guide",
        "Guide to Small Claims: Preparing Your Evidence Bundle",
        "Citizens' Advice Volunteers, Nairn",
        "2024-04-18",
        [
            (
                "Bundle contents",
                "Number every page. Include the claim form, your witness statement, contracts, receipts, photographs and a chronology. Put documents in date order, with the most important in a short front section.",
            ),
            (
                "Timetable",
                "Send copies to the other party at least 14 days before the hearing. Bring three copies to court: one each for you, the judge and the other side. Hearings for claims below GBP 1,500 usually take under an hour.",
            ),
        ],
        "How many days before the hearing must evidence be sent to the other party?",
        "At least 14 days.",
    ),
    Doc(
        "05-ceramics-glaze-recipes",
        "Studio Handbook: Cone 6 Glaze Recipes",
        "Mireille Fontaine",
        "2025-01-27",
        [
            (
                "Celadon",
                "Custer feldspar 45, silica 22, whiting 18, kaolin 10, talc 5. Add 1.5% red iron oxide for pale green. Fire to cone 6 in oxidation with a 15 minute hold.",
            ),
            (
                "Tenmoku",
                "Nepheline syenite 40, silica 25, whiting 12, ball clay 8, red iron oxide 10, rutile 5. Apply thickly. It breaks to rust on edges.",
            ),
            (
                "Troubleshooting",
                "Crazing indicates that the glaze contracts too much: increase silica by 3 parts. Pinholes: slow the cooling through 900 degrees C.",
            ),
        ],
        "What iron oxide percentage is added to the celadon glaze?",
        "1.5% red iron oxide.",
    ),
    Doc(
        "06-community-garden-funding-proposal",
        "Funding Proposal: Brookside Community Orchard",
        "Tamsin Okoye",
        "2026-05-08",
        [
            (
                "The project",
                "We ask for GBP 18,400 to plant 62 heritage fruit trees on the disused bowling green at Brookside, including 14 apple varieties native to Herefordshire.",
            ),
            (
                "Budget",
                "Itemised costs are below.",
                [
                    ("Item", "Cost (GBP)"),
                    ("Trees and stakes", "6,200"),
                    ("Fencing", "5,400"),
                    ("Water butts and irrigation", "2,300"),
                    ("Volunteer training", "1,700"),
                    ("Contingency", "2,800"),
                ],
            ),
            (
                "Outcomes",
                "Forty local volunteers trained, two school visits a term, and a community cider day in the third autumn.",
            ),
        ],
        "How many trees will the orchard plant, and how much funding is requested?",
        "62 trees and GBP 18,400.",
    ),
    Doc(
        "07-ballet-class-syllabus",
        "Intermediate Ballet Syllabus: Term 2",
        "Anya Volkova",
        "2025-01-06",
        [
            (
                "Barre",
                "Pliés in the five positions, tendus, degagés, rond de jambe a terre and fondus. New this term: frappés with an accent to the front.",
            ),
            (
                "Centre work",
                "Adage combinations of 32 counts, pirouettes from fourth and fifth positions, and petit allegro including assemblés and sissonnes. The end-of-term showing is on 21 March at 6 pm in the Corn Exchange.",
            ),
        ],
        "When and where is the end-of-term ballet showing?",
        "21 March at 6 pm in the Corn Exchange.",
    ),
    Doc(
        "08-volcano-evacuation-plan",
        "Evacuation Plan: Mount Karvelo Alert Level Three",
        "Civil Protection Office, Tavira Province",
        "2024-12-09",
        [
            (
                "Triggers",
                "Level three is declared when seismic tremor exceeds 90 events per hour for six consecutive hours or when gas emission of SO2 exceeds 1,800 tonnes per day.",
            ),
            (
                "Zones",
                "Zone Red (within 4 km of the crater) evacuates first, by bus from five assembly points beginning at the Santo Rocco church. Zone Amber (4 to 9 km) evacuates within 12 hours. Rescue dogs and livestock are the responsibility of the owner and must travel in the second convoy.",
            ),
        ],
        "What gas emission level triggers alert level three on Mount Karvelo?",
        "SO2 emission above 1,800 tonnes per day.",
    ),
    Doc(
        "09-vintage-camera-repair-notes",
        "Service Notes: Zenith Rangefinder Model 3, Shutter Repair",
        "Lars Nyberg",
        "2023-08-25",
        [
            (
                "Symptoms",
                "The shutter sticks at speeds slower than 1/30. This is almost always old lubricant on the escapement. Do not use WD-40.",
            ),
            (
                "Procedure",
                "Remove the lens board using the three captive screws. Clean the escapement with naphtha on a swab and relubricate with a single drop of clock oil at the 3 o'clock pivot. Test at every speed, listening for an even tick. Allow 24 hours before final timing on a speed tester.",
            ),
        ],
        "Which lubricant is used on the shutter escapement pivot and how much?",
        "A single drop of clock oil at the 3 o'clock pivot.",
    ),
    Doc(
        "10-aquarium-cycle-instructions",
        "Cycling a New Freshwater Aquarium Without Fish",
        "Delphine Arnaud",
        "2024-09-14",
        [
            (
                "Fishless cycle",
                "Add pure ammonia until the test shows 2 ppm. Test daily. When ammonia and nitrite both read zero within 24 hours of dosing 2 ppm, the tank is cycled. This typically takes 4 to 6 weeks at 26 degrees C.",
            ),
            (
                "Stocking",
                "Add fish in stages: no more than a third of the intended stock at once. Neon tetras are a hardy first choice, in a school of at least eight.",
            ),
        ],
        "What ammonia dose is used in the fishless cycle and how long does it take?",
        "2 ppm; typically 4 to 6 weeks at 26 degrees C.",
    ),
    Doc(
        "11-cricket-umpires-law-summary",
        "Summary of the Laws: LBW and the Umpire's Decision",
        "Peter Allsopp",
        "2025-04-04",
        [
            (
                "LBW",
                "A batter is out leg before wicket if the ball would have hit the stumps, was not pitched outside leg stump, and the first impact is in line between wicket and wicket or outside off stump when a shot is not attempted.",
            ),
            (
                "Decision review",
                "Each side receives two unsuccessful reviews per innings in Test matches. The 'umpire's call' zone covers 50% of the ball's width on the stumps for impact and 25% of the ball for wicket-hitting.",
            ),
        ],
        "How many unsuccessful reviews does each side receive per Test innings?",
        "Two.",
    ),
    Doc(
        "12-pastry-laminated-dough-sop",
        "Laminated Dough: Standard Operating Procedure",
        "Chef Mathieu Girard",
        "2026-07-14",
        [
            (
                "Dough and butter",
                "Use 500 g of strong flour, 10 g of salt, 55 g of sugar, 10 g of yeast, 140 g of milk and 140 g of water. Butter block: 280 g of 82% fat butter pounded to 18 cm square at 14 degrees C.",
            ),
            (
                "Folds",
                "Encase the butter, then give three single folds with 30 minutes' rest in the fridge between each. Final roll to 4 mm. Croissants are cut as triangles 9 cm wide at the base and 20 cm tall, proofed at 27 degrees C for 2 hours.",
            ),
        ],
        "What size and temperature is the butter block for the laminated dough?",
        "280 g of 82% fat butter pounded to 18 cm square at 14 degrees C.",
    ),
]
