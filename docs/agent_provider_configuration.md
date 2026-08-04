# Agent provider configuration

Phase 3 adds provider-neutral API slots for Agent 1 to Agent 4. External AI is
optional. With no environment variables and no network access, all four agents
run in deterministic mode and the workflow behaves exactly as before.

## Package layout

```text
app/agents/
  contracts.py            AgentProvider protocol, invocation context/result, errors
  config.py               Per-agent environment configuration
  schemas.py              Strict Pydantic response schemas for Agent 1-4
  pipeline.py             Validation pipeline and deterministic fallback runtime
  audit.py                Invocation audit records (secret-free)
  health.py               Per-agent health report (secret-free)
  agents.py               Agent 1-4 interfaces and deterministic baselines
  providers/
    __init__.py           Provider factory (build_provider)
    deterministic.py      DeterministicProvider (default, offline)
    mock.py               MockProvider (tests and demos, no network)
    http_json.py          HttpJsonProvider (generic JSON endpoint)
    openai_compatible.py  OpenAICompatibleProvider (chat completions wire format)
    transport.py          Minimal urllib JSON transport, replaceable in tests
```

No provider SDK is imported anywhere, and the domain layer (`app/pricing_engine.py`,
`app/rule_engine.py`, `app/approval_workflow.py`, …) does not import `app.agents`.

## Environment variables

Each agent has its own independent block. Replace `AGENT1` with `AGENT2`,
`AGENT3` or `AGENT4` as required.

| Variable | Default | Meaning |
| --- | --- | --- |
| `AGENT1_PROVIDER` | `deterministic` | `deterministic`, `mock`, `http_json`, `openai_compatible` |
| `AGENT1_BASE_URL` | empty | Absolute `http(s)` endpoint for the HTTP providers |
| `AGENT1_API_KEY_ENV` | `AGENT1_API_KEY` | Name of the variable holding the key |
| `AGENT1_MODEL` | empty | Model identifier |
| `AGENT1_TIMEOUT_SECONDS` | `30` | Per-call timeout, must be greater than zero |
| `AGENT1_MAX_RETRIES` | `0` | Additional attempts after the first one |
| `AGENT1_ORGANISATION` | empty | Optional OpenAI-compatible organisation header |
| `AGENT1_PROJECT` | empty | Optional OpenAI-compatible project header |
| `AGENT1_PROMPT_TEMPLATE_VERSION` | `v1` | Recorded in the audit record |

Defaults for a fully deterministic installation:

```bash
AGENT1_PROVIDER=deterministic
AGENT1_BASE_URL=
AGENT1_API_KEY_ENV=AGENT1_API_KEY
AGENT1_MODEL=
AGENT1_TIMEOUT_SECONDS=30

AGENT2_PROVIDER=deterministic
AGENT2_BASE_URL=
AGENT2_API_KEY_ENV=AGENT2_API_KEY
AGENT2_MODEL=
AGENT2_TIMEOUT_SECONDS=30

AGENT3_PROVIDER=deterministic
AGENT3_BASE_URL=
AGENT3_API_KEY_ENV=AGENT3_API_KEY
AGENT3_MODEL=
AGENT3_TIMEOUT_SECONDS=30

AGENT4_PROVIDER=deterministic
AGENT4_BASE_URL=
AGENT4_API_KEY_ENV=AGENT4_API_KEY
AGENT4_MODEL=
AGENT4_TIMEOUT_SECONDS=30
```

Agents are configured independently, so mixed setups are supported, for example
Agent 1 on an OpenAI-compatible gateway, Agent 3 on an internal JSON service and
Agents 2 and 4 deterministic:

```bash
AGENT1_PROVIDER=openai_compatible
AGENT1_BASE_URL=https://gateway.internal/v1
AGENT1_API_KEY_ENV=INTERNAL_GATEWAY_KEY
AGENT1_MODEL=internal-model
AGENT1_TIMEOUT_SECONDS=20
AGENT1_MAX_RETRIES=1

AGENT3_PROVIDER=http_json
AGENT3_BASE_URL=https://wording.internal/agents
AGENT3_TIMEOUT_SECONDS=10
```

The API key itself is never stored in the configuration object. Only the
variable *name* is kept and the value is resolved at call time.

## Agent responsibilities

| Agent | Task name | Purpose | Never allowed to |
| --- | --- | --- | --- |
| Agent 1 | `extract_requirements` | Structured requirement extraction, product-request interpretation, missing questions, recommendation rationale | Decide prices or approvals |
| Agent 2 | `summarise_pricing_evidence` | Summarise pricing evidence, explain the deterministic analysis, describe risks | Calculate or override trusted commercial results |
| Agent 3 | `rewrite_email` | Rewrite or draft internal and customer email wording | Change the email type or drop protected commercial facts |
| Agent 4 | `plan_document` | Produce a `DocumentPlan` JSON with section order and customer-safe narrative | Generate trusted prices or approval status |

## Health checks

`app.agents.agent_health_report()` returns, per agent, the provider name, whether
the endpoint/model are configured, whether the API key variable is set
(`api_key_present`, boolean only), the key variable *name*, the prompt template
version and the fallback mode. Secret values never appear.

## Testing

Automated tests never call an external provider. The HTTP providers accept an
injected transport, and `MockProvider` returns canned responses or raises the
error required to exercise each fallback branch.

```bash
python -m pytest tests/unit/test_agent_providers.py -q
```
