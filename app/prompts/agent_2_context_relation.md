# Agent 2 — context relation

Decide whether a reply continues the previous exchange.

Return ONLY JSON:

```json
{
  "relation": "related",
  "include_previous_context": true,
  "rewritten_question": "...",
  "confidence": 0.94
}
```

Use `related`, `standalone`, or `ambiguous`.
