# srsly-discord-mod
discord bot for srsly

To add as a systemd `.service`:
- Create environments file at `/etc/systemd/system/srsly_discord.env`

```
PATH={path_to_python_venv}/bin
DISCORD_BOT_TOKEN="{your token}"
```

- Create service file at `/etc/systemd/system/srsly_discord.service`

```
[Unit]
Description=srsly-discord
After=network.target

[Service]
Type=simple
User={your user}
WorkingDirectory={path_to_this_repo}
EnvironmentFile=/etc/systemd/system/srsly_discord.env
ExecStart={path_to_python_venv}/bin/python main.py
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
```
