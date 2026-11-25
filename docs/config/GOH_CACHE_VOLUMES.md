# GOH_CACHE_VOLUMES
Area: cache-tiers
Kind: setting
Order: 20
Default: `/Volumes`

Where the resolver looks for mounted volumes. The suite points it at a path that does not exist
(`tests/suite_home.py` CACHE_TIER), so no test reaches a real drive.
