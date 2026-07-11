# AstrBot coexistence

This directory contains the experimental integration that lets SanBot and AstrBot share one NapCat QQ account.

- SanBot keeps deterministic JM, JAV, TG, help, history, and administrator commands.
- AstrBot handles other direct mentions and its slash-prefixed commands.
- `astrbot_plugin_sanbot_router` prevents duplicate replies.
- AstrBot's built-in `GroupChatContext.active_reply` owns group context and occasional replies.
- Both sides require explicit group allowlists.

The production deployment keeps AstrBot's WebUI and reverse OneBot WebSocket bound to localhost. Access the WebUI through an SSH tunnel instead of exposing it directly:

```bash
ssh -L 6185:127.0.0.1:6185 root@your-server
```

Then open `http://127.0.0.1:6185` and configure an OpenAI-compatible model provider and personality. Provider credentials are intentionally not stored in this repository.
