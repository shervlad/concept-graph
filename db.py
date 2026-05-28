import sqlite3
import json
import hashlib
import threading
from collections import defaultdict, deque
from functools import lru_cache


@lru_cache(maxsize=256)
def _domain_color(key: str) -> str:
    h = int(hashlib.md5(key.encode()).hexdigest()[:8], 16) % 360
    s, l = 0.65, 0.60
    c = (1 - abs(2 * l - 1)) * s
    x = c * (1 - abs((h / 60) % 2 - 1))
    m = l - c / 2
    if h < 60:    r, g, b = c, x, 0
    elif h < 120: r, g, b = x, c, 0
    elif h < 180: r, g, b = 0, c, x
    elif h < 240: r, g, b = 0, x, c
    elif h < 300: r, g, b = x, 0, c
    else:         r, g, b = c, 0, x
    return "#{:02x}{:02x}{:02x}".format(round((r+m)*255), round((g+m)*255), round((b+m)*255))


class GraphDB:
    def __init__(self, path="knowledge.db"):
        self._path = path
        self._lock = threading.Lock()
        self._local = threading.local()
        self._read_conns = []
        self._read_conns_lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.row_factory = sqlite3.Row
        self._known_domains = set()
        self._init_tables()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    def _get_read_conn(self):
        conn = getattr(self._local, 'conn', None)
        if conn is None:
            conn = sqlite3.connect(self._path, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
            with self._read_conns_lock:
                self._read_conns.append(conn)
        return conn

    def _init_tables(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS nodes (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                year INTEGER,
                domains TEXT DEFAULT '[]',
                desc TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS edges (
                source TEXT NOT NULL,
                target TEXT NOT NULL,
                weight REAL DEFAULT 0.5,
                PRIMARY KEY (source, target),
                FOREIGN KEY (source) REFERENCES nodes(id),
                FOREIGN KEY (target) REFERENCES nodes(id)
            );
            CREATE TABLE IF NOT EXISTS domains (
                key TEXT PRIMARY KEY,
                label TEXT NOT NULL,
                color TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source);
            CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target);
        """)
        for row in self._conn.execute("SELECT key FROM domains").fetchall():
            self._known_domains.add(row[0])

    def add_node(self, node: dict) -> bool:
        with self._lock:
            domains = node.get("domains", [])
            if isinstance(domains, str):
                domains = [domains]
            for d in domains:
                self._ensure_domain(d)
            cursor = self._conn.execute(
                "INSERT OR IGNORE INTO nodes (id, name, year, domains, desc) VALUES (?, ?, ?, ?, ?)",
                (node["id"], node["name"], node.get("year"), json.dumps(domains), node.get("desc", ""))
            )
            self._conn.commit()
            return cursor.rowcount > 0

    def add_edge(self, edge: dict) -> bool:
        with self._lock:
            count = self._conn.execute(
                "SELECT COUNT(*) FROM nodes WHERE id IN (?, ?)",
                (edge["source"], edge["target"])
            ).fetchone()[0]
            if count < 2:
                return False
            weight = min(1.0, max(0.0, edge.get("weight", 0.5)))
            cursor = self._conn.execute(
                "INSERT OR IGNORE INTO edges (source, target, weight) VALUES (?, ?, ?)",
                (edge["source"], edge["target"], weight)
            )
            self._conn.commit()
            return cursor.rowcount > 0

    def _ensure_domain(self, key: str):
        if key in self._known_domains:
            return
        label = key.replace("_", " ").title()
        color = _domain_color(key)
        self._conn.execute(
            "INSERT OR IGNORE INTO domains (key, label, color) VALUES (?, ?, ?)",
            (key, label, color)
        )
        self._known_domains.add(key)

    def get_graph(self, limit: int = 500, center_id: str = None) -> dict:
        conn = self._get_read_conn()
        if center_id:
            node_rows = self._bfs_subgraph(center_id, limit, conn)
        else:
            node_rows = conn.execute("""
                SELECT n.*, COALESCE(ec.cnt, 0) as conn_count
                FROM nodes n
                LEFT JOIN (
                    SELECT id, COUNT(*) as cnt FROM (
                        SELECT source as id FROM edges
                        UNION ALL
                        SELECT target as id FROM edges
                    ) GROUP BY id
                ) ec ON ec.id = n.id
                ORDER BY conn_count DESC
                LIMIT ?
            """, (limit,)).fetchall()

        node_ids = {r["id"] for r in node_rows}

        if node_ids:
            ph = ",".join("?" for _ in node_ids)
            ids_list = list(node_ids)
            edges = conn.execute(
                "SELECT source, target, weight FROM edges WHERE source IN ({}) AND target IN ({})".format(ph, ph),
                ids_list + ids_list
            ).fetchall()
        else:
            edges = []

        domains = {r["key"]: {"color": r["color"], "label": r["label"]}
                   for r in conn.execute("SELECT * FROM domains").fetchall()}

        counts = conn.execute(
            "SELECT (SELECT COUNT(*) FROM nodes) as n, (SELECT COUNT(*) FROM edges) as e"
        ).fetchone()

        return {
            "domains": domains,
            "nodes": [
                {"id": r["id"], "name": r["name"], "year": r["year"],
                 "domains": json.loads(r["domains"]), "desc": r["desc"]}
                for r in node_rows
            ],
            "links": [
                {"source": r["source"], "target": r["target"], "weight": r["weight"]}
                for r in edges
            ],
            "total_nodes": counts[0],
            "total_edges": counts[1],
        }

    def _bfs_subgraph(self, center_id: str, limit: int, conn):
        adj = defaultdict(set)
        frontier = deque([center_id])
        visited = set()
        result_ids = []

        while frontier and len(result_ids) < limit:
            nid = frontier.popleft()
            if nid in visited:
                continue
            visited.add(nid)

            row = conn.execute("SELECT 1 FROM nodes WHERE id=?", (nid,)).fetchone()
            if not row:
                continue
            result_ids.append(nid)

            if nid not in adj:
                for r in conn.execute(
                    "SELECT source, target FROM edges WHERE source=? OR target=?", (nid, nid)
                ).fetchall():
                    adj[r[0]].add(r[1])
                    adj[r[1]].add(r[0])

            for neighbor in adj.get(nid, ()):
                if neighbor not in visited:
                    frontier.append(neighbor)

        if not result_ids:
            return []
        ph = ",".join("?" for _ in result_ids)
        return conn.execute(
            "SELECT * FROM nodes WHERE id IN ({})".format(ph), result_ids
        ).fetchall()

    def get_node_ids(self) -> set:
        conn = self._get_read_conn()
        return {r[0] for r in conn.execute("SELECT id FROM nodes").fetchall()}

    def get_node_names_by_ids(self, ids: list) -> dict:
        if not ids:
            return {}
        conn = self._get_read_conn()
        ph = ",".join("?" for _ in ids)
        rows = conn.execute(
            "SELECT id, name FROM nodes WHERE id IN ({})".format(ph), ids
        ).fetchall()
        return {r["id"]: r["name"] for r in rows}

    def get_least_expanded(self, expanded_set: set, n: int = 5) -> list:
        conn = self._get_read_conn()
        if expanded_set:
            ph = ",".join("?" for _ in expanded_set)
            rows = conn.execute("""
                SELECT n.id, COUNT(e.target) as out_edges
                FROM nodes n
                LEFT JOIN edges e ON e.source = n.id
                WHERE n.id NOT IN ({})
                GROUP BY n.id
                ORDER BY out_edges ASC
                LIMIT ?
            """.format(ph), list(expanded_set) + [n]).fetchall()
        else:
            rows = conn.execute("""
                SELECT n.id, COUNT(e.target) as out_edges
                FROM nodes n
                LEFT JOIN edges e ON e.source = n.id
                GROUP BY n.id
                ORDER BY out_edges ASC
                LIMIT ?
            """, (n,)).fetchall()
        return [r["id"] for r in rows]

    def get_stats(self) -> dict:
        conn = self._get_read_conn()
        row = conn.execute("""
            SELECT (SELECT COUNT(*) FROM nodes) as n,
                   (SELECT COUNT(*) FROM edges) as e,
                   (SELECT COUNT(*) FROM domains) as d
        """).fetchone()
        return {"nodes": row[0], "edges": row[1], "domains": row[2]}

    def import_json(self, data: dict):
        with self._lock:
            explicit_domains = data.get("domains", {})
            if explicit_domains:
                self._conn.executemany(
                    "INSERT OR REPLACE INTO domains (key, label, color) VALUES (?, ?, ?)",
                    ((k, v["label"], v["color"]) for k, v in explicit_domains.items())
                )
                self._known_domains.update(explicit_domains)

            node_rows = []
            for node in data.get("nodes", []):
                if "domains" in node:
                    domains = node["domains"]
                elif "domain" in node:
                    domains = [node["domain"]]
                else:
                    domains = []
                if isinstance(domains, str):
                    domains = [domains]
                for d in domains:
                    if d not in explicit_domains:
                        self._ensure_domain(d)
                node_rows.append((
                    node["id"], node["name"], node.get("year"),
                    json.dumps(domains), node.get("desc", "")
                ))

            if node_rows:
                self._conn.executemany(
                    "INSERT OR IGNORE INTO nodes (id, name, year, domains, desc) VALUES (?, ?, ?, ?, ?)",
                    node_rows
                )

            existing_ids = {r[0] for r in self._conn.execute("SELECT id FROM nodes").fetchall()}
            links = data.get("links", [])
            if links:
                self._conn.executemany(
                    "INSERT OR IGNORE INTO edges (source, target, weight) VALUES (?, ?, ?)",
                    (
                        (link["source"], link["target"], min(1.0, max(0.0, link.get("weight", 0.5))))
                        for link in links
                        if link["source"] in existing_ids and link["target"] in existing_ids
                    )
                )
            self._conn.commit()

    def close(self):
        self._conn.close()
        with self._read_conns_lock:
            for conn in self._read_conns:
                try:
                    conn.close()
                except Exception:
                    pass
            self._read_conns.clear()
