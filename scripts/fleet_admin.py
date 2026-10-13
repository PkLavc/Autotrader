"""Administer the private fleet API without persisting credentials locally."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen


DEFAULT_API = "https://api.pklavc.com/autotrader/v1"


def call(method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
    token = os.getenv("AUTOTRADER_FLEET_ADMIN_TOKEN", "").strip()
    if not token:
        raise RuntimeError("AUTOTRADER_FLEET_ADMIN_TOKEN is required in the current process")
    base = os.getenv("AUTOTRADER_FLEET_API", DEFAULT_API).rstrip("/")
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(
        base + path,
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "PkLavc-Autotrader-Admin/1",
        },
    )
    try:
        with urlopen(request, timeout=45) as response:
            return json.loads(response.read())
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1000]
        raise RuntimeError(f"fleet API returned HTTP {exc.code}: {detail}") from exc


def create(args: argparse.Namespace) -> Any:
    auth_type = args.auth_type
    if auth_type == "api_key":
        credentials = {
            "key_id": os.getenv("ALPACA_API_KEY", "").strip(),
            "secret_key": os.getenv("ALPACA_API_SECRET", "").strip(),
        }
        if not all(credentials.values()):
            raise RuntimeError("ALPACA_API_KEY and ALPACA_API_SECRET are required")
    else:
        credentials = {"access_token": os.getenv("ALPACA_OAUTH_TOKEN", "").strip()}
        if not credentials["access_token"]:
            raise RuntimeError("ALPACA_OAUTH_TOKEN is required")
    return call("POST", "/admin/accounts", {
        "display_name": args.name,
        "broker_mode": "paper",
        "auth_type": auth_type,
        "tax_profile": args.tax_profile,
        "credentials": credentials,
        "limits": {
            "max_order_notional": args.max_order,
            "max_total_notional": args.max_run,
            "max_target_weight": args.max_weight,
            "max_gross_target": args.max_gross,
            "rebalance_threshold": args.threshold,
        },
    })


def update(args: argparse.Namespace) -> Any:
    payload: dict[str, Any] = {}
    if args.name is not None:
        payload["display_name"] = args.name
    if args.active is not None:
        payload["active"] = args.active == "true"
    if args.execution is not None:
        payload["execution_enabled"] = args.execution == "true"
    return call("PATCH", f"/admin/accounts/{args.account_id}", payload)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Manage Autotrader fleet accounts securely")
    commands = root.add_subparsers(dest="command", required=True)

    commands.add_parser("list", help="List registered accounts")
    add = commands.add_parser("create", help="Validate and register an Alpaca paper account")
    add.add_argument("--name", required=True)
    add.add_argument("--auth-type", choices=("api_key", "oauth"), default="api_key")
    add.add_argument("--tax-profile", choices=("us_personal_no_local_tax", "brazil_foreign_investments_estimate"), default="us_personal_no_local_tax")
    add.add_argument("--max-order", type=float, default=100)
    add.add_argument("--max-run", type=float, default=500)
    add.add_argument("--max-weight", type=float, default=0.18)
    add.add_argument("--max-gross", type=float, default=0.90)
    add.add_argument("--threshold", type=float, default=0.03)

    invite = commands.add_parser("invite", help="Create a one-time private dashboard link")
    invite.add_argument("account_id")
    invite.add_argument("--valid-minutes", type=int, default=60)

    sync = commands.add_parser("sync", help="Refresh one account snapshot")
    sync.add_argument("account_id")

    revoke = commands.add_parser("revoke-access", help="Revoke all sessions and unused invitations")
    revoke.add_argument("account_id")

    change = commands.add_parser("update", help="Rename, enable, or disable an account")
    change.add_argument("account_id")
    change.add_argument("--name")
    change.add_argument("--active", choices=("true", "false"))
    change.add_argument("--execution", choices=("true", "false"))
    return root


def main() -> int:
    args = parser().parse_args()
    if args.command == "list":
        result = call("GET", "/admin/accounts")
    elif args.command == "create":
        result = create(args)
    elif args.command == "invite":
        result = call("POST", f"/admin/accounts/{args.account_id}/invite", {"valid_minutes": args.valid_minutes})
    elif args.command == "sync":
        result = call("POST", f"/admin/accounts/{args.account_id}/sync", {})
    elif args.command == "revoke-access":
        result = call("POST", f"/admin/accounts/{args.account_id}/revoke-access", {})
    else:
        result = update(args)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2) from error
