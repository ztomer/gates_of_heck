# GOH_CACHE_VOLUME_UUID
Area: cache-tiers
Kind: setting
Order: 10
Default: unset

Pins the removable cache volume: its marker's `volume_uuid=` must equal this as well as the volume's
own UUID. Unset, any mounted volume whose marker names its own UUID qualifies.
