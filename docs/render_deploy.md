# Deploy CAN-HMI on Render

## Deployment architecture

The `render.yaml` Blueprint creates a Free Python Web Service in Singapore. This deployment
uses a virtual CAN bus and the DBC v9 simulator because a Render container cannot access the
CarPC's USB-CAN adapter or SocketCAN interfaces. The camera proxy and status monitor remain
enabled; unreachable camera or Ethernet endpoints stay unavailable or report `false`.

The application reads the HTTP port assigned by Render from `PORT`. The API key is not stored
in Git; Render prompts for the `CAR_HMI_API_KEY` secret when the Blueprint is created.

## 1. Verify locally before pushing

Run these commands from the repository root:

```bash
.venv/bin/python -m json.tool config/system.json >/dev/null
.venv/bin/python -m pytest tests/1_unit_functions/test_core.py
```

You can run a smoke test with the same environment variables used on Render:

```bash
PORT=10000 \
CAR_HMI_REQUIRE_API_KEY=true \
CAR_HMI_API_KEY=local-render-test-key \
.venv/bin/python -m src.core.runner --config config/system.json --log-level INFO
```

Open `http://127.0.0.1:10000/system/health` to verify the service. Stop it with `Ctrl+C`.

## 2. Commit and push the deployment branch

```bash
git add .python-version render.yaml config/system.json config/system_bk.json \
  src/core/config.py src/core/runner.py frontend/js/api.js \
  tests/1_unit_functions/test_core.py docs/render_deploy.md README.md
git commit -m "Add Render deployment configuration" \
  -m $'Configure virtual CAN simulation for the hosted demo.\nRead Render port and API secrets from environment variables.\nDocument Blueprint deployment and verification steps.'
git push -u origin dev_onrender
```

Do not commit the API key or any `.env` file.

## 3. Create the Blueprint on Render

After pushing the branch, open
[Create Render Blueprint](https://dashboard.render.com/blueprint/new?repo=https://github.com/3tdn/car-hmi/tree/dev_onrender).

1. Sign in to Render and select **New > Blueprint**.
2. Connect GitHub if Render does not yet have access to the `3tdn/car-hmi` repository.
3. Select the `3tdn/car-hmi` repository and the `dev_onrender` branch.
4. Keep the Blueprint Path set to `render.yaml`.
5. Enter a sufficiently long secret for `CAR_HMI_API_KEY`. You can generate one with:

   ```bash
   openssl rand -hex 32
   ```

6. Confirm the `car-hmi` service, Singapore region, and Free plan, then select
   **Deploy Blueprint**.

Render installs the package with `pip install .` and then starts the application with Python.
This demo configuration does not require PostgreSQL, Redis, a worker, or a cron job.

## 4. Monitor and verify the deployment

Monitor the **Events/Logs** tab on the service page. A successful deployment should show:

- Uvicorn binding to `0.0.0.0:<Render PORT>`;
- the virtual CAN bus opening successfully;
- the simulator starting;
- the `/system/health` health check returning HTTP 200.

After the service status becomes **Live**, replace `<service-url>` with the actual Render URL:

```bash
curl -fsS https://<service-url>/system/health
curl -fsS https://<service-url>/api/info
curl -fsS -H 'X-API-Key: <CAR_HMI_API_KEY>' \
  https://<service-url>/signals/available
```

The dashboard is available at `https://<service-url>/`. To let the dashboard send the API key
without storing the secret in source code, open Developer Tools > Console on that domain and run:

```js
sessionStorage.setItem("can_hmi_api_key", "<CAR_HMI_API_KEY>");
location.reload();
```

The key is retained only in the current tab. Remove it from the browser with:

```js
sessionStorage.removeItem("can_hmi_api_key");
location.reload();
```

## Known limitations

- This is a simulated CAN demo. The camera proxy works only when its configured stream URL is
  reachable from the Render service.
- A Free Web Service can spin down while idle, so its first subsequent request can take time
  to start the service again.
- The Free plan uses an ephemeral filesystem. The SQLite `data/signals.db` file, profile
  sessions, configuration backups, and configuration changes made through the UI are lost
  when the service restarts, redeploys, or spins down.
- Persistent SQLite storage requires a paid plan with a Persistent Disk. Alternatively, the
  storage layer must be migrated to PostgreSQL before using Render Postgres.

## Common issues

- `CAR_HMI_API_KEY must be set...`: the secret is missing or still uses a placeholder value.
- `PORT must be an integer...`: `PORT` was overridden with an invalid value. Remove the
  override and let Render provide the port.
- Health check timeout: confirm that the logs show a bind to `0.0.0.0` on the value of `$PORT`.
- HTTP 401 from `/signals` or `/config`: the request or browser API key does not match the
  Render environment variable.
- Data disappears after a restart: this is a limitation of the Free plan's ephemeral
  filesystem, not a SQLite error.
