# Thesis

Version 1, 2026-10-21. Next review: 2026-11-03, after the HSP-029 vote closes (2026-11-02), and again on 2026-12-31 when P1 and P2 are decided. Earlier versions stay in git history.

## The claim

**NEAR Intents earns real and growing revenue, but it does not shrink NEAR's supply, and at today's volume it is far from offsetting dilution. Holding NEAR today is a bet on Intents growing several times over, not on scarcity.**

## The evidence

Every number here is verified in this repository. "Attested" means it comes from the TEE-attested daily entry in `nearbooks.near`; the rest comes from the offline pipeline (`make verify`).

1. **Supply keeps growing.** In the 28 days to 2026-10-21, net issuance was about 87,600 NEAR a day (attested). Total supply rose in every sampled interval of 2026 (finding F2b).
2. **Revenue is real but covers a small share.** Over the same 28 days the three Intents revenue wallets took in 17.3% as much NEAR as total supply grew (attested). Captured revenue rebuilds from raw transfers to within 0.56% of DefiLlama for 2026 (F1a).
3. **Bought-back NEAR is held, not burned.** It sits in `buybacks.multisignature.near` (F2a). Under the current policy no amount of volume makes total supply shrink; revenue only changes the share of new supply that ends up in the treasury's wallets.
4. **Even a burn would need about 5x more volume.** If the bought-back NEAR were burned, buybacks would offset issuance only at about $795M of Intents volume a day, 5.3x today's $149M (F4a). That is about 87,600 NEAR of purchases a day, whatever the price.
5. **HSP-029 helps slowly.** HSP-029's 1.6% issuance target lowers that bar to about 3.4x (F4b), but it ramps in over 24 months from implementation, so the full effect arrives around 2029. The vote closes 2026-11-02, and passing it changes nothing before 2027.
6. **The public numbers agree once lined up.** The eight most widely shared claims about NEAR's buybacks and issuance are graded in [`docs/claims.md`](docs/claims.md). Most hold. The official dashboard's "permanently remove" describes a policy that the chain does not enforce.

**What "dilution" means here.** It means growth in total supply. Most new NEAR goes to stakers, so a holder who stakes loses far less of their share than one who does not. The thesis is about the token's supply, not any one holder's position.

## What would prove it wrong

Any one of these would make us revise the thesis:
- **Revenue catches up.** The revenue wallets' 28-day gain reaches 50% of new supply in two consecutive 28-day windows. Prediction P4 tests this on chain for the window ending 2027-01-31.
- **Supply shrinks.** That would mean a burn of the bought-back NEAR or another supply sink. P1 tests it for 2026-12-31.
- **Issuance is cut sooner.** An issuance cut takes effect before 2027, faster than HSP-029's ramp.

## How it is scored

Six predictions are registered on chain, each decided only by the attested entry for its date:
- **Before judging:** P5 is decided on 2026-11-05 and P6 on 2026-11-15.
- **At year-end:** P1 to P4 are decided on 2026-12-31 and 2027-01-31.

Our odds for each, stated when they were registered, are in [`docs/deployment.md`](docs/deployment.md#predictions). A refuted prediction stays on chain next to the confirmed ones.

Not financial advice.
