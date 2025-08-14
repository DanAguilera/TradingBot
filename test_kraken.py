from kraken_client import get_balance, get_ticker_price

print("Testing Kraken API connection...")
print("Balances:", get_balance("ETH"))  # for Ethereum       
print("USDT:", get_balance("USDT"))           # optional
print("ETHUSD last:", get_ticker_price("ETHUSD"))
print("BTCUSD last:", get_ticker_price("BTCUSD"))  # optional
