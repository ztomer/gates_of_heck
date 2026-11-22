* `goh cache-root` (`lib/cache_tier.py`, GOH.CACHEEVICT): the removable cache tier is decided at use
  time. `/Volumes/<name>/cache.noindex` counts only when the volume is a mount point, its
  `.goh-cache-volume` marker names the volume's own UUID, it is writable and above its free floor;
  otherwise the internal root, with a one-line reason. The mount path is never written into static
  config and never created, so an unplugged drive cannot be squatted by a `mkdir -p`.
