"""Bounded discovery from cited research; social leads never bypass market checks."""
import re
from market import age


def candidates(reports, watchlist, at, limit=6):
    grouped = {}
    for report in reports:
        if report.get('status') != 'complete' or not -5 <= age(report.get('at'), at) <= 1200:
            continue
        for item in report.get('evidence', []):
            symbol = item.get('symbol', '')
            if not re.fullmatch(r'[A-Z]{1,5}', symbol) or symbol == 'MARKET' or symbol in watchlist:
                continue
            if not item.get('fresh') or not -60 <= age(item.get('published_at'), at) <= 86400:
                continue
            group = grouped.setdefault(symbol, {'symbol': symbol, 'sources': [], 'at': report['at']})
            if not any(e['url'] == item['url'] for e in group['sources']):
                group['sources'].append(item)
    rows = []
    for row in grouped.values():
        corroborated = any(e['lane'] != 'Social sentiment' for e in row['sources'])
        row.update(corroborated=corroborated, reason=row['sources'][0]['summary'],
                   status='Awaiting market checks' if corroborated else 'Social lead — needs corroboration')
        rows.append(row)
    return sorted(rows, key=lambda r: (-int(r['corroborated']), -len(r['sources']), r['symbol']))[:limit]
