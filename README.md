# MISP Provisioner (Browser-like Client)

This small Python app logs into a MISP instance using form-based auth and submits the `feeds/loadDefaultFeeds` action, emulating a real browser session with proper headers and CSRF token handling.

## Setup
# apt install python3.10-venv
1. Create and activate a virtual environment (optional).
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Provide credentials via environment variables or `.env` file:

```bash
cp env.sample .env
# edit .env with your values
```

- `MISP_USERNAME`: MISP username (email)
- `MISP_PASSWORD`: MISP password
- `MISP_BASE_URL` (optional): Defaults to `https://misp.ironclad.ofdecian`
 - `MISP_CERT_VALIDATION` (optional): `false` to disable TLS verification; defaults to `true`.
 - `MISP_CA_BUNDLE` (optional): Path to a custom CA bundle (PEM). Used when validation is enabled.
 - `MISP_CA_CERT` (optional): Alias for `MISP_CA_BUNDLE`.
 - `MISP_API_KEY` (optional): API key used for API commands (e.g., feeds count).

## Usage

Run the CLI to load default feeds:

```bash
# honor MISP_CERT_VALIDATION from environment (default true)
python cli.py load-default-feeds --base-url https://misp.ironclad.ofdecian
```

To use a custom root CA:

```bash
export MISP_CA_BUNDLE=/etc/ssl/certs/my-root-ca.pem
python cli.py load-default-feeds
```

You can also use the alias variable:

```bash
export MISP_CA_CERT=/etc/ssl/certs/my-root-ca.pem
python cli.py load-default-feeds
```

Troubleshooting TLS:
- Ensure the CA file exists and is readable by the process.
- Paths like `~` or `$HOME` are supported; they are expanded automatically.
- We also set `REQUESTS_CA_BUNDLE` to the resolved path to maximize compatibility with underlying libraries.

If `--base-url` is omitted, the value from `MISP_BASE_URL` or the built-in default is used.

## Notes
- The client fetches CSRF token fields from the login and Feeds pages and submits them with the POST, matching CakePHP SecurityComponent expectations.
- The session maintains cookies automatically via `requests.Session`.

## API usage: count feeds

With `MISP_API_KEY` set, you can get the count of feeds via the API without logging in via the form:

```bash
export MISP_API_KEY=YOUR_API_KEY
python cli.py feeds-count --base-url https://misp.ironclad.ofdecian
```

Outputs a single integer: the number of feed objects in the JSON array.

## API usage: configure all feeds (enable + cache)

```bash
export MISP_API_KEY=YOUR_API_KEY
python cli.py configure-feeds --base-url https://misp.ironclad.ofdecian
```

Prints a summary and returns nonzero if any enable failed.

## API usage: cache all feeds

```bash
export MISP_API_KEY=YOUR_API_KEY
python cli.py cache-feeds --base-url https://misp.ironclad.ofdecian
```

Returns "OK" on success.

## API usage: fetch from all feeds

```bash
export MISP_API_KEY=YOUR_API_KEY
python cli.py fetch-all-feeds --base-url https://misp.ironclad.ofdecian
```

Returns "OK" on success.

## Provision feeds (end-to-end)

```bash
export MISP_API_KEY=YOUR_API_KEY
export MISP_USERNAME=you@example.com
export MISP_PASSWORD=your-password
python cli.py provision-feeds --base-url https://misp.ironclad.ofdecian
```

Steps performed:
- Load default feeds (form-based login)
- Configure all feeds (enable + caching)
- Fetch from all feeds
- Cache all feeds
<!-- Auth key web flow removed as per request. -->
