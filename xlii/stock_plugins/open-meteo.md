---
id: open-meteo
name: Open-Meteo
description: Free weather forecasts and current conditions, no API key required
categories: [weather]
effect: read-only
trust: subscription
auth_type: none
auth_env_vars: []
actions:
  - id: geocode
    description: Look up latitude/longitude for a city name (e.g. 'Aurora, IL' or 'London')
    method: GET
    url: https://geocoding-api.open-meteo.com/v1/search
    params:
      name: {required: true, description: "City name (e.g. 'Aurora, IL'), auto URL-encoded"}
      count: {default: "1", description: "Number of results (1-10)"}
    response_shape: ".results[0] → {latitude, longitude, name, country, timezone}"
    output_schema:
      type: object
      additionalProperties: false
      required: [name, country, latitude, longitude, timezone]
      properties:
        name: {type: string, description: "From results[0].name"}
        country: {type: string, description: "From results[0].country"}
        latitude: {type: number, description: "From results[0].latitude (decimal degrees)"}
        longitude: {type: number, description: "From results[0].longitude (decimal degrees)"}
        timezone: {type: string, description: "From results[0].timezone (e.g. 'America/Chicago')"}
    output: schema
    output_transforms:
      - {op: lift, from: results.0}     # pull the first geocoding result up to root
    output_renderer: text_template
    output_renderer_args:
      template: |-
        {name}, {country}
          coordinates: {latitude}, {longitude}
          timezone:    {timezone}
  - id: current_weather
    description: Current weather + hourly forecast for lat/lon (use geocode first for cities)
    method: GET
    url: https://api.open-meteo.com/v1/forecast
    params:
      latitude: {required: true, description: "Decimal degrees (-90 to 90), e.g. 41.77"}
      longitude: {required: true, description: "Decimal degrees (-180 to 180), e.g. -88.32"}
      current_weather: {const: "true"}
      hourly: {default: "temperature_2m,precipitation_probability,wind_speed_10m"}
      timezone: {default: "auto"}
    response_shape: ".current_weather → {temperature_2m, windspeed_10m, weathercode, ...}; .hourly → arrays"
    output_schema:
      type: object
      additionalProperties: false
      required: [temperature_c, wind_kmh, wind_direction_deg, weather_description, timezone, observed_at]
      properties:
        temperature_c:
          type: number
          description: "From .current_weather.temperature (°C)"
        wind_kmh:
          type: number
          description: "From .current_weather.windspeed (km/h)"
        wind_direction_deg:
          type: number
          description: "From .current_weather.winddirection (compass degrees)"
        weather_description:
          type: string
          description: "Plain-English summary of .current_weather.weathercode using WMO mapping: 0=Clear sky; 1-3=Mainly clear/Partly cloudy/Overcast; 45,48=Fog; 51-57=Drizzle; 61-67=Rain; 71-77=Snow; 80-82=Rain showers; 85-86=Snow showers; 95-99=Thunderstorm. Pick the closest description."
        timezone:
          type: string
          description: "From response.timezone"
        observed_at:
          type: string
          description: "From .current_weather.time (ISO timestamp)"
    output: schema
    output_transforms:
      - {op: lift, from: current_weather}   # temperature/windspeed/weathercode/time → root (timezone already at root)
      - {op: rename, from: temperature, to: temperature_c}
      - {op: rename, from: windspeed, to: wind_kmh}
      - {op: rename, from: winddirection, to: wind_direction_deg}
      - {op: rename, from: time, to: observed_at}
      - op: code_map
        field: weathercode
        to: weather_description
        default: "Unknown conditions"
        table: {0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
                45: "Fog", 48: "Depositing rime fog", 51: "Light drizzle", 53: "Moderate drizzle",
                55: "Dense drizzle", 56: "Light freezing drizzle", 57: "Dense freezing drizzle",
                61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain", 66: "Light freezing rain",
                67: "Heavy freezing rain", 71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow",
                77: "Snow grains", 80: "Slight rain showers", 81: "Moderate rain showers",
                82: "Violent rain showers", 85: "Slight snow showers", 86: "Heavy snow showers",
                95: "Thunderstorm", 96: "Thunderstorm with slight hail", 99: "Thunderstorm with heavy hail"}
    output_renderer: text_template
    output_renderer_args:
      template: |-
        Current weather ({timezone}, observed {observed_at}):
          {weather_description}
          temperature: {temperature_c}°C
          wind:        {wind_kmh} km/h @ {wind_direction_deg}°
  - id: daily_forecast
    description: Daily forecast (next 7 days) for a location
    method: GET
    url: https://api.open-meteo.com/v1/forecast
    params:
      latitude: {required: true, description: "Decimal degrees, negative = south"}
      longitude: {required: true, description: "Decimal degrees, negative = west"}
      daily: {default: "temperature_2m_max,temperature_2m_min,precipitation_sum,wind_speed_10m_max"}
      timezone: {default: "auto"}
    response_shape: ".daily → parallel arrays of date, temp_max, temp_min, precip, wind"
    output_schema:
      type: object
      additionalProperties: false
      required: [timezone, days]
      properties:
        timezone:
          type: string
          description: "From response.timezone"
        days:
          type: array
          description: "One entry per day. Zip the parallel arrays under .daily into objects."
          items:
            type: object
            additionalProperties: false
            required: [date, temp_max_c, temp_min_c, precip_mm, wind_max_kmh]
            properties:
              date: {type: string, description: "From daily.time[i] (YYYY-MM-DD)"}
              temp_max_c: {type: number, description: "From daily.temperature_2m_max[i]"}
              temp_min_c: {type: number, description: "From daily.temperature_2m_min[i]"}
              precip_mm: {type: number, description: "From daily.precipitation_sum[i]"}
              wind_max_kmh: {type: number, description: "From daily.wind_speed_10m_max[i]"}
    output: schema
    output_transforms:
      - op: zip                             # parallel .daily arrays → one row-object per day
        from: [daily.time, daily.temperature_2m_max, daily.temperature_2m_min, daily.precipitation_sum, daily.wind_speed_10m_max]
        names: [date, temp_max_c, temp_min_c, precip_mm, wind_max_kmh]
        into: days
    output_renderer: table
    output_renderer_args:
      header: "Daily forecast"
      list_key: days
      columns:
        - {key: date, label: "Date", max_width: 12}
        - {key: temp_max_c, label: "Max °C", max_width: 8}
        - {key: temp_min_c, label: "Min °C", max_width: 8}
        - {key: precip_mm, label: "Rain mm", max_width: 8}
        - {key: wind_max_kmh, label: "Wind km/h", max_width: 10}
---

# Open-Meteo

Free weather API. No signup, no key, generous free-tier limits (10k calls/day for non-commercial use). Two endpoints worth knowing: a geocoding service (city name → lat/lon) and the forecast itself.

## Usage

### Look up coords for a city

```bash
curl -s 'https://geocoding-api.open-meteo.com/v1/search?name={CITY}&count=1' | jq '.results[0]'
```

`{CITY}` URL-encoded city name (`Tokyo`, `Seattle, US`, etc.). Response includes `latitude`, `longitude`, `country`, `timezone`.

### Current weather + hourly forecast

```bash
curl -s 'https://api.open-meteo.com/v1/forecast?latitude={LAT}&longitude={LON}&current_weather=true&hourly=temperature_2m,precipitation_probability,wind_speed_10m&timezone=auto'
```

### Daily forecast (next 7 days)

```bash
curl -s 'https://api.open-meteo.com/v1/forecast?latitude={LAT}&longitude={LON}&daily=temperature_2m_max,temperature_2m_min,precipitation_sum,wind_speed_10m_max&timezone=auto'
```

### Combined geocode-then-forecast (typical agent flow)

Always check that geocoding actually returned something before chaining into the forecast — `aurora illinois` (lowercase, no comma) sometimes returns zero results, and a missing-result `null` would silently produce a broken `latitude=&longitude=` URL.

```bash
GEO=$(curl -s "https://geocoding-api.open-meteo.com/v1/search?name={CITY}&count=1")
LAT=$(echo "$GEO" | jq -r '.results[0].latitude // empty')
LON=$(echo "$GEO" | jq -r '.results[0].longitude // empty')
if [ -z "$LAT" ] || [ -z "$LON" ]; then
  echo "no geocoding match for {CITY} — try a different spelling like 'Aurora, IL'" >&2
  exit 1
fi
curl -s "https://api.open-meteo.com/v1/forecast?latitude=${LAT}&longitude=${LON}&current_weather=true&timezone=auto"
```

If the city is ambiguous (multiple "Springfield"s), bump `count=10` on the geocoding call and inspect `.results[]` to disambiguate by `admin1`, `country`, or population before picking a row.

## Response shape

JSON. `current_weather` has `temperature`, `windspeed`, `winddirection`, `weathercode`. Hourly/daily come as parallel arrays under `hourly` / `daily`. `weathercode` follows WMO codes (0=clear, 1-3=mostly clear/cloudy, 45-48=fog, 51-67=rain, 71-77=snow, 80-99=showers/storms).

## Notes

- All lat/lon are decimal degrees, not DMS. Negative = south/west.
- `timezone=auto` makes timestamps local to the queried location — much easier to interpret than UTC.
- Commercial use requires the paid tier; everything above is free.
