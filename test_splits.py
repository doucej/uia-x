#!/usr/bin/env python
"""Test split functionality."""
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
import asyncio
import json


async def test():
    async with streamablehttp_client('http://localhost:8000/mcp') as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            
            # Attach to Quicken
            await s.call_tool('select_window', {'process_name': 'qw'})
            print('[OK] Selected Quicken window')
            
            # Get list of accounts
            r = await s.call_tool('list_sidebar_accounts', {})
            accts = json.loads(r.content[0].text).get('accounts', [])
            banking_accts = [a['name'] for a in accts if a.get('section') == 'Banking'][:3]
            print(f'Banking accounts: {banking_accts}')
            
            # Try first banking account or DCU Checking
            target_acct = banking_accts[0] if banking_accts else 'DCU Checking'
            r = await s.call_tool('navigate_to_account', {'account_name': target_acct})
            nav = json.loads(r.content[0].text)
            print(f"[OK] Navigated to {target_acct}: {nav.get('method')}")
            
            # Read first 3 transactions
            r = await s.call_tool('read_register_rows', {'max_rows': 3})
            rows = json.loads(r.content[0].text).get('rows', [])
            print(f'[INFO] Found {len(rows)} transactions in {target_acct}:')
            for i, row in enumerate(rows):
                print(f"  {i}: {row.get('date')} | {row.get('payee')} | {row.get('category')}")
            if rows:
                # Try to read splits for first transaction
                print(f'[INFO] Attempting to read splits for transaction 0...')
                r = await s.call_tool('read_transaction_splits', {'row_index': 0})
                splits_result = json.loads(r.content[0].text)
                print(f"[RESULT] ok: {splits_result.get('ok')}")
                if splits_result.get('ok'):
                    print(f"[RESULT] splits found: {splits_result.get('count')}")
                    # Print full split data
                    import json as json_lib
                    print("[DEBUG] Full splits response:")
                    print(json_lib.dumps(splits_result.get('splits', []), indent=2))
                    for sp in splits_result.get('splits', []):
                        print(f"  - {sp.get('category')} | {sp.get('amount')}")
                    
                    # Close the split dialog
                    print("[INFO] Closing split dialog...")
                    r2 = await s.call_tool('close_split_dialog', {'save': False})
                    close_result = json.loads(r2.content[0].text)
                    print(f"[RESULT] close ok: {close_result.get('ok')}")
                else:
                    print(f"[ERROR] {splits_result.get('error')}")


if __name__ == '__main__':
    asyncio.run(test())
