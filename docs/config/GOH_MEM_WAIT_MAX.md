# GOH_MEM_WAIT_MAX
Area: pushes
Kind: setting
Order: 530
Default: `600`

The memory wait's bound in seconds: on expiry the gate or suite says the memory is still low and
starts anyway, so a reading can never wedge every gate on the machine. The wait is charged to no
step.
