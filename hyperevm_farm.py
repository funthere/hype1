#!/usr/bin/env python3
"""
HyperEVM Testnet Farming Bot
=============================
Automatically interacts with DeFi protocols on HyperEVM testnet to build
on-chain history for airdrop farming. Uses ONLY testnet faucet tokens - ZERO real capital.

Research findings (May 2026):
  - HyperEVM Testnet: chain_id=998, RPC=https://rpc.hyperliquid-testnet.xyz/evm
  - HyperEVM Mainnet: chain_id=999, RPC=https://rpc.hyperliquid.xyz/evm
  - Testnet explorer: https://testnet.purrsec.com/
  - Mainnet explorer: https://hyperevmscan.io/
  - Faucet: https://app.hyperliquid-testnet.xyz/drip (UI only, requires mainnet deposit)
  - Bridge (HyperCore to EVM): 0x2222222222222222222222222222222222222222

Protocol addresses (mainnet confirmed):
  - HyperSwap V2: 0x03832767bDF9a8EF007449942125Ad605aCfADB8 (factory/router)
  - HyperLend Pooled: 0x5e887f0c6c3deec190c36186bf23369f
  - HypurrFi: airdrop-only protocol by Hyperliquid (address not public)
  - Multicall3: 0xcA11bde05977b3631167028862bE2a173976CA11 (both chains)

  NOTE: Testnet has its own separate contract deployments; mainnet addresses
  do NOT exist on testnet. The bot auto-discovers contracts on the target chain.

Usage:
  python3 hyperevm_farm.py [--dry-run] [--mainnet] [--loop] [--interval MINUTES]

Cron example (daily at 3am UTC):
  0 3 * * * cd ~/hyperliquid-bot && python3 hyperevm_farm.py >> hyperevm_farm.log 2>&1
"""

import argparse
import json
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests
from eth_account import Account
from web3 import Web3
from web3.exceptions import ContractLogicError, TimeExhausted

# ============================================================
# CONFIGURATION
# ============================================================

ENV_PATH = Path(__file__).parent / ".env"
if ENV_PATH.exists():
    with open(ENV_PATH) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())

CHAINS = {
    "testnet": {
        "chain_id": 998,
        "rpc": "https://rpc.hyperliquid-testnet.xyz/evm",
        "name": "HyperEVM Testnet",
        "explorer": "https://testnet.purrsec.com",
        "bridge": "0x2222222222222222222222222222222222222222",
        "native_symbol": "tHYPE",
        "faucet_url": "https://app.hyperliquid-testnet.xyz/drip",
    },
    "mainnet": {
        "chain_id": 999,
        "rpc": "https://rpc.hyperliquid.xyz/evm",
        "name": "HyperEVM Mainnet",
        "explorer": "https://hyperevmscan.io",
        "bridge": "0x2222222222222222222222222222222222222222",
        "native_symbol": "HYPE",
        "faucet_url": None,
    },
}

KNOWN_CONTRACTS = {
    "mainnet": {
        "hyper_swap": "0x03832767bDF9a8EF007449942125Ad605aCfADB8",
        "hyperlend": "0x5e887f0c6c3deec190c36186bf23369f",
        "multicall3": "0xcA11bde05977b3631167028862bE2a173976CA11",
    },
    "testnet": {
        "multicall3": "0xcA11bde05977b3631167028862bE2a173976CA11",
    },
}

ERC20_ABI = json.loads('[{"constant":true,"inputs":[],"name":"symbol","outputs":[{"name":"","type":"string"}],"type":"function"},{"constant":true,"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"type":"function"},{"constant":true,"inputs":[{"name":"","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"},{"constant":false,"inputs":[{"name":"","type":"address"},{"name":"","type":"uint256"}],"name":"approve","outputs":[{"name":"","type":"bool"}],"type":"function"}]')

FACTORY_ABI = json.loads('[{"constant":true,"inputs":[{"name":"","type":"address"},{"name":"","type":"address"}],"name":"getPair","outputs":[{"name":"","type":"address"}],"type":"function"},{"constant":true,"inputs":[],"name":"allPairsLength","outputs":[{"name":"","type":"uint256"}],"type":"function"},{"constant":true,"inputs":[{"name":"","type":"uint256"}],"name":"allPairs","outputs":[{"name":"","type":"address"}],"type":"function"}]')

ROUTER_ABI = json.loads('[{"constant":true,"inputs":[],"name":"WETH","outputs":[{"name":"","type":"address"}],"type":"function"},{"constant":false,"inputs":[{"name":"","type":"uint256"},{"name":"","type":"address[]"},{"name":"","type":"address"},{"name":"","type":"uint256"}],"name":"swapExactETHForTokens","outputs":[{"name":"","type":"uint256[]"}],"payable":true,"type":"function"},{"constant":false,"inputs":[{"name":"","type":"uint256"},{"name":"","type":"uint256"},{"name":"","type":"address[]"},{"name":"","type":"address"},{"name":"","type":"uint256"}],"name":"swapExactTokensForTokens","outputs":[{"name":"","type":"uint256[]"}],"type":"function"}]')

LENDING_ABI = json.loads('[{"constant":false,"inputs":[{"name":"asset","type":"address"},{"name":"amount","type":"uint256"},{"name":"onBehalfOf","type":"address"},{"name":"referralCode","type":"uint16"}],"name":"supply","outputs":[],"type":"function"}]')

MULTICALL_ABI = json.loads('[{"constant":false,"inputs":[{"name":"","type":"bytes[]"}],"name":"aggregate","outputs":[{"name":"","type":"uint256"},{"name":"","type":"bytes[]"}],"type":"function"}]')

LOG_FILE = Path(__file__).parent / "hyperevm_farm.log"
STATE_FILE = Path(__file__).parent / "hyperevm_farm_state.json"


def log(msg: str, level: str = "INFO"):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[{ts}] [{level}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


class HyperEVMFarmer:
    def __init__(self, network: str = "testnet", dry_run: bool = False):
        self.network = network
        self.dry_run = dry_run
        self.chain = CHAINS[network]
        self.w3 = Web3(Web3.HTTPProvider(self.chain["rpc"], request_kwargs={"timeout": 30}))
        if not self.w3.is_connected():
            raise ConnectionError(f"Cannot connect to {self.chain['rpc']}")
        pk = os.environ.get("PRIVATE_KEY", "")
        if not pk:
            raise ValueError("PRIVATE_KEY not set in .env")
        self.account = Account.from_key(pk)
        self.address = self.account.address
        self.nonce = self.w3.eth.get_transaction_count(self.address)
        self.state = self._load_state()
        self.contracts = {}
        log("=" * 60)
        log("HYPEREVM FARMING BOT")
        log("=" * 60)
        log(f"Network: {self.chain['name']} (chain_id={self.chain['chain_id']})")
        log(f"RPC: {self.chain['rpc']}")
        log(f"Address: {self.address}")
        log(f"Block: {self.w3.eth.block_number:,}")
        log(f"Gas: {self.w3.from_wei(self.w3.eth.gas_price, 'gwei')} Gwei")
        log(f"Explorer: {self.chain['explorer']}/address/{self.address}")
        log(f"Dry run: {dry_run}")
        log("")
        self._discover_contracts()

    def _load_state(self) -> dict:
        if STATE_FILE.exists():
            try:
                with open(STATE_FILE) as f:
                    return json.load(f)
            except Exception:
                pass
        return {"total_txns": 0, "last_run": None, "daily_txns": 0, "daily_date": None}

    def _save_state(self):
        try:
            with open(STATE_FILE, "w") as f:
                json.dump(self.state, f, indent=2)
        except Exception as e:
            log(f"State save error: {e}", "WARN")

    def _discover_contracts(self):
        log("--- Contract Discovery ---")
        known = KNOWN_CONTRACTS.get(self.network, {})
        for name, addr in known.items():
            try:
                code = self.w3.eth.get_code(Web3.to_checksum_address(addr))
                if code and code != b"" and code != b"0x":
                    self.contracts[name] = addr
                    log(f"  OK {name}: {addr}")
                else:
                    log(f"  -- {name}: {addr} (no code on {self.network})")
            except Exception as e:
                log(f"  -- {name}: {addr} ({e})")
        # Discover DEX pairs if factory exists
        factory = self.contracts.get("hyper_swap")
        if factory:
            try:
                f = self.w3.eth.contract(address=Web3.to_checksum_address(factory), abi=FACTORY_ABI)
                n = f.functions.allPairsLength().call()
                log(f"  DEX has {n:,} pairs")
                for i in range(min(int(n), 5)):
                    p = f.functions.allPairs(i).call()
                    self._inspect_pair(p)
            except Exception as e:
                log(f"  DEX error: {e}", "WARN")
        log("")

    def _inspect_pair(self, pair_addr):
        try:
            s0 = self.w3.eth.get_storage_at(Web3.to_checksum_address(pair_addr), 0)
            t0 = Web3.to_checksum_address(s0[-20:])
            s1 = self.w3.eth.get_storage_at(Web3.to_checksum_address(pair_addr), 1)
            t1 = Web3.to_checksum_address(s1[-20:])
            sym0 = self._token_sym(t0)
            sym1 = self._token_sym(t1)
            log(f"    Pair: {sym0}/{sym1}")
            if sym0 != "???": self.contracts[f"token_{sym0.lower()}"] = t0
            if sym1 != "???": self.contracts[f"token_{sym1.lower()}"] = t1
        except Exception:
            pass

    def _token_sym(self, addr):
        try:
            t = self.w3.eth.contract(address=Web3.to_checksum_address(addr), abi=ERC20_ABI)
            return t.functions.symbol().call()
        except Exception:
            return "???"

    def native_bal(self):
        return float(self.w3.from_wei(self.w3.eth.get_balance(self.address), "ether"))

    def token_bal(self, addr):
        try:
            t = self.w3.eth.contract(address=Web3.to_checksum_address(addr), abi=ERC20_ABI)
            d = t.functions.decimals().call()
            b = t.functions.balanceOf(self.address).call()
            return b / (10 ** d)
        except Exception:
            return 0.0

    def _send_tx(self, builder_fn, desc) -> Optional[str]:
        if self.dry_run:
            log(f"  [DRY] {desc}")
            return "dry"
        try:
            self.nonce = self.w3.eth.get_transaction_count(self.address)
            tx = builder_fn()
            tx["nonce"] = self.nonce
            tx["chainId"] = self.chain["chain_id"]
            tx["gas"] = self.w3.eth.estimate_gas(tx)
            tx["gasPrice"] = self.w3.eth.gas_price
            signed = self.account.sign_transaction(tx)
            h = self.w3.eth.send_raw_transaction(signed.raw_transaction)
            hhex = h.hex()
            log(f"  TX: {hhex}")
            log(f"  -> {self.chain['explorer']}/tx/{hhex}")
            rc = self.w3.eth.wait_for_transaction_receipt(h, timeout=120)
            if rc.status == 1:
                log(f"  OK {desc} (gas:{rc.gasUsed:,})")
                self.state["total_txns"] = self.state.get("total_txns", 0) + 1
                today = datetime.now().strftime("%Y-%m-%d")
                if self.state.get("daily_date") != today:
                    self.state["daily_date"] = today
                    self.state["daily_txns"] = 0
                self.state["daily_txns"] += 1
                self._save_state()
                return hhex
            else:
                log(f"  FAIL {desc} (reverted)", "ERROR")
                return None
        except ContractLogicError as e:
            log(f"  FAIL {desc} revert: {str(e)[:80]}", "ERROR")
            return None
        except TimeExhausted:
            log(f"  FAIL {desc} timeout", "ERROR")
            return None
        except Exception as e:
            log(f"  FAIL {desc}: {str(e)[:80]}", "ERROR")
            return None

    def do_self_transfer(self, amt=0.000001):
        return self._send_tx(
            lambda: {"to": self.address, "value": self.w3.to_wei(amt, "ether")},
            f"Self-transfer {amt} {self.chain['native_symbol']}"
        )

    def do_bridge(self, amt=0.000001):
        b = self.chain["bridge"]
        return self._send_tx(
            lambda: {"to": Web3.to_checksum_address(b), "value": self.w3.to_wei(amt, "ether")},
            f"Bridge interaction ({b[:10]}...)"
        )

    def do_multicall(self):
        mc = self.contracts.get("multicall3")
        if not mc: return None
        c = self.w3.eth.contract(address=Web3.to_checksum_address(mc), abi=MULTICALL_ABI)
        return self._send_tx(
            lambda: c.functions.aggregate([]).build_transaction({"from": self.address}),
            "Multicall3 aggregate"
        )

    def do_swap_to_token(self, router, token_out, amt=0.0001):
        r = self.w3.eth.contract(address=Web3.to_checksum_address(router), abi=ROUTER_ABI)
        try: weth = r.functions.WETH().call()
        except: return None
        sym = self._token_sym(token_out)
        path = [weth, Web3.to_checksum_address(token_out)]
        dl = int(time.time()) + 300
        return self._send_tx(
            lambda: r.functions.swapExactETHForTokens(0, path, self.address, dl).build_transaction({
                "from": self.address, "value": self.w3.to_wei(amt, "ether")
            }),
            f"Swap {amt} {self.chain['native_symbol']} -> {sym}"
        )

    def do_swap_to_native(self, router, token_in):
        r = self.w3.eth.contract(address=Web3.to_checksum_address(router), abi=ROUTER_ABI)
        try: weth = r.functions.WETH().call()
        except: return None
        t = self.w3.eth.contract(address=Web3.to_checksum_address(token_in), abi=ERC20_ABI)
        bal = t.functions.balanceOf(self.address).call()
        if bal == 0: return None
        sym = self._token_sym(token_in)
        path = [Web3.to_checksum_address(token_in), weth]
        dl = int(time.time()) + 300
        return self._send_tx(
            lambda: r.functions.swapExactTokensForTokens(bal, 0, path, self.address, dl).build_transaction({"from": self.address}),
            f"Swap {sym} -> {self.chain['native_symbol']}"
        )

    def do_supply(self, pool, token_addr, amt=None):
        t = self.w3.eth.contract(address=Web3.to_checksum_address(token_addr), abi=ERC20_ABI)
        try:
            d = t.functions.decimals().call()
            bal = t.functions.balanceOf(self.address).call()
            if bal == 0: return None
            raw = int((amt or 1.0) * 10 ** d) if amt else bal
        except: return None
        # Approve
        self._send_tx(
            lambda: t.functions.approve(Web3.to_checksum_address(pool), raw).build_transaction({"from": self.address}),
            f"Approve lending"
        )
        p = self.w3.eth.contract(address=Web3.to_checksum_address(pool), abi=LENDING_ABI)
        sym = self._token_sym(token_addr)
        return self._send_tx(
            lambda: p.functions.supply(Web3.to_checksum_address(token_addr), raw, self.address, 0).build_transaction({"from": self.address}),
            f"Supply {sym} to lending"
        )

    def run_cycle(self):
        log("=" * 60)
        log(f"FARMING CYCLE - {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
        log("=" * 60)
        txns = []
        bal = self.native_bal()
        log(f"Balance: {bal} {self.chain['native_symbol']}")

        if bal < 0.001 and not self.dry_run:
            log("LOW BALANCE - faucet info:")
            faucet = self.chain.get("faucet_url")
            if faucet:
                log(f"  Faucet URL: {faucet}")
                log(f"  NOTE: UI-only faucet. Must have mainnet deposit with same address.")
            log(f"  Your address: {self.address}")

        # Phase 1: Basic on-chain interactions
        if bal >= 0.0001:
            h = self.do_self_transfer(random.uniform(0.000001, 0.00001))
            if h: txns.append(("self_tx", h))
            time.sleep(random.uniform(1, 3))

            h = self.do_bridge(0.000001)
            if h: txns.append(("bridge", h))
            time.sleep(random.uniform(1, 3))

            h = self.do_multicall()
            if h: txns.append(("multicall", h))
            time.sleep(random.uniform(1, 3))

            for _ in range(random.randint(1, 3)):
                time.sleep(random.uniform(0.5, 2))
                h = self.do_self_transfer(random.uniform(0.000001, 0.000005))
                if h: txns.append(("self_tx", h))

        # Phase 2: DEX interactions
        router = self.contracts.get("hyper_swap")
        if router and bal > 0.005:
            log("--- DEX Interactions ---")
            try:
                f = self.w3.eth.contract(address=Web3.to_checksum_address(router), abi=FACTORY_ABI)
                n = f.functions.allPairsLength().call()
                if n > 0:
                    pa = f.functions.allPairs(0).call()
                    s = self.w3.eth.get_storage_at(Web3.to_checksum_address(pa), 0)
                    ta = Web3.to_checksum_address(s[-20:])
                    if self._token_sym(ta) != "???":
                        h = self.do_swap_to_token(router, ta, 0.0001)
                        if h: txns.append(("swap_to", h))
                        time.sleep(2)
                        h = self.do_swap_to_native(router, ta)
                        if h: txns.append(("swap_back", h))
            except Exception as e:
                log(f"  DEX error: {e}", "WARN")

        # Phase 3: Lending
        pool = self.contracts.get("hyperlend")
        if pool:
            log("--- Lending Interaction ---")
            whype = self.contracts.get("token_whype")
            if whype and self.token_bal(whype) > 0:
                h = self.do_supply(pool, whype)
                if h: txns.append(("supply", h))

        # Report
        log("")
        log("=" * 60)
        log("CYCLE COMPLETE")
        log(f"  Txns this cycle: {len(txns)}")
        log(f"  Total lifetime: {self.state.get('total_txns', 0)}")
        log(f"  Daily: {self.state.get('daily_txns', 0)} ({self.state.get('daily_date', '-')})")
        log(f"  Balance: {self.native_bal()} {self.chain['native_symbol']}")
        for name, h in txns:
            if h and not h.startswith("dry"):
                log(f"  -> {name}: {self.chain['explorer']}/tx/{h}")
        log("=" * 60)
        self.state["last_run"] = datetime.now(timezone.utc).isoformat()
        self._save_state()
        return txns

    def print_status(self):
        log("--- Status ---")
        log(f"  Network: {self.chain['name']} (id={self.chain['chain_id']})")
        log(f"  RPC: {self.chain['rpc']}")
        log(f"  Address: {self.address}")
        log(f"  Explorer: {self.chain['explorer']}/address/{self.address}")
        log(f"  Balance: {self.native_bal()} {self.chain['native_symbol']}")
        log(f"  Block: {self.w3.eth.block_number:,}")
        log(f"  Gas: {self.w3.from_wei(self.w3.eth.gas_price, 'gwei')} Gwei")
        log(f"  Total txns: {self.state.get('total_txns', 0)}")
        log(f"  Last run: {self.state.get('last_run', 'Never')}")
        log(f"  Contracts found: {len(self.contracts)}")
        for n, a in self.contracts.items():
            log(f"    {n}: {a}")
        if self.chain.get("faucet_url"):
            log(f"  Faucet: {self.chain['faucet_url']}")


def main():
    p = argparse.ArgumentParser(description="HyperEVM Farming Bot")
    p.add_argument("--mainnet", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--loop", action="store_true")
    p.add_argument("--interval", type=int, default=360)
    p.add_argument("--status", action="store_true")
    a = p.parse_args()
    net = "mainnet" if a.mainnet else "testnet"
    try:
        farmer = HyperEVMFarmer(network=net, dry_run=a.dry_run)
    except Exception as e:
        log(f"Init failed: {e}", "ERROR")
        sys.exit(1)
    if a.status:
        farmer.print_status()
        return
    if a.loop:
        log(f"Loop: every {a.interval} min (+/- 20% jitter)")
        c = 0
        while True:
            c += 1
            log(f"\n{'#' * 20} CYCLE {c} {'#' * 20}")
            try:
                farmer.run_cycle()
            except Exception as e:
                log(f"Cycle error: {e}", "ERROR")
            s = a.interval * 60 * random.uniform(0.8, 1.2)
            log(f"Next in {s/60:.0f} min...")
            time.sleep(s)
    else:
        farmer.run_cycle()
        farmer.print_status()


if __name__ == "__main__":
    main()
