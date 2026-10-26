# Brief: agent file libraries on Cosmos DB

*Version 0.1, 2026-10-22.*

## The problem

Agents can answer questions from "file libraries". A user or admin uploads documents which are stored, and a process can then index them, and an agent with the library attached can search it when a question calls for it.

One current solutions for this index is Azure AI Search Service index. This service counts each index, and the service caps the count: 200 indexes on the S2 and S3 tiers. Given that every agent with files needs a library, every user could build a personal agent, and admin could create hundreds of shared libraries for official agents, this limit is an issue, and the cap is reached however small the libraries are.

## The change

The proposal is to move file libraries to Azure Cosmos DB, and make a library a **value stored on each record** rather than an object the service counts.

- **One container, every library.** Every searchable piece of every document carries its library's ID. Cosmos uses that ID to store each library's content together and to build a **separate vector index for each library inside the same container**. (A vector index is what lets search find text by meaning rather than exact words.) Adding a library adds records, not infrastructure, so there is no design limit on the number of libraries.
- **Cosmos holds everything that is searched.** That means the text, the vectors, the document and library details, and the tags. Blob storage keeps the original files and images, plus complete extracted text for agents to read on demand.
- **Agents work as they do today.** Each attached library exposes tools described by the library's description, and the agent decides per conversation which libraries to use and whether it needs passages, complete documents, or both.

## What gets better

| | Today | Proposed |
|---|---|---|
| Number of libraries | Capped at 200 per search service (S2/S3) | No design limit: three containers per organization, however many libraries |
| Creating a library | Creates a search index | Creates one record |
| Search cost | — | Depends on the size of the library searched, not on how many libraries exist |
| Filtering | — | By the period a document covers, author, custom tags, file type, or upload date, in the same search |
| Images | — | Images in documents are described by AI and searchable; matching images come back to the agent and are shown to the user |
| Library descriptions | Written by hand | AI proposes a description and tags from the actual contents; the admin or owner approves them |
| Search results | — | Each result says what was searched and how much was found, so an agent can tell "nothing here" from "the answer doesn't exist" |
| Library usage | — | A report shows which libraries agents search, how often they come back empty, and how often their results get used |

Dashes mark things we have not checked in the current setup. The comparison claims only what is known.

## Personal agents and official agents

- **Admins** create shared libraries and attach as many as they want to official agents.
- **Users** build personal agents, and each personal agent gets its own library.
- **A personal library can never serve any other agent.** The platform keeps managing who may do what, as today. On top of that, the index records which agent each personal library belongs to, and that record can never change. If a personal library were ever attached to an official agent by mistake, every search from that agent would be refused.

## This is measured, not assumed

The design reuses earlier measurements of the same Cosmos features on live Azure. It tested a container holding 2,000 people's personal content, which is close to an organization full of personal agents. The highlights:

- **Giving each owner its own vector index made searches cheaper and faster.** Expensive searches (the 95th percentile) cost 54–69 units instead of 407–1,063, and took about 100–130 ms instead of 143–295 ms. Accuracy dropped by under 0.2 percentage points.
- **The cost of doing it wrong was measured too.** Searching without naming the owner cost about 220 times more, and searching several owners in one combined query cost about 300 times more. The design avoids both by construction.
- **The storage format matters.** Storing vectors at half precision cut storage by 46% with no measurable loss of accuracy. The chosen compression method lost no documents in very small libraries, where the alternative missed some.
- **Cosmos costs are small.** About $23 to load a million searchable pieces (roughly 30,000–50,000 documents), about $2–3 a month to store them, and under two cents per thousand searches. AI model costs for embeddings and image descriptions are separate and will be larger.

`evidence.md` has every number with its source and how solid it is.

## What the demo will show

1. 1,000+ libraries in one container, against the 200 limit, with search cost unchanged.
2. An admin creates a library, uploads files, and approves the AI-proposed description and tags.
3. An official agent answers a question, choosing the right library and filters itself, with citations.
4. The agent finds a chart inside a slide deck and shows it.
5. A user's personal agent answers from that user's own files.
6. That personal library cannot be attached to an official agent, and is refused even if forced.
7. The library usage report.

## What is not proven yet

- **This exact combination of Cosmos settings has never been built together.** Each was measured separately. The first build includes a test that proves the combination before anything depends on it.
- **The settings were tested on public reference text, not workplace documents.** The demo corpus will be real business documents.
- **The chosen compression method is a Microsoft preview feature.** A generally available alternative exists at slightly lower accuracy.
- **Very large deployments (millions of searchable pieces spread over many servers) are untested.** The design should behave the same at that size, but nobody has measured it.
- **Microsoft's documentation asks users of this partitioning and vector-search combination to contact the Cosmos team.** That is a step before production.
