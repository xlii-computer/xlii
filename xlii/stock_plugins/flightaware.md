---
id: flightaware
name: FlightAware AeroAPI
description: Flight status, schedules, airport and operator metadata via the FlightAware AeroAPI. Auth via x-apikey header.
categories: [travel, flights]
effect: read-only
trust: subscription
auth_type: header
auth_env_vars:
  - FLIGHTAWARE_API_KEY
actions:
  - id: set
    description: Store the FlightAware AeroAPI key in the vault
    params:
      FLIGHTAWARE_API_KEY: {secret: true, store: true, required: true, description: "AeroAPI key"}
    output: raw
  - id: flight_status
    description: Look up flight status, schedule, and live position by flight ident (e.g. UA1, BA286)
    method: GET
    url: https://aeroapi.flightaware.com/aeroapi/flights/{ident}
    headers:
      x-apikey: "${FLIGHTAWARE_API_KEY}"
    params:
      ident: {required: true, description: "Flight ident (IATA or ICAO; e.g. UA1, UAL1, BA286)"}
    response_shape: ".flights[] → {ident, status, scheduled_off, estimated_off, actual_off, scheduled_on, estimated_on, actual_on, origin, destination, aircraft_type, registration, route}"
    output_schema:
      type: object
      additionalProperties: false
      required: [legs]
      properties:
        legs:
          type: array
          description: "From response.flights[]. Up to 5 entries — typically recent + upcoming legs of the same ident."
          items:
            type: object
            additionalProperties: false
            required: [ident, status, origin, destination, scheduled_off, scheduled_on, aircraft]
            properties:
              ident: {type: string, description: "From flights[i].ident"}
              status: {type: string, description: "From flights[i].status"}
              origin: {type: string, description: "Concatenate flights[i].origin.code_iata and .name as 'CODE — Name'"}
              destination: {type: string, description: "Concatenate flights[i].destination.code_iata and .name as 'CODE — Name'"}
              scheduled_off: {type: string, description: "From flights[i].scheduled_off"}
              scheduled_on: {type: string, description: "From flights[i].scheduled_on"}
              aircraft: {type: string, description: "From flights[i].aircraft_type, or empty if null"}
    output: schema
    output_transforms:
      - {op: concat, list: flights, from: [origin.code_iata, origin.name], to: origin, sep: " — "}
      - {op: concat, list: flights, from: [destination.code_iata, destination.name], to: destination, sep: " — "}
      - {op: rename, list: flights, from: aircraft_type, to: aircraft}
    output_renderer: message_list
    output_renderer_args:
      header: "Flight legs"
      list_key: flights
      item_template: "  · {ident} — {status} · {aircraft}\n      {origin} → {destination}\n      depart {scheduled_off}  ·  arrive {scheduled_on}"
      empty_message: "(no flights matched)"
  - id: flight_search
    description: Search recent and upcoming flights by ident, route, or date range
    method: GET
    url: https://aeroapi.flightaware.com/aeroapi/flights/search
    headers:
      x-apikey: "${FLIGHTAWARE_API_KEY}"
    params:
      query: {required: true, description: "Search query — supports -idents, -origin, -destination, -airline, -date filters. e.g. '-idents UA1' or '-origin KSFO -destination KJFK'"}
      max_pages: {description: "Max pages to fetch (default 1)"}
    response_shape: ".flights[] → {ident, status, scheduled_off, origin, destination, aircraft_type}"
    output_schema:
      type: object
      additionalProperties: false
      required: [flights]
      properties:
        flights:
          type: array
          description: "Up to 25 entries from response.flights[]"
          items:
            type: object
            additionalProperties: false
            required: [ident, status, origin, destination, scheduled_off, aircraft]
            properties:
              ident: {type: string, description: "From flights[i].ident"}
              status: {type: string, description: "From flights[i].status"}
              origin: {type: string, description: "From flights[i].origin.code_iata or .code"}
              destination: {type: string, description: "From flights[i].destination.code_iata or .code"}
              scheduled_off: {type: string, description: "From flights[i].scheduled_off"}
              aircraft: {type: string, description: "From flights[i].aircraft_type, or empty"}
    output: schema
    output_renderer: table
    output_renderer_args:
      header: "Flight search"
      list_key: flights
      columns:                       # dotted keys read the nested origin/destination objects directly
        - {key: ident, label: "Flight", max_width: 10}
        - {key: status, label: "Status", max_width: 14}
        - {key: origin.code_iata, label: "From", max_width: 6}
        - {key: destination.code_iata, label: "To", max_width: 6}
        - {key: aircraft_type, label: "Acft", max_width: 8}
        - {key: scheduled_off, label: "Off (UTC)", max_width: 22}
  - id: airport_info
    description: Airport metadata by ICAO or IATA code
    method: GET
    url: https://aeroapi.flightaware.com/aeroapi/airports/{id}
    headers:
      x-apikey: "${FLIGHTAWARE_API_KEY}"
    params:
      id: {required: true, description: "Airport ID — ICAO (preferred, e.g. KSFO) or IATA (e.g. SFO)"}
    response_shape: "{name, code_iata, code_icao, city, state, country_code, latitude, longitude, timezone, elevation}"
    output_schema:
      type: object
      additionalProperties: false
      required: [name, code_iata, code_icao, city, country, latitude, longitude, timezone, elevation_ft]
      properties:
        name: {type: string, description: "From response.name"}
        code_iata: {type: string, description: "From response.code_iata"}
        code_icao: {type: string, description: "From response.code_icao"}
        city: {type: string, description: "Concatenate response.city + ', ' + response.state if state present, otherwise just city"}
        country: {type: string, description: "From response.country_code"}
        latitude: {type: number, description: "From response.latitude"}
        longitude: {type: number, description: "From response.longitude"}
        timezone: {type: string, description: "From response.timezone"}
        elevation_ft: {type: integer, description: "From response.elevation, default 0"}
    output: schema
    output_transforms:
      - {op: concat, from: [city, state], to: city, sep: ", "}   # "San Francisco, CA" (state dropped if absent)
      - {op: rename, from: country_code, to: country}
      - {op: rename, from: elevation, to: elevation_ft}
    output_renderer: text_template
    output_renderer_args:
      template: |-
        {name} ({code_iata} / {code_icao})
          location:    {city}, {country}
          coords:      {latitude}, {longitude}
          timezone:    {timezone}
          elevation:   {elevation_ft:,} ft
  - id: airport_scheduled_departures
    description: Scheduled departures from an airport in a time window (default = next 12h)
    method: GET
    url: https://aeroapi.flightaware.com/aeroapi/airports/{id}/flights/scheduled_departures
    headers:
      x-apikey: "${FLIGHTAWARE_API_KEY}"
    params:
      id: {required: true, description: "Airport ID (ICAO preferred — e.g. KSFO)"}
      start: {description: "ISO 8601 start time (default: now)"}
      end: {description: "ISO 8601 end time (default: now+12h)"}
    response_shape: ".scheduled_departures[] → {ident, scheduled_out, origin, destination, aircraft_type}"
    output_schema:
      type: object
      additionalProperties: false
      required: [flights]
      properties:
        flights:
          type: array
          description: "Up to 25 entries from response.scheduled_departures[]"
          items:
            type: object
            additionalProperties: false
            required: [ident, scheduled, origin, destination, aircraft]
            properties:
              ident: {type: string, description: "From scheduled_departures[i].ident"}
              scheduled: {type: string, description: "From scheduled_departures[i].scheduled_out"}
              origin: {type: string, description: "From scheduled_departures[i].origin.code_iata or .code"}
              destination: {type: string, description: "From scheduled_departures[i].destination.code_iata or .code"}
              aircraft: {type: string, description: "From scheduled_departures[i].aircraft_type, or empty"}
    output: schema
    output_renderer: table
    output_renderer_args:
      header: "Scheduled departures"
      list_key: scheduled_departures
      columns:
        - {key: ident, label: "Flight", max_width: 10}
        - {key: origin.code_iata, label: "From", max_width: 6}
        - {key: destination.code_iata, label: "To", max_width: 6}
        - {key: aircraft_type, label: "Acft", max_width: 8}
        - {key: scheduled_out, label: "Scheduled (UTC)", max_width: 22}
  - id: airport_scheduled_arrivals
    description: Scheduled arrivals at an airport in a time window (default = next 12h)
    method: GET
    url: https://aeroapi.flightaware.com/aeroapi/airports/{id}/flights/scheduled_arrivals
    headers:
      x-apikey: "${FLIGHTAWARE_API_KEY}"
    params:
      id: {required: true, description: "Airport ID (ICAO preferred — e.g. KSFO)"}
      start: {description: "ISO 8601 start time (default: now)"}
      end: {description: "ISO 8601 end time (default: now+12h)"}
    response_shape: ".scheduled_arrivals[] → {ident, scheduled_in, origin, destination, aircraft_type}"
    output_schema:
      type: object
      additionalProperties: false
      required: [flights]
      properties:
        flights:
          type: array
          description: "Up to 25 entries from response.scheduled_arrivals[]"
          items:
            type: object
            additionalProperties: false
            required: [ident, scheduled, origin, destination, aircraft]
            properties:
              ident: {type: string, description: "From scheduled_arrivals[i].ident"}
              scheduled: {type: string, description: "From scheduled_arrivals[i].scheduled_in"}
              origin: {type: string, description: "From scheduled_arrivals[i].origin.code_iata or .code"}
              destination: {type: string, description: "From scheduled_arrivals[i].destination.code_iata or .code"}
              aircraft: {type: string, description: "From scheduled_arrivals[i].aircraft_type, or empty"}
    output: schema
    output_renderer: table
    output_renderer_args:
      header: "Scheduled arrivals"
      list_key: scheduled_arrivals
      columns:
        - {key: ident, label: "Flight", max_width: 10}
        - {key: origin.code_iata, label: "From", max_width: 6}
        - {key: destination.code_iata, label: "To", max_width: 6}
        - {key: aircraft_type, label: "Acft", max_width: 8}
        - {key: scheduled_in, label: "Scheduled (UTC)", max_width: 22}
  - id: operator_info
    description: Airline / operator metadata by ICAO or IATA code
    method: GET
    url: https://aeroapi.flightaware.com/aeroapi/operators/{id}
    headers:
      x-apikey: "${FLIGHTAWARE_API_KEY}"
    params:
      id: {required: true, description: "Operator ID — ICAO (e.g. UAL) or IATA (e.g. UA)"}
    response_shape: "{name, icao, iata, callsign, country, location}"
    output_schema:
      type: object
      additionalProperties: false
      required: [name, icao, iata, callsign, country, location]
      properties:
        name: {type: string, description: "From response.name"}
        icao: {type: string, description: "From response.icao"}
        iata: {type: string, description: "From response.iata, or empty"}
        callsign: {type: string, description: "From response.callsign, or empty"}
        country: {type: string, description: "From response.country, or empty"}
        location: {type: string, description: "From response.location, or empty"}
    output: schema     # raw operator body is already flat {name, icao, iata, callsign, country, location}
    output_renderer: text_template
    output_renderer_args:
      template: |-
        {name} ({iata} / {icao})
          callsign:    {callsign}
          country:     {country}
          based:       {location}
---

# FlightAware AeroAPI

Flight status by ident (e.g. `UA1`), airport schedules, operator and airport metadata.
Modern HTTPS-only API with a free tier (~500 queries/month at the time of writing) and
paid tiers for higher limits. The free tier is enough for personal use; check
https://flightaware.com/aeroapi/portal/ for current pricing.

## Auth

Run `flightaware.set`. The key is a form secret, sent as `x-apikey` at
call time — never seen by the model.

## Usage

### By flight ident (most common)

```bash
curl -s -H "x-apikey: $FLIGHTAWARE_API_KEY" \
  "https://aeroapi.flightaware.com/aeroapi/flights/UA1"
```

Returns `.flights[]` array with status, scheduled vs actual times, origin/destination,
aircraft type and registration, route string. Multiple entries when the ident has
recent + upcoming legs.

### Search by route or date

```bash
curl -s -H "x-apikey: $FLIGHTAWARE_API_KEY" \
  "https://aeroapi.flightaware.com/aeroapi/flights/search?query=-origin%20KSFO%20-destination%20KJFK"
```

Query syntax supports `-idents`, `-origin`, `-destination`, `-airline`,
`-date`, `-aircrafttype`. URL-encode spaces.

### Airport schedule

```bash
curl -s -H "x-apikey: $FLIGHTAWARE_API_KEY" \
  "https://aeroapi.flightaware.com/aeroapi/airports/KSFO/flights/scheduled?type=departures"
```

### Airport / operator metadata

```bash
curl -s -H "x-apikey: $FLIGHTAWARE_API_KEY" \
  "https://aeroapi.flightaware.com/aeroapi/airports/KSFO"

curl -s -H "x-apikey: $FLIGHTAWARE_API_KEY" \
  "https://aeroapi.flightaware.com/aeroapi/operators/UAL"
```

## Response shape (flight_status)

```json
{
  "flights": [
    {
      "ident": "UAL1",
      "ident_iata": "UA1",
      "ident_icao": "UAL1",
      "status": "Scheduled",
      "scheduled_off": "2026-05-04T07:30:00Z",
      "estimated_off": "2026-05-04T07:35:00Z",
      "actual_off": null,
      "scheduled_on": "2026-05-04T18:45:00Z",
      "estimated_on": "2026-05-04T18:50:00Z",
      "origin": { "code": "KEWR", "code_iata": "EWR", "name": "Newark Liberty Intl" },
      "destination": { "code": "EGLL", "code_iata": "LHR", "name": "London Heathrow" },
      "aircraft_type": "B788",
      "registration": "N12345",
      "route": "EWR..LHR"
    }
  ]
}
```

## Notes

- **ICAO vs IATA codes.** FlightAware accepts both, but ICAO (`KSFO`, `EGLL`,
  `UAL`) is the canonical form internally. For lookups that fail with IATA,
  retry with ICAO.
- **"On time" judgment.** Compare `scheduled_off` vs `actual_off` (departed) or
  `scheduled_on` vs `actual_on` (arrived). Industry standard is a 15-minute
  threshold for "on time."
- **Live position data** isn't returned by `flight_status` — that's a separate
  `flights/{id}/track` endpoint not yet wired into this manifest. Add an
  action if you need it.
- **Rate limits.** The free tier is ~500 calls/month. Cache aggressively in
  application code if you're polling.
