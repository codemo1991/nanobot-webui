import akshare as ak

try:
    symbol = '600905'
    df = ak.stock_zh_a_hist(symbol=symbol, period='daily', start_date='20250101', end_date='20260227')
    df['EMA10'] = df['收盘'].ewm(span=10, adjust=False).mean()
    
    latest = df.iloc[-1]
    print(f'股票代码: {symbol}')
    print(f'股票名称: 厦门港务')
    print(f'最新日期: {latest["日期"]}')
    print(f'收盘价: {latest["收盘"]}')
    print(f'涨跌幅: {latest["涨跌幅"]}%')
    print(f'成交量: {latest["成交量"]}')
    print(f'EMA10: {latest["EMA10"]:.2f}')
    print()
    print('最近10个交易日:')
    print(df[['日期','收盘','涨跌幅','EMA10']].tail(10))
except Exception as e:
    print(f'Error: {e}')
    import traceback
    traceback.print_exc()
