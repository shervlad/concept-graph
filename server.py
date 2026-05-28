import os
import json
import asyncio
import logging
from pathlib import Path

from aiohttp import web

from db import GraphDB
from generator import expansion_loop, name_clusters, BACKENDS

PROJECT_DIR = Path(__file__).parent
log = logging.getLogger(__name__)


@web.middleware
async def cors_middleware(request, handler):
    resp = await handler(request)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp


def broadcast(app: web.Application, event: str, data: dict):
    msg = f"event: {event}\ndata: {json.dumps(data)}\n\n"
    dead = []
    for q in app["sse_clients"]:
        try:
            q.put_nowait(msg)
        except asyncio.QueueFull:
            dead.append(q)
    for q in dead:
        app["sse_clients"].remove(q)


def make_sse_callback(app: web.Application):
    async def sse_callback(event: str, data: dict):
        if event == "expand" and "focal" in data:
            broadcast(app, "status", {"message": f"Expanding around '{data['focal']}'..."})
        broadcast(app, event, data)
    return sse_callback


async def handle_index(request):
    return web.FileResponse(PROJECT_DIR / "index.html")


async def handle_static(request):
    filename = request.match_info["path"]
    allowed = {".html", ".js", ".css", ".json"}
    filepath = (PROJECT_DIR / filename).resolve()
    if not filepath.is_relative_to(PROJECT_DIR.resolve()):
        raise web.HTTPNotFound()
    if filepath.suffix not in allowed or not filepath.is_file():
        raise web.HTTPNotFound()
    return web.FileResponse(filepath)


async def handle_graph(request):
    db = request.app["db"]
    try:
        limit = int(request.query.get("limit", 500))
    except ValueError:
        return web.json_response({"error": "Invalid 'limit' parameter"}, status=400)
    center = request.query.get("center")
    data = db.get_graph(limit=limit, center_id=center)
    return web.json_response(data)


async def handle_stats(request):
    db = request.app["db"]
    return web.json_response(db.get_stats())


async def handle_backends(request):
    result = {}
    for name, info in BACKENDS.items():
        result[name] = {
            "model": info["model"],
            "configured": bool(os.environ.get(info["env"])),
        }
    return web.json_response(result)


async def handle_expand(request):
    app = request.app
    body = await request.json()
    seed = body.get("seed", "").strip()
    if not seed:
        return web.json_response({"error": "Missing 'seed' parameter"}, status=400)

    backend = body.get("backend", "openai")
    if backend not in BACKENDS:
        return web.json_response({"error": f"Unknown backend: {backend}"}, status=400)

    env_var = BACKENDS[backend]["env"]
    if not os.environ.get(env_var):
        return web.json_response({"error": f"Missing {env_var} environment variable"}, status=400)

    task = app["expansion_task"]
    if task is not None and not task.done():
        return web.json_response({"error": "Expansion already running. Stop it first."}, status=409)

    rounds = body.get("rounds", 0)
    db = app["db"]
    callback = make_sse_callback(app)

    async def run():
        try:
            await expansion_loop(seed, db, backend=backend, callback=callback, max_rounds=rounds)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception("Expansion failed")
            broadcast(app, "error", {"message": f"Expansion failed: {e}", "fatal": True})
            broadcast(app, "done", {"stats": db.get_stats()})

    task = asyncio.create_task(run())
    task.add_done_callback(lambda _: app.__setitem__("expansion_task", None))
    app["expansion_task"] = task

    return web.json_response({"status": "started", "seed": seed, "backend": backend})


async def handle_stop(request):
    app = request.app
    task = app["expansion_task"]
    if task is not None and not task.done():
        task.cancel()
        return web.json_response({"status": "stopping"})
    return web.json_response({"status": "not running"})


async def handle_cluster_names(request):
    body = await request.json()
    clusters = body.get("clusters", [])
    backend = body.get("backend", "openai")
    try:
        names = await name_clusters(clusters, backend=backend)
        return web.json_response({"names": names})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def handle_import(request):
    app = request.app
    db = app["db"]
    data = await request.json()
    db.import_json(data)
    stats = db.get_stats()
    broadcast(app, "stats", stats)
    return web.json_response({"status": "imported", **stats})


async def handle_stream(request):
    app = request.app
    resp = web.StreamResponse(
        status=200,
        reason="OK",
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
    await resp.prepare(request)

    queue = asyncio.Queue(maxsize=64)
    app["sse_clients"].append(queue)

    stats = app["db"].get_stats()
    await resp.write(f"event: stats\ndata: {json.dumps(stats)}\n\n".encode())

    try:
        while True:
            msg = await queue.get()
            await resp.write(msg.encode())
    except (asyncio.CancelledError, ConnectionResetError):
        pass
    finally:
        if queue in app["sse_clients"]:
            app["sse_clients"].remove(queue)

    return resp


async def on_cleanup(app):
    task = app.get("expansion_task")
    if task and not task.done():
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
    app["db"].close()


def create_app(db_path: str = None) -> web.Application:
    if db_path is None:
        db_path = os.environ.get("KNOWLEDGE_DB", "knowledge.db")
    database = GraphDB(db_path)
    app = web.Application(middlewares=[cors_middleware])
    app["db"] = database
    app["sse_clients"] = []
    app["expansion_task"] = None

    app.router.add_get("/", handle_index)
    app.router.add_get("/api/graph", handle_graph)
    app.router.add_get("/api/stats", handle_stats)
    app.router.add_get("/api/backends", handle_backends)
    app.router.add_post("/api/expand", handle_expand)
    app.router.add_post("/api/stop", handle_stop)
    app.router.add_post("/api/cluster-names", handle_cluster_names)
    app.router.add_post("/api/import", handle_import)
    app.router.add_get("/api/stream", handle_stream)
    app.router.add_get("/{path:.+}", handle_static)

    app.on_cleanup.append(on_cleanup)

    return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    port = int(os.environ.get("PORT", 8080))
    db_path = os.environ.get("KNOWLEDGE_DB", "knowledge.db")
    app = create_app(db_path)
    stats = app["db"].get_stats()
    log.info("Knowledge Tree Server")
    log.info("  Port: %s", port)
    log.info("  Database: %s", db_path)
    log.info("  Graph: %d nodes, %d edges, %d domains", stats["nodes"], stats["edges"], stats["domains"])
    for name, info in BACKENDS.items():
        configured = "yes" if os.environ.get(info["env"]) else "no"
        log.info("  Backend %s (%s): %s", name, info["model"], configured)
    web.run_app(app, port=port)
