# Coin Intelligence — Shadow Preview API

`POST /api/offers/inference-preview` is an authenticated, feature-flagged
shadow API. It takes only the project-unit price and `cash|tomorrow` settlement,
loads one local atomically-published rate Snapshot, ranks/matches candidates,
and appends a minimal audit decision before returning it.

The default is disabled:

```text
COIN_INTELLIGENCE_INFERENCE_PREVIEW_ENABLED=false
COIN_INTELLIGENCE_INFERENCE_SNAPSHOT_PATH=
```

With no configured path it fails closed. It cannot create, update, cancel, or
otherwise affect an Offer. `POST /api/offers/`, the bot parser, and the WebApp
remain unchanged. A returned `AUTO_SELECT` is an observation only; `CONFIRM`
and `ABSTAIN` never receive a hidden Imam fallback.

No collector, snapshot publisher, scheduler, external AI call, Telegram action,
or multi-server synchronization is started by this endpoint.

When the same flag is enabled, `POST /api/offers/parse` also observes only an
omitted-name parse that reached the legacy Imam fallback. Its existing
`commodity_id` and `commodity_name` remain untouched; the response gains a
`commodity_inference` object with `mode=SHADOW_ONLY`. Explicit commodity text,
the bot flow, and the WebApp UI are unchanged. This lets the interface compare
the legacy result with an inferred result before any user-visible policy is
promoted.
