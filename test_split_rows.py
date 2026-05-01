#!/usr/bin/env python
"""Test multiple row indices to find one with working split dialog."""
import asyncio
import json
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def test_multiple_rows():
    async with streamablehttp_client('http://localhost:8000/mcp') as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            await s.call_tool('select_window', {'process_name': 'qw'})
            await s.call_tool('navigate_to_account', {'account_name': 'DCU Checking'})
            
            # Test multiple row indices
            for row_idx in range(5):
                print(f'\n=== Testing row {row_idx} ===')
                r = await s.call_tool('read_transaction_splits', {'row_index': row_idx})
                result = json.loads(r.content[0].text)
                if result.get('ok'):
                    print(f'SUCCESS! Row {row_idx} has splits')
                    print(f'  Count: {result.get("count")}')
                    print(f'  Kind: {result.get("kind")}')
                    # Close dialog
                    await s.call_tool('close_split_dialog', {'save': False})
                else:
                    error = result.get('error', 'unknown')
                    if 'timeout' in error.lower():
                        print(f'TIMEOUT: Split dialog did not open')
                    else:
                        print(f'ERROR: {error[:50]}...')

if __name__ == '__main__':
    asyncio.run(test_multiple_rows())
