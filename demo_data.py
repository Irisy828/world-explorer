"""Synthetic fixture only. Does not call YouTube."""
def fetch(params, key):
    region = params['regionCode']
    return {'items': [
        {'id': 'DEMO0000001', 'status': {'privacyStatus': 'public'}, 'snippet': {'title': '城市漫游 / City walk [模拟]', 'channelTitle': 'World Guide Demo', 'publishedAt': '2026-10-20T00:00:00Z'}, 'statistics': {'viewCount': '12000'}},
        {'id': 'DEMO0000002', 'status': {'privacyStatus': 'public'}, 'snippet': {'title': '=海岸风景 | Coast <模拟>', 'channelTitle': 'Demo Channel', 'publishedAt': '2026-10-19T00:00:00Z'}, 'statistics': {}}
    ]}
