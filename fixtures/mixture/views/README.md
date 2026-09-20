# Mix01 adapter schema fixtures (P13)

Authored synthetic records shaped like the *expected* upstream schemas for each
mix01 family. Content is invented; field names follow the registry
(`recipes/mixtures/mix01_views.yaml`) and are **not live-verified** against the
real datasets. These fixtures prove adapter logic (required fields, English and
context filters, organic/synthetic separation, explicit rejections) — they never
prove live compatibility. Every view stays `live_verified: false` until a real
pilot tests its adapter against live rows.
