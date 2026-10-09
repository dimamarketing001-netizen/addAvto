# Security notes

- Do not commit `.env`, tokens, passwords, cookies, OAuth secrets, or client data.
- Any Click.ru token pasted into public chat, an issue, or a repository should be rotated immediately.
- Expose the web interface only behind HTTPS. Docker Compose binds to 127.0.0.1 by default.
- Set a unique `APP_PASSWORD` with at least 16 characters before deployment; set `APP_ENV=production`.
- The MVP uses HTTP Basic Auth as a simple single-operator gate. Before giving access to multiple users, implement individual identities, roles, audit logs, rate limiting, CSRF controls for browser mutations, secret encryption, and session management.
- Keep application and reverse-proxy logs free of authorization headers and request bodies containing credentials.
- Campaign creation, goal creation, and campaign start/stop are intentionally not exposed in the MVP. Verify all API methods with a test account and explicit operator approval before enabling paid actions.
