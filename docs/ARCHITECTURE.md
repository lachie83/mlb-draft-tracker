# MLB Draft 2026 Tracker — Architecture

## Data flow
1. `mlb_draft_prospects(2026)` populates `prospects`
2. Official MLB order data populates `draft_slots`
3. Prediction engine creates `predictions`
4. Live polling detects drafted prospects and writes `actual_picks`
5. Telegram notifier emits one message per new pick

## Source priority
### Prospects
1. `baseballr::mlb_draft_prospects(2026)`
2. MLB Pipeline / prospect pages as fallback enrichment

### Draft order
1. Official MLB 2026 order page
2. manually maintained CSV seed if page extraction is incomplete

### Live picks
1. `baseballr::mlb_draft_prospects(2026)` if near real-time
2. official MLB draft tracker page / endpoint fallback

## Prediction model v1
Heuristic model:
- 55% rank score
- 25% pick proximity / mock-shape score
- 15% team-fit score
- 5% buzz score

This is intentionally simple and should be treated as a baseline.

## Future improvements
- ingest mock drafts from MLB, BA, ESPN, FanGraphs
- team-specific priors based on historical draft behavior
- Monte Carlo simulation of first round outcomes
- dashboard with best available and surprise/reach metrics
