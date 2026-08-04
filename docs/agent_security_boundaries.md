# Agent security boundaries

## Trust pipeline

Every provider response passes through the same pipeline in
`app/agents/pipeline.py`:

```text
raw response
  -> JSON parsing            (invalid_json)
  -> schema validation       (schema_validation, unknown fields forbidden)
  -> business-rule validation(business_rule)
  -> protected-field checks  (protected_field)
  -> accepted result
     or deterministic fallback
```

Any failure at any stage produces the deterministic result. AI output never
writes to workflow state directly: the calling deterministic code decides what
to do with an accepted schema instance.

## Circuit-breaker fallback

The following always fall back to deterministic mode and are recorded with an
explicit error category:

| Condition | Error category |
| --- | --- |
| Missing or unusable API configuration | `missing_configuration` |
| Invalid environment configuration | `invalid_configuration` |
| Timeout (after configured retries) | `timeout` |
| Non-JSON or non-object response | `invalid_json` |
| Schema validation failure or unknown field | `schema_validation` |
| Business-rule violation | `business_rule` |
| Missing protected commercial fact | `protected_field` |
| Provider or transport error | `provider_error` |
| Unsafe output | `unsafe_output` |

A provider failure can therefore never block completion of the deterministic
workflow.

## Protected commercial facts

Callers pass the deterministic protected values (quotation identifier, customer
name, product identifier, quantity, currency, formatted prices, margin summary,
validation status, approval status) to the agent request. If any of those values
is missing from the AI output, the output is rejected and the deterministic
version is used. This generalises the existing
`app/email_generator.py` fact-protection check.

Additional per-agent rules:

* Agent 1 and Agent 2 output is rejected if it asserts a commercial or approval
  decision (`approved`, `discount`, `gross margin`, `final price`, `net price`,
  `minimum price`).
* Agent 3 may not change the email type.
* Agent 4 must keep exactly the required section set, may not invent or
  duplicate sections, and may not reference any field name listed in
  `app.config.CUSTOMER_PROHIBITED_FIELDS`.

Trusted prices, rule outcomes, approval status and the customer/internal data
separation stay entirely in the deterministic modules. They are inputs to the
agents, never outputs of the agents.

## Secret handling

* API keys are only referenced by environment variable *name* in configuration.
* Keys are resolved at call time in the provider, used for the request header
  only, and never stored on the configuration object.
* Audit records, health reports and `AgentProviderConfig.describe()` expose only
  the variable name and a boolean `api_key_present`.
* `app/agents/audit.py` scrubs any usage key that looks like a credential
  (`api_key`, `secret`, `password`, `authorization`, `credential`, `bearer`).
* No secret is written to logs, the UI, the database or exports.

## Audit record

Each invocation records: agent name, provider, model, start and end time,
duration, status (`accepted` or `fallback`), `fallback_used`, prompt-template
version, error category, a short non-sensitive error detail, and token/usage
metadata when the provider supplies it. Prompts and raw business payloads are
not logged by default.

## Testing policy

Automated tests must not call external providers. Use `MockProvider` or inject a
fake transport into `HttpJsonProvider` / `OpenAICompatibleProvider`. The default
base URLs used in tests point to unreachable `.invalid` hosts.
