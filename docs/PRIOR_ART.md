# Dark Fleet - Prior Art and Differentiation

**Status:** Draft v1. Based on a limited initial web survey (2026-10-30); vendor claims are **unverified marketing statements**. Needs a deeper pass before any strong novelty claim.

## 1. Real-world analogues (inspiration, not competitors)
Commercial maritime-intelligence vendors work on exactly this problem space. Their public descriptions indicate the following techniques; this is context for realism, not a claim about their actual performance.

| Source (vendor material) | Relevant idea | How Dark Fleet uses it |
|---|---|---|
| [Windward](https://windward.ai/knowledge-base/multi-source-tech-enhances-operational-readiness-makes-dark-vessel-detection-possible/) | Multi-source detection: radar detections correlated against AIS so unreported contacts remain; algorithms calibrated to separate ordinary signal loss from deliberate disablement; behavioral analytics rather than AIS alone | Sensor-fusion core; innocent-gap base rate; "absence is not proof" pillar |
| [Pole Star Global](https://www.polestarglobal.com/resources/unveiling-the-dark-fleet-and-the-latest-in-ais-spoofing-four-part-series/) | Reporting-gap detection, spoofing typologies, a transparency-style rating per vessel | Evidence scoring, deception traces and typology taxonomy |
| [Hellenic Shipping News: tracking dark vessels](https://hellenicshippingnews.com/how-to-track-dark-vessels-when-ais-goes-silent) | Multi-source tracking when AIS is silent | Design inputs for tasking and fusion |
| [Scientific American on AIS spoofing](https://www.scientificamerican.com/article/what-is-spoofing-how-a-u-s-seized-oil-tanker-reportedly-tried-to-evade/) | Cooperative-tracking limits; evasion and counter-evasion | Narrative framing |

## 2. Game analogues
| Title (type) | Relevance | Difference |
|---|---|---|
| Scotland Yard (board, 1983) and the hidden-movement genre (Fury of Dracula, Letters from Whitechapel, Mr. Jack) | Core asymmetric one-versus-many hidden-movement structure | Dark Fleet replaces grid chase with physical sensors, orbital timing, weather and an evidence/identification game; contacts are probabilistic, not exact reveals |
| [The Hunt](https://coopboardgames.com/?p=25575) (board, 2023; hunt for a raider) | Two-player asymmetric naval pursuit with incomplete information | Dark Fleet adds modeled sensors, deception mechanics, evidence scoring and an agent layer |
| Naval/submarine sims (not surveyed) | Sensor modeling in a combat setting | Dark Fleet has no combat simulation; the contest is detection, attribution and deception |

*Gap in this survey: no video-game titles were confirmed in this pass. A dedicated survey (naval strategy, intelligence-analysis games, geolocation/OSINT games) is required before claiming a differentiated niche.*

## 3. Agent and benchmark landscape
| Source | Relevance |
|---|---|
| Tool-injection benchmarks such as AgentDojo, InjecAgent and newer suites ([NetInjectBench, 2026](https://arxiv.org/pdf/2607.10490)) | Existing evaluation methods for prompt injection in tool-using agents; DarkFleet-Bench's injection tier should adopt compatible metrics and report adaptive attacks |
| Literature reporting that deterministic out-of-band policy defenses can fail under adaptive attacks | Motivates honest residual-risk reporting and adaptive tests ([search summary](https://arxiv.org/pdf/2602.10453); verify directly) |
| Model Context Protocol specification ([2026-07-28 revision](https://github.com/modelcontextprotocol/specification/releases/tag/2026-07-28)) | Tool/resource protocol; secondary sources report a move to a stateless design and deprecations of some client features - verify against the official spec before implementing |

## 4. Intended differentiation (to be validated)
1. A **physically grounded hidden-information game** whose "reveals" come from modeled sensors with weather and orbital timing.
2. A **deterministic, replayable, role-filtered simulation** that doubles as an **agent benchmark** with an injection tier.
3. Rigorous classical-versus-LLM comparison: tracker/planner baselines against agents, with cost and calibration.
4. Open licensing for code, docs and benchmark data.

## 5. Open research tasks
- Survey video games in naval strategy, intelligence-analysis and OSINT/geolocation genres.
- Survey existing agent environments/benchmarks involving partial observability and multi-agent tool use.
- Read primary sources (not vendor summaries) for AIS standards (ITU-R M.1371) and spoofing typologies.
- Review open maritime datasets and their licenses.
