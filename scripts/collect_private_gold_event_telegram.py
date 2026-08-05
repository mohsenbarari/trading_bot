#!/usr/bin/env python3
"""One-shot collector for the two configured private-gold event channels."""
from __future__ import annotations
import argparse, asyncio, json, sys
from pathlib import Path
from typing import Sequence
from core.market_intelligence.private_gold_telegram import PrivateGoldEventChannels, PrivateGoldTelegramSettings, PrivateGoldTelegramTransportError, collect_private_gold_event_telegram
from core.market_intelligence.public_telegram.transport import PublicTelegramCredentials

def _emit(**payload: object) -> None: print(json.dumps(payload, sort_keys=True, separators=(",", ":")), flush=True)
def _root(value: str) -> Path:
    root=Path(value).expanduser().resolve()
    if not root.is_dir(): raise ValueError("runtime_root_unavailable")
    return root
def _path(root: Path, value: str, name: str) -> Path:
    raw=Path(value).expanduser(); path=raw.resolve() if raw.is_absolute() else (root/raw).resolve()
    if root not in path.parents: raise ValueError(f"{name}_outside_runtime_root")
    return path
def main(argv: Sequence[str] | None = None) -> int:
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("--runtime-root",required=True); p.add_argument("--market-store",required=True); p.add_argument("--staging-store",required=True); p.add_argument("--session",required=True); p.add_argument("--days",type=int,default=1); p.add_argument("--replay-window",action="store_true"); p.add_argument("--bootstrap-session",action="store_true"); a=p.parse_args(argv)
    try:
        root=_root(a.runtime_root)
        if a.bootstrap_session and not sys.stdin.isatty(): raise ValueError("interactive_session_requires_tty")
        result=asyncio.run(collect_private_gold_event_telegram(PrivateGoldTelegramSettings(PublicTelegramCredentials.from_environment(),PrivateGoldEventChannels.from_environment(),_path(root,a.staging_store,"staging_store"),_path(root,a.market_store,"market_store"),_path(root,a.session,"telegram_session"),bool(a.bootstrap_session)),days=a.days,resume_from_checkpoint=not a.replay_window))
        _emit(command="collect",status="COLLECTED",**result); return 0
    except (ValueError,PrivateGoldTelegramTransportError) as exc: _emit(command="collect",status="FAILED",reason=str(exc)); return 2
if __name__ == "__main__": raise SystemExit(main())
