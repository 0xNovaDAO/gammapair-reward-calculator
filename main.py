import json
import re
import time

import requests
from web3 import Web3
from datetime import datetime, timezone
from web3.middleware import geth_poa_middleware
import concurrent.futures

with open('gammapair.json', 'r') as file:
    gammapair_abi = json.load(file)

with open('masterchef.json', 'r') as file:
    masterchef_abi = json.load(file)

with open('config.json', 'r') as file:
    config = json.load(file)


w3 = Web3(Web3.HTTPProvider(config['rpc_url']))
w3.middleware_onion.inject(geth_poa_middleware, layer=0)

masterchef_contract_address = config['masterchef_contract']
masterchef_contract = w3.eth.contract(address=masterchef_contract_address, abi=masterchef_abi)

token_price_cache = {}


def download_json_data(url):
    try:
        response = requests.get(url)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        print(f"Error fetching data: {e}")
        return None


def etherscan_datetime_to_timestamp(date_str): # Yeah this is pretty hacky, might be better to just demand an actual valid datetime
    pattern = r'(?P<date_time>.+?) (\+(?P<tz>\w+))?$'
    match = re.match(pattern, date_str)

    if not match:
        raise ValueError(f"Invalid date string format: {date_str}")

    date_time_str = match.group('date_time')
    tz_str = match.group('tz') or 'UTC'

    if tz_str != 'UTC':
        raise ValueError(f"Unsupported timezone: +{tz_str}")

    return int(datetime.strptime(date_time_str, "%b-%d-%Y %I:%M:%S %p").replace(tzinfo=timezone.utc).timestamp())


def fetch_token_usd_value(symbol):
    print(f"Waiting 10s before requesting price data for {symbol}")
    time.sleep(10)
    if symbol in token_price_cache:
        return token_price_cache[symbol]

    token_data = config['supported_token_prices'].get(symbol)
    if not token_data:
        return 0

    try:
        response = requests.get(token_data["endpoint"])
        response.raise_for_status()
        data = response.json()
        usd_value = data[token_data["id"]]['usd']
        token_price_cache[symbol] = usd_value

        return usd_value
    except requests.RequestException:
        print(f"Error fetching price for {symbol}.")
        return 0


def get_token_info(token_address):
    token_address = w3.to_checksum_address(token_address)
    try:
        token_contract = w3.eth.contract(address=token_address, abi=gammapair_abi)
        decimals = token_contract.functions.decimals().call()
        name = token_contract.functions.name().call()
        symbol = token_contract.functions.symbol().call()

        return decimals, name, symbol
    except Exception as e:
        print(f"Error checking token details for gammapair pool {token_address}: {e}")


def convert_to_normalised_dec(raw_value, decimals):
    normalised_value = raw_value / (10 ** decimals)
    return "{:,.{prec}f}".format(normalised_value, prec=decimals)


def check_pid_for_stake(pid, user_address, masterchef_contract, block='latest'):
    user_info = masterchef_contract.functions.userInfo(pid, user_address).call(block_identifier=block)
    if user_info[0] > 0:
        lp_contract_address = masterchef_contract.functions.lpToken(pid).call(block_identifier=block)
        return {
            'pid': pid,
            'lp_contract_address': lp_contract_address,
            'stake_amount': user_info[0]
        }


def get_user_stakes_in_pools(user_address, masterchef_contract):
    pool_count = masterchef_contract.functions.poolLength().call()
    user_stakes = []

    print(f"total pools: {pool_count}")

    with concurrent.futures.ThreadPoolExecutor() as executor:
        results = list(executor.map(lambda pid: check_pid_for_stake(pid, user_address, masterchef_contract), range(pool_count)))

    for result in results:
        if result:
            user_stakes.append(result)

    return user_stakes


def check_balance_for_pool(user_address, pool_key, hypervisor_data):
    try:
        pool_addr = w3.to_checksum_address(pool_key)
        pool_data = hypervisor_data[pool_key]
        lp_token_address = w3.to_checksum_address(pool_data["poolAddress"])
        ctr = w3.eth.contract(address=pool_addr, abi=gammapair_abi)
        user_balance = ctr.functions.balanceOf(user_address).call()

        if user_balance > 0:
            return {
                'pid': w3.to_checksum_address(pool_key),
                'lp_contract_address': lp_token_address,
                'stake_amount': user_balance
            }
    except Exception as e:
        print(f"Error checking balance for pool {pool_key}: {e}")
    return None


def get_user_stakes_from_wallet(user_address, hypervisor_data):
    user_stakes = []

    # Prepare the list of tasks for threading
    tasks = [(user_address, pool_key) for pool_key in hypervisor_data.keys()]

    with concurrent.futures.ThreadPoolExecutor() as executor:
        future_to_pool = {executor.submit(check_balance_for_pool, *task, hypervisor_data): task[1] for task in tasks}
        for future in concurrent.futures.as_completed(future_to_pool):
            result = future.result()
            if result:
                user_stakes.append(result)

    return user_stakes


def process_log_chunk(data, gammapair_contract):
    log_chunk, pid, current_user_stake = data
    user_fees0 = 0
    user_fees1 = 0

    for log in log_chunk:
        block_number = log['blockNumber']
        event_data = gammapair_contract.events.ZeroBurn().process_log(log)

        # If historical balance checking is enabled, fetch the stake at the time of the ZeroBurn being checked
        if config.get('check_historical_balances', False):
            stake_info = check_pid_for_stake(pid, user_wallet_address, masterchef_contract, block=block_number)
            user_stake = stake_info['stake_amount'] if stake_info else 0
        else:
            user_stake = current_user_stake

        total_supply = gammapair_contract.functions.totalSupply().call(block_identifier=block_number)
        user_percentage = user_stake / total_supply if total_supply > 0 else 0

        fees0 = event_data['args']['fees0']
        fees1 = event_data['args']['fees1']

        user_share_fees0 = fees0 * user_percentage
        user_share_fees1 = fees1 * user_percentage

        user_fees0 += user_share_fees0
        user_fees1 += user_share_fees1

    return user_fees0, user_fees1


def fetch_burn_events_and_calculate_fees(stake_info, start_datetime, end_datetime):
    lp_contract_address = stake_info['pid']
    gammapair_contract = w3.eth.contract(address=lp_contract_address, abi=gammapair_abi)

    start_timestamp = etherscan_datetime_to_timestamp(start_datetime)

    if end_datetime == "now":
        end_block = 'latest'
    else:
        end_timestamp = etherscan_datetime_to_timestamp(end_datetime)
        end_block = w3.eth.get_block('latest', full_transactions=True)['number'] - int(
            (w3.eth.get_block('latest')['timestamp'] - end_timestamp) / 2)

    start_block = w3.eth.get_block('latest', full_transactions=True)['number'] - int(
        (w3.eth.get_block('latest')['timestamp'] - start_timestamp) / 2)

    event_signature_hash = w3.keccak(text="ZeroBurn(uint8,uint256,uint256)").hex()
    logs = w3.eth.get_logs({
        'fromBlock': start_block,
        'toBlock': end_block,
        'address': lp_contract_address,
        'topics': [event_signature_hash]
    })

    block_numbers = [log['blockNumber'] for log in logs]
    print(f"{len(block_numbers)} zeroBurn event blocks to scan on contract {lp_contract_address}..")

    CHUNK_SIZE = 32
    log_chunks = [logs[i:i + CHUNK_SIZE] for i in range(0, len(logs), CHUNK_SIZE)]

    total_user_fees0 = 0
    total_user_fees1 = 0

    with concurrent.futures.ThreadPoolExecutor() as executor:
        results = list(
            executor.map(lambda data: process_log_chunk(data, gammapair_contract),
                         [(chunk, stake_info['pid'], stake_info['stake_amount']) for chunk in log_chunks]))

        for user_fees0, user_fees1 in results:
            total_user_fees0 += user_fees0
            total_user_fees1 += user_fees1

    print(f"User's total fees (token 0): {total_user_fees0}")
    print(f"User's total fees (token 1): {total_user_fees1}")

    return total_user_fees0, total_user_fees1


def get_lp_equivalent_for_rewards(lp_contract_address, delta0, delta1):
    gammapair_contract = w3.eth.contract(address=lp_contract_address, abi=gammapair_abi)

    token0_address = gammapair_contract.functions.token0().call()
    token1_address = gammapair_contract.functions.token1().call()
    token0_balance = w3.eth.contract(address=token0_address, abi=gammapair_abi).functions.balanceOf(lp_contract_address).call()
    token1_balance = w3.eth.contract(address=token1_address, abi=gammapair_abi).functions.balanceOf(lp_contract_address).call()

    total_lp_supply = gammapair_contract.functions.totalSupply().call()
    if total_lp_supply == 0 or token0_balance == 0 or token1_balance == 0:
        print(f"For contract: {lp_contract_address}, rebalance needed, or zero liquidity?")
        return -1

    value_per_lp_token0 = token0_balance / total_lp_supply
    value_per_lp_token1 = token1_balance / total_lp_supply
    lp_equivalent_for_delta0 = delta0 / value_per_lp_token0
    lp_equivalent_for_delta1 = delta1 / value_per_lp_token1

    total_lp_equivalent = lp_equivalent_for_delta0 + lp_equivalent_for_delta1

    return total_lp_equivalent


def get_lp_value_in_usd(stake_amount, hypervisor_info):
    if hypervisor_info['totalSupply'] > 0:
        user_percentage = stake_amount / hypervisor_info['totalSupply']
        lp_value_usd = user_percentage * float(hypervisor_info['tvlUSD'])
        return lp_value_usd
    return 0


def get_lp_tokens_for_fees(fees_usd, hypervisor_info):
    if float(hypervisor_info['tvlUSD']) > 0:
        total_lp_tokens = float(hypervisor_info['tvlUSD']) / float(hypervisor_info['totalSupply'])
        lp_tokens_for_fees = fees_usd / total_lp_tokens
        return lp_tokens_for_fees if lp_tokens_for_fees is not None else 0
    return 0


url = config['hypervisor_data']
hypervisor_data = download_json_data(url)

start_datetime = config['start_datetime']
end_datetime = config['end_datetime']
user_wallet_address = config['user_wallet_address']
user_stakes = get_user_stakes_from_wallet(user_wallet_address, hypervisor_data)
results = []

for stake_info in user_stakes:
    user_fees0, user_fees1 = fetch_burn_events_and_calculate_fees(stake_info, start_datetime, end_datetime)

    gammapair_contract = w3.eth.contract(address=stake_info['lp_contract_address'], abi=gammapair_abi)

    token0_address = gammapair_contract.functions.token0().call()
    token1_address = gammapair_contract.functions.token1().call()
    token0_decimals, token0_name, token0_symbol = get_token_info(token0_address)
    token1_decimals, token1_name, token1_symbol = get_token_info(token1_address)

    user_fees0_readable = convert_to_normalised_dec(user_fees0, token0_decimals)
    user_fees1_readable = convert_to_normalised_dec(user_fees1, token1_decimals)

    token0_usd_value = fetch_token_usd_value(token0_symbol)
    token1_usd_value = fetch_token_usd_value(token1_symbol)

    user_fees0_readable = user_fees0_readable.replace(',', '')
    user_fees1_readable = user_fees1_readable.replace(',', '')

    user_fees0_usd = float(user_fees0_readable) * token0_usd_value if token0_usd_value else None
    user_fees1_usd = float(user_fees1_readable) * token1_usd_value if token1_usd_value else None

    lp_decimals, lp_name, _ = get_token_info(stake_info['pid'])

    results.append({
        'pid': stake_info['pid'],
        'lp_address': stake_info['lp_contract_address'],
        'lp_name': lp_name,
        'stake_amount': stake_info['stake_amount'],
        'token0_name': token0_name,
        'token0_symbol': token0_symbol,
        'token0_address': token0_address,
        'user_fees0_raw': user_fees0,
        'user_fees0': user_fees0_readable,
        'token1_name': token1_name,
        'token1_symbol': token1_symbol,
        'token1_address': token1_address,
        'user_fees1_raw': user_fees1,
        'user_fees1': user_fees1_readable,
        'user_fees0_usd': user_fees0_usd,
        'user_fees1_usd': user_fees1_usd
    })

    print(f"PID: {stake_info['pid']}, LP Address: {stake_info['lp_contract_address']}, Stake Amount: {stake_info['stake_amount']}")
    print(f"User Fees in {token0_name} ({token0_symbol}): {user_fees0_readable}")
    print(f"User Fees in {token1_name} ({token1_symbol}): {user_fees1_readable}")

formatted_results = {
    'start_date': start_datetime,
    'end_date': end_datetime,
    'wallet_address': user_wallet_address,
    'masterchef_address': masterchef_contract_address
}

print("--------------------------------------------------")
print(f"Rewards due for wallet {user_wallet_address} from {start_datetime} to {end_datetime}:")
print("--------------------------------------------------")
for result in results:
    print(json.dumps(result, indent=4))
    lp_pair_symbol = result['lp_name']
    lp_contract_address = result['pid'].lower()

    total_lp_value_usd = None
    lp_tokens_for_fees = None
    total_fees = result['user_fees0_usd'] + result['user_fees1_usd']

    if lp_contract_address in hypervisor_data:
        hypervisor_info = hypervisor_data[lp_contract_address]

        total_lp_value_usd = get_lp_value_in_usd(result['stake_amount'], hypervisor_info)
        lp_tokens_for_fees = get_lp_tokens_for_fees(total_fees, hypervisor_info)

    formatted_results[lp_pair_symbol] = {
        'contract': result['lp_address'],
        'token0': {
            'address': result['token0_address'],
            'name': result['token0_name'],
            'symbol': result['token0_symbol'],
            'fees_raw': result['user_fees0_raw'],
            'fees_normalised': result['user_fees0'],
            'fees_usd': result['user_fees0_usd']
        },
        'token1': {
            'address': result['token1_address'],
            'name': result['token1_name'],
            'symbol': result['token1_symbol'],
            'fees_raw': result['user_fees1_raw'],
            'fees_normalised': result['user_fees1'],
            'fees_usd': result['user_fees1_usd']
        },
        'total_fees_usd': total_fees,
        'total_lp_value_usd': total_lp_value_usd,
        'total_lp_tokens': result['stake_amount'],
        'total_lp_tokens_normalized': convert_to_normalised_dec(result['stake_amount'], 18),
        'lp_tokens_for_fees': lp_tokens_for_fees,
        'lp_tokens_for_fees_normalized': convert_to_normalised_dec(lp_tokens_for_fees, 18)
    }
    print(f"PID: {result['pid']}, LP Address: {result['lp_address']}, Stake Amount: {result['stake_amount']}")
    print(f"Rewards For LP: {result['lp_name']} ::")
    print(f"User Fees in {result['token0_name']} ({result['token0_symbol']}): {result['user_fees0']}")
    print(f"User Fees in {result['token1_name']} ({result['token1_symbol']}): {result['user_fees1']}")
    if result['user_fees0_usd']:
        print(f"User Fees in {result['token0_name']} (USD): ${result['user_fees0_usd']:,.2f}")
    if result['user_fees1_usd']:
        print(f"User Fees in {result['token1_name']} (USD): ${result['user_fees1_usd']:,.2f}")
    print("--------------------------------------------------")


total_fees_usd_all_pairs = 0
total_lp_value_usd_all_pairs = 0

for key, value in formatted_results.items():
    if isinstance(value, dict):
        total_fees_usd_all_pairs += value.get('total_fees_usd', 0)
        total_lp_value_usd_all_pairs += value.get('total_lp_value_usd', 0) if value.get('total_lp_value_usd') is not None else 0

formatted_results["TOTAL"] = {
    'total_fees_usd_all_pairs': total_fees_usd_all_pairs,
    'total_lp_value_usd_all_pairs': total_lp_value_usd_all_pairs
}


if config.get('timestamp_rewards_json', False):
    filename = f"rewards_{user_wallet_address}_{start_datetime}_-_{end_datetime}.json"
else:
    filename = f"rewards_{user_wallet_address}.json"

with open(filename, 'w') as outfile:
    json.dump(formatted_results, outfile, indent=4)
