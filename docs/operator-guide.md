# Token Economics Live operator guide

[Back to the README](../README.md) · [Current live results](../README.md#hosted-verification--26-november-2026-v64)

## Azure configuration and deployment

The [hosted demo](https://app-tokecon-q7yxpkuusfxba.azurewebsites.net/) runs in
**West US 3**. For a new environment, confirm App Service quota and model
availability in your approved region before provisioning.

Requires Azure CLI, Azure Developer CLI (`azd`), a subscription, and permission
to provision the resources and assign roles at their scopes. Sign in as the
operator using `az login` and `azd auth login`. For local Live runs you also need
Foundry User on the account and, if required for Decision, Cognitive Services
User. No model API key is used.

### 1. Configure Foundry and the target subscription

Use the approved subscription, location and Foundry account. Locate the exact
model versions and deployments listed in the [specification](../Instruction.md)
before provisioning. Set `DEPLOY_MODELS=true` **only** after checking that the
five deployment names are unused and that you have quota. Leave it `false` to
reuse five existing deployments; set their names via the five variables in
[`.env.example`](../.env.example). The default deploys no new Foundry account.
`CREATE_FOUNDRY=true` opts into a separate account and project plus five deployments.

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
requires it. Set the router to **Balanced** with GPT-4.1-mini, GPT-4.1, and
GPT-6.1 Sol, covered by the [pricing file](../config/pricing.json). Guided
routing can narrow that pool, not add models; V6.4 needs GPT-4.1-mini and
GPT-6.1 Sol in the pool. If a separate operator manages Foundry, have them
apply those settings and allow time for them to take effect.

Calibrate `NAIVE_SIMILARITY_THRESHOLD` and `TIGHT_KILL_SWITCH_USD` for the
deployment before running the full acceptance matrix. See the [live dry-run
checklist](#verify-real-usage-and-dry-run-the-demo) for the values used here.

### 2. Provision hosting

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

### 3. Publish the agents and app

For a new environment, publish the agents before deploying the web package.
Use the exact project endpoint and directory returned by Azure (the provision
step also exports `FOUNDRY_PROJECT_ENDPOINT`):

```powershell
python -m scripts.sync_agents --project-endpoint '<confirmed project endpoint>' --tenant-id '<directory ID>' --apply
azd deploy web --no-prompt
```

Find the [named Prompt Agents](../README.md#foundry-agents) in your project.
The server pins each active version and checks the `agent_reference` returned
by Foundry. The app can recover an omitted subtotal from a single printed
field only when the line amounts and total
independently agree. On retry it recalculates derived amounts only if a second
extraction confirms the printed inputs. The validator and Decision reviewer
still have to pass; Inspect shows raw extraction and arithmetic evidence
separately. Use `scripts.sync_agents` without `--apply` to verify the live
definitions without editing them. For local Live runs in a nondefault Azure
directory, set `AZURE_TENANT_ID` to use an explicit Azure CLI credential.

## Microsoft sign-in for the hosted demo

The Azure App Service gate uses the real Microsoft Entra account picker.
It allows identities in the subscription's directory, including invited
guests, and keeps the separate access code for paid runs. Local Fake and
offline Replay don't use Entra sign-in.

1. Deploy the application release that serves `/readyz` before changing the
   sign-in settings. Wait for the GitHub Actions release to pass.
2. From PowerShell 7.4+ in this checkout, sign in with Azure CLI on an
   approved workstation and select the target subscription. Use an account
   permitted to create an Entra app registration and its federated credential,
   create and attach a user-assigned managed identity, and update this site.
3. During a break between demos, run the setup and review the Azure What-If
   before approving the final change:

   ```powershell
   .\scripts\enable_demo_sign_in.ps1 -SubscriptionId '<approved subscription ID>' `
       -ResourceGroup '<existing App Service resource group>' -WebAppName '<existing App Service name>'
   ```

The script registers a dedicated single-tenant app with one HTTPS callback,
attaches a separate identity for its passwordless client assertion, and
preserves the system-assigned identity for Foundry and Key Vault. No Graph
permissions are granted to the sign-in app, and the GitHub publishing identity
does not change. The script prepares the identity and app settings before the
final approval; changing the settings restarts the app. The What-If covers
only the existing site's authentication settings. Do not reprovision the
site to enable sign-in.

Open the site in a new browser session and confirm Microsoft prompts for an
account. After sign-in, confirm the presenter loads, Replay works without the
demo code, and Run still asks for the code. Without a session, `/` must redirect
a browser, while `/api/config` and `/api/scoreboard` must not return data. Only
`/healthz`, `/readyz`, and `/static/build.json` bypass the gate.
Use `/.auth/logout` to end the App Service session.

If the callback fails, inspect the Entra sign-in logs and verify the app
registration, exact callback, managed identity, and federated credential
match. To roll back a failed initial cutover, an authorized site operator can
choose **Allow unauthenticated access** in App Service **Authentication >
Edit authentication settings**. The paid-run access code still applies.

## Hosted access and readiness

The managed identity reaches the vault through VNet integration and a private
endpoint. Public vault access remains disabled. To verify hosted sign-in
without running paid inference, leave `DEMO_ACCESS_CODE` unset and run:

```powershell
.\.venv\Scripts\python.exe -m scripts.browser_test --base-url 'https://app-tokecon-q7yxpkuusfxba.azurewebsites.net' --source live --channel msedge --sign-in
```

The check prompts for sign-in, holds the App Service session in memory, plays
a recording, and checks that **Run** asks for the code. For a paid browser
check, use the
`demo-access-code` secret in the deployment's vault from an authorized
account on the private network. On the original Windows account, the local
encrypted copy is `.azure\tokecon-demo\demo-access-code.dpapi`. Decrypt it only
into the PowerShell 7 process environment:

```powershell
$url = 'https://app-tokecon-q7yxpkuusfxba.azurewebsites.net'
$code = ConvertTo-SecureString (Get-Content -Raw '.azure\tokecon-demo\demo-access-code.dpapi').Trim()
try {
    $env:DEMO_ACCESS_CODE = ConvertFrom-SecureString $code -AsPlainText
    .\.venv\Scripts\python.exe -m scripts.browser_test --base-url $url --source live --channel msedge --sign-in
} finally {
    Remove-Item Env:\DEMO_ACCESS_CODE -ErrorAction SilentlyContinue
    $code.Dispose()
}
```

For a manual run, enter the code in the app's access dialog after selecting
**Run**. Use only a private terminal or the vault to retrieve the value.
Don't put it in chat or on a slide. Replay doesn't require the code.

The app must return `ok` at `/healthz`. `/readyz` returns the Live workload
version after all five models, six agents, and pricing are ready. Neither
endpoint triggers inference. The signed-in `/api/status` provides details.
Startup probes use managed identity and can take time after a fresh
deployment or a role change. A code-authenticated refresh is limited to
once per minute.

## GitHub Actions releases

[The workflow](../.github/workflows/ci-deploy.yml) runs offline on pushes and pull
requests. It packages the tested revision without local state or stale recordings,
then exercises that ZIP with five Fake rounds and replays at each workload size.
Chromium verifies the nine modes at two projector sizes and on mobile. Main's
`tokecon-fake-smoke` artifact contains the synthetic results, not Azure measurements.
Only the current `main` revision in the [private repository](https://github.com/sauravraghuvanshi/token-economics-live)
can deploy. The publish job is disabled until `AZURE_DEPLOY_ENABLED` is `true`. A fork
does not inherit permission to deploy.

For the one-time setup, use PowerShell 7.4+ in this checkout, signed into the
site's subscription with permission to deploy a managed identity and assign an
Azure role on the App Service. GitHub CLI must be signed into the repository
owner's account. Preview the changes first:

```powershell
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true
$repo = 'sauravraghuvanshi/token-economics-live'
$sub = '<approved Azure subscription ID>'
$group = '<existing App Service resource group>'
$app = '<existing App Service name>'
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

Before enabling publishing, confirm `/readyz` reports the Live workload
version. Verify the router's deployed pool from Azure:

```powershell
$foundryAccountId = '<confirmed ARM resource ID of the Foundry account>'
$router = "$foundryAccountId/deployments/model-router"
az rest --method get --url "https://management.azure.com${router}?api-version=2025-10-01-preview" `
    --query '{state:properties.provisioningState,models:properties.routing.models[].name}'
```

Proceed when authorization has propagated and the router reports `Succeeded`
with GPT-4.1-mini and GPT-6.1 Sol in its pool. Then enable
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
   Record meter sources and the verification date in [`config/pricing.json`](../config/pricing.json).
   The short-context rates are used for the supplied workload. The demo defaults
   to the replacement cache-write formula in [section 8 of the specification](../Instruction.md);
   verify the [cache-write billing guidance](https://learn.microsoft.com/azure/foundry/openai/how-to/prompt-caching)
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

The command-line clients below don't sign in to App Service. Once hosted
sign-in is enabled, point them at a local Live instance configured for the
approved Foundry project. Use the signed-in [browser check](#hosted-access-and-readiness)
for the hosted presenter itself.

```powershell
$url = 'http://127.0.0.1:8000'
python -m scripts.smoke_test --base-url $url --rounds 5 --assert-stories --fail-fast --output .data\live-default-verification.json
python -m scripts.smoke_test --base-url $url --rounds 5 --max-counts --assert-stories --fail-fast --output .data\live-verification.json
python -m scripts.export_replays --base-url $url
```

The smoke check verifies real model usage, agent identity, event order, exact
call/row/KPI sums, invoice fields and accepted counts. It reports observed
grades and timing, not a guarantee of identical model answers. Full story
verification requires all nine modes, including the configured tight budget.
The browser check uses a process-local `DEMO_ACCESS_CODE`. It checks all
briefs and both self-hosted fonts at 1920×1080, 1366×768 and 390×844, then
exercises Replay and the access dialog. If given the demo code, it also
checks Live streaming and agent receipts.
Capture a Live Scoreboard screenshot before playing recordings: Replay
updates the board with a Replay badge. Replay uses original event timing and
makes no inference calls. Use `--verify-replays` to check Replay at 2× speed.
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
  recordings, then commit them under [`replays/`](../replays/). Save a screenshot of the Live
  Scoreboard for the backup slide. Fake results are never a substitute for Live.
- **Before going on:** sign in to the HTTPS app, check the green Foundry pill,
  and clear the response cache. To warm the prefix, run Prompt caching from
  the presenter. A warmed first request can show a read instead of a write.
  Confirm Replay opens and use five support requests (six for both cache
  traps) and three invoices.
- **On stage:** show token buckets in Prompt caching; routed models and the
  Decision escalation in Model routing; exact hits and wrong hits in Response
  caching; the safety ceiling versus handoff in Retry. Close with the Scoreboard
  and power-user month, labelled a projection from one run. Report the numbers
  actually on-screen.
- **If Foundry goes offline:** use **Replay last run**. If the venue network
  goes down, [play the included Live recordings offline](../README.md#replay-a-live-recording-offline).
  Check this on the presenting laptop before the event.

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

The V5 round-five recordings reproduced every streamed result and the
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
| Offline handoff | Nine recordings from this dry run replayed the same rows and KPIs with both model clients and DNS disabled. A Live Scoreboard backup was captured before playback. |
| Local regression checks | Ruff, mypy, and JavaScript syntax checks passed; 24 pytest tests passed. All nine Fake modes passed twice at maximum size and in Edge at both projector sizes. The stable prefix measured 1,849 tokens. |

In the repeated **Fake** runs, the fact-guarded cache accepted 10/10 requests
versus 7/10 for naive similarity. Evidence-based retries accepted 4/6 invoices
versus 2/6 with no retries. Small-only routing spent less but accepted 6/10
versus 10/10 for the control. These are fixture results, not Azure measurements.
