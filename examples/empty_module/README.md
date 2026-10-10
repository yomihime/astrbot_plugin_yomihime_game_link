# Empty extension template

This inert package is the smallest valid B05 manifest envelope. It contains no
module declarations, commands, Tools, or runtime work. Copy it as a starting
point and add a module only after implementing the frozen manifest and factory
contracts from `yomihime_game_link_sdk`.

`Factory.create(services)` demonstrates the async factory ABI. Importing this
file creates no host registration, task, file, or network activity. The no-op
instance is a contract fixture, not evidence of AstrBot activation or host
compatibility.
