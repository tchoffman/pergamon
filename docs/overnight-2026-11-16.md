# Overnight work, 2026-11-15 → 16

What changed while you slept, what was verified, and what needs a decision.

## Answers got better (and safer)

- **New default model: Qwen 3.5 9B.** Scored 20/21 on the new eval (Gemma 4
  12B also 20/21 but slower on 16 GB; Llama 3.1 8B was the weakest).
- **Model answers first, library checks.** The library is used to verify and
  add detail, not as the only source.
- **Off-topic passages are dropped.** A quick model check now asks which
  passages actually answer the question. The "plant asparagus because it can
  be eaten like asparagus" answer came from a wild-lettuce entry in a
  wildflower book; it no longer gets through.
- **Date and location** (Marietta, GA, set in `config/install.local.yaml`)
  go to the model, so "what can I plant now?" gets a local answer.
- **Two dangerous answers fixed.** The old Army manual's cut-and-suck
  snakebite advice, and "frozen meat keeps indefinitely" after an outage.
  Curated facts (CDC/NIOSH, MedlinePlus, USDA) now override old books for
  snakebite, meningitis, freezer/fridge limits and canned goods.
- **`pergamon eval`**: 21 scored questions with must-say / must-never-say
  checks. Run it before any model or prompt change.

Still failing: Qwen's puffball answer skips "slice it open" even though that
fact is injected; Gemma's morel answer skips "hollow".

## Radio

- **Multi-part answers arrive.** Parts now wait for an ack (verified: 4/4
  over the air in 16 s).
- **Radio commands:** `/news`, `/find <item>`, `/skills <skill>`, `ping`,
  `help`.
- **Radio mailbox:** `/msg <name> <text>` is held by Pergamon and delivered
  when the other radio is in range (verified: delivered to Handheld-B this
  morning, retried every 15 min).
- **Hardening:** reconnects after USB drop/radio reboot (verified), no
  internal error text over the air, duplicate messages answered once, max 2
  pending questions per radio, answer text in the log.

## Web

- One question no longer freezes every page for every visitor (generation
  moved off the server's event loop; verified pages answer in ~20 ms
  mid-answer).
- Guests: one answer at a time per device, 30 questions/hour. The host
  machine is exempt.

## Housekeeping

- 27 scripts in `build/venv` still pointed at the old `~/github/oracle`
  path; repointed. (`pergamon web` was unaffected.)
- Docs: `docs/commands.md`, `docs/mesh-test-plan.md` (results + gotchas).

## Needs your call (details in docs/proposals.md)

1. **Posting to the board from a radio.** Reading works; posting would let
   anyone in radio range post under the hub's identity. Allowlisted radios
   only, or a host approval queue?
2. **A gardening shelf** (e.g. UGA extension planting calendar), since the
   library has none.
3. **Allowlist your handheld** (`!e06fbbd0`) so strangers in range can't use
   your Pergamon.
