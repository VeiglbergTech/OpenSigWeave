# OpenSigWeave

OpenSigWeave is a centralized, self-hosted email signature manager designed to integrate an OIDC identity provider with **Rspamd / Mailcow** (Mail Filtering). 

Instead of forcing users to configure signatures in their individual mail clients (Outlook, Apple Mail, mobile devices, etc.), OpenSigWeave injects rich HTML signatures at the gateway level. It dynamically pulls live user attributes (Phone, Title, Name) from Authentik and appends them to outgoing emails seamlessly. Signature variables are rendered in a sandboxed template environment with automatic HTML escaping.

## ✨ Key Features
* **Gateway-Level Injection:** Signatures are applied by the mail server, meaning they work identically across all devices and email clients.
* **OIDC SSO:** Login uses the provider's OIDC discovery document and an explicitly configured HTTPS `OIDC_REDIRECT_URI` that must exactly match the redirect URI registered at the identity provider. The callback URL is never derived from the incoming Host header. The current live employee-attribute lookup and logout integration still use Authentik and are being separated in the ongoing refactor. The application should be reached through an HTTPS reverse proxy; its session cookie is Secure.
* **Rich Visual Editor:** A custom UI built with Tailwind CSS, Alpine.js, and Quill.js allows users and admins to design signatures visually or via raw HTML.
* **Advanced HTML Import:** Sanitize and import complex layouts from external generators with an intelligent visual editor lock that protects table-based signatures from accidental formatting loss.
* **Domain-Level Master Templates:** Enforce a corporate signature standard across an entire domain.
* **User Overrides:** Allow specific users to customize their signatures, or administratively lock them out.
* **Intelligent Reply-Chain Detection:** An experimental Lua engine detects when an email is a reply and cleanly injects the signature *above* the quoted history.
* **Whitespace Normalization (The "Peeler"):** Aggressively strips rogue HTML spaces, empty paragraphs, and quoted-printable soft-breaks from user emails to ensure perfectly crisp spacing around injected signatures.
* **Plain Text Conversion:** Automatically intercepts flat `text/plain` emails and upgrades them to `multipart/alternative` containers so rich HTML signatures can be injected.
* **Mobile Signature Assassin:** Automatically detects and strips default mobile sign-offs (e.g., "Sent from my iPhone" or "Get Outlook for iOS") before applying the professional corporate signature.
* **The "Kill Switch":** Easily disable signatures entirely for specific service accounts (e.g., `noreply@` or `billing@`).
* **Branding:** Easy white label branding options for logo, title and title color via simple, persistent files.
* **Rspamd Integration:** Signature mutation is performed by a Rspamd adapter. The deployed Rspamd processing order must be verified to ensure injection happens before DKIM signing; no blanket deliverability guarantee is made.

---
## 📸 UI Images

<table width="100%">
  <tr>
    <td align="center">
      <b>User UI</b><br>
      <img src="https://hatsthings.com/OpenSigWeave/UserUI.png" alt="User UI">
    </td>
    <td align="center">
      <b>Admin UI</b><br>
      <img src="https://hatsthings.com/OpenSigWeave/AdminUI.png" alt="Admin UI">
    </td>
  </tr>
</table>

<table width="100%">
  <tr>
    <td align="center">
      <b>Advanced HTML Import</b><br>
      <img src="https://hatsthings.com/OpenSigWeave/AdvancedHTMLImport.png" alt="Advanced HTML Import">
    </td>
    <td align="center">
      <b>Domain Settings</b><br>
      <img src="https://hatsthings.com/OpenSigWeave/DomainSettingsUI.png" alt="Domain Settings UI">
    </td>
    <td align="center">
      <b>User Overrides</b><br>
      <img src="https://hatsthings.com/OpenSigWeave/UserOverridesUI.png" alt="User Overrides UI">
    </td>
  </tr>
</table>

---

## 📧 Supported Mail Clients (Reply Injection)
The current integration targets outbound messages. Actual behavior depends on the MIME structure and the configured Rspamd processing pipeline; validate each supported client with integration tests. 

However, because there is no universal web standard for how email clients format reply chains, OpenSigWeave's **Experimental Reply Injection** uses specific Regex patterns to find the exact HTML line where the "quoted history" begins. 

The Lua engine officially supports injecting signatures cleanly into replies sent from the following clients:
* **Thunderbird:** Desktop and Mobile.
* **Self-Hosted Webmail:** Horde, Roundcube, SOGo (Mailcow default), and Nextcloud Mail.
The Lua engine also theoretically supports injecting signatures into replies sent from these clients, but has not been verified:
* **Microsoft Outlook:** Classic Desktop, New Desktop, OWA (Web), and iOS/Android Mobile Apps.
* **Apple Mail:** iOS, iPadOS, and macOS native mail apps.
* **Gmail:** Web, iOS, and Android Mobile Apps.
* **Yahoo Mail & Open-Xchange:** Web clients.

*Note: If a user sends a reply from an unrecognized client, the Lua engine acts defensively. It will safely abort the inline injection and append the signature to the absolute bottom of the email rather than risk placing it in the middle of a sentence.*

---

## 💡 Usage Tips
* **External Builders:** For the best results with multi-column layouts, it's recommended to design your signature in an online generator and use the HTML import feature. Review imported markup before saving.
* **Variable Injection:** When using external builders, simply type {{ first_name }}, {{ title }}, etc., directly into their form fields. When you import the HTML into OpenSigWeave, these will become dynamic placeholders.
* **Table Support:** If you paste a table into the "Advanced HTML" editor, the Visual Editor will lock to protect your layout. To return to the Visual Editor, you must clear the signature or remove the `<table>` tags.

---

## 🚀 1. Deploying the Web App

OpenSigWeave is a FastAPI application with PostgreSQL as the recommended new-install database. SQLite remains supported for legacy installations; Alembic manages schema migrations. 

### Prerequisites
* Python 3.10+
* Docker Engine and Docker Compose plugin (for the bundled PostgreSQL service)
* An active Authentik deployment
* An active Rspamd/Mailcow deployment
* A Reverse Proxy (Nginx, Traefik, Caddy, etc.) for SSL termination

### Installation
1. Clone the repository to your application server.
   ```bash
   sudo mkdir -p /opt/opensigweave
   sudo chown "$USER":"$USER" /opt/opensigweave
   git clone https://github.com/TylerHats/OpenSigWeave.git /opt/opensigweave
   cd /opt/opensigweave
   ```
2. Create and activate a Python virtual environment.
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```
3. Install the required Python modules.
   ```bash
   pip install -r requirements.txt
   ```
4. Prepare your environment variables. Generate separate random values for `SECRET_KEY`, `POSTGRES_PASSWORD` and `ENGINE_API_KEY`; all must be kept private and must never be committed.
   ```bash
   cp .env.example .env
   python -c "import secrets; print(secrets.token_urlsafe(48))"
   nano .env
   ```
   Set `SECRET_KEY` and `ENGINE_API_KEY` to separate generated values. Generate `POSTGRES_PASSWORD` as well, then put the same URL-safe value in both `POSTGRES_PASSWORD` and the password portion of `DATABASE_URL`. The application refuses to start with placeholder secrets or mismatched PostgreSQL credentials. Serve the web application over HTTPS: session cookies are configured as Secure.
5. Start the bundled PostgreSQL service:
   ```bash
   docker compose up -d --wait postgres
   ```
   PostgreSQL is bound to localhost only. If you use an external database or legacy SQLite, adjust `DATABASE_URL` accordingly. Signature HTML versions are stored below `SIGNATURE_STORAGE_PATH`; back up this directory together with the PostgreSQL volume so file hashes and metadata remain consistent.

6. Apply the database schema using Alembic. Run this after configuring `DATABASE_URL` and before starting the web application:
   ```bash
   alembic upgrade head
   ```
   The initial migration detects existing `domains` and `user_overrides` tables so existing SQLite data is not recreated or dropped.

7. Prepare your branding (Optional).
   ```bash
   cp branding/logo.example.png branding/logo.png
   cp branding/settings.example.json branding/settings.json
   ```
8. Start the application to verify it works. (See the systemd section below for running as a permanent service).
   ```bash
   uvicorn main:app --host 127.0.0.1 --port 8085
   ```

### Running as a Systemd Service (Recommended)
To keep OpenSigWeave running permanently and ensure it starts automatically on server reboots, create a dedicated unprivileged service account and a systemd service. Do not run the web application as root.

1. Create a dedicated service account and ensure the application directory is owned by it (adjust the path to your checkout):
   ```bash
   sudo useradd --system --home /opt/opensigweave --shell /usr/sbin/nologin opensigweave
   sudo chown -R opensigweave:opensigweave /opt/opensigweave
   sudo chmod 600 /opt/opensigweave/.env
   sudo nano /etc/systemd/system/opensigweave.service
   ```

2. Paste the following configuration. Adjust paths if your checkout is not located at `/opt/opensigweave`.

   ```ini
   [Unit]
   Description=OpenSigWeave FastAPI Backend
   After=network.target

   [Service]
   User=opensigweave
   Group=opensigweave
   WorkingDirectory=/opt/opensigweave
   Environment="PATH=/opt/opensigweave/venv/bin"
   EnvironmentFile=/opt/opensigweave/.env
   UMask=0077
   ExecStart=/opt/opensigweave/venv/bin/uvicorn main:app --host 127.0.0.1 --port 8085 --proxy-headers --forwarded-allow-ips=127.0.0.1
   Restart=always
   RestartSec=3

   [Install]
   WantedBy=multi-user.target
   ```

3. Enable and start the service:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable opensigweave
   sudo systemctl start opensigweave
   ```

### Reverse Proxy & SSL Configuration
OpenSigWeave does not handle SSL termination natively. It runs as a plain HTTP FastAPI application and **must** be placed behind a reverse proxy to secure the SSO callbacks and administrative sessions via HTTPS.

Here is a standard configuration example for **Nginx**:

```nginx
server {
    listen 443 ssl;
    server_name signature.yourdomain.com;

    # SSL Certificates (Managed by Certbot, etc.)
    ssl_certificate /etc/letsencrypt/live/signature.yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/signature.yourdomain.com/privkey.pem;

    location / {
        proxy_pass [http://127.0.0.1:8085](http://127.0.0.1:8085);
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### Authentik Setup
You will need two configurations in Authentik:
1. **OIDC Provider:** Create a standard OIDC Application/Provider for user logins. Ensure the scopes include `openid`, `email`, `profile`, and the claims required by your group/attribute mappings.
2. **Service Account API Token:** Create an App Token (Intent: **API Token**) bound to an administrative account. This is required for the Rspamd engine to query user attributes securely in the background.

**Note on SSO Compatibility:** OpenSigWeave was built and tested specifically for **Authentik** and **Mailcow**. While the OIDC web login is standard and adaptable, the backend signature engine strictly relies on Authentik's REST API (`/api/v3/core/users/`) to poll live user attributes during mail transit. Adapting this for Authelia, Keycloak, or Entra ID will require modifying the API request logic inside the `get_rspamd_signature` route in `main.py`.

---

## 🛠️ 2. Deploying the Rspamd Lua Engine (Mailcow)

The web app is only half the puzzle. To actually inject signatures, you must deploy the provided Lua script into your Mailcow/Rspamd instance.

1. **Copy the Script:**
   Transfer the `opensigweave.lua` script from this repository into your Mailcow server's custom Lua directory:
   ```text
   /opt/mailcow-dockerized/data/conf/rspamd/lua/opensigweave.lua
   ```

2. **Configure the Lua Script:**
   The Lua source contains no credentials. Add the configuration section from `rspamd/opensigweave.conf.example` to `/opt/mailcow-dockerized/data/conf/rspamd/rspamd.conf.local`. Set `api_url` to your HTTPS OpenSigWeave endpoint and set `engine_api_key` to exactly the same random value as the backend's `ENGINE_API_KEY` in `.env`. Keep this configuration file restricted because it contains a service credential.

3. **Enable the Plugin:**
   Tell Rspamd to load the Lua file by adding this line to the same `rspamd.conf.local` file:
   ```hcl
   lua = "/etc/rspamd/lua/opensigweave.lua";
   ```
   The Lua module reads its settings through Rspamd's `rspamd_config:get_all_opt("opensigweave")` API. It refuses to start without a valid HTTPS URL and a sufficiently long key.

4. **Restart Rspamd:**
   Apply the changes by restarting the Mailcow Rspamd container.
   ```bash
   docker compose restart rspamd-mailcow
   ```

---

## 🔒 Security Notes
* OpenSigWeave's API endpoint (`/api/signature/{email}`) exposes employee metadata (Phone, Title, etc.) to successfully compile signatures. 
* This endpoint is strictly protected by the `X-Engine-Key` header. Ensure your `ENGINE_API_KEY` is a long, cryptographically secure string, and never expose it to frontend clients.
* The engine key previously present in the Lua source must be treated as compromised if it was ever deployed. Rotate it in the backend environment and the protected Rspamd configuration. Removing a credential from the current source file does not remove it from earlier Git commits.

---

## 🪲 Bugs and Feature Requests
* **Bug reports and Feature Requests** are always welcome! Where possible, patches and new updates will be pushed with responses directly on submitted issues for status, questions, etc.
* **Pull requests** are also welcome and will be reviewed as soon as possible for *functionality, quality of code, and scope*. If approved, changes will be included in the next update of **OpenSigWeave**.

---

## 🙏 Acknowledgments & Licensing

OpenSigWeave's Rspamd injection engine (`opensigweave.lua`) heavily relies on the MIME-rebuild array logic originally developed by the **Mailcow** team for their internal `MOO_FOOTER` module. 

A massive thank you to Mailcow / Servercow for their hard work. In compliance with their original work and to give back to the community, this entire project is proudly open-sourced under the **GNU GPL v3.0** license.
