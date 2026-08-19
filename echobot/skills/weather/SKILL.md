---
description: >
  Fetch current weather conditions and forecasts — no API key needed. Use
  this skill whenever the user asks about weather, temperature, rain, wind,
  humidity, forecast, or whether to bring an umbrella — even casual phrasing
  like "is it cold outside?", "what's the weather like in Tokyo?", "will it
  rain tomorrow in Seattle?", or "check the weather for me". Works on Linux,
  macOS, and Windows without any setup.
homepage: "https://wttr.in/:help"
metadata:
  echo: '{"emoji": "🌤️"}'
name: weather
---

# Weather

Fetch and display current weather and forecasts — no API key required.

## Decision flow

1. **Identify the location** from the user's request.
   - If the user says "here", "my location", or gives no city, ask for the city name.
   - URL-encode spaces: `New+York`, `São+Paulo`.
   - Airport codes work: `JFK`, `LHR`, `NRT`.
2. **Detect the platform** (or infer from context):
   - Linux / macOS / Git Bash → `curl`
   - Windows PowerShell → `curl.exe` (bare `curl` maps to `Invoke-WebRequest` and may fail)
3. **Choose the format** based on what the user wants:
   - Quick one-liner → `format=3`
   - More detail (humidity, wind) → custom format string
   - Full 3-day forecast → `?T` flag
   - Structured data / calculations → Open-Meteo JSON (see below)

---

## wttr.in — primary source

### One-line summary (default for most queries)

```bash
# Linux / macOS
curl -fsSL "https://wttr.in/London?format=3"
# → London: ⛅️ +8°C

# Windows PowerShell
curl.exe -s "https://wttr.in/London?format=3"
```

### Compact with humidity and wind

```bash
# Linux / macOS
curl -fsSL "https://wttr.in/London?format=%l:+%c+%t+%h+%w"
# → London: ⛅️ +8°C 71% ↙5km/h

# Windows PowerShell
curl.exe -s "https://wttr.in/London?format=%l:+%c+%t+%h+%w"
```

### Full 3-day forecast (plain text, renders as a table)

```bash
curl -fsSL "https://wttr.in/London?T"          # Linux
curl.exe -s  "https://wttr.in/London?T"         # Windows
```

### Current conditions only (no forecast days)

```bash
curl -fsSL "https://wttr.in/London?0"           # Linux
curl.exe -s  "https://wttr.in/London?0"          # Windows
```

### Units

Append to the URL query string:

| Suffix | Unit system               |
|--------|---------------------------|
| `?m`   | Metric (°C, km/h)         |
| `?u`   | US customary (°F, mph)    |
| `?M`   | Metric with m/s wind      |
| *(none)* | Auto-detected by IP     |

### Format codes (for custom `format=` strings)

| Code | Meaning        |
|------|----------------|
| `%c` | Condition icon |
| `%t` | Temperature    |
| `%f` | Feels-like     |
| `%h` | Humidity       |
| `%w` | Wind           |
| `%p` | Precipitation  |
| `%P` | Pressure       |
| `%l` | Location name  |
| `%m` | Moon phase     |

---

## Open-Meteo — fallback for structured/JSON use

Use when the user wants data for calculations, comparisons, or programmatic output. Requires coordinates (latitude/longitude).

```bash
# Linux / macOS
curl -fsSL "https://api.open-meteo.com/v1/forecast?latitude=51.5&longitude=-0.12&current_weather=true"

# Windows PowerShell (cleanest for JSON)
Invoke-RestMethod -Uri "https://api.open-meteo.com/v1/forecast?latitude=51.5&longitude=-0.12&current_weather=true"
```

Returns JSON: `temperature`, `windspeed`, `winddirection`, `weathercode`, `time`.

Add `&hourly=temperature_2m,precipitation` for hourly data, or `&daily=temperature_2m_max,precipitation_sum` for daily summaries. Docs: https://open-meteo.com/en/docs

### WMO weather code → plain language

| Code    | Description           |
|---------|-----------------------|
| 0       | Clear sky             |
| 1–3     | Partly cloudy         |
| 45, 48  | Fog                   |
| 51–67   | Drizzle / Rain        |
| 71–77   | Snow                  |
| 80–82   | Rain showers          |
| 85–86   | Snow showers          |
| 95      | Thunderstorm          |
| 96, 99  | Thunderstorm + hail   |

---

## Presenting results to the user

- **Short query** ("what's the weather in Paris?"): run `format=3`, then restate in a natural sentence — *"It's currently ⛅ 12°C in Paris."*
- **Forecast request** ("3-day forecast for Tokyo"): use the `?T` flag and display the text table directly — it's already nicely formatted.
- **JSON / Open-Meteo**: translate `weathercode` to plain language; don't dump raw JSON at the user.
- **Fetch failure**: if the city isn't found or the request times out, try an alternate spelling or ask the user to confirm the location.
