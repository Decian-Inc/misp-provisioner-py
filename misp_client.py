from __future__ import annotations

import os
import sys
import tempfile
from dataclasses import dataclass
from typing import Dict, Optional, Tuple, Union
from os.path import expanduser, expandvars, isfile, abspath
import time

import requests
from bs4 import BeautifulSoup


DEFAULT_BASE_URL = None  # No default; base_url must be provided explicitly


@dataclass
class MispAuth:
	username: str
	password: str



def get_verify_from_env() -> bool:
	val = os.environ.get("MISP_CERT_VALIDATION", "true")
	if val is None:
		return True
	text = str(val).strip().lower()
	return text not in ("0", "false", "no", "off", "n")


def get_verify_config_from_env() -> Union[bool, str]:
	"""Return verify config for requests: False, True, or CA bundle path.

	Order:
	- if MISP_CERT_VALIDATION=false -> False
	- else -> True
	"""
	if not get_verify_from_env():
		return False
	ca_value = os.environ.get("MISP_CA_CERT")
	if ca_value:
		text_val = str(ca_value).strip()
		# Support inline PEM in env: detect BEGIN CERTIFICATE marker
		if "-----BEGIN CERTIFICATE-----" in text_val or "-----BEGIN TRUSTED CERTIFICATE-----" in text_val:
			# Allow \n sequences in .env to represent newlines
			pem_text = text_val.replace("\\n", "\n")
			# Persist to a temp file for requests to consume
			tmp_path = "/tmp/misp_ca_cert_from_env.pem"
			try:
				with open(tmp_path, "w") as f:
					f.write(pem_text)
			except Exception as e:
				raise RuntimeError(f"Failed to write inline CA cert to {tmp_path}: {e}")
			os.environ["REQUESTS_CA_BUNDLE"] = tmp_path
			return tmp_path
		# Otherwise treat as a filesystem path
		resolved = abspath(expanduser(expandvars(text_val)))
		os.environ["REQUESTS_CA_BUNDLE"] = resolved
		if not isfile(resolved):
			raise RuntimeError(f"CA bundle not found at path: {resolved}")
		return resolved
	return True


class MispBrowserClient:
	def __init__(self, base_url: str, session: Optional[requests.Session] = None, verify: Optional[Union[bool, str]] = None):
		if not base_url or not str(base_url).strip():
			raise ValueError("MISP base_url is required")
		self.base_url = base_url.rstrip("/")
		self.session = session or requests.Session()
		# Configure TLS verification from arg or env
		if verify is None:
			verify = get_verify_config_from_env()
		self.session.verify = verify
		# Browser-like headers
		self.session.headers.update({
			"user-agent": (
				"Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
				"AppleWebKit/537.36 (KHTML, like Gecko) "
				"Chrome/140.0.0.0 Safari/537.36"
			),
			"accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
			"accept-language": "en-US,en;q=0.9",
			"cache-control": "no-cache",
			"pragma": "no-cache",
			"upgrade-insecure-requests": "1",
		})

	def wait_until_healthy(self, timeout_seconds: Optional[int] = None, interval_seconds: int = 10, debug: bool = False) -> bool:
		"""Poll base URL until a 200 OK is returned or timeout elapses.

		If timeout_seconds is None, waits indefinitely. Returns True if 200 was observed, else False.
		"""
		deadline = None if timeout_seconds is None else (time.time() + timeout_seconds)
		attempt = 0
		while True:
			if deadline is not None and time.time() >= deadline:
				return False
			try:
				attempt += 1
				resp = self._get("")
				if debug:
					print(f"[wait] attempt={attempt} status={resp.status_code}")
				if resp.status_code == 200:
					return True
			except requests.RequestException as e:
				if debug:
					print(f"[wait] attempt={attempt} exception during request: {e.__class__.__name__}: {e}")
				pass
			time.sleep(interval_seconds)

	def api_get_feeds(self, api_key: str, debug: bool = False) -> list:
		"""Fetch feeds via MISP REST API using an API key.

		Tries several endpoints that MISP installations expose.
		"""
		headers = {
			"Authorization": api_key,
			"Accept": "application/json",
			# No Content-Type for GET
		}
		endpoints = [
			"feeds/index",
			"feeds/index.json",
			"feeds",
			"feeds.json",
		]
		for ep in endpoints:
			url = f"{self.base_url}/{ep}"
			resp = self.session.get(url, headers=headers, allow_redirects=True)
			if debug:
				print(f"[feeds] GET {url} -> {resp.status_code}")
			# Accept 200 and JSON content
			if resp.status_code == 200:
				try:
					data = resp.json()
					if isinstance(data, list):
						return data
					# Some MISP APIs return under a key
					if isinstance(data, dict) and "data" in data and isinstance(data["data"], list):
						return data["data"]
				except ValueError:
					pass
		# Include body snippet for auth errors to aid debugging
		snippet = ""
		try:
			snippet = resp.text[:300]
		except Exception:
			pass
		raise RuntimeError(f"Failed to fetch feeds as JSON; last status: {resp.status_code}. Body: {snippet}")

	def api_get_feed(self, feed_id: str, api_key: str) -> Optional[dict]:
		"""Fetch a single feed definition if available.

		Tries: feeds/view/{id}.json, feeds/{id}.json
		Returns the parsed dict (potentially with key "Feed" inside) or None if not retrievable.
		"""
		headers = {"Authorization": api_key, "Accept": "application/json"}
		candidates = [f"feeds/view/{feed_id}.json", f"feeds/{feed_id}.json"]
		for ep in candidates:
			url = f"{self.base_url}/{ep}"
			resp = self.session.get(url, headers=headers, allow_redirects=True)
			if resp.status_code == 200:
				try:
					return resp.json()
				except Exception:
					return None
		return None

	def api_enable_feed(self, feed_id: str, api_key: str, debug: bool = False) -> bool:
		"""Enable a single feed by id using API key, setting enabled=true and caching_enabled=true.

		Attempt order:
		1) POST feeds/edit/{id}.json with minimal JSON
		2) POST feeds/edit/{id}.json with JSON including id
		3) Fetch existing feed, merge, POST feeds/edit/{id}.json
		4) POST feeds/edit/{id} form-encoded (enabled=1, caching_enabled=1)
		5) Form override _method fallback
		"""
		auth_headers = {"Authorization": api_key, "Accept": "application/json"}
		url_json = f"{self.base_url}/feeds/edit/{feed_id}.json"
		json_headers = {**auth_headers, "Content-Type": "application/json"}
		# 1) minimal JSON
		payload_min = {"enabled": True, "caching_enabled": True}
		resp = self.session.post(url_json, headers=json_headers, json=payload_min, allow_redirects=True)
		if debug:
			print(f"[edit] POST {url_json} payload={payload_min} -> {resp.status_code}")
		if 200 <= resp.status_code < 300:
			return True
		# 2) include id
		payload_with_id = {"id": str(feed_id), **payload_min}
		resp2 = self.session.post(url_json, headers=json_headers, json=payload_with_id, allow_redirects=True)
		if debug:
			print(f"[edit] POST {url_json} payload={payload_with_id} -> {resp2.status_code}")
		if 200 <= resp2.status_code < 300:
			return True
		# 3) merge with existing feed
		feed_obj = self.api_get_feed(feed_id, api_key)
		if isinstance(feed_obj, dict):
			# Unwrap if nested under "Feed"
			if "Feed" in feed_obj and isinstance(feed_obj["Feed"], dict):
				base = dict(feed_obj["Feed"])  # copy
			else:
				base = dict(feed_obj)
			base["enabled"] = True
			# Some variants use caching_enabled, ensure correct key
			base["caching_enabled"] = True
			# Ensure id present as string
			if "id" not in base:
				base["id"] = str(feed_id)
			resp3 = self.session.post(url_json, headers=json_headers, json=base, allow_redirects=True)
			if debug:
				print(f"[edit] POST {url_json} merge-payload -> {resp3.status_code}")
			if 200 <= resp3.status_code < 300:
				return True
		# 4) form-encoded fallback
		url_form = f"{self.base_url}/feeds/edit/{feed_id}"
		form_headers = {**auth_headers, "Content-Type": "application/x-www-form-urlencoded"}
		form_data = {"enabled": "1", "caching_enabled": "1"}
		resp4 = self.session.post(url_form, headers=form_headers, data=form_data, allow_redirects=True)
		if debug:
			print(f"[edit] POST {url_form} form {form_data} -> {resp4.status_code}")
		if 200 <= resp4.status_code < 300:
			return True
		# 5) _method override
		resp5 = self.session.post(url_form, headers=form_headers, data={"_method": "POST", **form_data}, allow_redirects=True)
		if debug:
			print(f"[edit] POST {url_form} form-override -> {resp5.status_code}")
		return 200 <= resp5.status_code < 300

	def api_enable_all_feeds(self, api_key: str, debug: bool = False) -> Dict[str, int]:
		"""Fetch feeds and enable each one; returns summary counts.
		Counts: total, attempted, succeeded, failed.
		"""
		feeds = self.api_get_feeds(api_key, debug=debug)
		total = len(feeds)
		attempted = 0
		succeeded = 0
		failed = 0
		for item in feeds:
			# Handle both shapes: {"Feed": {"id": "..."}} or flat {"id": ...}
			if isinstance(item, dict) and "Feed" in item and isinstance(item["Feed"], dict):
				fid = str(item["Feed"].get("id", "")).strip()
			else:
				fid = str(item.get("id", "")).strip() if isinstance(item, dict) else ""
			if not fid:
				continue
			attempted += 1
			ok = self.api_enable_feed(fid, api_key, debug=debug)
			if ok:
				succeeded += 1
			else:
				failed += 1
			if debug:
				print(f"[enable] feed_id={fid} ok={ok}")
		return {"total": total, "attempted": attempted, "succeeded": succeeded, "failed": failed}

	def api_cache_all_feeds(self, api_key: str, debug: bool = False) -> bool:
		"""Trigger caching of all feeds via API.

		Tries several endpoints:
		- POST feeds/cacheFeeds/all
		- POST feeds/cacheFeeds/all.json
		Returns True if a 200-299 status is received.
		"""
		headers = {
			"Authorization": api_key,
			"Accept": "application/json",
		}
		endpoints = [
			"feeds/cacheFeeds/all",
			"feeds/cacheFeeds/all.json",
		]
		for ep in endpoints:
			url = f"{self.base_url}/{ep}"
			resp = self.session.post(url, headers=headers, allow_redirects=True)
			if debug:
				print(f"[cache] POST {url} -> {resp.status_code}")
			if 200 <= resp.status_code < 300:
				return True
			# Try method override fallback
			resp2 = self.session.post(url, headers={**headers, "Content-Type": "application/x-www-form-urlencoded"}, data={"_method": "POST"}, allow_redirects=True)
			if debug:
				print(f"[cache] POST {url} _method=POST -> {resp2.status_code}")
			return 200 <= resp2.status_code < 300
		return False

	def api_fetch_all_feeds(self, api_key: str, debug: bool = False) -> bool:
		"""Trigger fetching from all feeds via API.

		Tries several endpoints:
		- POST feeds/fetchFromAllFeeds
		- POST feeds/fetchFromAllFeeds.json
		Returns True if a 200-299 status is received.
		"""
		headers = {
			"Authorization": api_key,
			"Accept": "application/json",
		}
		endpoints = [
			"feeds/fetchFromAllFeeds",
			"feeds/fetchFromAllFeeds.json",
		]
		for ep in endpoints:
			url = f"{self.base_url}/{ep}"
			resp = self.session.post(url, headers=headers, allow_redirects=True)
			if debug:
				print(f"[fetch] POST {url} -> {resp.status_code}")
			if 200 <= resp.status_code < 300:
				return True
			# Try method override fallback
			resp2 = self.session.post(url, headers={**headers, "Content-Type": "application/x-www-form-urlencoded"}, data={"_method": "POST"}, allow_redirects=True)
			if debug:
				print(f"[fetch] POST {url} _method=POST -> {resp2.status_code}")
			return 200 <= resp2.status_code < 300
		return False

	def _get(self, path: str, **kwargs) -> requests.Response:
		url = f"{self.base_url}/{path.lstrip('/')}"
		print(f"[DEBUG] GET {url}", file=sys.stderr)
		# Ensure calls don't hang forever
		if "timeout" not in kwargs:
			kwargs["timeout"] = 30
		# Try with redirects first
		resp = self.session.get(url, allow_redirects=True, **kwargs)
		print(f"[DEBUG] GET {url} -> {resp.status_code} -> {resp.url}", file=sys.stderr)
		
		# If we got redirected to external domain, try without redirects and use the original URL
		if resp.url and self.base_url not in resp.url and "ironclad.ofdecian" in resp.url:
			print(f"[DEBUG] Redirected to external domain, retrying without redirects", file=sys.stderr)
			resp = self.session.get(url, allow_redirects=False, **kwargs)
			print(f"[DEBUG] GET {url} (no redirects) -> {resp.status_code}", file=sys.stderr)
		
		return resp

	def _post(self, path: str, data: Dict[str, str], headers: Optional[Dict[str, str]] = None) -> requests.Response:
		url = f"{self.base_url}/{path.lstrip('/')}"
		print(f"[DEBUG] POST {url}", file=sys.stderr)
		default_headers = {"content-type": "application/x-www-form-urlencoded"}
		if headers:
			default_headers.update(headers)
		# Ensure calls don't hang forever
		resp = self.session.post(url, data=data, headers=default_headers, allow_redirects=True, timeout=30)
		print(f"[DEBUG] POST {url} -> {resp.status_code} -> {resp.url}", file=sys.stderr)
		
		# If we got redirected to external domain, try without redirects and use the original URL
		if resp.url and self.base_url not in resp.url and "ironclad.ofdecian" in resp.url:
			print(f"[DEBUG] POST redirected to external domain, retrying without redirects", file=sys.stderr)
			resp = self.session.post(url, data=data, headers=default_headers, allow_redirects=False)
			print(f"[DEBUG] POST {url} (no redirects) -> {resp.status_code}", file=sys.stderr)
		
		return resp

	@staticmethod
	def _extract_csrf_fields(html: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
		"""Extract CakePHP SecurityComponent token fields from form.

		Returns: (token_key, token_fields, token_unlocked)
		"""
		soup = BeautifulSoup(html, "lxml")
		token_key_el = soup.select_one('input[name="data[_Token][key]"]')
		token_fields_el = soup.select_one('input[name="data[_Token][fields]"]')
		token_unlocked_el = soup.select_one('input[name="data[_Token][unlocked]"]')
		key = token_key_el["value"] if token_key_el and token_key_el.has_attr("value") else None
		fields = token_fields_el["value"] if token_fields_el and token_fields_el.has_attr("value") else None
		unlocked = token_unlocked_el["value"] if token_unlocked_el and token_unlocked_el.has_attr("value") else None
		return key, fields, unlocked

	def login(self, auth: MispAuth) -> None:
		# Visit login page to obtain CSRF fields and cookies
		login_page = self._get("users/login")
		if login_page.status_code >= 400:
			raise RuntimeError(f"Failed to load login page: {login_page.status_code}")
		key, fields, unlocked = self._extract_csrf_fields(login_page.text)
		form_data = {
			"_method": "POST",
			"data[_Token][key]": key or "",
			"data[_Token][fields]": fields or "",
			"data[_Token][unlocked]": unlocked or "",
			"data[User][email]": auth.username,
			"data[User][password]": auth.password,
		}
		resp = self._post("users/login", data=form_data, headers={"origin": self.base_url, "referer": f"{self.base_url}/users/login"})
		# If CSRF protection trips, refresh tokens and retry once
		if resp.status_code >= 400 or ("cross-site request forgery protection" in resp.text.lower() or "csrf error" in resp.text.lower()):
			print("[login] CSRF error detected, refreshing login page and retrying once", file=sys.stderr)
			login_page2 = self._get("users/login")
			if login_page2.status_code < 400:
				key2, fields2, unlocked2 = self._extract_csrf_fields(login_page2.text)
				form_data = {
					"_method": "POST",
					"data[_Token][key]": key2 or "",
					"data[_Token][fields]": fields2 or "",
					"data[_Token][unlocked]": unlocked2 or "",
					"data[User][email]": auth.username,
					"data[User][password]": auth.password,
				}
				resp = self._post("users/login", data=form_data, headers={"origin": self.base_url, "referer": f"{self.base_url}/users/login"})
		if resp.status_code >= 400:
			raise RuntimeError(f"Login POST failed: {resp.status_code}")
		# Determine successful login by presence of logout link or redirect to dashboard
		if "logout" not in resp.text.lower() and "/users/logout" not in resp.text.lower():
			# Some MISP redirects; fetch home to confirm
			home = self._get("")
			if home.status_code >= 400 or ("/users/logout" not in home.text and "logout" not in home.text.lower()):
				raise RuntimeError("Login may have failed; logout link not found")

	def login_with_retries(self, auth: MispAuth, max_attempts: int = 5, backoff_seconds: int = 5, debug: bool = True) -> None:
		"""Attempt login with simple retry/backoff and debug logs."""
		attempt = 0
		last_error = None
		while attempt < max_attempts:
			attempt += 1
			try:
				if debug:
					print(f"[login] attempt={attempt}")
				self.login(auth)
				if debug:
					print("[login] success")
				return
			except Exception as e:
				last_error = e
				if debug:
					print(f"[login] attempt={attempt} error={e}")
				time.sleep(backoff_seconds)
		raise RuntimeError(f"Login failed after {max_attempts} attempts: {last_error}")

	def load_default_feeds(self) -> requests.Response:
		# Navigate to Feeds index to get token
		feeds_page = self._get("Feeds")
		if feeds_page.status_code >= 400:
			raise RuntimeError(f"Failed to load Feeds page: {feeds_page.status_code}")
		key, fields, unlocked = self._extract_csrf_fields(feeds_page.text)
		form_data = {
			"_method": "POST",
			"data[_Token][key]": key or "",
			"data[_Token][fields]": fields or "",
			"data[_Token][unlocked]": unlocked or "",
		}
		resp = self._post("feeds/loadDefaultFeeds", data=form_data, headers={
			"origin": self.base_url,
			"referer": f"{self.base_url}/Feeds",
		})
		if resp.status_code >= 400:
			raise RuntimeError(f"loadDefaultFeeds failed: {resp.status_code}")
		return resp



def get_auth_from_env() -> MispAuth:
	username = os.environ.get("MISP_USERNAME")
	password = os.environ.get("MISP_PASSWORD")
	if not username or not password:
		raise RuntimeError("MISP_USERNAME and MISP_PASSWORD must be set")
	return MispAuth(username=username, password=password)


 
