USE dify_app;

SELECT player_id, nickname, coins, debt, grudge, reputation, visits, last_session_id, last_seen
FROM players;

SELECT id, name, price, stock FROM drinks ORDER BY id;

SELECT id, player_id, session_id, event_type, detail, created_at
FROM events ORDER BY id DESC LIMIT 15;

SELECT id, player_id, description, grudge_delta, created_at
FROM pranks ORDER BY id DESC LIMIT 10;

SELECT id, title, heat
FROM rumors ORDER BY heat DESC LIMIT 10;

SELECT id, title, MATCH(title, content, tags) AGAINST('后巷 歌声') AS score
FROM rumors
WHERE MATCH(title, content, tags) AGAINST('后巷 歌声')
ORDER BY score DESC;
