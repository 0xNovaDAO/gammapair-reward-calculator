import json
import requests
from web3 import Web3
from web3.middleware import geth_poa_middleware

with open('hypervisor.json', 'r') as file:
    hypervisor_abi = json.load(file)

with open('rewards_0x2357c0466588Cf2F8d87cc654F763023880c3474.json', 'r') as file:
    rewards = json.load(file)

with open('config.json', 'r') as file:
    config = json.load(file)

with open('pk_config.json', 'r') as file:
    pk_config = json.load(file)


w3 = Web3(Web3.HTTPProvider(config['rpc_url']))
w3.middleware_onion.inject(geth_poa_middleware, layer=0)

yield_manager_wallet = w3.to_checksum_address(config['user_wallet_address'])
yield_manager_private_key = w3.to_checksum_address(pk_config['user_wallet_optional_pk'])

def break_lp(lp_addr, amt_to_break):
    lp_addr = w3.to_checksum_address(lp_addr)
    hypervisor_contract = w3.eth.contract(address=lp_addr, abi=hypervisor_abi)

    withdraw_txn = hypervisor_contract.functions.withdraw(
        amt_to_break,
        yield_manager_wallet,
        yield_manager_wallet,
        [0, 0, 0, 0]
    ).build_transaction({
        'chainId': config['chain_id'],
        'gas': 2000000,
        'gasPrice': w3.eth.gas_price * 2,
        'nonce': w3.eth.get_transaction_count(yield_manager_wallet)
    })

    signed_tx = w3.eth.account.sign_transaction(withdraw_txn, private_key=yield_manager_private_key)
    tx_hash = w3.eth.send_raw_transaction(signed_tx.rawTransaction)
    tx_receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    return tx_receipt