from __future__ import annotations

import asyncio
import logging
import os
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    command = sys.argv[1] if len(sys.argv) > 1 else "api"
    if command == "api":
        import uvicorn
        import argparse
        parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8000)
        uvicorn.run("app.api:app", host="127.0.0.1", port=parser.parse_args(sys.argv[2:]).port, log_level="info")
    elif command == "mcp":
        from .mcp_server import serve
        import argparse
        parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8001)
        serve(parser.parse_args(sys.argv[2:]).port)
    elif command == "worker":
        from .worker import work
        import argparse
        parser=argparse.ArgumentParser();parser.add_argument('role',choices=['message','run']);parser.add_argument('--queue');parser.add_argument('--once',action='store_true')
        args=parser.parse_args(sys.argv[2:])
        asyncio.run(work(args.role,queue=args.queue,once=args.once))
    elif command == 'backfill-ledger':
        from .ledger import backfill
        print('Backfilled ledger entries:',backfill())
    elif command == "initdb":
        from alembic import command as migration
        from alembic.config import Config
        from sqlalchemy import text
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from .config import ROOT
        from .store import engine
        from .worker import checkpoint_url
        migration.upgrade(Config(str(ROOT / "alembic.ini")), "head")
        with engine.begin() as connection:
            connection.execute(text("CREATE SCHEMA IF NOT EXISTS studio_checkpoints"))
        async def setup():
            async with AsyncPostgresSaver.from_conn_string(checkpoint_url()) as saver:
                await saver.setup()
        asyncio.run(setup())
        print("Database and PostgreSQL graph checkpoints initialized.")
    else:
        raise SystemExit("Usage: python -m app.cli api|mcp|worker [message|run]|initdb")


if __name__ == "__main__":
    main()
