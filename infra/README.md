# infra/

Bicep for one organization's deployment (`docs/spec.md` §3, `docs/build-plan.md` Phase 0).

| File | What it deploys |
|---|---|
| `main.bicep` | Everything below, plus the user-assigned identity, Azure OpenAI with two deployments, Document Intelligence, Log Analytics and Application Insights |
| `modules/cosmos-account.bicep` | The Cosmos account (serverless by default, `EnableNoSQLVectorSearch`, `disableLocalAuth`) and database `fli` |
| `modules/cosmos-containers.bicep` | The three containers, loaded from `cosmos/*.json`, never retyped |
| `modules/roles.bicep` | The spec §3 roles for one principal, including the Cosmos data-plane role |

Resource names are `<prefix>-<kind>-<suffix>`. The suffix is derived from the resource group id, so names stay the same on every redeploy to that group.

Local auth is off everywhere: Cosmos, Storage (no shared key), Service Bus, Azure OpenAI and Document Intelligence. Everything authenticates with Entra ID.

## Deploy and run the canary

Prerequisites: the Azure CLI, Python 3.12, and an account that can create resources and role assignments in the subscription (Owner, or Contributor plus User Access Administrator).

```bash
az login
az account set --subscription <SUBSCRIPTION_ID>
```

Check that the region offers both models, and on which SKUs:

```bash
az cognitiveservices model list --location <REGION> --query "[?model.name=='text-embedding-3-large' || model.name=='gpt-5-mini'].{name:model.name, version:model.version, skus:join(',', model.skus[].name)}" -o table
```

If either is missing, set `openAiLocation=<OTHER_REGION>`, or change `chatModelName` and `chatModelVersion`, on the deploy below.

```bash
az group create --name <RESOURCE_GROUP> --location <REGION>
az deployment group what-if -g <RESOURCE_GROUP> -n main -f infra/main.bicep -p prefix=<PREFIX> operatorPrincipalId=$(az ad signed-in-user show --query id -o tsv)
az deployment group create -g <RESOURCE_GROUP> -n main -f infra/main.bicep -p prefix=<PREFIX> operatorPrincipalId=$(az ad signed-in-user show --query id -o tsv)
```

`operatorPrincipalId` grants you the same data-plane roles as the app identity, because the canary and the local demo run as your `az login` identity.

Load the outputs into your shell, wait about five minutes for role assignments to propagate, then run the canary:

```bash
eval "$(python scripts/deployment_env.py -g <RESOURCE_GROUP>)"
python scripts/canary.py --corpus <CORPUS_DIR>
```

Check the corpus first with `python scripts/canary.py --corpus <CORPUS_DIR> --count-only`, which needs no Azure.

The corpus is a directory of real documents (PDF, DOCX, PPTX, XLSX, CSV, TXT, MD, HTML) that together give at least 1,200 chunks. 1,500 chunks is about 750,000 tokens, or roughly 600–800 pages of prose. Scanned PDFs are skipped, because the canary does no OCR. Public annual reports in the demo's domain work well, and can be reused as the demo corpus.

The canary prints each check and writes `records/canary-<UTC timestamp>.json`. **The Phase 0 exit check is a passing run with that record committed.**

## If the canary fails

- Container definitions don't match: the service stored something other than `cosmos/*.json`. Check `quantizerType` first. Bicep has no type for it, so it is passed through unchecked.
- `401`/`403` from Cosmos or Azure OpenAI: role assignments have not propagated yet. Wait and rerun.
- Index not engaged, or low recall: recreate the account and rerun (`docs/evidence.md` E17). If it fails again, fall back to the product quantizer and float32 (`docs/build-plan.md` Phase 0, "If it fails"). That needs a new container.
- An interrupted run leaves its test library behind. The canary prints the command that removes it: `python scripts/canary.py --cleanup <libraryId>`.
