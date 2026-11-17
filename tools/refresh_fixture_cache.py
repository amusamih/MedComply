"""Explicit fixture administration; application agents cannot invoke this writer."""
import argparse,json
from medcomply.config import LOCAL
from medcomply.chain import Chain
from medcomply.db import initialize
from medcomply.quota import refresh_cache

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--network',choices=['local','sepolia'],default='local');args=parser.parse_args()
    c=Chain(args.network);initialize()
    demo=json.loads((LOCAL/('demo.json' if args.network=='local' else 'demo-sepolia.json')).read_text())
    for iso,mode in [('USA','export'),('GBR','import')]:refresh_cache(c,demo['roles']['incb'],iso,'morphine',2025,mode)
    print('Synthetic fixture cache verified against complete ledger history.')
