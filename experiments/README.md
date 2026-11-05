# Reproducible experiments

Open `/lab` for the separate laboratory page. `/` remains the main simulation.
The header links connect both pages; experiments do not create or mutate a normal session.

Run from the project root with `python -m backend.experiments`.
The default runs A* and fixed rules without network calls. Add `--modes astar rules agent`
to include real OpenRouter calls using the server's configured model and API key.
The runner saves metrics.csv and trials.json (including decision provenance, never secrets).

Five scenarios use the same north map, delivery mission, initial state, permissions,
and scripted gate schedule. A wall at x=3 has an opening at y=7; the detour scenario
also opens y=14. A stopped cart occupies (3,7) from tick 18 until tick 21+variant
(short) or 45+variant (long/detour). A permanent obstruction closes it indefinitely.
Variants 0–4 change the release tick, not the policy's knowledge of future events.
Every mode is limited to 180 simulation ticks. Agent/API failures are recorded as
provider_error, never replaced by rule decisions. No automatic provider retries.

Ticks freeze during LLM calls. Report API wall latency separately; do not interpret
ticks as seconds or omit unsuccessful trials from the success rate. Completion time
is null for unfinished missions. Operator requests count requests, not assumed human
repairs. These controlled gate scenarios do not model cart kinematics or physical time.
