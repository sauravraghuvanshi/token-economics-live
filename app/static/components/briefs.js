export const laneLabels = { control: "Without the lever", candidate: "With the lever" };

export const pages = {
  prompt_caching: {
    intro: "Same questions, model and policies. Can the model reuse work on the input?",
    baseline: "Put each new question before the shared policies. The leading text changes, so reuse is less likely.",
    receipt: "Compare tokens written to cache with tokens read from cache. Inspect any request to see the actual usage.",
    acceptance: "A support answer passes if it meets the required facts, policy citations and decision checks.",
  },
  model_routing: {
    intro: "The same support questions and policies in both lanes. Does the cost of choosing a model pay off?",
    baseline: "Send every question to the larger, more capable model, even when a smaller model could handle it.",
    receipt: "Check which model answered, what the route cost and whether the answer passed.",
    acceptance: "A support answer passes if it meets the required facts, policy citations and decision checks.",
  },
  response_cache: {
    intro: "The same support questions in both lanes. Can a saved answer replace a fresh one without changing the facts?",
    baseline: "Generate every answer from scratch. Both lanes disable model input caching on the supported deployment.",
    receipt: "A hit skips generation, not grading. The ledger reports input-cache reads and writes separately.",
    acceptance: "A saved or fresh answer faces the same checks for the current question.",
  },
  retry_killswitch: {
    intro: "The same invoices, checks and simulated currency tool in both lanes. Which failures are worth another try?",
    baseline: "Retry failures even when the error repeats, until the safety ceiling or the presenter stops new retries.",
    receipt: "Retries reconcile totals only when both readings agree on the printed inputs. A handoff is not a pass.",
    acceptance: "An invoice is accepted only if it passes the app’s validation and the reviewer approves it.",
  },
  scoreboard: {
    intro: "Compare the latest runs mode by mode. Then estimate what the cost of accepted outcomes means for one power user’s seat.",
  },
};

const retrySetup = [
  "Run the same extraction, currency check and validation in both lanes. The app simulates the currency tool; it always fails for USD and EUR in this demo.",
  "On retry, recalculate derived amounts only when a second extraction confirms the printed inputs. Then validate and review the result.",
  "Record repeated fields and error codes. Show whether each retry corrected a failure or repeated work.",
];

export const experiments = {
  default: {
    change: "Put the same policies first so the model can reuse them. It still answers every question.",
    watch: "Look for cache writes and reads. If the prefix is already warm, even the first request may show a read.",
    steps: [
      "Keep the model, support rules and answer checks fixed.",
      "Put shared instructions and policies before the question. Keep that text byte-for-byte identical; use cache settings the model supports.",
      "Check the actual usage for cache reads and writes, not just a cache setting. Compare cost per accepted answer.",
    ],
  },
  foundry_router: {
    change: "Classify the question, then use Foundry Model Router to send routine cases to GPT-4.1-mini and hard cases to the same frontier model as the baseline.",
    watch: "Both lanes use the same fresh input, so a warm prefix cannot skew the comparison. Inspect the selected model and both fees.",
    steps: [
      "Keep the full policies and grading rules. Put the unique ticket and question first to prevent provider cache reuse, and disable caching where supported.",
      "Use the classifier on question text alone. Confident routine cases get the low-cost pool; hard or uncertain cases keep the baseline model in the quality pool.",
      "Check the mode and model in each response and include both fees. If input-cache activity appears, show the cost but withhold the routing savings verdict.",
    ],
  },
  decision_router: {
    change: "The app classifies the question before attaching policies. Only a confident small-model choice takes the cheaper route.",
    watch: "Check the selected tier, confidence and decision cost. Low confidence or a hard question goes to the frontier model.",
    steps: [
      "Send only the question to the Decision service. Ask if it needs the small or frontier tier.",
      "Use the small model only when it is recommended at or above the confidence threshold. Otherwise use the frontier model.",
      "Attach the full policy text for the answer. Count both the decision call and the answer call when you compare cost.",
    ],
  },
  small_all: {
    counterexample: true,
    change: "Send every question to the small model, including questions that combine policies. No classifier or router is used.",
    watch: "This tests whether the cheapest route is enough. Compare passes as well as price; a quality drop is not guaranteed.",
    steps: [
      "Keep the workload, full policy prompt and grading rules the same.",
      "Use only the small model in the changed lane, with no route to a more capable model.",
      "Check every answer, especially multi-policy cases. Do not assume a quality drop or choose by total cost alone.",
    ],
  },
  fact_guarded: {
    change: "Reuse only when the topic and facts match. Call the answer model only on a miss or bypass, never on a hit.",
    watch: "A reworded card-refund question can reuse an answer; account credit needs a different one. Ambiguous questions bypass the cache.",
    steps: [
      "On supported GPT Standard deployments, set explicit prompt caching with no breakpoints in both lanes to isolate saved-answer reuse.",
      "Allow caching only for known questions with one clear topic and all key facts resolved.",
      "Put the topic, facts, language, policy version and answer deployment into the key. A card refund and account credit need different keys.",
      "Return saved answers without calling the answer model on a hit. Generate on a miss or bypass; store only when there is a usable key.",
    ],
  },
  naive: {
    counterexample: true,
    change: "Reuse by similarity without checking the facts. Only a miss calls the answer model, but every lookup has a cost.",
    watch: "Card and account-credit refunds may sound alike but have different timelines. Check any reused answer for a miss.",
    steps: [
      "Use the same prompt-cache settings as the baseline. Input caching is separate from reusing a complete answer.",
      "Turn each question into a numeric representation called an embedding. Compare it to saved questions.",
      "Reuse the closest answer above the similarity threshold. Only a miss calls the answer model, but every lookup has an embedding cost.",
      "Grade the saved answer against the current question, including facts such as refund destination or plan.",
    ],
  },
  guardrails: {
    change: "Allow one initial attempt and at most three retries. If the invoice still fails, hand it off with the errors.",
    watch: "A retry can fix an extraction error but not the simulated currency outage. Compare four attempts with the baseline ceiling, or stop the baseline yourself.",
    steps: [
      ...retrySetup,
      "Stop after three retries or sooner at the document token budget. An authorized presenter can stop new baseline retries without stopping this lane.",
    ],
  },
  no_retries: {
    counterexample: true,
    change: "Hand off after the first failed attempt. Clean invoices still have a chance to pass; nothing gets a second try.",
    watch: "Look at the recoverable invoices. Fewer calls can mean more handoffs and a higher cost per accepted invoice.",
    steps: [
      "Use the same extraction and checks in both lanes. The simulated currency tool still fails for USD and EUR.",
      "Set the retry allowance to zero. Send any failed first attempt to a person along with the errors.",
      "Count invoices saved by a retry in the other lane before judging what the reduced spend bought you.",
    ],
  },
  tight: {
    counterexample: true,
    change: "Add a small spend cap per invoice on top of the evidence and token limits. The app checks it after each paid attempt.",
    watch: "Look for where the cap triggers. It can stop a runaway loop, but it can also stop a recoverable invoice too soon.",
    steps: [
      ...retrySetup,
      "Stop at repeated evidence or the attempt or token limit, even if the document still fails.",
      "Check the spend cap after each failed attempt. One paid attempt can cross it; a cap is not a refund. Compare handoffs with successful retries.",
    ],
  },
};

export const supportAgentNote = "The app manages caching and decides when to call the answer service. A response-cache hit skips the answer agent; a miss or a bypass calls it.";
export const orchestrationNote = "A Foundry agent generates a response where configured. The app handles cache lookups, Decision calls, the simulated currency tool, validation and retry rules outside the agent.";

export const responsesExample = `# Get the active agent version and a Microsoft Entra token for the project.
reference = {
    "type": "agent_reference",
    "name": agent_name,
    "version": active_version,
}
response = await http.post(
    f"{project_endpoint}/openai/v1/responses",
    headers={"Authorization": f"Bearer {project_token}"},
    json={"agent_reference": reference, "input": input_messages,
          "store": False, **cache_options},
)
response.raise_for_status()
receipt = response.json()
# Verify the returned agent_reference before attributing the response.
response_id = receipt["id"]
model = receipt["model"]
usage = receipt["usage"]
input_details = usage.get("input_tokens_details") or {}
cached = input_details.get("cached_tokens", 0)
written = input_details.get("cache_write_tokens", 0)`;
