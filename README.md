# Gamma Reward Calculator

----

*Update 26/03/24: Updated to support coingecko pro endpoints*

----

*Update 02/03/24: lpBreaker.py added, which when run automatically breaks off the rewards portion of LP pairs based off the rewards.json output from main.py.*

----

*Update 03/02/24: Gamma Reward Calculator now accounts for self-custody LP, rather than relying on masterchef for staked token values.*

----

Gamma Liquidity Management is an extremely effective real-yield maximizer for liquidity pairs, and is currently playing home to around $80mil TVL.

Gamma Pairs function by operating in tight liquidity ranges in order to maximize user fees paid to liquidity providers. 

Once these ranges are broken through price movements on either side of the pair, liquidity positions are automatically compounded and re-created within new ranges in order to ensure providers remain in highly concentrated positions for effective fee accrual.

----

Due to the frequency of pair recreation required to maintain highly concentrated positions (several dozen times daily for volatile pairs), coupled with all user's fees being automatically compounded into these new positions, it can be difficult to determine the exact amount of fees accrued by an individual liquidity provider.

This can introduce difficulties when working with managed or shared LP, which are common in vaulting environments, or with DAO Treasuries/Treasury reporting.

The Gamma Reward Calculator removes this unknown by querying a given Gamma Pair's contract for all compounding events that took place on-chain between any given dates. With a specified wallet address, the wallet's stake is used to determine the percentage of the emitted fees which are due from each compounding event, and tallied for their total rewards over time.

Given the large amount of events needed to traverse for calculating fees over several weeks/months (potentially across several pools), the Gamma Reward Calculator utilizes concurrent futures/threading in order to calculate the awaiting rewards as quickly as possible.

## Masterchef Startup / Pool Discovery

This reward calculator has been specifically designed for use with masterchef contracts which house multiple pools; allowing for a pre-amble stage where we can determine every pool that the given wallet address is participating in, and grabbing their staked balance without the need to exit from any yield pools.

In short: provide a wallet address and a masterchef contract for gamma pools, coupled with a date range, and the Gamma Reward Calculator will determine exactly what fees are owed over this time period.

## Variable Stakes

Variable stakes are supported - where the Gamma Reward Calculator will check if a user's stake changed on any given block. However, due to the exponentially higher amount of RPC calls needed for this operation, it's strongly recommended that this script be used only for static/unchanged staking amounts (nb: gamma auto-compounding does **not** increase your staking token balance).

## Setup / Use

All settings can be set in config.json. Note that your RPC provider must allow for event log retrieval, and lookup of historical blocks. Infura's free tier should suffice here.

`rpc_url` : https endpoint

`user_wallet_address` : Enter the wallet address which holds any staking positions here.

`masterchef_contract` : Enter the masterchef contract address for any staking pools here. This is pre-loaded with Gamma's QuickSwap (Polygon) masterchef.

`start_datetime` : accepts Etherscan-formatted dates. Look up the tx you staked in to any Gamma pools, and copy/paste the transaction time in here (including the +UTC). Defines when to start searching for rewards owed.

`end_datetime` : same as the above, but defines when to stop searching for rewards owed. Enter `'now'` to search until the most recent block.

`timestamp_rewards_json` : set to `true` to add a timestamp to the rewards output file (useful if you need to compare a few results).

To enable variable stake lookup (i.e: stake amounts changed during staking), set `check_historical_balances` to `true`. **NB: This is much slower, and will likely exhaust your allowance with any free RPC Providers.**

----
Once your config.json is set up as required, run main.py to begin scanning for your given reward values.

## Supported Token Prices

The `supported_token_prices` section of config.json allows for inputting any api endpoints for tokens that support retrieving usd price results for tokens (following coingecko's api standard). If a paired token is available for lookup here, the results output will display the reward fees accrued at their current USD valuation.

## Small print

The given source code is provided as-is and without warranty or guarantee. The Gamma Reward Calculator is not intended as a tool for any level of official accounting, book-keeping, or tax reporting. By utilizing the Gamma Reward Calculator application or source code, you acknowledge and agree that the Author, and/or Nova DAO LLC shall not bear any liability for any following actions, losses, or consequences incurred.


Any questions/complaints (yes, the code IS messy) can be fired on to [@EoghanH](https://x.com/EoghanH). Built for [@0xNovaDAO](https://x.com/0xNovaDAO) x [@DeFiGirlsNFT](https://x.com/DeFiGirlsNFT) Yield Rewards program.