# Token Economics · Live

Compare four runtime AI cost levers on the same fictional CloudDesk workload. The
dashboard streams both lanes, prices measured usage token by token, and grades
each outcome without a model judge. Keep a change only if cost per accepted
outcome falls and quality holds. See [Instruction.md](Instruction.md) for the
workloads, exact formulas, and acceptance criteria.

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
.\.venv\Scripts\python.exe -m scripts.smoke_test --base-url http://127.0.0.1:8000 --source fake --rounds 2 --max-counts --assert-stories --verify-replays
```

For browser verification, install `playwright==1.58.0` in the same environment
and run `python -m scripts.browser_test --channel msedge`. Alternatively, run
`python -m playwright install chromium` and omit `--channel`. The checks cover
both projector sizes with external browser requests blocked.

## Azure configuration and deployment

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
azd env set AZURE_OPENAI_BASE_URL 'https://<resource>.openai.azure.com/openai/v1/'
azd env set FOUNDRY_USER_ROLE_ID (az role definition list --subscription $sub --name 'Foundry User' --query '[0].name' -o tsv)
azd env set VAULT_SECRETS_USER_ROLE_ID (az role definition list --subscription $sub --name 'Key Vault Secrets User' --query '[0].name' -o tsv)
azd env set MONITORING_PUBLISHER_ROLE_ID (az role definition list --subscription $sub --name 'Monitoring Metrics Publisher' --query '[0].name' -o tsv)
```

Look up `Azure AI User` if the renamed `Foundry User` is not present. Set
`COGNITIVE_SERVICES_USER_ROLE_ID` by the same lookup if the Decision endpoint
requires it. Set the router to **Balanced** with a subset covered by the pricing
file. If a separate operator is responsible for Foundry, have them apply those
settings and allow time for them to take effect.

In PowerShell 7, enter the operator-approved random demo code (16+ characters)
at a masked prompt. The secure Bicep parameter seeds the Key Vault secret, and
the app setting contains only the Key Vault reference. Do not put the value in
Git, a shell command argument, or build logs.

```powershell
$env:DEMO_ACCESS_CODE = Read-Host -MaskInput 'Demo access code'
try {
    azd up --no-prompt
} finally {
    Remove-Item Env:\DEMO_ACCESS_CODE -ErrorAction SilentlyContinue
}
```

Provisioning uses one Linux P0v3 instance with one Gunicorn/Uvicorn worker.
Do not scale workers or instances: the run registry, active-run guard and cache
are process-local. `/home/data` persists the rolling model spend ledger, run
recordings and scoreboard. The hourly ledger limits inference, not the hosting
bill. The App Service plan continues to incur charges even after the demo.

The app must return `ok` at `/healthz`; `/api/status` must list all five models
as `ok` and report verified prices without missing meters. The startup probes
use managed identity and can take time after a fresh deployment or a role
change. Unauthenticated status reads do not trigger inference. A code-authenticated
status refresh is limited to once per minute.

After provisioning, connect the designated GitHub repository with
`azd pipeline config`. The workflow runs offline tests and uses federated
authentication for deploys from `main`. CI never runs the live smoke test.

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
   and `rc-06`. Tune `NAIVE_SIMILARITY_THRESHOLD` to reproduce both wrong hits
   without hiding the intended correct examples. Five requests won't show
   `rc-06`. Confirm **Fact-guarded** preserves the plan, refund destination
   and language; `rc-10` should bypass.
4. Measure the first and later candidate attempts for the runaway invoices
   in **New evidence + guardrails**. Set `TIGHT_KILL_SWITCH_USD` using the
   measured spend so that tight mode stops early and hands off. It stops
   between attempts, not partway through a model call. Repeat to check that
   recoverable invoices still illustrate the trade-off.
5. Run all nine modes twice at maximum size and keep the JSON evidence. Confirm
   ten-request timings against the 90-second target; show real misses in
   Inspect and report them rather than adjusting the grader. In particular,
   `mr-05` expects PostgreSQL advice even though the exact role prompt limits
   answers to the supplied policies. Check the router's selected models and
   fees, both Decision tiers, and the fault-labelled FX retry chain.

```powershell
$url = 'https://<app-host>'
python -m scripts.smoke_test --base-url $url
python -m scripts.smoke_test --base-url $url --rounds 2 --max-counts --assert-stories --verify-replays --output .data\live-dry-run.json
python -m scripts.export_replays --base-url $url
```

The smoke check verifies real model usage, event order, exact call/row/KPI sums
and accepted counts. It reports observed grades and timing, not a guarantee of
identical model answers. If the tight budget is unset, it prints that tight mode
was skipped. Do not sign off the dry run without all nine modes. Replay uses
original event timing (halved in the smoke check) and makes no inference calls.

In Application Insights **Logs**, verify model calls and completed runs:

```kusto
dependencies
| where timestamp > ago(1d) and isnotempty(customDimensions.lever)
| extend lever = tostring(customDimensions.lever), lane = tostring(customDimensions.lane),
         model = tostring(customDimensions.response_model), cost = todouble(customDimensions.cost_usd)
| summarize calls = count(), cost_usd = sum(cost) by lever, lane, model
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

## Verification status

| Check | Result |
|---|---|
| Local build | Ruff and mypy clean; 23 pytest tests passed. The Bicep compiles and the azd package includes runtime files and Fake fixtures without local tooling, caches, or environment files. The stable prefix measured 1,849 tokens. |
| Repeated demo checks | All nine modes run twice at maximum size with synthetic data. Edge passed the presenter workflow at 1366×768 and 1920×1080. Replay matched the final KPIs and retained Fake provenance. |
| Azure and handoff | No Azure resources were created. Live latency, telemetry, calibrated thresholds and genuine recordings are not verified. Azure target approval and a designated GitHub remote are outstanding. |

In the repeated **Fake** runs, the fact-guarded cache accepted 10/10 requests
versus 7/10 for naive similarity. Evidence-based retries accepted 4/6 invoices
versus 2/6 with no retries. Small-only routing spent less but accepted 6/10
versus 10/10 for the control. These are fixture results, not Azure measurements.
