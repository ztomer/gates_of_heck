# GOH_REMOTE_CONTAINER
Area: remote-worker
Kind: setting
Order: 90
Default: `goh-remote-worker`

The long-lived worker container's name (`--restart unless-stopped`); each shard is a `docker exec`
into it. A container of this name on another image is replaced.
