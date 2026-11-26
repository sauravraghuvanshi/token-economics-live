# Token Economics · Live

Compare four AI cost levers on the same English-language CloudDesk workload.
Each page explains **Without the lever**, **With the lever**, and **What to
look for**. The run ledger then explains what happened using measured costs and
grades. Keep a change only if cost per accepted outcome falls and quality holds.
See [Instruction.md](Instruction.md) for the original design and pricing rules.
The current English workload supersedes the original multilingual examples.

## What to look for

| Experiment | Without the lever | With the lever |
|---|---|---|
| Prompt caching | The question changes the leading text. | Shared policies come first; compare cache writes and reads while every question still gets a fresh answer. |
| Model routing | The frontier model answers every question. In Guided Foundry, the direct baseline disables input caching where supported. | Decision classifies just the question. Foundry Model Router uses GPT-4.1-mini for routine cases and GPT-6.1 Sol for harder ones. Both lanes use the same fresh input. Inspect shows the confirmed model, routing mode, both fees and any billed cache writes. |
| Response caching | Every question gets a fresh answer. | An exact fact match skips generation. Both lanes disable provider prompt caching on the supported deployment. If either lane reports input-cache reads or writes, the app shows the raw totals but does not publish an isolated savings verdict. |
| Retry & kill switch | The agent retries failures even when evidence repeats, up to six attempts per invoice. | The default allows one initial attempt and three retries at most. A tighter token or spend rule can stop earlier. |

Use **Stop baseline retries** during an invoice run to stop new baseline
retries. An attempt already running can finish and is counted; later baseline
invoices get one initial attempt. The lever lane finishes normally. A stop
request does not replace the Scoreboard or the last recording.

The guided routing mode uses [Foundry per-request routing controls](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/model-router-routing-controls).
Those controls are **preview** and have no production SLA. The demo requires
Foundry to confirm the requested mode and return an eligible model; it does not
silently reroute a failed request. In response caching, `PROMPT_CACHE_MODE=auto`
is a compatibility setting for older models that cannot disable implicit
prompt caching. It does not guarantee zero input-cache activity.

## Run locally

Use Python 3.12. Fake mode runs all nine scenarios without Azure or CDN assets.
The tight budget below is for **Fake fixtures only**.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt
$env:FOUNDRY_MODE = 'fake'
$env:TIGHT_KILL_SWITCH_USD = '0.0007'
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open [localhost:8000](http://127.0.0.1:8000). Copy `.env.example` to `.env` if
you need to persist non-secret local overrides. Use synthetic demo questions
only. This demo is not designed for confidential inputs: its recordings retain
request and response details for Inspect.

From a second terminal, validate both the code and the running app:

```powershell
$env:FOUNDRY_MODE = 'fake'
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m mypy app
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m scripts.smoke_test --base-url http://127.0.0.1:8000 --source fake --rounds 5 --assert-stories --verify-replays
.\.venv\Scripts\python.exe -m scripts.smoke_test --base-url http://127.0.0.1:8000 --source fake --rounds 5 --max-counts --assert-stories --verify-replays
```

For browser verification, run `python -m scripts.browser_test --channel msedge`
in the same environment. Alternatively, run `python -m playwright install chromium`
and omit `--channel`. Both typefaces are self-hosted with their open font
licenses; playback needs no CDN.

## Azure configuration and deployment

The [hosted demo](https://app-tokecon-q7yxpkuusfxba.azurewebsites.net/) runs in
`rg-tokecon-demo` in **West US 3**. East US 2 lacked usable App Service regional
quota. The deployment uses a dedicated Foundry account and does not change the
existing East US 2 account.

Requires Azure CLI, Azure Developer CLI (`azd`), a subscription, and permission
to provision the resources and assign roles at their scopes. Sign in as the
operator using `az login` and `azd auth login`. For local Live runs you also need
Foundry User on the account and, if required for Decision, Cognitive Services
User. No model API key is used.

Use the approved subscription, location and Foundry account. Locate the exact
model versions and deployments listed in the specification before provisioning.
Set `DEPLOY_MODELS=true` **only** after checking that the five deployment names
are unused and that you have quota. Leave it `false` to reuse five existing
deployments; set their names via the five variables in `.env.example`. The
default deploys no new Foundry account. `CREATE_FOUNDRY=true` opts into a separate
account and project plus five deployments.

```powershell
$sub = '<approved subscription ID>'
azd env new tokecon-demo --no-prompt
azd env set AZURE_SUBSCRIPTION_ID $sub
azd env set AZURE_LOCATION '<approved region>'
azd env set FOUNDRY_RESOURCE_ID '<existing account ARM resource ID>'
azd env set FOUNDRY_ENDPOINT 'https://<resource>.services.ai.azure.com'
azd env set FOUNDRY_PROJECT_ENDPOINT 'https://<resource>.services.ai.azure.com/api/projects/<project>'
azd env set AZURE_OPENAI_BASE_URL 'https://<resource>.openai.azure.com/openai/v1/'
azd env set FOUNDRY_USER_ROLE_ID (az role definition list --subscription $sub --name 'Foundry User' --query '[0].name' -o tsv)
azd env set VAULT_SECRETS_USER_ROLE_ID (az role definition list --subscription $sub --name 'Key Vault Secrets User' --query '[0].name' -o tsv)
azd env set MONITORING_PUBLISHER_ROLE_ID (az role definition list --subscription $sub --name 'Monitoring Metrics Publisher' --query '[0].name' -o tsv)
```

Look up `Azure AI User` if the renamed `Foundry User` is not present. Set
`COGNITIVE_SERVICES_USER_ROLE_ID` by the same lookup if the Decision endpoint
requires it. Set the router to **Balanced** with GPT-4.1-mini, GPT-4.1, and GPT-6.1 Sol,
covered by the pricing file. The guided mode can narrow that set, not add models.
If a separate operator is responsible for Foundry, have them apply those settings
and allow time for them to take effect.

In PowerShell 7, enter the operator-approved random demo code (16+ characters)
at a masked prompt. The secure Bicep parameter seeds the Key Vault secret, and
the app setting contains only the Key Vault reference. Do not put the value in
Git, a shell command argument, or build logs.

```powershell
$env:DEMO_ACCESS_CODE = Read-Host -MaskInput 'Demo access code'
try {
    azd provision --no-prompt
} finally {
    Remove-Item Env:\DEMO_ACCESS_CODE -ErrorAction SilentlyContinue
}
```

For a new environment, publish the agents before deploying the web package.
Use the exact project endpoint and directory returned by Azure (the provision
step also exports `FOUNDRY_PROJECT_ENDPOINT`):

```powershell
python -m scripts.sync_agents --project-endpoint '<confirmed project endpoint>' --tenant-id '<directory ID>' --apply
azd deploy web --no-prompt
```

In [Foundry](https://ai.azure.com/), open **Token Economics Live** (`token-economics-live`),
go to **Agents**, and filter by `tokecon-`:

| Workload | Without the lever | With the lever |
|---|---|---|
| Support | `tokecon-support-baseline` | `tokecon-support-lever`; `tokecon-support-lever-small` for the small tier; `tokecon-support-lever-router` for Foundry routing |
| Invoices | `tokecon-invoice-baseline` | `tokecon-invoice-lever` |

These are real Prompt Agents, not labels on shared model calls. The server
pins each active version and checks the `agent_reference` returned by Foundry.
**Inspect** shows the name, version and response ID alongside raw token usage.
The app runs the cache, routing decisions, validation, simulated currency
conversion and retry policy around those agents. It can recover an omitted
subtotal from a single printed field only when the line amounts and total
independently agree. On retry it recalculates derived amounts only if a second
extraction confirms the printed inputs. The validator and Decision reviewer
still have to pass; Inspect shows raw extraction and arithmetic evidence
separately. A response-cache hit skips the answer agent. Use `scripts.sync_agents`
without `--apply` to verify the live definitions without editing them. For local
Live runs in a nondefault directory, set `AZURE_TENANT_ID` to use an explicit
Azure CLI credential.

Provisioning uses one Linux P0v3 instance with one Gunicorn/Uvicorn worker.
Do not scale workers or instances: the run registry, active-run guard and cache
are process-local. `/home/data` persists the rolling model spend ledger, run
recordings and scoreboard. The hourly ledger limits inference, not the hosting
bill. The App Service plan and private endpoint together cost about **$64 per
730 hours** at list rates, before usage, DNS, Key Vault and monitoring.

The managed identity reaches the vault through VNet integration and a private
endpoint. Public vault access remains disabled. For the hosted demo, use the
`demo-access-code` secret in `kv-tok-q7yxpkuusfxba` from an authorized account
on the private network. On the original Windows account, the local encrypted
copy is `.azure\tokecon-demo\demo-access-code.dpapi`. To smoke-test with it,
decrypt it only into the PowerShell 7 process environment:

```powershell
$url = 'https://app-tokecon-q7yxpkuusfxba.azurewebsites.net'
$code = ConvertTo-SecureString (Get-Content -Raw '.azure\tokecon-demo\demo-access-code.dpapi').Trim()
try {
    $env:DEMO_ACCESS_CODE = ConvertFrom-SecureString $code -AsPlainText
    .\.venv\Scripts\python.exe -m scripts.smoke_test --base-url $url
} finally {
    Remove-Item Env:\DEMO_ACCESS_CODE -ErrorAction SilentlyContinue
    $code.Dispose()
}
```

In the browser, enter the same code in the app's access dialog after pressing
**Run**. Use only a private terminal or the vault to retrieve the value. Do
not paste it into chat or put it in a slide. Replay doesn't require the code.

The app must return `ok` at `/healthz`; `/api/status` must list all five models
and six agents as `ok` and report verified prices without missing meters. The startup probes
use managed identity and can take time after a fresh deployment or a role
change. Unauthenticated status reads do not trigger inference. A code-authenticated
status refresh is limited to once per minute.

### GitHub Actions releases

[The workflow](.github/workflows/ci-deploy.yml) runs offline on pushes and pull
requests. It packages the tested revision without local state or stale recordings,
then exercises that ZIP with five Fake rounds and replays at each workload size.
Chromium verifies the nine modes at two projector sizes and on mobile. Main's
`tokecon-fake-smoke` artifact contains the synthetic results, not Azure measurements.
Only the current `main` revision in the [private repository](https://github.com/sauravraghuvanshi/token-economics-live)
can deploy. The publish job is disabled until `AZURE_DEPLOY_ENABLED` is `true`.

For the one-time setup, use PowerShell 7.4+ in this checkout, signed into the
site's subscription with permission to deploy a managed identity and assign an
Azure role on the App Service. GitHub CLI must be signed into the repository
owner's account. Preview the changes first:

```powershell
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true
$repo = 'sauravraghuvanshi/token-economics-live'
$sub = '6e73c798-4f1a-4ada-b279-b64f9ce1278d'
$group = 'rg-tokecon-demo'
$app = 'app-tokecon-q7yxpkuusfxba'
az account get-access-token --subscription $sub --resource 'https://management.azure.com/' --output none
$oidc = gh api "repos/$repo/actions/oidc/customization/sub" | ConvertFrom-Json
if (!$oidc.use_default -or !$oidc.use_immutable_subject -or !$oidc.sub_claim_prefix) {
    throw 'Expected the default immutable GitHub OIDC subject.'
}
$subject = "$($oidc.sub_claim_prefix):ref:refs/heads/main"
$identityArgs = @('--subscription', $sub, '--resource-group', $group,
    '--template-file', '.\infra\github-actions.bicep',
    '--parameters', "webAppName=$app", "githubSubject=$subject")
az deployment group what-if @identityArgs
```

The preview should add only a deployment identity, its branch-scoped federated
credential, and a **Website Contributor** assignment on the existing site.
The deployment identity is separate from the app's runtime identity. No publish
profile, client secret, or demo access code goes into GitHub.

```powershell
$clientId = az deployment group create @identityArgs --name tokecon-github-actions `
    --query properties.outputs.clientId.value --output tsv
gh variable set AZURE_CLIENT_ID --repo $repo --body $clientId
gh variable set AZURE_TENANT_ID --repo $repo --body (az account show --subscription $sub --query tenantId -o tsv)
gh variable set AZURE_SUBSCRIPTION_ID --repo $repo --body $sub
gh variable set AZURE_RESOURCE_GROUP --repo $repo --body $group
gh variable set AZURE_WEBAPP_NAME --repo $repo --body $app
```

Before enabling publishing, confirm `/api/status` reports the existing models
and agents as ready. Verify the router's deployed pool from Azure:

```powershell
$router = "/subscriptions/$sub/resourceGroups/$group/providers/Microsoft.CognitiveServices/accounts/ai-tokecon-q7yxpkuusfxba/deployments/model-router"
az rest --method get --url "https://management.azure.com${router}?api-version=2025-10-01-preview" `
    --query '{state:properties.provisioningState,models:properties.routing.models[].name}'
```

Proceed when authorization has propagated and the router reports `Succeeded`
with GPT-4.1-mini and at least one of GPT-4.1 or GPT-6.1 Sol. Then enable
publishing and trigger a fresh run:

```powershell
gh variable set AZURE_DEPLOY_ENABLED --repo $repo --body 'true'
gh workflow run ci-deploy.yml --repo $repo --ref main
```

Actions deploys the ZIP using the site's existing Oryx build and verifies the
served commit, workload version, Foundry readiness, and pricing. It does not
reprovision Azure or run paid demo comparisons. Run the live acceptance checks
below separately. Set `AZURE_DEPLOY_ENABLED` to `false` to pause releases
without disabling CI.

## Verify real usage and dry-run the demo

1. Check Global Standard USD meters for each configured model against the
   [Azure Retail Prices API](https://prices.azure.com/api/retail/prices) and
   [Azure OpenAI pricing](https://azure.microsoft.com/pricing/details/cognitive-services/openai-service/).
   Include every model the router can select and its input-token routing fee.
   Record meter sources and the verification date in `config/pricing.json`.
   The short-context rates are used for the supplied workload. The demo
   defaults to the replacement cache-write formula in §8; verify the
   [cache-write billing guidance](https://learn.microsoft.com/azure/foundry/openai/how-to/prompt-caching)
   before pricing another deployment or setting `cache_write_additive`.
2. Run the default smoke check against the App Service URL. It uses the
   configured default workload (five requests or three invoices with the
   supplied defaults), not an undocumented one-request API bypass. Enter the
   access code at the hidden prompt or supply it in a process environment.
3. Use a ten-request **Naive similarity** run to measure similarity at `rc-03`
   and `rc-06`. The wrong matches scored 0.8933 and 0.9166, while intended
   paraphrases scored as low as 0.6708. The calibrated
   `NAIVE_SIMILARITY_THRESHOLD=0.66` keeps both wrong hits and the paraphrase
   hits. Five requests won't show `rc-06`. Confirm **Fact-guarded** preserves
   the plan and refund destination; `rc-09` is an additional English card-refund
   paraphrase, and the two-topic `rc-10` should bypass.
4. Confirm the candidate stops after one initial attempt and three retries on
   the runaway invoices in **Three-retry guardrail**. Press **Stop baseline
   retries** in a separate run and check that the other lane still finishes.
   Set `TIGHT_KILL_SWITCH_USD` using the
   measured spend so that tight mode stops early and hands off. First attempts
   on the runaway invoices cost $0.0002912–$0.0003072 in calibration, so this
   deployment uses `TIGHT_KILL_SWITCH_USD=0.00025`. It checks the budget after
   a failed attempt, not partway through a model call. This tight limit can
   also stop a recoverable invoice before a second attempt.
5. Run all nine modes five times at both default and maximum sizes. Keep both
   JSON reports and verify the release ID. Confirm ten-request timings against
   the 90-second target, at least two actual model families in the guided mode,
   both Decision tiers, zero input-cache reads in guided routing, no baseline writes,
   zero input-cache reads or writes in response caching,
   lower response-cache spend, and the fault-labelled FX retry chain.
   Naive reuse, no retries and tight budgets should lose expected outcomes;
   never treat those intentional misses as a broken demo. Report Small-only
   quality as measured, not as a guaranteed drop.

```powershell
$url = 'https://<app-host>'
python -m scripts.smoke_test --base-url $url --rounds 5 --assert-stories --fail-fast --output .data\live-default-verification.json
python -m scripts.smoke_test --base-url $url --rounds 5 --max-counts --assert-stories --fail-fast --output .data\live-verification.json
python -m scripts.export_replays --base-url $url
python -m scripts.browser_test --base-url $url --source live --channel msedge
```

The smoke check verifies real model usage, agent identity, event order, exact
call/row/KPI sums, invoice fields and accepted counts. It reports observed
grades and timing, not a guarantee of identical model answers. Full story
verification requires all nine modes, including the configured tight budget.
The browser check uses a process-local `DEMO_ACCESS_CODE`. It checks all
briefs and both self-hosted fonts at 1920×1080, 1366×768 and 390×844, then
exercises Replay, the access dialog, live streaming and agent receipts.
Capture a Live Scoreboard screenshot before playing recordings: Replay
updates the board with a Replay badge. Replay uses original event timing and
makes no inference calls. Use `--verify-replays` to check HTTPS Replay at 2× speed.
Replays and Scoreboard rows are release-specific. Export fresh Live recordings
after a workload change; older recordings are retained only as historical evidence.

In Application Insights **Logs**, verify model calls and completed runs:

```kusto
dependencies
| where timestamp > ago(1d) and isnotempty(customDimensions.lever)
| extend lever = tostring(customDimensions.lever), lane = tostring(customDimensions.lane),
         agent = tostring(customDimensions.foundry_agent), model = tostring(customDimensions.response_model),
         cost = todouble(customDimensions.cost_usd)
| summarize calls = count(), cost_usd = sum(cost) by lever, lane, agent, model
| order by lever asc, lane asc
```

```kusto
customEvents
| where timestamp > ago(1d) and name == "run_completed"
| project timestamp, customDimensions
```

## Present and recover

- **Day before:** finish the dry run, export and review all nine genuine Live
  recordings, then commit them under `replays/`. Save a screenshot of the Live
  Scoreboard for the backup slide. Fake results are never a substitute for Live.
- **Before going on:** open the HTTPS app, check the green Foundry pill and clear
  the response cache. Run `python -m scripts.warmup --base-url $url` to warm the
  prefix. A warmed first request can show a read instead of a write. Confirm
  Replay opens and use five support requests (six for both cache traps) and
  three invoices.
- **On stage:** show token buckets in Prompt caching; routed models and the
  Decision escalation in Model routing; exact hits and wrong hits in Response
  caching; the safety ceiling versus handoff in Retry. Close with the Scoreboard
  and power-user month, labelled a projection from one run. Report the numbers
  actually on-screen.
- **If Foundry goes offline:** use **Replay last run**. If the venue network
  goes down, run the app locally with `$env:REPLAY_ONLY = 'true'` and no Azure
  credentials, then play the exported recordings from `replays/`. Check this
  on the presenting laptop offline before the event.

## Historical hosted verification — 23 November 2026 (v5)

The `english-foundry-v5` deployment passed **45 of 45** end-to-end comparisons
across five full rounds on the same release. Each support run compared 10
requests; each invoice run compared six documents. In total, the 390 paired
workloads used 1,130 paid calls, measured at **$1.098674620** at configured
list rates, excluding calibration, hosting, monitoring and the separate
browser run. All 30 ten-request runs finished within 52.20 seconds (target:
90 seconds). Application Insights confirmed all 45 run IDs and recorded
distinct agent names, versions and lanes.

| Live mode | Accepted, without → with | Behavior checks | Duration | Cost per accepted outcome, lower by |
|---|---:|---:|---:|---:|
| Prompt caching | 10/10 → 10/10 | 5/5 | 42.20–52.20 s | 74.0–76.9% |
| Foundry model router | 10/10 → 10/10 | 5/5 | 37.84–46.23 s | 52.2–57.1% |
| Decision router | 10/10 → 10/10 | 5/5 | 38.56–42.84 s | 23.4–33.0% |
| Small model for all | 10/10 → 9/10 once, 10/10 four times | 5/5 | 34.81–42.33 s | 66.6–74.6% |
| Fact-guarded cache | 10/10 → 10/10 | 5/5 | 33.94–41.00 s | 23.8–31.8% |
| Naive similarity | 10/10 → 8/10 | 5/5 | 33.45–39.11 s | 16.4–36.6% |
| New evidence + guardrails | 4/6 → 4/6 | 5/5 | 58.74–92.73 s | 43.4–45.4% |
| No retries | 4/6 → 2/6 | 5/5 | 53.38–66.06 s | 38.5–40.2% |
| Tight kill switch | 4/6 → 2/6 | 5/5 | 53.19–71.09 s | 36.6–39.2% |

The Foundry router selected GPT-4.1-mini in these batches; the Decision
router used both tiers. Fact-guarded caching skipped four answer generations
per batch; naive similarity replayed two wrong answers. Evidence guardrails
reduced paid calls from 22 to 14 per batch and stopped both simulated FX
failures on repeated evidence. No retries and Tight intentionally handed off
the two recoverable invoices as well. Small-only missed `mr-05` once and was
cheaper in every batch; do not present a quality penalty as guaranteed.

The prior release's fifth Tight run omitted a printed subtotal on a clean
invoice. The correction recovers an omitted subtotal only when a single
printed amount agrees with the lines and total. The exact failing output
passed deterministic validation with the fix; current hosted runs accepted
both clean invoices on their first attempt. No budget or acceptance check
was relaxed. These are observed pass rates, not a guarantee of future model
responses.

The [round-five recordings](replays/) reproduced every streamed result and the
Scoreboard with both model clients, HTTP transports and DNS disabled. In Edge,
the hosted view passed at 1920×1080, 1366×768 and 390×844, including Replay
without a code and a separate authenticated Run showing both Foundry agents
in Inspect. Ruff, mypy, JavaScript syntax checks and 34 pytest tests passed.
The per-run evidence is in `.data\presenter-v5-hosted-five-rounds.json`.

## Previous Azure dry run — 20 November 2026 (superseded)

Two maximum-size Live runs per mode completed with priced model usage, ordered
SSE, matching call/row/KPI totals, and no validation errors in the final suite.
The three retry modes were repeated after giving the Decision reviewer the
validator's arithmetic. The 18 verified runs used 462 model calls and cost
**$0.41648 at the configured list rates**, excluding calibration, hosting,
monitoring, and the separate browser run.

| Live mode | Time, run 1 / run 2 | Accepted, control → candidate | Observed result |
|---|---:|---|---|
| Prompt caching | 38.62s / 40.61s | 10/10 → 10/10, both | 75.4–75.5% lower cost with a warm prefix. |
| Foundry model router | 33.03s / 28.06s | 9/10 → 10/10; 9/10 → 9/10 | Selected model and per-call routing fee visible. |
| Decision router | 34.36s / 33.34s | 9/10 → 9/10, both | Both tiers used; `mr-03` and `mr-05` escalated. |
| Small model for all | 32.34s / 28.56s | 9/10 → 9/10; 9/10 → 10/10 | Cost per accepted outcome fell. Aggregate quality loss did not reproduce. |
| Fact-guarded cache | 23.55s / 25.61s | 10/10 → 10/10, both | Three model calls skipped; `rc-10` bypassed. |
| Naive similarity | 25.47s / 24.64s | 10/10 → 8/10, both | Wrong hits on `rc-03` and `rc-06` despite lower cost. |
| New evidence + guardrails | 32.39s / 31.75s | 4/6 → 4/6, both | 22 → 16 paid calls; runaway invoices handed off on repeated evidence. |
| No retries | 32.01s / 33.06s | 4/6 → 2/6, both | Both recoverable invoices were handed off on the first failure. |
| Tight kill switch | 31.36s / 38.20s | 4/6 → 2/6, both | Spend guard stopped failed documents after one attempt. |

All ten-request runs took less than 41 seconds (target: 90 seconds). For model
routing, `mr-05` expects PostgreSQL advice even though the required system
prompt says to answer only from the policy corpus. The frontier model's
abstention remained a miss under the specified grader. Small-only missed
`mr-09` in one run but did not lower aggregate acceptance. Don't present
small-only as a guaranteed quality penalty.

During calibration, a corrected `inv-05` was rejected by the Decision reviewer.
Passing the validator's arithmetic to the reviewer let the supported correction
pass at the unchanged 0.7 threshold. When a runaway invoice alternates between
different extractions, the retry limit can stop it instead of the repeated-evidence
check; both exits hand off to a person.

| Check | Evidence |
|---|---|
| Deployment and controls | HTTPS `/healthz` returned `ok`; all five deployments responded with managed identity; no missing prices. Unauthenticated Start returned 401, a second active Start returned 409, and the $2 rolling-hour inference cap passed local persistence tests. Key Vault access stayed private. |
| Cost and telemetry | Pricing and usage tests passed, including every §8.3 example. The App Insights queries returned real dependencies and `run_completed` events for all nine modes. |
| Presenter UI | Edge passed the hosted layout and all How-to panels at 1366×768 and 1920×1080. Hosted playback preserved the real summary and Inspect showed raw usage. A separate browser run verified the access dialog, live KPIs, and the nine Live Scoreboard rows. |
| Offline handoff | The nine genuine recordings in [`replays/`](replays/) replayed the same rows and KPIs with both model clients and DNS disabled. A Live Scoreboard backup was captured before playback. |
| Local regression checks | Ruff, mypy, and JavaScript syntax checks passed; 24 pytest tests passed. All nine Fake modes passed twice at maximum size and in Edge at both projector sizes. The stable prefix measured 1,849 tokens. |

In the repeated **Fake** runs, the fact-guarded cache accepted 10/10 requests
versus 7/10 for naive similarity. Evidence-based retries accepted 4/6 invoices
versus 2/6 with no retries. Small-only routing spent less but accepted 6/10
versus 10/10 for the control. These are fixture results, not Azure measurements.
