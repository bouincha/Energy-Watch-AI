# Put EnergyWatch AI online with Cloudflare Tunnel (free, no card)

Cloudflare Tunnel publishes the app running on your PC at a public `https://….trycloudflare.com`
address. You don't need a Cloudflare account, a credit card or Docker.

> Cloudflare Pages and Workers can't host this app: it is a Python server with AI agents.
> The tunnel runs it on your PC and Cloudflare carries the traffic.

## One-time preparation
1. Install **Python 3.12** from https://www.python.org/downloads/ and tick
   **"Add python.exe to PATH"**.
2. In the project folder, copy `.env.example` to `.env`, open it with Notepad and set:
   ```
   GROQ_API_KEY=your-groq-key          (free at https://console.groq.com, optional)
   ADMIN_API_KEY=choose-a-password     (important when the link is public)
   ```

## Every time you want to go online
1. Double-click **`start-online.bat`** in the project folder.
2. Wait. The first run takes a few minutes, because it installs the libraries and downloads
   Cloudflare Tunnel. Two windows open:
   - **"server"**: the app itself. Keep it open.
   - **"online with Cloudflare Tunnel"**: after a few seconds it prints a box containing your link:
     ```
     Your quick Tunnel has been created! Visit it at:
     https://some-random-words.trycloudflare.com
     ```
3. Open that link on any device, or send it to your teacher.
4. In the dashboard, click ⚙ **Settings** and enter your `ADMIN_API_KEY`, so you can approve
   actions and run the incident drill.

To go offline, close both windows, or press **Ctrl+C** in each.

## Good to know
- The link only works **while your PC is on and both windows are open**.
- The link **changes each time** you start the tunnel, so send the new one.
- If Windows SmartScreen warns about the `.bat` file, click **More info → Run anyway**.
- To use the app only on your PC (no public link), double-click **`start-local.bat`**.
- Check the live link from another PC:
  `python scripts/smoke_test.py https://your-link.trycloudflare.com`

## Troubleshooting

| Problem | Fix |
|---|---|
| "Python 3.12 was not found" | Install Python 3.12 (see above), then run the file again |
| The server window shows a red error | Send a screenshot of it; often a typo in `.env` |
| No link appears | Check your internet connection; close and run `start-online.bat` again |
| "Missing or invalid X-API-Key" when approving | Enter your `ADMIN_API_KEY` in ⚙ Settings |
