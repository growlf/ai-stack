# Secret management

ai-stack needs a few secrets: API keys for cloud models, a master key for LiteLLM, and database credentials. The simplest approach is storing them directly in `.env`. The more secure approach is using Bitwarden or a self-hosted VaultWarden instance.

---

## The simple approach: `.env` file

All secrets go in `.env` at the root of the project. This file is gitignored — it will never be committed to the repository.

```bash
LITELLM_MASTER_KEY=your-generated-key
ANTHROPIC_API_KEY=sk-ant-your-key
GEMINI_API_KEY=your-gemini-key
```

Generate a strong `LITELLM_MASTER_KEY`:
```bash
openssl rand -base64 32
```

**What to protect:**
- Never commit `.env` to git (it's gitignored, but verify with `git status`)
- Don't share your `.env` file
- Use different keys per machine

This is sufficient for personal use on a machine you control.

---

## The secure approach: Bitwarden / VaultWarden

For teams, shared machines, or if you want centralized secret management, the stack supports resolving API keys from a Bitwarden vault at runtime. Secrets never live in plain text on disk — they're fetched when the stack starts.

### What is VaultWarden?

[VaultWarden](https://github.com/dani-garcia/vaultwarden) is a self-hosted Bitwarden-compatible server. If you want zero cloud dependency for your secrets, run VaultWarden on your own hardware. The official Bitwarden cloud service also works.

### Setting up during install

The `install.sh` script prompts you to configure Bitwarden integration. If you said no during install, you can run the setup manually:

```bash
bash scripts/setup-bitwarden.sh
```

This installs the `bw` CLI (via npm) and walks you through:
1. Entering your Bitwarden server URL (or `https://vault.bitwarden.com` for cloud)
2. Creating an API key in Bitwarden (Account Settings → Security → API Key)
3. Storing the `BW_CLIENT_ID` and `BW_CLIENT_SECRET` in `.env`
4. Setting `VAULT_MASTER_PASSWORD` in `.env`

### Using vault placeholders

Once configured, use the `<vaultwarden:...>` placeholder format in `.env`:

```bash
ANTHROPIC_API_KEY=<vaultwarden:your-org-id/anthropic-api-key>
GEMINI_API_KEY=<vaultwarden:your-org-id/gemini-api-key>
LITELLM_MASTER_KEY=<vaultwarden:your-org-id/litellm-master-key>
```

The format is `<vaultwarden:ORGANIZATION_ID/ITEM_NAME>`. The organization ID comes from your Bitwarden organization settings. The item name matches the name of the item you created in the vault.

On each stack startup, `start.sh` calls `resolve-vaultwarden.sh` which:
1. Authenticates with Bitwarden using the stored credentials
2. Looks up each placeholder
3. Replaces placeholders with actual values in memory (not written to disk)
4. Starts the services with resolved values

### Manual resolution

```bash
# Resolve all placeholders and preview (dry run — doesn't modify .env)
./scripts/resolve-vaultwarden.sh --dry-run

# Resolve and update .env in place
./scripts/resolve-vaultwarden.sh
```

### Storing secrets in Bitwarden

For each secret you want to manage:
1. Open Bitwarden (web vault or app)
2. Create a new **Login** item
3. Name it exactly as you'll use in the placeholder (e.g., `anthropic-api-key`)
4. Store the key in the **Password** field
5. Assign it to your organization

---

## Generating keys

### LITELLM_MASTER_KEY

The LiteLLM master key authenticates requests to the LiteLLM proxy. Generate a strong one:

```bash
openssl rand -base64 32
```

This key is used internally — you'll rarely need to type it manually.

### Rotating keys

To rotate `LITELLM_MASTER_KEY`:
1. Generate a new key: `openssl rand -base64 32`
2. Update it in `.env` (or in Bitwarden if using vault placeholders)
3. Restart the stack: `sudo systemctl restart ai-stack.service`

Existing sessions using the old key will fail immediately. There are no stored session tokens to invalidate — just the key itself.

---

## What's in `.env` regardless of Bitwarden

Even with Bitwarden configured, these values live in `.env` in plain text (they're needed to authenticate with Bitwarden itself):

```bash
BW_CLIENT_ID=user.your-client-id
BW_CLIENT_SECRET=your-client-secret
VAULT_MASTER_PASSWORD=your-master-password
```

Protect your `.env` file with appropriate filesystem permissions:
```bash
chmod 600 .env
```

---

## Security checklist

- `chmod 600 .env` — restrict file permissions
- Verify `git status` before any commit — `.env` should never appear
- Use `openssl rand -base64 32` for all generated keys — not guessable strings
- Rotate API keys periodically, especially if you suspect exposure
- If using cloud APIs, monitor your API key usage at the provider console
