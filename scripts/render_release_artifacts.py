#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ipaddress
import json
from pathlib import Path
from urllib.parse import urlparse

try:
    from deploy_config import parse_env_file
except ModuleNotFoundError:  # pragma: no cover - used when imported as scripts.render_release_artifacts
    from scripts.deploy_config import parse_env_file


REQUIRED_KEYS = (
    "FOREIGN_PUBLIC_IP",
    "FOREIGN_PUBLIC_DOMAIN",
    "IRAN_PUBLIC_IP",
    "IRAN_PUBLIC_DOMAIN",
    "IRAN_APP_DOMAIN",
    "IRAN_PROJECT_DIR",
    "IRAN_CERTBOT_EMAIL",
)


def host_from_url(raw_value: str) -> str:
    candidate = raw_value if "://" in raw_value else f"https://{raw_value}"
    parsed = urlparse(candidate)
    return (parsed.hostname or "").strip().lower()


def require_domain(value: str, key: str) -> None:
    if not value or "://" in value or "/" in value:
        raise ValueError(f"{key} must be a bare host/domain, got: {value!r}")


def require_ip(value: str, key: str) -> None:
    try:
        ipaddress.ip_address(value)
    except ValueError as exc:
        raise ValueError(f"{key} must be a valid IP address, got: {value!r}") from exc


def require_url_host_matches(url: str, domain: str, url_key: str, domain_key: str) -> None:
    url_host = host_from_url(url)
    if url_host != domain.strip().lower():
        raise ValueError(f"{url_key} host {url_host!r} must match {domain_key} {domain!r}")


def derive_release_values(manifest_path: str) -> dict[str, str]:
    values = parse_env_file(Path(manifest_path))

    missing = [key for key in REQUIRED_KEYS if not values.get(key)]
    if missing:
        raise ValueError(f"Missing required release artifact inputs: {', '.join(missing)}")

    derived = dict(values)
    derived["FOREIGN_SERVER_URL"] = derived.get("FOREIGN_SERVER_URL") or f"https://{derived['FOREIGN_PUBLIC_DOMAIN']}"
    derived["FOREIGN_SERVER_DOMAIN"] = derived.get("FOREIGN_SERVER_DOMAIN") or derived["FOREIGN_PUBLIC_DOMAIN"]
    derived["IRAN_SERVER_URL"] = derived.get("IRAN_SERVER_URL") or f"https://{derived['IRAN_APP_DOMAIN']}"
    derived["IRAN_SERVER_DOMAIN"] = derived.get("IRAN_SERVER_DOMAIN") or derived["IRAN_APP_DOMAIN"]
    derived["FOREIGN_FRONTEND_URL"] = derived.get("FOREIGN_FRONTEND_URL") or f"https://{derived['FOREIGN_PUBLIC_DOMAIN']}"
    derived["IRAN_FRONTEND_URL"] = derived.get("IRAN_FRONTEND_URL") or f"https://{derived['IRAN_APP_DOMAIN']}"
    derived["IRAN_HEALTHCHECK_URL"] = derived.get("IRAN_HEALTHCHECK_URL") or f"https://{derived['IRAN_APP_DOMAIN']}/api/config"
    derived["IRAN_LOCAL_API_URL"] = derived.get("IRAN_LOCAL_API_URL") or "http://127.0.0.1:8000/api/config"
    derived["NGINX_SERVER_NAME"] = derived["IRAN_APP_DOMAIN"]
    derived["NGINX_APP_ROOT"] = f"{derived['IRAN_PROJECT_DIR'].rstrip('/')}/mini_app_dist"
    derived["CERTBOT_DOMAIN"] = derived["IRAN_APP_DOMAIN"]
    derived["CERTBOT_EMAIL"] = derived["IRAN_CERTBOT_EMAIL"]

    validate_release_values(derived)
    return derived


def validate_release_values(values: dict[str, str]) -> None:
    require_ip(values["FOREIGN_PUBLIC_IP"], "FOREIGN_PUBLIC_IP")
    require_ip(values["IRAN_PUBLIC_IP"], "IRAN_PUBLIC_IP")
    require_domain(values["FOREIGN_PUBLIC_DOMAIN"], "FOREIGN_PUBLIC_DOMAIN")
    require_domain(values["IRAN_PUBLIC_DOMAIN"], "IRAN_PUBLIC_DOMAIN")
    require_domain(values["IRAN_APP_DOMAIN"], "IRAN_APP_DOMAIN")
    require_domain(values["FOREIGN_SERVER_DOMAIN"], "FOREIGN_SERVER_DOMAIN")
    require_domain(values["IRAN_SERVER_DOMAIN"], "IRAN_SERVER_DOMAIN")

    if values["FOREIGN_PUBLIC_IP"] == values["IRAN_PUBLIC_IP"]:
        raise ValueError("FOREIGN_PUBLIC_IP and IRAN_PUBLIC_IP must not be identical in two-server production mode")

    require_url_host_matches(
        values["FOREIGN_SERVER_URL"],
        values["FOREIGN_SERVER_DOMAIN"],
        "FOREIGN_SERVER_URL",
        "FOREIGN_SERVER_DOMAIN",
    )
    require_url_host_matches(
        values["IRAN_SERVER_URL"],
        values["IRAN_SERVER_DOMAIN"],
        "IRAN_SERVER_URL",
        "IRAN_SERVER_DOMAIN",
    )

    health_host = host_from_url(values["IRAN_HEALTHCHECK_URL"])
    if health_host != values["IRAN_APP_DOMAIN"].strip().lower():
        raise ValueError("IRAN_HEALTHCHECK_URL must target IRAN_APP_DOMAIN")


def render_hosts_block(values: dict[str, str]) -> str:
    return "\n".join(
        [
            "# trading-bot-production-hosts START",
            f"{values['FOREIGN_PUBLIC_IP']} {values['FOREIGN_PUBLIC_DOMAIN']}",
            f"{values['IRAN_PUBLIC_IP']} {values['IRAN_PUBLIC_DOMAIN']}",
            "# trading-bot-production-hosts END",
            "",
        ]
    )


def render_nginx_config(values: dict[str, str], template_path: str) -> str:
    template = Path(template_path).read_text(encoding="utf-8")
    return (
        template
        .replace("__SERVER_NAME__", values["NGINX_SERVER_NAME"])
        .replace("__APP_ROOT__", values["NGINX_APP_ROOT"])
    )


def healthcheck_bundle(values: dict[str, str]) -> dict[str, str]:
    return {
        "iran_healthcheck_url": values["IRAN_HEALTHCHECK_URL"],
        "iran_local_api_url": values["IRAN_LOCAL_API_URL"],
        "iran_frontend_url": values["IRAN_FRONTEND_URL"],
        "foreign_frontend_url": values["FOREIGN_FRONTEND_URL"],
        "iran_server_url": values["IRAN_SERVER_URL"],
        "foreign_server_url": values["FOREIGN_SERVER_URL"],
    }


def release_values_bundle(values: dict[str, str]) -> dict[str, str]:
    return {
        "foreign_public_ip": values["FOREIGN_PUBLIC_IP"],
        "foreign_public_domain": values["FOREIGN_PUBLIC_DOMAIN"],
        "iran_public_ip": values["IRAN_PUBLIC_IP"],
        "iran_public_domain": values["IRAN_PUBLIC_DOMAIN"],
        "iran_app_domain": values["IRAN_APP_DOMAIN"],
        "nginx_server_name": values["NGINX_SERVER_NAME"],
        "nginx_app_root": values["NGINX_APP_ROOT"],
        "certbot_domain": values["CERTBOT_DOMAIN"],
        "certbot_email": values["CERTBOT_EMAIL"],
    }


def write_artifacts(values: dict[str, str], template_path: str, output_dir: str) -> dict[str, str]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    paths = {
        "hosts": out / "hosts.block",
        "nginx": out / "iran-online-nginx.conf",
        "health": out / "healthcheck.json",
        "values": out / "release-values.json",
    }
    paths["hosts"].write_text(render_hosts_block(values), encoding="utf-8")
    paths["nginx"].write_text(render_nginx_config(values, template_path), encoding="utf-8")
    paths["health"].write_text(json.dumps(healthcheck_bundle(values), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    paths["values"].write_text(json.dumps(release_values_bundle(values), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {key: str(path) for key, path in paths.items()}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render production release artifacts from the deployment manifest.")
    parser.add_argument("--manifest", default="deploy/production/online.env")
    parser.add_argument("--template", default="deploy/production/nginx-iran-online.conf.template")
    parser.add_argument("--output-dir", default="tmp/production-release/artifacts")
    parser.add_argument("--print", choices={"hosts", "nginx", "health", "values", "paths"})
    parser.add_argument("--validate-only", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    values = derive_release_values(args.manifest)

    if args.validate_only:
        return 0

    if args.print == "hosts":
        print(render_hosts_block(values), end="")
        return 0
    if args.print == "nginx":
        print(render_nginx_config(values, args.template), end="")
        return 0
    if args.print == "health":
        print(json.dumps(healthcheck_bundle(values), ensure_ascii=False, sort_keys=True))
        return 0
    if args.print == "values":
        print(json.dumps(release_values_bundle(values), ensure_ascii=False, sort_keys=True))
        return 0

    paths = write_artifacts(values, args.template, args.output_dir)
    if args.print == "paths":
        print(json.dumps(paths, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
