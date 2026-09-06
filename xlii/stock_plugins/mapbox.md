---
id: mapbox
name: Mapbox
description: Forward geocode, driving/walking directions, and a static map URL (access token)
categories: [maps, navigation]
effect: read-only
trust: subscription
auth_type: query_param
auth_env_vars:
  - MAPBOX_TOKEN
actions:
  - id: set
    description: Store the Mapbox public access token in the vault
    params:
      MAPBOX_TOKEN: {secret: true, store: true, required: true, description: "pk.eyJ… token from account.mapbox.com"}
    output: raw
  - id: geocode
    description: Forward geocode a place name to lon/lat (Mapbox Places). Use these coords for directions or a later map pane.
    method: GET
    url: https://api.mapbox.com/geocoding/v5/mapbox.places/{query}.json
    params:
      query: {required: true, description: "Place text (1600 Pennsylvania Ave, Chicago, IL)"}
      limit: {default: "5", description: "Max features (1-10)"}
      access_token: {const: "${MAPBOX_TOKEN}"}
    response_shape: ".features[] → {place_name, center[lon,lat], id}"
    output_schema:
      type: object
      additionalProperties: false
      required: [features]
      properties:
        features:
          type: array
          description: "Up to 5 entries from response.features[]"
          items:
            type: object
            additionalProperties: false
            required: [name, lon, lat]
            properties:
              name: {type: string, description: "From features[i].place_name"}
              lon: {type: number, description: "From features[i].center[0]"}
              lat: {type: number, description: "From features[i].center[1]"}
              id: {type: string, description: "From features[i].id"}
    output: schema
    output_transforms:
      - {op: rename, list: features, from: place_name, to: name}
      - {op: rename, list: features, from: [center, 0], to: lon}
      - {op: rename, list: features, from: [center, 1], to: lat}
    output_renderer: table
    output_renderer_args:
      header: "Mapbox geocode"
      list_key: features
      columns:
        - {key: name, label: "Place", max_width: 40}
        - {key: lon, label: "Lon", max_width: 12}
        - {key: lat, label: "Lat", max_width: 12}
  - id: directions
    description: Route between two lon,lat points. Profile is driving, walking, or cycling.
    method: GET
    url: https://api.mapbox.com/directions/v5/mapbox/{profile}/{from};{to}
    params:
      from: {required: true, description: "Start as lon,lat (e.g. -87.6298,41.8781)"}
      to: {required: true, description: "End as lon,lat"}
      profile: {default: "driving", enum: ["driving", "driving-traffic", "walking", "cycling"], description: "Travel mode"}
      geometries: {const: "geojson"}
      overview: {const: "simplified"}
      access_token: {const: "${MAPBOX_TOKEN}"}
    response_shape: ".routes[0] → {distance_m, duration_s, geometry}"
    output_schema:
      type: object
      additionalProperties: false
      required: [distance_m, duration_s]
      properties:
        distance_m: {type: number, description: "From routes[0].distance (meters)"}
        duration_s: {type: number, description: "From routes[0].duration (seconds)"}
        code: {type: string, description: "From code (Ok on success)"}
    output: schema
    output_transforms:
      - {op: lift, from: [routes, 0]}
      - {op: rename, from: distance, to: distance_m}
      - {op: rename, from: duration, to: duration_s}
    output_renderer: text_template
    output_renderer_args:
      template: |-
        route {code}
          distance: {distance_m} m
          duration: {duration_s} s
---

# Mapbox

Data only in this cut — geocode and a route. A Leaflet / sat pane that *draws*
those coords is a later surface (closed HTML, not an open map iframe).

Token from [account.mapbox.com](https://account.mapbox.com) (`pk.eyJ…`).

```
/plugin subscribe mapbox
/plugin call mapbox.set
/plugin call mapbox.geocode query=Chicago, IL
/plugin call mapbox.directions from=-87.6298,41.8781 to=-87.6244,41.8827 profile=walking
```

`from` / `to` are **lon,lat** (Mapbox order), not lat,lon.

## Next

- Static image URL (style + lon,lat,zoom) can feed a later map pane.
- Isochrone / matrix stay out until someone needs them.
