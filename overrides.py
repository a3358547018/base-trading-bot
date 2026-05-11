"""
overrides.py — 运行时参数覆盖层

TG Bot 面板修改参数（交易笔数/金额/gas/转账比例等），
实时写入 data/overrides.json。其他模块（scheduler/executor/gas_estimator）
通过 get_param 动态读取，无需重启进程即可生效。

使用：
    from overrides import get_param
    min_tx = get_param("MIN_TX_PER_WALLET_DAY")
"""

import json
import logging
from pathlib import Path
from threading import Lock

import config

logger = logging.getLogger(__name__)

_OVERRIDE_FILE = Path("data/overrides.json")
_LOCK = Lock()


# 可被覆盖的参数白名单
ALLOWED_KEYS = {
    # 交易笔数
    "MIN_TX_PER_WALLET_DAY",      # 每钱包最少 swap 笔数
    "MAX_TX_PER_WALLET_DAY",      # 每钱包最多 swap 笔数
    "TRANSFERS_PER_WALLET_DAY",   # 每钱包每日转账次数
    "CLAIMS_PER_WALLET_DAY",      # 每钱包每日领 V3 费次数

    # 金额
    "TX_AMOUNT_MIN_ETH",
    "TX_AMOUNT_MAX_ETH",
    "TRANSFER_PCT_MIN",           # 转账最低比例
    "TRANSFER_PCT_MAX",           # 转账最高比例（与 MIN 随机取）
    "TRANSFER_PCT",               # 兼容旧值（固定比例，已改为 MIN==MAX 时用）

    # Gas
    "GAS_PRICE_DEFAULT_GWEI",
    "GAS_LIMIT_MAX",
    "MAX_GAS_COST_USD",

    # 代币池
    "TOKEN_FETCH_COUNT",

    # 🎲 随机性调节
    "MIN_GAP_SECONDS",            # 时间表相邻事件最小间隔
    "BUY_PROBABILITY",            # 有持仓时买入概率（0~1，另一半为卖）
    "SELL_RATIO_MIN",             # 每次卖出持仓的最小比例
    "SELL_RATIO_MAX",             # 每次卖出持仓的最大比例
    "AMOUNT_JITTER_PCT",          # 金额抖动百分比（0 = 严格区间，0.2 = ±20% 扰动）
}


def _load_all() -> dict:
    if _OVERRIDE_FILE.exists():
        try:
            with open(_OVERRIDE_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_all(data: dict) -> None:
    _OVERRIDE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _OVERRIDE_FILE.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    tmp.replace(_OVERRIDE_FILE)


def get_param(key: str):
    """返回参数值（优先覆盖层，fallback config.py）。"""
    if key not in ALLOWED_KEYS:
        return getattr(config, key, None)
    with _LOCK:
        data = _load_all()
    if key in data:
        return data[key]
    return getattr(config, key, None)


def get_all_params() -> dict:
    return {k: get_param(k) for k in sorted(ALLOWED_KEYS)}


def set_param(key: str, value) -> bool:
    if key not in ALLOWED_KEYS:
        logger.warning(f"[Override] 不允许修改 {key}")
        return False
    with _LOCK:
        data = _load_all()
        data[key] = value
        _save_all(data)
    logger.info(f"[Override] {key} → {value}")
    return True


def reset_param(key: str) -> bool:
    with _LOCK:
        data = _load_all()
        if key in data:
            del data[key]
            _save_all(data)
            logger.info(f"[Override] 重置 {key}")
            return True
    return False


def reset_all() -> None:
    with _LOCK:
        _save_all({})
    logger.info("[Override] 重置全部参数")