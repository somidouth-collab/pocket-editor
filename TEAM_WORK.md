# Working together on one hosted project

## Public IP detection is available first

Open Extensions → Team Project. The **Your Public IP — Host & Client** panel is at the top, before role selection. Click **Detect My Public IP** to display your public IPv4, then use Copy IP if needed. It works for either role before starting a host or joining a server and stays visible when Setup is collapsed. A client can still join using only the host invitation; its own public IP is not a required connection setting.

If a lookup fails, read the error shown directly below the button and click it again to retry. The plugin automatically tries alternative services if one has a DNS, connection, TLS, or invalid-response failure. IP discovery does not establish inbound reachability or open a listening port.

## Easy internet setup (temporary sessions)

The host can use Cloudflare Quick Tunnel to obtain an HTTPS relay address without a Cloudflare account, domain name, router forwarding rule, or client-side relay installation. The project remains on the host. Cloudflare carries the traffic and the app still requires the individual client keys.

1. Choose **Host a project**, select the shared folder, save/close project tabs, then **Start Host**. Keep Listen IP at `0.0.0.0`, or use `127.0.0.1` for relay-only local access.
2. Open **Easy Internet Setup…** from Setup or Project Copies.
3. Review the detected OS and shared folder, then click **Start Internet Sharing**. The helper installs missing cloudflared in your user tools folder, verifies its official release checksum, and starts the relay. There is no sudo, Windows service, router change, or system firewall change.
4. Wait for **HTTPS relay verified**. The plugin waits for the tunnel connection, then authenticates back to this host through the public address. An unverified address is not used in invitations.
5. Generate/export the named client invitation as usual. Its server address is filled with the verified HTTPS relay URL. The client only loads the file, selects a local project folder, and clicks Join Team.
6. Keep the host and relay running. Stop Sharing, Leave / Stop Host, or closing the editor stops the managed tunnel. Existing temporary invitation URLs then stop working.

Quick Tunnels are temporary development/session tunnels, have no uptime guarantee, and change hostname on every restart. Export a fresh invitation after changing the tunnel. For a stable long-lived address, configure a named Cloudflare Tunnel separately. The app's chat uses polling rather than SSE, and its per-file limit remains 50 MiB. No email-interactive `--allowed-mail` flag is used because the app's client is non-interactive; application client keys provide its access check.

### Two terminal commands instead

The setup panel displays commands using the current Python interpreter and project location. It can also save a `.sh` script on Linux/macOS or a `.ps1` script on Windows. Run these on the host computer, from the editor directory:

```sh
python3 tools/team_relay.py setup
python3 tools/team_relay.py start --port 8765
```

Use the actual port displayed by Start Host if it differs. Windows users can run the same commands with `py` or use the PowerShell commands generated in the panel. On Linux/macOS the saved script is executable and can be started with `./setup-team-relay.sh`. The saved script uses that host's interpreter/location; generate it on the computer where it will run. If a shell policy blocks a saved script, use the displayed commands directly rather than changing global policy.

The script automatically detects Linux/macOS/Windows and CPU architecture. It downloads from the official Cloudflare GitHub release, checks SHA-256, and installs into the user tools directory. `python3 tools/team_relay.py check` shows the platform and installation; `setup --force` replaces the user-local cached executable. The terminal Start command requires the app host already running and prints its HTTPS URL; copy that URL into Client-facing server URL if you launched the tunnel outside the GUI. Ctrl+C stops the terminal tunnel. No host or client keys are embedded in saved scripts or command-line arguments. Known team-token and named-tunnel-token environment settings are removed from the managed quick-tunnel process.

Linux setup has been exercised on this Debian host; Windows/macOS asset selection and generated commands are covered by tests, rather than native execution here. Network restrictions or a relay-provider outage can still prevent setup; the app reports the failure rather than exporting an unverified URL. Outbound HTTPS is needed for downloads, and cloudflared must be able to connect to its provider.

Sources: [Cloudflare Quick Tunnels](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/), [official downloads](https://developers.cloudflare.com/tunnel/downloads/).

## Start the server

1. Restart Pocket Editor after updating the plugin. Open Extensions → Team Project.
2. Select **Host a project**. Set Your name and a Group/room name. Choose the shared project folder and save/close its files before starting.
3. The default Listen IP is `0.0.0.0`, which accepts LAN/VPN connections on your machine's interfaces. Keep port `8765` or choose another unused port, then click **Start Host**. The app generates the administration key, shows the actual listening port, and suggests a local-network invitation address. `0.0.0.0` is a listen setting, not the address clients should enter. Choose `127.0.0.1` only for same-computer testing.
4. Open Project Copies → Client Keys & Invitations. Enter a teammate's name and click Generate Client Key.
5. Review the automatically suggested **Client-facing server URL**, or enter your own reachable address, such as `http://192.168.1.20:8765` for a LAN/VPN or an HTTPS address for a public server. This must be reachable from the client's computer; the host's `127.0.0.1` address only reaches that same computer.
   Use **Detect My Public IP** at the top of Team Project to look up your outbound public IPv4. The result also appears in the invitation controls. It runs only on demand and sends no project data or client key. Detection does not overwrite an existing server URL. If you have HTTPS configured for that public IP, set the HTTPS port (443 by default) and choose **Use HTTPS IP**. A domain name with valid HTTPS can also be entered manually.
6. Choose Export Client Invitation and save a `.team-invite` file. Send that file to the named client using your chosen file-transfer method. It contains their key, server URL, group/room name, client name, and project name. It does not contain the host's administration key or local filesystem paths. Generate and export a separate invitation for each client. Export the invitation while the newly generated key is available; only its hash is stored on the server.


The host computer keeps the complete shared project, room history, attachments, and backups of files replaced by client uploads. It must stay running while clients connect. For public internet use, place the host behind an HTTPS reverse proxy and give clients the HTTPS address, or connect everyone through a private VPN. The plugin does not configure routers, certificates, or firewall rules.

## Join from each client

1. Open Extensions → Team Project, choose **Join as client** (the default), and click **Load Invitation…**. Loading an invitation also selects Client mode.
2. Select the `.team-invite` file sent by the host. The plugin validates it and fills in the server URL, group/room, client name, and key.
3. Choose a local folder for your project copy. An empty folder is best for a first join. Existing different files are preserved and reported as conflicts. The host cannot specify this local folder in the invitation.
4. Click **Join Team**. Loading the file alone does not connect. On joining, the shared project copies into your chosen folder, and Project Explorer opens it when installed. Connection settings collapse to give the chat more room; use Setup to show them again. Details stays available from the toolbar.
5. Edit locally, save, and close the project's tabs. Use Project Copies → Send Changes to Server to publish edits or new files. Other clients use Pull Project to receive them. Updates use these explicit actions; typing is local.

You can still enter the address, room, and key manually if needed.

In **Project Copies → Client Keys & Invitations…**, the host chooses **read-only**, **member**, or **admin** access before generating a key. Read-only clients can read their allowed project files and send chat messages, but cannot publish changes or upload attachments. Members can publish changes. Admin invitations can also manage client keys and inspect history; give them only to trusted people. Existing invitations keep member access.

Optionally enter allowed project folders separated by commas, such as `documents, assets/images`. Blank means the entire shared project. The server filters manifests and rejects file requests outside those folders. Folder restrictions apply to project files; chat remains limited to the invitation’s room. Admin access covers the whole project and cannot be folder restricted.

To remove an unwanted client, select them and click **Remove Client…**, then confirm. The client disappears from the list and their old key is rejected on subsequent requests, including after host restart. Their downloaded copies remain on their computer. Generate a new invitation if you want them to join again. **Revoke Selected Key** also blocks access but keeps the client visible as revoked.

## Understanding Host and Client IP addresses and ports

Only the host needs a listening port. The client makes outgoing requests to the address in its invitation; its operating system selects a source port automatically. Clients do not need to enter their own IP/port or configure an incoming port-forwarding rule.

| Detail | Host | Client |
| --- | --- | --- |
| Setup role | Host a project | Join as client |
| Address to share | Reachable host LAN/VPN URL or public HTTPS URL | None; load the host invitation |
| Listening port | Usually 8765 for the built-in host | None |
| Local network IP | Suggested from the OS route | Suggested from the OS route |
| Public IP | Optional Detect My Public IP | Optional Detect My Public IP |
| Request source port | Visible for each named client in the host's client list | Automatic and changes between requests |

Click **Details** for the role, local IP suggestion, host address/port, and connection-check information. The client can see its own address and source port as observed by the host. A reverse proxy may appear as that peer address, so the plugin does not treat forwarded headers as a verified client IP.

On the host, Client Keys & Invitations → Refresh Clients shows each named client's last observed peer IP, automatic source port, and request time. This data is held in memory for the current host session. Key access marked Active means the key is unrevoked; it is not an online-presence claim.

On a LAN/VPN, the host address is filled in automatically when a suitable local IP can be suggested. Check that teammates can reach the selected network. For public internet access, keep using the configured HTTPS endpoint or a private VPN; address detection does not configure routers or bypass CGNAT. The HTTPS port in an invitation can differ from the built-in host's listening port when a reverse proxy is used.

## Conflicts and file limits

If Alice and Bob both change a file, Alice can send her version first. Bob's later upload is rejected as a conflict. His local file stays intact, and the plugin creates a recovery copy under the client data directory’s `client-conflicts` folder. The sync result shows that folder. Each recovery file has a JSON sidecar containing its original project path and baseline version. Bob can compare it with the current host version and reconcile the changes. To reset a conflicting file to the server version, first move Bob's file out of his project folder, Pull Project, then apply the changes to that new copy and send again.

The host's selected folder is authoritative. Host-side disk edits appear in subsequent pulls. Close host file tabs before allowing clients to replace those files, and save local edits before syncing. Deletions are performed on the host; pulling does not delete extra local files, and Send Changes does not publish deletions. Automatic merging and simultaneous live typing are not implemented.

A shared project is limited to 5000 files and 512 MiB total, with 50 MiB per file. Symbolic links, `.git`, `.hg`, `.svn`, `.ssh`, `.aws`, virtual environments, `node_modules`, `__pycache__`, `.env*`, `*.pem`, `*.key`, `id_rsa`, and `id_ed25519` are excluded. Choose a dedicated project folder containing the files you intend to share. Attachment storage has a separate 1 GiB limit. Current project content and retained versions share another 1 GiB limit.

## Host file store and versioned synchronization

The host maintains `project.sqlite3`, a SQLite index of project path, SHA-256, size, version number, author, and change time. Saved host edits are indexed on the next project operation; unsaved drafts are not shared. Immutable file contents live once per SHA-256 in `project-versions`, including the currently indexed version. Identical contents across paths share the same stored blob. Historical metadata records creates, updates, and host-side deletions. History is retained until host storage fills; automatic 30-day cleanup is not implemented.

Updated clients negotiate protocol 2, send their last-known path/hash/version manifest, and receive changed entries plus removed paths. File contents are downloaded only when their checksums differ. Each upload includes the baseline version and hash. A stale revision is rejected even if the host file has returned to an earlier checksum. Versions and client baselines survive restarts. Legacy clients continue to use hash-based checks; upgrade both ends to use version checks.

The client validates the complete incoming manifest, paths, versions, file counts, sizes, and checksums before applying it. Symbolic links and paths outside its selected folder are rejected. A failed download checksum does not replace the local file.

This upgrade provides the file-store foundation, revision conflicts, invitation roles, folder restrictions, and client removal. Resumable chunk uploads, push notifications, automatic backups, per-client quotas/rate limits, file locks, and a history-restore GUI remain future work. Project pulls and sends remain explicit user actions; no live editing or structured-record database is introduced.

## Team chat and media files

Use Group Chat to send text. Enter or Ctrl+Enter sends; Shift+Enter inserts a new line. Incoming and outgoing messages have separate cards, with sender names, timestamps, and date separators. Shared attachments can be saved from their conversation cards or the Shared Files tab. Attach File shares an audio recording, video, image, document, or other file up to 50 MiB. Teammates open Shared Files, select the attachment, and choose Save Selected Attachment. Files are not automatically opened or executed. Audio/video support means file exchange, not live calls or built-in recording/playback.

Messages and attachments remain on the host across restarts. A newly joined client reads room history from that host. Project copies are stored in each client's chosen folder. Sync baselines and server chat/history data use `$XDG_DATA_HOME/pocket-editor/team-chat`, or `~/.local/share/pocket-editor/team-chat` by default. Keep this data directory outside the shared project. Back up both the shared project folder and host data directory. The host key is not saved automatically: reuse it manually after restarting if desired. Existing unrevoked client keys remain valid because the host retains their hashes.

## Optional dedicated server

From the editor directory, set a strong `POCKET_TEAM_TOKEN` in the server environment and run:

```sh
python3 -B -m pocket_editor_plugins._team_chat --bind 0.0.0.0 --port 8765 --storage /path/outside/project/team-data --project /path/to/shared-project --room project
```

The host can join from Pocket Editor using the administration token and manage client keys there. Keep that terminal process running. Use a LAN/VPN or provide HTTPS at a reverse proxy for internet clients.

## Independent Git Project plugin

Open Extensions → Git Project, or Project Explorer → Actions → Git Project. This is a separate plugin and can work without Team Project or Project Explorer. Browse selects any project folder. Clone downloads an existing repository; Choosing a folder automatically initializes Git if absent and registers its repository root. Set Git Folder prepares a typed folder too. Use Remote URL → Set Origin to configure a remote. Save edits, Refresh, select changed files, and Commit Selected. Push publishes to the upstream; a first push can set origin as upstream automatically. Pull receives teammates' commits and only fast-forwards. Git authentication and author identity use your existing Git configuration. Close project tabs before Pull.

Use **Git Project → Settings…** to set this repository’s author name, email, origin URL, line endings, file-mode tracking, and commit signing. Settings are saved to the repository config and reload on reopening the panel. Blank fields and Inherit use inherited/global defaults. The panel displays the effective author and uses existing SSH keys or credential helpers for authentication.

Other plugins may call `git_project.ensure_repository(folder)` to prepare/register Git without opening a window, then `git_project.get_repository()` to obtain the registered working-tree root. `git_project.get_project()` is a compatibility alias. `git_project.open_project(folder)` also opens the Git tools. Selecting a subfolder of an existing repository uses its root and does not create a nested repository. Team Project likewise exposes `team_project.open_project(folder)` for launching hosted teamwork with a selected folder.

## Public IP detection and actual connectivity

Public IP lookup tries [ipify](https://www.ipify.org/), [AWS CheckIP](https://checkip.amazonaws.com/), then [Cloudflare icanhazip](https://icanhazip.com/) over HTTPS. Each request uses a four-second timeout and a bounded response. Only the first valid public IPv4 is used; TLS verification remains enabled. The same lookup and result are used by the top panel, Details, and the invitation window. The built-in host listens on its selected bind address and port. Detecting an outbound public address does not test inbound reachability, open firewall/router ports, install TLS, or bypass CGNAT. A VPN or proxy can change which outbound address is detected.

For internet clients, use an HTTPS reverse proxy with a valid certificate and route it to the host. The address and port placed in an invitation must reach that HTTPS endpoint. For private networking, use a LAN/VPN address shared by the team instead. Do not assume an IP lookup alone makes a server reachable.

Host mode now includes direct **Install Relay**, **Start Internet Sharing**, and **Stop Sharing** buttons in the main Internet Sharing panel. They perform the setup/start commands without a terminal or extra dialog. The panel stays visible when Setup collapses after starting the host; Start Internet Sharing becomes available once the host is running. Installation is also automatic when starting sharing if the tool is missing.

## Interactive continuous test on one computer

Run `python3 -u -B tools/team_live_test.py` from the editor directory to open separate Host and Client windows plus a test-control window. It creates an isolated temporary project, issues a demo client key, joins locally, and checks chat in both directions, project pull/publish, and binary attachment transfers. After the first local pass it starts the HTTPS relay and reconnects the client through it. Checks then repeat with a 30-second interval until Stop Test, window closure, or Ctrl+C/SIGINT.

You can type messages or attach files in either demo window while it runs. Keep manual project files outside `_automation`, which is reserved for automated heartbeat files. The controller offers Check Now, Pause Checks, Test Internet Relay, Use Local Connection, and Stop Test. It shows the temporary folder containing `test.log`, `status.json`, and both project copies. Logs and demo files remain after stopping; normal projects and editor settings are not used.

For a finite local smoke test without public sharing, run `python3 -u -B tools/team_live_test.py --local-only --cycles 1`. The default continuous mode uses a temporary authenticated public tunnel for the demo project only.
