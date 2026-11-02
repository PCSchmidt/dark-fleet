# Status and open questions (2026-11-02, tag `v0.3-rules-v3`)

**State:** engine, rules v3, benchmark v2, learned Runners (four generations), a language-model Hunter study and a playable client with guided practice games are built and tested (42 C++ cases, 122 Python tests; CI green on Ubuntu and Windows). Detail: [RULES_V3.md](./RULES_V3.md).

## Owner playtest (first pass, 2026-11-02)
The unguided client was not playable by a newcomer: the rules were never explained in the interface. After adding guided practice games (`play/coach.py`), the owner's verdict was that the game makes sense and is "okay". A longer unguided playtest of the harder cases (dark and spoofed Runners, both roles) has **not** been done.

## Guided practice
Buttons *Guided practice: Hunter / Runner* start seed 2 with a step-by-step coach that uses only the player's own observation. Following the coach wins the Runner game on 20 of 20 seeds against the client's Hunter bot (the audit bot, without jump re-check or area sweep) and the Hunter game on 36 of 40 seeds where the Runner transfers with AIS on (two thin claims, two seeds where the target was not found). It does not cover a dark or spoofing Runner.

## Open design questions (untested)
- Payoffs 20 / 100 / 0 and the -25 / -50 penalties.
- The 6-of-12-tick grounds interval, the 30 km transfer area, 6 SAR passes, the decoys.
- Whether a human finds the dark and spoofed cases fun or a chore.

## Experiments not run
- Strong language model against the learned Runners (the only run that would show it does something the scripted bots cannot).
- Prompt-injection, raw-tools and no-defence studies under rules v3.
- Anomaly detector and claim calibrator retrained for v3.
- Hunter learned against the learned Runners; reinforcement learning; any policy beyond the 11-number search.

## Caveats on the language-model layer
The client plays fully without it (`--no-agents`). It has not been shown to beat the best free scripted Hunter (`pf_audit_sweep`, 87% on the v3 bench). Any rule change invalidates the recorded model runs. About $2.80 of the OpenRouter key budget remains.
