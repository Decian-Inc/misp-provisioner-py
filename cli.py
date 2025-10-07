import argparse
import os
import sys

from dotenv import load_dotenv

from misp_client import MispBrowserClient, MispAuth, get_auth_from_env, get_verify_config_from_env


def parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(description="MISP browser client CLI")
	parser.add_argument("command", choices=["load-default-feeds", "feeds-count", "configure-feeds", "cache-feeds", "fetch-all-feeds", "provision-feeds"], help="Command to run")
    parser.add_argument("--base-url", dest="base_url", default=os.environ.get("MISP_BASE_URL"), help="MISP base URL (required)")
	parser.add_argument("--debug", dest="debug", action="store_true", help="Enable verbose debug logging")
	return parser.parse_args()


def main() -> int:
	load_dotenv()
	args = parse_args()
    if not args.base_url:
        print("MISP_BASE_URL is required (via --base-url or env)", file=sys.stderr)
        return 2
    client = MispBrowserClient(base_url=args.base_url, verify=get_verify_config_from_env())
	if args.command == "load-default-feeds":
		auth = get_auth_from_env()
		client.login(auth)
		resp = client.load_default_feeds()
		print(f"Status: {resp.status_code}")
		print("OK" if resp.ok else "FAILED")
		return 0 if resp.ok else 1
	elif args.command == "feeds-count":
		api_key = os.environ.get("MISP_API_KEY")
		if not api_key:
			print("MISP_API_KEY is required", file=sys.stderr)
			return 2
		feeds = client.api_get_feeds(api_key, debug=True)
		print(len(feeds))
		return 0
	elif args.command == "configure-feeds":
		api_key = os.environ.get("MISP_API_KEY")
		if not api_key:
			print("MISP_API_KEY is required", file=sys.stderr)
			return 2
		summary = client.api_enable_all_feeds(api_key, debug=True)
		print(f"total={summary['total']} attempted={summary['attempted']} succeeded={summary['succeeded']} failed={summary['failed']}")
		return 0 if summary["failed"] == 0 else 3
	elif args.command == "cache-feeds":
		api_key = os.environ.get("MISP_API_KEY")
		if not api_key:
			print("MISP_API_KEY is required", file=sys.stderr)
			return 2
		ok = client.api_cache_all_feeds(api_key, debug=True)
		print("OK" if ok else "FAILED")
		return 0 if ok else 4
	elif args.command == "fetch-all-feeds":
		api_key = os.environ.get("MISP_API_KEY")
		if not api_key:
			print("MISP_API_KEY is required", file=sys.stderr)
			return 2
		ok = client.api_fetch_all_feeds(api_key, debug=True)
		print("OK" if ok else "FAILED")
		return 0 if ok else 5
	elif args.command == "provision-feeds":
		# 0) wait until base URL is healthy
		healthy = client.wait_until_healthy(timeout_seconds=None, interval_seconds=10, debug=True)
		if not healthy:
			print("Server not healthy (no 200) within timeout", file=sys.stderr)
			return 9
		# 1) load default feeds (form login required)
		auth = get_auth_from_env()
		client.login_with_retries(auth, max_attempts=10, backoff_seconds=10, debug=True)
		resp1 = client.load_default_feeds()
		if not resp1.ok:
			print(f"load-default-feeds failed: {resp1.status_code}", file=sys.stderr)
			return 10
		# 2) enable+cache each feed
		api_key = os.environ.get("MISP_API_KEY")
		if not api_key:
			print("MISP_API_KEY is required for subsequent steps", file=sys.stderr)
			return 11
		summary = client.api_enable_all_feeds(api_key, debug=True)
		print(f"configure-feeds: total={summary['total']} attempted={summary['attempted']} succeeded={summary['succeeded']} failed={summary['failed']}")
		if summary["failed"] > 0:
			return 12
		# 3) fetch all feeds
		ok_fetch = client.api_fetch_all_feeds(api_key, debug=True)
		print("fetch-all-feeds:", "OK" if ok_fetch else "FAILED")
		if not ok_fetch:
			return 13
		# 4) cache all feeds
		ok_cache = client.api_cache_all_feeds(api_key, debug=True)
		print("cache-feeds:", "OK" if ok_cache else "FAILED")
		return 0 if ok_cache else 14
	return 0


if __name__ == "__main__":
	sys.exit(main())
