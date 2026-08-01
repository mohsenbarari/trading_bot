# Final group-offer dataset contract

This contract applies only after a message has passed the relevance, offer-field,
and trade-linking gates.  The raw and staging layers remain separate and may keep
technical identifiers solely for deduplication, reply resolution, audit, and
retraining.

## Retained identity/source fields in final data

| Field | Meaning |
| --- | --- |
| `group_number` | Stable business group number: `1` for `account2_group1`, `2` for `account2_group2`. |
| `offerer_name` | Display name of the account that published the offer. |
| `counterparty_name` | Display name of the other party, only for a confirmed linked trade; otherwise `NULL`. |

The final record also retains the business/model fields needed to use the data:
offer time, parsed commodity, side, settlement (`today`/`tomorrow`), price,
quantity, free-form description when accepted, confirmation status, and the
provenance/quality version of the extraction.  These are not source-identifying
metadata.

## Explicitly excluded from final data

- Telegram message ID, reply ID, sender peer ID, event ID, source key, channel
  title/address/link, ingest file name/hash/offset, and raw event payload.
- Technical crawler/listener metadata and all private Telegram identifiers.

## Boundary rule

`sender_peer_id` and message IDs are permitted only in the private raw/staging
databases so that a reply can be resolved to its offer and a later source update
can replace an earlier version.  The final/promoted dataset must be produced by
a projection which never selects those columns or embeds them inside JSON.

## Name selection

Use the latest non-empty `sender_name` supplied by the source.  If it is absent,
the final field is `NULL`; do not substitute `sender_peer_id` or an opaque ID.
For a confirmed trade, `counterparty_name` is the named requester/acceptor on
the linked branch, not the name of an unrelated reply participant.
