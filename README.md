# Flight Status

A simple web app: enter a flight number, get its live status (delayed,
boarding, en route, landed, canceled...), with scheduled and updated times
for departure and arrival. When a flight's arrival time changes, the new
ETA is shown clearly, with the delay in minutes.

No external dependencies: the server is plain Python 3 (`http.server`
module), the page is vanilla HTML/CSS/JS. Nothing to install.

## 1. Get a free API key

Data comes from AeroDataBox, via RapidAPI:

1. Go to https://rapidapi.com/aedbx-aedbx/api/aerodatabox
2. Create a free RapidAPI account
3. Subscribe to the free ("Basic") plan of the AeroDataBox API
4. Copy your key ("X-RapidAPI-Key")

## 2. Configure the key

Two options:

**Option A - environment variable**

```bash
export AERODATABOX_API_KEY="your_key"
```

**Option B - config file**

Create `config.json` next to `server.py`:

```json
{ "AERODATABOX_API_KEY": "your_key" }
```

## 3. Run the app

```bash
python3 server.py
```

Then open http://localhost:8000

## Usage

Type a flight number (e.g. `AA1780`, `UA2419`, `DL283`) and press Enter or
"Search". The last searched flight is remembered and reloaded automatically
next time you open the app.

If no API key is configured, the app shows setup instructions directly on
screen instead of a raw error.
# flight-status-app
